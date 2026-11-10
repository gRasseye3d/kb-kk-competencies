"""Read-only UI contracts: department authorization and public projections."""
import json
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

from app.handlers import competencies as c, testing as t
from app.router import Router


U = '11111111-1111-4111-8111-111111111111'
D = '22222222-2222-4222-8222-222222222222'
OTHER = '33333333-3333-4333-8333-333333333333'
T = '44444444-4444-4444-8444-444444444444'
C = '55555555-5555-4555-8555-555555555555'


@contextmanager
def transaction():
    yield object()


class Request:
    def __init__(self, roles=(), target=None):
        self.user = {'user_id': U, 'roles': list(roles)}
        self.target = target

    def q(self, key):
        return self.target if key == 'user_id' else None


class UIReadApiTests(unittest.TestCase):
    def test_read_routes_registered(self):
        router = Router()
        c.register(router)
        t.register(router)
        for path, handler in (('/api/competencies/departments/' + D + '/members', c.members),
                              ('/api/testing/tests', t.tests)):
            self.assertTrue(any(method == 'GET' and regex.match(path) and fn == handler
                                for method, regex, fn in router.routes))

    def test_members_requires_manager_role_even_if_assigned(self):
        for roles in ((), ('employee',), ('admin',), ('kk_responsible',)):
            with self.subTest(roles=roles), patch.object(c.db, 'query') as query:
                with self.assertRaises(PermissionError):
                    c.members(Request(roles), D)
                query.assert_not_called()

    def test_members_rejects_foreign_inactive_and_missing_department(self):
        for dept in (None, {'id': D, 'manager_id': OTHER, 'is_active': True},
                     {'id': D, 'manager_id': U, 'is_active': False}):
            with self.subTest(dept=dept), patch.object(c.db, 'transaction', transaction), \
                    patch.object(c.db, 'query', return_value=dept) as query:
                with self.assertRaises(PermissionError):
                    c.members(Request(('manager',)), D)
                self.assertEqual(query.call_count, 1)

    def test_members_public_fields_active_department_and_combined_roles(self):
        member = {'id': U, 'display_name': 'Совмещённые роли', 'login': 'member'}
        with patch.object(c.db, 'transaction', transaction), patch.object(c.db, 'query', side_effect=[
                {'id': D, 'manager_id': U, 'is_active': True}, [member]]) as query:
            response = c.members(Request(('employee', 'manager', 'kk_responsible')), D)
        self.assertEqual(json.loads(response.body), [member])
        sql, params = query.call_args.args
        self.assertEqual(params, (D,))
        self.assertIn('department_id=%s AND is_active', sql)
        self.assertIn('id,display_name,username AS login', sql)
        self.assertNotIn('user_roles', sql)  # Same eligibility as assignment mutation.

    def test_tests_denies_employee_admin_and_unauthenticated(self):
        requests = [Request(roles) for roles in ((), ('employee',), ('admin',))]
        anonymous = Request()
        anonymous.user = None
        requests.append(anonymous)
        for req in requests:
            with self.subTest(user=req.user), patch.object(t.db, 'transaction', transaction), \
                    patch.object(t.db, 'query') as query:
                with self.assertRaises(PermissionError):
                    t.tests(req)
                query.assert_not_called()

    def list_tests(self, roles, appointed=False, manager=False, active=True, user_active=True):
        def query(sql, params=(), **kwargs):
            if 'SELECT department_id FROM users' in sql:
                return {'department_id': D} if user_active else None
            if 'SELECT id,manager_id,is_active FROM departments' in sql:
                self.assertEqual(tuple(map(str, params)), (D,))
                return {'id': D, 'manager_id': U if manager else OTHER, 'is_active': active}
            if 'FROM department_kk_responsibles x' in sql:
                self.assertIn('u.department_id=x.department_id AND u.is_active', sql)
                self.assertEqual(tuple(map(str, params)), (D, U))
                return {'ok': 1} if appointed else None
            if 'FROM tests t' in sql:
                self.assertIn('t.department_id=%s', sql)
                self.assertIn("t.status<>'archived'", sql)
                self.assertIn('EXISTS (SELECT 1 FROM test_versions', sql)
                self.assertIn('max(v.version)', sql)
                self.assertNotIn('frozen_structure', sql)
                self.assertNotIn('SELECT *', sql)
                did, all_statuses = params
                self.assertEqual(did, D)
                return [{'id': T, 'title': 'Тест', 'status': status, 'version': version}
                        for status,version in (('draft',None),('published',1),('draft',1),('archived',1))
                        if all_statuses or (version and status != 'archived')]
            if 'FROM test_block_competencies bc' in sql:
                self.assertEqual(params, (T,))
                return [{'block_id': D, 'title': 'Блок', 'link_id': OTHER, 'competency_id': C, 'competency_name': 'Навык'}]
            if 'FROM test_questions' in sql: return []
            if 'SELECT frozen_structure' in sql:
                return {'frozen_structure': {'format_version': 2, 'blocks': [
                    {'block_id': D, 'title': 'Блок', 'position': 1, 'competencies': [
                        {'competency_id': C, 'competency_name': 'Навык', 'questions': []}]}]}}
            raise AssertionError(sql)
        with patch.object(t.db, 'transaction', transaction), patch.object(t.db, 'query', side_effect=query), \
                patch.object(t.db, 'execute') as execute:
            response = t.tests(Request(roles))
            execute.assert_not_called()
        rows = json.loads(response.body)
        for row in rows:
            fields = {'id', 'title', 'status', 'version', 'competencies', 'latest_published_version',
                      'has_draft', 'assignable', 'next_version'}
            if appointed: fields.add('expected_token')
            self.assertEqual(set(row), fields)
            self.assertEqual(row['competencies'], [{'id': C, 'name': 'Навык'}])
            self.assertEqual(row['assignable'], row['version'] is not None and row['status'] != 'archived')
        return rows

    def test_tests_appointed_responsible_sees_all_statuses(self):
        rows = self.list_tests(('kk_responsible',), appointed=True)
        self.assertEqual([r['status'] for r in rows], ['draft', 'published', 'draft', 'archived'])
        self.assertIsNone(rows[0]['version'])

    def test_tests_assigned_manager_sees_publications_even_while_draft(self):
        self.assertEqual([r['status'] for r in self.list_tests(('manager',), manager=True)], ['published', 'draft'])

    def test_tests_combined_roles_use_union_of_actual_appointments(self):
        roles = ('employee', 'manager', 'kk_responsible')
        for appointed, manager, expected in ((True, True, 4), (True, False, 4), (False, True, 2)):
            with self.subTest(appointed=appointed, manager=manager):
                self.assertEqual(len(self.list_tests(roles, appointed=appointed, manager=manager)), expected)

    def test_tests_roles_without_appointment_are_denied(self):
        for roles in (('manager',), ('kk_responsible',), ('manager', 'kk_responsible')):
            with self.subTest(roles=roles), self.assertRaises(PermissionError):
                self.list_tests(roles)

    def test_tests_inactive_department_or_user_is_denied(self):
        for kwargs in ({'active': False}, {'user_active': False}):
            with self.subTest(kwargs=kwargs), self.assertRaises(PermissionError):
                self.list_tests(('manager', 'kk_responsible'), appointed=True, manager=True, **kwargs)

    def test_tests_requires_own_department(self):
        for user in (None, {'department_id': None}):
            with self.subTest(user=user), patch.object(t.db, 'transaction', transaction), \
                    patch.object(t.db, 'query', return_value=user) as query:
                with self.assertRaises(PermissionError):
                    t.tests(Request(('manager', 'kk_responsible')))
                self.assertTrue(all('SELECT department_id FROM users' in call.args[0]
                                    for call in query.call_args_list))

    def test_results_add_labels_and_timestamp_without_changing_department_filter(self):
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        row = {'assignment_id': T, 'status': 'completed', 'due_at': None, 'assigned_at': timestamp,
               'test_title': 'Тест', 'attempt_id': OTHER, 'attempt_status': 'graded',
               'competency_id': C, 'competency_name': 'Навык', 'correct_count': 2, 'total_count': 2, 'level': 2}
        for target, roles, own in ((None, ('employee',), True), (OTHER, ('manager',), False)):
            with self.subTest(own=own), patch.object(t.db, 'transaction', transaction), \
                    patch.object(t, 'own_department', return_value=D), \
                    patch.object(t.db, 'query', side_effect=[[row]] if own else [{'ok': 1}, [row]]) as query:
                response = json.loads(t.results(Request(roles, target)).body)
            self.assertEqual(response[0]['test_title'], 'Тест')
            self.assertEqual(response[0]['competency_name'], 'Навык')
            self.assertIn('2026-01-01', response[0]['assigned_at'])
            self.assertEqual(set(response[0]), set(row))
            sql, params = query.call_args.args
            self.assertEqual(params, (target or U, own, None if own else D))
            self.assertIn('(%s OR test.department_id=%s)', sql)
            self.assertIn('LEFT JOIN competencies c ON c.id=r.competency_id', sql)
            self.assertIn('a.assigned_at,test.title AS test_title', sql)
            self.assertIn('c.name AS competency_name', sql)

    def test_results_foreign_employee_denied_before_read(self):
        with patch.object(t.db, 'transaction', transaction), patch.object(t, 'own_department', return_value=D), \
                patch.object(t.db, 'query', return_value=None) as query:
            with self.assertRaises(PermissionError):
                t.results(Request(('manager',), OTHER))
            self.assertEqual(query.call_count, 1)

    def test_attempt_labels_for_included_and_excluded_without_answer_keys(self):
        frozen = {'id': T, 'body': 'Вопрос', 'type': 'single', 'revision_id': OTHER,
                  'options': [{'id': U, 'body': 'Ответ', 'correct': True}]}
        comps = [{'competency_id': C, 'competency_name': 'Включён', 'state': 'included',
                  'exclusion_reason': None, 'available_at': None},
                 {'competency_id': D, 'competency_name': 'Исключён', 'state': 'excluded',
                  'exclusion_reason': 'reserved_active', 'available_at': None}]
        for status in ('in_progress', 'graded'):
            attempt = {'status': status, 'test_title': 'Тест', 'immutable_snapshot': {'competencies': [
                {'competency_id': C, 'questions': [frozen]},
                {'competency_id': D, 'questions': [dict(frozen, body='Скрытый вопрос')]}]}}
            with self.subTest(status=status), patch.object(t.db, 'query', side_effect=[attempt, comps]) as query:
                response = t.attempt_view(Request(('employee',)), OTHER)
            body = json.loads(response.body)
            self.assertEqual(body['test_title'], 'Тест')
            self.assertEqual(body['competencies'], comps)
            self.assertEqual(len(body['questions']), 1 if status == 'in_progress' else 0)
            self.assertEqual(query.call_args_list[0].args[1], (OTHER, U))
            self.assertIn('x.user_id=%s', query.call_args_list[0].args[0])
            self.assertIn('test.title AS test_title', query.call_args_list[0].args[0])
            self.assertIn('c.name AS competency_name', query.call_args.args[0])
            for secret in ('correct', 'revision_id', 'immutable_snapshot', 'Скрытый вопрос'):
                self.assertNotIn(secret, response.body.decode())

    def test_attempt_foreign_owner_and_wrong_role_are_denied(self):
        with patch.object(t.db, 'query', return_value=None) as query:
            with self.assertRaises(PermissionError):
                t.attempt_view(Request(('manager',)), OTHER)
            query.assert_not_called()
            with self.assertRaises(PermissionError):
                t.attempt_view(Request(('employee',)), OTHER)
            self.assertEqual(query.call_count, 1)

    def test_question_bank_adds_name_and_keeps_department_and_profile_scope(self):
        row = {'id': T, 'competency_id': C, 'competency_name': 'Навык', 'body': 'Вопрос',
               'question_type': 'single', 'moderation_status': 'pending'}
        with patch.object(t.db, 'transaction', transaction), patch.object(t, 'own_department', return_value=D), \
                patch.object(t.db, 'query', return_value=[row]) as query:
            self.assertEqual(json.loads(t.bank(Request(('kk_responsible',))).body), [row])
        sql, params = query.call_args.args
        self.assertEqual(params, (D, D, None, None, None, None, 100, 0))
        self.assertIn('c.name AS competency_name', sql)
        self.assertIn('p.department_id=%s AND p.is_active AND q.bank_department_id=%s', sql)
        self.assertNotIn('answer_options', sql)


if __name__ == '__main__':
    unittest.main()

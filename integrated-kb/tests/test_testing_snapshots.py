"""Versioning contracts without a database; SQL lifecycle coverage is opt-in PG."""
import copy
import json
import unittest
from unittest.mock import patch

from app import testing_snapshots as s
from app.handlers import testing as t
from app.utils import ConflictError, PreconditionRequired
from test_testing import Request, transaction, A, B, C, Q, Q2, O, U


def draft():
    return {'title': 'Test', 'description': 'Description', 'blocks': [
        {'title': 'First', 'competencies': [
            {'competency_id': C, 'question_ids': [Q, Q2]},
            {'competency_id': B, 'question_ids': []}]}]}


def frozen():
    question = {'id': Q, 'revision_id': A, 'body': 'Frozen question', 'type': 'single',
                'options': [{'id': O, 'body': 'Yes', 'correct': True},
                            {'id': U, 'body': 'No', 'correct': False}]}
    return {'format_version': 2, 'scoring_rule': s.PRIMARY_RULE, 'test_title': 'Frozen title', 'test_version': 1,
            'blocks': [{'block_id': A, 'title': 'First', 'position': 1, 'competencies': [
                {'competency_id': C, 'competency_name': 'Frozen C', 'questions': [question]}]},
                {'block_id': B, 'title': 'Second', 'position': 2, 'competencies': [
                    {'competency_id': B, 'competency_name': 'Frozen B', 'questions': [question]}]}]}


class SnapshotTests(unittest.TestCase):
    def test_new_attempt_filters_empty_blocks_without_mutating_version(self):
        original = frozen()
        before = copy.deepcopy(original)
        attempt = s.for_attempt(original, {B})
        self.assertEqual(original, before)
        self.assertEqual(len(attempt['blocks']), 1)
        self.assertEqual(attempt['blocks'][0]['block_id'], B)
        self.assertEqual(attempt['blocks'][0]['position'], 2)
        self.assertEqual(attempt['scoring_rule'], s.PRIMARY_RULE)
        public = s.public_blocks(attempt)
        self.assertEqual(public[0]['competencies'][0]['competency_name'], 'Frozen B')
        self.assertEqual(public[0]['competencies'][0]['questions'][0]['body'], 'Frozen question')
        for secret in ('correct', 'revision_id', 'scoring_rule', 'block_id'):
            self.assertNotIn(secret, json.dumps(public))
        self.assertEqual(s.public_blocks(attempt, show_questions=False)[0]['competencies'][0]['questions'], [])

    def test_legacy_adapter_preserves_json_and_block_foreign_keys(self):
        legacy = {'competencies': [{'competency_id': C, 'block_id': A, 'questions': []},
                                   {'competency_id': B, 'block_id': B, 'questions': []}]}
        before = copy.deepcopy(legacy)
        self.assertEqual(s.for_attempt(legacy, {C}), before)
        self.assertEqual([b['block_id'] for b in s.blocks(legacy)], [A, B])
        self.assertEqual(s.competencies(legacy), legacy['competencies'])
        self.assertEqual(legacy, before)

    def test_scoring_is_frozen_and_explicit_unknown_rules_fail(self):
        q = frozen()['blocks'][0]['competencies'][0]['questions'][0]
        for snapshot in ({'competencies': []}, {'format_version': 1, 'competencies': []}, frozen()):
            score, level = s.scoring(snapshot)
            self.assertTrue(score([O], q))
            self.assertFalse(score([], q))
            self.assertFalse(score([O, U], q))
            self.assertEqual([level(0, 2), level(1, 2), level(2, 2)], [0, 1, 2])
        for snapshot in ({'scoring_rule': 'future_rule'}, {'format_version': 2},
                         {'format_version': 2, 'scoring_rule': 'future_rule'}):
            with self.assertRaisesRegex(ValueError, 'Unsupported scoring'):
                s.scoring(snapshot)
        with self.assertRaisesRegex(ValueError, 'Unsupported snapshot'):
            s.blocks({'format_version': 99})

    def test_format_validation_is_shared_and_rejects_noninteger_aliases(self):
        for version in (99, 0, -1, True, False, 1.0, 2.0, '1', '2', None, [], {}):
            snapshot = dict(frozen(), format_version=version)
            for name, adapt in (('blocks', s.blocks), ('scoring', s.scoring),
                                ('for_attempt', lambda value: s.for_attempt(value, {C}))):
                with self.subTest(version=version, adapter=name), \
                        self.assertRaisesRegex(ValueError, 'Unsupported snapshot format'):
                    adapt(snapshot)

    def test_attempt_title_names_version_are_frozen_including_exclusions(self):
        full = frozen()
        attempt = s.for_attempt(full, {C})
        row = {'status': 'in_progress', 'immutable_snapshot': attempt, 'test_title': 'Changed',
               'test_version': 1, 'published_snapshot': full}
        comps = [{'competency_id': C, 'competency_name': 'Changed C', 'state': 'included'},
                 {'competency_id': B, 'competency_name': 'Changed B', 'state': 'excluded'}]
        with patch.object(t.db, 'query', side_effect=[row, comps]):
            body = json.loads(t.attempt_view(Request(), A).body)
        self.assertEqual(body['test_title'], 'Frozen title')
        self.assertEqual(body['test_version'], 1)
        self.assertEqual([c['competency_name'] for c in body['competencies']], ['Frozen C', 'Frozen B'])
        self.assertEqual(len(body['blocks']), 1)
        self.assertEqual(len(body['questions']), 1)
        self.assertNotIn('correct', json.dumps(body))

    def test_results_strip_raw_snapshot_and_use_frozen_names(self):
        row = {'test_title': 'Changed', 'competency_id': C, 'competency_name': 'Changed',
               'test_version': 1, 'result_snapshot': frozen()}
        with patch.object(t.db, 'transaction', transaction), patch.object(t.db, 'query', return_value=[row]):
            body = json.loads(t.results(Request()).body)[0]
        self.assertEqual(body, {'test_title': 'Frozen title', 'competency_id': C,
                               'competency_name': 'Frozen C', 'test_version': 1})


class DraftValidationTests(unittest.TestCase):
    def test_legacy_create_never_silently_selects_questions(self):
        _, _, blocks = t.parse_draft({'title': 'Test', 'competency_ids': [C, B]})
        self.assertEqual([c['question_ids'] for c in blocks[0]['competencies']], [[], []])
        self.assertEqual(t.parse_draft(draft())[2], draft()['blocks'])

    def test_all_limits_and_duplicate_memberships(self):
        invalid = []
        data = draft(); data['blocks'] = []; invalid.append(data)
        data = draft(); data['blocks'] *= 11; invalid.append(data)
        data = draft(); data['blocks'][0]['competencies'].pop(); invalid.append(data)
        data = draft(); data['blocks'][0]['competencies'][1]['competency_id'] = C; invalid.append(data)
        data = draft(); data['blocks'] *= 2; invalid.append(data)
        data = draft(); data['blocks'][0]['competencies'][0]['question_ids'] = [Q, Q]; invalid.append(data)
        data = draft(); data['blocks'][0]['competencies'][1]['question_ids'] = [Q]; invalid.append(data)
        data = draft(); data['blocks'][0]['competencies'][0]['question_ids'] = [Q] * 51; invalid.append(data)
        data = draft(); data['blocks'][0]['competencies'][0]['question_ids'] = ['bad']; invalid.append(data)
        data = draft(); data['description'] = {}; invalid.append(data)
        data = draft(); data['blocks'][0]['title'] = ''; invalid.append(data)
        data = draft(); data['blocks'][0]['competencies'] = [
            {'competency_id': '%08d-1111-4111-8111-111111111111' % n, 'question_ids': []} for n in range(21)]
        invalid.append(data)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                t.parse_draft(data)

    def test_selected_bank_and_competency_ownership_publication_minimum(self):
        blocks = draft()['blocks']
        with patch.object(t, 'validate_profile'), patch.object(t.db, 'query', return_value=[]) as query:
            with self.assertRaisesRegex(ValueError, 'Two approved'):
                t.selected_questions(None, A, blocks, publishing=True)
            query.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                t.selected_questions(None, A, blocks)
            sql, params = query.call_args.args
            self.assertIn("moderation_status='approved' AND is_active", sql)
            self.assertIn('bank_department_id=%s', sql)
            self.assertEqual(params, (sorted([Q, Q2]), A))
        with patch.object(t, 'validate_profile'), patch.object(t.db, 'query', return_value=[
                {'id': Q, 'competency_id': B}, {'id': Q2, 'competency_id': C}]):
            with self.assertRaisesRegex(ValueError, 'another competency'):
                t.selected_questions(None, A, blocks)

    def test_bank_pagination_and_filters_no_truncation(self):
        req = Request(roles=('kk_responsible',), data={'competency_id': C, 'status': 'approved', 'limit': '25', 'offset': '200'})
        with patch.object(t.db, 'transaction', transaction), patch.object(t, 'own_department', return_value=A), \
                patch.object(t.db, 'query', return_value=[]) as query:
            t.bank(req)
        self.assertEqual(query.call_args.args[1], (A, A, C, C, 'approved', 'approved', 25, 200))
        self.assertIn('q.is_active', query.call_args.args[0])
        for data in ({'limit': '101'}, {'limit': '-1'}, {'offset': '-1'}, {'offset': 'bad'}, {'status': 'all'}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                t.bank(Request(data=data))


class DraftLifecycleTests(unittest.TestCase):
    def test_token_is_stable_and_invalidated_by_every_authoring_state_change(self):
        test = {'id': A, 'title': 'Test', 'description': '', 'status': 'draft'}
        blocks = [{'block_id': B, **draft()['blocks'][0]}]
        token = t.expected_token(test, blocks, None)
        self.assertEqual(token, t.expected_token(dict(reversed(list(test.items()))), copy.deepcopy(blocks), None))
        self.assertEqual(len(token), 64)
        for changed_test, changed_blocks, latest in ((dict(test, status='archived'), blocks, None),
                (dict(test, description='changed'), blocks, None), (test, blocks, 1),
                (test, [dict(blocks[0], block_id=O)], None)):
            self.assertNotEqual(token, t.expected_token(changed_test, changed_blocks, latest))
        with self.assertRaises(PreconditionRequired): t.check_token({}, test, blocks, None)
        with self.assertRaises(ConflictError): t.check_token({'expected_token': 'stale'}, test, blocks, None)
        t.check_token({'expected_token': token}, test, blocks, None)

    def test_all_existing_mutations_require_token_under_department_then_test_locks(self):
        for fn in (t.begin_draft, t.save_draft, t.publish, t.archive):
            for supplied, error in (({}, PreconditionRequired), ({'expected_token': 'stale'}, ConflictError)):
                seen = []
                def department(*args): seen.append('department'); return B
                def row(*args): seen.append('test'); return {'id': A, 'title': 'Test', 'status': 'draft'}
                with self.subTest(fn=fn.__name__, supplied=supplied), \
                        patch.object(t.db, 'transaction', transaction), patch.object(t, 'own_department', side_effect=department), \
                        patch.object(t, 'test_row', side_effect=row), patch.object(t, 'latest_version', return_value=1), \
                        patch.object(t, 'current_blocks', return_value=[]), patch.object(t.db, 'execute') as execute:
                    with self.assertRaises(error): fn(Request(data=supplied), A)
                    execute.assert_not_called()
                self.assertEqual(seen, ['department', 'test'])

    def test_published_title_locked_before_any_composition_write(self):
        test = {'id': A, 'title': 'Test', 'status': 'draft'}
        data = draft(); data['title'] = 'Renamed'; data['expected_token'] = t.expected_token(test, [], 1)
        with patch.object(t.db, 'transaction', transaction), patch.object(t, 'own_department', return_value=B), \
                patch.object(t, 'test_row', return_value=test), patch.object(t, 'latest_version', return_value=1), \
                patch.object(t, 'current_blocks', return_value=[]), patch.object(t.db, 'execute') as execute:
            with self.assertRaisesRegex(ConflictError, 'title is locked'): t.save_draft(Request(data=data), A)
            execute.assert_not_called()

    def test_save_composition_keeps_old_blocks_and_uses_global_question_positions(self):
        writes = []
        def query(sql, params=(), **kwargs):
            writes.append((sql, params))
            if 'max(position)' in sql: return {'position': 8}
            return {'id': B}
        blocks = draft()['blocks']
        blocks[0]['competencies'][1]['question_ids'] = [U]
        blocks.append({'title': 'Next', 'competencies': [{'competency_id': O, 'question_ids': [A]}]})
        with patch.object(t.db, 'query', side_effect=query), \
                patch.object(t.db, 'execute', side_effect=lambda sql, params, **kw: writes.append((sql, params))):
            t.save_composition(None, A, blocks)
        self.assertEqual([sql.split(' WHERE')[0] for sql, _ in writes if sql.startswith('DELETE')],
                         ['DELETE FROM test_questions', 'DELETE FROM test_block_competencies'])
        self.assertEqual([params[2] for sql, params in writes if 'INSERT INTO test_blocks(' in sql], [9, 10])
        self.assertEqual([params[-1] for sql, params in writes if 'INSERT INTO test_questions(' in sql], [1, 2, 3, 4])

    def test_manager_employee_and_admin_cannot_read_or_mutate_drafts(self):
        for role in ('manager', 'employee', 'admin'):
            for fn in (t.get_draft, t.begin_draft, t.save_draft, t.publish, t.archive):
                with self.subTest(role=role, fn=fn.__name__), patch.object(t.db, 'transaction', transaction), \
                        patch.object(t.db, 'query') as query:
                    with self.assertRaises(PermissionError): fn(Request(roles=(role,)), A)
                    query.assert_not_called()

    def test_employee_cannot_browse_version_snapshots(self):
        with patch.object(t.db, 'transaction', transaction), patch.object(t.db, 'query') as query:
            with self.assertRaises(PermissionError): t.versions(Request(), A)
            with self.assertRaises(PermissionError): t.version_detail(Request(), A, '1')
            query.assert_not_called()

    def test_unknown_scoring_rule_submit_has_no_writes(self):
        for snapshot in ({'scoring_rule': 'unknown'}, dict(frozen(), scoring_rule='unknown')):
            self.assert_submit_rejected_without_writes(snapshot, 'Unsupported scoring')

    def test_unsupported_format_with_known_rule_submit_has_no_writes(self):
        for version in (99, True, False, 1.0, 2.0, '2', None):
            with self.subTest(version=version):
                self.assert_submit_rejected_without_writes(
                    dict(frozen(), format_version=version), 'Unsupported snapshot format')

    def assert_submit_rejected_without_writes(self, snapshot, error):
        row = {'status': 'in_progress', 'assignment_id': B, 'immutable_snapshot': snapshot}
        before = copy.deepcopy(row)
        with patch.object(t.db, 'transaction', transaction), patch.object(t, 'lock_user'), \
                patch.object(t.db, 'query', return_value=row) as query, \
                patch.object(t.db, 'execute') as execute:
            with self.assertRaisesRegex(ValueError, error):
                t.submit(Request(data={'answers': {Q: [O]}}), A)
            # query() can write too (answers use INSERT RETURNING); the sole
            # call must be the locked attempt read. No results/events/cooldown,
            # answers, reservation releases or status updates may be written.
            query.assert_called_once()
            self.assertTrue(query.call_args.args[0].startswith('SELECT t.*'))
            self.assertIn('FOR UPDATE OF t', query.call_args.args[0])
            execute.assert_not_called()
        self.assertEqual(row, before)


if __name__ == '__main__':
    unittest.main()

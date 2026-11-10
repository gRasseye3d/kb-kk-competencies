import unittest
import json
import io
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.handlers import testing as t
from app import wsgi, db
from app.utils import ConflictError

U = '11111111-1111-4111-8111-111111111111'
A = '22222222-2222-4222-8222-222222222222'
C = '33333333-3333-4333-8333-333333333333'
Q = '44444444-4444-4444-8444-444444444444'
O = '55555555-5555-4555-8555-555555555555'
B = '66666666-6666-4666-8666-666666666666'
Q2 = '77777777-7777-4777-8777-777777777777'
FUTURE = datetime(2090,1,1,tzinfo=timezone.utc)


@contextmanager
def transaction():
    yield object()


class Request:
    def __init__(self, roles=('employee',), data=None, user=U):
        self.path = '/api/testing'
        self.user = {'user_id':user,'roles':list(roles),'csrf_token':'csrf'}
        self.data = data or {}

    def json(self):
        return dict(self.data,csrf='csrf')

    def q(self, key):
        return self.data.get(key)


class TestingTests(unittest.TestCase):
    def test_no_answer_keys_in_employee_view(self):
        frozen = {'id':Q,'body':'Question','type':'multiple','revision_id':A,
                  'options':[{'id':O,'body':'Yes','correct':True}]}
        with patch.object(t.db,'query',side_effect=[{'status':'in_progress','immutable_snapshot':
                {'competencies':[{'competency_id':C,'questions':[frozen]}]}},
                [{'competency_id':C,'state':'included','available_at':None}]]):
            response=t.attempt_view(Request(),A)
        self.assertNotIn(b'correct',response.body)
        self.assertNotIn(b'revision_id',response.body)
        self.assertIn(b'Question',response.body)

    def test_second_start_and_expired_deadline_have_no_writes(self):
        req=Request()
        for status,due,error in (
            ('in_progress',None,'already started'),
            ('assigned',datetime.now(timezone.utc)-timedelta(days=1),'Deadline')):
            a={'status':status,'assignment_type':'manager_assigned','due_at':due,'frozen_structure':{'competencies':[]}}
            def query(sql,*args,**kwargs):
                if 'FROM test_assignments a' in sql: return a
                if ' AS valid' in sql: return {'valid':False}
                return {'id':U}
            with patch.object(t.db,'transaction',transaction),patch.object(t.db,'query',side_effect=query),\
                 patch.object(t.db,'execute') as execute:
                with self.assertRaisesRegex(ConflictError,error): t.start(req,A)
                execute.assert_not_called()

    def test_department_and_role_checked_before_assigning(self):
        with patch.object(t.db,'transaction',transaction),patch.object(t,'own_department',side_effect=PermissionError),\
             patch.object(t.db,'query') as query:
            with self.assertRaises(PermissionError): t.assign(Request(data={'test_id':A,'user_id':U,'due_at':FUTURE.isoformat()}))
            query.assert_not_called()

    def test_transferred_employee_results_exclude_previous_department_for_new_manager(self):
        previous, current = B, C
        assignments = [(Q, previous), (Q2, current)]
        def query(sql, params=(), **kwargs):
            if 'FROM users WHERE id=' in sql:
                self.assertEqual(params, (U, current))
                return {'?column?': 1}
            if 'FROM test_assignments a' in sql:
                self.assertIn('JOIN tests test ON test.id=a.test_id', sql)
                self.assertIn('(%s OR test.department_id=%s)', sql)
                target, own, did = params
                self.assertEqual(target, U)
                return [{'assignment_id': aid} for aid, department_id in assignments
                        if own or department_id == did]
            raise AssertionError(sql)
        with patch.object(t.db, 'transaction', transaction), patch.object(t, 'own_department', return_value=current),\
             patch.object(t.db, 'query', side_effect=query):
            manager = Request(roles=('manager',), data={'user_id': U}, user=A)
            self.assertEqual(json.loads(t.results(manager).body), [{'assignment_id': Q2}])
            # Employees retain access to their own completed historical assignments.
            employee = Request(user=U)
            self.assertEqual(json.loads(t.results(employee).body),
                             [{'assignment_id': Q}, {'assignment_id': Q2}])

    def test_exact_match_validation_and_reservation_release_order(self):
        req=Request(data={'answers':{Q2:[]}})
        frozen={'type':'single','options':[{'id':O,'body':'x','correct':True}]}
        queries=[{'id':U}, {'status':'in_progress','assignment_id':A,'immutable_snapshot':{'competencies':[]}},
                 [{'competency_id':C,'question_count':2}],
                 [{'question_id':Q,'competency_id':C,'frozen_question':frozen}]]
        def query(sql,*args,**kwargs): return queries.pop(0)
        with patch.object(t.db,'transaction',transaction),patch.object(t.db,'query',side_effect=query),\
             patch.object(t.db,'execute') as execute:
            with self.assertRaisesRegex(ValueError,'Invalid question'): t.submit(req,A)
            execute.assert_not_called()

    def test_wrong_role_cannot_create_question(self):
        req=Request(data={'competency_id':C,'body':'Question','type':'single',
                          'options':[{'body':'yes','correct':True},{'body':'no','correct':False}]})
        with patch.object(t.db,'transaction',transaction),patch.object(t.db,'query') as query:
            with self.assertRaises(PermissionError): t.create_question(req)
            query.assert_not_called()

    def test_invalid_ids_and_keys_rejected(self):
        with patch.object(t.db,'transaction',transaction),patch.object(t.db,'query',return_value={'id':U}):
            with self.assertRaises(ValueError): t.start(Request(),'not-an-id')
        req=Request(data={'competency_id':C,'body':'Question','type':'single',
                          'options':[{'body':'yes','correct':True},{'body':'also','correct':True}]})
        with self.assertRaises(ValueError): t.create_question(req)

    def test_grading_writes_events_cooldown_and_releases_reservations(self):
        req=Request(data={'answers':{Q:[O]}})
        frozen={'type':'single','options':[{'id':O,'body':'x','correct':True}]}
        def query(sql,*args,**kwargs):
            if sql.startswith('SELECT id FROM users'): return {'id':U}
            if 'FROM test_attempts t' in sql: return {'status':'in_progress','assignment_id':A,'immutable_snapshot':{'competencies':[]}}
            if 'FROM attempt_competencies' in sql: return [{'competency_id':C,'question_count':2}]
            if 'FROM attempt_questions' in sql: return [{'question_id':qid,'competency_id':C,'frozen_question':frozen} for qid in (Q,Q2)]
            if 'INSERT INTO attempt_answers' in sql: return {'id':A}
            raise AssertionError(sql)
        with patch.object(t.db,'transaction',transaction),patch.object(t.db,'query',side_effect=query),\
             patch.object(t.db,'execute') as execute:
            response=t.submit(req,A)
        self.assertEqual(json.loads(response.body)['results'][0]['level'],1)
        sql=[call.args[0] for call in execute.call_args_list]
        self.assertTrue(any('competency_assessment_events' in s for s in sql))
        self.assertTrue(any('employee_competencies' in s and 'GREATEST' in s and
                            'last_level=EXCLUDED.current_level' in s for s in sql))
        self.assertTrue(any('assessment_cooldowns' in s for s in sql))
        self.assertLess(next(i for i,s in enumerate(sql) if 'released_at=now()' in s),
                        next(i for i,s in enumerate(sql) if "status='graded'" in s))

    def test_positions_global_and_active_reservation_has_no_fake_date(self):
        comps=[{'competency_id':C,'block_id':B,'questions':[
            {'id':Q,'revision_id':A}, {'id':Q2,'revision_id':A}]},
            {'competency_id':B,'block_id':B,'questions':[
            {'id':O,'revision_id':A}, {'id':U,'revision_id':A}]}]
        def query(sql,*args,**kwargs):
            if sql.startswith('SELECT id FROM users'): return {'id':U}
            if ' AS valid' in sql: return {'valid':True}
            if 'FROM test_assignments a' in sql: return {'status':'assigned','assignment_type':'manager_assigned','due_at':FUTURE,'frozen_structure':{'competencies':comps}}
            if 'FROM assessment_cooldowns' in sql: return None
            if 'FROM test_competency_reservations' in sql: return {'?column?':1} if args[0][1]==C else None
            if 'INSERT INTO test_attempts' in sql: return {'id':A}
            raise AssertionError(sql)
        with patch.object(t.db,'transaction',transaction),patch.object(t.db,'query',side_effect=query),\
             patch.object(t.db,'execute') as execute:
            t.start(Request(),A)
        comp_rows=[call.args[1] for call in execute.call_args_list if 'INSERT INTO attempt_competencies' in call.args[0]]
        self.assertEqual(comp_rows[0][4:6],('reserved_active',None))
        self.assertEqual(comp_rows[1][4:6],(None,None))
        positions=[call.args[1][4] for call in execute.call_args_list if 'INSERT INTO attempt_questions' in call.args[0]]
        self.assertEqual(positions,[1,2])

    def test_positions_across_two_included_competencies(self):
        comps=[{'competency_id':C,'block_id':B,'questions':[{'id':Q,'revision_id':A},{'id':Q2,'revision_id':A}]},
               {'competency_id':B,'block_id':B,'questions':[{'id':O,'revision_id':A},{'id':U,'revision_id':A}]}]
        def query(sql,*args,**kwargs):
            if sql.startswith('SELECT id FROM users'): return {'id':U}
            if ' AS valid' in sql: return {'valid':True}
            if 'FROM test_assignments a' in sql: return {'status':'assigned','assignment_type':'manager_assigned','due_at':FUTURE,'frozen_structure':{'competencies':comps}}
            if 'FROM assessment_cooldowns' in sql or 'FROM test_competency_reservations' in sql: return None
            if 'INSERT INTO test_attempts' in sql: return {'id':A}
            raise AssertionError(sql)
        with patch.object(t.db,'transaction',transaction),patch.object(t.db,'query',side_effect=query),\
             patch.object(t.db,'execute') as execute:
            t.start(Request(),A)
        positions=[call.args[1][4] for call in execute.call_args_list if 'INSERT INTO attempt_questions' in call.args[0]]
        self.assertEqual(positions,[1,2,3,4])

    def test_all_excluded_does_not_create_attempt_or_change_assignment(self):
        available_at = datetime(2090,1,1,tzinfo=timezone.utc)
        comps=[{'competency_id':cid,'block_id':B,'questions':[
            {'id':Q,'options':[{'id':O,'correct':True}],'answer_key_snapshot':[O]}]} for cid in (C,B)]
        names = {C:'Аналитика', B:'Общение'}
        for reason in ('cooldown_30d','reserved_active'):
            with self.subTest(reason=reason):
                assignment={'status':'assigned','assignment_type':'manager_assigned','due_at':FUTURE,'frozen_structure':{'competencies':comps}}
                def query(sql,params=(),**kwargs):
                    if ' AS valid' in sql: return {'valid':True}
                    if sql.startswith('SELECT id FROM users'): return {'id':U}
                    if 'FROM test_assignments a' in sql:
                        self.assertIn('a.user_id=%s',sql)
                        self.assertEqual(params,(A,U))
                        return assignment
                    if 'FROM assessment_cooldowns' in sql:
                        return {'available_at':available_at} if reason=='cooldown_30d' else None
                    if 'FROM test_competency_reservations' in sql: return {'?column?':1}
                    if 'FROM competencies WHERE' in sql: return [{'id':cid,'name':name} for cid,name in names.items()]
                    raise AssertionError(sql)
                responses=[]
                with patch.object(t.db,'transaction',transaction),patch.object(t.db,'query',side_effect=query) as q,\
                     patch.object(t.db,'execute') as execute,patch.object(wsgi,'Request',return_value=Request()),\
                     patch.object(wsgi.app_router,'dispatch',side_effect=lambda req:t.start(req,A)):
                    body=b''.join(wsgi.application({'PATH_INFO':'/api/testing/assignments/'+A+'/start'},
                        lambda status,headers:responses.append(status)))
                    execute.assert_not_called()
                self.assertEqual(responses,['409 Conflict'])
                self.assertEqual(json.loads(body),{'error':'No competencies currently available',
                    'code':'assessment_unavailable','details':{'competencies':[
                        {'competency_id':cid,'competency_name':names[cid],'reason':reason,
                         'available_at':available_at.isoformat() if reason=='cooldown_30d' else None}
                        for cid in (C,B)]}})
                self.assertEqual(assignment['status'],'assigned')
                self.assertFalse(any('INSERT' in call.args[0] or 'UPDATE test_' in call.args[0] for call in q.call_args_list))
                for private in (b'correct',b'answer_key',b'questions',Q.encode(),O.encode()):
                    self.assertNotIn(private,body)

    def test_unavailable_details_require_assignment_ownership(self):
        def query(sql,params=(),**kwargs):
            if sql.startswith('SELECT id FROM users'): return {'id':U}
            if 'FROM test_assignments a' in sql:
                self.assertIn('a.user_id=%s',sql)
                self.assertEqual(params,(A,U))
                return None
            raise AssertionError('Availability queried before owner authorization')
        with patch.object(t.db,'transaction',transaction),patch.object(t.db,'query',side_effect=query),\
             patch.object(t.db,'execute') as execute:
            with self.assertRaisesRegex(PermissionError,'Assignment unavailable'): t.start(Request(),A)
            execute.assert_not_called()

    def test_wsgi_conflict_details_are_allowlisted(self):
        row={'competency_id':C,'competency_name':'Аналитика','reason':'reserved_active','available_at':None}
        details={'competencies':[dict(row,questions=[{'correct':True}],answer_key_snapshot=[O])],
                 'immutable_snapshot':{'secret':True}}
        for code in ('assessment_unavailable','internal_conflict',None):
            error=ConflictError('unavailable',code=code,details=details)
            with patch.object(wsgi,'Request',return_value=Request()),\
                 patch.object(wsgi.app_router,'dispatch',side_effect=error):
                body=b''.join(wsgi.application({'PATH_INFO':'/api/testing'},lambda *_:None))
            expected={'error':'unavailable'}
            if code=='assessment_unavailable':
                expected.update(code=code,details={'competencies':[row]})
            self.assertEqual(json.loads(body),expected)

    def test_publish_locks_questions_in_stable_order_before_revision(self):
        blocks=[{'block_id': A, 'title': 'Block', 'position': 1, 'competencies': [
            {'competency_id': B, 'competency_name': 'B', 'question_ids': [Q2,Q]},
            {'competency_id': C, 'competency_name': 'C', 'question_ids': [O,U]}]}]
        test={'id': A, 'title': 'Test', 'status': 'draft'}
        seen=[]
        def query(sql,params=(),**kwargs):
            seen.append(sql)
            if 'FROM tests WHERE' in sql: return test
            if sql.startswith('SELECT max(version)'): return {'version': None}
            if 'FROM competency_profiles' in sql: return [{'competency_id': B}, {'competency_id': C}]
            if 'FROM questions\n' in sql:
                self.assertEqual(params[0],sorted([Q2,Q,O,U]))
                self.assertIn('ORDER BY id FOR UPDATE',sql)
                return [{'id':x,'competency_id':B if x in (Q,Q2) else C,'body':'question','question_type':'single'}
                        for x in params[0]]
            if 'FROM answer_options' in sql: return [{'id':O,'body':'yes','is_correct':True},{'id':U,'body':'no','is_correct':False}]
            if 'INSERT INTO question_revisions' in sql: return {'id':A}
            if 'INSERT INTO test_versions' in sql: return {'id':A}
            raise AssertionError(sql)
        with patch.object(t.db,'transaction',transaction),patch.object(t,'own_department',return_value=A),\
             patch.object(t,'current_blocks',return_value=blocks),\
             patch.object(t.db,'query',side_effect=query),patch.object(t.db,'execute') as execute:
            t.publish(Request(roles=('kk_responsible',), data={'expected_token': t.expected_token(test, blocks, None)}),A)
        self.assertLess(next(i for i,s in enumerate(seen) if 'ORDER BY id FOR UPDATE' in s),
                        next(i for i,s in enumerate(seen) if 'INSERT INTO question_revisions' in s))
        self.assertEqual(sum('INSERT INTO question_revisions' in s for s in seen),4)
        self.assertIn("status='published'",execute.call_args.args[0])

    def test_private_question_key_requires_responsible_of_department(self):
        with patch.object(t.db,'transaction',transaction),patch.object(t,'own_department',side_effect=PermissionError),\
             patch.object(t.db,'query') as query:
            with self.assertRaises(PermissionError): t.question_detail(Request(),Q)
            query.assert_not_called()
        with patch.object(t.db,'transaction',transaction),patch.object(t,'own_department',return_value=A),\
             patch.object(t.db,'query',side_effect=[{'id':Q,'body':'Question','import_item_id':O,'import_id':B},
                                                    [{'id':O,'body':'yes','is_correct':True}]]) as query:
            response=t.question_detail(Request(roles=('kk_responsible',)),Q)
        self.assertIn(b'is_correct',response.body)
        self.assertEqual(json.loads(response.body)['import_id'],B)
        self.assertIn('LEFT JOIN question_import_items',query.call_args_list[0].args[0])
        self.assertEqual(query.call_args_list[0].args[1],(Q,A))

    def test_assignment_deadline_checked_with_database_clock_in_transaction(self):
        req=Request(roles=('manager',),data={'user_id':U,'test_id':A,'due_at':'2090-01-01T00:00:00+00:00'})
        def query(sql,*args,**kwargs):
            if 'INSERT INTO test_assignments' in sql: return None
            if 'FROM tests WHERE' in sql: return {'status': 'published'}
            if 'SELECT max(version)' in sql: return {'version': 1}
            if 'SELECT frozen_structure' in sql: return {'frozen_structure': {'competencies': [{'competency_id': C}]}}
            if 'FROM competency_profiles' in sql: return [{'competency_id': C}]
            return {'?column?':1}
        with patch.object(t.db,'transaction',transaction),patch.object(t,'own_department',return_value=B),\
             patch.object(t.db,'query',side_effect=query) as queried:
            with self.assertRaisesRegex(ValueError,'future'): t.assign(req)
        self.assertIn('clock_timestamp()',queried.call_args.args[0])

    def test_request_initialization_failure_returns_500_with_api_headers(self):
        responses=[]
        with patch.object(wsgi,'Request',side_effect=RuntimeError('db unavailable')),\
             patch('sys.stderr',new_callable=io.StringIO):
            body=wsgi.application({'PATH_INFO':'/api/testing/results'},
                                  lambda status,headers: responses.append((status,headers)))
        self.assertEqual(responses[0][0],'500 Internal Server Error')
        self.assertIn(('Cache-Control','no-store'),responses[0][1])
        self.assertNotIn(b'db unavailable',body[0])

    def test_transaction_rolls_back_status_and_reservations_on_write_failure(self):
        class Conn:
            committed=False
            rolled_back=False
            closed=False
            def commit(self): self.committed=True
            def rollback(self): self.rolled_back=True
            def close(self): self.closed=True
        conn=Conn()
        with patch.object(db,'get_conn',return_value=conn),patch.object(t,'lock_user'),\
             patch.object(t.db,'query',side_effect=[{'status':'assigned','assignment_type':'manager_assigned','due_at':FUTURE,'frozen_structure':{
                 'competencies':[{'competency_id':C,'block_id':B,'questions':[{'id':Q},{'id':Q2}]}]}},
                 {'valid':True},None,None,{'id':A}]),patch.object(t.db,'execute',side_effect=RuntimeError('write failed')):
            with self.assertRaisesRegex(RuntimeError,'write failed'): t.start(Request(),A)
        self.assertTrue(conn.rolled_back)
        self.assertFalse(conn.committed)
        self.assertTrue(conn.closed)

    def test_failure_after_status_update_rolls_back_everything(self):
        class Conn:
            committed=False
            rolled_back=False
            def commit(self): self.committed=True
            def rollback(self): self.rolled_back=True
            def close(self): pass
        conn=Conn()
        def query(sql,*args,**kwargs):
            if ' AS valid' in sql: return {'valid':True}
            if 'FROM test_assignments a' in sql: return {'status':'assigned','assignment_type':'manager_assigned','due_at':FUTURE,
                'frozen_structure':{'competencies':[{'competency_id':C,'block_id':B,
                                                     'questions':[{'id':Q,'revision_id':A},{'id':Q2,'revision_id':A}]}]}}
            if 'FROM assessment_cooldowns' in sql or 'FROM test_competency_reservations' in sql: return None
            if 'INSERT INTO test_attempts' in sql: return {'id':A}
            raise AssertionError(sql)
        seen=[]
        def execute(sql,*args,**kwargs):
            seen.append(sql)
            if "status='in_progress'" in sql: raise RuntimeError('post-write failure')
        with patch.object(db,'get_conn',return_value=conn),patch.object(t,'lock_user'),\
             patch.object(t.db,'query',side_effect=query),patch.object(t.db,'execute',side_effect=execute):
            with self.assertRaisesRegex(RuntimeError,'post-write failure'): t.start(Request(),A)
        self.assertTrue(any('INSERT INTO test_competency_reservations' in s for s in seen))
        self.assertTrue(conn.rolled_back)
        self.assertFalse(conn.committed)


if __name__ == '__main__': unittest.main()

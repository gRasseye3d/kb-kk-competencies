"""First acceptance stage (stage1) contracts; no database/server required."""
import io
import json
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

from app import router, wsgi
from app.handlers import testing as t, history, pages
from test_testing import Request, transaction, U, A, B, C, Q, Q2, O


class NormalJsonContracts(unittest.TestCase):
    def test_normal_admin_and_page_json_pass_strict_parser(self):
        # Exercise the actual Request/parser/router, including nested values,
        # Unicode and repeated key names in DIFFERENT objects (not duplicates).
        cases = (
            ('/api/admin/spaces', {'name':'Пространство','space_key':'STAGE1'}),
            ('/api/pages', {'space_id':B,'title':'Страница','content':'<p>JSON</p>',
                            'labels':[],'parent_id':None}),
        )
        for path, data in cases:
            raw=json.dumps(dict(data,csrf='csrf',metadata=[{'key':1},{'key':2}])).encode()
            with self.subTest(path=path), patch.object(router,'current_user',return_value=Request(roles=('admin',)).user), \
                    patch.object(pages.db,'transaction',transaction), patch.object(pages.db,'query',return_value={'id':A}), \
                    patch.object(pages.db,'execute'), patch.object(pages.access,'space',return_value=True), \
                    patch.object(pages.audit,'log') as audit:
                statuses=[]
                body=b''.join(wsgi.application({'PATH_INFO':path,'REQUEST_METHOD':'POST',
                    'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),
                    'wsgi.input':io.BytesIO(raw)},lambda status,_:statuses.append(status)))
                self.assertEqual(statuses,['200 OK'])
                self.assertEqual(json.loads(body),{'id':A})
                audit.assert_called_once()


class SubmissionContracts(unittest.TestCase):
    def query(self, sql, params=(), **kwargs):
        if sql.startswith('SELECT id FROM users'): return {'id':U}
        if 'FROM test_attempts t' in sql: return {'status':'in_progress','assignment_id':A,
                                                 'immutable_snapshot':{'competencies':[]}}
        if 'FROM attempt_competencies' in sql:
            self.assertIn("state='included'", sql)
            return [{'competency_id':C,'question_count':2}]
        if 'FROM attempt_questions' in sql:
            return [{'question_id':qid,'competency_id':C,'frozen_question':{
                'type':'single','options':[{'id':O,'correct':True},{'id':B,'correct':False}]}}
                for qid in (Q,Q2)]
        if 'INSERT INTO attempt_answers' in sql: return {'id':A}
        raise AssertionError(sql)

    def test_omitted_empty_and_partial_grade_without_scoring_exclusions(self):
        for data,correct in (({},0),({'answers':{}},0),({'answers':{Q:[],Q2:[]}},0),
                             ({'answers':{Q:[O]}},1),({'answers':{Q.replace('-',''):[O]}},1)):
            with self.subTest(data=data), patch.object(t.db,'transaction',transaction), \
                    patch.object(t.db,'query',side_effect=self.query) as query, patch.object(t.db,'execute') as execute:
                result=json.loads(t.submit(Request(data=data),A).body)['results']
                self.assertEqual(result,[{'competency_id':C,'correct':correct,'total':2,'level':correct}])
                answers=[c.args[1] for c in query.call_args_list if 'INSERT INTO attempt_answers' in c.args[0]]
                self.assertEqual(len(answers),2)
                self.assertEqual(sum(row[2] for row in answers),correct)
                for call in execute.call_args_list:
                    if any(table in call.args[0] for table in ('test_competency_results','competency_assessment_events',
                                                             'employee_competencies','assessment_cooldowns')):
                        self.assertIn(C,call.args[1])
                        self.assertNotIn(B,call.args[1])
                score=[c.args[1][0] for c in execute.call_args_list if "status='graded'" in c.args[0]]
                self.assertEqual(score,[correct*50])

    def test_malformed_duplicate_and_foreign_answers_are_400_not_500(self):
        invalid=[None,[],False,'', {Q:None},{Q:{}},{Q:'x'}, {Q:[None]}, {Q:[{}]},
                 {Q:['bad']},{'bad':[]},{B:[]},{Q:[A]}, {Q:[O,O]}, {Q:[int(O.replace('-',''))]},
                 {Q:[O,O.replace('-','')]},{Q:[],Q.replace('-',''):[]},{Q:[O,B]}]
        for answers in invalid:
            with self.subTest(answers=answers), patch.object(t.db,'transaction',transaction), \
                    patch.object(t.db,'query',side_effect=self.query), patch.object(t.db,'execute'), \
                    patch.object(wsgi,'Request',return_value=Request(data={'answers':answers})), \
                    patch.object(wsgi.app_router,'dispatch',side_effect=lambda req:t.submit(req,A)):
                statuses=[]
                wsgi.application({'PATH_INFO':'/api/testing'},lambda status,_:statuses.append(status))
                self.assertEqual(statuses,['400 Bad Request'])

    def test_duplicate_raw_json_and_nonobject_requests_are_400(self):
        for raw in ('null','true','12','"text"','[]',
                    '{"csrf":"csrf","answers":{"'+Q+'":[],"'+Q+'":[]}}'):
            with self.subTest(raw=raw), patch.object(router,'current_user',return_value=Request().user):
                statuses=[]
                env={'PATH_INFO':f'/api/testing/attempts/{A}/submit','REQUEST_METHOD':'POST',
                     'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),
                     'wsgi.input':io.BytesIO(raw.encode())}
                wsgi.application(env,lambda status,_:statuses.append(status))
                self.assertEqual(statuses,['400 Bad Request'])

    def test_failure_after_answer_write_rolls_back_submission(self):
        events=[]
        @contextmanager
        def atomic():
            try:
                yield object()
                events.append('commit')
            except Exception:
                events.append('rollback')
                raise
        with patch.object(t.db,'transaction',atomic), patch.object(t.db,'query',side_effect=self.query), \
                patch.object(t.db,'execute',side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError): t.submit(Request(),A)
        self.assertEqual(events,['rollback'])

    def test_multiple_choice_still_requires_exact_frozen_key(self):
        def query(sql,params=(),**kwargs):
            result=self.query(sql,params,**kwargs)
            if 'FROM attempt_questions' in sql:
                for row in result:
                    row['frozen_question']['type']='multiple'
                    row['frozen_question']['options'][1]['correct']=True
            return result
        for selected,correct in (([],0),([O],0),([O,B],1)):
            with self.subTest(selected=selected), patch.object(t.db,'transaction',transaction), \
                    patch.object(t.db,'query',side_effect=query), patch.object(t.db,'execute'):
                result=json.loads(t.submit(Request(data={'answers':{Q:selected}}),A).body)['results'][0]
                self.assertEqual((result['correct'],result['total'],result['level']),(correct,2,correct))


class DeadlineContracts(unittest.TestCase):
    def test_direct_assignment_requests_return_400_for_missing_invalid_deadline(self):
        for payload in ({}, *({'due_at':due} for due in (None,'',[],{},True,'2090-01-01T12:00:00'))):
            raw=json.dumps(dict(payload,user_id=U,test_id=A,csrf='csrf',assignment_type='self_assigned')).encode()
            with self.subTest(payload=payload), patch.object(router,'current_user',return_value=Request(roles=('manager',)).user), \
                    patch.object(t.db,'transaction') as tx:
                statuses=[]
                wsgi.application({'PATH_INFO':'/api/testing/assignments','REQUEST_METHOD':'POST',
                    'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),
                    'wsgi.input':io.BytesIO(raw)},lambda status,_:statuses.append(status))
                self.assertEqual(statuses,['400 Bad Request'])
                tx.assert_not_called()

    def test_required_shape_cannot_be_bypassed_by_assignment_type(self):
        for due in (None,'',' ','2090-01-01','2090-01-01T12:00:00',False,[],{},42,'bad'):
            with self.subTest(due=due), patch.object(t.db,'transaction') as transaction_mock:
                with self.assertRaises(ValueError):
                    t.assign(Request(roles=('manager',),data={'user_id':U,'test_id':A,
                             'due_at':due,'assignment_type':'self_assigned'}))
                transaction_mock.assert_not_called()

    def test_offset_normalized_to_utc_and_checked_by_db_in_transaction(self):
        def query(sql, params=(), **kwargs):
            if 'FROM tests WHERE' in sql: return {'id': A, 'status': 'published'}
            if sql.startswith('SELECT max(version)'): return {'version': 1}
            if 'SELECT frozen_structure' in sql:
                return {'frozen_structure': {'competencies': [{'competency_id': C}]}}
            if 'FROM competency_profiles' in sql: return [{'competency_id': C}]
            return {'id': A}
        for due in ('2090-01-01T00:00:00Z','2090-01-01T03:00:00+03:00'):
            with self.subTest(due=due), patch.object(t.db,'transaction',transaction), \
                    patch.object(t,'own_department',return_value=B), \
                    patch.object(t.db,'query',side_effect=query) as queried:
                t.assign(Request(roles=('manager',),data={'user_id':U,'test_id':A,'due_at':due,
                                                        'assignment_type':'self_assigned'}))
                sql,params=queried.call_args.args
                self.assertIn('clock_timestamp()',sql)
                self.assertNotIn('IS NULL',sql)
                self.assertNotIn('self_assigned',params)
                self.assertEqual(params[-1],datetime(2090,1,1,tzinfo=timezone.utc))
                self.assertIs(params[-1].tzinfo,timezone.utc)
                self.assertIsNotNone(queried.call_args.kwargs['conn'])

    def test_legacy_manager_without_deadline_has_public_conflict_no_writes(self):
        assignment={'status':'assigned','due_at':None,'assignment_type':'manager_assigned'}
        with patch.object(t.db,'transaction',transaction), patch.object(t,'lock_user'), \
                patch.object(t.db,'query',return_value=assignment), patch.object(t.db,'execute') as execute, \
                patch.object(wsgi,'Request',return_value=Request()), \
                patch.object(wsgi.app_router,'dispatch',side_effect=lambda req:t.start(req,A)):
            statuses=[]
            body=b''.join(wsgi.application({'PATH_INFO':'/api/testing'},lambda status,_:statuses.append(status)))
            self.assertEqual(statuses,['409 Conflict'])
            self.assertEqual(json.loads(body)['code'],'due_missing')
            execute.assert_not_called()
            self.assertIsNone(assignment['due_at'])


class HistoryContracts(unittest.TestCase):
    def setUp(self):
        self.page={'id':A,'space_id':B,'created_by':U,'deleted_at':None,
                   'status':'published','current_version':1,'title':'Title','parent_id':None}

    def test_employee_and_kk_even_owner_cannot_read_or_restore(self):
        for roles in (('employee',),('employee','kk_responsible'),('kk_responsible',),()):
            with self.subTest(roles=roles), patch.object(history,'_page') as page:
                with self.assertRaises(PermissionError): history.list_hist(Request(roles=roles),A)
                with self.assertRaises(PermissionError): history.restore(Request(roles=roles),A,Q)
                page.assert_not_called()

    def test_allowlist_additive_but_read_and_write_acl_still_required(self):
        for role in ('admin','moderator','manager','eol','EOL','god'):
            for allowed in (True,False):
                with self.subTest(role=role,allowed=allowed), patch.object(history,'_page',return_value=self.page), \
                        patch.object(history.db,'transaction',transaction), \
                        patch.object(history.access,'space',return_value=allowed), \
                        patch.object(history.db,'query',side_effect=lambda sql,*a,**kw:
                            {'restricted':False,'allowed':False} if 'SELECT EXISTS' in sql else
                            {'title':'Restored','content':{'html':'safe'}} if 'SELECT * FROM page_revisions' in sql else []), \
                        patch.object(history.db,'execute') as execute, patch.object(history.audit,'log'):
                    req=Request(roles=('employee','kk_responsible',role))
                    response=history.list_hist(req,A)
                    self.assertEqual(response.status,'200 OK' if allowed else '404 Not Found')
                    if allowed:
                        self.assertEqual(history.restore(req,A,Q).status,'200 OK')
                    else:
                        with self.assertRaises(PermissionError): history.restore(req,A,Q)
                        execute.assert_not_called()

    def test_nonowner_restricted_and_readonly_restore_denied(self):
        for restricted,owner,status in ((True,False,'published'),(False,False,'published'),
                                        (False,True,'pending_review'),(False,True,'archived')):
            self.page.update(created_by=U if owner else C,status=status)
            with self.subTest(restricted=restricted,owner=owner,status=status), \
                    patch.object(history,'_page',return_value=self.page), patch.object(history.db,'transaction',transaction), \
                    patch.object(history.access,'space',return_value=True), \
                    patch.object(history.db,'query',return_value={'restricted':restricted,'allowed':False}), \
                    patch.object(history.db,'execute') as execute:
                req=Request(roles=('employee','manager'))
                if restricted: self.assertEqual(history.list_hist(req,A).status,'404 Not Found')
                with self.assertRaises(PermissionError): history.restore(req,A,Q)
                execute.assert_not_called()

    def test_restore_still_requires_csrf(self):
        req=Request(roles=('admin',))
        req.json=lambda: {'csrf':'wrong'}
        with self.assertRaises(PermissionError): history.restore(req,A,Q)

    def test_page_exposes_write_acl_for_restore_visibility(self):
        with patch.object(pages,'_page',return_value=self.page), \
                patch.object(pages.access,'page',side_effect=[True,False]) as acl, \
                patch.object(pages.db,'query',side_effect=[{'content':{}},[]]):
            self.assertFalse(json.loads(pages.get_page(Request(roles=('manager',)),A).body)['can_edit'])
            self.assertTrue(acl.call_args.kwargs['write'])

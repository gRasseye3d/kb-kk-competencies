import unittest
import io
import json
from contextlib import contextmanager
from unittest.mock import patch
from app.handlers import competencies as c, admin
from app.utils import ConflictError, PreconditionRequired
from app import wsgi

D = '11111111-1111-4111-8111-111111111111'
P = '22222222-2222-4222-8222-222222222222'
U = 'aaaaaaaa-3333-4333-8333-333333333333'
C = 'bbbbbbbb-4444-4444-8444-444444444444'


class Request:
    def __init__(self, roles, data=None):
        self.user = {'user_id': U, 'roles': roles, 'csrf_token': 'token'}
        self.data = data or {}

    def json(self):
        return dict(self.data, csrf='token')


@contextmanager
def tx():
    yield object()


class CompetencyTests(unittest.TestCase):
    def test_malformed_uuid_rejected(self):
        with self.assertRaises(ValueError):
            c.ident('not-a-uuid')
        with patch.object(c.db, 'transaction', tx), patch.object(c.db, 'query'):
            with self.assertRaises(ValueError):
                c.review(Request(['manager'], {'decision':'approve','expected_active_id':None,'expected_active_version':None}), 'bad')

    def test_department_manager_requires_role_and_assignment(self):
        with patch.object(c.db, 'query', return_value={'id':D,'is_active':True,'manager_id':U}):
            with self.assertRaises(PermissionError):
                c.department(None,D,{'user_id':U,'roles':['admin']},'manager')
        with patch.object(c.db, 'query', return_value={'id':D,'is_active':True,'manager_id':P}):
            with self.assertRaises(PermissionError):
                c.department(None,D,{'user_id':U,'roles':['manager']},'manager')

    def test_responsible_requires_role_and_assignment(self):
        user = {'user_id':U,'roles':['kk_responsible']}
        with patch.object(c.db, 'query', side_effect=[{'id':D,'is_active':True},None]):
            with self.assertRaises(PermissionError):
                c.department(None,D,user,'responsible')
        with patch.object(c.db, 'query', return_value={'id':D,'is_active':True}):
            with self.assertRaises(PermissionError):
                c.department(None,D,{'user_id':U,'roles':['admin']},'responsible')

    def test_wrong_department_and_non_pending_review_rejected(self):
        req = Request(['manager'], {'decision':'approve','expected_active_id':None,'expected_active_version':None})
        with patch.object(c.db,'transaction',tx), patch.object(c,'profile',return_value={'department_id':D,'status':'pending_approval'}), \
             patch.object(c,'department',side_effect=PermissionError('other department')), patch.object(c.db,'execute') as execute:
            with self.assertRaises(PermissionError): c.review(req,P)
            execute.assert_not_called()
        with patch.object(c.db,'transaction',tx), patch.object(c,'profile',return_value={'department_id':D,'status':'approved'}), \
             patch.object(c,'department'), patch.object(c.db,'execute') as execute:
            with self.assertRaises(ConflictError) as conflict: c.review(req,P)
            self.assertIsNone(conflict.exception.code)
            self.assertIsNone(conflict.exception.details)
            execute.assert_not_called()

    def test_approval_switches_active_in_single_transaction(self):
        req = Request(['manager'], {'decision':'approve','expected_active_id':None,'expected_active_version':None})
        operations = []
        with patch.object(c.db,'transaction',tx), patch.object(c,'profile',return_value={'department_id':D,'status':'pending_approval'}), \
             patch.object(c,'department'), patch.object(c.db,'query',return_value=None), \
             patch.object(c.db,'execute',side_effect=lambda sql,params,conn:operations.append(sql)), \
             patch.object(c.audit,'log'):
            c.review(req,P)
        self.assertEqual(len(operations),2)
        self.assertIn('is_active=FALSE', operations[0])
        self.assertIn('is_active=TRUE', operations[1])

    def test_approval_requires_explicit_expected_active_and_conflicts_on_stale(self):
        with self.assertRaisesRegex(PreconditionRequired, 'expected_active_id'):
            c.review(Request(['manager'], {'decision':'approve'}), P)
        req = Request(['manager'], {'decision':'approve','expected_active_id':None,'expected_active_version':None})
        with patch.object(c.db,'transaction',tx), patch.object(c,'profile',return_value={'department_id':D,'status':'pending_approval'}), \
             patch.object(c,'department'), patch.object(c.db,'query',return_value={'id':C}), \
             patch.object(c.db,'execute') as execute:
            with self.assertRaises(ConflictError): c.review(req,P)
            execute.assert_not_called()
        req.data['expected_active_id'] = C.upper()
        req.data['expected_active_version'] = 2
        with patch.object(c.db,'transaction',tx), patch.object(c,'profile',return_value={'department_id':D,'status':'pending_approval'}), \
             patch.object(c,'department'), patch.object(c.db,'query',return_value={'id':C,'version':2}), \
             patch.object(c.db,'execute') as execute, patch.object(c.audit,'log'):
            c.review(req,P)
            self.assertEqual(execute.call_count,2)

    def test_approval_rejects_stale_version_and_malformed_precondition(self):
        for data in ({'expected_active_id':C}, {'expected_active_id':None}):
            with self.assertRaises(PreconditionRequired):
                c.review(Request(['manager'], dict(decision='approve', **data)), P)
        for expected_id, version in ((C, True), (C, 0), (None, 1), ('bad', 1)):
            with self.assertRaises(ValueError):
                c.review(Request(['manager'], {'decision':'approve',
                    'expected_active_id':expected_id,'expected_active_version':version}), P)
        req = Request(['manager'], {'decision':'approve','expected_active_id':C,'expected_active_version':1})
        with patch.object(c.db,'transaction',tx), patch.object(c,'profile',return_value={'department_id':D,'status':'pending_approval'}), \
             patch.object(c,'department'), patch.object(c.db,'query',return_value={'id':C,'version':2}), \
             patch.object(c.db,'execute') as execute:
            with self.assertRaises(ConflictError): c.review(req,P)
            execute.assert_not_called()

    def test_self_role_and_delete_guards_normalize_uuid(self):
        for handler in (admin.set_roles,admin.delete_user):
            req = Request(['admin'], {'roles':['employee']})
            with patch.object(admin.db,'transaction') as transaction:
                with self.assertRaises(PermissionError):
                    handler(req,U.upper())
                transaction.assert_not_called()
            with self.assertRaises(ValueError): handler(req,'invalid')

    def test_department_change_requires_existing_active_department(self):
        with patch.object(admin.db,'query',return_value=None):
            with self.assertRaises(ValueError): admin.department_id(None,D)
        with self.assertRaises(ValueError): admin.department_id(None,'bad')

    def test_create_user_assigns_department_and_competency_role(self):
        data = {'login':'manager1','email':'m@example.org','full_name':'Руководитель',
                'password':'long-enough-password','department_id':D,'roles':['manager']}
        with patch.object(admin.db,'transaction',tx), patch.object(admin.db,'query',side_effect=[
                {'?column?':1}, {'id':U}, {'id':P}]) as query, \
             patch.object(admin.db,'execute') as execute, patch.object(admin.audit,'log'):
            admin.create_user(Request(['admin'],data))
            self.assertEqual(query.call_args_list[0].args[1],(D,))
            self.assertEqual(query.call_args_list[1].args[1][-1],D)
            self.assertEqual(query.call_args_list[2].args[1],('manager',))
            self.assertEqual(execute.call_args.args[1],(U,P))

    def test_set_manager_requires_explicit_null_to_clear(self):
        with self.assertRaises(ValueError):
            c.set_manager(Request(['admin']),D)
        with self.assertRaises(ValueError):
            c.set_manager(Request(['admin'],{'manager_id':''}),D)

    def test_seed_roles_requires_admin_and_csrf_and_is_idempotent(self):
        with self.assertRaises(PermissionError):
            admin.seed_competency_roles(Request(['employee']))
        with patch.object(admin.db,'transaction',tx), patch.object(admin.db,'execute') as execute, \
             patch.object(admin.audit,'log'):
            admin.seed_competency_roles(Request(['admin']))
            self.assertIn('ON CONFLICT(code) DO NOTHING',execute.call_args.args[0])

    def test_conflict_and_missing_precondition_http_status(self):
        environ = {'REQUEST_METHOD':'POST', 'PATH_INFO':'/api/example', 'wsgi.input':io.BytesIO()}
        for error, status in ((ConflictError('stale'), '409 Conflict'),
                              (PreconditionRequired('missing'), '428 Precondition Required')):
            results = []
            with patch.object(wsgi.app_router,'dispatch',side_effect=error):
                body = wsgi.application(environ, lambda code,headers: results.append((code,headers)))
            self.assertEqual(results[0][0],status)
            self.assertEqual(json.loads(b''.join(body)),{'error':str(error)})

    def test_admin_departments_not_exposed_to_employee(self):
        with patch.object(admin.db,'query') as query:
            with self.assertRaises(PermissionError): admin.list_departments(Request(['employee']))
            query.assert_not_called()

    def test_zero_and_missing_assessment_distinct(self):
        with patch.object(c.db,'query',return_value=[]) as query:
            c.mine(Request(['employee']))
            sql = query.call_args.args[0]
            self.assertIn('ec.user_id IS NULL',sql)
            self.assertIn('ec.current_level::text',sql)
            self.assertIn('ec.user_id=u.id',sql)

    def test_profile_cannot_submit_without_competencies(self):
        with patch.object(c.db,'transaction',tx), patch.object(c,'profile',return_value={'department_id':D,'status':'draft'}), \
             patch.object(c,'department'), patch.object(c.db,'query',return_value=None), patch.object(c.db,'execute') as execute:
            with self.assertRaises(ValueError): c.submit(Request(['kk_responsible']),P)
            execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()

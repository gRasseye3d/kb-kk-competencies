"""Fast validation and request-size guards; real SQL/API exercised in PG integration."""
import io
import json
import unittest
from unittest.mock import patch

from app import wsgi
from app.question_import_validation import duplicates, parse, safe_json, validate
from app.handlers.question_imports import (conditional, effective_duplicates, token, upload,
                                           from_generated, generated_item, guarded_source, source_pages)
from app.utils import ConflictError, PreconditionRequired


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.item = dict(external_id='x', competency_code='A', question='Q', type='single',
                         options=[dict(id='a', text='yes', is_correct=True),
                                  dict(id='b', text='no', is_correct=False)])

    def check(self, item, duplicate=False):
        with patch('app.question_import_validation.db.query', return_value={'exists': True}):
            return validate(None, 'department', {'user_id': 'actor'}, item, duplicate)

    def test_json_shape_duplicate_keys_and_exact_utf8_limit(self):
        for text in ('{"questions":[{"x":1,"x":2}]}', 'null', '{}', '[]',
                     '{"questions":[]}', json.dumps([{}] * 201)):
            with self.subTest(text=text[:50]), self.assertRaises(ValueError):
                parse(text)
        self.assertEqual(parse(json.dumps({'questions': [1, {}, None]}))[1], [1, {}, None])
        with self.assertRaises(ValueError):
            parse(' ' * (512 * 1024 - 1) + 'Я')
        for invalid in ('[{"x": 1e309}]', '[{"x": NaN}]',
                        '[{"x": "\\u0000"}]', '[{"x": "\\ud800"}]',
                        '[' * 21 + '1' + ']' * 21):
            with self.subTest(invalid=invalid[:50]), self.assertRaises(ValueError):
                parse(invalid)

    def test_strict_questions_and_options(self):
        self.assertEqual(self.check(self.item), [])
        self.assertIn('Duplicate external_id in import', self.check(self.item, True))
        for change in ({'question': '', 'body': 'other'}, {'type': 'single', 'options': [
                dict(id='a', text='A', correct=True), dict(id='a', text='B', correct=True)]},
                       {'options': [dict(id='a', text='A', correct=1), dict(id='b', text='B', correct=False)]},
                       {'source_revision_id': 'bad'}, {'explanation': 'x' * 5001}):
            with self.subTest(change=change):
                self.assertTrue(self.check(dict(self.item, **change)))
        self.assertEqual(self.check(None), ['Question must be an object'])
        self.assertEqual(duplicates([self.item, self.item, None]), {'x'})

    def test_optimistic_token_changes_without_exposing_answer_key(self):
        row = dict(id='id', status='pending', edited_payload=self.item, validation_errors=[],
                   reviewed_at=None, reviewed_by=None, rejection_reason=None, question_id=None)
        original = token(row)
        self.assertEqual(len(original), 64)
        self.assertNotIn('yes', original)
        conditional({'expected_token': original}, row)
        with self.assertRaises(PreconditionRequired):
            conditional({}, row)
        row['validation_errors'] = ['bad']
        with self.assertRaises(ConflictError):
            conditional({'expected_token': original}, row)

    def test_upload_rejects_oversize_before_stream_read(self):
        class Unreadable:
            def read(self, *args):
                raise AssertionError('body was read')
        req = type('Req', (), {'environ': {'CONTENT_LENGTH': str(1024 * 1024 + 1),
                                            'wsgi.input': Unreadable()}})()
        result = upload(req)
        self.assertEqual(result.status, '413 Payload Too Large')
        self.assertIn(b'1 MiB', result.body)
        req.environ['CONTENT_LENGTH'] = ''
        with self.assertRaises(ValueError):
            upload(req)

    def test_effective_external_id_lookup_never_reads_peer_payloads(self):
        with patch('app.handlers.question_imports.db.query') as query:
            self.assertFalse(effective_duplicates(None, 'import', 'item', 42))
            query.assert_not_called()
        with patch('app.handlers.question_imports.db.query', return_value={'exists': True}) as query:
            self.assertTrue(effective_duplicates(None, 'import', 'item', {'external_id': 'x'}))
            sql, params = query.call_args.args
            self.assertIn('external_id=%s', sql)
            self.assertNotIn('payload', sql.lower())
            self.assertEqual(params, ('import', 'item', 'x'))
            self.assertTrue(query.call_args.kwargs['one'])
        with patch('app.handlers.question_imports.db.query') as query:
            self.assertFalse(effective_duplicates(None, 'import', 'item', {'external_id': ''}))
            query.assert_not_called()

    def request(self, method, path, raw, length=None):
        content = raw.encode('utf-8') if isinstance(raw, str) else raw
        environ = dict(REQUEST_METHOD=method, PATH_INFO=path, CONTENT_TYPE='application/json',
                       CONTENT_LENGTH=str(len(content)) if length is None else length,
                       **{'wsgi.input': io.BytesIO(content)})
        outputs = []
        user = {'user_id': 'user', 'csrf_token': 'secret', 'roles': ['kk_responsible']}
        with patch('app.router.current_user', return_value=user), \
                patch('app.handlers.question_imports.db.transaction') as txn:
            result = b''.join(wsgi.application(environ, lambda status, headers: outputs.append(status)))
            txn.assert_not_called()
        return outputs[0], json.loads(result)

    def test_invalid_upload_never_writes_and_returns_json_error(self):
        for text in ('[{"x": 1e309}]', '[{"x": NaN}]', '[{"x": "\\u0000"}]',
                     '[{"x": "\\ud800"}]', '[' * 21 + '1' + ']' * 21):
            with self.subTest(text=text[:50]):
                body = json.dumps({'csrf': 'secret', 'filename': 'x.json', 'json_text': text})
                status, error = self.request('POST', '/api/testing/imports', body)
                self.assertEqual(status, '400 Bad Request')
                self.assertIn('error', error)
        status, error = self.request('POST', '/api/testing/imports', '{}', 'garbage')
        self.assertEqual(status, '400 Bad Request')
        body = json.dumps({'csrf': 'secret', 'filename': 'x.json',
                           'json_text': '["' + 'a' * (512 * 1024) + '"]'})
        status, error = self.request('POST', '/api/testing/imports', body)
        self.assertEqual(status, '400 Bad Request')
        self.assertIn('error', error)
        status, error = self.request('POST', '/api/testing/imports', '{}', str(1024 * 1024 + 1))
        self.assertEqual(status, '413 Payload Too Large')

    def test_invalid_edit_never_writes_or_reads_oversized_body(self):
        path = '/api/testing/imports/00000000-0000-0000-0000-000000000001/items/00000000-0000-0000-0000-000000000002'
        for payload in ({'x': float('inf')}, {'x': float('nan')}, {'x': 10 ** 400},
                        {'x': '\x00'}, {'x': '\ud800'},
                        {'x': json.loads('[' * 21 + '1' + ']' * 21)}):
            with self.subTest(payload=str(payload)[:60]):
                raw = json.dumps({'csrf': 'secret', 'expected_token': 'token', 'edited_payload': payload})
                status, error = self.request('PUT', path, raw)
                self.assertEqual(status, '400 Bad Request')
                self.assertIn('error', error)
        status, error = self.request('PUT', path, '{}', str(512 * 1024 + 1))
        self.assertEqual(status, '413 Payload Too Large')
        status, error = self.request('PUT', path, '{}', '')
        self.assertEqual(status, '400 Bad Request')
        raw = '{"csrf":"secret","edited_payload":{"x":1,"x":2}}'
        status, _ = self.request('PUT', path, raw)
        self.assertEqual(status, '400 Bad Request')

    def test_large_single_edit_rejected_before_db_even_under_request_cap(self):
        path = ('/api/testing/imports/00000000-0000-0000-0000-000000000001/items/'
                '00000000-0000-0000-0000-000000000002')
        body = json.dumps({'csrf': 'secret', 'expected_token': 'token',
                           'edited_payload': {'question': 'Я' * 17000}}, ensure_ascii=False)
        self.assertLess(len(body.encode('utf-8')), 512 * 1024)
        status, response = self.request('PUT', path, body)
        self.assertEqual(status, '413 Payload Too Large')
        self.assertIn('32 KiB', response['error'])

    def test_detail_limit_bounds_aggregate_edits_without_changing_frontend_limit(self):
        base = '/api/testing/imports/00000000-0000-0000-0000-000000000001'
        env = dict(REQUEST_METHOD='GET', PATH_INFO=base, QUERY_STRING='limit=51',
                   CONTENT_LENGTH='0', **{'wsgi.input': io.BytesIO(b'')})
        outputs = []
        with patch('app.router.current_user', return_value={'user_id': 'user',
                'csrf_token': 'secret', 'roles': ['kk_responsible']}), \
             patch('app.handlers.question_imports.db.transaction') as txn:
            response = b''.join(wsgi.application(env, lambda code, headers: outputs.append(code)))
            txn.assert_not_called()
        self.assertEqual(outputs, ['400 Bad Request'])
        self.assertIn(b'Invalid pagination', response)

    def test_review_rejects_oversized_body_before_read_or_db_access(self):
        path = '/api/testing/imports/00000000-0000-0000-0000-000000000001/items/00000000-0000-0000-0000-000000000002/review'
        class Unreadable:
            def read(self, *args):
                raise AssertionError('Oversized review body was read')
        env = dict(REQUEST_METHOD='POST', PATH_INFO=path, CONTENT_LENGTH=str(512 * 1024 + 1),
                   CONTENT_TYPE='application/json', **{'wsgi.input': Unreadable()})
        user = {'user_id': 'user', 'csrf_token': 'secret', 'roles': ['kk_responsible']}
        statuses = []
        with patch('app.router.current_user', return_value=user), \
                patch('app.handlers.question_imports.db.transaction') as txn:
            response = b''.join(wsgi.application(env, lambda status, headers: statuses.append(status)))
            txn.assert_not_called()
        self.assertEqual(statuses, ['413 Payload Too Large'])
        self.assertIn(b'512 KiB', response)

    def test_safe_json_accepts_finite_bounds(self):
        safe_json({'x': 1e308, 'y': 1e-308, 'z': 10 ** 100})
        with self.assertRaises(ValueError):
            safe_json({'x': 1e-309})

    def test_generated_correct_index_never_guesses_answer(self):
        source = {'page_id': 'page', 'revision_id': 'revision'}
        for raw in ({'question': 'Q', 'options': ['A', 'B'], 'correct_answer': 2},
                    {'question': 'Q', 'options': ['A', 'B'], 'correct_answer': True},
                    {'question': 'Q', 'options': ['A', 'B'], 'correct_answer': -1}):
            converted, errors = generated_item(raw, 0, source)
            self.assertTrue(errors)
            self.assertFalse(any(o['is_correct'] for o in converted['options']))
        converted, errors = generated_item({'question': 'Q', 'options': ['A', 'B'],
                                           'correct_answer': 0}, 1, source)
        self.assertEqual(errors, [])
        self.assertEqual(converted['external_id'], 'gen-2')
        self.assertEqual([o['is_correct'] for o in converted['options']], [True, False])

    def test_generated_constraints_and_csrf_before_db(self):
        path = '/api/testing/imports/from-generated'
        page = '00000000-0000-0000-0000-000000000001'
        base = {'csrf': 'secret', 'source_page_id': page, 'source_revision_id': page,
                'settings': {}, 'response': {'questions': [{}]}}
        for changes in ({'settings': {'questions_count': True}},
                        {'settings': {'answers_count': 9}},
                        {'settings': {'topics': ['']}} ,
                        {'response': {'questions': []}},
                        {'source_revision_id': 'bad'}, {'csrf': 'wrong'}):
            with self.subTest(changes=changes):
                status, _ = self.request('POST', path, json.dumps(dict(base, **changes)))
                self.assertIn(status, ('400 Bad Request', '403 Forbidden'))
        status, _ = self.request('POST', path, '{}', str(512 * 1024 + 1))
        self.assertEqual(status, '413 Payload Too Large')
        status, _ = self.request('POST', path, '{}', '')
        self.assertEqual(status, '400 Bad Request')

    def test_generated_source_tamper_conflicts_before_access_or_writes(self):
        source = {'page_id': 'page', 'revision_id': 'revision'}
        with patch('app.handlers.question_imports.source_page') as check:
            with self.assertRaises(ConflictError):
                guarded_source(None, 'dept', {}, {'payload': {'kind': 'generated_response_v1',
                    'source': source}}, {'source_page_id': 'other', 'source_revision_id': 'revision'})
            check.assert_not_called()

    def test_generated_preserves_original_response_and_invalid_items(self):
        import contextlib
        page = '00000000-0000-0000-0000-000000000001'
        response = {'questions': [42, {'question': 'Q', 'options': ['Yes', 'No'],
                                       'correct_answer': 99}], 'model_notes': 'unverified'}
        data = {'source_page_id': page, 'source_revision_id': page, 'settings': {},
                'response': response}
        req = type('Req', (), {'user': {'user_id': page, 'roles': ['kk_responsible']}})()
        with patch('app.handlers.question_imports.checked_body', return_value=(data, None)), \
             patch('app.handlers.question_imports.db.transaction', return_value=contextlib.nullcontext(None)), \
             patch('app.handlers.question_imports.own_department', return_value='dept'), \
             patch('app.handlers.question_imports.source_page'), \
             patch('app.handlers.question_imports.validation.validate', return_value=['competency_code required']), \
             patch('app.handlers.question_imports.db.query', return_value={'id': page}) as query, \
             patch('app.handlers.question_imports.db.execute') as execute:
            result = json.loads(from_generated(req).body)
        self.assertEqual((result['total'], result['status']), (2, 'validated'))
        self.assertIn('warning', result)
        envelope = json.loads(query.call_args.args[1][3])
        self.assertEqual(envelope['response'], response)
        self.assertEqual(envelope['source']['provenance'], 'selection_only')
        raw = [json.loads(call.args[1][3]) for call in execute.call_args_list]
        self.assertEqual(raw, response['questions'])
        edited = json.loads(execute.call_args_list[1].args[1][4])
        self.assertFalse(any(o['is_correct'] for o in edited['options']))
        self.assertEqual([call.args[1][-1] for call in execute.call_args_list], ['invalid', 'invalid'])

    def test_source_search_acl_precedes_pagination(self):
        import contextlib
        req = type('Req', (), {'q': lambda self, key: {'q': r'_%\\', 'limit': '20',
            'offset': '2'}.get(key), 'user': {'user_id': 'user', 'roles': ['kk_responsible']}})()
        with patch('app.handlers.question_imports.db.transaction', return_value=contextlib.nullcontext(None)), \
             patch('app.handlers.question_imports.own_department', return_value='dept'), \
             patch('app.handlers.question_imports.db.query', side_effect=[{'total': 0}, []]) as query:
            source_pages(req)
        sql_count, sql_page = [call.args[0] for call in query.call_args_list]
        for sql in (sql_count, sql_page):
            self.assertIn('page_permissions', sql)
            self.assertIn('space_members', sql)
            self.assertIn("p.status='published'", sql)
            self.assertIn('author.department_id', sql)
        self.assertNotIn('LIMIT', sql_count)
        self.assertLess(sql_page.index('page_permissions'), sql_page.index('LIMIT'))
        self.assertEqual(query.call_args.kwargs['conn'], None)


if __name__ == '__main__':
    unittest.main()

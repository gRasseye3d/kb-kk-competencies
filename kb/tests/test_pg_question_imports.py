"""Opt-in real PostgreSQL stage-3 import API tests; use a NEW empty kb_it_* DB.

Run separately from other PG modules: each module atomically bootstraps the schema
and refuses a database that already contains objects. Fixtures are rolled back.
"""
import io
import json
import secrets
import unittest

import test_pg_testing_integration as integration


@unittest.skipUnless(integration.os.environ.get('DATABASE_URL'), 'Requires a new kb_it_* database')
class QuestionImportPGTests(unittest.TestCase):
    setUpClass = classmethod(integration.PublishedTestApiIntegration.setUpClass.__func__)
    setUp = integration.PublishedTestApiIntegration.setUp
    _rollback_and_close = integration.PublishedTestApiIntegration._rollback_and_close
    row = integration.PublishedTestApiIntegration.row
    api = integration.PublishedTestApiIntegration.api
    seed = integration.PublishedTestApiIntegration.seed
    call = integration.PublishedTestApiIntegration.call

    def item(self, code, external, **changes):
        item = dict(external_id=external, competency_code=code, question='Import question', type='single',
                    options=[dict(id='a', text='Private correct option', is_correct=True),
                             dict(id='b', text='Wrong option', is_correct=False)])
        item.update(changes)
        return item

    def published_source(self, title='Visible', visibility='internal', author='employee'):
        sid = self.row('''INSERT INTO spaces(space_key,name,owner_id,visibility)
            VALUES(%s,%s,%s,%s) RETURNING id''',
            ('SRC_' + secrets.token_hex(6), 'Source space', self.users['admin'][0], visibility))[0]
        pid = self.row('''INSERT INTO pages(space_id,title,created_by,updated_by,status,current_version)
            VALUES(%s,%s,%s,%s,'published',1) RETURNING id''',
            (sid, title, self.users[author][0], self.users[author][0]))[0]
        rid = self.row('''INSERT INTO page_revisions(page_id,version,title,content,editor_id)
            VALUES(%s,1,%s,%s::jsonb,%s) RETURNING id''',
            (pid, title, '{}', self.users[author][0]))[0]
        return str(pid), str(rid), str(sid)

    def test_generated_selection_acl_and_review_provenance(self):
        self.seed()
        pid, rid, sid = self.published_source()
        private, private_rid, private_sid = self.published_source('Private title', 'private')
        restricted, restricted_rid, _ = self.published_source('Restricted title')
        self.row("INSERT INTO page_permissions(page_id,user_id,permission) VALUES(%s,%s,'view') RETURNING id",
                 (restricted, self.users['employee'][0]))
        first_visible, first_revision, _ = self.published_source('A title')
        last_visible, last_revision, _ = self.published_source('Z title')
        draft, _, _ = self.published_source('B draft title')
        self.row("UPDATE pages SET status='draft' WHERE id=%s RETURNING id", (draft,))
        prefix = '/api/testing/imports/source-pages'
        listing, _ = self.call('kk_responsible', 'GET', prefix + '?q=title&limit=20&offset=0')
        self.assertEqual(([(x['id'], x['current_revision_id']) for x in listing['items']], listing['total']),
                         ([(first_visible, first_revision), (last_visible, last_revision)], 2))
        listing, _ = self.call('kk_responsible', 'GET', prefix + '?q=title&limit=1&offset=1')
        self.assertEqual((listing['total'], listing['items'][0]['id']), (2, last_visible))
        listing, _ = self.call('kk_responsible', 'GET', prefix + '?q=title&limit=1&offset=2')
        self.assertEqual((listing['total'], listing['items']), (2, []))
        listing, _ = self.call('kk_responsible', 'GET', prefix + '?q=Visible&limit=1&offset=0')
        self.assertEqual((listing['total'], listing['items'][0]['id'], listing['items'][0]['current_revision_id']),
                         (1, pid, rid))
        for role in ('employee', 'manager'):
            self.call(role, 'GET', prefix, status='403 Forbidden')
        self.call('kk_responsible', 'GET', prefix + '?q=' + 'x' * 101, status='400 Bad Request')
        response = {'questions': [
            {'question': 'First', 'options': ['Yes', 'No'], 'correct_answer': 0},
            {'question': 'Broken', 'options': ['Yes', 'No'], 'correct_answer': 8}, 42]}
        path = '/api/testing/imports/from-generated'
        for inaccessible, revision in ((private, private_rid), (restricted, restricted_rid), (pid, private_rid)):
            self.call('kk_responsible', 'POST', path, status='409 Conflict', source_page_id=inaccessible,
                      source_revision_id=revision, settings={}, response=response)
        for role in ('employee', 'manager'):
            self.call(role, 'POST', path, status='403 Forbidden', source_page_id=pid,
                      source_revision_id=rid, settings={}, response=response)
        created, _ = self.call('kk_responsible', 'POST', path, source_page_id=pid,
                               source_revision_id=rid, settings={'questions_count': 2}, response=response)
        self.assertEqual(created['total'], 3)
        self.assertIn('warning', created)
        base = '/api/testing/imports/' + created['id']
        self.assertEqual(self.row('SELECT count(*) FROM questions WHERE import_item_id IN '
                                  '(SELECT id FROM question_import_items WHERE import_id=%s)',
                                  (created['id'],))[0], 0)
        details, _ = self.call('kk_responsible', 'GET', base)
        self.assertEqual(details['payload']['source']['provenance'], 'selection_only')
        self.assertEqual(details['payload']['kind'], 'generated_response_v1')
        self.assertEqual(details['payload']['settings'], {'questions_count': 2})
        self.assertEqual(details['payload']['response'], response)
        self.assertEqual((details['total'], details['pending'], details['invalid'], details['approved']),
                         (3, 0, 3, 0))
        self.assertEqual([x['status'] for x in details['items']], ['invalid'] * 3)
        self.assertEqual(details['items'][2]['payload'], 42)
        self.assertFalse(any(o['is_correct'] for o in details['items'][1]['edited_payload']['options']))
        code = self.row('SELECT code FROM competencies WHERE id=%s', (self.comps[0],))[0]
        first = details['items'][0]
        url = base + '/items/' + first['id']
        edited = dict(first['edited_payload'], competency_code=code)
        for tampered in (dict(edited, source_page_id=private),
                         dict(edited, source_revision_id=private_rid)):
            self.call('kk_responsible', 'PUT', url, status='409 Conflict',
                      edited_payload=tampered, expected_token=first['expected_token'])
        self.assertIsNone(self.row('SELECT reviewed_at FROM question_import_items WHERE id=%s', (first['id'],))[0])
        changed, _ = self.call('kk_responsible', 'PUT', url, edited_payload=edited,
                               expected_token=first['expected_token'])
        self.assertEqual(changed['status'], 'pending')
        self.call('kk_responsible', 'POST', url + '/review', status='400 Bad Request',
                  decision='approve', expected_token=changed['expected_token'])
        # No model-proven source access: review requires explicit human confirmation.
        approved, _ = self.call('kk_responsible', 'POST', url + '/review',
                                decision='approve', expected_token=changed['expected_token'],
                                source_confirmed=True)
        self.assertTrue(approved['question_id'])
        second = details['items'][1]
        second_url = base + '/items/' + second['id']
        fixed = dict(second['edited_payload'], competency_code=code,
                     options=[dict(option, is_correct=index == 0)
                              for index, option in enumerate(second['edited_payload']['options'])])
        second_edited, _ = self.call('kk_responsible', 'PUT', second_url,
                                     edited_payload=fixed, expected_token=second['expected_token'])
        self.row("UPDATE pages SET current_version=2 WHERE id=%s RETURNING id", (pid,))
        self.row('''INSERT INTO page_revisions(page_id,version,title,content,editor_id)
            VALUES(%s,2,'New revision','{}'::jsonb,%s) RETURNING id''', (pid, self.users['employee'][0]))
        self.call('kk_responsible', 'POST', second_url + '/review', status='409 Conflict',
                  decision='approve', expected_token=second_edited['expected_token'], source_confirmed=True)
        self.call('kk_responsible', 'PUT', second_url, status='409 Conflict',
                  edited_payload=fixed, expected_token=second_edited['expected_token'])
        self.assertEqual(self.row('SELECT count(*) FROM questions WHERE import_item_id=%s', (second['id'],))[0], 0)
        self.assertEqual(self.row('SELECT edited_payload FROM question_import_items WHERE id=%s',
                                  (second['id'],))[0], fixed)

    def test_manual_generated_response_repair_approve_and_reject(self):
        self.seed()
        pid, rid, _ = self.published_source()
        code = self.row('SELECT code FROM competencies WHERE id=%s', (self.comps[0],))[0]
        response = {'questions': [
            {'question': 'Valid first', 'options': ['Right', 'Wrong'], 'correct_answer': 0},
            {'question': 'Fix index', 'options': ['Wrong', 'Right'], 'correct_answer': 9},
            {'question': 'Reject me', 'options': ['Right', 'Wrong'], 'correct_answer': 0}]}
        settings = {'questions_count': 3, 'answers_count': 2, 'topics': ['Review manually']}
        result, _ = self.call('kk_responsible', 'POST', '/api/testing/imports/from-generated',
                              source_page_id=pid, source_revision_id=rid, settings=settings, response=response)
        base = '/api/testing/imports/' + result['id']
        detail, _ = self.call('kk_responsible', 'GET', base)
        self.assertEqual((detail['total'], detail['pending'], detail['invalid'], detail['approved'], detail['rejected']),
                         (3, 0, 3, 0, 0))
        self.assertEqual(detail['payload'], {'kind': 'generated_response_v1', 'source': {
            'page_id': pid, 'revision_id': rid, 'provenance': 'selection_only'},
            'settings': settings, 'response': response})
        before_bank, _ = self.call('kk_responsible', 'GET', '/api/testing/questions')
        self.assertEqual(before_bank, [])
        approved_ids = []
        for index in (0, 1):
            item = detail['items'][index]
            self.assertEqual(item['payload'], response['questions'][index])
            self.assertEqual(item['edited_payload']['competency_code'], '')
            corrected = dict(item['edited_payload'], competency_code=code)
            if index == 1:
                self.assertFalse(any(option['is_correct'] for option in corrected['options']))
                corrected['options'] = [dict(option, is_correct=pos == 1)
                                        for pos, option in enumerate(corrected['options'])]
            path = base + '/items/' + item['id']
            self.call('kk_responsible', 'POST', path + '/review', status='409 Conflict',
                      decision='approve', expected_token=item['expected_token'], source_confirmed=True)
            saved, _ = self.call('kk_responsible', 'PUT', path,
                                 edited_payload=corrected, expected_token=item['expected_token'])
            self.assertEqual((saved['status'], saved['validation_errors']), ('pending', []))
            self.call('kk_responsible', 'POST', path + '/review', status='400 Bad Request',
                      decision='approve', expected_token=saved['expected_token'])
            approved, _ = self.call('kk_responsible', 'POST', path + '/review',
                                    decision='approve', expected_token=saved['expected_token'], source_confirmed=True)
            approved_ids.append(approved['question_id'])
            self.assertEqual(approved['status'], 'approved')
            private, _ = self.call('kk_responsible', 'GET', '/api/testing/questions/' + approved['question_id'])
            self.assertEqual((private['import_id'], private['import_item_id'], private['source_page_id'],
                              private['source_revision_id']), (result['id'], item['id'], pid, rid))
            self.assertEqual([o['is_correct'] for o in private['options']],
                             [index == 0, index == 1])
            self.assertEqual(self.row('SELECT count(*) FROM question_revisions WHERE question_id=%s AND version=1',
                                      (approved['question_id'],))[0], 1)
            for role in ('manager', 'employee'):
                _, raw = self.call(role, 'GET', '/api/testing/questions/' + approved['question_id'],
                                   status='403 Forbidden')
                self.assertNotIn(b'Right', raw)
                self.assertNotIn(b'is_correct', raw)
                _, raw = self.call(role, 'GET', '/api/testing/questions', status='403 Forbidden')
                self.assertNotIn(b'is_correct', raw)
        third = detail['items'][2]
        rejected, _ = self.call('kk_responsible', 'POST', base + '/items/' + third['id'] + '/review',
                                decision='reject', reason='Not suitable', expected_token=third['expected_token'])
        self.assertEqual((rejected['status'], rejected['question_id']), ('rejected', None))
        summary, _ = self.call('kk_responsible', 'GET', base)
        self.assertEqual((summary['status'], summary['pending'], summary['invalid'],
                          summary['approved'], summary['rejected']), ('processed', 0, 0, 2, 1))
        self.assertEqual(self.row('SELECT count(*) FROM questions WHERE import_item_id IN '
                                  '(SELECT id FROM question_import_items WHERE import_id=%s)',
                                  (result['id'],))[0], 2)
        after_bank, _ = self.call('kk_responsible', 'GET', '/api/testing/questions')
        self.assertEqual({q['id'] for q in after_bank}, set(approved_ids))

    def test_200_raw_ordinals_duplicate_repair_review_and_department_acl(self):
        self.seed()
        code = self.row('SELECT code FROM competencies WHERE id=%s', (self.comps[0],))[0]
        items = [self.item(code, 'item-' + str(i)) for i in range(197)]
        items.extend([self.item(code, 'repeated'), self.item(code, 'repeated'), 42])
        result, _ = self.call('kk_responsible', 'POST', '/api/testing/imports',
                              filename='stage3.json', json_text=json.dumps({'questions': items}))
        self.assertEqual(result['total'], 200)
        iid = result['id']; base = '/api/testing/imports/' + iid
        rows = self.row('''SELECT count(*),count(*) FILTER (WHERE status='invalid'),
            count(DISTINCT ordinal),min(ordinal),max(ordinal) FROM question_import_items WHERE import_id=%s''', (iid,))
        self.assertEqual(rows, (200, 3, 200, 0, 199))
        self.assertEqual(self.row('SELECT payload FROM question_import_items WHERE import_id=%s AND ordinal=199', (iid,))[0], 42)
        self.assertEqual(self.row('SELECT payload FROM question_imports WHERE id=%s', (iid,))[0], {'questions': items})
        page, _ = self.call('kk_responsible', 'GET', base + '?limit=50&offset=150')
        self.assertEqual([r['ordinal'] for r in page['items']], list(range(150, 200)))
        self.assertEqual((page['pending'], page['invalid'], page['status']), (197, 3, 'validated'))
        self.assertEqual([r['status'] for r in page['items'][-3:]], ['invalid'] * 3)
        self.assertTrue(all(r['validation_errors'] for r in page['items'][-3:]))
        for role in ('manager', 'employee', 'admin'):
            for method, path, data in (('GET', '/api/testing/imports', {}), ('GET', base, {}),
                                       ('POST', base + '/items/' + page['items'][-3]['id'] + '/review',
                                        dict(decision='approve', expected_token=page['items'][-3]['expected_token']))):
                _, raw = self.call(role, method, path, status='403 Forbidden', **data)
                self.assertNotIn(b'Private correct option', raw)
                self.assertNotIn(b'is_correct', raw)
        foreign = self.row('INSERT INTO departments(code,name) VALUES(%s,%s) RETURNING id',
                           ('foreign_' + secrets.token_hex(6), 'Foreign'))[0]
        uid = self.row('''INSERT INTO users(department_id,email,username,display_name)
            VALUES(%s,%s,%s,'Foreign KK') RETURNING id''',
            (foreign, 'foreign_' + secrets.token_hex(8) + '@example.invalid', 'foreign_' + secrets.token_hex(8)))[0]
        self.row('INSERT INTO user_roles(user_id,role_id) SELECT %s,id FROM roles WHERE code=%s RETURNING user_id',
                 (uid, 'kk_responsible'))
        self.row('INSERT INTO department_kk_responsibles(department_id,user_id,appointed_by) VALUES(%s,%s,%s) RETURNING user_id',
                 (foreign, uid, self.users['admin'][0]))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        self.row('INSERT INTO sessions(token,user_id,csrf_token,expires_at) VALUES(%s,%s,%s,now()+interval \'1 hour\') RETURNING user_id',
                 (integration.hashlib.sha256(token.encode()).hexdigest(), uid, csrf))
        for path in ('/api/testing/imports', base):
            status, data, raw = self.api('GET', path, token)
            if path == base:
                self.assertEqual(status, '403 Forbidden')
            else:
                self.assertEqual((status, data), ('200 OK', []))
            self.assertNotIn(b'Private correct option', raw)

        # Fix both duplicates via tokened edits; raw payload never changes.
        for index, external in ((-3, 'fixed-a'), (-2, 'fixed-b')):
            original = page['items'][index]
            path = base + '/items/' + original['id']
            self.call('kk_responsible', 'POST', path + '/review', status='409 Conflict',
                      decision='approve', expected_token=original['expected_token'])
            edited, _ = self.call('kk_responsible', 'PUT', path,
                                  expected_token=original['expected_token'],
                                  edited_payload=self.item(code, external))
            self.assertEqual((edited['status'], edited['validation_errors']), ('pending', []))
            self.assertEqual(self.row('SELECT payload->>\'external_id\' FROM question_import_items WHERE id=%s',
                                      (original['id'],))[0], 'repeated')
            page['items'][index] = edited
        primitive = page['items'][-1]
        path = base + '/items/' + primitive['id']
        self.call('kk_responsible', 'POST', path + '/review', status='409 Conflict',
                  decision='approve', expected_token=primitive['expected_token'])
        repaired, _ = self.call('kk_responsible', 'PUT', path, expected_token=primitive['expected_token'],
                                edited_payload=self.item(code, 'primitive-repaired'))
        self.assertEqual(self.row('SELECT payload FROM question_import_items WHERE id=%s', (primitive['id'],))[0], 42)
        approved, _ = self.call('kk_responsible', 'POST', path + '/review', decision='approve',
                                expected_token=repaired['expected_token'])
        qid = approved['question_id']
        detail, _ = self.call('kk_responsible', 'GET', '/api/testing/questions/' + qid)
        self.assertEqual((detail['import_id'], detail['import_item_id'], detail['bank_department_id']),
                         (iid, primitive['id'], str(self.dept)))
        self.assertEqual(sum(bool(o['is_correct']) for o in detail['options']), 1)
        metadata = self.row('''SELECT bank_department_id,import_item_id,moderation_status,created_by,reviewed_by
            FROM questions WHERE id=%s''', (qid,))
        self.assertEqual(tuple(map(str, metadata)),
                         (str(self.dept), primitive['id'], 'approved',
                          str(self.users['kk_responsible'][0]), str(self.users['kk_responsible'][0])))
        keys = self.row('SELECT answer_key_snapshot FROM question_revisions WHERE question_id=%s AND version=1', (qid,))[0]
        correct = self.row('SELECT id FROM answer_options WHERE question_id=%s AND is_correct', (qid,))[0]
        self.assertEqual(keys, [str(correct)])
        self.assertEqual(self.row('SELECT count(*) FROM answer_options WHERE question_id=%s', (qid,))[0], 2)
        self.assertEqual(self.call('kk_responsible', 'GET', base)[0]['status'], 'validated')
        self.call('kk_responsible', 'POST', path + '/review', status='409 Conflict',
                  decision='approve', expected_token=repaired['expected_token'])
        # Review all remaining entries: only then may the import become processed.
        for offset in (0, 50, 100, 150):
            listing, _ = self.call('kk_responsible', 'GET', base + f'?limit=50&offset={offset}')
            for entry in listing['items']:
                if entry['status'] in ('pending', 'invalid'):
                    self.call('kk_responsible', 'POST', base + '/items/' + entry['id'] + '/review',
                              decision='reject', reason='Reviewed', expected_token=entry['expected_token'])
        summary, _ = self.call('kk_responsible', 'GET', base)
        self.assertEqual((summary['status'], summary['approved'], summary['rejected'], summary['invalid']),
                         ('processed', 1, 199, 0))

    def test_one_primitive_and_malformed_numeric_and_size_guards_real_wsgi(self):
        self.seed()
        code = self.row('SELECT code FROM competencies WHERE id=%s', (self.comps[0],))[0]
        _, auth_token, csrf = self.users['kk_responsible']
        valid = self.item(code, 'single')
        for invalid in ('[1e309]', '[{"external_id":1e309}]'):
            self.call('kk_responsible', 'POST', '/api/testing/imports', status='400 Bad Request',
                      filename='bad.json', json_text=invalid)
        result, _ = self.call('kk_responsible', 'POST', '/api/testing/imports',
                              filename='primitive.json', json_text='[false]')
        base = '/api/testing/imports/' + result['id']
        primitive = self.call('kk_responsible', 'GET', base)[0]['items'][0]
        self.assertIs(primitive['payload'], False)
        self.assertEqual(primitive['status'], 'invalid')
        path = base + '/items/' + primitive['id']
        class Unreadable:
            def read(self, *args):
                raise AssertionError('Oversized body read')
        for method, url, size in (('POST', '/api/testing/imports', 1024 * 1024 + 1),
                                   ('POST', '/api/testing/imports/from-generated', 512 * 1024 + 1),
                                   ('PUT', path, 512 * 1024 + 1),
                                   ('POST', path + '/review', 512 * 1024 + 1)):
            env = dict(REQUEST_METHOD=method, PATH_INFO=url, CONTENT_LENGTH=str(size),
                       CONTENT_TYPE='application/json', **{'wsgi.input': Unreadable(),
                       'HTTP_COOKIE': 'sid=' + auth_token, 'REMOTE_ADDR': '127.0.0.1',
                       'wsgi.url_scheme': 'https'})
            out = []
            response = b''.join(self.application(env, lambda status, headers: out.append(status)))
            self.assertEqual(out[0], '413 Payload Too Large')
            self.assertIn(b'error', response)
        self.assertEqual(self.row('SELECT status FROM question_import_items WHERE id=%s', (primitive['id'],))[0], 'invalid')
        self.assertEqual(self.row('SELECT count(*) FROM questions WHERE import_item_id=%s', (primitive['id'],))[0], 0)
        raw = ('{"csrf":' + json.dumps(csrf) + ',"expected_token":' +
               json.dumps(primitive['expected_token']) + ',"edited_payload":{"x":1e309}}').encode()
        env = dict(REQUEST_METHOD='PUT', PATH_INFO=path, CONTENT_LENGTH=str(len(raw)),
                   CONTENT_TYPE='application/json', **{'wsgi.input': io.BytesIO(raw),
                   'HTTP_COOKIE': 'sid=' + auth_token, 'REMOTE_ADDR': '127.0.0.1',
                   'wsgi.url_scheme': 'https'})
        out = []
        self.assertIn(b'error', b''.join(self.application(env, lambda status, headers: out.append(status))))
        self.assertEqual(out[0], '400 Bad Request')
        self.assertEqual(self.row('SELECT edited_payload FROM question_import_items WHERE id=%s', (primitive['id'],))[0], None)
        self.call('kk_responsible', 'PUT', path, status='413 Payload Too Large',
                  expected_token=primitive['expected_token'],
                  edited_payload={'external_id': 'big', 'question': 'Я' * 17000})
        self.assertIsNone(self.row('SELECT edited_payload FROM question_import_items WHERE id=%s',
                                   (primitive['id'],))[0])
        self.assertEqual(self.row('SELECT count(*) FROM question_imports')[0], 1)
        self.assertEqual(valid['options'][0]['is_correct'], True)

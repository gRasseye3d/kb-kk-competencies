"""Opt-in, local-only PostgreSQL 15+ API integration test.

Run against a NEW, disposable, EMPTY database (never the local application DB):
  createdb -h localhost kb_it_testing
  DATABASE_URL=postgresql://localhost/kb_it_testing python -m unittest discover -s tests -p test_pg_testing_integration.py -v

Alternatively set DATABASE_URL to a local PostgreSQL URI with an explicit
 kb_it_ database name. The suite refuses any pre-existing objects (including
 a previous run's schema): recreate the disposable database yourself for every
 run. Schema bootstrap is one transaction; fixture data is rolled back. The
 suite never drops a database or table.
"""
import hashlib
import io
import json
import os
import re
import secrets
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

try:
    import psycopg2
    from psycopg2.extensions import parse_dsn
except ImportError:
    psycopg2 = None


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / 'sql' / 'schema_conflunce_v4.sql'
MIGRATIONS = [ROOT / 'sql' / f'{n}_{name}.sql' for n, name in (
    ('03', 'sessions_v4'), ('04', 'testing'), ('05', 'testing_review'),
    ('06', 'login_rate_limits'), ('09', 'question_imports'))]


def schema_sql(file):
    """Remove the one outer transaction pair from each known SQL script.

    Running the original BEGIN/COMMIT per file would persist a partial schema
    if a later migration fails. Reject unexpected script layout, never guess.
    """
    source = file.read_text(encoding='utf-8')
    begin = list(re.finditer(r'^[ \t]*BEGIN;[ \t]*$', source, re.MULTILINE))
    end = list(re.finditer(r'^[ \t]*COMMIT;[ \t]*$', source, re.MULTILINE))
    if len(begin) != 1 or len(end) != 1 or begin[0].end() >= end[0].start():
        raise ValueError(f'Unexpected transaction boundaries in {file}')
    return source[:begin[0].start()] + source[begin[0].end():end[0].start()] + source[end[0].end():]


def guarded_dsn():
    """Reject production, default application DBs and remote endpoints BEFORE connecting."""
    dsn = os.environ.get('DATABASE_URL')
    if not dsn:
        raise ValueError('Explicit DATABASE_URL is required')
    if psycopg2 is None:
        raise RuntimeError('Install kb/requirements.txt first')
    params = parse_dsn(dsn)
    if not re.fullmatch(r'kb_it_[A-Za-z0-9_]+', params.get('dbname', '')):
        raise ValueError('DATABASE_URL must explicitly name a kb_it_* database')
    if (params.get('service') or params.get('hostaddr') or os.environ.get('PGHOSTADDR')
            or params.get('host') not in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('DATABASE_URL must explicitly target localhost without hostaddr/service')
    return dsn


class _SharedConnection:
    """App transactions share the fixture; errors revert only the current request."""
    def __init__(self, conn):
        self.conn = conn

    def cursor(self, *args, **kwargs):
        return self.conn.cursor(*args, **kwargs)

    def commit(self):
        pass

    def rollback(self):
        with self.conn.cursor() as cur:
            cur.execute('ROLLBACK TO SAVEPOINT kb_it_request')

    def close(self):
        pass


class IntegrationSafetyTests(unittest.TestCase):
    def test_scripts_have_one_outer_transaction(self):
        for file in [SCHEMA, *MIGRATIONS]:
            with self.subTest(file=file):
                sql = schema_sql(file)
                self.assertNotRegex(sql, r'(?m)^[ \t]*(?:BEGIN|COMMIT);[ \t]*$')

    def test_dsn_requires_explicit_disposable_local_database(self):
        # Exercise the guard without connecting to PostgreSQL (or requiring a driver).
        with patch.dict(os.environ, {}, clear=True), patch(__name__ + '.psycopg2', object()), \
                patch(__name__ + '.parse_dsn', create=True, side_effect=lambda dsn: dict(
                    part.split('=', 1) for part in dsn.split(';'))):
            for dsn in ('dbname=kb_it_sample;host=localhost',
                        'dbname=kb_it_sample;host=127.0.0.1'):
                with self.subTest(dsn=dsn), patch.dict(os.environ, {'DATABASE_URL': dsn}):
                    self.assertEqual(guarded_dsn(), dsn)
            for dsn in ('host=localhost', 'dbname=kb;host=localhost',
                        'dbname=kb_it_;host=localhost', 'dbname=kb_it_sample;host=remote',
                        'dbname=kb_it_sample;host=localhost;hostaddr=127.0.0.1',
                        'dbname=kb_it_sample;host=localhost;service=other'):
                with self.subTest(dsn=dsn), patch.dict(os.environ, {'DATABASE_URL': dsn}):
                    with self.assertRaises(ValueError):
                        guarded_dsn()

    def test_request_rollback_does_not_rollback_fixture(self):
        from unittest.mock import MagicMock
        connection = MagicMock()
        _SharedConnection(connection).rollback()
        connection.rollback.assert_not_called()
        connection.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
            'ROLLBACK TO SAVEPOINT kb_it_request')


@unittest.skipUnless(os.environ.get('DATABASE_URL'), 'Set DATABASE_URL to a disposable kb_it_ database')
class PublishedTestApiIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dsn = guarded_dsn()
        from app import wsgi  # no DB access on import
        cls.application = staticmethod(wsgi.application)
        cls.dsn = dsn
        conn = psycopg2.connect(dsn)
        try:
            with conn.cursor() as cur:
                cur.execute('SHOW server_version_num')
                if int(cur.fetchone()[0]) < 150000:
                    raise RuntimeError('PostgreSQL 15+ required')
                cur.execute('''SELECT EXISTS (
                     SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                     WHERE n.nspname NOT IN ('pg_catalog','information_schema')
                       AND n.nspname NOT LIKE 'pg_toast%%'
                 ) OR EXISTS (
                     SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                     WHERE n.nspname='public'
                 ) OR EXISTS (
                     SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                     WHERE n.nspname='public'
                 ) OR EXISTS (
                     SELECT 1 FROM pg_collation c JOIN pg_namespace n ON n.oid=c.collnamespace
                     WHERE n.nspname='public'
                ) OR EXISTS (
                    SELECT 1 FROM pg_namespace WHERE nspname NOT IN
                      ('public','pg_catalog','information_schema')
                      AND nspname NOT LIKE 'pg_toast%%' AND nspname NOT LIKE 'pg_temp_%%'
                ) OR EXISTS (SELECT 1 FROM pg_extension WHERE extname <> 'plpgsql')''')
                if cur.fetchone()[0]:
                    raise RuntimeError('Database is not empty (including previously initialized schema); '
                                       'create a NEW disposable kb_it_ database')
                for file in [SCHEMA, *MIGRATIONS]:
                    cur.execute(schema_sql(file))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def setUp(self):
        self.conn = psycopg2.connect(self.dsn)
        from app import db
        self.db_patch = patch.object(db, 'get_conn', return_value=_SharedConnection(self.conn))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self._rollback_and_close)

    def _rollback_and_close(self):
        self.conn.rollback()
        self.conn.close()

    def row(self, sql, params=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def api(self, method, path, token, csrf=None, **payload):
        if csrf is not None:
            payload['csrf'] = csrf
        body = json.dumps(payload).encode() if method in ('POST', 'PUT', 'DELETE') else b''
        url = urlsplit(path)
        env = {'REQUEST_METHOD': method, 'PATH_INFO': url.path, 'QUERY_STRING': url.query, 'wsgi.input': io.BytesIO(body),
               'CONTENT_LENGTH': str(len(body)), 'CONTENT_TYPE': 'application/json',
               'HTTP_COOKIE': 'sid=' + token, 'REMOTE_ADDR': '127.0.0.1',
               'wsgi.url_scheme': 'https'}
        output = []
        # The application calls db.transaction().rollback() on a 409. Its
        # connection proxy rolls back to THIS request's savepoint, not the
        # outer fixture transaction; subsequent requests still see the setup.
        with self.conn.cursor() as cur:
            cur.execute('SAVEPOINT kb_it_request')
        try:
            result = b''.join(self.application(env, lambda status, headers: output.append((status, headers))))
        except BaseException:
            with self.conn.cursor() as cur:
                cur.execute('ROLLBACK TO SAVEPOINT kb_it_request')
            raise
        finally:
            with self.conn.cursor() as cur:
                cur.execute('RELEASE SAVEPOINT kb_it_request')
        self.assertEqual(dict(output[0][1]).get('Cache-Control'), 'no-store')
        return output[0][0], json.loads(result), result

    def seed(self, competency_count=2):
        """Fake users, department, roles and active two-competency profile."""
        suffix = secrets.token_hex(6)
        self.dept = self.row('INSERT INTO departments(code,name) VALUES(%s,%s) RETURNING id',
                             ('it_' + suffix, 'Integration'))[0]
        self.users = {}
        for role in ('admin', 'kk_responsible', 'manager', 'employee'):
            uid = self.row('''INSERT INTO users(department_id,email,username,display_name)
                VALUES(%s,%s,%s,%s) RETURNING id''',
                (self.dept, role + suffix + '@example.invalid', role + suffix, role))[0]
            rid = self.row('INSERT INTO roles(code,name) VALUES(%s,%s) RETURNING id',
                           (role, role))[0]
            self.row('INSERT INTO user_roles(user_id,role_id) VALUES(%s,%s) RETURNING user_id', (uid,rid))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            self.row('''INSERT INTO sessions(token,user_id,csrf_token,expires_at)
                VALUES(%s,%s,%s,now()+interval '1 hour') RETURNING user_id''',
                (hashlib.sha256(token.encode()).hexdigest(), uid, csrf))
            self.users[role] = (uid, token, csrf)
        self.row('UPDATE departments SET manager_id=%s WHERE id=%s RETURNING id',
                 (self.users['manager'][0], self.dept))
        self.row('''INSERT INTO department_kk_responsibles(department_id,user_id,appointed_by)
            VALUES(%s,%s,%s) RETURNING user_id''',
            (self.dept, self.users['kk_responsible'][0], self.users['admin'][0]))
        cat = self.row('INSERT INTO competency_categories(code,name) VALUES(%s,%s) RETURNING id',
                       ('it_' + suffix, 'Integration'))[0]
        self.comps = [self.row('INSERT INTO competencies(category_id,code,name) VALUES(%s,%s,%s) RETURNING id',
                               (cat, 'it_' + suffix + str(i), 'Competency ' + str(i)))[0]
                      for i in range(competency_count)]
        pid = self.row('''INSERT INTO competency_profiles(department_id,name,status,is_active,
            prepared_by,approved_by,approved_at) VALUES(%s,%s,'approved',TRUE,%s,%s,now()) RETURNING id''',
            (self.dept,'Integration profile',self.users['kk_responsible'][0],self.users['manager'][0]))[0]
        for cid in self.comps:
            self.row('INSERT INTO profile_competencies(profile_id,competency_id,target_level) VALUES(%s,%s,2) RETURNING competency_id',
                     (pid,cid))

    def call(self, role, method, path, status='200 OK', **payload):
        _, token, csrf = self.users[role]
        got, data, raw = self.api(method, path, token, csrf if method in ('POST', 'PUT', 'DELETE') else None, **payload)
        self.assertEqual(got, status, (path, data))
        return data, raw

    def _testing_bank(self):
        questions, answers = {}, {}
        for comp in self.comps:
            cid = str(comp)
            questions[cid] = []
            for n in range(2):
                q, _ = self.call('kk_responsible', 'POST', '/api/testing/questions',
                    competency_id=cid, body=f'Original {cid} {n}', type='single',
                    options=[{'body': 'Original yes', 'correct': True}, {'body': 'Original no', 'correct': False}])
                qid = q['id']
                self.call('kk_responsible', 'POST', f'/api/testing/questions/{qid}/review', decision='approve')
                questions[cid].append(qid)
                answers[qid] = [str(self.row('SELECT id FROM answer_options WHERE question_id=%s AND is_correct', (qid,))[0])]
        return questions, answers

    def _testing_token(self, tid):
        rows, _ = self.call('kk_responsible', 'GET', '/api/testing/tests')
        return next(row['expected_token'] for row in rows if row['id'] == tid)

    @staticmethod
    def draft_payload(draft):
        return {key: draft[key] for key in ('title', 'description', 'blocks', 'expected_token')}

    def test_stage3_import_validation_acl_review_and_immutable_bank(self):
        self.seed()
        code = self.row('SELECT code FROM competencies WHERE id=%s', (self.comps[0],))[0]
        good = dict(external_id='one', competency_code=code, question='Imported question', type='single',
                    options=[dict(id='yes', text='Correct', is_correct=True),
                             dict(id='no', text='Incorrect', is_correct=False)])
        other = dict(good, external_id='two', question='Second question')
        for role in ('employee', 'manager', 'admin'):
            self.call(role, 'POST', '/api/testing/imports', status='403 Forbidden',
                      filename='qs.json', json_text=json.dumps([good]))
        for bad in ('{"questions":[{"a":1,"a":2}]}', '[]', '{}', '[', 'null'):
            self.call('kk_responsible', 'POST', '/api/testing/imports', status='400 Bad Request',
                      filename='qs.json', json_text=bad)
        self.assertEqual(self.row('SELECT count(*) FROM question_imports')[0], 0)
        upload, _ = self.call('kk_responsible', 'POST', '/api/testing/imports',
                              filename='qs.json', json_text=json.dumps({'questions': [good, other, 7]}))
        iid = upload['id']; base = f'/api/testing/imports/{iid}'
        self.assertEqual(upload['total'], 3)
        self.assertEqual(self.row('SELECT payload FROM question_imports WHERE id=%s', (iid,))[0],
                         {'questions': [good, other, 7]})
        detail, raw = self.call('kk_responsible', 'GET', base)
        self.assertEqual((detail['pending'], detail['invalid'], detail['status']), (2, 1, 'validated'))
        self.assertEqual([r['ordinal'] for r in detail['items']], [0, 1, 2])
        self.assertEqual(detail['items'][2]['payload'], 7)
        self.assertIn(b'is_correct', raw)
        for role in ('employee', 'manager', 'admin'):
            self.call(role, 'GET', '/api/testing/imports', status='403 Forbidden')
            self.call(role, 'GET', base, status='403 Forbidden')
        self.call('kk_responsible', 'GET', '/api/testing/imports?limit=101', status='400 Bad Request')
        self.assertEqual(len(self.call('kk_responsible', 'GET', base + '?limit=1&offset=2')[0]['items']), 1)
        self.assertEqual(len(self.call('kk_responsible', 'GET', '/api/testing/imports?limit=1&offset=1')[0]), 0)
        invalid = detail['items'][2]; url = base + '/items/' + invalid['id']
        self.call('kk_responsible', 'POST', url + '/review', status='428 Precondition Required', decision='approve')
        self.call('kk_responsible', 'POST', url + '/review', status='409 Conflict',
                  expected_token=invalid['expected_token'], decision='approve')
        self.call('kk_responsible', 'PUT', url, status='409 Conflict',
                  expected_token='stale', edited_payload=good)
        edited, _ = self.call('kk_responsible', 'PUT', url, expected_token=invalid['expected_token'],
                              edited_payload=dict(good, external_id='three'))
        self.assertEqual(edited['status'], 'pending')
        self.assertEqual(self.row('SELECT payload FROM question_import_items WHERE id=%s', (invalid['id'],))[0], 7)
        self.call('kk_responsible', 'PUT', url, status='409 Conflict',
                  expected_token=invalid['expected_token'], edited_payload=good)
        self.call('kk_responsible', 'POST', url + '/review', status='400 Bad Request',
                  expected_token=edited['expected_token'], decision='reject')
        rejected, _ = self.call('kk_responsible', 'POST', url + '/review',
                                expected_token=edited['expected_token'], decision='reject', reason='Not relevant')
        self.assertEqual(rejected['rejection_reason'], 'Not relevant')
        self.call('kk_responsible', 'POST', url + '/review', status='409 Conflict',
                  expected_token=rejected['expected_token'], decision='approve')
        first = detail['items'][0]
        approved, _ = self.call('kk_responsible', 'POST', base + '/items/' + first['id'] + '/review',
                                expected_token=first['expected_token'], decision='approve')
        qid = approved['question_id']
        self.assertEqual(str(self.row('SELECT bank_department_id FROM questions WHERE id=%s', (qid,))[0]), str(self.dept))
        self.assertEqual(self.row('SELECT moderation_status FROM questions WHERE id=%s', (qid,))[0], 'approved')
        revision = self.row('SELECT version,answer_key_snapshot FROM question_revisions WHERE question_id=%s', (qid,))
        self.assertEqual(revision[0], 1)
        self.assertEqual(revision[1], [str(self.row('SELECT id FROM answer_options WHERE question_id=%s AND is_correct', (qid,))[0])])
        bank, _ = self.call('kk_responsible', 'GET', '/api/testing/questions?status=approved')
        self.assertIn(qid, [q['id'] for q in bank])
        self.call('employee', 'GET', f'/api/testing/questions/{qid}', status='403 Forbidden')
        second = detail['items'][1]
        self.call('kk_responsible', 'POST', base + '/items/' + second['id'] + '/review',
                  expected_token=second['expected_token'], decision='reject', reason='Other')
        self.assertEqual(self.call('kk_responsible', 'GET', base)[0]['status'], 'processed')
        self.assertEqual(self.row('SELECT count(*) FROM questions WHERE import_item_id=%s', (first['id'],))[0], 1)
        # Department transfer revokes visibility to historical imports, including original answer keys.
        foreign = self.row('INSERT INTO departments(code,name) VALUES(%s,%s) RETURNING id',
                           ('foreign_' + secrets.token_hex(5), 'Other'))[0]
        self.row('UPDATE users SET department_id=%s WHERE id=%s RETURNING id',
                 (foreign, self.users['kk_responsible'][0]))
        self.call('kk_responsible', 'GET', base, status='403 Forbidden')

    def test_stage3_source_requires_visible_department_page_and_confirmation(self):
        self.seed()
        code = self.row('SELECT code FROM competencies WHERE id=%s', (self.comps[0],))[0]
        space, _ = self.call('admin', 'POST', '/api/admin/spaces',
                             name='Import source', space_key='IMP_' + secrets.token_hex(6))
        self.row("INSERT INTO space_members(space_id,user_id,role) VALUES(%s,%s,'editor') RETURNING user_id",
                 (space['id'], self.users['kk_responsible'][0]))
        page, _ = self.call('kk_responsible', 'POST', '/api/pages', space_id=space['id'],
                            title='Source', content='<p>Source</p>')
        payload = dict(external_id='source', competency_code=code, body='Grounded question',
                       type='single', source_page_id=page['id'],
                       options=[dict(id='a', body='A', correct=True), dict(id='b', body='B', correct=False)])
        created, _ = self.call('kk_responsible', 'POST', '/api/testing/imports',
                               filename='source.json', json_text=json.dumps([payload]))
        base = f"/api/testing/imports/{created['id']}"
        item = self.call('kk_responsible', 'GET', base)[0]['items'][0]
        self.assertEqual(item['status'], 'pending')
        path = f"{base}/items/{item['id']}/review"
        self.call('kk_responsible', 'POST', path, status='400 Bad Request',
                  expected_token=item['expected_token'], decision='approve')
        self.assertEqual(self.row('SELECT count(*) FROM questions WHERE import_item_id=%s', (item['id'],))[0], 0)
        approved, _ = self.call('kk_responsible', 'POST', path, expected_token=item['expected_token'],
                                decision='approve', source_confirmed=True)
        self.assertEqual(str(self.row('SELECT source_page_id FROM questions WHERE id=%s',
                                      (approved['question_id'],))[0]), page['id'])
        self.assertEqual(str(self.row('SELECT source_revision_id FROM questions WHERE id=%s',
                                      (approved['question_id'],))[0]),
                         str(self.row('SELECT id FROM page_revisions WHERE page_id=%s AND version=1', (page['id'],))[0]))
        self.assertEqual(self.call('kk_responsible', 'GET', base)[0]['status'], 'processed')

    def test_version_pins_append_only_blocks_frozen_keys_and_archive(self):
        """Real SQL lifecycle/rollback coverage, NOT a concurrent-transaction proof."""
        self.seed(4)
        questions, answers = self._testing_bank()
        cids = list(map(str, self.comps))
        unused, _ = self.call('kk_responsible', 'POST', '/api/testing/questions',
                             competency_id=cids[0], body='Approved but not selected', type='single',
                             options=[{'body': 'Yes', 'correct': True}, {'body': 'No', 'correct': False}])
        self.call('kk_responsible', 'POST', f"/api/testing/questions/{unused['id']}/review", decision='approve')
        comp = lambda cid: {'competency_id': cid, 'question_ids': list(reversed(questions[cid]))}
        blocks = [{'title': 'Original first', 'competencies': [comp(cid) for cid in cids[:2]]},
                  {'title': 'Original second', 'competencies': [comp(cid) for cid in cids[2:]]}]
        created, _ = self.call('kk_responsible', 'POST', '/api/testing/tests', title='Pinned test', blocks=blocks)
        tid = created['id']; base = f'/api/testing/tests/{tid}'
        draft, _ = self.call('kk_responsible', 'GET', base + '/draft')
        self.assertFalse(draft['title_locked'])
        self.call('kk_responsible', 'POST', base + '/publish', status='428 Precondition Required')
        self.call('kk_responsible', 'POST', base + '/publish', status='409 Conflict', expected_token='stale')
        self.assertEqual(self.row('SELECT count(*) FROM test_versions WHERE test_id=%s', (tid,))[0], 0)
        published, _ = self.call('kk_responsible', 'POST', base + '/publish', expected_token=draft['expected_token'])
        self.assertEqual(published, {'id': tid, 'version': 1})
        original_version = self.row('SELECT frozen_structure FROM test_versions WHERE test_id=%s AND version=1', (tid,))[0]
        self.assertEqual([[c['competency_id'] for c in b['competencies']] for b in original_version['blocks']],
                         [cids[:2], cids[2:]])
        for block in original_version['blocks']:
            for competency in block['competencies']:
                self.assertEqual([q['id'] for q in competency['questions']],
                                 list(reversed(questions[competency['competency_id']])))
        original_blocks = {b['block_id'] for b in original_version['blocks']}
        employee = str(self.users['employee'][0]); due = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        assign = lambda **extra: self.call('manager', 'POST', '/api/testing/assignments',
                                           test_id=tid, user_id=employee, due_at=due, **extra)[0]
        old_inprogress, old_pending = assign(), assign()
        started, _ = self.call('employee', 'POST', f"/api/testing/assignments/{old_inprogress['id']}/start")
        old_attempt = started['id']
        original_attempt = self.row('SELECT immutable_snapshot FROM test_attempts WHERE id=%s', (old_attempt,))[0]
        self.assertEqual(original_attempt, original_version)
        old_view, _ = self.call('employee', 'GET', f'/api/testing/attempts/{old_attempt}')

        draft, _ = self.call('kk_responsible', 'POST', base + '/draft', expected_token=self._testing_token(tid))
        self.assertTrue(draft['title_locked'])
        self.assertEqual(draft['blocks'], blocks)
        for role in ('manager', 'employee', 'admin'):
            self.call(role, 'GET', base + '/draft', status='403 Forbidden')
            self.call(role, 'PUT', base + '/draft', status='403 Forbidden', **self.draft_payload(draft))
        self.call('employee', 'GET', base + '/versions', status='403 Forbidden')
        self.call('employee', 'GET', base + '/versions/1', status='403 Forbidden')
        changed = dict(draft, title='Forbidden rename')
        self.call('kk_responsible', 'PUT', base + '/draft', status='409 Conflict', **self.draft_payload(changed))
        stale_token = draft['expected_token']
        # Reorder, move competencies to a different block, remove a block and one competency.
        draft['blocks'] = [{'title': 'New merged', 'competencies': [comp(cids[i]) for i in (3, 0, 2)]}]
        draft['description'] = 'Version two description'
        saved, _ = self.call('kk_responsible', 'PUT', base + '/draft', **self.draft_payload(draft))
        self.assertNotEqual(saved['expected_token'], stale_token)
        self.call('kk_responsible', 'POST', base + '/publish', status='409 Conflict', expected_token=stale_token)
        self.assertEqual(assign()['test_version'], 1)  # An unpublished draft does not withdraw v1.
        manager_tests, _ = self.call('manager', 'GET', '/api/testing/tests')
        self.assertEqual(len(manager_tests[0]['competencies']), 4)
        self.assertTrue(manager_tests[0]['assignable'])
        self.assertNotIn('expected_token', manager_tests[0])
        # Fixture-only bank changes retain original option IDs/FKs but invert keys.
        self.row("UPDATE questions SET body='Changed bank body' WHERE id=%s RETURNING id", (questions[cids[0]][0],))
        self.row('UPDATE answer_options SET is_correct=NOT is_correct,body=body || %s WHERE question_id=%s RETURNING id',
                 (' changed', questions[cids[0]][0]))
        self.row("UPDATE competencies SET name='Changed competency name' WHERE id=%s RETURNING id", (cids[0],))
        # The token includes current named composition: reload after fixture catalog edit.
        saved, _ = self.call('kk_responsible', 'GET', base + '/draft')
        published, _ = self.call('kk_responsible', 'POST', base + '/publish', expected_token=saved['expected_token'])
        self.assertEqual(published['version'], 2)
        version_two = self.row('SELECT frozen_structure FROM test_versions WHERE test_id=%s AND version=2', (tid,))[0]
        self.assertEqual([c['competency_id'] for c in version_two['blocks'][0]['competencies']],
                         [cids[i] for i in (3, 0, 2)])
        changed_question = next(q for b in version_two['blocks'] for c in b['competencies']
                                for q in c['questions'] if q['id'] == questions[cids[0]][0])
        self.assertEqual(changed_question['body'], 'Changed bank body')
        self.assertNotEqual([o['id'] for o in changed_question['options'] if o['correct']], answers[changed_question['id']])
        self.assertEqual(self.row('SELECT frozen_structure FROM test_versions WHERE test_id=%s AND version=1', (tid,))[0], original_version)
        self.assertEqual(self.row('SELECT immutable_snapshot FROM test_attempts WHERE id=%s', (old_attempt,))[0], original_attempt)
        self.assertEqual(self.call('employee', 'GET', f'/api/testing/attempts/{old_attempt}')[0], old_view)
        self.assertEqual(self.row('SELECT count(*) FROM test_blocks WHERE test_id=%s AND id=ANY(%s::uuid[])',
                                  (tid, list(original_blocks)))[0], 2)
        self.assertGreater(self.row('SELECT count(*) FROM test_blocks WHERE test_id=%s', (tid,))[0], 2)
        summaries, _ = self.call('manager', 'GET', base + '/versions')
        self.assertEqual([(v['version'], v['block_count'], v['competency_count'], v['question_count']) for v in summaries],
                         [(2, 1, 3, 6), (1, 2, 4, 8)])
        detail, raw = self.call('manager', 'GET', base + '/versions/1')
        self.assertEqual(detail['blocks'][0]['title'], 'Original first')
        for private in (b'correct', b'revision_id', b'frozen_structure', b'answer_key'):
            self.assertNotIn(private, raw)
        self.assertEqual(detail['blocks'][0]['competencies'][0]['competency_name'], 'Competency 0')

        # Completion uses v1's old key despite bank changes and v2 publication.
        graded, _ = self.call('employee', 'POST', f'/api/testing/attempts/{old_attempt}/submit', answers=answers)
        self.assertEqual({r['level'] for r in graded['results']}, {2})
        history, raw = self.call('employee', 'GET', '/api/testing/results')
        frozen_result = next(r for r in history if r['attempt_id'] == old_attempt and r['competency_id'] == cids[0])
        self.assertEqual((frozen_result['test_version'], frozen_result['competency_name']), (1, 'Competency 0'))
        self.assertNotIn(b'result_snapshot', raw)
        # Only block two is available: no empty block is exposed or stored in the attempt.
        self.row("UPDATE assessment_cooldowns SET last_completed_at=now()-interval '31 days',available_at=now()-interval '1 day' WHERE user_id=%s AND competency_id=ANY(%s::uuid[]) RETURNING user_id",
                 (employee, cids[2:]))
        started, _ = self.call('employee', 'POST', f"/api/testing/assignments/{old_pending['id']}/start")
        pending_attempt = started['id']
        view, _ = self.call('employee', 'GET', f'/api/testing/attempts/{pending_attempt}')
        self.assertEqual(view['test_version'], 1)
        self.assertEqual([b['title'] for b in view['blocks']], ['Original second'])
        self.assertEqual(len(view['questions']), 4)
        self.assertEqual({c['competency_id'] for c in view['competencies'] if c['state'] == 'excluded'}, set(cids[:2]))
        attempt_snapshot = self.row('SELECT immutable_snapshot FROM test_attempts WHERE id=%s', (pending_attempt,))[0]
        self.assertEqual([b['title'] for b in attempt_snapshot['blocks']], ['Original second'])
        self.assertTrue(self.row('SELECT bool_and(block_id=ANY(%s::uuid[])) FROM attempt_competencies WHERE attempt_id=%s',
                                 (list(original_blocks), pending_attempt))[0])
        self.call('employee', 'POST', f'/api/testing/attempts/{pending_attempt}/submit', answers={qid: answers[qid] for cid in cids[2:] for qid in questions[cid]})

        # v2 remains latest/assignable while the next draft exists. Explicit v1 stays pinned.
        self.call('kk_responsible', 'POST', base + '/draft', expected_token=self._testing_token(tid))
        self.assertEqual(assign(test_version=1)['test_version'], 1)
        latest_pending = assign()
        self.assertEqual(latest_pending['test_version'], 2)
        for invalid in (0, -1, True, '1', None):
            self.call('manager', 'POST', '/api/testing/assignments', status='400 Bad Request',
                      test_id=tid, user_id=employee, due_at=due, test_version=invalid)
        # Profile edits affect new assignments only.
        self.row('DELETE FROM profile_competencies WHERE competency_id=%s RETURNING competency_id', (cids[0],))
        self.call('manager', 'POST', '/api/testing/assignments', status='403 Forbidden', test_id=tid, user_id=employee, due_at=due)
        self.call('kk_responsible', 'POST', base + '/archive', expected_token=self._testing_token(tid))
        self.call('manager', 'POST', '/api/testing/assignments', status='403 Forbidden', test_id=tid, user_id=employee, due_at=due)
        self.call('kk_responsible', 'POST', base + '/draft', status='409 Conflict', expected_token=self._testing_token(tid))
        self.row("UPDATE assessment_cooldowns SET last_completed_at=now()-interval '31 days',available_at=now()-interval '1 day' WHERE user_id=%s RETURNING user_id", (employee,))
        started, _ = self.call('employee', 'POST', f"/api/testing/assignments/{latest_pending['id']}/start")
        view, _ = self.call('employee', 'GET', f"/api/testing/attempts/{started['id']}")
        self.assertEqual((view['test_version'], len(view['blocks']), len(view['questions'])), (2, 1, 6))
        self.assertEqual(view['blocks'][0]['title'], 'New merged')
        self.call('employee', 'POST', f"/api/testing/attempts/{started['id']}/submit")
        completed, _ = self.call('employee', 'GET', f"/api/testing/attempts/{started['id']}")
        self.assertEqual(completed['questions'], [])
        self.assertTrue(all(not c['questions'] for b in completed['blocks'] for c in b['competencies']))

    def test_legacy_publication_empty_authoring_questions_adapts_without_rewrites(self):
        self.seed()
        questions, answers = self._testing_bank()
        blocks = [{'title': 'Основной блок', 'competencies': [
            {'competency_id': str(cid), 'question_ids': questions[str(cid)]} for cid in self.comps]}]
        source, _ = self.call('kk_responsible', 'POST', '/api/testing/tests', title='Source', blocks=blocks)
        self.call('kk_responsible', 'POST', f"/api/testing/tests/{source['id']}/publish", expected_token=self._testing_token(source['id']))
        frozen = self.row('SELECT frozen_structure FROM test_versions WHERE test_id=%s', (source['id'],))[0]
        legacy, _ = self.call('kk_responsible', 'POST', '/api/testing/tests', title='Legacy',
                              competency_ids=list(map(str, self.comps)))
        tid = legacy['id']; base = f'/api/testing/tests/{tid}'
        bid = str(self.row('SELECT id FROM test_blocks WHERE test_id=%s', (tid,))[0])
        old = {'competencies': [dict(competency_id=c['competency_id'], block_id=bid, questions=c['questions'])
                                for c in frozen['blocks'][0]['competencies']]}
        # Build a genuine pre-stage2 fixture with no metadata and no test_questions.
        self.row('INSERT INTO test_versions(test_id,version,frozen_structure,published_by) VALUES(%s,1,%s::jsonb,%s) RETURNING id',
                 (tid, json.dumps(old), self.users['kk_responsible'][0]))
        self.row("UPDATE tests SET status='published' WHERE id=%s RETURNING id", (tid,))
        self.assertEqual(self.row('SELECT count(*) FROM test_questions WHERE test_id=%s', (tid,))[0], 0)
        due = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        assigned, _ = self.call('manager', 'POST', '/api/testing/assignments', test_id=tid,
                                user_id=str(self.users['employee'][0]), due_at=due)
        self.assertEqual(assigned['test_version'], 1)
        # A second legacy v1 assignment is already in progress before editing v2,
        # with one competency excluded. Keep the first assignment pending too.
        employee = str(self.users['employee'][0])
        included, excluded = map(str, self.comps)
        excluded_cooldown = self.row('''INSERT INTO assessment_cooldowns
            (user_id,competency_id,last_completed_at,available_at)
            VALUES(%s,%s,now()-interval '1 day',now()+interval '29 days')
            RETURNING last_completed_at,available_at''', (employee, excluded))
        active_assignment, _ = self.call('manager', 'POST', '/api/testing/assignments',
                                          test_id=tid, user_id=employee, due_at=due)
        active, _ = self.call('employee', 'POST', f"/api/testing/assignments/{active_assignment['id']}/start")
        active_id = active['id']
        active_view, _ = self.call('employee', 'GET', f'/api/testing/attempts/{active_id}')
        self.assertEqual(active_view['test_version'], 1)
        self.assertEqual({c['competency_id']: c['state'] for c in active_view['competencies']},
                         {included: 'included', excluded: 'excluded'})
        self.assertEqual({q['id'] for q in active_view['questions']}, set(questions[included]))
        self.assertEqual(self.row('SELECT immutable_snapshot FROM test_attempts WHERE id=%s', (active_id,))[0], old)
        draft, _ = self.call('kk_responsible', 'POST', base + '/draft', expected_token=self._testing_token(tid))
        self.assertEqual(draft['blocks'], blocks)
        self.assertEqual(self.row('SELECT count(*) FROM test_questions WHERE test_id=%s', (tid,))[0], 4)
        draft['blocks'][0]['title'] = 'Edited legacy v2'
        draft['blocks'][0]['competencies'].reverse()
        draft, _ = self.call('kk_responsible', 'PUT', base + '/draft', **self.draft_payload(draft))
        published, _ = self.call('kk_responsible', 'POST', base + '/publish', expected_token=draft['expected_token'])
        self.assertEqual(published['version'], 2)
        self.assertEqual(self.row('SELECT frozen_structure FROM test_versions WHERE test_id=%s AND version=1', (tid,))[0], old)
        self.call('kk_responsible', 'POST', base + '/archive', expected_token=self._testing_token(tid))
        self.assertEqual(self.row('SELECT immutable_snapshot FROM test_attempts WHERE id=%s', (active_id,))[0], old)
        self.assertEqual(self.call('employee', 'GET', f'/api/testing/attempts/{active_id}')[0], active_view)
        self.assertTrue(self.row('SELECT bool_and(block_id=%s) FROM attempt_competencies WHERE attempt_id=%s',
                                 (bid, active_id))[0])
        graded, _ = self.call('employee', 'POST', f'/api/testing/attempts/{active_id}/submit',
                              answers={qid: answers[qid] for qid in questions[included]})
        self.assertEqual(graded['results'], [{'competency_id': included, 'correct': 2, 'total': 2, 'level': 2}])
        self.assertEqual(self.row('SELECT count(*) FROM test_competency_results WHERE attempt_id=%s', (active_id,))[0], 1)
        self.assertEqual(self.row('SELECT count(*) FROM competency_assessment_events WHERE test_attempt_id=%s', (active_id,))[0], 1)
        self.assertEqual(self.row('SELECT state,correct_count,assessed_level FROM attempt_competencies WHERE attempt_id=%s AND competency_id=%s',
                                  (active_id, excluded)), ('excluded', None, None))
        self.assertEqual(self.row('SELECT last_completed_at,available_at FROM assessment_cooldowns WHERE user_id=%s AND competency_id=%s',
                                  (employee, excluded)), excluded_cooldown)
        self.assertEqual(self.row('SELECT immutable_snapshot FROM test_attempts WHERE id=%s', (active_id,))[0], old)
        # Fixture-only expiry lets the pending legacy assignment exercise both
        # competencies after v2 publication, preserving its original snapshot.
        self.row("UPDATE assessment_cooldowns SET last_completed_at=now()-interval '31 days',available_at=now()-interval '1 day' WHERE user_id=%s RETURNING user_id",
                 (employee,))
        started, _ = self.call('employee', 'POST', f"/api/testing/assignments/{assigned['id']}/start")
        self.assertEqual(self.row('SELECT immutable_snapshot FROM test_attempts WHERE id=%s', (started['id'],))[0], old)
        self.assertEqual(str(self.row('SELECT block_id FROM attempt_competencies WHERE attempt_id=%s LIMIT 1', (started['id'],))[0]), bid)
        self.row("UPDATE competencies SET name='Legacy current name' WHERE id=%s RETURNING id", (self.comps[0],))
        view, raw = self.call('employee', 'GET', f"/api/testing/attempts/{started['id']}")
        self.assertEqual(view['test_version'], 1)
        self.assertIn('Legacy current name', [c['competency_name'] for c in view['competencies']])
        self.assertNotIn(b'correct', raw)
        graded, _ = self.call('employee', 'POST', f"/api/testing/attempts/{started['id']}/submit", answers=answers)
        self.assertEqual({r['level'] for r in graded['results']}, {2})
        self.assertEqual(self.row('SELECT frozen_structure FROM test_versions WHERE test_id=%s AND version=1', (tid,))[0], old)

    def assert_unsupported_snapshot_has_no_writes(self, field, value):
        self.seed()
        questions, answers = self._testing_bank()
        blocks = [{'title': 'Guard', 'competencies': [
            {'competency_id': str(cid), 'question_ids': questions[str(cid)]} for cid in self.comps]}]
        created, _ = self.call('kk_responsible', 'POST', '/api/testing/tests', title='Guard', blocks=blocks)
        tid = created['id']
        self.call('kk_responsible', 'POST', f'/api/testing/tests/{tid}/publish', expected_token=self._testing_token(tid))
        due = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        assigned, _ = self.call('manager', 'POST', '/api/testing/assignments', test_id=tid,
                                user_id=str(self.users['employee'][0]), due_at=due)
        started, _ = self.call('employee', 'POST', f"/api/testing/assignments/{assigned['id']}/start")
        aid = started['id']
        snapshot = self.row('SELECT immutable_snapshot FROM test_attempts WHERE id=%s', (aid,))[0]
        snapshot[field] = value
        self.row('UPDATE test_attempts SET immutable_snapshot=%s::jsonb WHERE id=%s RETURNING id',
                 (json.dumps(snapshot), aid))
        tables = ('test_attempts', 'test_assignments', 'attempt_competencies', 'attempt_answers',
                  'test_competency_results', 'competency_assessment_events', 'assessment_cooldowns',
                  'employee_competencies', 'test_competency_reservations')
        def state():
            return [self.row(f'SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),\'[]\'::jsonb) FROM {table} t')[0]
                    for table in tables]
        before = state()
        self.call('employee', 'POST', f'/api/testing/attempts/{aid}/submit',
                  status='400 Bad Request', answers=answers)
        self.assertEqual(state(), before)

    def test_unknown_snapshot_format_rejected_without_writes(self):
        self.assert_unsupported_snapshot_has_no_writes('format_version', 999)

    def test_unknown_scoring_rule_rejected_without_writes(self):
        self.assert_unsupported_snapshot_has_no_writes('scoring_rule', 'future_unknown_rule')

    def test_draft_validation_bank_pagination_and_publication_revalidation(self):
        self.seed()
        questions, _ = self._testing_bank()
        cids = list(map(str, self.comps))
        blocks = [{'title': 'Selected', 'competencies': [
            {'competency_id': cid, 'question_ids': questions[cid]} for cid in cids]}]
        page1, _ = self.call('kk_responsible', 'GET', f'/api/testing/questions?competency_id={cids[0]}&status=approved&limit=1&offset=0')
        page2, _ = self.call('kk_responsible', 'GET', f'/api/testing/questions?competency_id={cids[0]}&status=approved&limit=1&offset=1')
        page3, _ = self.call('kk_responsible', 'GET', f'/api/testing/questions?competency_id={cids[0]}&status=approved&limit=1&offset=2')
        self.assertEqual({page1[0]['id'], page2[0]['id']}, set(questions[cids[0]]))
        self.assertEqual(page3, [])
        self.assertTrue(page1[0]['is_active'])
        self.call('kk_responsible', 'GET', '/api/testing/questions?limit=101', status='400 Bad Request')
        created, _ = self.call('kk_responsible', 'POST', '/api/testing/tests', title='Validation', blocks=blocks)
        tid = created['id']; base = f'/api/testing/tests/{tid}'
        original, _ = self.call('kk_responsible', 'GET', base + '/draft')
        before = self.row('SELECT count(*) FROM test_blocks WHERE test_id=%s', (tid,))[0]
        # UUID membership is checked against competency, department bank and moderation.
        wrong = json.loads(json.dumps(original))
        wrong['blocks'][0]['competencies'][0]['question_ids'] = questions[cids[1]]
        wrong['blocks'][0]['competencies'][1]['question_ids'] = []
        self.call('kk_responsible', 'PUT', base + '/draft', status='400 Bad Request', **self.draft_payload(wrong))
        duplicate = json.loads(json.dumps(original))
        duplicate['blocks'].append(duplicate['blocks'][0])
        self.call('kk_responsible', 'PUT', base + '/draft', status='400 Bad Request', **self.draft_payload(duplicate))
        pending, _ = self.call('kk_responsible', 'POST', '/api/testing/questions', competency_id=cids[0],
                               body='Pending', type='single', options=[{'body': 'Y', 'correct': True}, {'body': 'N', 'correct': False}])
        unapproved = json.loads(json.dumps(original))
        unapproved['blocks'][0]['competencies'][0]['question_ids'] = [pending['id']]
        self.call('kk_responsible', 'PUT', base + '/draft', status='400 Bad Request', **self.draft_payload(unapproved))
        foreign_dept = self.row('INSERT INTO departments(code,name) VALUES(%s,%s) RETURNING id',
                                 ('foreign_' + secrets.token_hex(6), 'Foreign'))[0]
        # Fixture-only bank transfer: previously saved choices must fail publication now.
        qid = questions[cids[0]][0]
        self.row('UPDATE questions SET bank_department_id=%s WHERE id=%s RETURNING id', (foreign_dept, qid))
        self.call('kk_responsible', 'POST', base + '/publish', status='400 Bad Request', expected_token=original['expected_token'])
        self.assertEqual(self.row('SELECT count(*) FROM test_versions WHERE test_id=%s', (tid,))[0], 0)
        self.assertEqual(self.row('SELECT count(*) FROM question_revisions')[0], 0)
        self.row('UPDATE questions SET bank_department_id=%s,is_active=FALSE WHERE id=%s RETURNING id', (self.dept, qid))
        self.call('kk_responsible', 'POST', base + '/publish', status='400 Bad Request', expected_token=original['expected_token'])
        self.assertEqual(self.row('SELECT count(*) FROM test_blocks WHERE test_id=%s', (tid,))[0], before)
        self.assertEqual(self.call('kk_responsible', 'GET', base + '/draft')[0], original)
        self.row('UPDATE questions SET is_active=TRUE WHERE id=%s RETURNING id', (qid,))
        self.row('DELETE FROM profile_competencies WHERE competency_id=%s RETURNING competency_id', (cids[0],))
        self.call('kk_responsible', 'POST', base + '/publish', status='403 Forbidden', expected_token=original['expected_token'])

    def test_publish_start_submit_scoring_cooldown_and_replay(self):
        self.seed()
        question_ids, correct_options, wrong_options = [], [], []
        for comp in self.comps:
            for _ in range(2):
                data, _ = self.call('kk_responsible','POST','/api/testing/questions',
                    competency_id=str(comp),body='Question',type='single',
                    options=[{'body':'Correct','correct':True},{'body':'Wrong','correct':False}])
                qid = data['id']
                question_ids.append(qid)
                self.call('kk_responsible','POST',f'/api/testing/questions/{qid}/review',decision='approve')
                opts = self.row('SELECT id FROM answer_options WHERE question_id=%s AND is_correct', (qid,))
                correct_options.append(str(opts[0]))
                wrong_options.append(str(self.row('SELECT id FROM answer_options WHERE question_id=%s AND NOT is_correct', (qid,))[0]))
        data, _ = self.call('kk_responsible','POST','/api/testing/tests',
                            title='Integration test',blocks=[{'title':'Основной блок','competencies':[
                                {'competency_id':str(comp),'question_ids':question_ids[i*2:i*2+2]}
                                for i,comp in enumerate(self.comps)]}])
        tid = data['id']
        draft, _ = self.call('kk_responsible','GET',f'/api/testing/tests/{tid}/draft')
        self.call('kk_responsible','POST',f'/api/testing/tests/{tid}/publish',expected_token=draft['expected_token'])
        self.assertEqual(self.row('SELECT count(*) FROM question_revisions')[0],4)
        self.assertEqual(self.row('SELECT status FROM tests WHERE id=%s',(tid,))[0],'published')
        employee = str(self.users['employee'][0])
        due = (datetime.now(timezone(timedelta(hours=3))) + timedelta(days=21)).isoformat()
        for invalid in (None, '', '2090-01-01T12:00:00', [], {}):
            self.call('manager','POST','/api/testing/assignments',status='400 Bad Request',
                      test_id=tid,user_id=employee,due_at=invalid,assignment_type='self_assigned')
        self.call('manager','POST','/api/testing/assignments',status='400 Bad Request',test_id=tid,user_id=employee)
        self.call('manager','POST','/api/testing/assignments',status='400 Bad Request',
                  test_id=tid,user_id=employee,due_at='2000-01-01T00:00:00Z')
        # A deadline after transaction start but before the live database clock
        # must fail: PostgreSQL now() alone would incorrectly accept it.
        elapsed = self.row("SELECT clock_timestamp() - interval '1 millisecond'")[0]
        self.assertTrue(self.row('SELECT %s > now()', (elapsed,))[0])
        self.call('manager','POST','/api/testing/assignments',status='400 Bad Request',
                  test_id=tid,user_id=employee,due_at=elapsed.isoformat())
        assigned, _ = self.call('manager','POST','/api/testing/assignments',test_id=tid,user_id=employee,due_at=due)
        aid = assigned['id']
        stored_due, future = self.row('SELECT due_at,due_at > clock_timestamp() FROM test_assignments WHERE id=%s',(aid,))
        self.assertEqual(stored_due, datetime.fromisoformat(due))
        self.assertTrue(future)
        started, _ = self.call('employee','POST',f'/api/testing/assignments/{aid}/start')
        attempt = started['id']
        view, raw = self.call('employee','GET',f'/api/testing/attempts/{attempt}')
        self.assertEqual(len(view['questions']),4)
        self.assertNotIn(b'correct',raw)
        self.assertNotIn(b'revision_id',raw)
        self.assertNotIn(b'answer_key',raw)
        self.assertEqual({c['state'] for c in view['competencies']},{'included'})
        self.call('employee','POST',f'/api/testing/assignments/{aid}/start',status='409 Conflict')
        busy, _ = self.call('manager','POST','/api/testing/assignments',test_id=tid,user_id=employee,due_at=due)
        unavailable, raw = self.call('employee','POST',f"/api/testing/assignments/{busy['id']}/start",status='409 Conflict')
        self.assertEqual(unavailable['code'],'assessment_unavailable')
        self.assertEqual(len(unavailable['details']['competencies']),2)
        for comp in unavailable['details']['competencies']:
            self.assertEqual(comp['reason'],'reserved_active')
            self.assertIsNone(comp['available_at'])
        self.assertNotIn(b'correct',raw)
        self.assertEqual(self.row('SELECT status FROM test_assignments WHERE id=%s',(busy['id'],))[0],'assigned')
        self.assertEqual(self.row('SELECT count(*) FROM test_attempts WHERE assignment_id=%s',(busy['id'],))[0],0)
        for invalid in ({question_ids[0]:[correct_options[0],correct_options[0]]},
                        {question_ids[0]:[correct_options[1]]}, {'bad-id':[]}, None):
            self.call('employee','POST',f'/api/testing/attempts/{attempt}/submit',
                      status='400 Bad Request',answers=invalid)
            self.assertEqual(self.row('SELECT count(*) FROM attempt_answers WHERE attempt_id=%s',(attempt,))[0],0)
        # First competency: correct + omitted => 1; second: empty + omitted => 0.
        answers = {question_ids[0]:[correct_options[0]],question_ids[2]:[]}
        graded, raw = self.call('employee','POST',f'/api/testing/attempts/{attempt}/submit',answers=answers)
        self.assertNotIn(b'answer_key',raw)
        self.assertNotIn(b'is_correct',raw)
        self.assertEqual({r['competency_id']:(r['correct'],r['total'],r['level']) for r in graded['results']},
                         {str(self.comps[0]):(1,2,1),str(self.comps[1]):(0,2,0)})
        self.assertEqual(self.row('SELECT status,score_percent FROM test_attempts WHERE id=%s',(attempt,)),('graded',25))
        self.assertEqual(self.row('SELECT status FROM test_assignments WHERE id=%s',(aid,))[0],'completed')
        self.assertEqual(self.row('SELECT count(*) FROM competency_assessment_events WHERE test_attempt_id=%s',(attempt,))[0],2)
        self.assertEqual(self.row('SELECT count(*) FROM test_competency_reservations WHERE attempt_id=%s AND released_at IS NOT NULL',(attempt,))[0],2)
        for comp,level in zip(self.comps,(1,0)):
            self.assertEqual(self.row('SELECT current_level FROM employee_competencies WHERE user_id=%s AND competency_id=%s',
                                      (employee,comp))[0],level)
            self.assertTrue(self.row('SELECT available_at > now() + interval \'29 days\' FROM assessment_cooldowns WHERE user_id=%s AND competency_id=%s',
                                     (employee,comp))[0])
        self.call('employee','POST',f'/api/testing/attempts/{attempt}/submit',status='409 Conflict',answers=answers)
        again, _ = self.call('manager','POST','/api/testing/assignments',test_id=tid,user_id=employee,due_at=due)
        unavailable, raw = self.call('employee','POST',f"/api/testing/assignments/{again['id']}/start",status='409 Conflict')
        self.assertEqual(unavailable['error'],'No competencies currently available')
        self.assertEqual(unavailable['code'],'assessment_unavailable')
        self.assertEqual(len(unavailable['details']['competencies']),2)
        for comp in unavailable['details']['competencies']:
            self.assertEqual(comp['reason'],'cooldown_30d')
            self.assertIn('T',comp['available_at'])
            self.assertIn(comp['competency_name'],('Competency 0','Competency 1'))
        self.assertNotIn(b'answer_key',raw)
        self.assertNotIn(b'correct',raw)
        self.assertEqual(self.row('SELECT status FROM test_assignments WHERE id=%s',(again['id'],))[0],'assigned')
        self.assertEqual(self.row('SELECT count(*) FROM test_attempts WHERE assignment_id=%s',(again['id'],))[0],0)
        # Legacy assigned records remain untouched and cannot start without a deadline.
        self.row('UPDATE test_assignments SET due_at=NULL WHERE id=%s RETURNING id',(again['id'],))
        missing, _ = self.call('employee','POST',f"/api/testing/assignments/{again['id']}/start",status='409 Conflict')
        self.assertEqual(missing['code'],'due_missing')
        self.assertIsNone(self.row('SELECT due_at FROM test_assignments WHERE id=%s',(again['id'],))[0])
        # Only one competency becomes available; the other remains excluded, not level zero.
        self.row("UPDATE assessment_cooldowns SET last_completed_at=now()-interval '31 days',available_at=now()-interval '1 day' WHERE user_id=%s AND competency_id=%s RETURNING user_id",
                 (employee,self.comps[0]))
        started, _ = self.call('employee','POST',f"/api/testing/assignments/{busy['id']}/start")
        second_attempt = started['id']
        # Existing in-progress legacy assignments can finish without a deadline.
        self.row('UPDATE test_assignments SET due_at=NULL WHERE id=%s RETURNING id',(busy['id'],))
        view, _ = self.call('employee','GET',f'/api/testing/attempts/{second_attempt}')
        self.assertEqual(len(view['questions']),2)
        self.assertEqual({c['state'] for c in view['competencies']},{'included','excluded'})
        graded, _ = self.call('employee','POST',f'/api/testing/attempts/{second_attempt}/submit')
        self.assertEqual(graded['results'],[{'competency_id':str(self.comps[0]),'correct':0,'total':2,'level':0}])
        self.assertEqual(self.row('SELECT current_level,last_level FROM employee_competencies WHERE user_id=%s AND competency_id=%s',
                                 (employee,self.comps[0])),(1,0))
        self.assertEqual(self.row('SELECT count(*) FROM test_competency_results WHERE attempt_id=%s',(second_attempt,))[0],1)
        self.assertEqual(self.row('SELECT count(*) FROM competency_assessment_events WHERE test_attempt_id=%s',(second_attempt,))[0],1)
        self.assertEqual(self.row('SELECT count(*) FROM attempt_answers WHERE attempt_id=%s AND NOT is_correct',(second_attempt,))[0],2)
        self.assertEqual(self.row('SELECT status,score_percent FROM test_attempts WHERE id=%s',(second_attempt,)),('graded',0))
        self.assertEqual(self.row('SELECT current_level,last_level FROM employee_competencies WHERE user_id=%s AND competency_id=%s',
                                 (employee,self.comps[1])),(0,0))

    def test_normal_json_history_roles_restore_and_stale_moderation(self):
        """Real WSGI JSON parser, sessions, ACL and SQL; only fixture commits are suppressed."""
        self.seed()
        space, _ = self.call('admin','POST','/api/admin/spaces',
                             name='JSON пространство',space_key='IT_' + secrets.token_hex(6))
        self.row("INSERT INTO space_members(space_id,user_id,role) VALUES(%s,%s,'editor') RETURNING user_id",
                 (space['id'],self.users['employee'][0]))
        page, _ = self.call('employee','POST','/api/pages',space_id=space['id'],
                            title='Первая версия',content='<p>Original</p>',labels=['stage1','json'])
        pid = page['id']
        original = self.row('SELECT id FROM page_revisions WHERE page_id=%s AND version=1',(pid,))[0]
        self.call('employee','PUT',f'/api/pages/{pid}',title='Second version',
                  content='<p>Updated</p>',labels=[])
        pending, _ = self.call('employee','POST',f'/api/moderation/submit/{pid}')
        repeated, _ = self.call('employee','POST',f'/api/moderation/submit/{pid}')
        self.assertEqual(pending,repeated)
        self.assertEqual(self.row('SELECT count(*) FROM page_approvals WHERE page_id=%s',(pid,))[0],1)

        # Ownership and even additive KK responsibility do not grant history access.
        for with_kk in (False, True):
            if with_kk:
                self.row('INSERT INTO user_roles(user_id,role_id) SELECT %s,id FROM roles WHERE code=%s RETURNING user_id',
                         (self.users['employee'][0],'kk_responsible'))
            self.call('employee','GET',f'/api/pages/{pid}/history',status='403 Forbidden')
            self.call('employee','POST',f'/api/pages/{pid}/restore/{original}',status='403 Forbidden')
        self.assertEqual(self.row('SELECT current_version,status FROM pages WHERE id=%s',(pid,)),(2,'pending_review'))
        self.assertEqual(self.row('SELECT count(*) FROM page_revisions WHERE page_id=%s',(pid,))[0],2)
        versions, _ = self.call('admin','GET',f'/api/pages/{pid}/history')
        self.assertEqual([v['version'] for v in versions],[2,1])
        _, token, _ = self.users['admin']
        status, _, _ = self.api('POST',f'/api/pages/{pid}/restore/{original}',token,csrf='wrong')
        self.assertEqual(status,'403 Forbidden')
        self.call('admin','POST',f'/api/pages/{pid}/restore/{original}')
        self.assertEqual(self.row('SELECT current_version,status,title,updated_by FROM pages WHERE id=%s',(pid,)),
                         (3,'draft','Первая версия',self.users['admin'][0]))
        self.assertEqual(self.row('SELECT title,content,editor_id FROM page_revisions WHERE page_id=%s AND version=3',(pid,)),
                         ('Первая версия',{'html':'<p>Original</p>'},self.users['admin'][0]))
        self.assertEqual(self.row('SELECT title,content FROM page_revisions WHERE page_id=%s AND version=2',(pid,)),
                         ('Second version',{'html':'<p>Updated</p>'}))
        versions, _ = self.call('admin','GET',f'/api/pages/{pid}/history')
        self.assertEqual([v['version'] for v in versions],[3,2,1])
        current, _ = self.call('employee','GET',f'/api/pages/{pid}')
        self.assertEqual((current['current_version'],current['content'],current['can_edit']),
                         (3,'<p>Original</p>',True))
        self.assertEqual(self.call('admin','GET','/api/moderation/queue')[0],[])
        stale_path = f"/api/moderation/decide/{pending['request_id']}"
        stale, _ = self.call('admin','POST',stale_path,status='409 Conflict',action='approve',comment='Old request')
        self.assertEqual(stale['code'],'approval_stale')
        new, _ = self.call('employee','POST',f'/api/moderation/submit/{pid}')
        self.assertNotEqual(new['request_id'],pending['request_id'])
        queue, _ = self.call('admin','GET','/api/moderation/queue')
        self.assertEqual([(r['id'],r['version']) for r in queue],[(new['request_id'],3)])
        self.call('admin','POST',stale_path,status='409 Conflict',action='approve')
        self.assertEqual(self.row('SELECT status FROM page_approvals WHERE id=%s',(pending['request_id'],))[0],'pending')
        self.call('admin','POST',f"/api/moderation/decide/{new['request_id']}",action='approve',comment='Обычный JSON')
        self.assertEqual(self.row('SELECT current_version,status FROM pages WHERE id=%s',(pid,)),(3,'published'))
        self.assertEqual(self.row('SELECT status FROM page_approvals WHERE id=%s',(new['request_id'],))[0],'approved')
        self.call('admin','POST',f"/api/moderation/decide/{new['request_id']}",status='409 Conflict',action='approve')
        self.assertEqual(self.call('admin','GET','/api/moderation/queue')[0],[])
        self.assertEqual(self.row('SELECT count(*) FROM page_revisions WHERE page_id=%s',(pid,))[0],3)


if __name__ == '__main__':
    unittest.main()

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
from pathlib import Path
from unittest.mock import patch

try:
    import psycopg2
    from psycopg2.extensions import parse_dsn
except ImportError:
    psycopg2 = None


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT.parent / 'docs' / 'schema_conflunce_v4.sql'
MIGRATIONS = [ROOT / 'sql' / f'{n}_{name}.sql' for n, name in (
    ('03', 'sessions_v4'), ('04', 'testing'), ('05', 'testing_review'),
    ('06', 'login_rate_limits'))]


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
        body = json.dumps(payload).encode() if method == 'POST' else b''
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'wsgi.input': io.BytesIO(body),
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

    def seed(self):
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
                      for i in range(2)]
        pid = self.row('''INSERT INTO competency_profiles(department_id,name,status,is_active,
            prepared_by,approved_by,approved_at) VALUES(%s,%s,'approved',TRUE,%s,%s,now()) RETURNING id''',
            (self.dept,'Integration profile',self.users['kk_responsible'][0],self.users['manager'][0]))[0]
        for cid in self.comps:
            self.row('INSERT INTO profile_competencies(profile_id,competency_id,target_level) VALUES(%s,%s,2) RETURNING competency_id',
                     (pid,cid))

    def call(self, role, method, path, status='200 OK', **payload):
        _, token, csrf = self.users[role]
        got, data, raw = self.api(method, path, token, csrf if method == 'POST' else None, **payload)
        self.assertEqual(got, status, (path, data))
        return data, raw

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
                            title='Integration test',competency_ids=list(map(str,self.comps)))
        tid = data['id']
        self.call('kk_responsible','POST',f'/api/testing/tests/{tid}/publish')
        self.assertEqual(self.row('SELECT count(*) FROM question_revisions')[0],4)
        self.assertEqual(self.row('SELECT status FROM tests WHERE id=%s',(tid,))[0],'published')
        employee = str(self.users['employee'][0])
        assigned, _ = self.call('manager','POST','/api/testing/assignments',test_id=tid,user_id=employee)
        aid = assigned['id']
        started, _ = self.call('employee','POST',f'/api/testing/assignments/{aid}/start')
        attempt = started['id']
        view, raw = self.call('employee','GET',f'/api/testing/attempts/{attempt}')
        self.assertEqual(len(view['questions']),4)
        self.assertNotIn(b'correct',raw)
        self.assertNotIn(b'revision_id',raw)
        self.assertNotIn(b'answer_key',raw)
        self.assertEqual({c['state'] for c in view['competencies']},{'included'})
        self.call('employee','POST',f'/api/testing/assignments/{aid}/start',status='409 Conflict')
        busy, _ = self.call('manager','POST','/api/testing/assignments',test_id=tid,user_id=employee)
        unavailable, raw = self.call('employee','POST',f"/api/testing/assignments/{busy['id']}/start",status='409 Conflict')
        self.assertEqual(unavailable['code'],'assessment_unavailable')
        self.assertEqual(len(unavailable['details']['competencies']),2)
        for comp in unavailable['details']['competencies']:
            self.assertEqual(comp['reason'],'reserved_active')
            self.assertIsNone(comp['available_at'])
        self.assertNotIn(b'correct',raw)
        self.assertEqual(self.row('SELECT status FROM test_assignments WHERE id=%s',(busy['id'],))[0],'assigned')
        self.assertEqual(self.row('SELECT count(*) FROM test_attempts WHERE assignment_id=%s',(busy['id'],))[0],0)
        answers = {qid:[opt] for qid,opt in zip(question_ids, correct_options)}
        # First competency: 1/2 => level 1; second: 0/2 => level 0.
        answers[question_ids[1]] = [wrong_options[1]]
        for i in (2,3):
            answers[question_ids[i]] = [wrong_options[i]]
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
        again, _ = self.call('manager','POST','/api/testing/assignments',test_id=tid,user_id=employee)
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


if __name__ == '__main__':
    unittest.main()

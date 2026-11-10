"""Opt-in real-connection races; run separately on another NEW empty database.

DATABASE_URL must pass the integration suite's localhost/kb_it_* guard and
KB_IT_CONCURRENCY=1 is additionally required. Fixtures intentionally commit;
dispose of the entire owned test database/container afterwards. This module
never drops objects and refuses an already initialized schema. No mocked DB,
shared application connection, or production schema is used.
"""
import hashlib
import io
import json
import os
import secrets
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import test_pg_testing_integration as integration


@unittest.skipUnless(os.environ.get('DATABASE_URL') and os.environ.get('KB_IT_CONCURRENCY') == '1',
                     'Requires separate empty kb_it_* database and KB_IT_CONCURRENCY=1')
class RealConnectionConcurrencyTests(unittest.TestCase):
    row = integration.PublishedTestApiIntegration.row
    seed = integration.PublishedTestApiIntegration.seed
    call = integration.PublishedTestApiIntegration.call
    _testing_bank = integration.PublishedTestApiIntegration._testing_bank
    _testing_token = integration.PublishedTestApiIntegration._testing_token
    draft_payload = staticmethod(integration.PublishedTestApiIntegration.draft_payload)

    @classmethod
    def setUpClass(cls):
        # Reuse the strict empty-database check and atomic bootstrap, NOT the
        # rollback-only fixture or its connection patch.
        integration.PublishedTestApiIntegration.setUpClass.__func__(cls)

    def api(self, method, path, token, csrf=None, **payload):
        if csrf is not None:
            payload['csrf'] = csrf
        body = json.dumps(payload).encode() if method in ('POST', 'PUT', 'DELETE') else b''
        url = urlsplit(path)
        env = {'REQUEST_METHOD': method, 'PATH_INFO': url.path, 'QUERY_STRING': url.query,
               'wsgi.input': io.BytesIO(body), 'CONTENT_LENGTH': str(len(body)),
               'CONTENT_TYPE': 'application/json', 'HTTP_COOKIE': 'sid=' + token,
               'REMOTE_ADDR': '127.0.0.1', 'wsgi.url_scheme': 'https'}
        output = []
        result = b''.join(self.application(env, lambda status, headers: output.append((status, headers))))
        self.assertEqual(dict(output[0][1]).get('Cache-Control'), 'no-store')
        return output[0][0], json.loads(result), result

    def request(self, role, method, path, payload):
        _, token, csrf = self.users[role]
        return self.api(method, path, token, csrf, **payload)

    def ordered_race(self, tid, first, second):
        """Observe TWO real backend PIDs waiting before releasing the row lock.

        The first request holds the department lock and waits on the test row;
        the second waits on that department lock (the real authorization path).
        Both run the unmodified WSGI/session/CSRF/transaction stack. The
        coordinator only delays entry; it does not execute either operation.
        """
        blocker = integration.psycopg2.connect(self.dsn)
        observer = integration.psycopg2.connect(self.dsn)
        observer.autocommit = True
        pool = ThreadPoolExecutor(max_workers=2)
        try:
            with blocker.cursor() as cur:
                cur.execute('SELECT id FROM tests WHERE id=%s FOR UPDATE', (tid,))
            def wait_for(count):
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    with observer.cursor() as cur:
                        cur.execute('''SELECT pid FROM pg_stat_activity
                            WHERE datname=current_database() AND pid<>%s
                              AND wait_event_type='Lock' AND (
                                (query LIKE %s AND query LIKE %s) OR
                                (query LIKE %s AND query LIKE %s))''',
                                    (blocker.get_backend_pid(), '%FROM tests WHERE id=%', '%' + tid + '%',
                                     '%FROM departments WHERE id=%', '%' + str(self.dept) + '%'))
                        pids = {r[0] for r in cur.fetchall()}
                    if len(pids) == count:
                        return pids
                    time.sleep(0.02)
                self.fail(f'Expected {count} independently blocked request backends')
            a = pool.submit(self.request, *first)
            first_pid = wait_for(1)
            b = pool.submit(self.request, *second)
            both_pids = wait_for(2)
            self.assertTrue(first_pid < both_pids)
            self.assertFalse(a.done())
            self.assertFalse(b.done())
            blocker.commit()
            results = a.result(timeout=20), b.result(timeout=20)
            print('REAL_RACE independent_backend_count=2 statuses=' + ','.join(r[0] for r in results), flush=True)
            return results
        finally:
            blocker.rollback()
            blocker.close()
            observer.close()
            pool.shutdown(wait=True)

    def test_token_publish_default_assignment_and_archive_both_orderings(self):
        self.conn = integration.psycopg2.connect(self.dsn)
        self.addCleanup(self.conn.close)
        self.seed()
        uid = self.users['kk_responsible'][0]
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        self.row('''INSERT INTO sessions(token,user_id,csrf_token,expires_at)
            VALUES(%s,%s,%s,now()+interval '1 hour') RETURNING user_id''',
                 (hashlib.sha256(token.encode()).hexdigest(), uid, csrf))
        self.users['second_editor'] = (uid, token, csrf)
        self.conn.commit()
        self.conn.autocommit = True
        questions, _ = self._testing_bank()
        blocks = [{'title': 'Concurrent', 'competencies': [
            {'competency_id': str(cid), 'question_ids': questions[str(cid)]} for cid in self.comps]}]
        due = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        for scenario in ('save_publish', 'publish_assign', 'archive_assign'):
            for reverse in (False, True):
                with self.subTest(scenario=scenario, reverse=reverse):
                    created, _ = self.call('kk_responsible', 'POST', '/api/testing/tests',
                                          title='Concurrent ' + scenario, blocks=blocks)
                    tid = created['id']; base = f'/api/testing/tests/{tid}'
                    self.call('kk_responsible', 'POST', base + '/publish', expected_token=self._testing_token(tid))
                    assignment = ('manager', 'POST', '/api/testing/assignments',
                                  dict(test_id=tid, user_id=str(self.users['employee'][0]), due_at=due))
                    if scenario != 'archive_assign':
                        draft, _ = self.call('kk_responsible', 'POST', base + '/draft',
                                            expected_token=self._testing_token(tid))
                        publish = ('second_editor', 'POST', base + '/publish',
                                   dict(expected_token=draft['expected_token']))
                    if scenario == 'save_publish':
                        payload = self.draft_payload(draft)
                        payload['description'] = 'Saved concurrently'
                        pair = (('kk_responsible', 'PUT', base + '/draft', payload), publish)
                    elif scenario == 'publish_assign':
                        pair = (publish, assignment)
                    else:
                        pair = (('kk_responsible', 'POST', base + '/archive',
                                 dict(expected_token=self._testing_token(tid))), assignment)
                    ordered = pair[::-1] if reverse else pair
                    result = self.ordered_race(tid, *ordered)
                    left, right = result[::-1] if reverse else result
                    print(f'REAL_RACE scenario={scenario} reverse={reverse}', flush=True)
                    if scenario == 'save_publish':
                        self.assertEqual([r[0] for r in result], ['200 OK', '409 Conflict'])
                        self.assertEqual(self.row('SELECT count(*) FROM test_versions WHERE test_id=%s', (tid,))[0],
                                         2 if reverse else 1)
                        saved = self.row('SELECT status,description FROM tests WHERE id=%s', (tid,))
                        self.assertEqual(saved[0], 'published' if reverse else 'draft')
                        if not reverse:
                            self.assertEqual(saved[1], 'Saved concurrently')
                    elif scenario == 'publish_assign':
                        self.assertEqual((left[0], right[0]), ('200 OK', '200 OK'))
                        pinned = 1 if reverse else 2
                        self.assertEqual(right[1]['test_version'], pinned)
                        self.assertEqual(self.row('''SELECT a.test_version,v.version FROM test_assignments a
                            JOIN test_versions v ON v.test_id=a.test_id AND v.version=a.test_version
                            WHERE a.id=%s''', (right[1]['id'],)), (pinned, pinned))
                    else:
                        self.assertEqual(left[0], '200 OK')
                        self.assertEqual(right[0], '200 OK' if reverse else '403 Forbidden')
                        self.assertEqual(self.row('SELECT count(*) FROM test_assignments WHERE test_id=%s', (tid,))[0],
                                         1 if reverse else 0)
                        if reverse:
                            self.assertEqual(right[1]['test_version'], 1)
                        self.assertEqual(self.row('SELECT status FROM tests WHERE id=%s', (tid,))[0], 'archived')

    def import_double_approval_independent_connections(self):
        self.conn = integration.psycopg2.connect(self.dsn)
        self.addCleanup(self.conn.close)
        self.seed()
        self.conn.commit()
        self.conn.autocommit = True
        code = self.row('SELECT code FROM competencies WHERE id=%s', (self.comps[0],))[0]
        payload = dict(external_id='race', competency_code=code, question='Race question', type='single',
                       options=[dict(id='a', text='Yes', is_correct=True),
                                dict(id='b', text='No', is_correct=False)])
        created, _ = self.call('kk_responsible', 'POST', '/api/testing/imports',
                               filename='race.json', json_text=json.dumps([payload]))
        path = '/api/testing/imports/' + created['id']
        item = self.call('kk_responsible', 'GET', path)[0]['items'][0]
        review = path + '/items/' + item['id'] + '/review'
        body = dict(decision='approve', expected_token=item['expected_token'])
        blocker = integration.psycopg2.connect(self.dsn)
        observer = integration.psycopg2.connect(self.dsn)
        observer.autocommit = True
        try:
            with blocker.cursor() as cur:
                cur.execute('SELECT id FROM departments WHERE id=%s FOR UPDATE', (self.dept,))
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.request, 'kk_responsible', 'POST', review, body)
                second = pool.submit(self.request, 'kk_responsible', 'POST', review, body)
                deadline = time.monotonic() + 12
                pids = set()
                while time.monotonic() < deadline:
                    with observer.cursor() as cur:
                        cur.execute('''SELECT pid FROM pg_stat_activity WHERE datname=current_database()
                            AND pid<>%s AND wait_event_type='Lock'
                            AND query LIKE %s''',
                            (blocker.get_backend_pid(), '%FROM departments WHERE id=%'))
                        pids = {row[0] for row in cur.fetchall()}
                    if len(pids) >= 2:
                        break
                    time.sleep(0.02)
                blocker.commit()
                self.assertEqual(len(pids), 2, 'Two independently blocked request backends required')
                results = first.result(timeout=20), second.result(timeout=20)
            self.assertEqual(sorted(result[0] for result in results), ['200 OK', '409 Conflict'])
            self.assertEqual(self.row('SELECT count(*) FROM questions WHERE import_item_id=%s', (item['id'],))[0], 1)
            self.assertEqual(self.row('SELECT count(*) FROM question_revisions WHERE question_id=%s',
                                      (next(result[1]['question_id'] for result in results if result[0] == '200 OK'),))[0], 1)
        finally:
            blocker.rollback()
            blocker.close()
            observer.close()


if __name__ == '__main__':
    unittest.main()

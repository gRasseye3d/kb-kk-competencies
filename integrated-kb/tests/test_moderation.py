import io
import json
import sqlite3
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from app import access, router, wsgi
from app.handlers import moderation


PID = '11111111-2222-4333-8444-555555555555'
RID = '21111111-2222-4333-8444-555555555555'


class ModerationTests(unittest.TestCase):
    def setUp(self):
        self.page = dict(id=PID, space_id='space', created_by='author',
                         status='pending_review', current_version=2, deleted_at=None)
        self.row = dict(id=RID, page_id=PID, revision_id='v2', submitted_by='author', status='pending')
        self.user = dict(user_id='author', roles=['admin'], csrf_token='token')
        self.body = dict(csrf='token', action='approve', comment='Reason')
        self.req = SimpleNamespace(user=self.user, json=lambda: self.body)
        self.space_allowed = True
        self.permissions = dict(restricted=False, allowed=False)
        self.existing = {'id': RID}
        self.insertions = 0
        self.locked = False
        self.conn = object()

        @contextmanager
        def transaction():
            yield self.conn

        def page(pid, conn=None, lock=False):
            if lock:
                self.assertIs(conn, self.conn)
                self.locked = True
            return self.page

        for target, name, kwargs in [
            (moderation.db, 'transaction', dict(side_effect=transaction)),
            (moderation, '_page', dict(side_effect=page)),
            (access, 'space', dict(side_effect=lambda *a, **k: self.space_allowed)),
            (moderation.db, 'query', dict(side_effect=self.query)),
            (moderation.db, 'execute', dict(side_effect=self.execute)),
            (moderation.audit, 'log', {})
        ]:
            patcher = patch.object(target, name, **kwargs)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            if name == 'execute': self.writes = mock
            if name == 'log': self.audit = mock

    def query(self, sql, params=(), **kwargs):
        if 'page_permissions' in sql:
            return self.permissions
        if 'SELECT * FROM page_approvals' in sql:
            self.assertIn('FOR UPDATE', sql)
            return self.row
        if 'FROM page_revisions' in sql:
            self.assertTrue(self.locked)
            return {'id': 'v2'}
        if 'SELECT id FROM page_approvals' in sql:
            self.assertTrue(self.locked)
            self.assertEqual(params, (PID, 'v2'))
            self.assertIn("status='pending'", sql)
            return self.existing
        if 'INSERT INTO page_approvals' in sql:
            self.assertTrue(self.locked)
            self.insertions += 1
            return {'id': RID}
        self.fail(sql)

    def execute(self, sql, params=(), conn=None):
        self.assertTrue(self.locked)
        if "SET status='pending_review'" in sql:
            self.page['status'] = 'pending_review'

    def payload(self, response):
        return json.loads(response.body)

    def test_admin_can_approve_own_article(self):
        self.assertEqual(moderation.decide(self.req, RID).status, '200 OK')
        self.assertEqual(self.writes.call_args.args[1], ('published', PID))
        self.audit.assert_called_once()

    def test_normal_json_decision_through_wsgi_strict_parser(self):
        raw = json.dumps(dict(self.body, comment='Обычный JSON',
                              metadata=[{'key':1},{'key':2}])).encode()
        statuses = []
        with patch.object(router, 'current_user', return_value=self.user):
            body = b''.join(wsgi.application({
                'PATH_INFO': f'/api/moderation/decide/{RID}', 'REQUEST_METHOD': 'POST',
                'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)),
                'wsgi.input': io.BytesIO(raw)}, lambda status, _: statuses.append(status)))
        self.assertEqual(statuses, ['200 OK'])
        self.assertEqual(json.loads(body), {'ok': True})
        self.assertEqual(self.writes.call_args.args[1], ('published', PID))
        self.audit.assert_called_once()

    def test_admin_cannot_reject_own_article(self):
        self.body['action'] = 'reject'
        response = moderation.decide(self.req, RID)
        self.assertEqual(response.status, '403 Forbidden')
        self.assertEqual(self.payload(response)['code'], 'self_review')
        self.writes.assert_not_called()

    def test_moderator_cannot_approve_own_article(self):
        self.user['roles'] = ['moderator']
        self.assertEqual(moderation.decide(self.req, RID).status, '403 Forbidden')
        self.writes.assert_not_called()

    def test_author_moderator_cannot_approve_admin_submission(self):
        self.user['roles'] = ['moderator']
        self.row['submitted_by'] = 'admin'
        self.assertEqual(moderation.decide(self.req, RID).status, '403 Forbidden')

    def test_other_authorized_moderator_can_decide(self):
        self.user.update(user_id='reviewer', roles=['moderator'])
        for action in ('approve', 'reject'):
            with self.subTest(action=action):
                self.body['action'] = action
                self.assertEqual(moderation.decide(self.req, RID).status, '200 OK')

    def test_stale_revision_conflicts_without_writes(self):
        self.row['revision_id'] = 'v1'
        response = moderation.decide(self.req, RID)
        self.assertEqual(response.status, '409 Conflict')
        self.assertEqual(self.payload(response)['code'], 'approval_stale')
        self.writes.assert_not_called()
        self.audit.assert_not_called()

    def test_stale_status_conflicts_for_authorized_other_moderator(self):
        self.user.update(user_id='reviewer', roles=['moderator'])
        for status in ('draft', 'published', 'rejected', 'archived'):
            with self.subTest(status=status):
                self.page['status'] = status
                self.assertEqual(moderation.decide(self.req, RID).status, '409 Conflict')
        self.writes.assert_not_called()

    def test_completed_request_conflicts(self):
        self.row['status'] = 'approved'
        self.assertEqual(moderation.decide(self.req, RID).status, '409 Conflict')
        self.writes.assert_not_called()

    def test_decision_preserves_space_page_and_deleted_access(self):
        self.user.update(user_id='reviewer', roles=['moderator'])
        for restriction in ('space', 'page', 'deleted'):
            with self.subTest(restriction=restriction):
                self.space_allowed = restriction != 'space'
                self.permissions['restricted'] = restriction == 'page'
                self.page['deleted_at'] = 'today' if restriction == 'deleted' else None
                with self.assertRaises(PermissionError):
                    moderation.decide(self.req, RID)
        self.writes.assert_not_called()

    def test_employee_cannot_decide(self):
        self.user['roles'] = []
        with self.assertRaises(PermissionError):
            moderation.decide(self.req, RID)

    def test_csrf_required_for_submit_and_decide(self):
        self.body['csrf'] = 'bad'
        for endpoint, ident in ((moderation.submit, PID), (moderation.decide, RID)):
            with self.assertRaises(PermissionError):
                endpoint(self.req, ident)
        self.assertFalse(self.locked)

    def test_reject_requires_nonblank_comment(self):
        self.body.update(action='reject', comment='  ')
        self.assertEqual(moderation.decide(self.req, RID).status, '400 Bad Request')
        self.writes.assert_not_called()

    def test_repeat_submit_author_is_idempotent_under_page_lock(self):
        self.user['roles'] = []
        self.page['status'] = 'draft'
        first = moderation.submit(self.req, PID)
        second = moderation.submit(self.req, PID)
        self.assertEqual(self.payload(first), self.payload(second))
        self.assertEqual(self.insertions, 1)
        self.writes.assert_called_once()
        self.audit.assert_called_once()
        self.assertFalse(access.page(self.user, self.page, write=True))

    def test_repeat_submit_admin_reuses_request(self):
        self.assertEqual(self.payload(moderation.submit(self.req, PID)), {'request_id': RID})
        self.assertEqual(self.insertions, 0)
        self.writes.assert_not_called()

    def test_repeat_submit_without_current_pending_request_conflicts(self):
        self.user['roles'] = []
        self.existing = None
        self.assertEqual(moderation.submit(self.req, PID).status, '409 Conflict')
        self.assertEqual(self.insertions, 0)

    def test_repeat_submit_does_not_allow_other_moderator(self):
        self.user.update(user_id='reviewer', roles=['moderator'])
        with self.assertRaises(PermissionError):
            moderation.submit(self.req, PID)
        self.assertEqual(self.insertions, 0)

    def test_repeat_submit_requires_space_access(self):
        self.user['roles'] = []
        self.space_allowed = False
        with self.assertRaises(PermissionError):
            moderation.submit(self.req, PID)

    def test_repeat_submit_deleted_page_denied(self):
        self.page['deleted_at'] = 'today'
        with self.assertRaises(PermissionError):
            moderation.submit(self.req, PID)

    def test_queue_filters_stale_rows_without_mutating_them(self):
        # Execute the queue SELECT against local in-memory rows (no project DB).
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript('''
                CREATE TABLE pages(id, title, current_version, status, deleted_at);
                CREATE TABLE page_revisions(id, page_id, version);
                CREATE TABLE page_approvals(id, page_id, revision_id, submitted_by, submitted_at, status);
                CREATE TABLE users(id, display_name);
                INSERT INTO pages VALUES('p', 'Example', 2, 'pending_review', NULL);
                INSERT INTO page_revisions VALUES('v1', 'p', 1), ('v2', 'p', 2);
                INSERT INTO page_approvals VALUES('old', 'p', 'v1', 'author', 1, 'pending'),
                    ('current', 'p', 'v2', 'author', 2, 'pending'),
                    ('done', 'p', 'v2', 'author', 0, 'approved');
            ''')
            with patch.object(moderation.db, 'query', side_effect=lambda sql: [dict(r) for r in conn.execute(sql)]):
                rows = self.payload(moderation.queue(self.req))
                self.assertEqual([r['id'] for r in rows], ['current'])
                self.assertTrue(rows[0]['can_approve'])
                self.assertFalse(rows[0]['can_reject'])
                for change in ("status='draft'", "status='published'", "status='pending_review',deleted_at='today'"):
                    conn.execute('UPDATE pages SET ' + change)
                    self.assertEqual(self.payload(moderation.queue(self.req)), [])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM page_approvals').fetchone()[0], 3)
        self.writes.assert_not_called()

    def test_queue_capabilities_and_permission_filter(self):
        row = {**self.row, 'version': 2}
        original_query = self.query
        def query(sql, *args, **kwargs):
            return [dict(row)] if 'JOIN pages' in sql else original_query(sql, *args, **kwargs)
        with patch.object(moderation.db, 'query', side_effect=query):
            self.user['roles'] = ['moderator']
            own = self.payload(moderation.queue(self.req))[0]
            self.assertFalse(own['can_approve'])
            self.assertFalse(own['can_reject'])
            self.assertEqual(own['reason'], 'self_review')
            self.user['user_id'] = 'reviewer'
            other = self.payload(moderation.queue(self.req))[0]
            self.assertTrue(other['can_approve'])
            self.assertTrue(other['can_reject'])
            self.permissions['restricted'] = True
            self.assertEqual(self.payload(moderation.queue(self.req)), [])

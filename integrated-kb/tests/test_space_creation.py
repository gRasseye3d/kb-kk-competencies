"""Creation capabilities use the existing ACL, not the employee's global role."""
import io
import json
import sqlite3
import unittest
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from app import access, router, wsgi
from app.handlers import admin, history, pages


UID = uuid.UUID('11111111-2222-4333-8444-555555555555')
SID = uuid.UUID('21111111-2222-4333-8444-555555555555')
PID = uuid.UUID('31111111-2222-4333-8444-555555555555')


class SpaceCreationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript('''
            CREATE TABLE spaces(id, name, space_key, visibility, owner_id);
            CREATE TABLE space_members(space_id, user_id, role);
            CREATE TABLE page_permissions(page_id, user_id, group_id, permission);
            CREATE TABLE group_members(group_id, user_id);
        ''')
        self.conn.execute('INSERT INTO spaces VALUES(?,?,?,?,?)',
                          (str(SID), 'Demo', 'DEMO', 'internal', 'other'))
        self.user = dict(user_id=UID, roles=['employee'], csrf_token='token')
        self.data = dict(csrf='token', space_id=str(SID), title='Unsaved article', content='')
        self.req = SimpleNamespace(user=self.user, json=lambda: self.data)
        self.calls = []
        patcher = patch.object(admin.db, 'query', side_effect=self.query)
        patcher.start()
        self.addCleanup(patcher.stop)

    def query(self, sql, params=(), one=False, conn=None):
        self.calls.append((sql, params))
        # UUID adaptation is confined to this SQLite test adapter. The application
        # must retain the actual UUID user_id used by ownership checks elsewhere.
        rows = self.conn.execute(sql.replace('%s', '?'),
                                 tuple(str(p) if isinstance(p, uuid.UUID) else p for p in params))
        if one:
            row = rows.fetchone()
            return dict(row) if row else None
        return [dict(row) for row in rows]

    def membership(self, role):
        self.conn.execute('DELETE FROM space_members')
        if role:
            self.conn.execute('INSERT INTO space_members VALUES(?,?,?)', (str(SID), str(UID), role))

    def test_readable_spaces_keep_visibility_and_report_current_write_acl(self):
        for visibility, owner, membership, roles, readable, writable in (
            ('internal', 'other', None, ['employee'], True, False),
            ('internal', 'other', 'viewer', ['employee'], True, False),
            ('private', 'other', None, ['employee'], False, False),
            ('private', 'other', 'viewer', ['employee'], True, False),
            ('private', 'other', 'editor', ['employee'], True, True),
            ('private', str(UID), None, ['employee'], True, True),
            ('private', 'other', 'admin', ['employee'], True, True),
            ('private', 'other', None, ['admin'], True, True),
            ('internal', 'other', None, ['moderator'], True, False),
        ):
            with self.subTest(visibility=visibility, owner=owner, membership=membership, roles=roles):
                self.conn.execute('UPDATE spaces SET visibility=?,owner_id=?', (visibility, owner))
                self.membership(membership)
                self.user['roles'] = roles
                rows = json.loads(admin.list_spaces(self.req).body)
                self.assertEqual(rows, [dict(id=str(SID), name='Demo', space_key='DEMO',
                                            can_create=writable)] if readable else [])
                self.assertEqual(access.space(self.user, SID, write=True), writable)
                self.assertIs(self.user['user_id'], UID)

    def test_anonymous_spaces_are_empty_without_queries(self):
        self.req.user = None
        self.assertEqual(json.loads(admin.list_spaces(self.req).body), [])
        self.assertEqual(self.calls, [])

    def test_list_request_cannot_mutate_user_or_impersonate_query_user(self):
        self.membership('editor')
        statuses = []
        with patch.object(router, 'current_user', return_value=self.user):
            body = b''.join(wsgi.application({
                'PATH_INFO': '/api/admin/spaces', 'REQUEST_METHOD': 'GET',
                'QUERY_STRING': 'user_id=other', 'wsgi.input': io.BytesIO(b'')},
                lambda status, _: statuses.append(status)))
        self.assertEqual(statuses, ['200 OK'])
        self.assertTrue(json.loads(body)[0]['can_create'])
        self.assertIs(self.user['user_id'], UID)
        self.assertTrue(all(params[0] is UID for _, params in self.calls))
        p = dict(id=PID, space_id=SID, created_by=UID, status='draft', deleted_at=None)
        self.assertTrue(access.page(self.user, p, write=True))
        p['created_by'] = uuid.uuid4()
        self.assertFalse(access.page(self.user, p, write=True))

    def test_internal_reader_viewer_private_outsider_cannot_post(self):
        for visibility, membership in [('internal', None), ('internal', 'viewer'), ('private', None)]:
            with self.subTest(visibility=visibility, membership=membership):
                self.conn.execute('UPDATE spaces SET visibility=?', (visibility,))
                self.membership(membership)
                with patch.object(pages.db, 'transaction') as transaction:
                    with self.assertRaisesRegex(PermissionError, 'Space access denied'):
                        pages.create_page(self.req)
                    transaction.assert_not_called()

    def test_employee_editor_can_create_and_revoked_access_is_rechecked(self):
        self.membership('editor')
        self.assertTrue(json.loads(admin.list_spaces(self.req).body)[0]['can_create'])

        @contextmanager
        def transaction():
            yield self.conn

        def query(sql, params=(), **kwargs):
            if 'INSERT INTO pages' in sql:
                self.assertIs(params[3], UID)
                return {'id': PID}
            return self.query(sql, params, **kwargs)

        with patch.object(pages.db, 'transaction', side_effect=transaction), \
                patch.object(pages.db, 'query', side_effect=query), \
                patch.object(pages.db, 'execute'), patch.object(pages, '_labels'), \
                patch.object(pages.audit, 'log'):
            self.assertEqual(json.loads(pages.create_page(self.req).body), {'id': str(PID)})
        self.membership(None)
        with self.assertRaisesRegex(PermissionError, 'Space access denied'):
            pages.create_page(self.req)

    def test_employee_editor_still_cannot_read_or_restore_history(self):
        self.membership('editor')
        self.assertTrue(access.space(self.user, SID, write=True))
        with patch.object(history, '_page') as page, patch.object(history.db, 'transaction') as transaction:
            with self.assertRaisesRegex(PermissionError, 'Page history role required'):
                history.list_hist(self.req, str(PID))
            with self.assertRaisesRegex(PermissionError, 'Page history role required'):
                history.restore(self.req, str(PID), str(PID))
            page.assert_not_called()
            transaction.assert_not_called()


if __name__ == '__main__':
    unittest.main()

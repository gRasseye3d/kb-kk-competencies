import unittest
import os
import hashlib
from unittest.mock import patch
from app import auth, access, content
from app.handlers import search, admin


class SecurityTests(unittest.TestCase):
    def test_password_hash_and_legacy_rejection(self):
        value = auth.hash_password('sample password')
        self.assertTrue(auth.verify_password('sample password', value))
        self.assertFalse(auth.verify_password('other', value))
        self.assertFalse(auth.verify_password('sample password', 'a' * 64))
        self.assertNotEqual(value, auth.hash_password('sample password'))

    def test_html_drops_executable_markup(self):
        rendered = content.clean('<p>Hi</p><img src=x onerror=alert(1)><a href="javascript:alert(1)">X</a>')
        self.assertNotIn('javascript:', rendered)
        self.assertNotIn('onerror', rendered)
        self.assertIn('<p>Hi</p>', rendered)

    def test_no_anonymous_space_access(self):
        self.assertFalse(access.space(None, 'fake'))
        self.assertFalse(access.page(None, None))

    def test_private_space_requires_membership(self):
        user = {'user_id': 'uid', 'roles': []}
        with patch.object(access.db, 'query', return_value=None):
            self.assertFalse(access.space(user, 'sid'))
        with patch.object(access.db, 'query', return_value={'id': 'sid'}):
            self.assertTrue(access.space(user, 'sid'))

    def test_cookie_dev_only_explicit_http_loopback(self):
        local = {'REMOTE_ADDR': '127.0.0.1', 'wsgi.url_scheme': 'http',
                 'HTTP_X_FORWARDED_PROTO': 'https'}
        with patch.dict(os.environ, {'KB_DEV_HTTP_COOKIE': '1'}):
            self.assertNotIn('Secure', auth.cookie_attributes(local))
            self.assertIn('Secure', auth.cookie_attributes({**local, 'REMOTE_ADDR': '192.0.2.4'}))
            self.assertIn('Secure', auth.cookie_attributes({**local, 'wsgi.url_scheme': 'https'}))
            self.assertIn('Secure', auth.cookie_attributes({**local, 'REMOTE_ADDR': 'bad',
                                                          'HTTP_X_FORWARDED_FOR': '127.0.0.1'}))
        with patch.dict(os.environ, {'KB_DEV_HTTP_COOKIE': '0'}):
            self.assertIn('Secure', auth.cookie_attributes(local))

    def test_login_throttle_counts_both_account_and_ip_before_password_lookup(self):
        from contextlib import contextmanager
        @contextmanager
        def transaction():
            yield object()
        counts = {}
        def query(sql, params=(), **kwargs):
            self.assertNotIn('FROM users', sql)
            self.assertIn('ON CONFLICT(scope,key_hash) DO UPDATE', sql)
            self.assertIn('RETURNING attempts', sql)
            counts[params] = counts.get(params, 0) + 1
            return {'attempts': counts[params]}
        class Request:
            environ = {'REMOTE_ADDR': '127.0.0.1', 'HTTP_X_FORWARDED_FOR': '198.51.100.1'}
            def json(self): return {'login': 'victim', 'password': 'wrong'}
        with patch.object(auth.db, 'transaction', transaction), patch.object(auth.db, 'query', side_effect=query),\
             patch.object(auth.db, 'execute'), patch.object(auth.secrets, 'randbelow', return_value=1):
            for _ in range(12):
                self.assertTrue(auth.allow_login('victim', '127.0.0.1'))
            response = admin.login(Request())
            self.assertEqual(response.status, '429 Too Many Requests')
        self.assertEqual(counts[('account', hashlib.sha256(b'victim').hexdigest())], 13)
        self.assertEqual(counts[('ip', hashlib.sha256(b'127.0.0.1').hexdigest())], 13)

    def test_login_throttle_ip_limit_cannot_be_evaded_by_changing_account(self):
        from contextlib import contextmanager
        @contextmanager
        def transaction():
            yield object()
        attempts = {}
        def query(sql, params=(), **kwargs):
            attempts[params] = attempts.get(params, 0) + 1
            return {'attempts': attempts[params]}
        with patch.object(auth.db, 'transaction', transaction), patch.object(auth.db, 'query', side_effect=query),\
             patch.object(auth.secrets, 'randbelow', return_value=1):
            for index in range(30):
                self.assertTrue(auth.allow_login(f'user-{index}', '127.0.0.1'))
            self.assertFalse(auth.allow_login('another-user', '127.0.0.1'))

    def test_pending_moderator_cannot_bypass_page_permissions(self):
        user = {'user_id': 'moderator', 'roles': ['moderator']}
        page = {'id': 'page', 'space_id': 'space', 'deleted_at': None,
                'status': 'pending_review', 'created_by': 'another'}
        with patch.object(access, 'space', return_value=True), patch.object(access.db, 'query',
                return_value={'restricted': True, 'allowed': False}):
            self.assertFalse(access.page(user, page))

    def test_search_applies_access_before_limit(self):
        class Request:
            user = {'user_id': 'uid', 'roles': ['moderator']}
            def q(self, key):
                return 'query'
        with patch.object(search.db, 'query', return_value=[]) as query:
            search.search(Request())
            sql, params = query.call_args.args
            self.assertLess(sql.index('page_permissions'), sql.index('LIMIT 50'))
            self.assertTrue(params['moderator'])


if __name__ == '__main__':
    unittest.main()

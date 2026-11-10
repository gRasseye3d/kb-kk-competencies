"""Local safety tests; no browser, server or database access."""
import contextlib
import io
import types
import unittest
from unittest.mock import patch

from deploy.poc import browser_smoke as smoke


class BrowserSmokeTests(unittest.TestCase):
    def test_default_is_read_only(self):
        args = smoke.parse_args(['https://demo.invalid/'])
        self.assertIsNone(args.exercise_user)
        self.assertEqual(args.base, 'https://demo.invalid')

    def test_exercise_cannot_target_main_or_existing_shared_smoke_account(self):
        for login in ('demo_employee', 'demo_admin', 'smoke_employee', 'smoke_employee_'):
            with self.subTest(login=login), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    smoke.parse_args(['https://demo.invalid', '--exercise-user', login])
        self.assertEqual(smoke.parse_args(['https://demo.invalid', '--exercise-user',
                                          'smoke_employee_unique_run']).exercise_user,
                         'smoke_employee_unique_run')

    def test_exercise_requires_one_untouched_assignment(self):
        row = {'status': 'assigned', 'attempt_id': None}
        self.assertEqual(smoke.fresh_assignment([row]), row)
        for rows in ([], [row, row], [{'status': 'completed', 'attempt_id': 'old'}],
                     [{'status': 'in_progress', 'attempt_id': 'old'}],
                     [{'status': 'assigned', 'attempt_id': 'old'}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                smoke.fresh_assignment(rows)

    def test_default_accepts_completed_smoke_and_only_posts_logout(self):
        writes, contexts, headings = [], [], []

        class Response:
            def __init__(self, data=None, status=200):
                self.data, self.status = data, status

            def json(self):
                return self.data

        class Context:
            def __init__(self):
                self.login, self.logged_out = '', False
                self.request = self
                contexts.append(self)

            def new_page(self):
                return self

            def on(self, *_):
                pass

            def goto(self, *_):
                pass

            def locator(self, selector):
                def fill(value):
                    if selector == '#login':
                        self.login = value
                return types.SimpleNamespace(fill=fill)

            def get_by_role(self, role, *, name, exact):
                if role == 'heading':
                    headings.append(name)
                return types.SimpleNamespace(click=lambda: None)

            def cookies(self):
                return [] if self.logged_out else [{'name': 'kb_kk_poc_sid', 'secure': True,
                                                    'httpOnly': True, 'sameSite': 'Lax'}]

            def get(self, url):
                if url.endswith('/api/me'):
                    return Response({'user': None if self.logged_out else {'login': self.login, 'csrf': 'token'}})
                if url.endswith('/api/tree'):
                    return Response([{'id': 'page', 'status': 'published'}])
                if url.endswith('/api/testing/results'):
                    return Response([{'status': 'completed', 'attempt_id': 'old'}])
                if url.endswith(('/api/admin/users', '/api/testing/questions')):
                    return Response(status=403)
                return Response()

            def post(self, url, **kwargs):
                writes.append((self.login, url))
                self.logged_out = True
                return Response()

            def close(self):
                pass

        browser = types.SimpleNamespace(new_context=lambda **_: Context(), close=lambda: None)
        pw = types.SimpleNamespace(chromium=types.SimpleNamespace(launch=lambda **_: browser))
        fake = types.ModuleType('playwright.sync_api')
        fake.sync_playwright = lambda: contextlib.nullcontext(pw)
        fake.expect = lambda _: types.SimpleNamespace(to_be_visible=lambda: None)
        credentials = {login: 'test-password' for login in
                       ('demo_admin', 'demo_manager', 'demo_kk', 'demo_employee', 'smoke_employee')}
        with patch.dict('sys.modules', {'playwright.sync_api': fake}), contextlib.redirect_stdout(io.StringIO()):
            smoke.run(smoke.parse_args(['https://demo.invalid']), credentials)
        self.assertEqual(len(contexts), 5)
        self.assertEqual(len(writes), 5)
        self.assertTrue(all(url.endswith('/api/logout') for _, url in writes))
        self.assertEqual(headings, ['Мои тесты и результаты'] * 5)


if __name__ == '__main__':
    unittest.main()

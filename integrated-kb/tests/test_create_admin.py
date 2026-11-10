"""Offline manual-bootstrap regression tests; no database or real credentials."""
import contextlib
import io
import os
import unittest
from unittest.mock import patch
import warnings

from deploy import create_admin as bootstrap


class TerminalInput(io.StringIO):
    def isatty(self):
        return True


class CreateAdminTests(unittest.TestCase):
    def setUp(self):
        # Synthetic input only; never a credential for a running service.
        self.arguments = ('sample-admin', 'admin@example.invalid', 'Sample Admin', 'x' * 12)

    def test_reads_normal_stdin_and_hides_password(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'test-only'}), \
                patch.object(bootstrap.sys, 'stdin', TerminalInput('sample-admin\nadmin@example.invalid\nSample Admin\n')), \
                patch.object(bootstrap.getpass, 'getpass', return_value=self.arguments[3]) as hidden, \
                patch.object(bootstrap, 'create_admin') as create, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            bootstrap.main()
        create.assert_called_once_with(*self.arguments)
        hidden.assert_called_once()
        self.assertNotIn(self.arguments[3], output.getvalue())

    def test_refuses_redirected_stdin(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'test-only'}), \
                patch.object(bootstrap.sys, 'stdin', io.StringIO()), \
                patch.object(bootstrap, 'create_admin') as create:
            with self.assertRaisesRegex(SystemExit, 'interactive terminal'):
                bootstrap.main()
        create.assert_not_called()

    def test_requires_explicit_database(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(bootstrap, 'create_admin') as create:
            with self.assertRaisesRegex(SystemExit, 'DATABASE_URL'):
                bootstrap.main()
        create.assert_not_called()

    def test_never_falls_back_to_echoed_password(self):
        def unsafe_prompt(_):
            warnings.warn('No hidden input available', bootstrap.getpass.GetPassWarning)
            self.fail('An echoed password must never be read')
        with patch.dict(os.environ, {'DATABASE_URL': 'test-only'}), \
                patch.object(bootstrap.sys, 'stdin', TerminalInput('sample-admin\na@b.invalid\nSample\n')), \
                patch.object(bootstrap.getpass, 'getpass', side_effect=unsafe_prompt), \
                patch.object(bootstrap, 'create_admin') as create, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(bootstrap.getpass.GetPassWarning):
                bootstrap.main()
        create.assert_not_called()

    def test_validates_before_database_access(self):
        with patch.object(bootstrap.db, 'transaction') as transaction:
            for args in (('', *self.arguments[1:]),
                         (self.arguments[0], '', *self.arguments[2:]),
                         (*self.arguments[:2], '', self.arguments[3]),
                         (*self.arguments[:3], 'x')):
                with self.assertRaises(ValueError):
                    bootstrap.create_admin(*args)
        transaction.assert_not_called()

    def test_nonempty_users_refused_under_lock(self):
        with patch.object(bootstrap.db, 'transaction'), \
                patch.object(bootstrap.db, 'execute') as execute, \
                patch.object(bootstrap.db, 'query', return_value={'exists': 1}) as query, \
                patch.object(bootstrap, 'hash_password') as hash_password:
            def existing_user(*args, **kwargs):
                self.assertEqual(execute.call_args.args[0], 'LOCK TABLE users IN EXCLUSIVE MODE')
                return {'exists': 1}
            query.side_effect = existing_user
            with self.assertRaisesRegex(ValueError, 'nonempty'):
                bootstrap.create_admin(*self.arguments)
        self.assertEqual(execute.call_count, 1)  # Lock only; no INSERT or UPDATE.
        self.assertEqual(query.call_count, 1)
        hash_password.assert_not_called()

    def test_missing_admin_role_refused(self):
        with patch.object(bootstrap.db, 'transaction'), \
                patch.object(bootstrap.db, 'execute') as execute, \
                patch.object(bootstrap.db, 'query', side_effect=[None, None]), \
                patch.object(bootstrap, 'hash_password') as hash_password:
            with self.assertRaisesRegex(ValueError, 'admin role'):
                bootstrap.create_admin(*self.arguments)
        self.assertEqual(execute.call_count, 1)
        hash_password.assert_not_called()

    def test_creates_user_and_role_in_same_transaction(self):
        with patch.object(bootstrap.db, 'transaction') as transaction, \
                patch.object(bootstrap.db, 'execute') as execute, \
                patch.object(bootstrap.db, 'query', side_effect=[None, {'id': 'role'}, {'id': 'user'}]) as query, \
                patch.object(bootstrap, 'hash_password', return_value='synthetic-hash') as hash_password:
            bootstrap.create_admin(*self.arguments)
        conn = transaction.return_value.__enter__.return_value
        self.assertEqual(execute.call_args_list[0].args[0], 'LOCK TABLE users IN EXCLUSIVE MODE')
        self.assertIn('FOR SHARE', query.call_args_list[1].args[0])
        self.assertEqual(query.call_args.args[1], (*self.arguments[:3], 'synthetic-hash'))
        self.assertEqual(execute.call_args.args[1], ('user', 'role'))
        self.assertTrue(all(call.kwargs['conn'] is conn for call in query.call_args_list + execute.call_args_list))
        hash_password.assert_called_once_with(self.arguments[3])
        transaction.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()

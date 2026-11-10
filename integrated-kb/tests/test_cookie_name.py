import os
import unittest
from unittest.mock import patch

from app import auth


class CookieNameTests(unittest.TestCase):
    def test_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(auth.cookie_name(), 'sid')

    def test_custom_cookie_does_not_read_other_app_session(self):
        with patch.dict(os.environ, {'KB_SESSION_COOKIE_NAME': 'kb_kk_poc_sid'}), patch.object(auth, 'get_session', return_value=None) as get:
            auth.current_user({'HTTP_COOKIE': 'sid=other; kb_kk_poc_sid=ours'})
            get.assert_called_once_with('ours')

    def test_invalid_name(self):
        with patch.dict(os.environ, {'KB_SESSION_COOKIE_NAME': 'bad; name'}):
            with self.assertRaises(ValueError):
                auth.cookie_name()

"""Department appointment lists must serialize as arrays, not PostgreSQL UUID text."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.handlers import admin


class AdminDepartmentProjectionTests(unittest.TestCase):
    def test_responsible_ids_use_driver_supported_text_array(self):
        uid = '11111111-1111-4111-8111-111111111111'
        rows = [{'name': 'Assigned', 'responsible_ids': [uid]},
                {'name': 'Empty', 'responsible_ids': []}]
        with patch.object(admin.db, 'query', return_value=rows) as query:
            response = admin.list_departments(SimpleNamespace(user={'roles': ['admin']}))
        self.assertEqual(json.loads(response.body), rows)
        # psycopg2 without register_uuid returns uuid[] as a PostgreSQL string.
        # text[] is natively decoded, including the empty-array fallback.
        self.assertIn('array_agg(x.user_id::text)', query.call_args.args[0])
        self.assertIn("FILTER (WHERE x.user_id IS NOT NULL),'{}')", query.call_args.args[0])

    def test_department_projection_remains_admin_only(self):
        for user in (None, {'roles': ['employee']}, {'roles': ['manager']}):
            with self.subTest(user=user), patch.object(admin.db, 'query') as query:
                with self.assertRaises(PermissionError):
                    admin.list_departments(SimpleNamespace(user=user))
                query.assert_not_called()


if __name__ == '__main__':
    unittest.main()

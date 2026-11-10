import json
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from app.utils import json_response


class JsonResponseTests(unittest.TestCase):
    def test_dates_use_isoformat_in_nested_cooldown_details(self):
        timestamp = datetime(2026, 12, 4, 19, 37, 56, tzinfo=timezone.utc)
        response = json_response({'details': {'competencies': [
            {'available_at': timestamp, 'day': date(2026, 12, 4)}]}})
        item = json.loads(response.body)['details']['competencies'][0]
        self.assertEqual(item['available_at'], '2026-12-04T19:37:56+00:00')
        self.assertEqual(item['day'], '2026-12-04')

    def test_uuid_decimal_and_response_metadata_are_preserved(self):
        identifier = UUID('931402ae-23b2-43b9-86c7-a7040457ade4')
        response = json_response({'id': identifier, 'value': Decimal('2.50')},
                                 status='409 Conflict', headers=[('X-Test', 'yes')])
        self.assertEqual(json.loads(response.body), {'id': str(identifier), 'value': '2.50'})
        self.assertEqual(response.status, '409 Conflict')
        self.assertIn(('X-Test', 'yes'), response.headers)

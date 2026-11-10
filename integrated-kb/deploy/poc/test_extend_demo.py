"""Safety tests for the additive deployment script; no network or database."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('extend_demo', Path(__file__).with_name('extend_demo.py'))
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class Client:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def call(self, *args):
        self.calls += 1
        if self.fail:
            raise TimeoutError('Ambiguous result')
        return {'id': 'created'}


class ExtensionSafetyTests(unittest.TestCase):
    def test_completed_step_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = demo.Journal(Path(directory) / 'manifest.json', {'steps': {}})
            client = Client()
            for _ in range(2):
                self.assertEqual(journal.step('x', client, 'POST', '/x', {'a': 1}), {'id': 'created'})
            self.assertEqual(client.calls, 1)
            with self.assertRaises(RuntimeError):
                journal.step('x', client, 'POST', '/x', {'a': 2})

    def test_ambiguous_write_blocks_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            journal = demo.Journal(path, {'steps': {}})
            client = Client(fail=True)
            with self.assertRaises(TimeoutError):
                journal.step('x', client, 'POST', '/x', {})
            resumed = demo.Journal(path, demo.private_json(path))
            with self.assertRaises(RuntimeError):
                resumed.step('x', client, 'POST', '/x', {})
            self.assertEqual(client.calls, 1)

    def test_credentials_private_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'credentials.json'
            demo.save(path, {'password': 'original'}, exclusive=True)
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                demo.save(path, {'password': 'replacement'}, exclusive=True)
            self.assertEqual(demo.private_json(path)['password'], 'original')

    def test_only_archival_flags_may_change(self):
        before = {'competency_profiles': [{'id': 'old', 'status': 'approved', 'is_active': True, 'name': 'Map'}],
                  'assessment_cooldowns': [{'user_id': 'u', 'available_at': 'unchanged'}]}
        after = {'competency_profiles': [{'id': 'old', 'status': 'archived', 'is_active': False, 'name': 'Map'}, {'id': 'new'}],
                 'assessment_cooldowns': [{'user_id': 'u', 'available_at': 'unchanged'}]}
        self.assertEqual(demo.preserved(before, after, 'old')['competency_profiles']['added_rows'], 1)
        after['assessment_cooldowns'][0]['available_at'] = 'reset'
        with self.assertRaises(RuntimeError):
            demo.preserved(before, after, 'old')

    def test_duplicate_or_deleted_old_rows_detected(self):
        with self.assertRaises(RuntimeError):
            demo.preserved({'answers': [{'id': 1}, {'id': 2}]}, {'answers': [{'id': 1}, {'id': 1}]}, 'old')

    def test_content_and_test_overlap(self):
        self.assertEqual(len(demo.COMPETENCIES), 6)
        self.assertEqual(set(demo.QUESTIONS), set(demo.COMPETENCIES))
        self.assertEqual(set(demo.TESTS['A'][1]) & set(demo.TESTS['B'][1]), {'EDIT'})
        for items in demo.QUESTIONS.values():
            self.assertGreaterEqual(len(items), 2)
            self.assertEqual([demo.question_payload('id', q)['type'] for q in items], ['single', 'multiple'])
            for item in items:
                options = demo.question_payload('id', item)['options']
                self.assertTrue(any(o['correct'] for o in options))
                self.assertTrue(any(not o['correct'] for o in options))


if __name__ == '__main__':
    unittest.main()

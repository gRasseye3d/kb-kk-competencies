"""Standalone verifier tests, not standalone application tests."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from verify_manifest import verify


class ManifestTests(unittest.TestCase):
    def test_accepts_matching_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'payload').write_bytes(b'fixture')
            self.assertEqual(verify(root, {'payload': hashlib.sha256(b'fixture').hexdigest()}), [])

    def test_rejects_changed_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'payload').write_bytes(b'changed')
            self.assertEqual(verify(root, {'payload': hashlib.sha256(b'fixture').hexdigest()}),
                             ['SHA256 mismatch: payload'])

    def test_rejects_missing_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(verify(Path(directory), {'missing': '0' * 64}),
                             ['Missing/unsafe file: missing'])

    def test_rejects_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in ('../outside', '/absolute', '.git/config'):
                with self.subTest(path=path):
                    self.assertEqual(verify(Path(directory), {path: '0' * 64}),
                                     [f'Unsafe manifest path: {path}'])

    def test_rejects_malformed_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(verify(Path(directory), {'payload': 'invalid'}),
                             ['Invalid SHA256: payload'])

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'original').write_bytes(b'fixture')
            (root / 'payload').symlink_to(root / 'original')
            self.assertEqual(verify(root, {'payload': hashlib.sha256(b'fixture').hexdigest()}),
                             ['Missing/unsafe file: payload'])


if __name__ == '__main__':
    unittest.main()

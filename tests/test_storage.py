import tempfile
import unittest
from pathlib import Path

from lob_recorder.storage import validate_storage


class StorageTests(unittest.TestCase):
    def test_fixture_requires_explicit_override(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".lob-storage-root").touch()
            with self.assertRaises(RuntimeError):
                validate_storage(root, "fixture", allow_test=False)
            self.assertEqual(validate_storage(root, "fixture", allow_test=True), root)

    def test_live_rejects_test_override_and_unmounted_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".lob-storage-root").touch()
            with self.assertRaises(RuntimeError):
                validate_storage(root, "live", allow_test=True)
            with self.assertRaises(RuntimeError):
                validate_storage(root, "live", allow_test=False)

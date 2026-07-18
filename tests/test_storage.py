import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lob_recorder.storage import STORAGE_MARKER, ensure_layout, usable_bytes, validate_storage


class StorageTests(unittest.TestCase):
    def test_fixture_requires_explicit_override(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".lob-storage-root").write_text(STORAGE_MARKER + "\n")
            ensure_layout(root)
            with self.assertRaises(RuntimeError):
                validate_storage(root, "fixture", allow_test=False)
            self.assertEqual(validate_storage(root, "fixture", allow_test=True), root)

    def test_live_rejects_test_override_and_unmounted_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".lob-storage-root").write_text(STORAGE_MARKER + "\n")
            ensure_layout(root)
            with self.assertRaises(RuntimeError):
                validate_storage(root, "live", allow_test=True)
            with self.assertRaises(RuntimeError):
                validate_storage(root, "live", allow_test=False)

    def test_rejects_wrong_marker_content(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".lob-storage-root").write_text("wrong\n")
            ensure_layout(root)
            with self.assertRaises(RuntimeError):
                validate_storage(root, "fixture", allow_test=True)

    def test_rejects_incomplete_collector_storage_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".lob-storage-root").write_text(STORAGE_MARKER + "\n")
            with self.assertRaises(RuntimeError):
                validate_storage(root, "fixture", allow_test=True)

    def test_usable_bytes_excludes_reserved_free_blocks(self):
        usage = SimpleNamespace(total=1_000, used=600, free=300)
        with patch("lob_recorder.storage.shutil.disk_usage", return_value=usage):
            self.assertEqual(usable_bytes("/data"), 900)

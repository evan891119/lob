import tempfile
import unittest

from lob_recorder.exporter import export_clickhouse


class ExporterTests(unittest.TestCase):
    def test_rejects_path_traversal_symbol_before_connecting(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                export_clickhouse("unavailable", "../../private", "2026-01-02", folder)

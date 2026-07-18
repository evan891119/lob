import tempfile
import unittest
import os
from pathlib import Path

from lob_recorder.credentials import load_credentials
from lob_recorder.privacy import JsonLogger, safe_fields
from lob_recorder.privacy_tools import inspect_spool_schema, inventory, purge_runtime, purge_spool
from lob_recorder.models import normalize


class PrivacyTests(unittest.TestCase):
    def test_logger_redacts_and_drops_unknown_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "collector.log"
            logger = JsonLogger(path)
            fake_person_id = "A" + "123456789"
            logger.write(f"failure {fake_person_id}", category="SJ_API_KEY=" + "PRIVATE_CANARY", account_id="ACCOUNT_CANARY")
            for handler in logger._logger.handlers:
                handler.flush()
            text = path.read_text()
            self.assertNotIn(fake_person_id, text)
            self.assertNotIn("PRIVATE_CANARY", text)
            self.assertNotIn("ACCOUNT_CANARY", text)
            self.assertIn("REDACTED", text)

    def test_inventory_never_prints_matched_text_and_runtime_purge_is_scoped(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder) / "private-runtime"
            spool = Path(folder) / "spool"
            runtime.mkdir(); spool.mkdir()
            (runtime / "sample.log").write_text("Bearer " + "PRIVATE_CANARY_VALUE")
            (spool / "pending.jsonl").write_text("public market event")
            runtime_inode = runtime.stat().st_ino
            listing = inventory(runtime)
            self.assertEqual(listing[0]["name"], "sample.log")
            self.assertNotIn("PRIVATE_CANARY_VALUE", str(listing))
            purge_runtime(runtime, dry_run=False)
            self.assertEqual(runtime.stat().st_ino, runtime_inode)
            self.assertTrue((spool / "pending.jsonl").exists())

    def test_spool_purge_preserves_bind_mount_root(self):
        with tempfile.TemporaryDirectory() as folder:
            spool = Path(folder) / "spool"
            spool.mkdir()
            (spool / "pending.jsonl").write_text("{}\n")
            spool_inode = spool.stat().st_ino
            self.assertEqual(purge_spool(spool, dry_run=False), 1)
            self.assertEqual(spool.stat().st_ino, spool_inode)
            self.assertEqual(list(spool.iterdir()), [])

    def test_sensitive_keys_are_not_allowed(self):
        self.assertNotIn("api_key", safe_fields({"api_key": "x", "status": "ok"}))

    def test_credential_parser_rejects_broad_host_file(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "credentials"
            file.write_text("SJ_API_KEY=" + "fake-key\n" + "SJ_SEC_KEY=" + "fake-secret\n")
            os.chmod(file, 0o644)
            with self.assertRaises(RuntimeError):
                load_credentials(file)

    def test_credential_parser_rejects_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "credentials"
            file.write_text(
                "SJ_API_KEY=" + "fake-key\n"
                + "SJ_API_KEY=" + "second-fake-key\n"
                + "SJ_SEC_KEY=" + "fake-secret\n"
            )
            os.chmod(file, 0o600)
            with self.assertRaises(RuntimeError):
                load_credentials(file)

    def test_spool_schema_reports_count_without_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            record = normalize(
                {"stream": "tick", "symbol": "2330", "exchange": "TSE", "security_type": "STK", "event_ts": "2026-01-02T09:00:00+08:00"},
                "00000000-0000-0000-0000-000000000001",
                1,
            ).to_record()
            import json
            (root / "pending.jsonl").write_text(json.dumps(record) + "\n" + json.dumps({"unexpected": "PRIVATE_CANARY"}) + "\n")
            result = inspect_spool_schema(root)
            self.assertEqual(result["records"], 2)
            self.assertEqual(result["violations"], 1)
            self.assertNotIn("PRIVATE_CANARY", str(result))

    def test_large_private_file_is_reported_as_not_scanned(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "large.log").write_bytes(b"x" * 10_000_001)
            result = inventory(root)
            self.assertEqual(result[0]["sensitive_hits"], -1)

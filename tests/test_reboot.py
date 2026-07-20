import json
import tempfile
import unittest
from pathlib import Path

from lob_recorder.reboot import build_reboot_evidence


def acceptance(started_at: str, lob_rows: int, tick_rows: int):
    return {
        "database": {
            "lob_rows": lob_rows,
            "tick_rows": tick_rows,
            "first_event_ts": "2026-01-02 09:00:00+08:00",
        },
        "latest_session": {
            "started_at": started_at,
            "simulation": True,
        },
        "checks": {
            "health_fresh": True,
            "collector_operational": True,
            "subscriptions_active": True,
            "storage_below_stop_threshold": True,
            "no_open_gaps": True,
        },
        "private_canary": "PRIVATE_REBOOT_CANARY",
    }


class RebootEvidenceTests(unittest.TestCase):
    def _write(self, root: Path, name: str, payload: dict) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_verified_reboot_preserves_history_without_private_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before = self._write(root, "before.json", acceptance("before", 100, 50))
            after = self._write(root, "after.json", acceptance("after", 120, 60))
            report = build_reboot_evidence(before, after, reboot_observed=True)

        encoded = json.dumps(report)
        self.assertTrue(report["checks"]["reboot_persistence_verified"])
        self.assertEqual(report["row_deltas"], {"lob_rows": 20, "tick_rows": 10})
        self.assertNotIn("PRIVATE_REBOOT_CANARY", encoded)

    def test_same_boot_and_session_fail_reboot_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before = self._write(root, "before.json", acceptance("same", 100, 50))
            after = self._write(root, "after.json", acceptance("same", 100, 50))
            report = build_reboot_evidence(before, after, reboot_observed=False)

        self.assertFalse(report["checks"]["host_reboot_observed"])
        self.assertFalse(report["checks"]["collector_session_restarted"])
        self.assertFalse(report["checks"]["reboot_persistence_verified"])

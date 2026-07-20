import json
import tempfile
import unittest
from pathlib import Path

from lob_recorder.outage import build_outage_evidence


def acceptance(started_at: str, counters: dict, intervals: int, open_intervals: int):
    return {
        "latest_session": {
            "started_at": started_at,
            "simulation": True,
            **counters,
        },
        "gaps": [{
            "category": "database_failure",
            "intervals": intervals,
            "open_intervals": open_intervals,
            "affected_count": 10,
        }],
        "private_canary": "PRIVATE_OUTAGE_CANARY",
    }


class OutageEvidenceTests(unittest.TestCase):
    def _write(self, root: Path, name: str, payload: dict) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_verified_outage_uses_only_counter_deltas_and_checks(self):
        before_counters = {
            "received": 100, "written": 100, "spooled": 0,
            "replayed": 0, "dropped": 0, "notice_dropped": 0,
        }
        after_counters = {
            "received": 160, "written": 120, "spooled": 40,
            "replayed": 40, "dropped": 0, "notice_dropped": 0,
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before = self._write(root, "before.json", acceptance("start", before_counters, 0, 0))
            after = self._write(root, "after.json", acceptance("start", after_counters, 1, 0))
            report = build_outage_evidence(before, after, 60)

        encoded = json.dumps(report)
        self.assertTrue(report["checks"]["outage_recovery_verified"])
        self.assertEqual(report["counter_deltas"]["spooled"], 40)
        self.assertEqual(report["counter_deltas"]["replayed"], 40)
        self.assertNotIn("PRIVATE_OUTAGE_CANARY", encoded)

    def test_restart_or_open_gap_fails_outage_verification(self):
        counters = {
            "received": 100, "written": 80, "spooled": 20,
            "replayed": 20, "dropped": 0, "notice_dropped": 0,
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before = self._write(root, "before.json", acceptance("old", {key: 0 for key in counters}, 0, 0))
            after = self._write(root, "after.json", acceptance("new", counters, 1, 1))
            report = build_outage_evidence(before, after, 60)

        self.assertFalse(report["checks"]["same_collector_session"])
        self.assertFalse(report["checks"]["database_gap_closed"])
        self.assertFalse(report["checks"]["outage_recovery_verified"])

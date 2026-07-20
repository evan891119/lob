import json
import tempfile
import unittest
from pathlib import Path

from lob_recorder.outage import build_network_outage_evidence


def acceptance(started_at: str, counters: dict, intervals: int, open_intervals: int):
    return {
        "latest_session": {
            "started_at": started_at,
            "simulation": True,
            "subscriptions_active": 4,
            "subscriptions_failed": 0,
            **counters,
        },
        "gaps": [{
            "category": "connection_down",
            "intervals": intervals,
            "open_intervals": open_intervals,
            "affected_count": 0,
        }],
        "private_canary": "PRIVATE_NETWORK_CANARY",
    }


class NetworkOutageEvidenceTests(unittest.TestCase):
    def _write(self, root: Path, name: str, payload: dict) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_verified_network_outage_requires_restored_streams(self):
        before_counters = {
            "received": 100, "dropped": 0, "notice_dropped": 0, "reconnects": 0,
        }
        after_counters = {
            "received": 130, "dropped": 0, "notice_dropped": 0, "reconnects": 1,
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before = self._write(root, "before.json", acceptance("start", before_counters, 0, 0))
            after = self._write(root, "after.json", acceptance("start", after_counters, 1, 0))
            report = build_network_outage_evidence(before, after, 60)

        encoded = json.dumps(report)
        self.assertTrue(report["checks"]["network_outage_verified"])
        self.assertEqual(report["counter_deltas"]["reconnects"], 1)
        self.assertNotIn("PRIVATE_NETWORK_CANARY", encoded)

    def test_open_gap_or_missing_reconnect_fails_verification(self):
        counters = {
            "received": 100, "dropped": 0, "notice_dropped": 0, "reconnects": 0,
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before = self._write(root, "before.json", acceptance("start", counters, 0, 0))
            after = self._write(root, "after.json", acceptance("start", counters, 1, 1))
            report = build_network_outage_evidence(before, after, 60)

        self.assertFalse(report["checks"]["reconnect_recorded"])
        self.assertFalse(report["checks"]["connection_gap_closed"])
        self.assertFalse(report["checks"]["network_outage_verified"])

import tempfile
import time
import unittest
from pathlib import Path

from lob_recorder.collector import Collector
from lob_recorder.sinks import JsonlSink, read_jsonl


EVENT = {"stream": "tick", "exchange": "TSE", "security_type": "STK", "symbol": "2330",
         "event_ts": "2026-01-02T09:00:00+08:00", "close": 100, "volume": 1, "total_volume": 1,
         "tick_type": 1, "best_bid_price": 99, "best_bid_volume": 1, "best_ask_price": 100, "best_ask_volume": 2}


class FailingSink:
    def write(self, records):
        raise ConnectionError("upstream text must not be persisted")


class CollectorTests(unittest.TestCase):
    def test_fixture_batch_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            collector = Collector(JsonlSink(root / "events.jsonl"), root / "spool", root / "log/collector.log", root / "health.json", batch_size=2, flush_ms=10)
            collector.start(); collector.emit(EVENT); collector.emit(EVENT); collector.stop()
            self.assertEqual(len(read_jsonl(root / "events.jsonl")), 2)
            self.assertEqual(collector.counters.written, 2)

    def test_database_failure_is_spooled_without_raw_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            collector = Collector(FailingSink(), root / "spool", root / "log/collector.log", root / "health.json", batch_size=1, flush_ms=10)
            collector.start(); collector.emit(EVENT); collector.stop()
            self.assertEqual(collector.counters.spooled, 1)
            self.assertNotIn("upstream text", (root / "log/collector.log").read_text())

import tempfile
import time
import unittest
import json
import threading
from pathlib import Path

from lob_recorder.collector import Collector
from lob_recorder.sinks import JsonlSink, read_jsonl
from lob_recorder.storage import Capacity


EVENT = {"stream": "tick", "exchange": "TSE", "security_type": "STK", "symbol": "2330",
         "event_ts": "2026-01-02T09:00:00+08:00", "close": 100, "volume": 1, "total_volume": 1,
         "tick_type": 1, "best_bid_price": 99, "best_bid_volume": 1, "best_ask_price": 100, "best_ask_volume": 2}


class FailingSink:
    def write(self, records):
        raise ConnectionError("upstream text must not be persisted")

    def session(self, record):
        return None

    def gap(self, record):
        return None


class RecoveringSink:
    def __init__(self):
        self.available = True
        self.events = []
        self.sessions = []
        self.gaps = []

    def _check(self):
        if not self.available:
            raise ConnectionError("private upstream diagnostic")

    def write(self, records):
        self._check(); self.events.extend(records)

    def session(self, record):
        self._check(); self.sessions.append(record)

    def gap(self, record):
        self._check(); self.gaps.append(record)


class AuditFirstSink(RecoveringSink):
    def __init__(self):
        super().__init__()
        self.fail_next_gap = True

    def gap(self, record):
        if self.fail_next_gap:
            self.fail_next_gap = False
            raise ConnectionError("audit not ready")
        super().gap(record)


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

    def test_outage_replays_market_and_audit_without_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sink = RecoveringSink()
            collector = Collector(sink, root / "spool", root / "log/collector.log", root / "health.json", batch_size=1, flush_ms=5, replay_interval=0.02)
            collector.start()
            sink.available = False
            collector.emit(EVENT)
            deadline = time.monotonic() + 2
            while collector.counters.spooled < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            sink.available = True
            while collector.counters.replayed < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            collector.stop()
            self.assertEqual(len(sink.events), 1)
            self.assertEqual(collector.counters.replayed, 1)
            database_gaps = [row for row in sink.gaps if row["category"] == "database_failure"]
            self.assertTrue(database_gaps)
            self.assertIsNotNone(database_gaps[-1]["ended_at"])
            self.assertFalse((root / "spool/pending.jsonl").exists())

    def test_callback_overflow_never_calls_sink(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sink = RecoveringSink()
            collector = Collector(sink, root / "spool", root / "log/collector.log", root / "health.json", queue_size=1)
            collector.emit(EVENT)
            collector.emit(EVENT)
            self.assertEqual(sink.events, [])
            self.assertEqual(sink.gaps, [])
            self.assertEqual(collector.counters.dropped, 1)
            self.assertEqual(collector.notices.qsize(), 1)

    def test_capacity_stop_requests_graceful_shutdown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sink = RecoveringSink()
            collector = Collector(
                sink, root / "spool", root / "log/collector.log", root / "health.json",
                storage_root=root, capacity_probe=lambda _root: Capacity(95, 1), flush_ms=5,
            )
            collector.start()
            deadline = time.monotonic() + 2
            while not collector.stop_requested() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(collector.stop_requested())
            self.assertEqual(collector.stop_reason, "disk_capacity")
            collector.stop(collector.stop_reason)
            self.assertEqual(sink.sessions[-1]["status"], "disk_capacity")
            self.assertEqual(sink.sessions[-1]["capacity_bytes_percent"], 95)
            self.assertEqual(sink.sessions[-1]["capacity_inode_percent"], 1)
            self.assertEqual(sink.sessions[-1]["capacity_used_percent"], 95)
            self.assertEqual(sink.sessions[-1]["queue_capacity"], 20_000)

    def test_inode_capacity_warning_is_reported_separately(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            collector = Collector(
                RecoveringSink(), root / "spool", root / "log/collector.log", root / "health.json",
                storage_root=root, capacity_probe=lambda _root: Capacity(10, 85),
            )
            collector._check_capacity(time.monotonic())
            collector._write_health("running")
            health = json.loads((root / "health.json").read_text())
            self.assertFalse(collector.stop_requested())
            self.assertEqual(health["storage_capacity"]["bytes_percent"], 10)
            self.assertEqual(health["storage_capacity"]["inode_percent"], 85)
            self.assertEqual(health["storage_capacity"]["used_percent"], 85)

    def test_capacity_probe_failure_stops_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            def unavailable(_root):
                raise OSError("private filesystem diagnostic")

            collector = Collector(
                RecoveringSink(), root / "spool", root / "log/collector.log", root / "health.json",
                storage_root=root, capacity_probe=unavailable,
            )
            collector._check_capacity(time.monotonic())
            self.assertTrue(collector.stop_requested())
            self.assertEqual(collector.stop_reason, "storage_unavailable")
            self.assertNotIn("private filesystem diagnostic", (root / "log/collector.log").read_text())

    def test_health_contains_metrics_and_is_atomic_json(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            collector = Collector(JsonlSink(root / "events.jsonl"), root / "spool", root / "log/collector.log", root / "health.json", batch_size=1, flush_ms=5)
            collector.start(); collector.emit(EVENT); collector.stop()
            health = json.loads((root / "health.json").read_text())
            self.assertEqual(health["status"], "stopped")
            self.assertIn("queue_high_water", health["counters"])

    def test_concurrent_health_writes_share_one_atomic_replace_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            collector = Collector(
                JsonlSink(root / "events.jsonl"), root / "spool",
                root / "log/collector.log", root / "health.json",
            )
            barrier = threading.Barrier(8)
            errors = []

            def writer():
                try:
                    barrier.wait()
                    for _ in range(50):
                        collector._write_health("running")
                except Exception as exc:
                    errors.append(type(exc).__name__)

            threads = [threading.Thread(target=writer) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            self.assertEqual(json.loads((root / "health.json").read_text())["status"], "running")

    def test_session_persists_public_subscription_results(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sink = RecoveringSink()
            collector = Collector(sink, root / "spool", root / "log/collector.log", root / "health.json")
            collector.start()
            collector.set_subscriptions(1, 0, ["STK:TSE:2330:tick:2330:subscribed"])
            collector.stop()
            self.assertEqual(
                sink.sessions[-1]["subscription_results"],
                ["STK:TSE:2330:tick:2330:subscribed"],
            )

    def test_partial_subscription_failure_is_degraded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sink = RecoveringSink()
            collector = Collector(sink, root / "spool", root / "log/collector.log", root / "health.json")
            collector.start()
            collector.set_subscriptions(1, 1, [
                "STK:TSE:2330:bidask:2330:subscribed",
                "STK:TSE:2330:tick:2330:RuntimeError",
            ])
            health = json.loads((root / "health.json").read_text())
            collector.stop()
            self.assertEqual(health["status"], "degraded")
            self.assertIn("degraded", [record["status"] for record in sink.sessions])

    def test_untrusted_runtime_log_is_size_limited_without_reading_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            untrusted = root / "shioaji.log"
            untrusted.write_bytes(b"x" * 1_000_001)
            collector = Collector(
                RecoveringSink(), root / "spool", root / "log/collector.log", root / "health.json",
                untrusted_log_path=untrusted, untrusted_log_max_bytes=1_000_000,
            )
            collector._check_runtime_log(time.monotonic())
            self.assertEqual(untrusted.stat().st_size, 0)

    def test_market_replay_waits_until_older_audit_replay_succeeds(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sink = AuditFirstSink()
            collector = Collector(sink, root / "spool", root / "log/collector.log", root / "health.json")
            gap = collector._gap_base("database_failure", 1, "000000000000")
            collector.audit_spool.append([{"kind": "gap", "record": gap}])
            collector.spool.append([{"stream": "tick", "sequence_no": 1}])
            collector._replay_spools()
            self.assertEqual(sink.events, [])
            collector._replay_spools()
            self.assertEqual(len(sink.events), 1)

    def test_recovered_direct_write_replays_open_audit_before_closing_gap(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sink = RecoveringSink()
            collector = Collector(sink, root / "spool", root / "log/collector.log", root / "health.json")
            sink.available = False
            collector._flush([{"stream": "tick", "sequence_no": 1}])
            sink.available = True
            collector._flush([{"stream": "tick", "sequence_no": 2}])
            database_gaps = [row for row in sink.gaps if row["category"] == "database_failure"]
            self.assertEqual(database_gaps[0]["gap_id"], database_gaps[-1]["gap_id"])
            self.assertIsNone(database_gaps[0]["ended_at"])
            self.assertIsNotNone(database_gaps[-1]["ended_at"])
            self.assertEqual([row["sequence_no"] for row in sink.events], [2, 1])

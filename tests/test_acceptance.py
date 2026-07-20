import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lob_recorder.acceptance import collect_acceptance_report
from lob_recorder.models import TAIPEI


class Result:
    def __init__(self, rows):
        self.column_names = list(rows[0]) if rows else []
        self.result_rows = [tuple(row.values()) for row in rows]


class FakeClient:
    def query(self, statement):
        if "uniqExact(symbol) AS symbols" in statement:
            return Result([{
                "lob_rows": 20,
                "tick_rows": 10,
                "symbols": 2,
                "trading_days": 1,
                "first_event_ts": "2026-01-02 09:00:00",
                "last_event_ts": "2026-01-02 09:01:00",
            }])
        if "GROUP BY security_type" in statement:
            return Result([
                {
                    "security_type": "STK",
                    "exchange": "TSE",
                    "symbol": "2330",
                    "lob_rows": 20,
                    "tick_rows": 10,
                    "trading_days": 1,
                    "first_event_ts": "2026-01-02 09:00:00",
                    "last_event_ts": "2026-01-02 09:01:00",
                },
                {
                    "security_type": "FUT",
                    "exchange": "TAIFEX",
                    "symbol": "TXF_TEST",
                    "lob_rows": 8,
                    "tick_rows": 2,
                    "trading_days": 1,
                    "first_event_ts": "2026-01-02 09:00:00",
                    "last_event_ts": "2026-01-02 09:01:00",
                },
            ])
        if "capture_sessions_latest" in statement:
            return Result([{
                "status": "active",
                "simulation": True,
                "symbols": ["2330"],
                "enabled_symbols": 1,
                "subscriptions_active": 2,
                "subscriptions_failed": 0,
                "subscription_results": ["STK:TSE:2330:tick:2330:subscribed"],
                "received": 30,
                "written": 30,
                "spooled": 0,
                "replayed": 0,
                "dropped": 0,
                "notice_dropped": 0,
                "reconnects": 0,
                "queue_capacity": 20_000,
                "queue_high_water": 2,
                "capacity_bytes_percent": 10.0,
                "capacity_inode_percent": 1.0,
                "capacity_used_percent": 10.0,
                "batch_count": 3,
                "batch_insert_ms_max": 4.0,
                "callback_latency_ms_max": 5.0,
                "clock_anomalies": 0,
                "started_at": "2026-01-02 09:00:00",
                "ended_at": None,
            }])
        if "capture_gaps_latest" in statement:
            return Result([{
                "category": "database_failure",
                "intervals": 1,
                "open_intervals": 0,
                "affected_count": 1,
            }])
        raise AssertionError("unexpected acceptance query")


class AcceptanceTests(unittest.TestCase):
    def test_report_contains_only_allowlisted_operational_evidence(self):
        health = {
            "status": "running",
            "updated_at": datetime.now(TAIPEI).isoformat(),
            "session_id": "PRIVATE_SESSION_CANARY",
            "account_id": "PRIVATE_ACCOUNT_CANARY",
            "queue_size": 0,
            "queue_capacity": 20_000,
            "storage_capacity": {"bytes_percent": 10, "inode_percent": 1, "used_percent": 10},
            "subscriptions_active": 2,
            "subscriptions_failed": 0,
            "subscription_results": [
                "STK:TSE:2330:tick:2330:subscribed",
                "SJ_API_KEY:PRIVATE_SUBSCRIPTION_CANARY",
            ],
            "counters": {"received": 30, "written": 30, "unknown_private": "PRIVATE_VALUE"},
        }
        fake_module = SimpleNamespace(get_client=lambda **_kwargs: FakeClient())
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            sys.modules, {"clickhouse_connect": fake_module}
        ):
            path = Path(folder) / "health.json"
            path.write_text(json.dumps(health))
            report = collect_acceptance_report("clickhouse", path)

        encoded = json.dumps(report)
        self.assertNotIn("PRIVATE_SESSION_CANARY", encoded)
        self.assertNotIn("PRIVATE_ACCOUNT_CANARY", encoded)
        self.assertNotIn("PRIVATE_VALUE", encoded)
        self.assertNotIn("PRIVATE_SUBSCRIPTION_CANARY", encoded)
        self.assertTrue(report["checks"]["health_fresh"])
        self.assertTrue(report["checks"]["simulation_only"])
        self.assertTrue(report["checks"]["both_streams_present"])
        self.assertTrue(report["checks"]["stock_both_streams_present"])
        self.assertTrue(report["checks"]["futures_both_streams_present"])
        self.assertFalse(report["checks"]["options_both_streams_present"])
        self.assertTrue(report["checks"]["no_open_gaps"])
        self.assertFalse(report["checks"]["pilot_scope_reached"])

    def test_unreadable_health_is_safe_and_fails_health_checks(self):
        fake_module = SimpleNamespace(get_client=lambda **_kwargs: FakeClient())
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            sys.modules, {"clickhouse_connect": fake_module}
        ):
            path = Path(folder) / "health.json"
            path.write_text('{"account_id":"PRIVATE_ACCOUNT_CANARY"}')
            report = collect_acceptance_report("clickhouse", path)

        self.assertFalse(report["health"]["readable"])
        self.assertFalse(report["checks"]["health_fresh"])
        self.assertFalse(report["checks"]["collector_operational"])
        self.assertNotIn("PRIVATE_ACCOUNT_CANARY", json.dumps(report))

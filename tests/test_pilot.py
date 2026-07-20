import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lob_recorder.pilot import collect_report


class Result:
    def __init__(self, columns, rows):
        self.column_names = columns
        self.result_rows = rows


class FakeClient:
    def query(self, statement):
        if "peak_events_per_second" in statement:
            return Result(
                [
                    "table", "security_type", "exchange", "symbol",
                    "trading_date", "peak_events_per_second",
                ],
                [("lob_events", "STK", "TSE", "2330", "2026-01-02", 12)],
            )
        if "average_events_per_second" in statement:
            return Result(
                [
                    "table", "security_type", "exchange", "symbol", "trading_date",
                    "rows", "first_event_ts", "last_event_ts",
                    "average_events_per_second", "latency_ms_p50", "latency_ms_p95",
                    "latency_ms_p99",
                ],
                [
                    (
                        "lob_events", "STK", "TSE", "2330", "2026-01-02",
                        100, "first", "last", 5, 1, 2, 3,
                    )
                ],
            )
        if "system.parts" in statement:
            return Result(
                [
                    "table", "rows", "bytes_on_disk",
                    "compressed_data_bytes", "uncompressed_data_bytes",
                ],
                [("lob_events", 100, 1_000, 400, 1_600)],
            )
        if "capture_sessions_latest" in statement:
            return Result([], [])
        if "capture_gaps_latest" in statement:
            return Result([], [])
        raise AssertionError("unexpected pilot query")


class PilotTests(unittest.TestCase):
    def test_report_includes_peak_eps_compression_and_retention_estimate(self):
        fake_module = SimpleNamespace(get_client=lambda **_kwargs: FakeClient())
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            sys.modules, {"clickhouse_connect": fake_module}
        ):
            output = Path(folder) / "pilot.json"
            collect_report("clickhouse", output, storage_total=20_000)
            report = json.loads(output.read_text())

        self.assertEqual(report["market"][0]["peak_events_per_second"], 12)
        self.assertEqual(report["bytes_on_disk"], 1_000)
        self.assertEqual(report["compressed_data_bytes"], 400)
        self.assertEqual(report["uncompressed_data_bytes"], 1_600)
        self.assertEqual(report["compression_ratio"], 4.0)
        self.assertEqual(report["market_parts"][0]["compression_ratio"], 4.0)
        self.assertFalse(report["pilot_scope"]["minimum_product_count_reached"])
        self.assertEqual(report["storage"]["observed_trading_days"], 1)
        self.assertEqual(report["storage"]["average_bytes_on_disk_per_day"], 1_000)
        self.assertEqual(report["storage"]["estimated_retention_days_at_90_percent"], 18)
        projection = report["capacity_projections"]
        self.assertFalse(projection["basis"]["minimum_dataset_scope_reached"])
        self.assertEqual(projection["basis"]["bytes_on_disk_per_product_trading_day"], 1_000)
        self.assertEqual(projection["basis"]["average_aggregate_events_per_second"], 5.0)
        self.assertEqual(projection["basis"]["conservative_peak_sum_events_per_second"], 12.0)
        ten_products = projection["targets"][0]
        self.assertEqual(ten_products["products"], 10)
        self.assertEqual(ten_products["estimated_average_events_per_second"], 50.0)
        self.assertEqual(
            ten_products["estimated_conservative_peak_sum_events_per_second"], 120.0
        )
        self.assertEqual(ten_products["estimated_bytes_per_trading_day"], 10_000)
        self.assertEqual(ten_products["estimated_bytes_per_20_trading_days"], 200_000)
        self.assertEqual(ten_products["estimated_bytes_per_250_trading_days"], 2_500_000)
        self.assertEqual(
            ten_products["estimated_one_full_copy_backup_bytes_per_250_trading_days"],
            2_500_000,
        )
        self.assertEqual(ten_products["estimated_retention_trading_days_at_90_percent"], 1)

    def test_report_marks_minimum_and_recommended_pilot_scope(self):
        class FiveDayClient(FakeClient):
            def query(self, statement):
                result = super().query(statement)
                if "average_events_per_second" in statement:
                    rows = []
                    for day in range(2, 7):
                        for symbol in ("2330", "2317", "TXFR1"):
                            rows.append(
                                (
                                    "lob_events", "STK", "TSE", symbol,
                                    f"2026-01-{day:02d}", 100,
                                    "first", "last", 5, 1, 2, 3,
                                )
                            )
                    return Result(result.column_names, rows)
                if "peak_events_per_second" in statement:
                    return Result(result.column_names, [])
                return result

        fake_module = SimpleNamespace(get_client=lambda **_kwargs: FiveDayClient())
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            sys.modules, {"clickhouse_connect": fake_module}
        ):
            output = Path(folder) / "pilot.json"
            collect_report("clickhouse", output)
            report = json.loads(output.read_text())

        self.assertEqual(report["pilot_scope"]["observed_products"], 3)
        self.assertEqual(report["pilot_scope"]["observed_trading_days"], 5)
        self.assertTrue(report["pilot_scope"]["minimum_dataset_scope_reached"])
        self.assertTrue(report["pilot_scope"]["recommended_five_day_scope_reached"])

    def test_scope_distinguishes_same_symbol_across_market_identity(self):
        class SameSymbolClient(FakeClient):
            def query(self, statement):
                result = super().query(statement)
                if "average_events_per_second" in statement:
                    rows = [
                        (
                            "lob_events", security_type, exchange, "SAME", "2026-01-02",
                            100, "first", "last", 5, 1, 2, 3,
                        )
                        for security_type, exchange in (
                            ("STK", "TSE"), ("FUT", "TAIFEX"), ("OPT", "TAIFEX")
                        )
                    ]
                    return Result(result.column_names, rows)
                if "peak_events_per_second" in statement:
                    return Result(result.column_names, [])
                return result

        fake_module = SimpleNamespace(get_client=lambda **_kwargs: SameSymbolClient())
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            sys.modules, {"clickhouse_connect": fake_module}
        ):
            output = Path(folder) / "pilot.json"
            collect_report("clickhouse", output)
            report = json.loads(output.read_text())

        self.assertEqual(report["pilot_scope"]["observed_products"], 3)
        self.assertTrue(report["pilot_scope"]["minimum_product_count_reached"])

    def test_empty_dataset_does_not_invent_retention(self):
        class EmptyClient(FakeClient):
            def query(self, statement):
                result = super().query(statement)
                if "average_events_per_second" in statement or "peak_events_per_second" in statement:
                    return Result(result.column_names, [])
                if "system.parts" in statement:
                    return Result(result.column_names, [])
                return result

        fake_module = SimpleNamespace(get_client=lambda **_kwargs: EmptyClient())
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            sys.modules, {"clickhouse_connect": fake_module}
        ):
            output = Path(folder) / "pilot.json"
            collect_report("clickhouse", output, storage_total=20_000)
            report = json.loads(output.read_text())

        self.assertEqual(report["market"], [])
        self.assertIsNone(report["compression_ratio"])
        self.assertFalse(report["pilot_scope"]["minimum_dataset_scope_reached"])
        self.assertIsNone(report["storage"]["estimated_retention_days_at_90_percent"])
        projection = report["capacity_projections"]
        self.assertIsNone(projection["basis"]["bytes_on_disk_per_product_trading_day"])
        self.assertIsNone(projection["basis"]["average_aggregate_events_per_second"])
        self.assertIsNone(projection["targets"][0]["estimated_bytes_per_trading_day"])
        self.assertIsNone(
            projection["targets"][0]["estimated_retention_trading_days_at_90_percent"]
        )

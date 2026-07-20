import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lob_recorder.pilot import collect_report


class Result:
    def __init__(self, columns, rows):
        self.column_names = columns
        self.result_rows = rows


class FakeClient:
    def __init__(self):
        self.queries = []

    def query(self, statement, parameters=None):
        self.queries.append((statement, parameters or {}))
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
            return Result(
                ["process_cpu_seconds", "process_max_rss_bytes", "average_process_cpu_percent"],
                [(25.0, 104_857_600, 12.5)],
            )
        if "capture_gaps_latest" in statement:
            return Result([], [])
        raise AssertionError("unexpected pilot query")


class PilotTests(unittest.TestCase):
    def test_report_includes_peak_eps_compression_and_retention_estimate(self):
        client = FakeClient()
        fake_module = SimpleNamespace(get_client=lambda **_kwargs: client)
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            sys.modules, {"clickhouse_connect": fake_module}
        ):
            output = Path(folder) / "pilot.json"
            collect_report("clickhouse", output, storage_total=20_000)
            report = json.loads(output.read_text())

        self.assertEqual(report["market"][0]["peak_events_per_second"], 12)
        self.assertEqual(report["report_scope"]["market_storage_measurement"], "exact_active_parts")
        self.assertEqual(report["bytes_on_disk"], 1_000)
        self.assertEqual(report["compressed_data_bytes"], 400)
        self.assertEqual(report["uncompressed_data_bytes"], 1_600)
        self.assertEqual(report["compression_ratio"], 4.0)
        self.assertEqual(report["market_parts"][0]["compression_ratio"], 4.0)
        self.assertEqual(report["capture_sessions"][0]["process_max_rss_bytes"], 104_857_600)
        self.assertEqual(report["capture_sessions"][0]["average_process_cpu_percent"], 12.5)
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
            def query(self, statement, parameters=None):
                result = super().query(statement, parameters)
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
            def query(self, statement, parameters=None):
                result = super().query(statement, parameters)
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

    def test_date_scope_filters_queries_and_estimates_storage_by_table_row_share(self):
        class ScopedClient(FakeClient):
            def query(self, statement, parameters=None):
                result = super().query(statement, parameters)
                if "system.parts" in statement:
                    return Result(
                        result.column_names,
                        [("lob_events", 1_000, 10_000, 4_000, 16_000)],
                    )
                return result

        client = ScopedClient()
        fake_module = SimpleNamespace(get_client=lambda **_kwargs: client)
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            sys.modules, {"clickhouse_connect": fake_module}
        ):
            output = Path(folder) / "pilot.json"
            collect_report(
                "clickhouse", output, storage_total=20_000,
                start_date=date(2026, 1, 2), end_date=date(2026, 1, 2),
            )
            report = json.loads(output.read_text())

        self.assertEqual(report["report_scope"]["start_date"], "2026-01-02")
        self.assertEqual(
            report["report_scope"]["market_storage_measurement"],
            "estimated_by_table_row_share_of_active_parts",
        )
        self.assertEqual(report["market_parts"][0]["scope_row_fraction"], 0.1)
        self.assertEqual(report["bytes_on_disk"], 1_000)
        self.assertEqual(report["global_active_parts"]["bytes_on_disk"], 10_000)
        filtered_queries = [statement for statement, _parameters in client.queries if "BETWEEN" in statement]
        overlap_queries = [
            statement for statement, _parameters in client.queries
            if "toDate(ifNull(ended_at" in statement
        ]
        self.assertEqual(len(filtered_queries), 2)
        self.assertEqual(len(overlap_queries), 2)
        for _statement, parameters in client.queries:
            if parameters:
                self.assertEqual(parameters["start_date"], date(2026, 1, 2))
                self.assertEqual(parameters["end_date"], date(2026, 1, 2))

    def test_date_scope_requires_a_complete_ordered_range(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "pilot.json"
            with self.assertRaisesRegex(ValueError, "provided together"):
                collect_report("clickhouse", output, start_date=date(2026, 1, 2))
            with self.assertRaisesRegex(ValueError, "on or before"):
                collect_report(
                    "clickhouse", output,
                    start_date=date(2026, 1, 3), end_date=date(2026, 1, 2),
                )
            with self.assertRaisesRegex(ValueError, "positive"):
                collect_report("clickhouse", output, storage_total=0)

    def test_empty_dataset_does_not_invent_retention(self):
        class EmptyClient(FakeClient):
            def query(self, statement, parameters=None):
                result = super().query(statement, parameters)
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

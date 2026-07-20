import tempfile
import unittest
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from lob_recorder import exporter
from lob_recorder.exporter import export_clickhouse, partition_path


class ExporterTests(unittest.TestCase):
    def test_rejects_path_traversal_symbol_before_connecting(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                export_clickhouse("unavailable", "../../private", "2026-01-02", folder)

    def test_partition_path_uses_complete_market_identity(self):
        target = partition_path(
            "/exports", "stk", "tse", "2330", "2026-01-02"
        )
        self.assertEqual(
            target,
            Path(
                "/exports/security_type=STK/exchange=TSE/"
                "symbol=2330/trading_date=2026-01-02"
            ),
        )

    def test_symbol_discovery_keeps_same_code_in_separate_markets(self):
        class FakeClient:
            def query(self, _statement, parameters=None):
                self.parameters = parameters
                return SimpleNamespace(result_rows=[
                    ("STK", "TSE", "SAME"),
                    ("FUT", "TAIFEX", "SAME"),
                ])

        client = FakeClient()
        exported = []
        with patch.object(exporter, "_client", return_value=client), patch.object(
            exporter,
            "_export_identity",
            side_effect=lambda _client, identity, _day, _output: exported.append(identity)
            or Path("/safe"),
        ):
            paths = export_clickhouse(
                "clickhouse", "SAME", "2026-01-02", "/exports"
            )

        self.assertEqual(len(paths), 2)
        self.assertEqual(exported, [
            ("STK", "TSE", "SAME"),
            ("FUT", "TAIFEX", "SAME"),
        ])
        self.assertEqual(client.parameters, {"day": "2026-01-02", "symbol": "SAME"})

    def test_requires_security_type_and_exchange_together(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "provided together"):
                export_clickhouse(
                    "unavailable",
                    "2330",
                    "2026-01-02",
                    folder,
                    security_type="STK",
                )

    def test_rejects_unsafe_exchange_before_connecting(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "exchange"):
                export_clickhouse(
                    "unavailable",
                    "2330",
                    "2026-01-02",
                    folder,
                    security_type="STK",
                    exchange="../../private",
                )

    def test_export_query_filters_and_writes_complete_identity_partition(self):
        class FakeClient:
            def __init__(self):
                self.queries = []

            def query(self, statement, parameters=None):
                self.queries.append((statement, parameters))
                if "lob_events" in statement:
                    return SimpleNamespace(
                        column_names=["security_type", "exchange", "symbol"],
                        result_rows=[("STK", "TSE", "2330")],
                    )
                return SimpleNamespace(column_names=[], result_rows=[])

        class FakeTable:
            @staticmethod
            def from_pylist(rows):
                return rows

        fake_pa = ModuleType("pyarrow")
        fake_pa.Table = FakeTable
        fake_pq = ModuleType("pyarrow.parquet")
        fake_pq.write_table = (
            lambda _table, destination, compression: Path(destination).write_bytes(
                compression.encode("ascii")
            )
        )
        fake_pa.parquet = fake_pq
        client = FakeClient()
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            sys.modules, {"pyarrow": fake_pa, "pyarrow.parquet": fake_pq}
        ):
            target = exporter._export_identity(
                client, ("STK", "TSE", "2330"), "2026-01-02", folder
            )

            self.assertTrue((target / "lob_events.parquet").is_file())
            self.assertFalse((target / "tick_events.parquet").exists())

        self.assertEqual(len(client.queries), 2)
        for statement, parameters in client.queries:
            self.assertIn("security_type = {security_type:String}", statement)
            self.assertIn("exchange = {exchange:String}", statement)
            self.assertEqual(parameters, {
                "security_type": "STK",
                "exchange": "TSE",
                "symbol": "2330",
                "day": "2026-01-02",
            })

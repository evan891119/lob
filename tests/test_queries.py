import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class QueryExamplesTests(unittest.TestCase):
    def test_duckdb_example_reads_both_stream_files_by_complete_identity(self):
        query = (ROOT / "queries" / "duckdb_parquet.sql").read_text()

        self.assertIn(
            "security_type=*/exchange=*/symbol=*/trading_date=*/*.parquet",
            query,
        )
        self.assertIn("lob_events.parquet", query)
        self.assertIn("tick_events.parquet", query)
        self.assertIn("union_by_name = true", query)
        self.assertIn("hive_partitioning = true", query)
        self.assertIn("filename = true", query)
        self.assertIn("WHEN filename LIKE '%/lob_events.parquet' THEN 'bidask'", query)
        self.assertIn("WHEN filename LIKE '%/tick_events.parquet' THEN 'tick'", query)

    def test_duckdb_example_scopes_identity_date_and_time(self):
        query = (ROOT / "queries" / "duckdb_parquet.sql").read_text()

        for predicate in (
            "security_type = 'STK'",
            "exchange = 'TSE'",
            "symbol = '2330'",
            "trading_date = DATE '2026-01-02'",
            "event_ts >= TIMESTAMPTZ",
            "event_ts < TIMESTAMPTZ",
        ):
            self.assertIn(predicate, query)


if __name__ == "__main__":
    unittest.main()

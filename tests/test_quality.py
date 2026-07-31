import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stdout
from unittest.mock import patch

from lob_recorder.cli import command_quality
from lob_recorder.quality import inspect, inspect_parquet


class QualityTests(unittest.TestCase):
    def test_detects_crossed_and_negative(self):
        result = inspect([{"stream": "bidask", "session_id": "s", "sequence_no": 1, "symbol": "x",
                           "event_ts": "2", "bid_price_1": 11, "ask_price_1": 10, "bid_volume_1": -1}])
        self.assertEqual(result["crossed_book"], 1)
        self.assertEqual(result["negative_volume"], 1)

    def test_nullable_book_columns_are_ignored(self):
        result = inspect([{
            "stream": "bidask", "session_id": "s", "sequence_no": 1,
            "symbol": "x", "event_ts": "2026-01-02T09:00:00+08:00",
            "bid_price_1": None, "ask_price_1": None,
        }])
        self.assertEqual(result["crossed_book"], 0)

    def test_one_sided_book_is_not_crossed(self):
        result = inspect([{
            "stream": "bidask", "session_id": "s", "sequence_no": 1,
            "symbol": "x", "event_ts": "2026-01-02T09:00:00+08:00",
            "bid_price_1": 100, "ask_price_1": 0,
        }])
        self.assertEqual(result["crossed_book"], 0)

    def test_detects_sequence_gap(self):
        result = inspect([
            {"stream": "tick", "session_id": "s", "sequence_no": 1, "symbol": "x", "event_ts": "1"},
            {"stream": "tick", "session_id": "s", "sequence_no": 3, "symbol": "x", "event_ts": "3"},
        ], sequence_scope_complete=True)
        self.assertEqual(result["sequence_gaps"], 1)
        self.assertTrue(result["sequence_scope_complete"])

    def test_partial_scope_does_not_invent_sequence_gaps(self):
        result = inspect([
            {"stream": "tick", "session_id": "s", "sequence_no": 1, "symbol": "x", "event_ts": "1"},
            {"stream": "tick", "session_id": "s", "sequence_no": 3, "symbol": "x", "event_ts": "3"},
        ])
        self.assertIsNone(result["sequence_gaps"])
        self.assertFalse(result["sequence_scope_complete"])

    def test_interleaved_streams_do_not_create_false_sequence_gap(self):
        result = inspect([
            {"stream": "bidask", "session_id": "s", "sequence_no": 1, "symbol": "x", "event_ts": "2026-01-02T09:00:00+08:00"},
            {"stream": "tick", "session_id": "s", "sequence_no": 2, "symbol": "x", "event_ts": "2026-01-02T09:00:01+08:00"},
            {"stream": "bidask", "session_id": "s", "sequence_no": 3, "symbol": "x", "event_ts": "2026-01-02T09:00:02+08:00"},
        ], sequence_scope_complete=True)
        self.assertEqual(result["sequence_gaps"], 0)

    def test_same_symbol_in_different_markets_does_not_mix_time_order(self):
        result = inspect([
            {
                "stream": "tick", "session_id": "s", "sequence_no": 1,
                "security_type": "STK", "exchange": "TSE", "symbol": "SAME",
                "event_ts": "2026-01-02T10:00:00+08:00",
            },
            {
                "stream": "tick", "session_id": "s", "sequence_no": 2,
                "security_type": "FUT", "exchange": "TAIFEX", "symbol": "SAME",
                "event_ts": "2026-01-02T09:00:00+08:00",
            },
            {
                "stream": "bidask", "session_id": "s", "sequence_no": 3,
                "security_type": "STK", "exchange": "TSE", "symbol": "SAME",
                "event_ts": "2026-01-02T10:00:01+08:00",
            },
            {
                "stream": "bidask", "session_id": "s", "sequence_no": 4,
                "security_type": "FUT", "exchange": "TAIFEX", "symbol": "SAME",
                "event_ts": "2026-01-02T09:00:01+08:00",
            },
        ], sequence_scope_complete=True)

        self.assertEqual(result["out_of_order"], 0)
        self.assertEqual(result["market_identities"], 2)
        self.assertTrue(result["all_identities_have_both_streams"])
        self.assertTrue(result["complete_scope_quality_passed"])

    def test_same_session_sequence_across_streams_is_duplicate(self):
        result = inspect([
            {"stream": "bidask", "session_id": "s", "sequence_no": 1, "symbol": "x", "event_ts": "2026-01-02T09:00:00+08:00"},
            {"stream": "tick", "session_id": "s", "sequence_no": 1, "symbol": "x", "event_ts": "2026-01-02T09:00:01+08:00"},
        ])
        self.assertEqual(result["duplicates"], 1)

    def test_detects_time_gap_with_explicit_threshold(self):
        result = inspect([
            {"stream": "tick", "session_id": "s", "sequence_no": 1, "symbol": "x", "event_ts": "2026-01-02T09:00:00+08:00"},
            {"stream": "tick", "session_id": "s", "sequence_no": 2, "symbol": "x", "event_ts": "2026-01-02T09:02:00+08:00"},
        ], max_gap_seconds=60)
        self.assertEqual(result["time_gaps"], 1)

    def test_mixed_naive_and_aware_timestamps_fail_as_invalid_not_exception(self):
        result = inspect([
            {
                "stream": "tick", "session_id": "s", "sequence_no": 1,
                "security_type": "STK", "exchange": "TSE", "symbol": "2330",
                "event_ts": "2026-01-02T09:00:00",
            },
            {
                "stream": "tick", "session_id": "s", "sequence_no": 2,
                "security_type": "STK", "exchange": "TSE", "symbol": "2330",
                "event_ts": "2026-01-02T09:00:01+08:00",
            },
        ], sequence_scope_complete=True)

        self.assertEqual(result["invalid_timestamp"], 1)
        self.assertFalse(result["complete_scope_quality_passed"])

    def test_parquet_glob_supports_recursive_partition_pattern(self):
        with patch("glob.glob", return_value=[]) as matched:
            with self.assertRaises(ValueError):
                inspect_parquet("root/**/*.parquet")
        matched.assert_called_once_with("root/**/*.parquet", recursive=True)

    def test_complete_scope_requires_both_streams_for_every_market_identity(self):
        only_tick = inspect([{
            "stream": "tick", "session_id": "s", "sequence_no": 1,
            "security_type": "STK", "exchange": "TSE", "symbol": "2330",
            "event_ts": "2026-01-02T09:00:00+08:00",
        }], sequence_scope_complete=True)

        self.assertFalse(only_tick["all_identities_have_both_streams"])
        self.assertFalse(only_tick["complete_scope_quality_passed"])

    def test_complete_scope_rejects_missing_market_identity(self):
        result = inspect([
            {
                "stream": "bidask", "session_id": "s", "sequence_no": 1,
                "symbol": "2330", "event_ts": "2026-01-02T09:00:00+08:00",
            },
            {
                "stream": "tick", "session_id": "s", "sequence_no": 2,
                "symbol": "2330", "event_ts": "2026-01-02T09:00:01+08:00",
            },
        ], sequence_scope_complete=True)

        self.assertEqual(result["invalid_market_identity"], 2)
        self.assertFalse(result["complete_scope_quality_passed"])

    def test_cli_writes_report_before_failing_incomplete_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_path = root / "events.jsonl"
            output_path = root / "quality.json"
            input_path.write_text(json.dumps({
                "stream": "tick", "session_id": "s", "sequence_no": 1,
                "security_type": "STK", "exchange": "TSE", "symbol": "2330",
                "event_ts": "2026-01-02T09:00:00+08:00",
            }) + "\n")
            args = SimpleNamespace(
                input=str(input_path),
                parquet=None,
                max_gap_seconds=60,
                complete_sequence_scope=True,
                output=str(output_path),
                require_complete_clean=True,
            )
            with redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "verification failed"):
                    command_quality(args)
            report = json.loads(output_path.read_text())

        self.assertFalse(report["complete_scope_quality_passed"])

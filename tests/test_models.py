from datetime import datetime, timezone
import unittest

from lob_recorder.models import normalize
from lob_recorder.sinks import ClickHouseSink


class ModelTests(unittest.TestCase):
    def test_bidask_is_flattened_to_five_levels(self):
        event = normalize({
            "stream": "bidask", "exchange": "TSE", "security_type": "STK", "symbol": "2330",
            "event_ts": "2026-01-02T09:00:00+08:00", "bid_price": [10, 9], "bid_volume": [1, 2],
            "ask_price": [11], "ask_volume": [3], "diff_bid_vol": [], "diff_ask_vol": [],
        }, "00000000-0000-0000-0000-000000000001", 1, datetime.now(timezone.utc)).to_record()
        self.assertEqual(event["bid_price_1"], 10)
        self.assertEqual(event["bid_price_5"], 0.0)
        self.assertNotIn("payload", event)

    def test_rejects_unknown_stream(self):
        with self.assertRaises(ValueError):
            normalize({"stream": "other", "symbol": "x", "event_ts": "2026-01-01T00:00:00+08:00"}, "s", 1)

    def test_clickhouse_temporal_values_are_typed(self):
        self.assertEqual(ClickHouseSink._value("trading_date", "2026-01-02").isoformat(), "2026-01-02")
        self.assertEqual(ClickHouseSink._value("event_ts", "2026-01-02T09:00:00+08:00").utcoffset().total_seconds(), 28800)

    def test_event_timestamp_is_normalized_to_taipei(self):
        event = normalize({"stream": "tick", "symbol": "x", "event_ts": "2026-01-02T01:00:00+00:00"}, "s", 1)
        self.assertEqual(event.event_ts, "2026-01-02T09:00:00+08:00")
        self.assertEqual(event.trading_date, "2026-01-02")

    def test_rejects_invalid_timestamp(self):
        with self.assertRaises(ValueError):
            normalize({"stream": "tick", "symbol": "x", "event_ts": "not-a-time"}, "s", 1)

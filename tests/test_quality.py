import unittest

from lob_recorder.quality import inspect


class QualityTests(unittest.TestCase):
    def test_detects_crossed_and_negative(self):
        result = inspect([{"stream": "bidask", "session_id": "s", "sequence_no": 1, "symbol": "x",
                           "event_ts": "2", "bid_price_1": 11, "ask_price_1": 10, "bid_volume_1": -1}])
        self.assertEqual(result["crossed_book"], 1)
        self.assertEqual(result["negative_volume"], 1)

    def test_detects_sequence_gap(self):
        result = inspect([
            {"stream": "tick", "session_id": "s", "sequence_no": 1, "symbol": "x", "event_ts": "1"},
            {"stream": "tick", "session_id": "s", "sequence_no": 3, "symbol": "x", "event_ts": "3"},
        ])
        self.assertEqual(result["sequence_gaps"], 1)

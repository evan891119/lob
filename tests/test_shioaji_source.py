import unittest
from types import SimpleNamespace

from lob_recorder.config import Instrument
from lob_recorder.sources.shioaji_source import ShioajiSource


class ShioajiSourceTests(unittest.TestCase):
    def test_tick_is_enriched_from_latest_bidask_without_io(self):
        events = []
        source = ShioajiSource(("fake", "fake"), [Instrument("2330", "STK", "TSE", ("bidask", "tick"))], events.append)
        source._stock_bidask(SimpleNamespace(
            code="2330", datetime="2026-01-02T09:00:00+08:00",
            bid_price=[100, 99, 98, 97, 96], bid_volume=[1, 2, 3, 4, 5],
            ask_price=[101, 102, 103, 104, 105], ask_volume=[6, 7, 8, 9, 10],
            diff_bid_vol=[0] * 5, diff_ask_vol=[0] * 5, simtrade=True, intraday_odd=False,
        ))
        source._stock_tick(SimpleNamespace(
            code="2330", datetime="2026-01-02T09:00:01+08:00", close=101,
            volume=1, total_volume=1, tick_type=1, simtrade=True, intraday_odd=False,
        ))
        self.assertEqual(events[1]["best_bid_price"], 100)
        self.assertEqual(events[1]["best_ask_volume"], 6)

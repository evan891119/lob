import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_callbacks_precede_subscribe_and_partial_failure_is_reported(self):
        actions = []
        system_codes = []
        contract = SimpleNamespace(code="2330", security_type="STK", exchange="TSE", target_code=None)

        class FakeApi:
            def __init__(self, simulation):
                self.contracts = SimpleNamespace(get=lambda code: contract if code == "2330" else None)

            def login(self, **_kwargs): actions.append("login")
            def set_on_bidask_stk_v1_callback(self, _cb): actions.append("callback")
            def set_on_tick_stk_v1_callback(self, _cb): actions.append("callback")
            def set_on_bidask_fop_v1_callback(self, _cb): actions.append("callback")
            def set_on_tick_fop_v1_callback(self, _cb): actions.append("callback")
            def set_session_down_callback(self, _cb): actions.append("down_callback")
            def set_event_callback(self, cb): self.event_callback = cb; actions.append("event_callback")
            def subscribe(self, _contract, quote_type, intraday_odd):
                actions.append(f"subscribe:{quote_type}")
                if quote_type == "tick":
                    raise RuntimeError("private upstream message")
            def unsubscribe(self, *_args, **_kwargs): return None
            def logout(self): actions.append("logout")

        fake_module = SimpleNamespace(
            Shioaji=FakeApi,
            QuoteType=SimpleNamespace(BidAsk="bidask", Tick="tick"),
        )
        source = ShioajiSource(
            ("fake", "fake"),
            [Instrument("2330", "STK", "TSE", ("bidask", "tick"))],
            lambda _event: None,
            on_event=system_codes.append,
        )
        runtime = {"SJ_HOME_PATH": "/tmp/a", "SJ_CONTRACTS_PATH": "/tmp/b", "SJ_LOG_PATH": "/tmp/c"}
        with patch.dict("os.environ", runtime, clear=False), patch.dict(sys.modules, {"shioaji": fake_module}):
            results = source.connect()
            source.api.event_callback(0, 13, "ignored-private-info", "ignored-private-event")
            source.api.event_callback(SimpleNamespace(event_code=12, info="ignored-private-info"))
            source.close()
        first_subscribe = next(index for index, action in enumerate(actions) if action.startswith("subscribe:"))
        self.assertGreaterEqual(actions[:first_subscribe].count("callback"), 4)
        self.assertEqual([result.active for result in results], [True, False])
        self.assertEqual(results[1].category, "RuntimeError")
        self.assertEqual(system_codes, [13, 12])
        self.assertEqual(results[0].descriptor(), "STK:TSE:2330:bidask:2330:subscribed")

    def test_contract_metadata_mismatch_is_rejected(self):
        source = ShioajiSource(("fake", "fake"), [Instrument("2330", "STK", "TSE", ("tick",))], lambda _event: None)
        bad = SimpleNamespace(code="2330", security_type="FUT", exchange="TAIFEX")
        source.api = SimpleNamespace(contracts=SimpleNamespace(get=lambda _code: bad))
        with self.assertRaises(ValueError):
            source._resolve(source.instruments[0])

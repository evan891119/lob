import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch

from lob_recorder.config import Instrument
from lob_recorder.models import normalize
from lob_recorder.sources.shioaji_source import (
    ContractLookupError,
    ShioajiLoginError,
    ShioajiSource,
    SubscriptionResult,
)


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

    def test_shioaji_15_uppercase_contract_facade_is_supported(self):
        contract = SimpleNamespace(code="2330", security_type="STK", exchange="TSE")
        stocks = SimpleNamespace(get=lambda code: contract if code == "2330" else None)
        source = ShioajiSource(("fake", "fake"), [Instrument("2330", "STK", "TSE", ("tick",))], lambda _event: None)
        source.api = SimpleNamespace(Contracts=SimpleNamespace(Stocks=stocks))

        self.assertIs(source._resolve(source.instruments[0]), contract)

    def test_legacy_contract_lookup_can_use_exchange_group(self):
        contract = SimpleNamespace(code="2330", security_type="STK", exchange="TSE")
        tse = SimpleNamespace(get=lambda code: contract if code == "2330" else None)
        stocks = SimpleNamespace(get=lambda key: tse if key == "TSE" else None)
        source = ShioajiSource(("fake", "fake"), [Instrument("2330", "STK", "TSE", ("tick",))], lambda _event: None)
        source.api = SimpleNamespace(Contracts=SimpleNamespace(Stocks=stocks))

        self.assertIs(source._resolve(source.instruments[0]), contract)

    def test_missing_contract_facades_uses_safe_error_category(self):
        source = ShioajiSource(("fake", "fake"), [Instrument("2330", "STK", "TSE", ("tick",))], lambda _event: None)
        source.api = SimpleNamespace()

        with self.assertRaises(ContractLookupError) as caught:
            source._resolve(source.instruments[0])
        self.assertNotIn("fake", str(caught.exception))

    def test_login_failure_uses_safe_error_category(self):
        class FakeApi:
            def __init__(self, simulation):
                self.simulation = simulation

            def login(self, **_kwargs):
                raise RuntimeError("private upstream login response")

        fake_module = SimpleNamespace(Shioaji=FakeApi)
        source = ShioajiSource(("fake-key", "fake-secret"), [Instrument("2330", "STK", "TSE", ("tick",))], lambda _event: None)
        runtime = {"SJ_HOME_PATH": "/tmp/a", "SJ_CONTRACTS_PATH": "/tmp/b", "SJ_LOG_PATH": "/tmp/c"}
        with patch.dict("os.environ", runtime, clear=False), patch.dict(sys.modules, {"shioaji": fake_module}):
            with self.assertRaises(ShioajiLoginError) as caught:
                source.connect()
        self.assertNotIn("private upstream", str(caught.exception))

    def test_all_failed_subscriptions_are_returned_for_safe_diagnostics(self):
        contract = SimpleNamespace(code="2330", security_type="STK", exchange="TSE", target_code=None)

        class FakeApi:
            def __init__(self, simulation):
                self.contracts = SimpleNamespace(get=lambda _code: contract)

            def login(self, **_kwargs): return None
            def set_on_bidask_stk_v1_callback(self, _cb): return None
            def set_on_tick_stk_v1_callback(self, _cb): return None
            def set_on_bidask_fop_v1_callback(self, _cb): return None
            def set_on_tick_fop_v1_callback(self, _cb): return None
            def set_session_down_callback(self, _cb): return None
            def set_event_callback(self, _cb): return None
            def subscribe(self, *_args, **_kwargs): raise RuntimeError("private upstream message")

        fake_module = SimpleNamespace(Shioaji=FakeApi, QuoteType=SimpleNamespace(BidAsk="bidask", Tick="tick"))
        source = ShioajiSource(("fake", "fake"), [Instrument("2330", "STK", "TSE", ("bidask", "tick"))], lambda _event: None)
        runtime = {"SJ_HOME_PATH": "/tmp/a", "SJ_CONTRACTS_PATH": "/tmp/b", "SJ_LOG_PATH": "/tmp/c"}
        with patch.dict("os.environ", runtime, clear=False), patch.dict(sys.modules, {"shioaji": fake_module}):
            results = source.connect()
        self.assertEqual([result.active for result in results], [False, False])
        self.assertEqual([result.category for result in results], ["RuntimeError", "RuntimeError"])

    def test_continuous_future_callbacks_use_target_metadata_and_optional_diffs(self):
        events = []
        instrument = Instrument("TXFR1", "FUT", "TAIFEX", ("bidask", "tick"))
        contract = SimpleNamespace(
            code="TXFR1", security_type="FUT", exchange="TAIFEX", target_code="TXF_TEST"
        )
        source = ShioajiSource(("fake", "fake"), [instrument], events.append)
        source._register_contract_metadata(instrument, contract)

        source._fop_bidask(SimpleNamespace(
            code="TXF_TEST", datetime="2026-01-02T09:00:00+08:00",
            bid_price=[20000, 19999], bid_volume=[3, 4],
            ask_price=[20001, 20002], ask_volume=[5, 6], simtrade=True,
        ))
        source._fop_tick(SimpleNamespace(
            code="TXF_TEST", datetime="2026-01-02T09:00:01+08:00",
            close=20001, volume=1, total_volume=10, tick_type=1, simtrade=True,
        ))

        self.assertEqual(events[0]["exchange"], "TAIFEX")
        self.assertEqual(events[0]["security_type"], "FUT")
        self.assertEqual(events[0]["symbol"], "TXF_TEST")
        self.assertEqual(events[0]["diff_bid_vol"], [])
        self.assertEqual(events[0]["diff_ask_vol"], [])
        self.assertEqual(events[1]["best_bid_price"], 20000)
        self.assertEqual(events[1]["best_ask_volume"], 5)
        record = normalize(
            events[0], "00000000-0000-0000-0000-000000000001", 1
        ).to_record()
        self.assertEqual(record["diff_bid_vol_1"], 0)
        self.assertEqual(record["diff_ask_vol_5"], 0)
        result = SubscriptionResult(
            "TXFR1", "bidask", True, "subscribed", "TXFR1", "TXF_TEST", "TAIFEX", "FUT"
        )
        self.assertEqual(
            result.descriptor(), "FUT:TAIFEX:TXFR1:bidask:TXF_TEST:subscribed"
        )

    def test_option_callback_uses_configured_security_type(self):
        events = []
        instrument = Instrument("TXO_TEST", "OPT", "TAIFEX", ("tick",))
        contract = SimpleNamespace(
            code="TXO_TEST", security_type="OPT", exchange="TAIFEX", target_code=None
        )
        source = ShioajiSource(("fake", "fake"), [instrument], events.append)
        source._register_contract_metadata(instrument, contract)

        source._fop_tick(SimpleNamespace(
            code="TXO_TEST", datetime="2026-01-02T09:00:01+08:00",
            close=100, volume=1, total_volume=2, tick_type=2, simtrade=True,
        ))

        self.assertEqual(events[0]["exchange"], "TAIFEX")
        self.assertEqual(events[0]["security_type"], "OPT")

    def test_conflicting_resolved_identifier_is_rejected(self):
        stock = Instrument("2330", "STK", "TSE", ("tick",))
        future = Instrument("TXFR1", "FUT", "TAIFEX", ("tick",))
        source = ShioajiSource(("fake", "fake"), [stock, future], lambda _event: None)
        contract = SimpleNamespace(code="TXFR1", target_code="2330")

        with self.assertRaises(ValueError):
            source._register_contract_metadata(future, contract)

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from lob_recorder.config import Instrument


@dataclass(slots=True)
class SubscriptionResult:
    code: str
    stream: str
    active: bool
    category: str


class ShioajiSource:
    """One in-process Shioaji connection; callbacks only normalize and enqueue."""

    def __init__(self, credentials: tuple[str, str], instruments: list[Instrument], emit: Callable[[dict], None], on_disconnect: Callable[[], None] | None = None):
        self.credentials = credentials
        self.instruments = instruments
        self.emit = emit
        self.on_disconnect = on_disconnect or (lambda: None)
        self.api = None
        self.contracts: list[tuple[Instrument, object]] = []
        self.metadata = {item.code: (item.exchange, item.security_type) for item in instruments}
        self.books: dict[str, tuple[float, int, float, int]] = {}

    def connect(self) -> list[SubscriptionResult]:
        # Runtime paths must be exported by the entrypoint before this delayed import.
        for required in ("SJ_HOME_PATH", "SJ_CONTRACTS_PATH", "SJ_LOG_PATH"):
            if not os.environ.get(required):
                raise RuntimeError("Shioaji runtime paths are not isolated")
        import shioaji as sj

        self.api = sj.Shioaji(simulation=True)
        self.api.login(api_key=self.credentials[0], secret_key=self.credentials[1])
        self.api.set_on_bidask_stk_v1_callback(self._stock_bidask)
        self.api.set_on_tick_stk_v1_callback(self._stock_tick)
        self.api.set_on_bidask_fop_v1_callback(self._fop_bidask)
        self.api.set_on_tick_fop_v1_callback(self._fop_tick)
        self.api.set_session_down_callback(self.on_disconnect)
        results: list[SubscriptionResult] = []
        for instrument in self.instruments:
            contract = self._resolve(instrument)
            self.contracts.append((instrument, contract))
            for stream in instrument.streams:
                quote_type = sj.QuoteType.BidAsk if stream == "bidask" else sj.QuoteType.Tick
                self.api.subscribe(contract, quote_type=quote_type, intraday_odd=instrument.intraday_odd)
                results.append(SubscriptionResult(instrument.code, stream, True, "subscribed"))
        return results

    def close(self) -> None:
        if self.api is None:
            return
        try:
            import shioaji as sj
            for instrument, contract in self.contracts:
                for stream in instrument.streams:
                    quote_type = sj.QuoteType.BidAsk if stream == "bidask" else sj.QuoteType.Tick
                    self.api.unsubscribe(contract, quote_type=quote_type, intraday_odd=instrument.intraday_odd)
        finally:
            try:
                self.api.logout()
            except Exception:
                pass

    def _resolve(self, item: Instrument):
        contract = self.api.contracts.get(item.code)
        if contract is None:
            raise ValueError("contract was not resolved")
        return contract

    @staticmethod
    def _values(value):
        return [float(v) if isinstance(v, Decimal) else v for v in value]

    def _bidask(self, bidask, fallback_type):
        exchange, security_type = self.metadata.get(bidask.code, ("", fallback_type))
        bid_price = self._values(bidask.bid_price)
        bid_volume = list(bidask.bid_volume)
        ask_price = self._values(bidask.ask_price)
        ask_volume = list(bidask.ask_volume)
        self.books[bidask.code] = (
            bid_price[0] if bid_price else 0, bid_volume[0] if bid_volume else 0,
            ask_price[0] if ask_price else 0, ask_volume[0] if ask_volume else 0,
        )
        self.emit({"stream": "bidask", "exchange": exchange, "security_type": security_type,
                   "symbol": bidask.code, "event_ts": bidask.datetime,
                   "bid_price": bid_price, "bid_volume": bid_volume,
                   "ask_price": ask_price, "ask_volume": ask_volume,
                   "diff_bid_vol": list(bidask.diff_bid_vol), "diff_ask_vol": list(bidask.diff_ask_vol),
                   "simtrade": bool(bidask.simtrade), "intraday_odd": bool(getattr(bidask, "intraday_odd", False))})

    def _tick(self, tick, fallback_type):
        exchange, security_type = self.metadata.get(tick.code, ("", fallback_type))
        best_bid_price, best_bid_volume, best_ask_price, best_ask_volume = self.books.get(tick.code, (0, 0, 0, 0))
        self.emit({"stream": "tick", "exchange": exchange, "security_type": security_type,
                   "symbol": tick.code, "event_ts": tick.datetime, "close": tick.close,
                   "volume": tick.volume, "total_volume": tick.total_volume, "tick_type": tick.tick_type,
                   "best_bid_price": best_bid_price, "best_bid_volume": best_bid_volume,
                   "best_ask_price": best_ask_price, "best_ask_volume": best_ask_volume,
                   "simtrade": bool(tick.simtrade), "intraday_odd": bool(getattr(tick, "intraday_odd", False))})

    def _stock_bidask(self, bidask): self._bidask(bidask, "STK")
    def _stock_tick(self, tick): self._tick(tick, "STK")
    def _fop_bidask(self, bidask): self._bidask(bidask, "FOP")
    def _fop_tick(self, tick): self._tick(tick, "FOP")

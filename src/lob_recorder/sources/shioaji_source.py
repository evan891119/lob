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
    resolved_code: str = ""
    target_code: str = ""
    exchange: str = ""
    security_type: str = ""

    def descriptor(self) -> str:
        resolved = self.resolved_code or self.code
        return ":".join((self.security_type, self.exchange, self.code, self.stream, resolved, self.category))


class ShioajiSource:
    """One in-process Shioaji connection; callbacks only normalize and enqueue."""

    def __init__(
        self,
        credentials: tuple[str, str],
        instruments: list[Instrument],
        emit: Callable[[dict], None],
        on_disconnect: Callable[[], None] | None = None,
        on_event: Callable[[int], None] | None = None,
    ):
        self.credentials = credentials
        self.instruments = instruments
        self.emit = emit
        self.on_disconnect = on_disconnect or (lambda: None)
        self.on_event = on_event or (lambda _code: None)
        self.api = None
        self.contracts: list[tuple[Instrument, object]] = []
        self.active_subscriptions: list[tuple[Instrument, object, str]] = []
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
        self.api.set_event_callback(self._system_event)
        results: list[SubscriptionResult] = []
        for instrument in self.instruments:
            try:
                contract = self._resolve(instrument)
                self.contracts.append((instrument, contract))
            except Exception as exc:
                results.extend(
                    SubscriptionResult(
                        instrument.code, stream, False, type(exc).__name__,
                        exchange=instrument.exchange, security_type=instrument.security_type,
                    )
                    for stream in instrument.streams
                )
                continue
            for stream in instrument.streams:
                quote_type = sj.QuoteType.BidAsk if stream == "bidask" else sj.QuoteType.Tick
                try:
                    self.api.subscribe(contract, quote_type=quote_type, intraday_odd=instrument.intraday_odd)
                    self.active_subscriptions.append((instrument, contract, stream))
                    results.append(SubscriptionResult(
                        instrument.code,
                        stream,
                        True,
                        "subscribed",
                        resolved_code=str(getattr(contract, "code", instrument.code)),
                        target_code=str(getattr(contract, "target_code", "") or ""),
                        exchange=instrument.exchange,
                        security_type=instrument.security_type,
                    ))
                except Exception as exc:
                    results.append(SubscriptionResult(
                        instrument.code, stream, False, type(exc).__name__,
                        exchange=instrument.exchange, security_type=instrument.security_type,
                    ))
        if not any(result.active for result in results):
            raise RuntimeError("no market-data subscription became active")
        return results

    def close(self) -> None:
        if self.api is None:
            return
        try:
            import shioaji as sj
            for instrument, contract, stream in self.active_subscriptions:
                quote_type = sj.QuoteType.BidAsk if stream == "bidask" else sj.QuoteType.Tick
                try:
                    self.api.unsubscribe(contract, quote_type=quote_type, intraday_odd=instrument.intraday_odd)
                except Exception:
                    pass
        finally:
            try:
                self.api.logout()
            except Exception:
                pass

    def _resolve(self, item: Instrument):
        contract = self.api.contracts.get(item.code)
        if contract is None:
            raise ValueError("contract was not resolved")
        resolved_code = str(getattr(contract, "code", ""))
        if resolved_code and resolved_code != item.code:
            raise ValueError("resolved contract code does not match")
        resolved_type = self._enum_value(getattr(contract, "security_type", ""))
        if resolved_type and resolved_type != item.security_type:
            raise ValueError("resolved security type does not match")
        resolved_exchange = self._enum_value(getattr(contract, "exchange", ""))
        if resolved_exchange and resolved_exchange != item.exchange:
            raise ValueError("resolved exchange does not match")
        return contract

    @staticmethod
    def _enum_value(value) -> str:
        raw = getattr(value, "value", value)
        return str(raw).upper()

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
                   "simtrade": bool(getattr(bidask, "simtrade", True)), "intraday_odd": bool(getattr(bidask, "intraday_odd", False))})

    def _tick(self, tick, fallback_type):
        exchange, security_type = self.metadata.get(tick.code, ("", fallback_type))
        best_bid_price, best_bid_volume, best_ask_price, best_ask_volume = self.books.get(tick.code, (0, 0, 0, 0))
        self.emit({"stream": "tick", "exchange": exchange, "security_type": security_type,
                   "symbol": tick.code, "event_ts": tick.datetime, "close": tick.close,
                   "volume": tick.volume, "total_volume": tick.total_volume, "tick_type": getattr(tick, "tick_type", 0),
                   "best_bid_price": best_bid_price, "best_bid_volume": best_bid_volume,
                   "best_ask_price": best_ask_price, "best_ask_volume": best_ask_volume,
                   "simtrade": bool(getattr(tick, "simtrade", True)), "intraday_odd": bool(getattr(tick, "intraday_odd", False))})

    def _system_event(self, *args) -> None:
        # Shioaji versions expose either the four-field callback or one event object.
        if len(args) >= 2:
            event_code = args[1]
        elif len(args) == 1:
            event_code = getattr(args[0], "event_code", args[0])
        else:
            return
        self.on_event(int(event_code))

    def _stock_bidask(self, bidask): self._bidask(bidask, "STK")
    def _stock_tick(self, tick): self._tick(tick, "STK")
    def _fop_bidask(self, bidask): self._bidask(bidask, "FOP")
    def _fop_tick(self, tick): self._tick(tick, "FOP")

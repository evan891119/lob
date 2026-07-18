from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

TAIPEI_OFFSET = "+08:00"
TAIPEI = ZoneInfo("Asia/Taipei")


def _number(value: Any, default: int | float = 0) -> int | float:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return float(value)
    return value


def _five(values: Any, default: int | float = 0) -> list[int | float]:
    result = [_number(v, default) for v in list(values or [])[:5]]
    return result + [default] * (5 - len(result))


def parse_event_ts(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value)
        if not text:
            raise ValueError("event timestamp is required")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("event timestamp is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI)
    else:
        parsed = parsed.astimezone(TAIPEI)
    if parsed.utcoffset() is None:
        raise ValueError("event timestamp is required")
    return parsed.isoformat()


@dataclass(slots=True)
class MarketEvent:
    stream: str
    exchange: str
    security_type: str
    symbol: str
    event_ts: str
    received_ts: str
    trading_date: str
    session_id: str
    sequence_no: int
    simtrade: bool = True
    intraday_odd: bool = False
    payload: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        payload = record.pop("payload")
        if self.stream == "bidask":
            for prefix in ("bid_price", "bid_volume", "ask_price", "ask_volume", "diff_bid_vol", "diff_ask_vol"):
                defaults = 0.0 if "price" in prefix else 0
                for index, value in enumerate(_five(payload.get(prefix), defaults), 1):
                    record[f"{prefix}_{index}"] = value
        else:
            for name in ("close", "volume", "total_volume", "tick_type", "best_bid_price", "best_bid_volume", "best_ask_price", "best_ask_volume"):
                record[name] = _number(payload.get(name), 0)
        return record


def normalize(raw: dict[str, Any], session_id: str, sequence_no: int, received: datetime | None = None) -> MarketEvent:
    stream = str(raw.get("stream", "")).lower()
    if stream not in {"bidask", "tick"}:
        raise ValueError("unsupported stream")
    symbol = str(raw.get("symbol") or raw.get("code") or "")
    if not symbol:
        raise ValueError("symbol is required")
    event_ts = parse_event_ts(raw.get("event_ts") or raw.get("datetime") or raw.get("ts"))
    if received is None and raw.get("received_ts"):
        try:
            received = datetime.fromisoformat(str(raw["received_ts"]))
        except ValueError as exc:
            raise ValueError("received timestamp is invalid") from exc
    received = received or datetime.now(TAIPEI)
    if received.tzinfo is None:
        received = received.replace(tzinfo=TAIPEI)
    else:
        received = received.astimezone(TAIPEI)
    payload_keys = {
        "bid_price", "bid_volume", "ask_price", "ask_volume", "diff_bid_vol", "diff_ask_vol",
        "close", "volume", "total_volume", "tick_type", "best_bid_price", "best_bid_volume",
        "best_ask_price", "best_ask_volume",
    }
    return MarketEvent(
        stream=stream,
        exchange=str(raw.get("exchange", "")),
        security_type=str(raw.get("security_type", "")),
        symbol=symbol,
        event_ts=event_ts,
        received_ts=received.isoformat(),
        trading_date=event_ts[:10],
        session_id=session_id,
        sequence_no=sequence_no,
        simtrade=bool(raw.get("simtrade", True)),
        intraday_odd=bool(raw.get("intraday_odd", False)),
        payload={key: raw.get(key) for key in payload_keys},
    )

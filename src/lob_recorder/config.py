from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Instrument:
    code: str
    security_type: str
    exchange: str
    streams: tuple[str, ...]
    intraday_odd: bool = False


def load_instruments(path: str | Path) -> list[Instrument]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load instrument config") from exc
    document: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = document.get("instruments")
    if not isinstance(entries, list):
        raise ValueError("instruments must be a list")
    result: list[Instrument] = []
    seen: set[tuple[str, str, bool]] = set()
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("instrument entry must be a mapping")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        if not enabled:
            continue
        code = str(item.get("code", "")).strip()
        security_type = str(item.get("security_type", "")).upper().strip()
        exchange = str(item.get("exchange", "")).upper().strip()
        raw_streams = item.get("streams", [])
        if not isinstance(raw_streams, list):
            raise ValueError("streams must be a list")
        streams = tuple(str(v).lower() for v in raw_streams)
        odd = item.get("intraday_odd", False)
        if not isinstance(odd, bool):
            raise ValueError("intraday_odd must be a boolean")
        if not code or not security_type or not exchange or not streams:
            raise ValueError("enabled instrument is incomplete")
        if security_type not in {"STK", "FUT", "OPT"}:
            raise ValueError("security_type must be STK, FUT, or OPT")
        if not set(streams) <= {"bidask", "tick"}:
            raise ValueError("streams may only contain bidask and tick")
        if len(streams) != len(set(streams)):
            raise ValueError("streams may not contain duplicates")
        if odd and security_type != "STK":
            raise ValueError("intraday_odd is only supported for stocks")
        key = (security_type, code, odd)
        if key in seen:
            raise ValueError("duplicate instrument")
        seen.add(key)
        result.append(Instrument(code, security_type, exchange, streams, odd))
    if not result:
        raise ValueError("at least one instrument must be enabled")
    return result

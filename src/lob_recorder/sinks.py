from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Iterable
from uuid import UUID


class JsonlSink:
    """Fixture/testing sink containing normalized public market events only."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = Lock()

    def write(self, records: list[dict]) -> None:
        with self.lock, self.path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n")

    def session(self, record: dict) -> None:
        return None

    def gap(self, record: dict) -> None:
        return None


class ClickHouseSink:
    LOB_COLUMNS = [
        "trading_date", "exchange", "security_type", "symbol", "event_ts", "received_ts", "session_id", "sequence_no",
        *[f"{name}_{i}" for name in ("bid_price", "bid_volume", "ask_price", "ask_volume", "diff_bid_vol", "diff_ask_vol") for i in range(1, 6)],
        "simtrade", "intraday_odd",
    ]
    TICK_COLUMNS = [
        "trading_date", "exchange", "security_type", "symbol", "event_ts", "received_ts", "session_id", "sequence_no",
        "close", "volume", "total_volume", "tick_type", "best_bid_price", "best_bid_volume", "best_ask_price", "best_ask_volume",
        "simtrade", "intraday_odd",
    ]

    def __init__(self, host: str, database: str = "lob"):
        import clickhouse_connect
        self.client = clickhouse_connect.get_client(host=host, database=database)

    @staticmethod
    def _value(column: str, value):
        if value is None:
            return None
        if column == "trading_date":
            return date.fromisoformat(value) if isinstance(value, str) else value
        if column in {"event_ts", "received_ts", "started_at", "ended_at"}:
            return datetime.fromisoformat(value) if isinstance(value, str) else value
        if column in {"session_id", "gap_id"}:
            return UUID(value) if isinstance(value, str) else value
        if "price" in column or column == "close":
            return Decimal(str(value))
        return value

    def write(self, records: list[dict]) -> None:
        for stream, table, columns in (("bidask", "lob_events", self.LOB_COLUMNS), ("tick", "tick_events", self.TICK_COLUMNS)):
            selected = [record for record in records if record["stream"] == stream]
            if selected:
                self.client.insert(table, [[self._value(c, r.get(c)) for c in columns] for r in selected], column_names=columns)

    def session(self, record: dict) -> None:
        columns = list(record)
        self.client.insert("capture_sessions", [[self._value(c, record[c]) for c in columns]], column_names=columns)

    def gap(self, record: dict) -> None:
        columns = list(record)
        self.client.insert("capture_gaps", [[self._value(c, record[c]) for c in columns]], column_names=columns)


def read_jsonl(path: str | Path) -> list[dict]:
    file = Path(path)
    if not file.exists():
        return []
    return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Iterable
from uuid import UUID


class PartialWriteError(RuntimeError):
    """A batch write failed after some stream groups were confirmed committed."""

    def __init__(self, pending_records: list[dict], written_count: int):
        super().__init__("market batch was only partially written")
        self.pending_records = pending_records
        self.written_count = written_count


class JsonlSink:
    """Fixture/testing sink containing normalized public market events only."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = Lock()
        self.sessions_path = self.path.with_name(f"{self.path.stem}.sessions.jsonl")
        self.gaps_path = self.path.with_name(f"{self.path.stem}.gaps.jsonl")

    def write(self, records: list[dict]) -> None:
        with self.lock, self.path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n")

    def session(self, record: dict) -> None:
        self._metadata(self.sessions_path, record)

    def gap(self, record: dict) -> None:
        self._metadata(self.gaps_path, record)

    def _metadata(self, path: Path, record: dict) -> None:
        with self.lock, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n")


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
        self.client = clickhouse_connect.get_client(
            host=host,
            database=database,
            connect_timeout=5,
            send_receive_timeout=10,
        )
        self.lock = Lock()

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
        with self.lock:
            groups = (
                ("bidask", "lob_events", self.LOB_COLUMNS),
                ("tick", "tick_events", self.TICK_COLUMNS),
            )
            written_count = 0
            for index, (stream, table, columns) in enumerate(groups):
                selected = [record for record in records if record["stream"] == stream]
                if selected:
                    try:
                        self._insert(table, columns, selected)
                    except Exception as exc:
                        pending_streams = {name for name, _table, _columns in groups[index:]}
                        pending = [record for record in records if record["stream"] in pending_streams]
                        raise PartialWriteError(pending, written_count) from exc
                    written_count += len(selected)

    def replay(self, records: list[dict]) -> None:
        """Idempotently replay market events after an uncertain database write."""
        with self.lock:
            groups = (
                ("bidask", "lob_events", self.LOB_COLUMNS),
                ("tick", "tick_events", self.TICK_COLUMNS),
            )
            for stream, table, columns in groups:
                selected = [record for record in records if record["stream"] == stream]
                missing = self._missing_records(table, selected)
                if missing:
                    self._insert(table, columns, missing)

    def _insert(self, table: str, columns: list[str], records: list[dict]) -> None:
        self.client.insert(
            table,
            [[self._value(column, record.get(column)) for column in columns] for record in records],
            column_names=columns,
        )

    def _missing_records(self, table: str, records: list[dict]) -> list[dict]:
        if not records:
            return []
        by_partition: dict[tuple[str, str], list[int]] = {}
        for record in records:
            session_id = str(UUID(str(record["session_id"])))
            trading_date = str(record["trading_date"])
            by_partition.setdefault((session_id, trading_date), []).append(int(record["sequence_no"]))

        existing: set[tuple[str, int]] = set()
        for (session_id, trading_date), sequences in by_partition.items():
            result = self.client.query(
                f"""
                SELECT session_id, sequence_no
                FROM {table}
                WHERE session_id = {{session_id:UUID}}
                  AND trading_date = {{trading_date:Date}}
                  AND sequence_no BETWEEN {{minimum:UInt64}} AND {{maximum:UInt64}}
                """,
                parameters={
                    "session_id": session_id,
                    "trading_date": trading_date,
                    "minimum": min(sequences),
                    "maximum": max(sequences),
                },
            )
            existing.update((str(row[0]), int(row[1])) for row in result.result_rows)

        missing: list[dict] = []
        seen = set(existing)
        for record in records:
            identity = (str(UUID(str(record["session_id"]))), int(record["sequence_no"]))
            if identity not in seen:
                missing.append(record)
                seen.add(identity)
        return missing

    def session(self, record: dict) -> None:
        columns = list(record)
        with self.lock:
            self.client.insert("capture_sessions", [[self._value(c, record[c]) for c in columns]], column_names=columns)

    def gap(self, record: dict) -> None:
        columns = list(record)
        with self.lock:
            self.client.insert("capture_gaps", [[self._value(c, record[c]) for c in columns]], column_names=columns)


def read_jsonl(path: str | Path) -> list[dict]:
    file = Path(path)
    if not file.exists():
        return []
    return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Iterable


def inspect(
    records: Iterable[dict],
    max_gap_seconds: float = 60.0,
    *,
    sequence_scope_complete: bool = False,
) -> dict[str, int | bool | None]:
    rows = list(records)
    issues = Counter()
    seen: set[tuple] = set()
    sessions: dict[str, list[dict]] = defaultdict(list)
    streams: dict[tuple[str, str, str], list[dict]] = defaultdict(list)

    for row in rows:
        session = str(row.get("session_id", ""))
        sequence = int(row.get("sequence_no", 0))
        stream = str(row.get("stream", ""))
        # sequence_no is allocated across every market stream in a collector
        # session, so the stream must not be part of duplicate identity.
        key = (session, sequence)
        if key in seen:
            issues["duplicates"] += 1
        seen.add(key)
        sessions[session].append(row)
        streams[(session, str(row.get("symbol", "")), stream)].append(row)

        for name, value in row.items():
            if ("volume" in name or "_vol_" in name) and isinstance(value, (int, float)) and value < 0 and not name.startswith("diff_"):
                issues["negative_volume"] += 1
        if row.get("stream") == "bidask":
            best_bid = row.get("bid_price_1")
            best_ask = row.get("ask_price_1")
            if (
                best_bid is not None and best_ask is not None
                and best_bid > 0 and best_ask > 0 and best_bid > best_ask
            ):
                issues["crossed_book"] += 1

    if sequence_scope_complete:
        for session_rows in sessions.values():
            sequences = sorted({int(row.get("sequence_no", 0)) for row in session_rows})
            for previous, current in zip(sequences, sequences[1:]):
                if current > previous + 1:
                    issues["sequence_gaps"] += current - previous - 1

    for stream_rows in streams.values():
        ordered = sorted(stream_rows, key=lambda row: int(row.get("sequence_no", 0)))
        previous_time: datetime | None = None
        for row in ordered:
            try:
                current_time = datetime.fromisoformat(str(row.get("event_ts", "")))
            except ValueError:
                issues["invalid_timestamp"] += 1
                continue
            if previous_time is not None:
                delta = (current_time - previous_time).total_seconds()
                if delta < 0:
                    issues["out_of_order"] += 1
                elif delta > max_gap_seconds:
                    issues["time_gaps"] += 1
            previous_time = current_time

    names = (
        "duplicates",
        "out_of_order",
        "sequence_gaps",
        "time_gaps",
        "invalid_timestamp",
        "negative_volume",
        "crossed_book",
    )
    result: dict[str, int | bool | None] = {
        name: issues.get(name, 0) for name in names
    }
    result["sequence_scope_complete"] = sequence_scope_complete
    if not sequence_scope_complete:
        result["sequence_gaps"] = None
    return result


def inspect_parquet(
    pattern: str,
    max_gap_seconds: float = 60.0,
    *,
    sequence_scope_complete: bool = False,
) -> dict[str, int | bool | None]:
    import glob

    files = glob.glob(pattern, recursive=True)
    if not files:
        raise ValueError("no parquet files matched")
    import pyarrow.parquet as parquet
    rows = []
    for file in files:
        table = parquet.ParquetFile(file).read()
        stream = "bidask" if "bid_price_1" in table.column_names else "tick"
        for row in table.to_pylist():
            row.setdefault("stream", stream)
            rows.append(row)
    return inspect(
        rows,
        max_gap_seconds=max_gap_seconds,
        sequence_scope_complete=sequence_scope_complete,
    )

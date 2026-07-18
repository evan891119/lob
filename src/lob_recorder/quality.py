from __future__ import annotations

from collections import Counter
from typing import Iterable


def inspect(records: Iterable[dict]) -> dict[str, int]:
    rows = list(records)
    issues = Counter()
    seen: set[tuple] = set()
    last: dict[tuple[str, str], tuple[str, int]] = {}
    last_sequence: dict[str, int] = {}
    for row in rows:
        key = (row.get("session_id"), row.get("sequence_no"), row.get("stream"))
        if key in seen:
            issues["duplicates"] += 1
        seen.add(key)
        session = str(row.get("session_id"))
        sequence = int(row.get("sequence_no", 0))
        if session in last_sequence and sequence > last_sequence[session] + 1:
            issues["sequence_gaps"] += sequence - last_sequence[session] - 1
        last_sequence[session] = max(sequence, last_sequence.get(session, 0))
        order_key = (str(row.get("symbol")), str(row.get("stream")))
        current = (str(row.get("event_ts")), int(row.get("sequence_no", 0)))
        if order_key in last and current < last[order_key]:
            issues["out_of_order"] += 1
        last[order_key] = current
        for name, value in row.items():
            if ("volume" in name or "_vol_" in name) and isinstance(value, (int, float)) and value < 0 and not name.startswith("diff_"):
                issues["negative_volume"] += 1
        if row.get("stream") == "bidask" and row.get("bid_price_1", 0) > row.get("ask_price_1", float("inf")):
            issues["crossed_book"] += 1
    return {name: issues.get(name, 0) for name in ("duplicates", "out_of_order", "sequence_gaps", "negative_volume", "crossed_book")}


def inspect_parquet(pattern: str) -> dict[str, int]:
    import glob
    import pyarrow as pa
    import pyarrow.parquet as parquet
    files = glob.glob(pattern)
    if not files:
        raise ValueError("no parquet files matched")
    rows = pa.concat_tables([parquet.ParquetFile(file).read() for file in files], promote_options="default").to_pylist()
    return inspect(rows)

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def collect_report(host: str, output: str | Path, storage_total: int | None = None) -> Path:
    import clickhouse_connect
    client = clickhouse_connect.get_client(host=host, database="lob")
    rows = client.query("""
        SELECT table, symbol, count() AS rows FROM (
          SELECT 'lob_events' AS table, symbol FROM lob_events
          UNION ALL
          SELECT 'tick_events' AS table, symbol FROM tick_events
        ) GROUP BY table, symbol ORDER BY table, symbol
    """).result_rows
    parts = client.query("""
        SELECT table, sum(rows) AS rows, sum(bytes_on_disk) AS bytes
        FROM system.parts
        WHERE active AND database = 'lob' AND table IN ('lob_events', 'tick_events')
        GROUP BY table ORDER BY table
    """).result_rows
    total_bytes = sum(int(row[2]) for row in parts)
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "market_rows": [{"table": row[0], "symbol": row[1], "rows": int(row[2])} for row in rows],
        "market_parts": [{"table": row[0], "rows": int(row[1]), "compressed_bytes": int(row[2])} for row in parts],
        "compressed_bytes": total_bytes,
        "storage": None if storage_total is None else {
            "usable_bytes": storage_total,
            "warning_80_bytes": int(storage_total * 0.80),
            "stop_90_bytes": int(storage_total * 0.90),
        },
    }
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return target

from __future__ import annotations

from pathlib import Path
from uuid import UUID
import re

SAFE_SYMBOL = re.compile(r"^[A-Za-z0-9._-]+$")


def export_clickhouse(host: str, symbol: str, trading_date: str, output: str | Path) -> Path:
    if not SAFE_SYMBOL.fullmatch(symbol):
        raise ValueError("symbol contains unsupported characters")
    from datetime import date
    date.fromisoformat(trading_date)
    import clickhouse_connect
    import pyarrow as pa
    import pyarrow.parquet as pq
    target = Path(output) / f"symbol={symbol}" / f"trading_date={trading_date}"
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    client = clickhouse_connect.get_client(host=host, database="lob")
    for table_name in ("lob_events", "tick_events"):
        query = f"SELECT * FROM {table_name} WHERE symbol = {{symbol:String}} AND trading_date = {{day:Date}} ORDER BY event_ts, sequence_no"
        result = client.query(query, parameters={"symbol": symbol, "day": trading_date})
        rows = [
            {name: str(value) if isinstance(value, UUID) else value for name, value in zip(result.column_names, row)}
            for row in result.result_rows
        ]
        destination = target / f"{table_name}.parquet"
        if not rows:
            if destination.exists():
                destination.unlink()
            continue
        table = pa.Table.from_pylist(rows)
        temporary = target / f".{table_name}.parquet.tmp"
        pq.write_table(table, temporary, compression="zstd")
        temporary.replace(destination)
    return target


def export_day(host: str, trading_date: str, output: str | Path) -> list[Path]:
    import clickhouse_connect

    client = clickhouse_connect.get_client(host=host, database="lob", connect_timeout=5, send_receive_timeout=30)
    symbols = client.query("""
        SELECT DISTINCT symbol FROM (
          SELECT symbol FROM lob_events WHERE trading_date = {day:Date}
          UNION ALL
          SELECT symbol FROM tick_events WHERE trading_date = {day:Date}
        ) ORDER BY symbol
    """, parameters={"day": trading_date}).result_rows
    return [export_clickhouse(host, str(row[0]), trading_date, output) for row in symbols]

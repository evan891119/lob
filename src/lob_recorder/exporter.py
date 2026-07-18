from __future__ import annotations

from pathlib import Path
from uuid import UUID


def export_clickhouse(host: str, symbol: str, trading_date: str, output: str | Path) -> Path:
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
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, target / f"{table_name}.parquet", compression="zstd")
    return target

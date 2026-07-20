from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID
import re


SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
SECURITY_TYPES = {"STK", "FUT", "OPT"}


def _validated_identity(
    security_type: str, exchange: str, symbol: str
) -> tuple[str, str, str]:
    security_type = security_type.upper().strip()
    exchange = exchange.upper().strip()
    symbol = symbol.strip()
    if security_type not in SECURITY_TYPES:
        raise ValueError("security_type must be STK, FUT, or OPT")
    if not SAFE_SEGMENT.fullmatch(exchange):
        raise ValueError("exchange contains unsupported characters")
    if not SAFE_SEGMENT.fullmatch(symbol):
        raise ValueError("symbol contains unsupported characters")
    return security_type, exchange, symbol


def partition_path(
    output: str | Path,
    security_type: str,
    exchange: str,
    symbol: str,
    trading_date: str,
) -> Path:
    security_type, exchange, symbol = _validated_identity(
        security_type, exchange, symbol
    )
    date.fromisoformat(trading_date)
    return (
        Path(output)
        / f"security_type={security_type}"
        / f"exchange={exchange}"
        / f"symbol={symbol}"
        / f"trading_date={trading_date}"
    )


def _client(host: str):
    import clickhouse_connect

    return clickhouse_connect.get_client(
        host=host,
        database="lob",
        connect_timeout=5,
        send_receive_timeout=30,
    )


def _identities(client, trading_date: str, symbol: str | None = None):
    symbol_filter = "AND symbol = {symbol:String}" if symbol is not None else ""
    parameters = {"day": trading_date}
    if symbol is not None:
        parameters["symbol"] = symbol
    result = client.query(f"""
        SELECT DISTINCT security_type, exchange, symbol FROM (
          SELECT security_type, exchange, symbol FROM lob_events
          WHERE trading_date = {{day:Date}} {symbol_filter}
          UNION ALL
          SELECT security_type, exchange, symbol FROM tick_events
          WHERE trading_date = {{day:Date}} {symbol_filter}
        ) ORDER BY security_type, exchange, symbol
    """, parameters=parameters)
    return [
        _validated_identity(str(row[0]), str(row[1]), str(row[2]))
        for row in result.result_rows
    ]


def _export_identity(
    client,
    identity: tuple[str, str, str],
    trading_date: str,
    output: str | Path,
) -> Path:
    security_type, exchange, symbol = identity
    target = partition_path(output, *identity, trading_date)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    import pyarrow as pa
    import pyarrow.parquet as pq

    parameters = {
        "security_type": security_type,
        "exchange": exchange,
        "symbol": symbol,
        "day": trading_date,
    }
    for table_name in ("lob_events", "tick_events"):
        result = client.query(f"""
            SELECT * FROM {table_name}
            WHERE security_type = {{security_type:String}}
              AND exchange = {{exchange:String}}
              AND symbol = {{symbol:String}}
              AND trading_date = {{day:Date}}
            ORDER BY event_ts, sequence_no
        """, parameters=parameters)
        rows = [
            {
                name: str(value) if isinstance(value, UUID) else value
                for name, value in zip(result.column_names, row)
            }
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


def export_clickhouse(
    host: str,
    symbol: str,
    trading_date: str,
    output: str | Path,
    *,
    security_type: str | None = None,
    exchange: str | None = None,
) -> list[Path]:
    if not SAFE_SEGMENT.fullmatch(symbol):
        raise ValueError("symbol contains unsupported characters")
    date.fromisoformat(trading_date)
    if (security_type is None) != (exchange is None):
        raise ValueError("security_type and exchange must be provided together")
    requested_identity = (
        _validated_identity(security_type, exchange, symbol)
        if security_type is not None and exchange is not None
        else None
    )
    client = _client(host)
    identities = (
        [requested_identity]
        if requested_identity is not None
        else _identities(client, trading_date, symbol)
    )
    return [
        _export_identity(client, identity, trading_date, output)
        for identity in identities
    ]


def export_day(host: str, trading_date: str, output: str | Path) -> list[Path]:
    date.fromisoformat(trading_date)
    client = _client(host)
    return [
        _export_identity(client, identity, trading_date, output)
        for identity in _identities(client, trading_date)
    ]

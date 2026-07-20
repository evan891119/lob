from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from lob_recorder.models import TAIPEI


def _rows(result) -> list[dict]:
    return [dict(zip(result.column_names, row)) for row in result.result_rows]


def collect_report(host: str, output: str | Path, storage_total: int | None = None) -> Path:
    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=host,
        database="lob",
        connect_timeout=5,
        send_receive_timeout=30,
    )
    market = client.query("""
        SELECT table, symbol, trading_date, count() AS rows,
               min(event_ts) AS first_event_ts, max(event_ts) AS last_event_ts,
               round(if(dateDiff('millisecond', first_event_ts, last_event_ts) > 0,
                   rows * 1000.0 / dateDiff('millisecond', first_event_ts, last_event_ts), 0), 3) AS average_events_per_second,
               round(quantileExact(0.50)(dateDiff('microsecond', event_ts, received_ts)) / 1000.0, 3) AS latency_ms_p50,
               round(quantileExact(0.95)(dateDiff('microsecond', event_ts, received_ts)) / 1000.0, 3) AS latency_ms_p95,
               round(quantileExact(0.99)(dateDiff('microsecond', event_ts, received_ts)) / 1000.0, 3) AS latency_ms_p99
        FROM (
          SELECT 'lob_events' AS table, symbol, trading_date, event_ts, received_ts FROM lob_events
          UNION ALL
          SELECT 'tick_events' AS table, symbol, trading_date, event_ts, received_ts FROM tick_events
        ) GROUP BY table, symbol, trading_date ORDER BY table, symbol, trading_date
    """)
    peaks = client.query("""
        SELECT table, symbol, trading_date, max(events_in_second) AS peak_events_per_second
        FROM (
          SELECT table, symbol, trading_date, toStartOfSecond(event_ts) AS event_second,
                 count() AS events_in_second
          FROM (
            SELECT 'lob_events' AS table, symbol, trading_date, event_ts FROM lob_events
            UNION ALL
            SELECT 'tick_events' AS table, symbol, trading_date, event_ts FROM tick_events
          )
          GROUP BY table, symbol, trading_date, event_second
        ) GROUP BY table, symbol, trading_date
        ORDER BY table, symbol, trading_date
    """)
    parts = client.query("""
        SELECT table, sum(rows) AS rows,
               sum(bytes_on_disk) AS bytes_on_disk,
               sum(data_compressed_bytes) AS compressed_data_bytes,
               sum(data_uncompressed_bytes) AS uncompressed_data_bytes
        FROM system.parts
        WHERE active AND database = 'lob' AND table IN ('lob_events', 'tick_events')
        GROUP BY table ORDER BY table
    """)
    sessions = client.query("""
        SELECT session_id, started_at, ended_at, status, symbols, subscription_results,
               received, written, spooled, replayed, dropped,
               reconnects, queue_capacity, queue_high_water,
               round(if(queue_capacity > 0, queue_high_water * 100.0 / queue_capacity, 0), 3) AS queue_high_water_percent,
               capacity_bytes_percent, capacity_inode_percent, capacity_used_percent,
               batch_count, batch_insert_ms_total, batch_insert_ms_max,
               callback_latency_ms_max, clock_anomalies
        FROM capture_sessions_latest ORDER BY started_at
    """)
    gaps = client.query("""
        SELECT category, count() AS intervals, sum(affected_count) AS affected_count,
               countIf(ended_at IS NULL) AS open_intervals
        FROM capture_gaps_latest GROUP BY category ORDER BY category
    """)
    market_rows = _rows(market)
    peak_by_group = {
        (row["table"], row["symbol"], str(row["trading_date"])): int(row["peak_events_per_second"])
        for row in _rows(peaks)
    }
    for row in market_rows:
        row["peak_events_per_second"] = peak_by_group.get(
            (row["table"], row["symbol"], str(row["trading_date"])), 0
        )
    part_rows = _rows(parts)
    for row in part_rows:
        compressed = int(row["compressed_data_bytes"])
        uncompressed = int(row["uncompressed_data_bytes"])
        row["compression_ratio"] = round(uncompressed / compressed, 3) if compressed else None
    total_bytes_on_disk = sum(int(row["bytes_on_disk"]) for row in part_rows)
    total_compressed_data_bytes = sum(int(row["compressed_data_bytes"]) for row in part_rows)
    total_uncompressed_data_bytes = sum(int(row["uncompressed_data_bytes"]) for row in part_rows)
    compression_ratio = (
        round(total_uncompressed_data_bytes / total_compressed_data_bytes, 3)
        if total_compressed_data_bytes
        else None
    )
    trading_days = len({str(row["trading_date"]) for row in market_rows})
    observed_symbols = len({str(row["symbol"]) for row in market_rows})
    average_bytes_per_day = round(total_bytes_on_disk / trading_days) if trading_days else 0
    pilot_scope = {
        "observed_symbols": observed_symbols,
        "observed_trading_days": trading_days,
        "minimum_product_count_reached": observed_symbols >= 3,
        "minimum_dataset_scope_reached": observed_symbols >= 3 and trading_days >= 1,
        "recommended_five_day_scope_reached": observed_symbols >= 3 and trading_days >= 5,
    }
    storage = None
    if storage_total is not None:
        stop_bytes = int(storage_total * 0.90)
        storage = {
            "usable_bytes": storage_total,
            "warning_80_bytes": int(storage_total * 0.80),
            "stop_90_bytes": stop_bytes,
            "observed_trading_days": trading_days,
            "average_bytes_on_disk_per_day": average_bytes_per_day,
            "estimated_retention_days_at_90_percent": (
                stop_bytes // average_bytes_per_day if average_bytes_per_day else None
            ),
        }
    report = {
        "generated_at": datetime.now(TAIPEI).isoformat(),
        "market": market_rows,
        "market_parts": part_rows,
        "capture_sessions": _rows(sessions),
        "capture_gaps": _rows(gaps),
        "pilot_scope": pilot_scope,
        "bytes_on_disk": total_bytes_on_disk,
        "compressed_data_bytes": total_compressed_data_bytes,
        "uncompressed_data_bytes": total_uncompressed_data_bytes,
        "compression_ratio": compression_ratio,
        "storage": storage,
    }
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=True, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target

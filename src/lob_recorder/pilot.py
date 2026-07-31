from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from lob_recorder.models import TAIPEI

PROJECTION_PRODUCT_COUNTS = (10, 50, 100)
TRADING_DAYS_PER_MONTH = 20
TRADING_DAYS_PER_YEAR = 250


def _rows(result) -> list[dict]:
    return [dict(zip(result.column_names, row)) for row in result.result_rows]


def _subscribed_identities(session_rows: list[dict]) -> set[tuple[str, str, str]]:
    identities = set()
    for row in session_rows:
        results = row.get("subscription_results", [])
        if not isinstance(results, list):
            continue
        for result in results:
            parts = str(result).split(":")
            if (
                len(parts) == 6
                and parts[3] in {"bidask", "tick"}
                and parts[5] in {"subscribed", "resubscribed"}
                and all(part.replace(".", "").replace("_", "").isalnum() for part in parts)
            ):
                identities.add((parts[0], parts[1], parts[4]))
    return identities


def _scope_part_metrics(
    part_rows: list[dict], market_rows: list[dict], *, filtered: bool
) -> tuple[int, int, int]:
    selected_rows_by_table: dict[str, int] = {}
    for row in market_rows:
        table = str(row["table"])
        selected_rows_by_table[table] = selected_rows_by_table.get(table, 0) + int(row["rows"])

    for row in part_rows:
        table = str(row["table"])
        active_rows = int(row["rows"])
        selected_rows = selected_rows_by_table.get(table, 0)
        if filtered:
            row_fraction = min(1.0, selected_rows / active_rows) if active_rows else 0.0
            method = "estimated_by_table_row_share_of_active_parts"
        else:
            selected_rows = active_rows
            row_fraction = 1.0 if active_rows else 0.0
            method = "exact_active_parts"
        row["scope_rows"] = selected_rows
        row["scope_row_fraction"] = round(row_fraction, 9)
        row["scope_measurement"] = method
        for source, target in (
            ("bytes_on_disk", "scope_bytes_on_disk"),
            ("compressed_data_bytes", "scope_compressed_data_bytes"),
            ("uncompressed_data_bytes", "scope_uncompressed_data_bytes"),
        ):
            row[target] = round(int(row[source]) * row_fraction)
        scoped_compressed = int(row["scope_compressed_data_bytes"])
        scoped_uncompressed = int(row["scope_uncompressed_data_bytes"])
        row["scope_compression_ratio"] = (
            round(scoped_uncompressed / scoped_compressed, 3)
            if scoped_compressed
            else None
        )

    return (
        sum(int(row["scope_bytes_on_disk"]) for row in part_rows),
        sum(int(row["scope_compressed_data_bytes"]) for row in part_rows),
        sum(int(row["scope_uncompressed_data_bytes"]) for row in part_rows),
    )


def _capacity_projections(
    market_rows: list[dict],
    *,
    observed_products: int,
    trading_days: int,
    total_bytes_on_disk: int,
    stop_bytes: int | None,
) -> dict:
    daily_average_eps: dict[str, float] = {}
    daily_conservative_peak_sum_eps: dict[str, float] = {}
    for row in market_rows:
        trading_date = str(row["trading_date"])
        daily_average_eps[trading_date] = (
            daily_average_eps.get(trading_date, 0.0)
            + float(row["average_events_per_second"])
        )
        daily_conservative_peak_sum_eps[trading_date] = (
            daily_conservative_peak_sum_eps.get(trading_date, 0.0)
            + float(row["peak_events_per_second"])
        )

    basis_units = observed_products * trading_days
    bytes_per_product_trading_day = (
        total_bytes_on_disk / basis_units if basis_units else None
    )
    average_aggregate_eps = (
        sum(daily_average_eps.values()) / trading_days if trading_days else None
    )
    conservative_peak_sum_eps = (
        max(daily_conservative_peak_sum_eps.values())
        if daily_conservative_peak_sum_eps
        else None
    )
    average_eps_per_product = (
        average_aggregate_eps / observed_products
        if average_aggregate_eps is not None and observed_products
        else None
    )
    conservative_peak_sum_eps_per_product = (
        conservative_peak_sum_eps / observed_products
        if conservative_peak_sum_eps is not None and observed_products
        else None
    )

    targets = []
    for products in PROJECTION_PRODUCT_COUNTS:
        bytes_per_day = (
            round(bytes_per_product_trading_day * products)
            if bytes_per_product_trading_day is not None
            else None
        )
        targets.append(
            {
                "products": products,
                "estimated_average_events_per_second": (
                    round(average_eps_per_product * products, 3)
                    if average_eps_per_product is not None
                    else None
                ),
                "estimated_conservative_peak_sum_events_per_second": (
                    round(conservative_peak_sum_eps_per_product * products, 3)
                    if conservative_peak_sum_eps_per_product is not None
                    else None
                ),
                "estimated_bytes_per_trading_day": bytes_per_day,
                "estimated_bytes_per_20_trading_days": (
                    bytes_per_day * TRADING_DAYS_PER_MONTH if bytes_per_day is not None else None
                ),
                "estimated_bytes_per_250_trading_days": (
                    bytes_per_day * TRADING_DAYS_PER_YEAR if bytes_per_day is not None else None
                ),
                "estimated_one_full_copy_backup_bytes_per_20_trading_days": (
                    bytes_per_day * TRADING_DAYS_PER_MONTH if bytes_per_day is not None else None
                ),
                "estimated_one_full_copy_backup_bytes_per_250_trading_days": (
                    bytes_per_day * TRADING_DAYS_PER_YEAR if bytes_per_day is not None else None
                ),
                "estimated_retention_trading_days_at_90_percent": (
                    stop_bytes // bytes_per_day
                    if stop_bytes is not None and bytes_per_day
                    else None
                ),
            }
        )

    return {
        "basis": {
            "observed_products": observed_products,
            "observed_trading_days": trading_days,
            "minimum_dataset_scope_reached": observed_products >= 3 and trading_days >= 1,
            "bytes_on_disk_per_product_trading_day": (
                round(bytes_per_product_trading_day)
                if bytes_per_product_trading_day is not None
                else None
            ),
            "average_aggregate_events_per_second": (
                round(average_aggregate_eps, 3) if average_aggregate_eps is not None else None
            ),
            "conservative_peak_sum_events_per_second": (
                round(conservative_peak_sum_eps, 3)
                if conservative_peak_sum_eps is not None
                else None
            ),
        },
        "assumptions": {
            "linear_scaling_by_product_count": True,
            "trading_days_per_month": TRADING_DAYS_PER_MONTH,
            "trading_days_per_year": TRADING_DAYS_PER_YEAR,
            "backup_model": "one_full_copy_of_new_market_data_only",
            "peak_model": "sum_of_individual_stream_peaks_conservative_upper_bound",
        },
        "targets": targets,
    }


def collect_report(
    host: str,
    output: str | Path,
    storage_total: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> Path:
    if (start_date is None) != (end_date is None):
        raise ValueError("start_date and end_date must be provided together")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    if storage_total is not None and storage_total <= 0:
        raise ValueError("storage_total must be positive")

    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=host,
        database="lob",
        connect_timeout=5,
        send_receive_timeout=30,
    )
    filtered = start_date is not None
    query_parameters = (
        {"start_date": start_date, "end_date": end_date} if filtered else {}
    )
    market_filter = (
        "WHERE trading_date BETWEEN {start_date:Date} AND {end_date:Date}"
        if filtered
        else ""
    )
    audit_filter = (
        "WHERE toDate(started_at) <= {end_date:Date} "
        "AND toDate(ifNull(ended_at, now64(6))) >= {start_date:Date}"
        if filtered
        else ""
    )
    market = client.query(f"""
        SELECT table, security_type, exchange, symbol, trading_date, count() AS rows,
               min(event_ts) AS first_event_ts, max(event_ts) AS last_event_ts,
               round(if(dateDiff('millisecond', first_event_ts, last_event_ts) > 0,
                   rows * 1000.0 / dateDiff('millisecond', first_event_ts, last_event_ts), 0), 3) AS average_events_per_second,
               round(quantileExact(0.50)(dateDiff('microsecond', event_ts, received_ts)) / 1000.0, 3) AS latency_ms_p50,
               round(quantileExact(0.95)(dateDiff('microsecond', event_ts, received_ts)) / 1000.0, 3) AS latency_ms_p95,
               round(quantileExact(0.99)(dateDiff('microsecond', event_ts, received_ts)) / 1000.0, 3) AS latency_ms_p99
        FROM (
          SELECT 'lob_events' AS table, security_type, exchange, symbol,
                 trading_date, event_ts, received_ts FROM lob_events {market_filter}
          UNION ALL
          SELECT 'tick_events' AS table, security_type, exchange, symbol,
                 trading_date, event_ts, received_ts FROM tick_events {market_filter}
        ) GROUP BY table, security_type, exchange, symbol, trading_date
        ORDER BY table, security_type, exchange, symbol, trading_date
    """, parameters=query_parameters)
    peaks = client.query(f"""
        SELECT table, security_type, exchange, symbol, trading_date,
               max(events_in_second) AS peak_events_per_second
        FROM (
          SELECT table, security_type, exchange, symbol, trading_date,
                 toStartOfSecond(event_ts) AS event_second,
                 count() AS events_in_second
          FROM (
            SELECT 'lob_events' AS table, security_type, exchange, symbol,
                   trading_date, event_ts FROM lob_events {market_filter}
            UNION ALL
            SELECT 'tick_events' AS table, security_type, exchange, symbol,
                   trading_date, event_ts FROM tick_events {market_filter}
          )
          GROUP BY table, security_type, exchange, symbol, trading_date, event_second
        ) GROUP BY table, security_type, exchange, symbol, trading_date
        ORDER BY table, security_type, exchange, symbol, trading_date
    """, parameters=query_parameters)
    parts = client.query("""
        SELECT table, sum(rows) AS rows,
               sum(bytes_on_disk) AS bytes_on_disk,
               sum(data_compressed_bytes) AS compressed_data_bytes,
               sum(data_uncompressed_bytes) AS uncompressed_data_bytes
        FROM system.parts
        WHERE active AND database = 'lob' AND table IN ('lob_events', 'tick_events')
        GROUP BY table ORDER BY table
    """)
    sessions = client.query(f"""
        SELECT started_at, ended_at, status, simulation, symbols, subscription_results,
               received, written, spooled, replayed, dropped, notice_dropped,
               reconnects, queue_capacity, queue_high_water,
               round(if(queue_capacity > 0, queue_high_water * 100.0 / queue_capacity, 0), 3) AS queue_high_water_percent,
               capacity_bytes_percent, capacity_inode_percent, capacity_used_percent,
               batch_count, batch_insert_ms_total, batch_insert_ms_max,
               callback_latency_ms_max, clock_anomalies,
               process_cpu_seconds, process_max_rss_bytes,
               round(if(dateDiff('millisecond', started_at, ifNull(ended_at, now64(6))) > 0,
                   process_cpu_seconds * 1000.0 * 100.0 /
                   dateDiff('millisecond', started_at, ifNull(ended_at, now64(6))), 0), 3)
                   AS average_process_cpu_percent
        FROM capture_sessions_latest {audit_filter} ORDER BY started_at
    """, parameters=query_parameters)
    gaps = client.query(f"""
        SELECT category, count() AS intervals, sum(affected_count) AS affected_count,
               countIf(ended_at IS NULL) AS open_intervals
        FROM capture_gaps_latest {audit_filter}
        GROUP BY category ORDER BY category
    """, parameters=query_parameters)
    market_rows = _rows(market)
    peak_by_group = {
        (
            row["table"], row["security_type"], row["exchange"],
            row["symbol"], str(row["trading_date"]),
        ): int(row["peak_events_per_second"])
        for row in _rows(peaks)
    }
    for row in market_rows:
        row["peak_events_per_second"] = peak_by_group.get(
            (
                row["table"], row["security_type"], row["exchange"],
                row["symbol"], str(row["trading_date"]),
            ),
            0,
        )
    part_rows = _rows(parts)
    for row in part_rows:
        compressed = int(row["compressed_data_bytes"])
        uncompressed = int(row["uncompressed_data_bytes"])
        row["compression_ratio"] = round(uncompressed / compressed, 3) if compressed else None
    global_bytes_on_disk = sum(int(row["bytes_on_disk"]) for row in part_rows)
    global_compressed_data_bytes = sum(int(row["compressed_data_bytes"]) for row in part_rows)
    global_uncompressed_data_bytes = sum(int(row["uncompressed_data_bytes"]) for row in part_rows)
    (
        total_bytes_on_disk,
        total_compressed_data_bytes,
        total_uncompressed_data_bytes,
    ) = _scope_part_metrics(part_rows, market_rows, filtered=filtered)
    compression_ratio = (
        round(total_uncompressed_data_bytes / total_compressed_data_bytes, 3)
        if total_compressed_data_bytes
        else None
    )
    observed_dates = {str(row["trading_date"]) for row in market_rows}
    observed_identities = {
        (str(row["security_type"]), str(row["exchange"]), str(row["symbol"]))
        for row in market_rows
    }
    trading_days = len(observed_dates)
    observed_products = len(observed_identities)
    streams_by_identity_date: dict[tuple[str, str, str, str], set[str]] = {}
    for row in market_rows:
        key = (
            str(row["security_type"]),
            str(row["exchange"]),
            str(row["symbol"]),
            str(row["trading_date"]),
        )
        streams_by_identity_date.setdefault(key, set()).add(str(row["table"]))
    average_bytes_per_day = round(total_bytes_on_disk / trading_days) if trading_days else 0
    pilot_scope = {
        "observed_products": observed_products,
        "observed_trading_days": trading_days,
        "minimum_product_count_reached": observed_products >= 3,
        "minimum_dataset_scope_reached": observed_products >= 3 and trading_days >= 1,
        "recommended_five_day_scope_reached": observed_products >= 3 and trading_days >= 5,
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
    projections = _capacity_projections(
        market_rows,
        observed_products=observed_products,
        trading_days=trading_days,
        total_bytes_on_disk=total_bytes_on_disk,
        stop_bytes=storage["stop_90_bytes"] if storage is not None else None,
    )
    session_rows = _rows(sessions)
    gap_rows = _rows(gaps)
    subscribed_identities = _subscribed_identities(session_rows)
    all_subscribed_products_observed = bool(subscribed_identities) and (
        subscribed_identities <= observed_identities
    )
    expected_identity_dates = {
        (*identity, trading_date)
        for identity in (observed_identities | subscribed_identities)
        for trading_date in observed_dates
    }
    all_products_all_days_both_streams = bool(expected_identity_dates) and all(
        streams_by_identity_date.get(key) == {"lob_events", "tick_events"}
        for key in expected_identity_dates
    )
    for row in session_rows:
        row.pop("subscription_results", None)
    sessions_available = bool(session_rows)
    sessions_simulation_only = sessions_available and all(
        bool(row.get("simulation")) for row in session_rows
    )
    sessions_no_drops = sessions_available and all(
        int(row.get("dropped", 0)) == 0
        and int(row.get("notice_dropped", 0)) == 0
        for row in session_rows
    )
    scoped_sessions_completed = sessions_available and all(
        row.get("ended_at") is not None for row in session_rows
    )
    no_open_gaps = all(int(row.get("open_intervals", 0)) == 0 for row in gap_rows)
    storage_projection_available = bool(
        storage
        and total_bytes_on_disk > 0
        and projections["basis"]["bytes_on_disk_per_product_trading_day"] is not None
    )
    checks = {
        "minimum_dataset_scope_reached": pilot_scope["minimum_dataset_scope_reached"],
        "recommended_five_day_scope_reached": pilot_scope["recommended_five_day_scope_reached"],
        "all_subscribed_products_observed": all_subscribed_products_observed,
        "all_products_all_days_both_streams_present": all_products_all_days_both_streams,
        "sessions_available": sessions_available,
        "sessions_simulation_only": sessions_simulation_only,
        "sessions_no_drops": sessions_no_drops,
        "scoped_sessions_completed": scoped_sessions_completed,
        "no_open_gaps": no_open_gaps,
        "storage_projection_available": storage_projection_available,
        "pilot_data_integrity_ready": (
            pilot_scope["minimum_dataset_scope_reached"]
            and all_subscribed_products_observed
            and all_products_all_days_both_streams
            and sessions_simulation_only
            and sessions_no_drops
            and scoped_sessions_completed
            and no_open_gaps
            and storage_projection_available
        ),
        "full_trading_period_operator_confirmation_required": True,
    }
    report = {
        "generated_at": datetime.now(TAIPEI).isoformat(),
        "report_scope": {
            "start_date": start_date.isoformat() if start_date is not None else None,
            "end_date": end_date.isoformat() if end_date is not None else None,
            "inclusive": True,
            "market_storage_measurement": (
                "estimated_by_table_row_share_of_active_parts"
                if filtered
                else "exact_active_parts"
            ),
        },
        "market": market_rows,
        "market_parts": part_rows,
        "capture_sessions": session_rows,
        "capture_gaps": gap_rows,
        "pilot_scope": pilot_scope,
        "bytes_on_disk": total_bytes_on_disk,
        "compressed_data_bytes": total_compressed_data_bytes,
        "uncompressed_data_bytes": total_uncompressed_data_bytes,
        "compression_ratio": compression_ratio,
        "global_active_parts": {
            "bytes_on_disk": global_bytes_on_disk,
            "compressed_data_bytes": global_compressed_data_bytes,
            "uncompressed_data_bytes": global_uncompressed_data_bytes,
        },
        "storage": storage,
        "capacity_projections": projections,
        "checks": checks,
    }
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=True, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from lob_recorder.models import TAIPEI
from lob_recorder.privacy import SENSITIVE_VALUE_PATTERNS


PUBLIC_TOKEN = re.compile(r"^[A-Za-z0-9._:-]+$")
COUNTER_KEYS = {
    "received", "written", "spooled", "replayed", "dropped", "notice_dropped",
    "queue_high_water", "batch_count", "batch_insert_ms_max",
    "callback_latency_ms_max", "clock_anomalies",
}


def _rows(result) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row)) for row in result.result_rows]


def _token(value: Any) -> str:
    text = str(value)
    if not PUBLIC_TOKEN.fullmatch(text):
        return "[REDACTED]"
    if any(pattern.search(text) for pattern in SENSITIVE_VALUE_PATTERNS):
        return "[REDACTED]"
    return text


def _health_summary(path: str | Path, max_age_seconds: float) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("health payload must be an object")
        updated = datetime.fromisoformat(str(raw["updated_at"]))
        age = (datetime.now(updated.tzinfo) - updated).total_seconds()
        counters = raw.get("counters") if isinstance(raw.get("counters"), dict) else {}
        storage = raw.get("storage_capacity") if isinstance(raw.get("storage_capacity"), dict) else None
        results = raw.get("subscription_results") if isinstance(raw.get("subscription_results"), list) else []
        return {
            "readable": True,
            "fresh": 0 <= age <= max_age_seconds,
            "age_seconds": round(age, 3),
            "status": _token(raw.get("status", "unknown")),
            "subscriptions_active": int(raw.get("subscriptions_active", 0)),
            "subscriptions_failed": int(raw.get("subscriptions_failed", 0)),
            "subscription_results": [_token(value) for value in results],
            "queue_size": int(raw.get("queue_size", 0)),
            "queue_capacity": int(raw.get("queue_capacity", 0)),
            "storage_capacity": None if storage is None else {
                "bytes_percent": float(storage.get("bytes_percent", 0)),
                "inode_percent": float(storage.get("inode_percent", 0)),
                "used_percent": float(storage.get("used_percent", 0)),
            },
            "counters": {
                key: counters[key]
                for key in sorted(COUNTER_KEYS)
                if key in counters and isinstance(counters[key], (int, float))
            },
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {
            "readable": False,
            "fresh": False,
            "age_seconds": None,
            "status": "unavailable",
            "subscriptions_active": 0,
            "subscriptions_failed": 0,
            "subscription_results": [],
            "queue_size": 0,
            "queue_capacity": 0,
            "storage_capacity": None,
            "counters": {},
        }


def collect_acceptance_report(
    host: str,
    health_file: str | Path,
    max_health_age_seconds: float = 90,
) -> dict[str, Any]:
    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=host,
        database="lob",
        connect_timeout=5,
        send_receive_timeout=30,
    )
    totals_result = client.query("""
        SELECT countIf(stream = 'bidask') AS lob_rows,
               countIf(stream = 'tick') AS tick_rows,
               uniqExact(symbol) AS symbols,
               uniqExact(trading_date) AS trading_days,
               minOrNull(event_ts) AS first_event_ts,
               maxOrNull(event_ts) AS last_event_ts
        FROM (
          SELECT 'bidask' AS stream, symbol, trading_date, event_ts FROM lob_events
          UNION ALL
          SELECT 'tick' AS stream, symbol, trading_date, event_ts FROM tick_events
        )
    """)
    symbols_result = client.query("""
        SELECT security_type, exchange, symbol,
               countIf(stream = 'bidask') AS lob_rows,
               countIf(stream = 'tick') AS tick_rows,
               uniqExact(trading_date) AS trading_days,
               min(event_ts) AS first_event_ts,
               max(event_ts) AS last_event_ts
        FROM (
          SELECT 'bidask' AS stream, security_type, exchange, symbol, trading_date, event_ts FROM lob_events
          UNION ALL
          SELECT 'tick' AS stream, security_type, exchange, symbol, trading_date, event_ts FROM tick_events
        )
        GROUP BY security_type, exchange, symbol
        ORDER BY security_type, exchange, symbol
    """)
    session_result = client.query("""
        SELECT status, simulation, symbols, enabled_symbols,
               subscriptions_active, subscriptions_failed, subscription_results,
               received, written, spooled, replayed, dropped, notice_dropped,
               reconnects, queue_capacity, queue_high_water,
               capacity_bytes_percent, capacity_inode_percent, capacity_used_percent,
               batch_count, batch_insert_ms_max, callback_latency_ms_max, clock_anomalies,
               started_at, ended_at
        FROM capture_sessions_latest
        ORDER BY started_at DESC LIMIT 1
    """)
    gaps_result = client.query("""
        SELECT category, count() AS intervals,
               countIf(ended_at IS NULL) AS open_intervals,
               sum(affected_count) AS affected_count
        FROM capture_gaps_latest
        GROUP BY category ORDER BY category
    """)

    total_rows = _rows(totals_result)
    totals = total_rows[0] if total_rows else {
        "lob_rows": 0,
        "tick_rows": 0,
        "symbols": 0,
        "trading_days": 0,
        "first_event_ts": None,
        "last_event_ts": None,
    }
    symbols = [
        {
            "security_type": _token(row["security_type"]),
            "exchange": _token(row["exchange"]),
            "symbol": _token(row["symbol"]),
            "lob_rows": int(row["lob_rows"]),
            "tick_rows": int(row["tick_rows"]),
            "trading_days": int(row["trading_days"]),
            "first_event_ts": None if row["first_event_ts"] is None else str(row["first_event_ts"]),
            "last_event_ts": None if row["last_event_ts"] is None else str(row["last_event_ts"]),
        }
        for row in _rows(symbols_result)
    ]
    session_rows = _rows(session_result)
    session = None
    if session_rows:
        row = session_rows[0]
        session = {
            "status": _token(row["status"]),
            "simulation": bool(row["simulation"]),
            "symbols": [_token(value) for value in row["symbols"]],
            "enabled_symbols": int(row["enabled_symbols"]),
            "subscriptions_active": int(row["subscriptions_active"]),
            "subscriptions_failed": int(row["subscriptions_failed"]),
            "subscription_results": [_token(value) for value in row["subscription_results"]],
            "received": int(row["received"]),
            "written": int(row["written"]),
            "spooled": int(row["spooled"]),
            "replayed": int(row["replayed"]),
            "dropped": int(row["dropped"]),
            "notice_dropped": int(row["notice_dropped"]),
            "reconnects": int(row["reconnects"]),
            "queue_capacity": int(row["queue_capacity"]),
            "queue_high_water": int(row["queue_high_water"]),
            "capacity_bytes_percent": row["capacity_bytes_percent"],
            "capacity_inode_percent": row["capacity_inode_percent"],
            "capacity_used_percent": row["capacity_used_percent"],
            "batch_count": int(row["batch_count"]),
            "batch_insert_ms_max": float(row["batch_insert_ms_max"]),
            "callback_latency_ms_max": float(row["callback_latency_ms_max"]),
            "clock_anomalies": int(row["clock_anomalies"]),
            "started_at": str(row["started_at"]),
            "ended_at": None if row["ended_at"] is None else str(row["ended_at"]),
        }
    gaps = [
        {
            "category": _token(row["category"]),
            "intervals": int(row["intervals"]),
            "open_intervals": int(row["open_intervals"]),
            "affected_count": int(row["affected_count"]),
        }
        for row in _rows(gaps_result)
    ]
    health = _health_summary(health_file, max_health_age_seconds)
    lob_rows = int(totals["lob_rows"])
    tick_rows = int(totals["tick_rows"])
    capacity = health["storage_capacity"]
    checks = {
        "health_fresh": bool(health["fresh"]),
        "collector_operational": health["status"] in {"running", "degraded"},
        "simulation_only": bool(session and session["simulation"]),
        "subscriptions_active": health["subscriptions_active"] > 0,
        "market_rows_present": lob_rows + tick_rows > 0,
        "both_streams_present": lob_rows > 0 and tick_rows > 0,
        "current_session_no_drops": bool(session and session["dropped"] == 0),
        "no_open_gaps": all(row["open_intervals"] == 0 for row in gaps),
        "storage_below_stop_threshold": bool(capacity and capacity["used_percent"] < 90),
        "pilot_scope_reached": int(totals["symbols"]) >= 3 and int(totals["trading_days"]) >= 1,
    }
    return {
        "generated_at": datetime.now(TAIPEI).isoformat(),
        "health": health,
        "database": {
            "lob_rows": lob_rows,
            "tick_rows": tick_rows,
            "symbols": int(totals["symbols"]),
            "trading_days": int(totals["trading_days"]),
            "first_event_ts": None if totals["first_event_ts"] is None else str(totals["first_event_ts"]),
            "last_event_ts": None if totals["last_event_ts"] is None else str(totals["last_event_ts"]),
            "by_symbol": symbols,
        },
        "latest_session": session,
        "gaps": gaps,
        "checks": checks,
    }


def write_acceptance_report(report: dict[str, Any], output: str | Path) -> Path:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target

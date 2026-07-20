from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from lob_recorder.models import TAIPEI


COUNTERS = ("received", "written", "spooled", "replayed", "dropped", "notice_dropped")
NETWORK_COUNTERS = ("received", "dropped", "notice_dropped", "reconnects")


def _load(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("outage input must be a JSON object")
    return payload


def _counter(report: dict[str, Any], name: str) -> int:
    session = report.get("latest_session")
    if not isinstance(session, dict):
        raise ValueError("latest session is unavailable")
    value = session.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("session counter is unavailable")
    return value


def _gap(report: dict[str, Any], category: str) -> dict[str, Any]:
    gaps = report.get("gaps")
    if not isinstance(gaps, list):
        raise ValueError("gap summary is unavailable")
    for gap in gaps:
        if isinstance(gap, dict) and gap.get("category") == category:
            return gap
    return {"intervals": 0, "open_intervals": 0, "affected_count": 0}


def build_outage_evidence(
    before_path: str | Path,
    after_path: str | Path,
    requested_outage_seconds: int,
) -> dict[str, Any]:
    if not 30 <= requested_outage_seconds <= 900:
        raise ValueError("outage duration must be between 30 and 900 seconds")
    before = _load(before_path)
    after = _load(after_path)
    before_session = before.get("latest_session")
    after_session = after.get("latest_session")
    if not isinstance(before_session, dict) or not isinstance(after_session, dict):
        raise ValueError("latest session is unavailable")

    deltas = {name: _counter(after, name) - _counter(before, name) for name in COUNTERS}
    before_gap = _gap(before, "database_failure")
    after_gap = _gap(after, "database_failure")
    checks = {
        "same_collector_session": (
            before_session.get("started_at") is not None
            and before_session.get("started_at") == after_session.get("started_at")
        ),
        "simulation_only": bool(before_session.get("simulation") and after_session.get("simulation")),
        "market_events_received": deltas["received"] > 0,
        "spooled_during_outage": deltas["spooled"] > 0,
        "spool_fully_replayed": deltas["replayed"] >= deltas["spooled"] > 0,
        "no_new_drops": deltas["dropped"] == 0 and deltas["notice_dropped"] == 0,
        "database_gap_recorded": int(after_gap.get("intervals", 0)) > int(before_gap.get("intervals", 0)),
        "database_gap_closed": int(after_gap.get("open_intervals", 0)) == 0,
    }
    checks["outage_recovery_verified"] = all(checks.values())
    return {
        "generated_at": datetime.now(TAIPEI).isoformat(),
        "requested_outage_seconds": requested_outage_seconds,
        "counter_deltas": deltas,
        "checks": checks,
    }


def build_network_outage_evidence(
    before_path: str | Path,
    after_path: str | Path,
    requested_outage_seconds: int,
) -> dict[str, Any]:
    if not 30 <= requested_outage_seconds <= 300:
        raise ValueError("network outage duration must be between 30 and 300 seconds")
    before = _load(before_path)
    after = _load(after_path)
    before_session = before.get("latest_session")
    after_session = after.get("latest_session")
    if not isinstance(before_session, dict) or not isinstance(after_session, dict):
        raise ValueError("latest session is unavailable")

    deltas = {
        name: _counter(after, name) - _counter(before, name)
        for name in NETWORK_COUNTERS
    }
    before_gap = _gap(before, "connection_down")
    after_gap = _gap(after, "connection_down")
    before_active = before_session.get("subscriptions_active")
    after_active = after_session.get("subscriptions_active")
    before_failed = before_session.get("subscriptions_failed")
    after_failed = after_session.get("subscriptions_failed")
    subscription_counts_valid = all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (before_active, after_active, before_failed, after_failed)
    )
    checks = {
        "same_collector_session": (
            before_session.get("started_at") is not None
            and before_session.get("started_at") == after_session.get("started_at")
        ),
        "simulation_only": bool(before_session.get("simulation") and after_session.get("simulation")),
        "market_events_resumed": deltas["received"] > 0,
        "reconnect_recorded": deltas["reconnects"] > 0,
        "subscriptions_restored": bool(
            subscription_counts_valid
            and before_active > 0
            and after_active >= before_active
            and after_failed <= before_failed
        ),
        "no_new_drops": deltas["dropped"] == 0 and deltas["notice_dropped"] == 0,
        "connection_gap_recorded": int(after_gap.get("intervals", 0)) > int(before_gap.get("intervals", 0)),
        "connection_gap_closed": int(after_gap.get("open_intervals", 0)) == 0,
    }
    checks["network_outage_verified"] = all(checks.values())
    return {
        "generated_at": datetime.now(TAIPEI).isoformat(),
        "requested_outage_seconds": requested_outage_seconds,
        "counter_deltas": deltas,
        "checks": checks,
    }


def write_outage_evidence(report: dict[str, Any], output: str | Path) -> Path:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target

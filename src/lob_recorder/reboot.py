from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from lob_recorder.models import TAIPEI


def _load(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("reboot input must be a JSON object")
    return payload


def _nonnegative_int(container: dict[str, Any], key: str) -> int:
    value = container.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("reboot row count is unavailable")
    return value


def build_reboot_evidence(
    before_path: str | Path,
    after_path: str | Path,
    reboot_observed: bool,
) -> dict[str, Any]:
    before = _load(before_path)
    after = _load(after_path)
    before_database = before.get("database")
    after_database = after.get("database")
    before_session = before.get("latest_session")
    after_session = after.get("latest_session")
    after_checks = after.get("checks")
    if not all(isinstance(value, dict) for value in (
        before_database, after_database, before_session, after_session, after_checks
    )):
        raise ValueError("reboot acceptance data is unavailable")

    row_deltas = {
        key: _nonnegative_int(after_database, key) - _nonnegative_int(before_database, key)
        for key in ("lob_rows", "tick_rows")
    }
    before_first = before_database.get("first_event_ts")
    after_first = after_database.get("first_event_ts")
    checks = {
        "host_reboot_observed": bool(reboot_observed),
        "collector_session_restarted": (
            before_session.get("started_at") is not None
            and after_session.get("started_at") is not None
            and before_session.get("started_at") != after_session.get("started_at")
        ),
        "simulation_only": bool(before_session.get("simulation") and after_session.get("simulation")),
        "historical_start_preserved": bool(
            before_first is not None and before_first == after_first
        ),
        "market_rows_not_decreased": all(delta >= 0 for delta in row_deltas.values()),
        "collector_operational": bool(
            after_checks.get("health_fresh")
            and after_checks.get("collector_operational")
            and after_checks.get("subscriptions_active")
        ),
        "storage_operational": bool(after_checks.get("storage_below_stop_threshold")),
        "no_open_gaps": bool(after_checks.get("no_open_gaps")),
    }
    checks["reboot_persistence_verified"] = all(checks.values())
    return {
        "generated_at": datetime.now(TAIPEI).isoformat(),
        "row_deltas": row_deltas,
        "checks": checks,
    }


def write_reboot_evidence(report: dict[str, Any], output: str | Path) -> Path:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target

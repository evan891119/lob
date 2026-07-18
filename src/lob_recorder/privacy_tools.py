from __future__ import annotations

import json
import shutil
from pathlib import Path

from lob_recorder.privacy import SENSITIVE_VALUE_PATTERNS

COMMON_MARKET_KEYS = {
    "stream", "trading_date", "exchange", "security_type", "symbol", "event_ts", "received_ts",
    "session_id", "sequence_no", "simtrade", "intraday_odd",
}
LOB_KEYS = COMMON_MARKET_KEYS | {
    f"{name}_{level}"
    for name in ("bid_price", "bid_volume", "ask_price", "ask_volume", "diff_bid_vol", "diff_ask_vol")
    for level in range(1, 6)
}
TICK_KEYS = COMMON_MARKET_KEYS | {
    "close", "volume", "total_volume", "tick_type", "best_bid_price", "best_bid_volume",
    "best_ask_price", "best_ask_volume",
}


def inventory(root: str | Path) -> list[dict]:
    base = Path(root)
    result = []
    if not base.exists():
        return result
    for file in sorted(path for path in base.rglob("*") if path.is_file()):
        stat = file.stat()
        hits = 0
        if stat.st_size <= 10_000_000:
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
                hits = sum(len(pattern.findall(text)) for pattern in SENSITIVE_VALUE_PATTERNS)
            except OSError:
                hits = -1
        result.append({"name": str(file.relative_to(base)), "size": stat.st_size, "mtime": int(stat.st_mtime), "sensitive_hits": hits})
    return result


def inspect_spool_schema(root: str | Path) -> dict[str, int | str]:
    base = Path(root)
    files = 0
    records = 0
    violations = 0
    if not base.exists():
        return {"area": "spool-schema", "files": 0, "records": 0, "violations": 0}
    for file in sorted(path for path in base.rglob("*.jsonl*") if path.is_file()):
        files += 1
        try:
            with file.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    records += 1
                    try:
                        record = json.loads(line)
                        expected = LOB_KEYS if record.get("stream") == "bidask" else TICK_KEYS if record.get("stream") == "tick" else set()
                        if set(record) != expected:
                            violations += 1
                    except (json.JSONDecodeError, AttributeError):
                        violations += 1
        except OSError:
            violations += 1
    return {"area": "spool-schema", "files": files, "records": records, "violations": violations}


def purge_runtime(root: str | Path, dry_run: bool) -> int:
    base = Path(root)
    files = [path for path in base.rglob("*") if path.is_file()] if base.exists() else []
    if not dry_run:
        shutil.rmtree(base, ignore_errors=False)
        for name in ("shioaji/home", "shioaji/contracts", "collector", "crash", "tmp"):
            (base / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    return len(files)


def purge_spool(root: str | Path, dry_run: bool) -> int:
    base = Path(root)
    files = [path for path in base.rglob("*") if path.is_file()] if base.exists() else []
    if not dry_run:
        shutil.rmtree(base, ignore_errors=False)
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return len(files)

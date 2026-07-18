from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from lob_recorder.privacy import SENSITIVE_VALUE_PATTERNS


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

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Capacity:
    bytes_percent: float
    inode_percent: float

    @property
    def used_percent(self) -> float:
        return max(self.bytes_percent, self.inode_percent)


def validate_storage(root: str | Path, mode: str, allow_test: bool = False) -> Path:
    path = Path(root)
    if not path.is_absolute() or path == Path("/"):
        raise RuntimeError("storage root must be a safe absolute path")
    if mode == "live" and allow_test:
        raise RuntimeError("test storage override is forbidden in live mode")
    marker = path / ".lob-storage-root"
    if not path.is_dir() or not marker.is_file():
        raise RuntimeError("storage root or marker is unavailable")
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        raise RuntimeError("storage root is not writable")
    if mode == "live" and not os.path.ismount(path):
        raise RuntimeError("live storage root is not a mount point")
    if mode != "live" and not allow_test and not os.path.ismount(path):
        raise RuntimeError("fixture storage requires explicit test override")
    return path


def capacity(root: str | Path) -> Capacity:
    usage = shutil.disk_usage(root)
    bytes_percent = 100.0 * usage.used / usage.total
    stats = os.statvfs(root)
    inode_total = stats.f_files
    inode_percent = 0.0 if not inode_total else 100.0 * (inode_total - stats.f_ffree) / inode_total
    return Capacity(bytes_percent, inode_percent)


def ensure_layout(root: str | Path) -> None:
    path = Path(root)
    for name in ("clickhouse", "parquet", "spool", "backup", "private-runtime"):
        (path / name).mkdir(parents=True, exist_ok=True, mode=0o700)

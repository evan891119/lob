from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Iterator


class DurableSpool:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.pending = self.root / "pending.jsonl"

    def append(self, records: Iterable[dict]) -> int:
        count = 0
        descriptor = os.open(self.pending, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n")
                count += 1
            handle.flush()
            os.fsync(handle.fileno())
        return count

    def records(self) -> Iterator[dict]:
        if not self.pending.exists():
            return
        with self.pending.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)

    def clear(self) -> None:
        if self.pending.exists():
            self.pending.unlink()

    def replay(self, write_batch, batch_size: int) -> int:
        if not self.pending.exists():
            return 0
        replaying = self.root / "replaying.jsonl"
        self.pending.replace(replaying)
        total = 0
        batch: list[dict] = []
        try:
            with replaying.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    batch.append(json.loads(line))
                    if len(batch) >= batch_size:
                        write_batch(batch)
                        total += len(batch)
                        batch = []
                if batch:
                    write_batch(batch)
                    total += len(batch)
            replaying.unlink()
            return total
        except Exception:
            if replaying.exists() and not self.pending.exists():
                replaying.replace(self.pending)
            raise

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Iterable, Iterator


class DurableSpool:
    def __init__(self, root: str | Path, filename: str = "pending.jsonl"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.pending = self.root / filename
        self.replaying = self.root / f"{filename}.replaying"
        self._lock = threading.Lock()
        self._recover_interrupted_replay()

    def _recover_interrupted_replay(self) -> None:
        if not self.replaying.exists():
            return
        recovered = self.root / f"{self.pending.name}.recovered"
        with recovered.open("wb") as output:
            with self.replaying.open("rb") as source:
                shutil.copyfileobj(source, output)
            if self.pending.exists():
                with self.pending.open("rb") as source:
                    shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        recovered.replace(self.pending)
        self.replaying.unlink()

    def append(self, records: Iterable[dict]) -> int:
        with self._lock:
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
        with self._lock:
            if not self.pending.exists():
                return
            values = [json.loads(line) for line in self.pending.read_text(encoding="utf-8").splitlines() if line.strip()]
        yield from values

    def clear(self) -> None:
        with self._lock:
            if self.pending.exists():
                self.pending.unlink()
            if self.replaying.exists():
                self.replaying.unlink()

    def replay(self, write_batch, batch_size: int) -> int:
        with self._lock:
            if not self.pending.exists():
                return 0
            self.pending.replace(self.replaying)
            total = 0
            committed_offset = 0
            batch: list[dict] = []
            try:
                with self.replaying.open("r", encoding="utf-8") as handle:
                    while True:
                        line = handle.readline()
                        if not line:
                            break
                        next_offset = handle.tell()
                        if not line.strip():
                            committed_offset = next_offset
                            continue
                        batch.append(json.loads(line))
                        if len(batch) >= batch_size:
                            write_batch(batch)
                            total += len(batch)
                            batch = []
                            committed_offset = next_offset
                    if batch:
                        write_batch(batch)
                        total += len(batch)
                        committed_offset = handle.tell()
                self.replaying.unlink()
                return total
            except Exception:
                self._restore_uncommitted(committed_offset)
                raise

    def _restore_uncommitted(self, committed_offset: int) -> None:
        recovery = self.root / f"{self.pending.name}.recovery"
        with self.replaying.open("rb") as source, recovery.open("wb") as output:
            source.seek(committed_offset)
            shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        recovery.replace(self.pending)
        self.replaying.unlink()

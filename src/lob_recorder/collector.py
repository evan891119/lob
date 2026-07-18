from __future__ import annotations

import queue
import signal
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from lob_recorder.models import normalize
from lob_recorder.privacy import JsonLogger, correlation_id
from lob_recorder.spool import DurableSpool
from lob_recorder.storage import capacity


@dataclass(slots=True)
class Counters:
    received: int = 0
    written: int = 0
    spooled: int = 0
    replayed: int = 0
    dropped: int = 0


class Collector:
    def __init__(self, sink, spool_root: str | Path, log_path: str | Path, health_path: str | Path,
                 queue_size: int = 20_000, batch_size: int = 1_000, flush_ms: int = 250,
                 storage_root: str | Path | None = None, warn_percent: float = 80, stop_percent: float = 90):
        if not 0 < warn_percent < stop_percent <= 99:
            raise ValueError("capacity thresholds must satisfy 0 < warn < stop <= 99")
        self.sink = sink
        self.spool = DurableSpool(spool_root)
        self.logger = JsonLogger(log_path)
        self.health_path = Path(health_path)
        self.queue: queue.Queue[dict] = queue.Queue(maxsize=queue_size)
        self.batch_size = batch_size
        self.flush_seconds = flush_ms / 1000
        self.storage_root = Path(storage_root) if storage_root else None
        self.warn_percent = warn_percent
        self.stop_percent = stop_percent
        self.session_id = str(uuid.uuid4())
        self.started_at = datetime.now().astimezone().isoformat()
        self.counters = Counters()
        self._sequence = 0
        self._stop = threading.Event()
        self._next_capacity_check = 0.0
        self._last_capacity_level = "ok"
        self._worker = threading.Thread(target=self._work, name="batch-writer", daemon=True)

    def start(self) -> None:
        self._worker.start()
        self._write_health("running")
        self.logger.write("collector_started", session_id=self.session_id, status="running")
        self._session_record("running")

    def emit(self, raw: dict[str, Any]) -> None:
        if self._stop.is_set():
            return
        self._sequence += 1
        try:
            event = normalize(raw, self.session_id, self._sequence).to_record()
            self.queue.put_nowait(event)
            self.counters.received += 1
        except queue.Full:
            self.counters.dropped += 1
            self.logger.write("queue_overflow", level="warning", session_id=self.session_id, category="queue", count=1)
            self._gap_record("queue_overflow", 1)

    def stop(self, reason: str = "graceful_stop") -> None:
        self._stop.set()
        self._worker.join(timeout=30)
        self._write_health("stopped")
        self.logger.write("collector_stopped", session_id=self.session_id, status=reason, count=self.counters.written)
        self._session_record(reason, ended=True)

    def record_gap(self, category: str, affected_count: int = 0) -> None:
        self.logger.write("capture_gap", level="warning", session_id=self.session_id, category=category, count=affected_count)
        self._gap_record(category, affected_count)

    def _work(self) -> None:
        try:
            self.counters.replayed += self.spool.replay(self.sink.write, self.batch_size)
        except Exception as exc:
            self.logger.write("spool_replay_failed", level="warning", category=type(exc).__name__, correlation_id=correlation_id(exc))
        batch: list[dict] = []
        deadline = time.monotonic() + self.flush_seconds
        while not self._stop.is_set() or not self.queue.empty():
            timeout = max(0.0, deadline - time.monotonic())
            try:
                batch.append(self.queue.get(timeout=min(timeout, 0.1)))
            except queue.Empty:
                pass
            if batch and (len(batch) >= self.batch_size or time.monotonic() >= deadline or (self._stop.is_set() and self.queue.empty())):
                self._flush(batch)
                batch = []
                deadline = time.monotonic() + self.flush_seconds
            elif not batch and time.monotonic() >= deadline:
                deadline = time.monotonic() + self.flush_seconds
            self._check_capacity()

    def _flush(self, batch: list[dict]) -> None:
        try:
            self.sink.write(batch)
            self.counters.written += len(batch)
        except Exception as exc:
            self.counters.spooled += self.spool.append(batch)
            self.logger.write("database_write_failed", level="warning", category=type(exc).__name__, correlation_id=correlation_id(exc), count=len(batch))
            self._gap_record("database_failure", len(batch), correlation_id(exc))

    def _check_capacity(self) -> None:
        if not self.storage_root:
            return
        now = time.monotonic()
        if now < self._next_capacity_check:
            return
        self._next_capacity_check = now + 30
        used = capacity(self.storage_root).used_percent
        if used >= self.stop_percent:
            if self._last_capacity_level != "stop":
                self.logger.write("disk_capacity_stop", level="error", category="capacity", percent=round(used, 2))
                self._gap_record("disk_capacity", 0)
            self._last_capacity_level = "stop"
            self._stop.set()
        elif used >= self.warn_percent:
            if self._last_capacity_level != "warning":
                self.logger.write("disk_capacity_warning", level="warning", category="capacity", percent=round(used, 2))
            self._last_capacity_level = "warning"
        else:
            self._last_capacity_level = "ok"

    def _write_health(self, status: str) -> None:
        import json
        self.health_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.health_path.write_text(json.dumps({"status": status, "updated_at": datetime.now().astimezone().isoformat()}), encoding="utf-8")

    def _session_record(self, status: str, ended: bool = False) -> None:
        now = datetime.now().astimezone().isoformat()
        record = {
            "session_id": self.session_id, "started_at": self.started_at, "ended_at": now if ended else None,
            "simulation": True, "status": status, "enabled_symbols": 0,
            "received": self.counters.received, "written": self.counters.written,
            "spooled": self.counters.spooled, "replayed": self.counters.replayed,
            "dropped": self.counters.dropped, "reconnects": 0,
        }
        try:
            self.sink.session(record)
        except Exception:
            pass

    def _gap_record(self, category: str, affected_count: int, cid: str = "000000000000") -> None:
        try:
            self.sink.gap({
                "gap_id": str(uuid.uuid4()), "session_id": self.session_id,
                "started_at": datetime.now().astimezone().isoformat(), "ended_at": None,
                "category": category, "correlation_id": cid[:12].ljust(12, "0"), "affected_count": affected_count,
            })
        except Exception:
            pass


def install_signal_handlers(stop_callback) -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda _signum, _frame: stop_callback())

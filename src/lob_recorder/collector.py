from __future__ import annotations

import queue
import resource
import signal
import sys
import threading
import time
import uuid
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from lob_recorder.models import TAIPEI, normalize
from lob_recorder.privacy import JsonLogger, correlation_id
from lob_recorder.spool import DurableSpool
from lob_recorder.storage import Capacity, capacity


@dataclass(slots=True)
class Counters:
    received: int = 0
    written: int = 0
    spooled: int = 0
    replayed: int = 0
    dropped: int = 0
    notice_dropped: int = 0
    queue_high_water: int = 0
    batch_count: int = 0
    batch_insert_ms_total: float = 0.0
    batch_insert_ms_max: float = 0.0
    callback_latency_ms_max: float = 0.0
    clock_anomalies: int = 0


@dataclass(frozen=True, slots=True)
class ProcessResources:
    cpu_seconds: float
    max_rss_bytes: int


def process_resources() -> ProcessResources:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # Linux reports ru_maxrss in KiB; macOS reports bytes. The deployed
    # collector is Linux, while this branch keeps host unit tests portable.
    rss_multiplier = 1 if sys.platform == "darwin" else 1_024
    return ProcessResources(
        cpu_seconds=float(usage.ru_utime + usage.ru_stime),
        max_rss_bytes=max(0, int(usage.ru_maxrss * rss_multiplier)),
    )


class Collector:
    def __init__(
        self,
        sink,
        spool_root: str | Path,
        log_path: str | Path,
        health_path: str | Path,
        queue_size: int = 20_000,
        batch_size: int = 1_000,
        flush_ms: int = 250,
        storage_root: str | Path | None = None,
        warn_percent: float = 80,
        stop_percent: float = 90,
        symbols: Iterable[str] = (),
        simulation: bool = True,
        replay_interval: float = 5.0,
        capacity_probe: Callable[[str | Path], Capacity] = capacity,
        resource_probe: Callable[[], ProcessResources] = process_resources,
        untrusted_log_path: str | Path | None = None,
        untrusted_log_max_bytes: int = 20_000_000,
    ):
        if not 0 < warn_percent < stop_percent <= 99:
            raise ValueError("capacity thresholds must satisfy 0 < warn < stop <= 99")
        if queue_size < 1 or batch_size < 1 or flush_ms < 1:
            raise ValueError("queue and batch settings must be positive")
        if untrusted_log_max_bytes < 1_000_000:
            raise ValueError("untrusted log limit must be at least 1 MB")
        self.sink = sink
        self.spool = DurableSpool(spool_root)
        log_path = Path(log_path)
        self.audit_spool = DurableSpool(log_path.parent / "audit-spool", "audit.jsonl")
        self.logger = JsonLogger(log_path)
        self.health_path = Path(health_path)
        self.queue: queue.Queue[dict] = queue.Queue(maxsize=queue_size)
        self.notices: queue.Queue[dict] = queue.Queue(maxsize=max(1_024, min(queue_size, 20_000)))
        self.queue_size = queue_size
        self.batch_size = batch_size
        self.flush_seconds = flush_ms / 1000
        self.replay_interval = replay_interval
        self.storage_root = Path(storage_root) if storage_root else None
        self.warn_percent = warn_percent
        self.stop_percent = stop_percent
        self.capacity_probe = capacity_probe
        self.resource_probe = resource_probe
        self.untrusted_log_path = Path(untrusted_log_path) if untrusted_log_path else None
        self.untrusted_log_max_bytes = untrusted_log_max_bytes
        self.symbols = sorted(set(symbols))
        self.simulation = simulation
        self.session_id = str(uuid.uuid4())
        self.started_at = self._now()
        self.counters = Counters()
        self.subscriptions_active = 0
        self.subscriptions_failed = 0
        self.subscription_results: list[str] = []
        self.reconnects = 0
        self._sequence = 0
        self._stop = threading.Event()
        self._stop_reason = "graceful_stop"
        self._next_capacity_check = 0.0
        self._next_replay = 0.0
        self._next_health = 0.0
        self._next_runtime_log_check = 0.0
        self._last_capacity_level = "ok"
        self._capacity: Capacity | None = None
        self._health_status = "created"
        self._health_lock = threading.Lock()
        self._resource_lock = threading.Lock()
        try:
            initial_resources = self.resource_probe()
        except (OSError, ValueError):
            initial_resources = ProcessResources(0.0, 0)
        self._resource_cpu_start = initial_resources.cpu_seconds
        self._process_cpu_seconds = 0.0
        self._process_max_rss_bytes = initial_resources.max_rss_bytes
        self._open_gaps: dict[str, dict] = {}
        self._worker = threading.Thread(target=self._work, name="batch-writer", daemon=True)

    @staticmethod
    def _now() -> str:
        return datetime.now(TAIPEI).isoformat()

    def start(self, status: str = "running") -> None:
        self._worker.start()
        self._health_status = status
        self._write_health(status)
        self.logger.write("collector_started", session_id=self.session_id, status=status)
        self._session_record(status)

    def emit(self, raw: dict[str, Any]) -> None:
        """Shioaji callback path: normalize and enqueue only; no file or database I/O."""
        if self._stop.is_set():
            return
        self._sequence += 1
        try:
            event = normalize(raw, self.session_id, self._sequence).to_record()
            event_time = datetime.fromisoformat(event["event_ts"])
            received_time = datetime.fromisoformat(event["received_ts"])
            latency_ms = (received_time - event_time).total_seconds() * 1_000
            self.counters.callback_latency_ms_max = max(self.counters.callback_latency_ms_max, latency_ms)
            if latency_ms < -2_000:
                self.counters.clock_anomalies += 1
                self._notice("clock_anomaly", category="clock", count=1)
            self.queue.put_nowait(event)
            self.counters.received += 1
            self.counters.queue_high_water = max(self.counters.queue_high_water, self.queue.qsize())
        except queue.Full:
            self.counters.dropped += 1
            self._notice("queue_overflow", category="queue", count=1)
        except Exception as exc:
            self.counters.dropped += 1
            self._notice("invalid_market_event", category=type(exc).__name__, correlation_id=correlation_id(exc), count=1)

    def _notice(self, event: str, **fields: Any) -> None:
        try:
            self.notices.put_nowait({"event": event, **fields})
        except queue.Full:
            self.counters.notice_dropped += 1

    def source_event(self, event_code: int) -> None:
        if event_code in {12, 13}:
            self._notice("source_event", category=str(event_code))

    def record_gap(self, category: str, affected_count: int = 0) -> None:
        self._notice("capture_gap", category=category, count=affected_count)

    def set_subscriptions(self, active: int, failed: int, results: Iterable[str] = ()) -> None:
        self.subscriptions_active = active
        self.subscriptions_failed = failed
        self.subscription_results = sorted(set(results))
        if not active:
            status = "subscription_failed"
            self._health_status = status
        elif failed:
            status = "degraded"
            self._health_status = status
        else:
            status = "active"
            self._health_status = "running"
        self._write_health(self._health_status)
        self._session_record(status)

    def set_status(self, status: str) -> None:
        self._health_status = status
        self._write_health(status)
        self._session_record(status)

    def stop_requested(self) -> bool:
        return self._stop.is_set()

    @property
    def stop_reason(self) -> str:
        return self._stop_reason

    def stop(self, reason: str | None = None) -> None:
        if reason:
            self._stop_reason = reason
        self._stop.set()
        self._worker.join(timeout=30)
        if self._worker.is_alive():
            self.logger.write("worker_shutdown_timeout", level="error", session_id=self.session_id, category="shutdown")
            self._stop_reason = "shutdown_timeout"
        for category in list(self._open_gaps):
            self._end_gap(category)
        self._health_status = "stopped"
        self._write_health(self._health_status)
        self.logger.write("collector_stopped", session_id=self.session_id, status=self._stop_reason, count=self.counters.written)
        self._session_record(self._stop_reason, ended=True)

    def _work(self) -> None:
        self._replay_spools()
        batch: list[dict] = []
        deadline = time.monotonic() + self.flush_seconds
        while not self._stop.is_set() or not self.queue.empty() or not self.notices.empty():
            self._drain_notices()
            timeout = max(0.0, deadline - time.monotonic())
            try:
                batch.append(self.queue.get(timeout=min(timeout, 0.1)))
            except queue.Empty:
                pass
            if batch and (
                len(batch) >= self.batch_size
                or time.monotonic() >= deadline
                or (self._stop.is_set() and self.queue.empty())
            ):
                self._flush(batch)
                batch = []
                deadline = time.monotonic() + self.flush_seconds
            elif not batch and time.monotonic() >= deadline:
                deadline = time.monotonic() + self.flush_seconds
            now = time.monotonic()
            if now >= self._next_replay:
                self._next_replay = now + self.replay_interval
                self._replay_spools()
            self._check_capacity(now)
            self._check_runtime_log(now)
            if now >= self._next_health:
                self._next_health = now + 10
                self._write_health("stopping" if self._stop.is_set() else self._health_status)
                self._session_record("stopping" if self._stop.is_set() else self._health_status)
        self._drain_notices()
        self._replay_spools()

    def _drain_notices(self) -> None:
        aggregated: dict[tuple[str, str, str], int] = {}
        while True:
            try:
                notice = self.notices.get_nowait()
            except queue.Empty:
                break
            event = notice.pop("event")
            if event == "source_event":
                code = int(notice["category"])
                if code == 12:
                    self._begin_gap("connection_down")
                    self.logger.write("connection_reconnecting", level="warning", category="connection")
                elif code == 13:
                    self.reconnects += 1
                    self._end_gap("connection_down")
                    self.logger.write("connection_reconnected", category="connection", count=self.reconnects)
                    self._session_record("active")
            else:
                category = str(notice.get("category", event))
                count = int(notice.get("count", 0))
                cid = str(notice.get("correlation_id", "000000000000"))
                key = (event, category, cid)
                aggregated[key] = aggregated.get(key, 0) + count
        for (event, category, cid), count in aggregated.items():
            self.logger.write(event, level="warning", session_id=self.session_id, category=category, count=count, correlation_id=cid)
            self._instant_gap(event, count, cid)

    def _flush(self, batch: list[dict]) -> None:
        started = time.monotonic()
        try:
            self.sink.write(batch)
            elapsed_ms = (time.monotonic() - started) * 1_000
            self.counters.written += len(batch)
            self.counters.batch_count += 1
            self.counters.batch_insert_ms_total += elapsed_ms
            self.counters.batch_insert_ms_max = max(self.counters.batch_insert_ms_max, elapsed_ms)
            # A recovered direct write must not close the gap before the older
            # open-gap audit envelope and market spool have been replayed.
            self._replay_spools()
        except Exception as exc:
            self.counters.spooled += self.spool.append(batch)
            cid = correlation_id(exc)
            self.logger.write("database_write_failed", level="warning", category=type(exc).__name__, correlation_id=cid, count=len(batch))
            self._begin_gap("database_failure", len(batch), cid)

    def _replay_spools(self) -> None:
        try:
            self.audit_spool.replay(self._write_audit_batch, 1)
        except Exception as exc:
            self.logger.write("audit_replay_failed", level="warning", category=type(exc).__name__, correlation_id=correlation_id(exc))
            return
        try:
            replayed = self.spool.replay(self.sink.write, self.batch_size)
            if replayed:
                self.counters.replayed += replayed
                self.logger.write("spool_replayed", count=replayed, category="market")
            self._end_gap("database_failure")
        except Exception as exc:
            self.logger.write("spool_replay_failed", level="warning", category=type(exc).__name__, correlation_id=correlation_id(exc))

    def _write_audit_batch(self, envelopes: list[dict]) -> None:
        for envelope in envelopes:
            if envelope["kind"] == "session":
                self.sink.session(envelope["record"])
            elif envelope["kind"] == "gap":
                self.sink.gap(envelope["record"])
            else:
                raise ValueError("unsupported audit envelope")

    def _persist_audit(self, kind: str, record: dict) -> None:
        try:
            self._write_audit_batch([{"kind": kind, "record": record}])
        except Exception:
            self.audit_spool.append([{"kind": kind, "record": record}])

    def _check_capacity(self, now: float) -> None:
        if not self.storage_root or now < self._next_capacity_check:
            return
        self._next_capacity_check = now + 30
        try:
            measured = self.capacity_probe(self.storage_root)
        except OSError as exc:
            cid = correlation_id(exc)
            self.logger.write(
                "storage_capacity_check_failed", level="error", category=type(exc).__name__,
                correlation_id=cid,
            )
            self._begin_gap("storage_unavailable", 0, cid)
            self._stop_reason = "storage_unavailable"
            self._health_status = "stopping_storage_unavailable"
            self._write_health(self._health_status)
            self._stop.set()
            return
        self._capacity = measured
        used = measured.used_percent
        if used >= self.stop_percent:
            if self._last_capacity_level != "stop":
                self.logger.write("disk_capacity_stop", level="error", category="capacity", percent=round(used, 2))
                self._begin_gap("disk_capacity", 0)
            self._last_capacity_level = "stop"
            self._stop_reason = "disk_capacity"
            self._health_status = "stopping_capacity"
            self._write_health(self._health_status)
            self._stop.set()
        elif used >= self.warn_percent:
            if self._last_capacity_level != "warning":
                self.logger.write("disk_capacity_warning", level="warning", category="capacity", percent=round(used, 2))
            self._last_capacity_level = "warning"
        else:
            self._last_capacity_level = "ok"

    def _check_runtime_log(self, now: float) -> None:
        if not self.untrusted_log_path or now < self._next_runtime_log_check:
            return
        self._next_runtime_log_check = now + 60
        try:
            if self.untrusted_log_path.stat().st_size > self.untrusted_log_max_bytes:
                os.truncate(self.untrusted_log_path, 0)
                self.logger.write("untrusted_runtime_log_truncated", category="privacy")
        except FileNotFoundError:
            return
        except OSError as exc:
            self.logger.write("untrusted_runtime_log_limit_failed", level="warning", category=type(exc).__name__, correlation_id=correlation_id(exc))

    def _write_health(self, status: str) -> None:
        import json

        with self._health_lock:
            resources = self._sample_process_resources()
            self.health_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            payload = {
                "status": status,
                "updated_at": self._now(),
                "session_id": self.session_id,
                "queue_size": self.queue.qsize(),
                "queue_capacity": self.queue_size,
                "storage_capacity": None if self._capacity is None else {
                    "bytes_percent": round(self._capacity.bytes_percent, 3),
                    "inode_percent": round(self._capacity.inode_percent, 3),
                    "used_percent": round(self._capacity.used_percent, 3),
                },
                "subscriptions_active": self.subscriptions_active,
                "subscriptions_failed": self.subscriptions_failed,
                "subscription_results": self.subscription_results,
                "counters": asdict(self.counters),
                "process_resources": {
                    "cpu_seconds": resources.cpu_seconds,
                    "max_rss_bytes": resources.max_rss_bytes,
                },
            }
            temporary = self.health_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self.health_path)

    def _session_record(self, status: str, ended: bool = False) -> None:
        resources = self._sample_process_resources()
        record = {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": self._now() if ended else None,
            "simulation": self.simulation,
            "status": status,
            "symbols": self.symbols,
            "enabled_symbols": len(self.symbols),
            "subscriptions_active": self.subscriptions_active,
            "subscriptions_failed": self.subscriptions_failed,
            "subscription_results": self.subscription_results,
            "received": self.counters.received,
            "written": self.counters.written,
            "spooled": self.counters.spooled,
            "replayed": self.counters.replayed,
            "dropped": self.counters.dropped,
            "notice_dropped": self.counters.notice_dropped,
            "reconnects": self.reconnects,
            "queue_capacity": self.queue_size,
            "queue_high_water": self.counters.queue_high_water,
            "capacity_bytes_percent": None if self._capacity is None else round(self._capacity.bytes_percent, 3),
            "capacity_inode_percent": None if self._capacity is None else round(self._capacity.inode_percent, 3),
            "capacity_used_percent": None if self._capacity is None else round(self._capacity.used_percent, 3),
            "batch_count": self.counters.batch_count,
            "batch_insert_ms_total": round(self.counters.batch_insert_ms_total, 3),
            "batch_insert_ms_max": round(self.counters.batch_insert_ms_max, 3),
            "callback_latency_ms_max": round(self.counters.callback_latency_ms_max, 3),
            "clock_anomalies": self.counters.clock_anomalies,
            "process_cpu_seconds": resources.cpu_seconds,
            "process_max_rss_bytes": resources.max_rss_bytes,
        }
        self._persist_audit("session", record)

    def _sample_process_resources(self) -> ProcessResources:
        with self._resource_lock:
            try:
                measured = self.resource_probe()
            except (OSError, ValueError):
                return ProcessResources(
                    round(self._process_cpu_seconds, 3), self._process_max_rss_bytes
                )
            self._process_cpu_seconds = max(
                self._process_cpu_seconds,
                measured.cpu_seconds - self._resource_cpu_start,
            )
            self._process_max_rss_bytes = max(
                self._process_max_rss_bytes, measured.max_rss_bytes
            )
            return ProcessResources(
                round(self._process_cpu_seconds, 3), self._process_max_rss_bytes
            )

    def _gap_base(self, category: str, affected_count: int, cid: str) -> dict:
        return {
            "gap_id": str(uuid.uuid4()),
            "session_id": self.session_id,
            "started_at": self._now(),
            "ended_at": None,
            "category": category,
            "correlation_id": cid[:12].ljust(12, "0"),
            "affected_count": affected_count,
        }

    def _begin_gap(self, category: str, affected_count: int = 0, cid: str = "000000000000") -> None:
        if category in self._open_gaps:
            self._open_gaps[category]["affected_count"] += affected_count
            return
        record = self._gap_base(category, affected_count, cid)
        self._open_gaps[category] = record
        self._persist_audit("gap", record)

    def _end_gap(self, category: str) -> None:
        record = self._open_gaps.pop(category, None)
        if record is None:
            return
        closed = {**record, "ended_at": self._now()}
        self._persist_audit("gap", closed)

    def _instant_gap(self, category: str, affected_count: int = 0, cid: str = "000000000000") -> None:
        record = self._gap_base(category, affected_count, cid)
        record["ended_at"] = record["started_at"]
        self._persist_audit("gap", record)


def install_signal_handlers(stop_callback) -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda _signum, _frame: stop_callback())

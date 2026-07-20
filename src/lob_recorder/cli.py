from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from lob_recorder.acceptance import collect_acceptance_report, write_acceptance_report
from lob_recorder.collector import Collector, install_signal_handlers
from lob_recorder.config import load_instruments
from lob_recorder.credentials import load_credentials
from lob_recorder.exporter import export_clickhouse, export_day
from lob_recorder.pilot import collect_report
from lob_recorder.privacy import correlation_id
from lob_recorder.privacy_tools import inspect_spool_schema, inventory, purge_runtime, purge_spool
from lob_recorder.quality import inspect, inspect_parquet
from lob_recorder.sinks import ClickHouseSink, JsonlSink, read_jsonl
from lob_recorder.sources.fixture import FixtureSource
from lob_recorder.storage import STORAGE_MARKER, ensure_layout, usable_bytes, validate_storage


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def wait_for_stop(signal_event: threading.Event, collector: Collector, timeout: float | None = None) -> bool:
    deadline = None if timeout is None else time.monotonic() + timeout
    while not signal_event.wait(0.25):
        if collector.stop_requested():
            return True
        if deadline is not None and time.monotonic() >= deadline:
            return False
    return True


def build_collector(sink, storage_root: Path, symbols=()) -> Collector:
    private = Path(env("LOB_PRIVATE_ROOT", str(storage_root / "private-runtime")))
    return Collector(
        sink=sink,
        spool_root=env("LOB_SPOOL_ROOT", str(storage_root / "spool")),
        log_path=private / "collector/collector.log",
        health_path=private / "collector/health.json",
        queue_size=int(env("LOB_QUEUE_SIZE", "20000")),
        batch_size=int(env("LOB_BATCH_SIZE", "1000")),
        flush_ms=int(env("LOB_FLUSH_MS", "250")),
        storage_root=storage_root,
        warn_percent=float(env("LOB_WARN_PERCENT", "80")),
        stop_percent=float(env("LOB_STOP_PERCENT", "90")),
        symbols=symbols,
        simulation=True,
        untrusted_log_path=os.environ.get("SJ_LOG_PATH"),
        untrusted_log_max_bytes=int(env("LOB_SHIOAJI_LOG_MAX_BYTES", "20000000")),
    )


def run_fixture(fixture: str, output: str, keep_running: bool = False, sink=None) -> dict:
    configured_root = os.environ.get("LOB_STORAGE_ROOT")
    storage_root = Path(configured_root or tempfile.mkdtemp(prefix="lob-fixture-"))
    allow_test = not configured_root or env("LOB_ALLOW_TEST_STORAGE", "false").lower() == "true"
    storage_root.mkdir(parents=True, exist_ok=True)
    if not configured_root:
        (storage_root / ".lob-storage-root").write_text(STORAGE_MARKER + "\n", encoding="ascii")
    ensure_layout(storage_root)
    validate_storage(storage_root, "fixture", allow_test=allow_test)
    raw_events = list(FixtureSource(fixture).events())
    symbols = sorted({str(raw.get("symbol") or raw.get("code")) for raw in raw_events if raw.get("symbol") or raw.get("code")})
    collector = build_collector(sink or JsonlSink(output), storage_root, symbols=symbols)
    collector.start()
    for raw in raw_events:
        collector.emit(raw)
    if keep_running:
        stopped = threading.Event()
        install_signal_handlers(stopped.set)
        repeat_seconds = float(env("LOB_FIXTURE_REPEAT_SECONDS", "0"))
        if repeat_seconds < 0:
            raise ValueError("fixture repeat interval may not be negative")
        if repeat_seconds:
            while not wait_for_stop(stopped, collector, repeat_seconds):
                for raw in raw_events:
                    collector.emit(raw)
        else:
            wait_for_stop(stopped, collector)
    collector.stop(collector.stop_reason)
    from dataclasses import asdict
    return asdict(collector.counters)


def run_live() -> None:
    storage_root = validate_storage(env("LOB_STORAGE_ROOT", "/var/lib/lob"), "live", allow_test=False)
    instruments = load_instruments(env("LOB_CONFIG", "/app/config/instruments.yaml"))
    credentials = load_credentials(env("LOB_CREDENTIAL_FILE", "/run/secrets/shioaji_credentials"))
    sink = ClickHouseSink(env("LOB_CLICKHOUSE_HOST", "clickhouse"))
    collector = build_collector(sink, storage_root, symbols=[instrument.code for instrument in instruments])
    from lob_recorder.sources.shioaji_source import NoActiveSubscriptionError, ShioajiSource
    stopped = threading.Event()
    install_signal_handlers(stopped.set)
    collector.start(status="starting")
    source = None
    try:
        retry_seconds = 60
        while not stopped.is_set():
            source = ShioajiSource(
                credentials,
                instruments,
                collector.emit,
                on_disconnect=lambda *_args: collector.source_event(12),
                on_event=collector.source_event,
            )
            try:
                results = source.connect()
                for result in results:
                    collector.logger.write(
                        "subscription_result", symbol=result.code, stream=result.stream,
                        status=result.category, exchange=result.exchange,
                        security_type=result.security_type, resolved_code=result.resolved_code,
                        target_code=result.target_code,
                    )
                active = sum(result.active for result in results)
                failed = len(results) - active
                collector.set_subscriptions(active, failed, [result.descriptor() for result in results])
                if active == 0:
                    raise NoActiveSubscriptionError("no market-data subscription became active")
                collector.logger.write("subscriptions_ready", status="active", count=active)
                break
            except Exception as exc:
                collector.logger.write("live_start_failed", level="error", category=type(exc).__name__, correlation_id=correlation_id(exc))
                collector.record_gap("live_start_failure")
                collector.set_status("retrying")
                source.close()
                source = None
                if wait_for_stop(stopped, collector, retry_seconds):
                    return
                retry_seconds = min(retry_seconds * 2, 900)
        wait_for_stop(stopped, collector)
    finally:
        if source is not None:
            source.close()
        collector.stop(collector.stop_reason if collector.stop_requested() else "graceful_stop")


def command_run(_args) -> None:
    mode = env("LOB_MODE", "fixture")
    if mode == "live":
        run_live()
    elif mode == "fixture":
        root = Path(env("LOB_STORAGE_ROOT", "/var/lib/lob"))
        sink = ClickHouseSink(env("LOB_CLICKHOUSE_HOST", "clickhouse"))
        run_fixture(env("LOB_FIXTURE", "/app/fixtures/events.jsonl"), str(root / "private-runtime/collector/fixture-output.jsonl"), keep_running=True, sink=sink)
    else:
        raise SystemExit("LOB_MODE must be fixture or live")


def command_fixture(args) -> None:
    print(json.dumps(run_fixture(args.input, args.output), sort_keys=True))


def command_init_storage(args) -> None:
    root = Path(args.root)
    if not root.is_absolute() or root == Path("/"):
        raise SystemExit("refusing unsafe storage root")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    ensure_layout(root)
    (root / ".lob-storage-root").write_text(STORAGE_MARKER + "\n", encoding="ascii")
    print("storage layout initialized")


def command_health(args) -> None:
    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise SystemExit(1)
    try:
        updated = datetime.fromisoformat(data["updated_at"])
        age = (datetime.now(updated.tzinfo) - updated).total_seconds()
    except (KeyError, TypeError, ValueError):
        raise SystemExit(1)
    raise SystemExit(0 if data.get("status") == "running" and 0 <= age <= args.max_age else 1)


def command_privacy_list(args) -> None:
    for area, root in (("private-runtime", args.root), ("spool", args.spool_root)):
        if not root:
            continue
        for item in inventory(root):
            print(json.dumps({"area": area, **item}, sort_keys=True))
    if args.spool_root:
        print(json.dumps(inspect_spool_schema(args.spool_root), sort_keys=True))


def _confirmed(args, warning: str) -> bool:
    if args.dry_run:
        return True
    if not args.yes:
        answer = input(f"{warning} Type DELETE to continue: ")
        return answer == "DELETE"
    return True


def command_privacy_purge(args) -> None:
    if not any((args.runtime, args.spool, args.credentials, args.all_private)):
        raise SystemExit("select a purge scope")
    if not _confirmed(args, "Collector must be stopped before purge."):
        raise SystemExit("cancelled")
    count = 0
    if args.runtime or args.all_private:
        count += purge_runtime(args.runtime_root, args.dry_run)
    if args.spool:
        if not _confirmed(args, "Spool deletion permanently loses pending market data."):
            raise SystemExit("cancelled")
        count += purge_spool(args.spool_root, args.dry_run)
    if args.credentials or args.all_private:
        credential = Path(args.credential_file)
        if credential.exists():
            count += 1
            if not args.dry_run:
                credential.unlink()
    print(json.dumps({"files_selected": count, "dry_run": args.dry_run}, sort_keys=True))


def command_quality(args) -> None:
    result = inspect(read_jsonl(args.input), args.max_gap_seconds) if args.input else inspect_parquet(args.parquet, args.max_gap_seconds)
    print(json.dumps(result, sort_keys=True))


def command_export(args) -> None:
    if args.all_symbols:
        export_day(args.host, args.date, args.output)
    else:
        export_clickhouse(args.host, args.symbol, args.date, args.output)
    print("parquet export complete")


def command_pilot_report(args) -> None:
    storage_bytes = args.storage_total_bytes
    if storage_bytes is None:
        storage_bytes = usable_bytes(env("LOB_STORAGE_ROOT", "/var/lib/lob"))
    collect_report(args.host, args.output, storage_bytes)
    print("pilot report complete")


def command_acceptance_report(args) -> None:
    try:
        report = collect_acceptance_report(args.host, args.health_file, args.max_health_age)
        if args.output:
            write_acceptance_report(report, args.output)
            print("acceptance report complete")
        else:
            print(json.dumps(report, ensure_ascii=True, sort_keys=True, default=str))
    except Exception as exc:
        raise SystemExit(f"acceptance report failed: {type(exc).__name__}") from None


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="lob-recorder")
    sub = result.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run"); run.set_defaults(func=command_run)
    fixture = sub.add_parser("fixture"); fixture.add_argument("--input", required=True); fixture.add_argument("--output", required=True); fixture.set_defaults(func=command_fixture)
    init = sub.add_parser("init-storage"); init.add_argument("--root", required=True); init.set_defaults(func=command_init_storage)
    health = sub.add_parser("health"); health.add_argument("--file", required=True); health.add_argument("--max-age", type=float, default=60); health.set_defaults(func=command_health)
    listing = sub.add_parser("privacy-list"); listing.add_argument("--root", required=True); listing.add_argument("--spool-root"); listing.set_defaults(func=command_privacy_list)
    purge = sub.add_parser("privacy-purge")
    purge.add_argument("--runtime", action="store_true"); purge.add_argument("--spool", action="store_true")
    purge.add_argument("--credentials", action="store_true"); purge.add_argument("--all-private", action="store_true")
    purge.add_argument("--database-metadata", action="store_true", help="handled by scripts/privacy-purge using ClickHouse exec")
    purge.add_argument("--runtime-root", required=True); purge.add_argument("--spool-root", required=True); purge.add_argument("--credential-file", required=True)
    purge.add_argument("--dry-run", action="store_true"); purge.add_argument("--yes", action="store_true"); purge.set_defaults(func=command_privacy_purge)
    quality = sub.add_parser("quality"); quality_input = quality.add_mutually_exclusive_group(required=True); quality_input.add_argument("--input"); quality_input.add_argument("--parquet"); quality.add_argument("--max-gap-seconds", type=float, default=60.0); quality.set_defaults(func=command_quality)
    export = sub.add_parser("export"); export.add_argument("--host", default="clickhouse"); export_target = export.add_mutually_exclusive_group(required=True); export_target.add_argument("--symbol"); export_target.add_argument("--all-symbols", action="store_true"); export.add_argument("--date", required=True); export.add_argument("--output", required=True); export.set_defaults(func=command_export)
    pilot = sub.add_parser("pilot-report"); pilot.add_argument("--host", default="clickhouse"); pilot.add_argument("--output", required=True); pilot.add_argument("--storage-total-bytes", type=int); pilot.set_defaults(func=command_pilot_report)
    acceptance = sub.add_parser("acceptance-report"); acceptance.add_argument("--host", default="clickhouse"); acceptance.add_argument("--health-file", default="/var/lib/lob/private-runtime/collector/health.json"); acceptance.add_argument("--max-health-age", type=float, default=90); acceptance.add_argument("--output"); acceptance.set_defaults(func=command_acceptance_report)
    return result


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

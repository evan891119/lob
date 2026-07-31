from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from lob_recorder.models import TAIPEI


ALLOWED_SERVICES = {"clickhouse", "collector"}
SIZE_PATTERN = re.compile(
    r"^(?P<value>[0-9]+(?:\.[0-9]+)?)"
    r"(?P<unit>B|kB|KB|KiB|MB|MiB|GB|GiB|TB|TiB)$"
)
PERCENT_PATTERN = re.compile(r"^(?P<value>[0-9]+(?:\.[0-9]+)?)%$")
SIZE_MULTIPLIERS = {
    "B": 1,
    "kB": 1_000,
    "KB": 1_000,
    "KiB": 1_024,
    "MB": 1_000_000,
    "MiB": 1_048_576,
    "GB": 1_000_000_000,
    "GiB": 1_073_741_824,
    "TB": 1_000_000_000_000,
    "TiB": 1_099_511_627_776,
}


def _percent(value: str) -> float:
    match = PERCENT_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError("resource percentage is invalid")
    return float(match.group("value"))


def _bytes(value: str) -> int:
    match = SIZE_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError("resource size is invalid")
    return round(
        Decimal(match.group("value")) * SIZE_MULTIPLIERS[match.group("unit")]
    )


def parse_samples(lines: Iterable[str]) -> dict:
    samples: dict[str, list[dict[str, float | int]]] = {
        service: [] for service in sorted(ALLOWED_SERVICES)
    }
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) != 6 or parts[0] not in ALLOWED_SERVICES:
            raise ValueError("resource sample shape is invalid")
        service, cpu, memory_used, memory_limit, memory_percent, _sample_number = parts
        sample_number = int(_sample_number)
        if sample_number < 1:
            raise ValueError("resource sample number is invalid")
        samples[service].append({
            "sample": sample_number,
            "cpu_percent": _percent(cpu),
            "memory_used_bytes": _bytes(memory_used),
            "memory_limit_bytes": _bytes(memory_limit),
            "memory_percent": _percent(memory_percent),
        })

    if any(len(service_samples) < 3 for service_samples in samples.values()):
        raise ValueError("resource samples are incomplete")
    sample_numbers = {
        service: {int(row["sample"]) for row in service_samples}
        for service, service_samples in samples.items()
    }
    if (
        any(len(numbers) < 3 for numbers in sample_numbers.values())
        or len({tuple(sorted(numbers)) for numbers in sample_numbers.values()}) != 1
    ):
        raise ValueError("resource sample sets do not match")

    services = {}
    for service, service_samples in samples.items():
        cpu_values = [float(row["cpu_percent"]) for row in service_samples]
        memory_values = [int(row["memory_used_bytes"]) for row in service_samples]
        memory_percent_values = [
            float(row["memory_percent"]) for row in service_samples
        ]
        memory_limits = {int(row["memory_limit_bytes"]) for row in service_samples}
        if len(memory_limits) != 1 or next(iter(memory_limits), 0) <= 0:
            raise ValueError("resource memory limit changed during sampling")
        services[service] = {
            "samples": len(service_samples),
            "cpu_percent_min": round(min(cpu_values), 3),
            "cpu_percent_average": round(sum(cpu_values) / len(cpu_values), 3),
            "cpu_percent_max": round(max(cpu_values), 3),
            "memory_used_bytes_min": min(memory_values),
            "memory_used_bytes_average": round(
                sum(memory_values) / len(memory_values)
            ),
            "memory_used_bytes_max": max(memory_values),
            "memory_limit_bytes": memory_limits.pop(),
            "memory_percent_average": round(
                sum(memory_percent_values) / len(memory_percent_values), 3
            ),
            "memory_percent_max": round(max(memory_percent_values), 3),
        }

    return {
        "generated_at": datetime.now(TAIPEI).isoformat(),
        "measurement": {
            "source": "docker_stats_no_stream",
            "point_in_time_samples": True,
            "services_allowlisted": sorted(ALLOWED_SERVICES),
        },
        "services": services,
        "checks": {
            "both_required_services_sampled": set(services) == ALLOWED_SERVICES,
            "memory_limits_stable": True,
        },
    }


def write_resource_report(report: dict, output: str | Path) -> Path:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target

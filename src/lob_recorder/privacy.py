from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = re.compile(r"(?i)(api.?key|secret|token|password|passwd|person.?id|account.?id|broker.?id|authorization|username)")
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)(SJ_API_KEY|SJ_SEC_KEY)\s*[=:]\s*\S+"),
    re.compile(r"\b[A-Z][12]\d{8}\b"),
)
ALLOWED_LOG_FIELDS = {
    "event", "level", "correlation_id", "session_id", "stream", "symbol", "count",
    "category", "status", "percent", "exchange", "security_type", "resolved_code",
    "target_code",
}


def redact_text(value: Any) -> str:
    text = str(value)
    for pattern in SENSITIVE_VALUE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def correlation_id(exc: BaseException) -> str:
    del exc
    return uuid.uuid4().hex[:12]


def safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in ALLOWED_LOG_FIELDS or SENSITIVE_KEYS.search(key):
            continue
        result[key] = redact_text(value)
    return result


class JsonLogger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._logger = logging.getLogger(f"lob-recorder:{self.path}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        if not self._logger.handlers:
            handler = logging.handlers.RotatingFileHandler(self.path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)

    def write(self, event: str, level: str = "info", **fields: Any) -> None:
        payload = safe_fields({"event": event, "level": level, **fields})
        getattr(self._logger, level if level in {"info", "warning", "error"} else "info")(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        )


# logging.handlers is deliberately imported after defining the narrow surface.
import logging.handlers  # noqa: E402

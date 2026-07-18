from __future__ import annotations

import os
import stat
from pathlib import Path

EXPECTED = {"SJ_API_KEY", "SJ_SEC_KEY"}
PLACEHOLDER = "REPLACE_WITH_"


def load_credentials(path: str | Path) -> tuple[str, str]:
    file = Path(path)
    if not file.is_file():
        raise RuntimeError("credential file is unavailable")
    mode = stat.S_IMODE(file.stat().st_mode)
    is_container_secret = file.parent == Path("/run/secrets")
    if mode & 0o077 and not (is_container_secret and not mode & 0o022):
        raise RuntimeError("credential file permissions are too broad")
    values: dict[str, str] = {}
    for line in file.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in EXPECTED or not value:
            raise RuntimeError("credential file has an invalid schema")
        if key in values:
            raise RuntimeError("credential file contains a duplicate key")
        values[key] = value
    if set(values) != EXPECTED or any(value.startswith(PLACEHOLDER) for value in values.values()):
        raise RuntimeError("credential file is incomplete")
    return values["SJ_API_KEY"], values["SJ_SEC_KEY"]

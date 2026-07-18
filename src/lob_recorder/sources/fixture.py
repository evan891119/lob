from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator


class FixtureSource:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def events(self) -> Iterator[dict]:
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("fixture event must be an object")
                yield value

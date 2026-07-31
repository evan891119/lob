import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot-check"


class PilotWrapperTests(unittest.TestCase):
    def _run(self, start_date: str, end_date: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as folder:
            fake_bin = Path(folder) / "bin"
            fake_bin.mkdir()
            fake_id = fake_bin / "id"
            fake_id.write_text("#!/bin/sh\nprintf '0\\n'\n", encoding="utf-8")
            fake_id.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            return subprocess.run(
                [
                    "sh", str(SCRIPT), "/not-used", "/not-used",
                    start_date, end_date,
                ],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_rejects_non_iso_date_before_external_checks(self):
        result = self._run("2026/01/02", "2026-01-06")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pilot start date must use YYYY-MM-DD", result.stderr)

    def test_rejects_inverted_date_range_before_external_checks(self):
        result = self._run("2026-01-07", "2026-01-06")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pilot start date must be on or before end date", result.stderr)


if __name__ == "__main__":
    unittest.main()

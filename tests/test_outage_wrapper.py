import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "database-outage-drill"


class OutageWrapperTests(unittest.TestCase):
    def _run(self, duration: str, confirmation: str):
        with tempfile.TemporaryDirectory() as folder:
            fake_bin = Path(folder) / "bin"
            fake_bin.mkdir()
            fake_id = fake_bin / "id"
            fake_id.write_text("#!/bin/sh\nprintf '0\\n'\n", encoding="utf-8")
            fake_id.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            return subprocess.run(
                ["sh", str(SCRIPT), "/not-used", "/not-used", duration, confirmation],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_requires_explicit_database_outage_confirmation(self):
        result = self._run("60", "--not-confirmed")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit database outage confirmation is required", result.stderr)

    def test_rejects_outage_longer_than_safety_bound(self):
        result = self._run("901", "--confirm-database-outage")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outage duration must be between 30 and 900 seconds", result.stderr)


if __name__ == "__main__":
    unittest.main()

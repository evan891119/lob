import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "host-reboot-check"


class RebootWrapperTests(unittest.TestCase):
    def _run(self, action: str, confirmation: str):
        with tempfile.TemporaryDirectory() as folder:
            fake_bin = Path(folder) / "bin"
            fake_bin.mkdir()
            fake_id = fake_bin / "id"
            fake_id.write_text("#!/bin/sh\nprintf '0\\n'\n", encoding="utf-8")
            fake_id.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            return subprocess.run(
                ["sh", str(SCRIPT), action, "/not-used", "/not-used", confirmation],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_prepare_requires_explicit_confirmation(self):
        result = self._run("prepare", "--not-confirmed")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit reboot preparation confirmation is required", result.stderr)

    def test_verify_requires_explicit_after_reboot_confirmation(self):
        result = self._run("verify", "--not-confirmed")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit after-reboot confirmation is required", result.stderr)


if __name__ == "__main__":
    unittest.main()

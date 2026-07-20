import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acceptance-check"


class AcceptanceWrapperTests(unittest.TestCase):
    def _run(self, host_lines: list[str], stat_output: str = "10001:10001:600") -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_id = fake_bin / "id"
            fake_id.write_text("#!/bin/sh\nprintf '0\\n'\n", encoding="utf-8")
            fake_id.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            fake_stat = fake_bin / "stat"
            fake_stat.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"${FAKE_STAT_OUTPUT:-10001:10001:600}\"\n",
                encoding="utf-8",
            )
            fake_stat.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            fake_mountpoint = fake_bin / "mountpoint"
            fake_mountpoint.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            fake_mountpoint.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            credential = root / "credential.env"
            credential.touch()
            host = root / "host.env"
            host.write_text(
                "\n".join(line.format(root=root, credential=credential) for line in host_lines) + "\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            environment["FAKE_STAT_OUTPUT"] = stat_output
            return subprocess.run(
                ["sh", str(SCRIPT), str(root), str(host)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_rejects_relative_credential_path(self):
        result = self._run([
            "LOB_DATA_ROOT={root}",
            "LOB_CREDENTIAL_FILE=credential.env",
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("credential file must use an absolute host path", result.stderr)

    def test_rejects_credential_mode_other_than_0600(self):
        result = self._run([
            "LOB_DATA_ROOT={root}",
            "LOB_CREDENTIAL_FILE={credential}",
        ], stat_output="10001:10001:644")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("credential file owner/mode must be 10001:10001/0600", result.stderr)

    def test_rejects_credential_owned_by_another_uid(self):
        result = self._run([
            "LOB_DATA_ROOT={root}",
            "LOB_CREDENTIAL_FILE={credential}",
        ], stat_output="0:0:600")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("credential file owner/mode must be 10001:10001/0600", result.stderr)

    def test_0600_credential_reaches_storage_validation(self):
        result = self._run([
            "LOB_DATA_ROOT={root}",
            "LOB_CREDENTIAL_FILE={credential}",
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("storage root is not a mount point", result.stderr)


if __name__ == "__main__":
    unittest.main()

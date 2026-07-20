import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "storage-identity-check"


class StorageIdentityWrapperTests(unittest.TestCase):
    def _run(self, filesystem_type: str = "ext4", matching_fstab: bool = True):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fake_bin = root / "bin"
            fake_bin.mkdir()

            commands = {
                "id": "#!/bin/sh\nprintf '0\\n'\n",
                "mountpoint": "#!/bin/sh\nexit 0\n",
                "findmnt": """#!/bin/sh
case "$*" in
  *"-o TARGET"*) printf '%s\\n' "$FAKE_ROOT" ;;
  *"-o FSTYPE"*) printf '%s\\n' "$FAKE_FILESYSTEM" ;;
  *"-o SOURCE"*) printf '/dev/PRIVATE_DEVICE_CANARY\\n' ;;
  *) exit 1 ;;
esac
""",
                "blkid": "#!/bin/sh\nprintf 'PRIVATE_UUID_CANARY\\n'\n",
                "df": """#!/bin/sh
printf '1B-blocks Used Available\\n'
printf '20000000000000 1000000000000 18000000000000\\n'
""",
            }
            for name, source in commands.items():
                path = fake_bin / name
                path.write_text(source, encoding="utf-8")
                path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

            fstab = root / "fstab"
            uuid = "PRIVATE_UUID_CANARY" if matching_fstab else "DIFFERENT_UUID"
            fstab.write_text(
                f"UUID={uuid} {root} ext4 defaults 0 2\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update({
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "FAKE_ROOT": str(root),
                "FAKE_FILESYSTEM": filesystem_type,
                "LOB_STORAGE_IDENTITY_TEST_ONLY": "true",
                "LOB_STORAGE_IDENTITY_FSTAB": str(fstab),
            })
            return subprocess.run(
                ["sh", str(SCRIPT), str(root)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_outputs_only_privacy_safe_storage_evidence(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["filesystem_type"], "ext4")
        self.assertEqual(report["total_bytes"], 20_000_000_000_000)
        self.assertEqual(report["service_usable_bytes"], 19_000_000_000_000)
        self.assertTrue(report["fstab_uuid_match"])
        self.assertNotIn("PRIVATE_DEVICE_CANARY", result.stdout)
        self.assertNotIn("PRIVATE_UUID_CANARY", result.stdout)

    def test_rejects_fstab_mismatch_without_printing_uuid(self):
        result = self._run(matching_fstab=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fstab UUID entry does not match", result.stderr)
        self.assertNotIn("PRIVATE_UUID_CANARY", result.stderr)

    def test_rejects_unsupported_filesystem(self):
        result = self._run(filesystem_type="btrfs")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("storage filesystem type is unsupported", result.stderr)


if __name__ == "__main__":
    unittest.main()

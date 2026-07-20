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
    def _run(
        self,
        filesystem_type: str = "ext4",
        layout: str = "direct",
        matching_fstab: bool = True,
        bind_source_matches: bool = True,
        bind_dependency_matches: bool = True,
    ):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fake_bin = root / "bin"
            fake_bin.mkdir()

            commands = {
                "id": "#!/bin/sh\nprintf '0\\n'\n",
                "mountpoint": "#!/bin/sh\nexit 0\n",
                "findmnt": """#!/bin/sh
selector=target
case "$*" in
  *"--mountpoint"*) selector=backing ;;
esac
case "$*" in
  *"-o TARGET"*)
    if test "$selector" = backing; then
      printf '%s\n' "$FAKE_BACKING_ROOT"
    else
      printf '%s\n' "$FAKE_ROOT"
    fi
    ;;
  *"-o FSTYPE"*) printf '%s\n' "$FAKE_FILESYSTEM" ;;
  *"-o FSROOT"*)
    if test "$FAKE_LAYOUT" = bind; then
      printf '/lob-project\n'
    else
      printf '/\n'
    fi
    ;;
  *"-o UUID"*) printf 'PRIVATE_UUID_CANARY\n' ;;
  *"-o SOURCE"*)
    if test "$FAKE_LAYOUT" = bind; then
      printf '/dev/PRIVATE_DEVICE_CANARY[/lob-project]\n'
    else
      printf '/dev/PRIVATE_DEVICE_CANARY\n'
    fi
    ;;
  *) exit 1 ;;
esac
""",
                "blkid": "#!/bin/sh\nprintf 'PRIVATE_UUID_CANARY\\n'\n",
                "df": """#!/bin/sh
printf '1B-blocks Used Available\n'
printf '20000000000000 1000000000000 18000000000000\n'
""",
            }
            for name, source in commands.items():
                path = fake_bin / name
                path.write_text(source, encoding="utf-8")
                path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

            fstab = root / "fstab"
            uuid = "PRIVATE_UUID_CANARY" if matching_fstab else "DIFFERENT_UUID"
            backing_root = root / "shared-disk"
            if layout == "direct":
                fstab_contents = (
                    f"UUID={uuid} {root} {filesystem_type} defaults 0 2\n"
                )
            else:
                source = backing_root / (
                    "lob-project" if bind_source_matches else "wrong-project"
                )
                dependency = (
                    backing_root if bind_dependency_matches else root / "wrong-disk"
                )
                fstab_contents = (
                    f"UUID={uuid} {backing_root} {filesystem_type} defaults 0 2\n"
                    f"{source} {root} none "
                    f"bind,x-systemd.requires-mounts-for={dependency} 0 0\n"
                )
            fstab.write_text(fstab_contents, encoding="utf-8")
            environment = os.environ.copy()
            environment.update({
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "FAKE_ROOT": str(root),
                "FAKE_BACKING_ROOT": str(backing_root),
                "FAKE_FILESYSTEM": filesystem_type,
                "FAKE_LAYOUT": layout,
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
        self.assertEqual(report["mount_layout"], "direct")
        self.assertEqual(report["filesystem_type"], "ext4")
        self.assertEqual(report["total_bytes"], 20_000_000_000_000)
        self.assertEqual(report["service_usable_bytes"], 19_000_000_000_000)
        self.assertTrue(report["fstab_uuid_match"])
        self.assertIsNone(report["bind_source_match"])
        self.assertIsNone(report["bind_dependency_match"])
        self.assertNotIn("PRIVATE_DEVICE_CANARY", result.stdout)
        self.assertNotIn("PRIVATE_UUID_CANARY", result.stdout)

    def test_accepts_shared_filesystem_bind_mount_with_explicit_dependency(self):
        result = self._run(layout="bind")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["mount_layout"], "bind")
        self.assertTrue(report["fstab_uuid_match"])
        self.assertTrue(report["bind_source_match"])
        self.assertTrue(report["bind_dependency_match"])
        self.assertNotIn("PRIVATE_DEVICE_CANARY", result.stdout)
        self.assertNotIn("PRIVATE_UUID_CANARY", result.stdout)
        self.assertNotIn("shared-disk", result.stdout)

    def test_rejects_fstab_mismatch_without_printing_uuid(self):
        result = self._run(matching_fstab=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fstab UUID entry does not match", result.stderr)
        self.assertNotIn("PRIVATE_UUID_CANARY", result.stderr)

    def test_rejects_bind_mount_with_wrong_source(self):
        result = self._run(layout="bind", bind_source_matches=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fstab bind mount entry or dependency does not match", result.stderr)
        self.assertNotIn("PRIVATE_UUID_CANARY", result.stderr)

    def test_rejects_bind_mount_without_exact_systemd_dependency(self):
        result = self._run(layout="bind", bind_dependency_matches=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fstab bind mount entry or dependency does not match", result.stderr)
        self.assertNotIn("PRIVATE_UUID_CANARY", result.stderr)

    def test_rejects_unsupported_filesystem(self):
        result = self._run(filesystem_type="btrfs")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("storage filesystem type is unsupported", result.stderr)


if __name__ == "__main__":
    unittest.main()

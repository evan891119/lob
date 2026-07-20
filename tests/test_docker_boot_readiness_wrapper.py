import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "docker-boot-readiness-check"


class DockerBootReadinessWrapperTests(unittest.TestCase):
    def _run(
        self,
        *,
        enabled: bool = True,
        active: bool = True,
        collector_policy: str = "unless-stopped",
        clickhouse_policy: str = "unless-stopped",
        collector_running: bool = True,
        clickhouse_running: bool = True,
    ):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fake_bin = root / "bin"
            fake_bin.mkdir()

            commands = {
                "id": "#!/bin/sh\nprintf '0\\n'\n",
                "systemctl": """#!/bin/sh
case "$1" in
  is-enabled) test "$FAKE_DOCKER_ENABLED" = true ;;
  is-active) test "$FAKE_DOCKER_ACTIVE" = true ;;
  *) exit 1 ;;
esac
""",
                "docker": """#!/bin/sh
if test "$1" = compose; then
  case "$*" in
    *" config --quiet") exit 0 ;;
    *" ps -q clickhouse")
      test "$FAKE_CLICKHOUSE_RUNNING" = true && printf 'PRIVATE_CLICKHOUSE_ID\n'
      exit 0
      ;;
    *" ps -q collector")
      test "$FAKE_COLLECTOR_RUNNING" = true && printf 'PRIVATE_COLLECTOR_ID\n'
      exit 0
      ;;
    *) exit 1 ;;
  esac
fi

if test "$1" = inspect; then
  case "$*" in
    *"State.Running"*) printf 'true\n' ;;
    *"RestartPolicy"*"PRIVATE_CLICKHOUSE_ID") printf '%s\n' "$FAKE_CLICKHOUSE_POLICY" ;;
    *"RestartPolicy"*"PRIVATE_COLLECTOR_ID") printf '%s\n' "$FAKE_COLLECTOR_POLICY" ;;
    *) exit 1 ;;
  esac
  exit 0
fi
exit 1
""",
            }
            for name, source in commands.items():
                path = fake_bin / name
                path.write_text(source, encoding="utf-8")
                path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

            host_env = root / "PRIVATE_HOST_PATH_CANARY.env"
            host_env.write_text(
                "LOB_MODE=live\nLOB_DATA_ROOT=/PRIVATE_DATA_PATH_CANARY\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update({
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "FAKE_DOCKER_ENABLED": str(enabled).lower(),
                "FAKE_DOCKER_ACTIVE": str(active).lower(),
                "FAKE_COLLECTOR_POLICY": collector_policy,
                "FAKE_CLICKHOUSE_POLICY": clickhouse_policy,
                "FAKE_COLLECTOR_RUNNING": str(collector_running).lower(),
                "FAKE_CLICKHOUSE_RUNNING": str(clickhouse_running).lower(),
            })
            return subprocess.run(
                ["sh", str(SCRIPT), str(host_env)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_outputs_only_privacy_safe_boot_readiness(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["docker_service_enabled"])
        self.assertTrue(report["docker_service_active"])
        self.assertTrue(report["required_services_running"])
        self.assertTrue(report["restart_policies_match"])
        self.assertNotIn("PRIVATE_", result.stdout)

    def test_rejects_docker_service_not_enabled(self):
        result = self._run(enabled=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("docker service is not enabled at boot", result.stderr)
        self.assertNotIn("PRIVATE_", result.stderr)

    def test_rejects_inactive_docker_service(self):
        result = self._run(active=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("docker service is not active", result.stderr)
        self.assertNotIn("PRIVATE_", result.stderr)

    def test_rejects_wrong_restart_policy(self):
        result = self._run(collector_policy="no")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compose restart policy does not match", result.stderr)
        self.assertNotIn("PRIVATE_", result.stderr)

    def test_rejects_required_service_not_running(self):
        result = self._run(clickhouse_running=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required compose service is not running", result.stderr)
        self.assertNotIn("PRIVATE_", result.stderr)


if __name__ == "__main__":
    unittest.main()

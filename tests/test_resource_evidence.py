import json
import tempfile
import unittest
from pathlib import Path

from lob_recorder.resource_evidence import parse_samples, write_resource_report


SAMPLES = [
    "clickhouse|10.0%|1.5GiB|8GiB|18.75%|1\n",
    "collector|2.0%|100MiB|2GiB|4.88%|1\n",
    "clickhouse|20.0%|1.6GiB|8GiB|20.00%|2\n",
    "collector|4.0%|110MiB|2GiB|5.37%|2\n",
    "clickhouse|30.0%|1.7GiB|8GiB|21.25%|3\n",
    "collector|6.0%|120MiB|2GiB|5.86%|3\n",
]


class ResourceEvidenceTests(unittest.TestCase):
    def test_report_contains_only_allowlisted_numeric_service_metrics(self):
        report = parse_samples(SAMPLES)

        self.assertEqual(set(report["services"]), {"clickhouse", "collector"})
        self.assertEqual(
            report["services"]["clickhouse"]["cpu_percent_average"], 20.0
        )
        self.assertEqual(
            report["services"]["clickhouse"]["memory_limit_bytes"],
            8 * 1_073_741_824,
        )
        self.assertEqual(report["services"]["collector"]["samples"], 3)
        self.assertTrue(report["checks"]["both_required_services_sampled"])

    def test_private_or_malformed_lines_fail_without_echoing_input(self):
        private = "private-host|1%|1GiB|2GiB|50%|1"
        with self.assertRaisesRegex(ValueError, "shape is invalid") as raised:
            parse_samples([private])
        self.assertNotIn("private-host", str(raised.exception))

    def test_missing_service_and_changing_limit_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            parse_samples([line for line in SAMPLES if not line.startswith("collector|")])
        with self.assertRaisesRegex(ValueError, "incomplete"):
            parse_samples(SAMPLES[:2])
        changed = list(SAMPLES)
        changed[-2] = "clickhouse|30.0%|1.7GiB|9GiB|21.25%|3\n"
        with self.assertRaisesRegex(ValueError, "limit changed"):
            parse_samples(changed)

    def test_atomic_writer_preserves_only_parser_generated_shape(self):
        report = parse_samples(SAMPLES)
        self.assertEqual(
            set(report),
            {"generated_at", "measurement", "services", "checks"},
        )
        with tempfile.TemporaryDirectory() as folder:
            target = write_resource_report(
                report, Path(folder) / "resource-report.json"
            )
            encoded = target.read_text()
        self.assertEqual(json.loads(encoded)["measurement"]["source"], "docker_stats_no_stream")


if __name__ == "__main__":
    unittest.main()

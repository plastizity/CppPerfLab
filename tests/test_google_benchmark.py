"""Native Google Benchmark JSON semantics; real collection is an acceptance run."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from cppperflab.adapters import parse_google_benchmark
from cppperflab.errors import LabError


class GoogleBenchmarkTests(unittest.TestCase):
    def test_installed_cli_preserves_failure_exit_status(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, str(root / "scripts/install.py")],
                       check=True, capture_output=True)
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "invalid.json"
            config.write_text('{"schema_version": 999}')
            result = subprocess.run([sys.executable, str(root / ".local/bin/perflab"),
                                     "run", "--config", str(config), "--json"],
                                    capture_output=True, text=True)
        envelope = json.loads(result.stdout)
        self.assertFalse(envelope["ok"])
        self.assertEqual(result.returncode, envelope["error"]["exit_code"])
        self.assertNotEqual(result.returncode, 0)

    def parse(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "benchmark.json"
            path.write_text(json.dumps({"context": {"library_version": "1.9.4"},
                                        "benchmarks": rows}))
            return parse_google_benchmark(path)

    def row(self, **changes):
        row = dict(name="Kernel/1024", run_name="Kernel/1024", run_type="iteration",
                   iterations=100, time_unit="ns", real_time=2000, cpu_time=1000)
        row.update(changes)
        return row

    def test_repetitions_are_samples_aggregates_are_not(self):
        result = self.parse([self.row(), self.row(real_time=3000),
                             self.row(run_type="aggregate", name="Kernel/1024_mean")])
        self.assertEqual(len(result["samples"]), 2)
        wall, cpu = result["samples"][0]
        self.assertAlmostEqual(wall["value"], 2e-6)
        self.assertAlmostEqual(cpu["value"], 1e-6)
        self.assertEqual(wall["unit"], "seconds")
        self.assertNotEqual(wall["name"], cpu["name"])
        self.assertEqual(result["library_version"], "1.9.4")

    def test_failed_target_is_not_a_timing(self):
        with self.assertRaises(LabError) as caught:
            self.parse([self.row(error_occurred=True, error_message="wrong output")])
        self.assertEqual(caught.exception.category, "target_failed")

    def test_multiple_cases_empty_samples_and_invalid_units_are_rejected(self):
        for rows in ([], [self.row(), self.row(run_name="Kernel/2048")],
                     [self.row(time_unit="ticks")], [self.row(iterations=0)]):
            with self.subTest(rows=rows), self.assertRaises(LabError) as caught:
                self.parse(rows)
            self.assertEqual(caught.exception.category, "parse_failed")

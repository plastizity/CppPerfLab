"""Contract tests for request validation, process lifetime, parsers and comparison."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from cppperflab.adapters import parse_hyperfine, parse_massif, parse_perf_report, parse_stat
from cppperflab.cli import main
from cppperflab.comparison import compare
from cppperflab.errors import LabError
from cppperflab.execution import check_execution, execute
from cppperflab.lab import plan
from cppperflab.protocol import load_request
from cppperflab.studies import fit_growth, load_study


def write_json(path, value):
    Path(path).write_text(json.dumps(value))


def request_template(tmp):
    exe = str(Path(sys.executable).resolve())
    command = [exe, "-c", "pass"]
    return {
        "schema_version": 1,
        "name": "unit",
        "workload": {"id": "example", "parameters": {"size": 4}, "inputs": []},
        "target": {"argv": command, "environment": {}, "build": {
            "revision": "unknown", "compiler": "unknown", "flags": [],
            "mode": "release", "artifacts": []}},
        "measurement": {"tool": "hyperfine", "repetitions": 3, "warmups": 0,
                        "timeout_seconds": 2, "options": {}},
        "correctness": {"argv": command, "description": "exits zero"},
    }


def comparison_result(run_id, *, values=None):
    values = values or [1.0] * 7
    samples = [{"metrics": [{"name": "time.wall", "value": value,
                              "unit": "seconds", "method": "native-process-timing",
                              "scope": "same target scope"}]}
               for value in values]
    return {
        "run_id": run_id,
        "status": "completed",
        "request": {
            "workload": {"id": "w", "parameters": {}},
            "measurement": {"cpu_affinity": None, "options": {}, "warmups": 1, "limits": {}},
            "target": {"environment": {}, "cwd": "/work", "argv": ["/bin/x", "arg"],
                       "build": {"mode": "release", "revision": "r", "flags": []}},
            "correctness": {"description": "oracle"},
        },
        "identity": {"inputs": {}},
        "tool": {"id": "hyperfine", "version": "1.18", "mode": "native-process-timing"},
        "environment": {key: "same" for key in
                        ("os", "kernel", "machine", "hostname", "cpu_model", "cpu_flags", "allowed_cpus", "governor")},
        "correctness": {"status": "passed"},
        "samples": samples,
    }


class RequestProtocolTests(unittest.TestCase):
    def test_schema_version_one_resolves_executables_and_accepts_complete_request(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "request.json"
            write_json(path, request_template(temp))
            request = load_request(path)
            self.assertEqual(request["schema_version"], 1)
            self.assertEqual(request["target"]["argv"][0], str(Path(sys.executable).resolve()))

    def test_unknown_fields_and_nonfinite_json_are_rejected_at_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "request.json"
            request = request_template(temp)
            request["surprise"] = True
            write_json(path, request)
            with self.assertRaises(LabError) as caught:
                load_request(path)
            self.assertEqual(caught.exception.category, "invalid_request")

            path.write_text('{"schema_version": NaN}')
            with self.assertRaises(LabError) as caught:
                load_request(path)
            self.assertEqual(caught.exception.category, "invalid_request")

    def test_unsupported_schema_has_distinct_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "request.json"
            request = request_template(temp)
            request["schema_version"] = 2
            write_json(path, request)
            with self.assertRaises(LabError) as caught:
                load_request(path)
            self.assertEqual(caught.exception.category, "unsupported_schema")


class ExecutionLifecycleTests(unittest.TestCase):
    def test_timeout_cleans_owned_process_group_and_preserves_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            pid_file = directory / "descendant.pid"
            child_code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
            parent_code = (
                "import subprocess,sys,time,pathlib; "
                "p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]); "
                "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)"
            )
            record = execute([sys.executable, "-c", parent_code, str(pid_file), child_code],
                             directory / "run", timeout=0.3)
            self.assertEqual(record["status"], "timeout")
            self.assertEqual((directory / "run/stdout.txt").read_bytes(), b"")
            self.assertTrue((directory / "run/stderr.txt").is_file())
            with self.assertRaises(LabError) as caught:
                check_execution(record)
            self.assertEqual(caught.exception.category, "timeout")
            child_pid = int(pid_file.read_text())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                # A killed orphan can briefly remain as a zombie until init reaps it.
                stat_path = Path("/proc") / str(child_pid) / "stat"
                try:
                    state = stat_path.read_text().split()[2]
                except FileNotFoundError:
                    break  # The dead orphan was reaped between kill(0) and this read.
                if state == "Z":
                    break
                time.sleep(0.02)
            else:
                self.fail("timed-out child remained alive")

    def test_cancelled_execution_is_reported_and_cleaned(self):
        # Exercise the real cancellation branch without signaling the test runner.
        with tempfile.TemporaryDirectory() as temp:
            real_wait = subprocess.Popen.wait
            interrupted = {"done": False}

            def interrupt_once(process, timeout=None):
                if not interrupted["done"]:
                    interrupted["done"] = True
                    raise KeyboardInterrupt
                return real_wait(process, timeout=timeout)

            with mock.patch.object(subprocess.Popen, "wait", interrupt_once):
                record = execute([sys.executable, "-c", "import time; time.sleep(30)"],
                                 Path(temp) / "run", timeout=5)
            self.assertEqual(record["status"], "cancelled")
            self.assertIsNotNone(record["returncode"])
            with self.assertRaises(LabError) as caught:
                check_execution(record)
            self.assertEqual(caught.exception.category, "cancelled")


class ParserTests(unittest.TestCase):
    def parse_stat_text(self, temp, text, events=("duration_time",)):
        path = Path(temp) / "perf.csv"
        path.write_text(text)
        return parse_stat(path, events)

    def test_perf_clock_unit_conversion_and_counter_semantics(self):
        with tempfile.TemporaryDirectory() as temp:
            parsed = self.parse_stat_text(temp, "1000;msec;duration_time;1;100.00\n")
            metric = parsed["metrics"][0]
            self.assertEqual(metric["name"], "time.wall")
            self.assertEqual(metric["unit"], "ns")
            self.assertEqual(metric["value"], 1_000_000_000)

    def test_perf_unavailable_event_is_preserved_for_adapter_to_reject(self):
        with tempfile.TemporaryDirectory() as temp:
            parsed = self.parse_stat_text(temp, "<not counted>;;duration_time;1;0.00\n")
            self.assertEqual(parsed["metrics"], [])
            self.assertEqual(parsed["unavailable_metrics"],
                             [{"event": "duration_time", "reason": "<not counted>"}])

    def test_perf_missing_malformed_duplicate_and_unsupported_units_fail(self):
        cases = [
            ("", ("duration_time",), "Missing perf events"),
            ("abc;msec;duration_time;1;100.00\n", ("duration_time",), "Malformed perf CSV"),
            ("1;cycles;cycles;1;100.00\n", ("cycles",), "Unexpected counter unit"),
            ("1;fortnight;duration_time;1;100.00\n", ("duration_time",), "Unsupported perf clock unit"),
            ("1;msec;duration_time;1;100.00\n1;msec;duration_time;1;100.00\n",
             ("duration_time",), "Duplicate perf event"),
        ]
        for text, events, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(LabError) as caught:
                    self.parse_stat_text(temp, text, events)
                self.assertEqual(caught.exception.category, "parse_failed")
                self.assertIn(message, str(caught.exception))

    def test_massif_heap_peak_excludes_stack_and_extra_fields(self):
        text = """desc: test
snapshot=0
mem_heap_B=10
mem_heap_extra_B=2
mem_stacks_B=90
snapshot=1
mem_heap_B=30
mem_heap_extra_B=4
mem_stacks_B=80
"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "massif.out"
            path.write_text(text)
            parsed = parse_massif(path)
            self.assertEqual(parsed["snapshot_count"], 2)
            self.assertEqual(parsed["metrics"][0]["value"], 30)
            self.assertIn("excludes RSS", parsed["metrics"][0]["scope"])

    def test_massif_incomplete_or_absent_snapshots_fail(self):
        for text in ("desc: none\n", "snapshot=0\nmem_heap_B=3\n"):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "massif.out"
                path.write_text(text)
                with self.assertRaises(LabError) as caught:
                    parse_massif(path)
                self.assertEqual(caught.exception.category, "parse_failed")

    def test_hyperfine_preserves_each_sample_and_rejects_failed_target(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hyperfine.json"
            write_json(path, {"results": [{"times": [0.2, 0.3], "exit_codes": [0, 0]}]})
            parsed = parse_hyperfine(path)
            self.assertEqual([s[0]["value"] for s in parsed["samples"]], [0.2, 0.3])
            write_json(path, {"results": [{"times": [0.2, 0.3], "exit_codes": [0, 1]}]})
            with self.assertRaises(LabError) as caught:
                parse_hyperfine(path)
            self.assertEqual(caught.exception.category, "target_failed")


class ComparisonTests(unittest.TestCase):
    def test_conditions_that_change_measurement_meaning_are_incomparable(self):
        baseline = comparison_result("base")
        candidate = comparison_result("candidate")
        candidate["environment"]["cpu_model"] = "other"
        with self.assertRaises(LabError) as caught:
            compare(baseline, candidate)
        self.assertEqual(caught.exception.category, "incomparable")
        self.assertIn("environment.cpu_model", [d["condition"] for d in caught.exception.details["differences"]])

    def test_insufficient_or_noisy_samples_never_claim_a_change(self):
        baseline = comparison_result("base", values=[1.0] * 3)
        candidate = comparison_result("candidate", values=[0.8] * 3)
        result = compare(baseline, candidate)
        self.assertEqual(result["classification"], "inconclusive")
        self.assertFalse(result["sufficient_samples"])

        noisy = compare(comparison_result("b", values=[1, 2, 1, 2, 1, 2, 1]),
                        comparison_result("c", values=[0.5, 1, 0.5, 1, 0.5, 1, 0.5]))
        self.assertEqual(noisy["classification"], "inconclusive")
        self.assertFalse(noisy["stable_samples"])

    def test_repeated_stable_samples_can_identify_material_regression(self):
        baseline = comparison_result("base", values=[1.00, 1.01, 0.99, 1.00, 1.01, 0.99, 1.00])
        candidate = comparison_result("candidate", values=[1.20, 1.21, 1.19, 1.20, 1.21, 1.19, 1.20])
        result = compare(baseline, candidate)
        self.assertTrue(result["sufficient_samples"])
        self.assertEqual(result["classification"], "observed_regression")


class GrowthStudyTests(unittest.TestCase):
    @staticmethod
    def points(fn):
        return [{"x": n, "median": fn(n), "n": 9, "cv": 0.01}
                for n in (10, 20, 40, 80, 160)]

    def test_known_linear_and_quadratic_growth_rank_the_matching_model_first(self):
        linear = fit_growth(self.points(lambda n: 3 * n + 7))
        quadratic = fit_growth(self.points(lambda n: 0.25 * n * n + 3))
        self.assertEqual(linear["kind"], "statistical_inference")
        self.assertEqual(linear["models"][0]["model"], "n")
        self.assertEqual(quadratic["models"][0]["model"], "n_squared")
        self.assertIn("not a Big-O proof", linear["limitations"][0])

    def test_growth_fit_rejects_too_few_or_repeated_scales(self):
        with self.assertRaises(LabError):
            fit_growth(self.points(lambda n: n)[:3])
        with self.assertRaises(LabError):
            fit_growth([{"x": 8, "median": 8, "n": 8, "cv": 0.01},
                        {"x": 8, "median": 9, "n": 8, "cv": 0.01},
                        {"x": 16, "median": 16, "n": 8, "cv": 0.01},
                        {"x": 32, "median": 32, "n": 8, "cv": 0.01}])

    def test_study_loader_enforces_experiment_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            write_json(directory / "request.json", request_template(temp))
            study_spec = {
                "schema_version": 1, "name": "scales", "template": "request.json",
                "axis": {"parameter": "size", "values": [8, 16, 32, 64], "argument_index": 2},
                "variants": [{"id": "baseline", "executable": sys.executable,
                              "revision": "r1", "artifacts": []}],
                "budget": 3, "seed": 17, "metric": "time.wall", "constraints": "same input family",
            }
            path = directory / "study.json"
            write_json(path, study_spec)
            with self.assertRaises(LabError) as caught:
                load_study(path)
            self.assertEqual(caught.exception.category, "invalid_request")
            self.assertIn("exceeds budget", str(caught.exception))

    def test_auto_plan_only_claims_perf_metrics_requested_by_configured_events(self):
        request = request_template(".")
        request["measurement"].update(tool="auto", options={"events": ["duration_time"]})
        request["goal"] = {"metrics": ["cpu.instructions"], "max_overhead": "low",
                            "allow_instrumentation": False}
        probes = {tool_id: {"tool": tool_id, "available": True, "reason": None, "version": "test"}
                  for tool_id in ("hyperfine", "perf-stat", "perf-record", "massif")}
        with mock.patch("cppperflab.lab.probe_tools", return_value={"tools": probes}):
            result = plan(request, object())
        perf_candidate = next(c for c in result["candidates"] if c["tool"] == "perf-stat")
        self.assertFalse(perf_candidate["feasible"])
        self.assertIn("missing_metrics: cpu.instructions", perf_candidate["reasons"])
        self.assertIsNone(result["selected"])

        request["measurement"]["options"] = {"events": ["instructions"]}
        with mock.patch("cppperflab.lab.probe_tools", return_value={"tools": probes}):
            result = plan(request, object())
        perf_candidate = next(c for c in result["candidates"] if c["tool"] == "perf-stat")
        self.assertTrue(perf_candidate["feasible"])
        self.assertEqual(result["selected"], "perf-stat")


class PerfRecordParserTests(unittest.TestCase):
    def test_report_sample_count_has_distinct_field_and_hotspots(self):
        report = """# Samples: 241 of event 'cpu-clock'
Overhead;Command;Shared Object;Symbol
 72.50%;solver;solver-bin;compute_kernel
"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "perf-report.txt"
            path.write_text(report)
            parsed = parse_perf_report(path)
        self.assertEqual(parsed["sample_count"], 241)
        self.assertNotIn("samples", parsed)
        self.assertEqual(parsed["hotspots"][0]["symbol"], "compute_kernel")
        self.assertEqual(parsed["hotspots"][0]["self_percent"], 72.5)


class CliErrorTests(unittest.TestCase):
    def test_cli_returns_structured_invalid_request_exit(self):
        output = __import__("io").StringIO()
        with tempfile.TemporaryDirectory() as temp, mock.patch("sys.argv", [
                "perflab", "tools", "probe", "--tool", "hyperfine", "--events", "cycles",
                "--store", str(Path(temp) / "store"), "--json"]), mock.patch("sys.stdout", output):
            code = main()
        self.assertEqual(code, 2)
        envelope = json.loads(output.getvalue())
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["category"], "invalid_request")


if __name__ == "__main__":
    unittest.main()

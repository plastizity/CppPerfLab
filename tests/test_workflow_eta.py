import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cppperflab.errors import LabError
from cppperflab.storage import Store
from cppperflab.workflow_eta import cost_key, estimate_run_eta
from test_core import request_template


def base_result():
    result = {
        "run_id": "current", "status": "running",
        "request": {"name": "display name", "workload": {"scale": 8},
                    "measurement": {"repetitions": 3}},
        "identity": {"artifacts": {"/target": "abc"}, "inputs": {"/input": "def"}},
        "tool": {"id": "perf-stat", "version": "v1", "mode": "counter",
                 "parser_version": 2, "executable_path": "/usr/bin/perf",
                 "executable_sha256": "tool-hash"},
        "environment": {"os": "Linux", "kernel": "k", "machine": "x86_64",
                        "hostname": "host", "cpu_model": "cpu", "cpu_flags": ["a"],
                        "allowed_cpus": [1], "governor": "performance", "python": "3.11",
                        "numa_nodes": "0", "load_average": [1.0]},
        "workflow_cost": {"schema_version": 1, "method": "monotonic-run-lifetime",
                           "started_monotonic_ns": 10_000_000_000,
                           "clock": {"name": "CLOCK_MONOTONIC", "boot_id": "boot",
                                     "time_namespace": "time:[1]"},
                           "cost_key": None, "implementation_sha256": "impl",
                           "evidence_state": "Measured"},
    }
    result["workflow_cost"]["cost_key"] = cost_key(result, "impl")
    return result


class MemoryStore:
    def __init__(self, records):
        self.records = records

    def list(self, kind="run"):
        if kind != "run":
            raise AssertionError(kind)
        return self.records


def historical(run_id, seconds, key="key", status="completed", correctness="passed"):
    result = base_result()
    result.update(run_id=run_id, status=status, correctness={"status": correctness})
    result["workflow_cost"].update(elapsed_seconds=seconds)
    if key != "key":
        result["workflow_cost"]["cost_key"] = key
    return result


class WorkflowEtaTests(unittest.TestCase):
    def test_run_invalidates_workflow_cost_if_selected_tool_changes(self):
        from cppperflab import lab

        with tempfile.TemporaryDirectory() as temp:
            request = request_template(temp)
            request['measurement'].update(tool='hyperfine', repetitions=1, warmups=0)
            tool_path='/usr/bin/true'
            tool_hash = hashlib.sha256(Path(tool_path).read_bytes()).hexdigest()
            probe = dict(version='test-version', executable_path=tool_path,
                         executable_sha256=tool_hash, mode='native-process-timing',parser_version=1)
            plan = dict(selected='hyperfine', probes={'tools': {'hyperfine': probe}})
            parsed = dict(metrics=[], samples=[[dict(name='time.wall', value=0.01, unit='seconds',
                          method='native-process-timing', scope='test target', evidence='raw.json')]],
                          hotspots=[])
            execution = dict(status='completed', returncode=0, argv=request['target']['argv'])
            adapter = mock.Mock(batch=True)
            adapter.describe.return_value = {'id': 'hyperfine'}
            adapter.prepare.return_value = request['target']['argv']
            adapter.parse.return_value = parsed
            actual_sha256=lab.sha256
            def changed_tool(path):
                return 'changed-tool-hash' if str(path)==tool_path else actual_sha256(path)
            with mock.patch.object(lab, 'plan', return_value=plan), \
                 mock.patch.object(lab, 'REGISTRY', {'hyperfine': adapter}), \
                 mock.patch.object(lab, 'binary_info', return_value={}), \
                 mock.patch.object(lab, 'execute', return_value=execution), \
                 mock.patch.object(lab, 'sha256', side_effect=changed_tool):
                result = lab.run(request, Store(Path(temp) / 'store'))

        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(result['workflow_cost']['evidence_state'], 'Invalid')
        self.assertIsNone(result['workflow_cost']['cost_key'])
        self.assertGreater(result['workflow_cost']['elapsed_seconds'], 0)

    def test_cost_key_ignores_display_and_dynamic_fields_but_tracks_work_and_tool(self):
        result = base_result()
        original = cost_key(result, "impl")
        changed = copy.deepcopy(result)
        changed["request"]["name"] = "another label"
        changed["environment"]["load_average"] = [9.0]
        self.assertEqual(original, cost_key(changed, "impl"))
        changed["request"]["workload"]["scale"] = 16
        self.assertNotEqual(original, cost_key(changed, "impl"))
        changed = copy.deepcopy(result)
        changed["tool"]["executable_sha256"] = "changed-tool"
        self.assertNotEqual(original, cost_key(changed, "impl"))
        result["tool"]["version"] = "unknown-until-collection"
        self.assertIsNone(cost_key(result, "impl"))
        result["tool"]["version"] = "v1"
        result["tool"].pop("executable_sha256")
        self.assertIsNone(cost_key(result, "impl"))

    @mock.patch("cppperflab.workflow_eta._current_clock", return_value={"boot_id": "boot", "time_namespace": "time:[1]"})
    def test_two_sample_estimate_and_expiry_preserves_raw_history(self, _clock):
        current = base_result()
        store = MemoryStore([historical("r1", 100), historical("r2", 120)])
        estimate = estimate_run_eta(store, current, {"alive": True}, now_ns=60_000_000_000)
        self.assertEqual(estimate["evidence_state"], "Estimated")
        self.assertEqual(estimate["total_remaining_seconds"], 60)
        self.assertEqual(estimate["historical_total_seconds"], {"median": 110, "min": 100, "max": 120})
        self.assertEqual(estimate["source_run_ids"], ["r1", "r2"])
        expired = estimate_run_eta(store, current, {"alive": True}, now_ns=120_000_000_000)
        self.assertEqual(expired["evidence_state"], "Missing")
        self.assertIsNone(expired["total_remaining_seconds"])
        self.assertEqual(expired["source_total_seconds"], [100.0, 120.0])
        self.assertIn("expired", expired["reason"])

    @mock.patch("cppperflab.workflow_eta._current_clock", return_value={"boot_id": "boot", "time_namespace": "time:[1]"})
    def test_legacy_failed_and_unknown_owner_do_not_produce_eta(self, _clock):
        current = base_result()
        store = MemoryStore([{"run_id": "legacy", "status": "completed", "correctness": {"status": "passed"}},
                             historical("failed", 20, status="failed"),
                             historical("wrong", 30, correctness="failed")])
        result = estimate_run_eta(store, current, {"alive": True}, now_ns=20_000_000_000)
        self.assertEqual(result["evidence_state"], "Missing")
        self.assertEqual(result["source_run_ids"], [])
        self.assertIsNone(result["total_remaining_seconds"])
        unknown = estimate_run_eta(store, current, {"alive": None}, now_ns=20_000_000_000)
        self.assertEqual(unknown["evidence_state"], "Missing")
        self.assertIn("owner", unknown["reason"])

    @mock.patch("cppperflab.workflow_eta._current_clock", return_value={"boot_id": "boot", "time_namespace": "time:[1]"})
    def test_matching_malformed_historical_cost_fails_storage(self, _clock):
        malformed = historical("broken", 10)
        malformed["workflow_cost"]["elapsed_seconds"] = float("nan")
        with self.assertRaises(LabError) as caught:
            estimate_run_eta(MemoryStore([malformed]), base_result(), {"alive": True}, now_ns=20_000_000_000)
        self.assertEqual(caught.exception.category, "storage_failed")


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest

from cppperflab.errors import LabError
from cppperflab.lifecycle import process_identity
from cppperflab.storage import Store, write_json, utc
from cppperflab.study_status import study_status


class StudyStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def study(self, **fields):
        identifier, directory = self.store.create("studies")
        result = dict(schema_version=1, record_kind="study", study_id=identifier, name="test",
            status="running", started_at=utc(), error=None, runs=[])
        result.update(fields)
        if result.get("lifecycle") is not None:
            result["lifecycle"].setdefault("updated_at", utc())
        write_json(directory / "study.json", result)
        return identifier

    def make_run(self, status="completed", samples=2):
        identifier, directory = self.store.create("runs")
        result = dict(schema_version=1, run_id=identifier, status=status, error=None,
            started_at=utc(), ended_at=utc() if status != "running" else None,
            request=dict(name="synthetic", target=dict(build={})), samples=[{} for _ in range(samples)],
            correctness=dict(status="passed"), lifecycle=dict(schema_version=1,
                owner=dict(pid=99999999, evidence_state="Missing", reason="test"),
                normalized_samples=samples, active_process=None))
        write_json(directory / "result.json", result)
        return identifier

    def test_nested_canonical_children_deduplicate_flattened_assessment_runs(self):
        run_id = self.make_run()
        child_id = self.study(lifecycle=dict(schema_version=1, owner={"pid": 99999999, "evidence_state": "Missing", "reason": "test"}, planned_runs=1,
            children=[dict(record_kind="run", record_id=run_id, context={"variant": "A"}, created_at=utc())],
            active_child=0))
        root_id = self.study(runs=[dict(run_id=run_id)], execution_budget=dict(run_calls=1),
            lifecycle=dict(schema_version=1, owner={"pid": 99999999, "evidence_state": "Missing", "reason": "test"}, planned_runs=1,
                children=[dict(record_kind="study", record_id=child_id, context={"sample": "s1"}, created_at=utc())],
                active_child=0))
        result = study_status(self.store, root_id)
        self.assertEqual(result["progress"]["created_runs"], 1)
        self.assertEqual(result["progress"]["completed_runs"], 1)
        self.assertEqual(result["progress"]["normalized_samples"], 2)
        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual([part["record_id"] for part in result["active_path"]], [root_id, child_id, run_id])

    def test_legacy_running_without_owner_is_unverified(self):
        run_id = self.make_run("running", samples=0)
        identifier = self.study(runs=[dict(run_id=run_id)])
        result = study_status(self.store, identifier)
        self.assertEqual(result["observed_state"], "unverified")
        self.assertEqual(result["runs"][0]["observed_state"], "unverified")
        self.assertEqual(result["active_path"], [])

    def test_cycle_and_invalid_active_index_are_rejected(self):
        first, second = self.store.create("studies")[0], self.store.create("studies")[0]
        for identifier, other in ((first, second), (second, first)):
            write_json(self.store.record_path(identifier, "study"), dict(schema_version=1,
                record_kind="study", study_id=identifier, status="running", error=None,
                lifecycle=dict(schema_version=1, owner={"pid": 99999999, "evidence_state": "Missing", "reason": "test"}, planned_runs=1,
                    updated_at=utc(), children=[dict(record_kind="study", record_id=other, context={}, created_at=utc())], active_child=0)))
        with self.assertRaises(LabError) as error:
            study_status(self.store, first)
        self.assertEqual(error.exception.category, "storage_failed")
        malformed = self.study(lifecycle=dict(schema_version=1, owner=None, planned_runs=0, children=[], active_child=0))
        with self.assertRaises(LabError) as error:
            study_status(self.store, malformed)
        self.assertEqual(error.exception.category, "storage_failed")

    def test_failed_child_error_is_retained(self):
        run_id = self.make_run("failed", samples=0)
        identifier = self.study(status="failed", error="child execution failed",
            lifecycle=dict(schema_version=1, owner={"pid": 99999999, "evidence_state": "Missing", "reason": "test"}, planned_runs=1,
                children=[dict(record_kind="run", record_id=run_id, context={}, created_at=utc())], active_child=None))
        result = study_status(self.store, identifier)
        self.assertEqual(result["source_error"], "child execution failed")
        self.assertEqual(result["progress"]["failed_runs"], 1)
        self.assertEqual(result["progress"]["completed_runs"], 0)

    def test_running_study_with_recorded_owner_that_exited_is_not_reported_live(self):
        owner = process_identity(os.getpid())
        owner["pid"] = 99999999
        identifier = self.study(lifecycle=dict(schema_version=1,
            owner=owner,
            planned_runs=0, children=[], active_child=None))
        result = study_status(self.store, identifier)
        self.assertEqual(result["observed_state"], "owner_exited")


if __name__ == "__main__":
    unittest.main()

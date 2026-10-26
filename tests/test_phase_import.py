import json
from pathlib import Path
import tempfile
import unittest

from cppperflab.errors import LabError
from cppperflab.phase_import import convert_log, load_mapping


class PhaseImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def mapping(self, pattern=r"STAGE total=(?P<t>\S+) calls=(?P<n>\S+)", **changes):
        rule = {"id": "solve", "name": "Solve", "parent_id": None, "keyword": "STAGE",
                "pattern": pattern, "aggregation": "sum", "required": True,
                "children_coverage": "unknown", "inclusive": {"group": "t", "unit": "ms", "kind": "per_call_mean"},
                "calls": {"group": "n"}}
        rule.update(changes.pop("rule", {}))
        config = {"schema_version": 1, "scope": "synthetic solve", "unmatched_lines": "ignore", "rules": [rule]}
        config.update(changes)
        path = self.root / "mapping.json"
        path.write_text(json.dumps(config))
        return path

    def run_import(self, text, mapping=None):
        source = self.root / "run.log"
        source.write_text(text)
        output = self.root / "phases.json"
        convert_log(source, mapping or self.mapping(), output)
        return json.loads(output.read_text())

    def test_two_log_formats_map_to_same_semantics(self):
        mapping = self.mapping(pattern=r"SOLVE duration=(?P<t>\S+) n=(?P<n>\S+)", rule={"keyword": "SOLVE"})
        first = self.run_import("SOLVE duration=2 n=3\n", mapping)
        mapping.write_text(json.dumps({"schema_version": 1, "scope": "synthetic solve", "unmatched_lines": "ignore",
            "rules": [{"id": "solve", "name": "Solve", "parent_id": None, "keyword": "elapsed",
            "pattern": r"elapsed (?P<t>\S+) ms for (?P<n>\S+)", "aggregation": "sum", "required": True,
            "children_coverage": "unknown", "inclusive": {"group": "t", "unit": "ms", "kind": "per_call_mean"},
            "calls": {"group": "n"}}]}))
        second = self.run_import("elapsed 2 ms for 3\n", mapping)
        self.assertEqual(first["stages"][0]["inclusive"], second["stages"][0]["inclusive"])
        self.assertEqual(first["stages"][0]["calls"], second["stages"][0]["calls"])
        self.assertEqual(first["stages"][0]["inclusive"], {"seconds": 0.006, "evidence_state": "Estimated"})

    def test_sum_converts_units_and_keeps_work_and_correctness(self):
        rule = {"self": {"group": "s", "unit": "us"},
                "correctness": {"group": "ok", "values": {"Y": "passed", "N": "failed"}},
                "work": {"iterations": {"group": "w", "unit": "step", "evidence_state": "Measured"}}}
        mapping = self.mapping(pattern=r"STAGE total=(?P<t>\S+) calls=(?P<n>\S+) self=(?P<s>\S+) ok=(?P<ok>\S+) work=(?P<w>\S+)", rule=rule)
        result = self.run_import("STAGE total=2 calls=3 self=10 ok=Y work=4\nSTAGE total=1 calls=2 self=5 ok=N work=6\n", mapping)
        stage = result["stages"][0]
        self.assertEqual(stage["inclusive"]["seconds"], 0.008)
        self.assertEqual(stage["self"]["seconds"], 15e-6)
        self.assertEqual(stage["calls"], 5)
        self.assertEqual(stage["correctness"], "failed")
        self.assertEqual(stage["work"]["iterations"]["value"], 10)

    def test_missing_optional_rule_is_null_not_zero(self):
        mapping = self.mapping(rule={"required": False})
        stage = self.run_import("unrelated diagnostic\n", mapping)["stages"][0]
        self.assertIsNone(stage["inclusive"])
        self.assertIsNone(stage["calls"])
        self.assertEqual(stage["source_lines"]["count"], 0)

    def test_selected_malformed_value_is_rejected(self):
        with self.assertRaises(LabError):
            self.run_import("STAGE total=oops calls=1\n")

    def test_duplicate_and_ambiguous_matches_are_rejected(self):
        with self.assertRaises(LabError):
            self.run_import("STAGE total=1 calls=1\nSTAGE total=2 calls=1\n", self.mapping(aggregation="single"))
        mapping = self.mapping()
        config = json.loads(mapping.read_text())
        duplicate = dict(config["rules"][0], id="other", name="Other", keyword="STAGE")
        config["rules"].append(duplicate)
        mapping.write_text(json.dumps(config))
        with self.assertRaises(LabError):
            self.run_import("STAGE total=1 calls=1\n", mapping)

    def test_invalid_configuration_and_expected_hash_are_rejected(self):
        mapping = self.mapping()
        config = json.loads(mapping.read_text())
        config["rules"][0]["unexpected"] = True
        mapping.write_text(json.dumps(config))
        with self.assertRaises(LabError):
            load_mapping(mapping)
        mapping = self.mapping()
        source = self.root / "run.log"
        source.write_text("STAGE total=1 calls=1\n")
        with self.assertRaises(LabError):
            convert_log(source, mapping, self.root / "out.json", expected_sha256="wrong")

    def test_format_template_is_full_line_and_load_mapping_stays_json_data(self):
        config = {"schema_version": 1, "scope": "formatted", "unmatched_lines": "error", "rules": [{
            "id": "phase", "name": "Phase", "parent_id": None, "format": "Stage {calls}: {mean} ms",
            "aggregation": "single", "required": True, "children_coverage": "unknown",
            "calls": {"group": "calls"}, "inclusive": {"group": "mean", "unit": "ms", "kind": "per_call_mean"}}]}
        mapping = self.root / "mapping.json"
        mapping.write_text(json.dumps(config))
        self.assertEqual(load_mapping(mapping), config)
        result = self.run_import("Stage 4: 1.25e-2 ms\n", mapping)["stages"][0]
        self.assertEqual(result["calls"], 4)
        self.assertEqual(result["inclusive"]["seconds"], 0.00005)
        with self.assertRaises(LabError):
            self.run_import("prefix Stage 4: 1.25e-2 ms\n", mapping)

    def test_simple_keyword_fields_extract_numeric_and_status_tokens(self):
        config = {"schema_version": 1, "scope": "simple", "unmatched_lines": "ignore", "rules": [{
            "id": "phase", "name": "Phase", "parent_id": None, "keyword": "SOLVE",
            "aggregation": "single", "required": True, "children_coverage": "unknown",
            "inclusive": {"keyword": "elapsed", "unit": "s", "kind": "total"},
            "calls": {"keyword": "calls"},
            "correctness": {"keyword": "status", "values": {"ok": "passed", "bad": "failed"}}}]}
        mapping = self.root / "mapping.json"
        mapping.write_text(json.dumps(config))
        stage = self.run_import("SOLVE elapsed: 2.5e-3; calls=7, status ok\n", mapping)["stages"][0]
        self.assertEqual(stage["inclusive"]["seconds"], 0.0025)
        self.assertEqual(stage["calls"], 7)
        self.assertEqual(stage["correctness"], "passed")
        with self.assertRaises(LabError):
            self.run_import("SOLVE elapsed: broken; calls=7, status ok\n", mapping)


if __name__ == "__main__":
    unittest.main()

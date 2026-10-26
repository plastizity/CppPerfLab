#!/usr/bin/env python3
import json
import pathlib
import subprocess
import sys
import tempfile


def main():
    executable = pathlib.Path(sys.argv[1])
    subprocess.run([str(executable), "--verify"], check=True, capture_output=True, text=True)
    with tempfile.TemporaryDirectory(prefix="cppperflab-thread-activity-") as directory:
        report = pathlib.Path(directory) / "activity.json"
        completed = subprocess.run(
            [str(executable), "10001", str(report)], check=True, capture_output=True, text=True
        )
        assert '"correctness":"passed"' in completed.stdout
        data = json.loads(report.read_text(encoding="utf-8"))

    assert data["schema_version"] == 1
    assert data["scope"] == "synthetic_two_thread_activity"
    clock = data["clock"]
    tracks = clock["tracks"]
    assert set(tracks) == {"controller", "worker"}
    assert all(track["pid"] > 0 and track["tid"] > 0 for track in tracks.values())
    assert len({track["tid"] for track in tracks.values()}) == 2
    assert data["start_ns"] == 0 and data["end_ns"] > 0
    stages = data["stages"]
    assert len(stages) == 3
    assert len({stage["id"] for stage in stages}) == len(stages)
    assert {stage["track_id"] for stage in stages} == set(tracks)
    kinds = {"application_work": 0, "application_wait": 0}
    for stage in stages:
        assert stage["correctness"] == "passed"
        assert stage["input_id"] == "cyclic-sum-N=10001"
        assert 0 <= stage["start_ns"] <= stage["end_ns"] <= data["end_ns"]
        activity = stage["activity"]
        assert activity["kind"] in kinds
        kinds[activity["kind"]] += 1
        if activity["kind"] == "application_wait":
            assert activity["reason"] == "synchronization"
        else:
            assert "reason" not in activity
    assert kinds == {"application_work": 2, "application_wait": 1}
    print("thread activity protocol: passed")


if __name__ == "__main__":
    main()

"""Evidence-backed estimate of the remaining CppPerfLab run workflow."""
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import time

from .errors import LabError


_HOST_FIELDS = ("os", "kernel", "machine", "hostname", "cpu_model", "cpu_flags",
                "allowed_cpus", "governor", "python", "numa_nodes")
_TOOL_FIELDS = ("id", "version", "mode", "parser_version", "executable_path",
                "executable_sha256")
_SCOPE = ("CppPerfLab run() entry through final cleanup/inventory, before final result write; "
          "not target performance time")
_LIMITATIONS = [
    "Historical durations are descriptive observations, not a confidence interval or guarantee.",
    "Host load, filesystem state and adaptive workload behavior are uncontrolled.",
    "The estimate uses completed runs with the same declared request and measured environment identity.",
]


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def cost_key(result, implementation_sha256):
    """Return a stable SHA-256 key for runs whose selected tool identity is known."""
    if (result.get('execution_context') or {}).get('kind')=='concurrent_ab':
        return None
    tool = result["tool"]
    if (tool.get("version") in (None, "unknown-until-collection") or
            not tool.get("executable_path") or not tool.get("executable_sha256")):
        return None
    request = dict(result["request"])
    request.pop("name", None)
    identity = result["identity"]
    environment = result["environment"]
    material = {
        "model_version": 1,
        "implementation_sha256": implementation_sha256,
        "request": request,
        "identity": identity,
        "tool": {key: tool[key] for key in _TOOL_FIELDS},
        "host": {key: environment.get(key) for key in _HOST_FIELDS},
    }
    return hashlib.sha256(_canonical(material)).hexdigest()


def _missing(reason, *, state="Missing", elapsed=None, histories=(), source_ids=()):
    durations = list(histories)
    return {
        "evidence_state": state,
        "total_remaining_seconds": None,
        "elapsed_seconds": elapsed,
        "historical_total_seconds": ({"median": statistics.median(durations), "min": min(durations),
                                       "max": max(durations)} if durations else None),
        "source_run_ids": list(source_ids),
        "source_total_seconds": durations,
        "n": len(durations),
        "remaining_observed_range_seconds": None,
        "scope": _SCOPE,
        "reason": reason,
        "limitations": list(_LIMITATIONS),
    }


def _current_clock():
    try:
        return {
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "time_namespace": os.readlink("/proc/self/ns/time"),
        }
    except (FileNotFoundError, PermissionError, OSError) as error:
        return None, str(error)


def _finite_positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _storage_error(message, details=None):
    raise LabError("storage_failed", message, details)


def estimate_run_eta(store, result, owner_observation, now_ns=None):
    """Estimate total workflow time remaining from completed, like-for-like runs."""
    if result.get("status") != "running":
        return _missing("Run is terminal; no future workflow ETA applies")
    if not isinstance(owner_observation, dict) or owner_observation.get("alive") is not True:
        return _missing("Run owner is not confirmed alive")

    workflow = result.get("workflow_cost")
    if not isinstance(workflow, dict):
        return _missing("Run has no workflow monotonic timer")
    if workflow.get("schema_version") != 1 or workflow.get("method") != "monotonic-run-lifetime":
        return _missing("Run workflow timer is missing or uses an unsupported method")
    clock = workflow.get("clock")
    if not isinstance(clock, dict) or clock.get("name") != "CLOCK_MONOTONIC":
        return _missing("Run workflow clock context is missing", state="Unsupported")
    if not isinstance(clock.get("boot_id"), str) or not clock["boot_id"] or not isinstance(clock.get("time_namespace"), str) or not clock["time_namespace"]:
        return _missing("Run boot or time namespace metadata is unavailable", state="Unsupported")
    current_clock = _current_clock()
    if isinstance(current_clock, tuple):
        return _missing("Current monotonic clock context is unavailable: " + current_clock[1], state="Unsupported")
    if (clock.get("boot_id") != current_clock["boot_id"] or
            clock.get("time_namespace") != current_clock["time_namespace"]):
        return _missing("Run and query use different boot or time namespace", state="Unsupported")
    started = workflow.get("started_monotonic_ns")
    if not isinstance(started, int) or isinstance(started, bool) or started < 0:
        return _missing("Run start monotonic timestamp is missing or invalid")
    current_ns = time.monotonic_ns() if now_ns is None else now_ns
    if not isinstance(current_ns, int) or isinstance(current_ns, bool) or current_ns < started:
        return _missing("Current monotonic timestamp is invalid")
    elapsed = (current_ns - started) / 1_000_000_000
    key = workflow.get("cost_key")
    if not isinstance(key, str) or not key:
        return _missing("Run cost key is unavailable", elapsed=elapsed)

    matching = []
    for past in store.list("run"):
        if not isinstance(past, dict):
            _storage_error("Stored run record is not an object")
        if past.get("run_id") == result.get("run_id") or past.get("status") != "completed":
            continue
        correctness = past.get("correctness")
        if not isinstance(correctness, dict) or correctness.get("status") != "passed":
            continue
        old = past.get("workflow_cost")
        if not isinstance(old, dict) or old.get("cost_key") != key:
            continue
        implementation_hash = old.get("implementation_sha256")
        if not isinstance(implementation_hash, str) or not implementation_hash:
            _storage_error("Matching historical workflow cost has no implementation identity",
                           {"run_id": past.get("run_id")})
        try:
            recomputed_key = cost_key(past, implementation_hash)
        except (KeyError, TypeError, ValueError) as error:
            _storage_error("Matching historical run cannot reproduce its workflow cost key",
                           {"run_id": past.get("run_id"), "cause": str(error)})
        if recomputed_key != key:
            _storage_error("Matching historical workflow cost key does not match recorded run identity",
                           {"run_id": past.get("run_id")})
        if (old.get("schema_version") != 1 or old.get("method") != "monotonic-run-lifetime" or
                old.get("evidence_state") != "Measured"):
            _storage_error("Matching historical workflow cost has an invalid protocol", {"run_id": past.get("run_id")})
        duration = old.get("elapsed_seconds")
        if not _finite_positive(duration):
            _storage_error("Matching historical workflow duration is invalid", {"run_id": past.get("run_id")})
        run_id = past.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            _storage_error("Matching historical run has no valid run ID")
        matching.append((run_id, float(duration)))

    matching.sort(key=lambda item: item[0])
    source_ids = [item[0] for item in matching]
    durations = [item[1] for item in matching]
    if len(durations) < 2:
        return _missing("At least two completed matching runs are required", elapsed=elapsed,
                        histories=durations, source_ids=source_ids)
    median = statistics.median(durations)
    if elapsed >= median or elapsed > max(durations):
        return _missing("Forecast expired: elapsed time reached the historical median or exceeded the observed maximum",
                        elapsed=elapsed, histories=durations, source_ids=source_ids)
    low = min(durations) - elapsed
    high = max(durations) - elapsed
    return {
        "evidence_state": "Estimated",
        "total_remaining_seconds": median - elapsed,
        "elapsed_seconds": elapsed,
        "historical_total_seconds": {"median": median, "min": min(durations), "max": max(durations)},
        "source_run_ids": source_ids,
        "source_total_seconds": durations,
        "n": len(durations),
        "remaining_observed_range_seconds": [low if low >= 0 else None, high],
        "scope": _SCOPE,
        "reason": "Median total workflow duration minus current monotonic elapsed time",
        "limitations": list(_LIMITATIONS),
    }

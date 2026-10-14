"""Orchestration only; tools own measurement and parsers own normalization."""
import json
from pathlib import Path
import statistics

from .adapters import REGISTRY, EVENTS
from .environment import discover, fingerprints, binary_info
from .errors import LabError
from .execution import execute, check_execution
from .protocol import sha256
from .storage import write_json, utc, inventory


def probe_tools(store, tool_ids=None, options=None, target=None):
    identifier, directory = store.create("probes")
    probes = {}
    for tool in tool_ids or REGISTRY:
        if tool not in REGISTRY:
            raise LabError("invalid_request", "Unknown tool " + tool)
        probes[tool] = REGISTRY[tool].probe(directory / tool, (options or {}).get(tool, {}), target=target)
    result = {"schema_version": 1, "probe_id": identifier, "environment": discover(),
              "tools": probes, "evidence_directory": str(directory)}
    write_json(directory / "probe.json", result)
    return result


def plan(request, store):
    selected = request["measurement"]["tool"]
    if selected != "auto" and selected not in REGISTRY:
        raise LabError("invalid_request", "Unknown adapter " + selected)
    ids = list(REGISTRY) if selected == "auto" else [selected]
    eligible, invalid = [], {}
    for tool in ids:
        try:
            REGISTRY[tool].validate(request["measurement"]["options"])
            eligible.append(tool)
        except LabError as error:
            if selected != "auto":
                raise
            invalid[tool] = {"tool": tool, "available": False, "reason": "unsupported_options", "diagnostic": str(error)}
    if selected == "google-benchmark" and request["measurement"]["warmups"]:
        raise LabError("invalid_request", "Google Benchmark uses options.warmup_seconds; set warmups=0")
    probes = probe_tools(store, eligible, {tool: request["measurement"]["options"] for tool in eligible}, request["target"]) if eligible else {"tools": {}}
    probes["tools"].update(invalid)
    candidates = []
    rank = {"low": 0, "moderate": 1, "high": 2}
    for tool in ids:
        adapter = REGISTRY[tool]
        reasons = []
        if not probes["tools"][tool]["available"]:
            reasons.append(probes["tools"][tool]["reason"])
        goal = request.get("goal")
        if goal:
            configured_metrics = ([EVENTS[e][0] for e in request["measurement"]["options"].get("events", ["duration_time"]) if e in EVENTS]
                                  if tool == "perf-stat" else list(adapter.metrics))
            missing = set(goal["metrics"]) - set(configured_metrics)
            if missing:
                reasons.append("missing_metrics: " + ", ".join(sorted(missing)))
            if rank[adapter.overhead] > rank[goal["max_overhead"]]:
                reasons.append("overhead_budget_exceeded")
            if adapter.mode == "instrumentation" and not goal["allow_instrumentation"]:
                reasons.append("instrumentation_disallowed")
        candidates.append({"tool": tool, "feasible": not reasons, "reasons": reasons,
                           "overhead": adapter.overhead, "capabilities": list(adapter.metrics),
                           "limitations": list(adapter.limitations)})
    feasible = sorted((c for c in candidates if c["feasible"]), key=lambda c: (rank[c["overhead"]], ids.index(c["tool"])))
    return {"schema_version": 1, "selected": feasible[0]["tool"] if feasible else None,
            "selection_basis": "required metric coverage, actual probe, overhead and instrumentation constraints; registry order breaks ties",
            "candidates": candidates, "probes": probes,
            "steps": ["fingerprint artifacts", "correctness command", "target preflight", "warmup", "measurement", "verify unchanged inputs", "normalize and archive"],
            "note": "No silent fallback after failure. Integrated Google Benchmark capability probes execute the configured target; external-tool probes use small system programs."}


def summarize(samples):
    groups = {}
    for sample in samples:
        for item in sample["metrics"]:
            key = (item["name"], item["unit"], item["method"], item["scope"])
            groups.setdefault(key, []).append(item["value"])
    summaries = []
    for (name, unit, method, scope), values in groups.items():
        mean = statistics.mean(values)
        sd = statistics.stdev(values) if len(values) > 1 else None
        median = statistics.median(values)
        summaries.append({"name": name, "unit": unit, "method": method, "scope": scope,
                          "n": len(values), "median": median, "mean": mean,
                          "min": min(values), "max": max(values), "stdev": sd,
                          "cv": sd / mean if sd is not None and mean else None,
                          "mad": statistics.median(abs(v - median) for v in values)})
    return summaries


def run(request, store):
    identifier, directory = store.create()
    result = {"schema_version": 1, "run_id": identifier, "status": "running", "started_at": utc(),
              "request": request, "environment": discover(), "samples": [], "hotspots": [],
              "summary": [], "correctness": {"status": "not_run"}, "error": None,
              "evidence_directory": str(directory)}
    write_json(directory / "request.json", request)
    write_json(directory / "result.json", result)
    try:
        result["plan"] = plan(request, store)
        selected = result["plan"]["selected"]
        if selected is None:
            raise LabError("unavailable", "No feasible tool", result["plan"])
        adapter = REGISTRY[selected]
        result["tool"] = {**adapter.describe(), **result["plan"]["probes"]["tools"][selected]}
        result["identity"] = fingerprints(request, directory)
        result["binary"] = binary_info(request, directory)
        target, config = request["target"], request["measurement"]

        def launch(command, folder, tool=False):
            folder.mkdir(exist_ok=True)
            # Only {run_dir} is a template; literal shell syntax stays literal argv.
            command = [s.replace("{run_dir}", str(folder)) for s in command]
            record = execute(command, folder, cwd=target.get("cwd", str(folder)),
                             env=target["environment"], timeout=config["timeout_seconds"],
                             affinity=config.get("cpu_affinity"), limits=config.get("limits"))
            write_json(folder / "execution.json", record)
            check_execution(record)
            if record["returncode"] and not tool:
                raise LabError("target_failed", "Target exited nonzero", {"returncode": record["returncode"], "evidence": str(folder)})
            return record

        try:
            launch(request["correctness"]["argv"], directory / "correctness")
        except LabError as error:
            if error.category == "target_failed":
                result["correctness"]["status"] = "failed"
                raise LabError("correctness_failed", "Correctness command failed", error.details) from error
            raise
        result["correctness"] = {"status": "passed", "description": request["correctness"]["description"],
                                 "evidence": "correctness/execution.json",
                                 "meaning": "user-provided oracle exited zero; not a proof of numerical correctness"}
        if selected != "google-benchmark":
            launch(target["argv"], directory / "preflight")
        if not adapter.batch:
            for i in range(config["warmups"]):
                launch(target["argv"], directory / ("warmup-%03d" % i))
        count = 1 if adapter.batch else config["repetitions"]
        for index in range(count):
            folder = directory / ("measurement-%03d" % index)
            folder.mkdir()
            target_argv = [s.replace("{run_dir}", str(folder)) for s in target["argv"]]
            command = adapter.prepare(target_argv, folder, config["options"],
                                      repetitions=config["repetitions"], warmups=config["warmups"])
            record = launch(command, folder, tool=True)
            try:
                parsed = adapter.parse(folder, config["options"], record)
            except (OSError, ValueError, KeyError, TypeError) as error:
                category = "tool_failed" if record["returncode"] else "parse_failed"
                raise LabError(category, "Cannot normalize tool output", {"reason": str(error), "evidence": str(folder)}) from error
            write_json(folder / "normalized.json", parsed)
            if "library_version" in parsed:
                result["tool"]["version"] = parsed["library_version"]
            if "sample_count" in parsed:
                result.setdefault("sampling_quality", []).append({
                    "measurement": index, "sample_count": parsed["sample_count"],
                    "unknown_symbol_self_percent": parsed["unknown_symbol_self_percent"]})
            for metrics in parsed.get("samples", [parsed["metrics"]]):
                for item in metrics:
                    item["evidence"] = str(folder.relative_to(directory) / item["evidence"])
                    item["tool"] = selected
                    item["tool_version"] = result["tool"]["version"]
                result["samples"].append({"index": len(result["samples"]), "metrics": metrics})
            result["hotspots"].extend({**h, "sample": index,
                                       "evidence": str(folder.relative_to(directory) / h["evidence"])} for h in parsed["hotspots"])
            if selected == "massif":
                # Full allocation trees are already retained; human rendering is optional.
                from shutil import which
                if which("ms_print"):
                    rendering = launch([which("ms_print"), str(folder / "massif.out")], folder / "render", tool=True)
                    if rendering["returncode"]:
                        raise LabError("tool_failed", "Massif rendering failed; raw data retained")
            result.setdefault("limitations", []).extend(parsed.get("limitations", []))
        if len(result["samples"]) != config["repetitions"]:
            raise LabError("parse_failed", "Unexpected number of measurement samples")
        for category, items in result["identity"].items():
            for path, original in items.items():
                if sha256(path) != original:
                    raise LabError("invalid_request", category + " changed during measurement", {"path": path})
        result["summary"] = summarize(result["samples"])
        result["status"] = "completed"
    except KeyboardInterrupt:
        result["status"] = "cancelled"
        result["error"] = LabError("cancelled", "Run interrupted").as_dict()
    except LabError as error:
        result["status"] = error.category if error.category in ("cancelled", "timeout") else "failed"
        result["error"] = error.as_dict()
    except OSError as error:
        result["status"] = "failed"
        result["error"] = LabError("storage_failed", str(error)).as_dict()
    except Exception as error:
        import traceback
        (directory / "internal-error.txt").write_text(traceback.format_exc())
        result["status"] = "failed"
        result["error"] = LabError("internal_error", str(error)).as_dict()
    finally:
        result["ended_at"] = utc()
        result["environment_after"] = discover()
        result["evidence"] = inventory(directory)
        write_json(directory / "result.json", result)
    return result

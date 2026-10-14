"""Conservative comparisons of semantically identical metrics, with raw samples."""
import random
import statistics

from .errors import LabError

MIN_COMPARISON_SAMPLES = 7
MAX_COMPARISON_CV = .10


def compare(a, b, metric_name="time.wall", allow_command_change=False,
            regression_fraction=0.05):
    differences = []
    def same(label, left, right):
        if left != right:
            differences.append({"condition": label, "baseline": left, "candidate": right})

    same("completed", a["status"], "completed")
    same("candidate_completed", b["status"], "completed")
    if a["status"] != "completed" or b["status"] != "completed":
        raise LabError("incomparable", "Only completed experiments can be compared", {"differences": differences})
    for key in ("id", "parameters"):
        same("workload." + key, a["request"]["workload"][key], b["request"]["workload"][key])
    same("inputs", a["identity"]["inputs"], b["identity"]["inputs"])
    for key in ("id", "version", "mode"):
        same("tool." + key, a["tool"][key], b["tool"][key])
    for key in ("os", "kernel", "machine", "hostname", "cpu_model", "cpu_flags", "allowed_cpus", "governor"):
        same("environment." + key, a["environment"][key], b["environment"][key])
    for key in ("cpu_affinity", "options", "warmups", "limits"):
        same("measurement." + key, a["request"]["measurement"].get(key), b["request"]["measurement"].get(key))
    for key in ("environment", "cwd"):
        same("target." + key, a["request"]["target"].get(key), b["request"]["target"].get(key))
    same("build.mode", a["request"]["target"]["build"]["mode"], b["request"]["target"]["build"]["mode"])
    if not allow_command_change:
        same("target.arguments", a["request"]["target"]["argv"][1:], b["request"]["target"]["argv"][1:])
    same("correctness", a["correctness"]["status"], b["correctness"]["status"])
    same("oracle.description", a["request"]["correctness"]["description"], b["request"]["correctness"]["description"])
    collections = [[m for s in r["samples"] for m in s["metrics"] if m["name"] == metric_name] for r in (a, b)]
    if any(not values for values in collections):
        differences.append({"condition": "missing_metric", "metric": metric_name})
    else:
        meanings = [{(m["unit"], m["method"], m["scope"]) for m in values} for values in collections]
        same("metric_semantics", sorted(meanings[0]), sorted(meanings[1]))
        if any(len(x) != 1 for x in meanings):
            differences.append({"condition": "mixed_metric_semantics"})
        if any(m.get("running_percent", 100) < 99 for values in collections for m in values):
            differences.append({"condition": "incomplete_counter_running_time"})
    if differences:
        raise LabError("incomparable", "Measurement conditions differ", {"differences": differences})
    av, bv = [[m["value"] for m in values] for values in collections]
    am, bm = statistics.median(av), statistics.median(bv)
    if am <= 0 or bm <= 0:
        raise LabError("incomparable", "Ratios require positive medians")
    rng = random.Random(1014)
    ratios = sorted(statistics.median(rng.choices(av, k=len(av))) /
                    statistics.median(rng.choices(bv, k=len(bv))) for _ in range(5000))
    ci = [ratios[125], ratios[4874]]
    cv = [statistics.stdev(v) / statistics.mean(v) if len(v) > 1 else None for v in (av, bv)]
    sufficient = min(len(av), len(bv)) >= MIN_COMPARISON_SAMPLES
    stable = all(v is not None and v <= MAX_COMPARISON_CV for v in cv)
    if not sufficient or not stable:
        classification = "inconclusive"
    elif ci[1] < 1 / (1 + regression_fraction):
        classification = "observed_regression"
    elif ci[0] > 1 + regression_fraction:
        classification = "observed_improvement"
    else:
        classification = "no_clear_change"
    return {"schema_version": 1, "baseline_id": a["run_id"], "candidate_id": b["run_id"],
            "comparable": True, "metric": metric_name, "unit": collections[0][0]["unit"],
            "baseline_samples": av, "candidate_samples": bv, "baseline_median": am,
            "candidate_median": bm, "speedup": am / bm, "change_fraction": bm / am - 1,
            "bootstrap_95_percent_speedup": ci, "coefficient_of_variation": cv,
            "classification": classification, "sufficient_samples": sufficient, "stable_samples": stable,
            "policy": {"min_samples": MIN_COMPARISON_SAMPLES, "max_cv": MAX_COMPARISON_CV, "material_fraction": regression_fraction,
                       "bootstrap_resamples": 5000, "seed": 1014, "paired": False},
            "treatment": {"baseline_argv": a["request"]["target"]["argv"],
                          "candidate_argv": b["request"]["target"]["argv"],
                          "baseline_build": a["request"]["target"]["build"],
                          "candidate_build": b["request"]["target"]["build"],
                          "command_change_acknowledged": allow_command_change},
            "limitations": ["Unpaired bootstrap is descriptive and cannot remove thermal/load/order drift.",
                            "An observed improvement is not automatic optimization acceptance; repeat interleaved A/B and review oracle coverage."]}


def analyze(result):
    facts = [{"kind": "observation", "metric": summary} for summary in result["summary"]]
    hotspots = sorted(result["hotspots"], key=lambda x: x["self_percent"], reverse=True)
    for item in hotspots[:20]:
        facts.append({"kind": "observation", "hotspot": item})
    hypotheses = []
    if hotspots:
        hypotheses.append({"kind": "hypothesis", "statement": "Inspect high self-sample symbols and their production callers; sampling does not establish the cause.",
                           "evidence": [h["evidence"] for h in hotspots[:3]],
                           "next_measurement": "Choose counters, algorithm-scale runs or memory profiling based on source and data access."})
    return {"schema_version": 1, "run_id": result["run_id"], "status": result["status"],
            "facts": facts, "hypotheses": hypotheses,
            "sampling_quality": result.get("sampling_quality", []),
            "limitations": result.get("limitations", []) + result["environment"]["limitations"],
            "error": result["error"], "optimization_accepted": False}

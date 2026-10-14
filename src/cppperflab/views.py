"""Shared evidence-backed views for Agent queries and HTML presentation.

No measurement or statistical estimation lives here. Run summaries, comparisons
and empirical fits are produced by the existing measurement/analysis modules.
"""
from collections import Counter
from .comparison import analyze, MIN_COMPARISON_SAMPLES, MAX_COMPARISON_CV
from .errors import LabError
from .protocol import sha256


SECTIONS = ("overview", "conditions", "metrics", "hotspots", "quality", "evidence",
            "facts", "hypotheses", "growth", "comparisons")


def evidence_id(run_id, path):
    return run_id + ":" + path


def base_view(kind, identifier, status, error):
    return {"schema_version": 1, "view_kind": kind, "record_id": identifier,
            "status": status, "error": error, "optimization_accepted": False,
            "overview": {}, "conditions": {}, "metrics": [], "hotspots": [],
            "quality": [], "evidence": [], "facts": [], "hypotheses": [],
            "growth": [], "comparisons": [], "limitations": []}


def run_view(result):
    rid = result["run_id"]
    view = base_view("run", rid, result["status"], result["error"])
    request = result["request"]
    view["overview"] = {"name": request["name"], "workload": request["workload"],
                        "correctness": result["correctness"], "tool": result.get("tool"),
                        "started_at": result["started_at"], "ended_at": result.get("ended_at"),
                        "metric_groups": len(result["summary"]), "samples": len(result["samples"]),
                        "hotspot_rows": len(result["hotspots"])}
    view["conditions"] = {"target": request["target"], "measurement": request["measurement"],
                          "environment": result["environment"], "identity": result.get("identity"),
                          "binary": result.get("binary"), "oracle": request["correctness"]}
    for summary in result["summary"]:
        matching = [(s["index"], m) for s in result["samples"] for m in s["metrics"]
                    if all(m[k] == summary[k] for k in ("name", "unit", "method", "scope"))]
        view["metrics"].append({"summary": summary,
                                "samples": [{"index": i, "value": m["value"],
                                             "evidence_id": evidence_id(rid, m["evidence"])}
                                            for i, m in matching]})
        # Use the comparison engine's documented initial screening policy.
        if summary["n"] < MIN_COMPARISON_SAMPLES:
            view["quality"].append({"code": "insufficient_repetitions", "severity": "review",
                                    "metric": summary["name"], "observed": summary["n"],
                                    "policy_min_samples": MIN_COMPARISON_SAMPLES,
                                    "message": "Too few repetitions for the current comparison policy."})
        if summary["cv"] is not None and summary["cv"] > MAX_COMPARISON_CV:
            view["quality"].append({"code": "high_variability", "severity": "review",
                                    "metric": summary["name"], "observed_cv": summary["cv"],
                                    "policy_max_cv": MAX_COMPARISON_CV,
                                    "message": "High variability; a stable improvement is not established."})
        for _, metric in matching:
            if metric.get("running_percent", 100) < 99:
                view["quality"].append({"code": "incomplete_counter", "severity": "review",
                                        "metric": metric["name"], "running_percent": metric["running_percent"],
                                        "message": "Unscaled count does not represent full execution."})
    for hotspot in sorted(result["hotspots"], key=lambda h: h["self_percent"], reverse=True):
        view["hotspots"].append({**hotspot, "evidence_id": evidence_id(rid, hotspot["evidence"])})
    for sampling in result.get("sampling_quality", []):
        view["quality"].append({"code": "sampling_coverage", "severity": "review", **sampling,
                                "message": "On-CPU sample coverage only; no causal or wall-time conclusion. Review sample count for the question being asked."})
    if result["correctness"]["status"] != "passed":
        view["quality"].append({"code": "correctness_not_passed", "severity": "error",
                                "message": "Correctness gate has not passed."})
    if result["error"]:
        view["quality"].append({"code": "execution_failed", "severity": "error",
                                "message": result["error"]["message"]})
    for item in result.get("evidence", []):
        view["evidence"].append({**item, "id": evidence_id(rid, item["path"]), "run_id": rid})
    existing = analyze(result)
    view["facts"], view["hypotheses"] = existing["facts"], existing["hypotheses"]
    view["limitations"] = list(dict.fromkeys(existing["limitations"] + [
        "Hotspot rows belong to individual sampling repetitions; percentages are not summed across runs.",
        "Correctness passed means the declared command exited zero within its stated coverage."]))
    return view


def comparison_view(result):
    if not result["comparable"]:
        view = base_view("comparison", result["comparison_id"], "incomparable", result["error"])
        view["overview"] = {k: result[k] for k in ("baseline_id", "candidate_id", "metric", "comparable")}
        view["comparisons"] = [result]
        view["conditions"] = result["error"]["details"]
        view["quality"] = [{"code": "incomparable", "severity": "error",
                            "message": "Measurement conditions differ; no ratios or improvement classification were produced."}]
        view["limitations"] = ["Resolve the recorded semantic/condition differences before a performance comparison."]
        return view
    view = base_view("comparison", result["comparison_id"], "completed", None)
    view["overview"] = {key: result[key] for key in (
        "baseline_id", "candidate_id", "metric", "unit", "comparable", "classification",
        "baseline_median", "candidate_median", "speedup", "change_fraction",
        "bootstrap_95_percent_speedup", "coefficient_of_variation")}
    view["conditions"] = {"policy": result["policy"], "treatment": result["treatment"]}
    view["comparisons"] = [result]
    view["facts"] = [{"kind": "statistical_inference", "comparison": result}]
    view["quality"] = [{"code": result["classification"],
                        "severity": "review" if result["classification"] == "inconclusive" else "info",
                        "sufficient_samples": result["sufficient_samples"],
                        "stable_samples": result["stable_samples"],
                        "message": "Classification uses the recorded policy; no automatic optimization acceptance."}]
    view["limitations"] = result["limitations"]
    return view


def study_view(result):
    view = base_view("study", result["study_id"], result["status"], result["error"])
    spec = result["spec"]
    view["overview"] = {"name": spec["name"], "axis": spec["axis"], "metric": spec["metric"],
                        "runs": result["runs"], "variants": [v["id"] for v in spec["variants"]]}
    view["conditions"] = {"spec": spec, "started_at": result["started_at"],
                          "ended_at": result.get("ended_at")}
    meanings = set()
    for variant, fitted in result["growth"].items():
        points = fitted["points"]
        for point in points:
            meanings.add(tuple(point[k] for k in ("name", "unit", "method", "scope")))
        view["growth"].append({"variant": variant, **fitted})
        view["limitations"].extend(fitted["limitations"])
        view["quality"].append({"code": "empirical_growth", "severity": "review",
                                "variant": variant,
                                "message": "Finite-range empirical fit; not a mathematical Big-O proof."})
        for point in points:
            if point["n"] < MIN_COMPARISON_SAMPLES or point["cv"] is None or point["cv"] > MAX_COMPARISON_CV:
                view["quality"].append({"code": "growth_point_uncertain", "severity": "review",
                                        "variant": variant, "scale": point["x"], "run_id": point["run_id"],
                                        "n": point["n"], "cv": point["cv"],
                                        "message": "Model ranking includes an uncertain measurement point."})
    view["conditions"]["growth_metric_semantics"] = [dict(zip(("name", "unit", "method", "scope"), m))
                                                       for m in sorted(meanings)]
    if len(meanings) > 1:
        view["quality"].append({"code": "mixed_growth_semantics", "severity": "error",
                                "message": "Growth series contain different metric meanings; do not overlay or rank them together."})
    view["comparisons"] = result["comparisons"]
    view["facts"] = [{"kind": "observation", "run": r} for r in result["runs"]]
    view["limitations"] = list(dict.fromkeys(view["limitations"] + [
        "A study evaluates reviewed prebuilt variants; it is not an automatic optimizer.",
        "Each scale comparison retains its own classification; the fastest point does not select a production algorithm."]))
    return view


def build_view(store, identifier, kind="run"):
    record = store.read(identifier, kind)
    builders = {"run": run_view, "comparison": comparison_view, "study": study_view}
    view = builders[kind](record)
    # Comparison/study evidence remains in the same run store. Failed runs can
    # legitimately have no inventory yet; their error and logs stay at their ID.
    if kind != "run":
        run_ids = ([record["baseline_id"], record["candidate_id"]] if kind == "comparison"
                   else [r["run_id"] for r in record["runs"]])
        for rid in dict.fromkeys(run_ids):
            run = store.read(rid)
            view["evidence"].extend({**item, "id": evidence_id(rid, item["path"]), "run_id": rid}
                                    for item in run.get("evidence", []))
    return view


def query_view(view, section="overview", *, metric=None, symbol=None, offset=0, limit=20):
    if section not in SECTIONS or offset < 0 or not 1 <= limit <= 200:
        raise LabError("invalid_request", "Invalid analysis section or pagination (limit 1..200)")
    if metric is not None and section not in ("metrics", "growth", "comparisons"):
        raise LabError("invalid_request", "--metric requires metrics, growth or comparisons section")
    if symbol is not None and section != "hotspots":
        raise LabError("invalid_request", "--symbol requires hotspots section")
    data = view[section]
    if isinstance(data, list):
        if metric is not None:
            if section == "metrics":
                data = [r for r in data if r["summary"]["name"] == metric]
            elif section == "growth":
                data = [r for r in data if any(p["name"] == metric for p in r["points"])]
            else:
                data = [r for r in data if r["metric"] == metric]
        if symbol is not None:
            data = [r for r in data if symbol.casefold() in r["symbol"].casefold()]
        total = len(data)
        data = data[offset:offset + limit]
        pagination = {"offset": offset, "limit": limit, "total": total,
                      "next_offset": offset + limit if offset + limit < total else None}
    else:
        if offset:
            raise LabError("invalid_request", "Object sections do not support offset")
        pagination = None
    return {"schema_version": 1, "view_kind": view["view_kind"], "record_id": view["record_id"],
            "status": view["status"], "error": view["error"], "section": section,
            "data": data, "pagination": pagination, "available_sections": list(SECTIONS),
            "quality_summary": [{"code": code, "severity": severity, "count": count}
                                for (code, severity), count in Counter(
                                    (q["code"], q["severity"]) for q in view["quality"]).items()],
            "limitations": view["limitations"], "optimization_accepted": False}


def evidence_path(store, run_id, relative_path):
    run = store.read(run_id)
    entries = [e for e in run.get("evidence", []) if e["path"] == relative_path]
    if len(entries) != 1:
        raise LabError("not_found", "Evidence path is not in the run inventory")
    root = store.record_path(run_id).parent.resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise LabError("invalid_request", "Evidence path escapes its run directory")
    if not path.is_file():
        raise LabError("not_found", "Archived evidence file is missing")
    return path, entries[0]


def read_evidence(store, run_id, relative_path, *, offset=0, limit=80):
    if offset < 0 or not 1 <= limit <= 500:
        raise LabError("invalid_request", "Invalid line pagination (limit 1..500)")
    path, entry = evidence_path(store, run_id, relative_path)
    if sha256(path) != entry["sha256"]:
        raise LabError("storage_failed", "Evidence hash differs from the recorded inventory")
    # Native binary data is preserved for its owning tool, not decoded as text.
    lines, total = [], 0
    try:
        with path.open(encoding="utf-8") as stream:
            for total, line in enumerate(stream, 1):
                if "\x00" in line:
                    raise LabError("invalid_request", "Binary evidence requires its native tool")
                if offset < total <= offset + limit:
                    lines.append({"line": total, "text": line.rstrip("\n")})
    except UnicodeDecodeError as error:
        raise LabError("invalid_request", "Evidence is not UTF-8 text; use its native tool") from error
    return {"schema_version": 1, "id": evidence_id(run_id, relative_path), "run_id": run_id,
            "path": relative_path, "sha256": entry["sha256"], "integrity": "verified",
            "lines": lines, "pagination": {"offset": offset, "limit": limit, "total": total,
                                           "next_offset": offset + limit if offset + limit < total else None}}

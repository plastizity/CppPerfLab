"""Algorithm experiments reuse lab.run and the version-1 measurement protocol.

This module proposes no code and compiles nothing. Candidates must be reviewed,
prebuilt executables. A study manifest is an index of ordinary experiment IDs.
"""
import copy
import json
import math
from pathlib import Path
import random
import statistics

from .comparison import compare, MIN_COMPARISON_SAMPLES, MAX_COMPARISON_CV
from .errors import LabError
from .lab import run
from .protocol import fields, load_request, require, positive
from .storage import write_json, utc


MODELS = {
    "constant": lambda n: 0.0,
    "log_n": math.log,
    "n": lambda n: n,
    "n_log_n": lambda n: n * math.log(n),
    "n_squared": lambda n: n * n,
    "n_cubed": lambda n: n * n * n,
}


def linear_fit(xs, ys):
    """Fit y = a*x+b with nonnegative a,b; enumerate 2D boundary optima."""
    mx, my = statistics.mean(xs), statistics.mean(ys)
    variance = sum((x - mx) ** 2 for x in xs)
    choices = [(0.0, my)]
    if variance:
        a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / variance
        b = my - a * mx
        if a >= 0 and b >= 0:
            choices.append((a, b))
    if sum(x * x for x in xs):
        choices.append((max(0, sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)), 0.0))
    return min(choices, key=lambda ab: sum((y - ab[0] * x - ab[1]) ** 2 for x, y in zip(xs, ys)))


def fit_growth(points):
    require(len(points) >= 4, "Growth fitting needs at least four distinct measured scales")
    points = sorted(points, key=lambda p: p["x"])
    ns = [p["x"] for p in points]
    require(all(type(n) in (int, float) and math.isfinite(n) and n > 0 for n in ns)
            and len(ns) == len(set(ns)), "Scales must be positive, finite and unique")
    ys = [p["median"] for p in points]
    require(all(math.isfinite(y) and y > 0 for y in ys), "Growth fitting requires positive finite measurements")
    variance = sum((y - statistics.mean(ys)) ** 2 for y in ys)
    models = []
    for name, transform in MODELS.items():
        xs = [transform(n) for n in ns]
        a, b = linear_fit(xs, ys)
        residuals = [y - (a * x + b) for x, y in zip(xs, ys)]
        cross_errors = []
        for i in range(len(xs)):
            aa, bb = linear_fit(xs[:i] + xs[i + 1:], ys[:i] + ys[i + 1:])
            cross_errors.append(ys[i] - (aa * xs[i] + bb))
        models.append({"model": name, "scale_coefficient": a, "intercept": b,
                       "r_squared": 1 - sum(r * r for r in residuals) / variance if variance else None,
                       "relative_rmse": math.sqrt(statistics.mean(r * r for r in residuals)) / statistics.mean(ys),
                       "leave_one_out_relative_rmse": math.sqrt(statistics.mean(r * r for r in cross_errors)) / statistics.mean(ys)})
    models.sort(key=lambda m: m["leave_one_out_relative_rmse"])
    slopes = [{"from": ns[i - 1], "to": ns[i],
               "empirical_log_slope": math.log(ys[i] / ys[i - 1]) / math.log(ns[i] / ns[i - 1])} for i in range(1, len(ns))]
    warnings = ["Empirical models over the measured range only; not a Big-O proof.",
                "Startup, oracle work, cache transitions and a limited size range can distort model ranking.",
                "All other input features must remain fixed; no automatic runtime algorithm dispatch is installed."]
    if len(points) < 5:
        warnings.append("Fewer than five scales: model ranking is weak evidence.")
    if any(p["n"] < MIN_COMPARISON_SAMPLES or p.get("cv") is None or p["cv"] > MAX_COMPARISON_CV for p in points):
        warnings.append("Some scales have too few repeats or high noise; trend requires confirmation.")
    return {"kind": "statistical_inference", "points": points, "models": models,
            "local_slopes": slopes, "measured_range": [ns[0], ns[-1]], "limitations": warnings}


def load_study(path):
    path = Path(path).resolve()
    try:
        spec = json.loads(path.read_text())
    except (ValueError, OSError) as error:
        raise LabError("invalid_request", "Cannot read study", {"reason": str(error)}) from error
    fields(spec, ("schema_version", "name", "template", "axis", "variants", "budget", "seed", "metric", "constraints"),
           ("schema_version", "name", "template", "axis", "variants", "budget", "seed", "metric", "constraints"), "study")
    require(type(spec["schema_version"]) is int and spec["schema_version"] == 1, "Unsupported study schema")
    require(all(isinstance(spec[k], str) and spec[k] for k in ("name", "template", "metric", "constraints")), "study textual fields must be nonempty")
    positive(spec["budget"], "budget", integer=True)
    require(type(spec["seed"]) is int, "seed must be an integer")
    spec["template"] = str((path.parent / spec["template"]).resolve())
    template = load_request(spec["template"])
    axis = spec["axis"]
    fields(axis, ("parameter", "values", "argument_index"), ("parameter", "values", "argument_index"), "axis")
    require(isinstance(axis["parameter"], str) and bool(axis["parameter"]), "axis.parameter required")
    require(isinstance(axis["values"], list) and bool(axis["values"]), "axis.values required")
    for value in axis["values"]:
        positive(value, "scale")
    require(len(axis["values"]) == len(set(axis["values"])), "duplicate scales")
    index = axis["argument_index"]
    require(type(index) is int and 1 <= index < len(template["target"]["argv"]), "argument_index must address a target argument, not executable")
    require(isinstance(spec["variants"], list) and bool(spec["variants"]), "variants required")
    ids = []
    for variant in spec["variants"]:
        fields(variant, ("id", "executable", "revision", "artifacts"), ("id", "executable", "revision", "artifacts"), "variant")
        require(all(isinstance(variant[k], str) and bool(variant[k]) for k in ("id", "executable", "revision")), "variant identity invalid")
        require(isinstance(variant["artifacts"], list) and all(isinstance(p, str) for p in variant["artifacts"]), "variant artifacts invalid")
        variant["executable"] = str((path.parent / variant["executable"]).resolve())
        variant["artifacts"] = [str((path.parent / p).resolve()) for p in variant["artifacts"]]
        ids.append(variant["id"])
    require(len(ids) == len(set(ids)), "duplicate variant IDs")
    needed = len(ids) * len(axis["values"])
    require(needed <= spec["budget"], "Study needs {} experiments, exceeds budget {}".format(needed, spec["budget"]))
    return spec, template


def study(path, store):
    spec, template = load_study(path)
    identifier, directory = store.create("studies")
    result = {"schema_version": 1, "study_id": identifier, "status": "running", "spec": spec,
              "started_at": utc(), "runs": [], "comparisons": [], "growth": {}, "error": None}
    write_json(directory / "study.json", result)
    jobs = [(variant, value) for value in spec["axis"]["values"] for variant in spec["variants"]]
    random.Random(spec["seed"]).shuffle(jobs)
    results = {}
    try:
        for index, (variant, scale) in enumerate(jobs):
            request = copy.deepcopy(template)
            request["name"] = spec["name"] + "/" + variant["id"] + "/" + str(scale)
            request["workload"]["parameters"][spec["axis"]["parameter"]] = scale
            request["target"]["argv"][0] = variant["executable"]
            request["target"]["argv"][spec["axis"]["argument_index"]] = str(scale)
            request["target"]["build"]["revision"] = variant["revision"]
            request["target"]["build"]["artifacts"] = variant["artifacts"]
            # The independent oracle receives the candidate executable as argv, never rewritten by a candidate.
            request["correctness"]["argv"] = [s.replace("{candidate}", variant["executable"]) for s in request["correctness"]["argv"]]
            config = directory / ("request-%03d.json" % index)
            write_json(config, request)
            measured = run(load_request(config), store)
            result["runs"].append({"variant": variant["id"], "scale": scale, "run_id": measured["run_id"], "status": measured["status"]})
            write_json(directory / "study.json", result)
            if measured["status"] != "completed":
                raise LabError(measured["error"]["category"], "Candidate rejected; no score assigned", {"run_id": measured["run_id"], "error": measured["error"]})
            results[(variant["id"], scale)] = measured
        for variant in spec["variants"]:
            points = []
            for scale in spec["axis"]["values"]:
                measured = results[(variant["id"], scale)]
                summaries = [s for s in measured["summary"] if s["name"] == spec["metric"]]
                require(len(summaries) == 1, "Study metric missing or has mixed semantics")
                points.append({"x": scale, "run_id": measured["run_id"], **summaries[0]})
            result["growth"][variant["id"]] = fit_growth(points) if len(points) >= 4 else {"points": points, "limitations": ["Need at least four scales for fitting"]}
        first = spec["variants"][0]["id"]
        for scale in spec["axis"]["values"]:
            for variant in spec["variants"][1:]:
                c = compare(results[(first, scale)], results[(variant["id"], scale)], spec["metric"])
                result["comparisons"].append({"scale": scale, "baseline_variant": first, "candidate_variant": variant["id"], **c})
        result["status"] = "completed"
    except LabError as error:
        result["status"] = "failed"
        result["error"] = error.as_dict()
    except KeyboardInterrupt:
        result["status"] = "cancelled"
        result["error"] = LabError("cancelled", "Study interrupted").as_dict()
    finally:
        result["ended_at"] = utc()
        write_json(directory / "study.json", result)
    return result

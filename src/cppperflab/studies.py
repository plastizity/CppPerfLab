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
from .metrics import evidence_state
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
    semantic_keys = ('name','unit','method','scope')
    if any(any(k in p for k in semantic_keys) for p in points):
        require(all(all(k in p for k in semantic_keys) for p in points), 'Missing growth metric semantics')
        require(len({tuple(p[k] for k in semantic_keys) for p in points})==1, 'Mixed growth metric semantics')
    from .perf_events import METRIC_SELECTORS, DESCRIPTOR_FIELDS
    if any(p.get('name') in METRIC_SELECTORS for p in points):
        require(all(all(k in p for k in DESCRIPTOR_FIELDS) for p in points), 'Missing growth event descriptor')
        require(len({tuple(p[k] for k in DESCRIPTOR_FIELDS) for p in points})==1, 'Mixed growth event descriptor semantics')
    require(all(p.get('evidence_state','Measured')=='Measured' for p in points), 'Growth requires measured input points')
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
        models.append({"model": name, "evidence_state":"Modeled", "scale_coefficient": a, "intercept": b,
                       "expression": ({"constant": format(b, '.10g'),
                                       "log_n": f"{a:.10g} * log(x) + {b:.10g}",
                                       "n": f"{a:.10g} * x + {b:.10g}",
                                       "n_log_n": f"{a:.10g} * x * log(x) + {b:.10g}",
                                       "n_squared": f"{a:.10g} * x^2 + {b:.10g}",
                                       "n_cubed": f"{a:.10g} * x^3 + {b:.10g}"})[name],
                       "model_family": "polynomial" if name in ("constant", "n", "n_squared", "n_cubed") else "non_polynomial",
                       "r_squared": 1 - sum(r * r for r in residuals) / variance if variance else None,
                       "relative_rmse": math.sqrt(statistics.mean(r * r for r in residuals)) / statistics.mean(ys),
                       "leave_one_out_relative_rmse": math.sqrt(statistics.mean(r * r for r in cross_errors)) / statistics.mean(ys)})
    models.sort(key=lambda m: m["leave_one_out_relative_rmse"])
    slopes = [{"from": ns[i - 1], "to": ns[i],
               "evidence_state":"Estimated", "growth_ratio":ys[i]/ys[i-1],
               "source_run_ids":[points[i-1].get("run_id"),points[i].get("run_id")],
               "empirical_log_slope": math.log(ys[i] / ys[i - 1]) / math.log(ns[i] / ns[i - 1])} for i in range(1, len(ns))]
    warnings = ["Empirical models over the measured range only; not a Big-O proof.",
                "Startup, oracle work, cache transitions and a limited size range can distort model ranking.",
                "All other input features must remain fixed; no automatic runtime algorithm dispatch is installed."]
    if len(points) < 5:
        warnings.append("Fewer than five scales: model ranking is weak evidence.")
    if any(p["n"] < MIN_COMPARISON_SAMPLES or p.get("cv") is None or p["cv"] > MAX_COMPARISON_CV for p in points):
        warnings.append("Some scales have too few repeats or high noise; trend requires confirmation.")
    point_quality=[{'x':p['x'],'run_id':p.get('run_id'),
                    'sufficient_samples':p['n']>=MIN_COMPARISON_SAMPLES,
                    'stable_samples':p.get('cv') is not None and p['cv']<=MAX_COMPARISON_CV} for p in points]
    return {"kind": "statistical_inference", "points": points, "models": models,
            "quality":{'status':'PASS' if all(p['sufficient_samples'] and p['stable_samples'] for p in point_quality) else 'INCONCLUSIVE',
                       'points':point_quality,'policy':{'min_samples':MIN_COMPARISON_SAMPLES,'max_cv':MAX_COMPARISON_CV},
                       'meaning':'Measurement screening only, not proof that the best growth model is correct'},
            "doubling_ratios": [{"from":ns[i], "to":ns[j], "value":ys[j]/ys[i],
                                 "name":"algorithm.doubling_ratio", "evidence_state":"Estimated",
                                 "source_run_ids":[points[i].get("run_id"),points[j].get("run_id")]}
                                for i in range(len(ns)) for j in range(i+1,len(ns)) if ns[j]==2*ns[i]],
            "local_slopes": slopes, "measured_range": [ns[0], ns[-1]], "limitations": warnings}


def load_study(path,store=None):
    path = Path(path).resolve()
    try:
        spec = json.loads(path.read_text(),parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
    except (ValueError, OSError) as error:
        raise LabError("invalid_request", "Cannot read study", {"reason": str(error)}) from error
    has_workflow = isinstance(spec, dict) and "workflow" in spec
    if has_workflow:
        from .contracts import validate_workflow
        workflow = spec.pop("workflow")
        validate_workflow(workflow)
    loaded, requests = _load_study(spec, path, store)
    if has_workflow:
        loaded["workflow"] = workflow
    return loaded, requests


def _load_study(spec,path,store=None):
    if isinstance(spec,dict) and spec.get('analysis')=='assessment':
        require(store is not None,'Assessments require the selected Store for archived samples')
        from .assessment import load_assessment
        return load_assessment(spec,path.parent,store)
    if isinstance(spec,dict) and spec.get('analysis')=='cases':
        require(store is not None,'Case studies require the selected Store for archived samples')
        from .cases import load_cases
        return load_cases(spec,path.parent,store)
    if isinstance(spec,dict) and spec.get('analysis')=='paired':
        from .paired import load_paired
        return load_paired(spec,path.parent)
    fields(spec, ("schema_version", "name", "template", "axis", "variants", "budget", "seed", "metric", "constraints", "secondary_metrics", "analysis"),
           ("schema_version", "name", "template", "axis", "variants", "budget", "seed", "metric", "constraints"), "study")
    require(type(spec["schema_version"]) is int and spec["schema_version"] == 1, "Unsupported study schema")
    require(all(isinstance(spec[k], str) and spec[k] for k in ("name", "template", "metric", "constraints")), "study textual fields must be nonempty")
    secondary=spec.get('secondary_metrics',[])
    require(isinstance(secondary,list) and all(isinstance(v,str) and v for v in secondary)
            and len(set(secondary))==len(secondary) and spec['metric'] not in secondary, 'secondary_metrics must be distinct IDs excluding the primary metric')
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
    require(spec.get('analysis','growth') in ('growth','strong_scaling'), 'Unknown study analysis')
    if spec.get('analysis')=='strong_scaling':
        require(all(type(v) is int for v in axis['values']) and 1 in axis['values'], 'Strong scaling requires integer worker counts including 1')
        require(spec['metric'] in ('time.wall','time.iteration.wall'), 'Strong scaling requires elapsed time')
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


def _growth_request(spec, template, variant, scale):
    request = copy.deepcopy(template)
    request["name"] = spec["name"] + "/" + variant["id"] + "/" + str(scale)
    request["workload"]["parameters"][spec["axis"]["parameter"]] = scale
    request["target"]["argv"][0] = variant["executable"]
    request["target"]["argv"][spec["axis"]["argument_index"]] = str(scale)
    request["target"]["build"]["revision"] = variant["revision"]
    request["target"]["build"]["artifacts"] = variant["artifacts"]
    request["correctness"]["argv"] = [s.replace("{candidate}", variant["executable"]) for s in request["correctness"]["argv"]]
    return request


def study(path, store):
    spec, template = load_study(path,store)
    if spec.get('analysis')=='assessment':
        from .assessment import run_assessment
        return run_assessment(spec,template,store)
    if spec.get('analysis')=='cases':
        from .cases import run_cases
        return run_cases(spec,template,store)
    if spec.get('analysis')=='paired':
        from .paired import run_paired
        return run_paired(spec,template,store)
    identifier, directory = store.create("studies")
    result = {"schema_version": 1, "study_id": identifier, "status": "running", "spec": spec,
              "started_at": utc(), "runs": [], "comparisons": [], "growth": {}, "secondary_growth": {}, "error": None}
    from .lifecycle import StudyProgress,notify_record_created
    from .workflowphase import begin, session, finalize_into
    jobs = [(variant, value) for value in spec["axis"]["values"] for variant in spec["variants"]]
    progress=StudyProgress(result,directory,[template],len(jobs))
    write_json(directory / "study.json", result)
    random.Random(spec["seed"]).shuffle(jobs)
    results = {}
    try:
        notify_record_created('study',identifier)
        record = begin(spec, {json.dumps([variant["id"], scale]): _growth_request(spec, template, variant, scale) for variant, scale in jobs}, store, identifier, directory)
        if record is not None:
            result["workflow"] = record
            write_json(directory / "study.json", result)
        with session(record):
            for index, (variant, scale) in enumerate(jobs):
                request = _growth_request(spec, template, variant, scale)
                config = directory / ("request-%03d.json" % index)
                write_json(config, request)
                with progress.delegate('run',dict(variant=variant['id'],scale=scale,index=index)):
                    measured = run(load_request(config), store)
                result["runs"].append({"variant": variant["id"], "scale": scale, "run_id": measured["run_id"], "status": measured["status"]})
                write_json(directory / "study.json", result)
                if measured["status"] != "completed":
                    raise LabError(measured["error"]["category"], "Candidate rejected; no score assigned", {"run_id": measured["run_id"], "error": measured["error"]})
                results[(variant["id"], scale)] = measured
            for metric_name in [spec['metric'],*spec.get('secondary_metrics',[])]:
                destination=result['growth'] if metric_name==spec['metric'] else result['secondary_growth'].setdefault(metric_name,{})
                for variant in spec['variants']:
                    points=[]
                    for scale in spec['axis']['values']:
                        measured=results[(variant['id'],scale)]
                        entries=[[m for m in sample['metrics'] if m['name']==metric_name] for sample in measured['samples']]
                        require(bool(entries) and all(len(row)==1 for row in entries),'Study metric missing or duplicated in a sample: '+metric_name)
                        require(all(evidence_state(row[0])=='Measured' and
                                    (row[0]['method']!='hardware-counter' or 99<=row[0].get('running_percent',0)<=100)
                                    for row in entries),'Study requires measured, full-coverage observations')
                        summaries=[s for s in measured['summary'] if s['name']==metric_name]
                        require(len(summaries)==1,'Study metric missing or has mixed semantics')
                        points.append({'x':scale,'run_id':measured['run_id'],**summaries[0]})
                    if spec.get('analysis')=='strong_scaling':
                        if metric_name==spec['metric']:
                            result.setdefault('scaling',{})[variant['id']]=strong_scaling(results,variant['id'],spec)
                        destination[variant['id']]={'points':points,'limitations':['Worker sweep: no algorithm growth fit; fixed workload required.']}
                    else:
                        destination[variant['id']]=fit_growth(points) if len(points)>=4 else {'points':points,'limitations':['Need at least four scales for fitting']}
            first = spec["variants"][0]["id"]
            for scale in spec["axis"]["values"]:
                for variant in spec["variants"][1:]:
                    c = compare(results[(first, scale)], results[(variant["id"], scale)], spec["metric"])
                    result["comparisons"].append({"scale": scale, "baseline_variant": first, "candidate_variant": variant["id"], **c})
            result["status"] = "completed"
    except LabError as error:
        result["status"] = "cancelled" if error.category=='cancelled' else "failed"
        result["error"] = error.as_dict()
    except KeyboardInterrupt:
        result["status"] = "cancelled"
        result["error"] = LabError("cancelled", "Study interrupted").as_dict()
    except OSError as error:
        result['status']='failed';result['error']=LabError('storage_failed',str(error)).as_dict()
    except Exception as error:
        result['status']='failed';result['error']=LabError('internal_error',str(error)).as_dict();raise
    finally:
        finalize_into(result, store)
        progress.terminal()
        result["ended_at"] = utc()
        write_json(directory / "study.json", result)
    return result


def strong_scaling(results, variant, spec):
    """Reuse comparison gates and intervals; only declared worker argument may vary."""
    baseline=results[(variant,1)]
    index=spec['axis']['argument_index']
    rows=[]
    for workers in sorted(spec['axis']['values']):
        candidate=results[(variant,workers)]
        for r in (baseline,candidate):
            require(r['request']['target']['argv'][index]==str(r['request']['workload']['parameters'][spec['axis']['parameter']]), 'Worker argument does not match declared parameter')
        require(baseline['request']['target']['argv'][:index]+baseline['request']['target']['argv'][index+1:]==candidate['request']['target']['argv'][:index]+candidate['request']['target']['argv'][index+1:], 'Only worker argument may change')
        require(baseline['identity']['artifacts']==candidate['identity']['artifacts'], 'Strong scaling requires identical artifact fingerprints')
        c=compare(baseline,candidate,spec['metric'],allow_command_change=True,varying_parameter=spec['axis']['parameter'])
        rows.append({'workers':workers,'speedup':c['speedup'],'efficiency':c['speedup']/workers,
                     'speedup_interval_95_percent':c['bootstrap_95_percent_speedup'],
                     'efficiency_interval_95_percent':[x/workers for x in c['bootstrap_95_percent_speedup']],
                     'evidence_state':'Estimated','measurement_status':'PASS' if c['sufficient_samples'] and c['stable_samples'] else 'INCONCLUSIVE',
                     'comparison':c})
    return {'mode':'strong_scaling','points':rows,'constraints':spec['constraints'],
            'limitations':['Fixed problem size is a caller declaration; platform checks stored controls, not algorithm semantics.',
                           'Worker count is requested, not an independently observed active-thread count.',
                           'Unpaired descriptive intervals include order/load effects; no automatic optimization acceptance.',
                           'One-worker reference uses the same run, not an independent repeat.']}

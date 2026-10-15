"""Conservative comparisons of semantically identical metrics, with raw samples."""
import random
import statistics
import math

from .metrics import metric_samples, evidence_state, DEFINITIONS, DERIVATIONS
from .protocol import require, fields

from .errors import LabError

MIN_COMPARISON_SAMPLES = 7
MAX_COMPARISON_CV = .10


def validate_policy(policy):
    fields(policy, ('schema_version','min_samples','max_cv','max_regression_fraction','min_speedup','direction'),
           ('schema_version','min_samples','max_cv','max_regression_fraction'), 'acceptance policy')
    require(type(policy['schema_version']) is int and policy['schema_version']==1, 'Unsupported policy version')
    require(type(policy['min_samples']) is int and policy['min_samples']>=2, 'min_samples must be >= 2')
    for key in ('max_cv','max_regression_fraction','min_speedup'):
        if key in policy:
            require(type(policy[key]) in (int,float) and math.isfinite(policy[key]) and
                    (policy[key]>0 if key=='min_speedup' else policy[key]>=0), 'Invalid '+key)
    require(policy.get('direction','lower') in ('lower','higher'), 'Invalid direction')
    return dict(policy)


def acceptance(result, policy, direction):
    if direction == 'none':
        return {'status':'INCONCLUSIVE', 'reason':'Diagnostic metric has no acceptance direction; declare a metric-specific policy.'}
    if not result['sufficient_samples'] or not result['stable_samples']:
        return {'status':'INCONCLUSIVE', 'reason':'Sample count or variability does not meet policy.'}
    lo,hi = result['bootstrap_95_percent_speedup']
    if direction=='higher':
        lo,hi=1/hi,1/lo
    threshold=(1-policy['max_regression_fraction'] if direction=='higher' else 1/(1+policy['max_regression_fraction']))
    if hi < threshold:
        return {'status':'FAIL','reason':'Confirmed material regression','benefit_interval':[lo,hi]}
    if lo < threshold:
        return {'status':'INCONCLUSIVE','reason':'Regression bound is uncertain','benefit_interval':[lo,hi]}
    if 'min_speedup' in policy:
        if hi < policy['min_speedup']:
            return {'status':'FAIL','reason':'Performance target not reached','benefit_interval':[lo,hi]}
        if lo < policy['min_speedup']:
            return {'status':'INCONCLUSIVE','reason':'Performance target interval crosses threshold','benefit_interval':[lo,hi]}
    return {'status':'PASS','reason':'Correctness, comparability, statistical and declared performance gates passed',
            'benefit_interval':[lo,hi]}


def compare(a, b, metric_name="time.wall", allow_command_change=False,
            regression_fraction=0.05, policy=None, varying_parameter=None):
    policy = validate_policy(policy if policy is not None else {"schema_version":1, "min_samples":MIN_COMPARISON_SAMPLES,
                             "max_cv":MAX_COMPARISON_CV, "max_regression_fraction":regression_fraction})
    direction = policy.get("direction", DEFINITIONS.get(metric_name, {}).get("expected_direction", "none"))
    correctness = [r["correctness"]["status"] for r in (a,b)]
    if any(state != "passed" for state in correctness):
        state = "FAIL" if "failed" in correctness else ("UNSUPPORTED" if any(
            (r.get("error") or {}).get("category") in ("unavailable","permission_denied") for r in (a,b)) else "INCONCLUSIVE")
        raise LabError("incomparable", "Correctness must pass before performance scoring",
                       {"acceptance": {"status":state, "reason":"correctness gate"}, "correctness":correctness})
    validation_contracts=[r['request']['correctness'].get('sample_validation') for r in (a,b)]
    if any(validation_contracts) or metric_name.startswith(('solver.','numerical.')):
        if not all(validation_contracts) or any(r['correctness'].get('sample_validation_status')!='passed' or
                len(r.get('solver_samples',[]))!=len(r['samples']) or
                any(s['status']!='PASS' for s in r.get('solver_samples',[])) for r in (a,b)):
            raise LabError('incomparable','Passed per-solve validation required',{'acceptance':{'status':'INCONCLUSIVE','reason':'per-solve validation missing or failed'}})
    differences = []
    def same(label, left, right):
        if left != right:
            differences.append({"condition": label, "baseline": left, "candidate": right})

    same("completed", a["status"], "completed")
    same("candidate_completed", b["status"], "completed")
    if a["status"] != "completed" or b["status"] != "completed":
        raise LabError("incomparable", "Only completed experiments can be compared", {"differences": differences})
    same('workload.id',a['request']['workload']['id'],b['request']['workload']['id'])
    parameters=[{k:v for k,v in r['request']['workload']['parameters'].items() if k!=varying_parameter} for r in (a,b)]
    same('workload.parameters',*parameters)
    if varying_parameter is not None:
        require(all(varying_parameter in r['request']['workload']['parameters'] for r in (a,b)), 'Declared varying parameter missing')
    same('sample_validation_contract',*validation_contracts)
    if all(validation_contracts):
        validator_identities=[{p:digest for p,digest in r['identity']['artifacts'].items() if p in c['argv']}
                              for r,c in zip((a,b),validation_contracts)]
        same('sample_validator_artifacts',*validator_identities)
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
    if metric_name not in DERIVATIONS:
        for r in (a,b):
            if any(sum(m['name']==metric_name for m in sample['metrics'])!=1 for sample in r['samples']):
                differences.append({'condition':'missing_or_duplicate_metric_in_sample', 'run_id':r['run_id'], 'metric':metric_name})
    collections = [metric_samples(r, metric_name) for r in (a, b)]
    states = [evidence_state(m) for values in collections for m in values]
    if any(state != "Measured" for state in states):
        verdict = "UNSUPPORTED" if "Unsupported" in states else "INCONCLUSIVE"
        raise LabError("incomparable", "Acceptance requires measured evidence",
                       {"acceptance":{"status":verdict,"reason":"non-measured evidence"}, "evidence_states":states})
    if any(not values for values in collections):
        differences.append({"condition": "missing_metric", "metric": metric_name})
    else:
        meanings = [{(m["unit"], m["method"], m["scope"], m.get("event", ""),
                       tuple((v["name"],v["unit"],v["method"],v["scope"],v.get("event",""))
                             for v in (m.get("sources",[]) if isinstance(m.get("sources",[]),list) else [])),
                       m.get('source_counter',''), m.get('operation_count'), m.get('counter_contract','')) for m in values} for values in collections]
        same("metric_semantics", sorted(meanings[0]), sorted(meanings[1]))
        if any(len(x) != 1 for x in meanings):
            differences.append({"condition": "mixed_metric_semantics"})
        if any((m["method"]=="hardware-counter" and not 99 <= m.get("running_percent",0) <= 100)
               or m.get("running_percent",100) < 99 for values in collections for m in values):
            differences.append({"condition": "incomplete_counter_running_time"})
    if differences:
        raise LabError("incomparable", "Measurement conditions differ", {"differences": differences})
    av, bv = [[m["value"] for m in values] for values in collections]
    if any(type(x) not in (int,float) or not math.isfinite(x) or x <= 0 for values in (av,bv) for x in values):
        raise LabError("incomparable", "Ratio comparison requires positive finite samples")
    am, bm = statistics.median(av), statistics.median(bv)
    rng = random.Random(1014)
    reference_identity = a["run_id"] == b["run_id"]
    ratios = [1.0] * 5000 if reference_identity else sorted(statistics.median(rng.choices(av, k=len(av))) /
                    statistics.median(rng.choices(bv, k=len(bv))) for _ in range(5000))
    ci = [ratios[125], ratios[4874]]
    cv = [statistics.stdev(v) / statistics.mean(v) if len(v) > 1 else None for v in (av, bv)]
    sufficient = min(len(av), len(bv)) >= policy["min_samples"]
    stable = all(v is not None and v <= policy["max_cv"] for v in cv)
    if not sufficient or not stable:
        classification = "inconclusive"
    elif ci[1] < 1 / (1 + regression_fraction):
        classification = "observed_regression"
    elif ci[0] > 1 + regression_fraction:
        classification = "observed_improvement"
    else:
        classification = "no_clear_change"
    result = {"schema_version": 1, "baseline_id": a["run_id"], "candidate_id": b["run_id"],
            "comparable": True, "metric": metric_name, "unit": collections[0][0]["unit"],
            "baseline_samples": av, "candidate_samples": bv, "baseline_median": am,
            "candidate_median": bm, "speedup": am / bm, "change_fraction": bm / am - 1,
            "bootstrap_95_percent_speedup": ci, "coefficient_of_variation": cv,
            "classification": classification, "sufficient_samples": sufficient, "stable_samples": stable,
            "policy": {"min_samples": policy["min_samples"], "max_cv": policy["max_cv"], "material_fraction": regression_fraction,
                       "bootstrap_resamples": 0 if reference_identity else 5000, "seed": 1014, "paired": False,
                       "reference_identity": reference_identity},
            "treatment": {"baseline_argv": a["request"]["target"]["argv"],
                          "candidate_argv": b["request"]["target"]["argv"],
                          "baseline_build": a["request"]["target"]["build"],
                          "candidate_build": b["request"]["target"]["build"],
                          "command_change_acknowledged": allow_command_change, "varying_parameter":varying_parameter},
            "limitations": ["Unpaired bootstrap is descriptive and cannot remove thermal/load/order drift.",
                            "An observed improvement is not automatic optimization acceptance; repeat interleaved A/B and review oracle coverage."]}

    result['acceptance_policy'] = policy
    result['acceptance'] = acceptance(result,policy,direction)
    result['evidence_state'] = 'Estimated'
    result['input_evidence_state'] = 'Measured'
    result['correctness_status'] = 'passed'
    result['ratio_semantics'] = 'baseline / candidate; not a runtime speedup unless a time metric'
    result['runtime_reduction'] = 1-bm/am if metric_name.startswith('time.') else None
    result['regression_status'] = result['acceptance']['status']
    result['normalized_cost'] = normalized_cost(result)
    return result


def normalized_cost(result):
    """Project an existing comparison; never collect or recompute statistics.

    A cost is an observed relative objective, not an optimization acceptance.
    Legacy records without explicit evidence/correctness gates remain unscored.
    """
    gate = result.get('acceptance', {'status':'INCONCLUSIVE', 'reason':'Acceptance evidence missing'})
    cost = {'schema_version':1, 'metric':result['metric'], 'baseline':1,
            'unit':'ratio', 'lower_is_better':True, 'value':None,
            'interval_95_percent':None, 'evidence_state':'Missing',
            'acceptance_status':gate['status'], 'reason':gate['reason']}
    if not result['comparable']:
        cost['reason'] = 'Comparison rejected; no valid relative cost'
        cost['evidence_state'] = 'Unsupported' if gate['status']=='UNSUPPORTED' else 'Invalid'
        return cost
    if result.get('correctness_status') != 'passed' or result.get('input_evidence_state') != 'Measured':
        cost['reason'] = 'Explicit passed correctness and measured input evidence required; rerun comparison'
        return cost
    direction = result['acceptance_policy'].get('direction', DEFINITIONS.get(result['metric'], {}).get('expected_direction','none'))
    cost['direction'] = direction
    if direction == 'none':
        cost['reason'] = 'No declared optimization direction for this diagnostic metric'
        return cost
    lo, hi = result['bootstrap_95_percent_speedup']
    cost.update(value=1/result['speedup'] if direction=='lower' else result['speedup'],
                interval_95_percent=[1/hi,1/lo] if direction=='lower' else [lo,hi],
                evidence_state='Estimated')
    return cost


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

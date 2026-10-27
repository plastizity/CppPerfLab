"""Conservative comparisons of semantically identical metrics, with raw samples."""
import random
import statistics
import math

from .metrics import metric_samples, evidence_state, DEFINITIONS, ALL_DERIVATIONS
from .protocol import require, fields

from .errors import LabError

MIN_COMPARISON_SAMPLES = 7
MAX_COMPARISON_CV = .10


def combine_gates(states):
    """Conjunction of existing explicit gates; missing evidence can never pass."""
    if 'FAIL' in states:return 'FAIL'
    if not states or 'INCONCLUSIVE' in states:return 'INCONCLUSIVE'
    if 'UNSUPPORTED' in states:return 'UNSUPPORTED'
    require(all(s=='PASS' for s in states),'Unknown acceptance state')
    return 'PASS'



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


def request_differences(a,b,allow_command_change=False,varying_parameter=None):
    """Shared declared-condition checks, available before any target execution."""
    differences=[]
    def same(label,left,right):
        if left!=right:differences.append({'condition':label,'baseline':left,'candidate':right})
    same('workload.id',a['workload']['id'],b['workload']['id'])
    parameters=[{k:v for k,v in r['workload']['parameters'].items() if k!=varying_parameter} for r in (a,b)]
    same('workload.parameters',*parameters)
    if varying_parameter is not None:
        require(all(varying_parameter in r['workload']['parameters'] for r in (a,b)),'Declared varying parameter missing')
    same('sample_validation_contract',a['correctness'].get('sample_validation'),b['correctness'].get('sample_validation'))
    for key in ('claim','execution_state'):
        same(key,a.get(key),b.get(key))
    for key in ('cpu_affinity','options','warmups','limits','phase_report','work_report'):
        same('measurement.'+key,a['measurement'].get(key),b['measurement'].get(key))
    same('measurement.progress',a['measurement'].get('progress',False),b['measurement'].get('progress',False))
    for key in ('environment','cwd'):same('target.'+key,a['target'].get(key),b['target'].get(key))
    same('build.mode',a['target']['build']['mode'],b['target']['build']['mode'])
    if not allow_command_change:same('target.arguments',a['target']['argv'][1:],b['target']['argv'][1:])
    runtime=[r['measurement'].get('runtime_identity') for r in (a,b)]
    same('measurement.runtime_identity_controls',*[dict({k:v for k,v in c.items() if k!='required_objects'},
        required_object_selectors=sorted(r['basename'] for r in c.get('required_objects',[]))) if c is not None else None for c in runtime])
    same('oracle.description',a['correctness']['description'],b['correctness']['description'])
    return differences


def validated_samples(a, b, metric_name="time.wall", allow_command_change=False,
                      varying_parameter=None, metric_scope=None):
    """One authoritative correctness, evidence and comparability boundary."""
    require(metric_scope is None or isinstance(metric_scope,str) and bool(metric_scope),'metric_scope must be a nonempty exact scope')
    correctness = [r["correctness"]["status"] for r in (a,b)]
    if any(state != "passed" for state in correctness):
        state = "FAIL" if "failed" in correctness else ("UNSUPPORTED" if any(
            (r.get("error") or {}).get("category") in ("unavailable","permission_denied") for r in (a,b)) else "INCONCLUSIVE")
        raise LabError("incomparable", "Correctness must pass before performance scoring",
                       {"acceptance": {"status":state, "reason":"correctness gate"}, "correctness":correctness})
    from .adapters import REGISTRY
    for r in (a,b):
        runtime=r['request']['measurement'].get('runtime_identity')
        if runtime is None:continue
        reports=r.get('runtime_identities',[])
        for stage in runtime['stages']:
            expected=1 if stage=='preflight' or REGISTRY[r['tool']['id']].batch else r['request']['measurement']['repetitions']
            selected=[v for v in reports if v['execution_stage']==stage]
            groups={v['measurement_group'] for v in selected}
            expected_groups={None} if stage=='preflight' else set(range(expected))
            if len(selected)!=expected or groups!=expected_groups or (runtime.get('required_objects') and any(v['acceptance']['status']!='PASS' for v in selected)):
                raise LabError('incomparable','Requested runtime identity evidence or expectation gate missing/failed',
                               {'acceptance':{'status':'INCONCLUSIVE','reason':'runtime identity evidence required'}})
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

    def execution_context(run):
        context=run.get('execution_context')
        if context is None:
            return {'kind':'serial'}
        if not isinstance(context,dict):
            raise LabError('incomparable','Run execution context is malformed')
        kind=context.get('kind')
        if kind=='serial':
            return {'kind':'serial'}
        if kind!='concurrent_ab':
            raise LabError('incomparable','Run execution context has an unknown kind')
        conditions=context.get('resource_conditions')
        overlap=context.get('overlap_fraction')
        if (context.get('concurrent_runs')!=2 or not isinstance(conditions,dict) or
                type(conditions.get('minimum_overlap_fraction')) not in (int,float) or
                not math.isfinite(conditions['minimum_overlap_fraction']) or
                not 0 < conditions['minimum_overlap_fraction'] <= 1 or
                context.get('status')!='PASS' or type(overlap) not in (int,float) or
                not math.isfinite(overlap) or not conditions['minimum_overlap_fraction'] <= overlap <= 1):
            raise LabError('incomparable','Concurrent run lacks a passing, sufficient overlap gate',
                           {'acceptance':{'status':'INCONCLUSIVE','reason':'concurrent overlap evidence missing or insufficient'}})
        return {key:context.get(key) for key in ('kind','concurrent_runs','competition_pair_identity','resource_conditions')}
    same('execution_context',execution_context(a),execution_context(b))

    same("completed", a["status"], "completed")
    same("candidate_completed", b["status"], "completed")
    if a["status"] != "completed" or b["status"] != "completed":
        raise LabError("incomparable", "Only completed experiments can be compared", {"differences": differences})
    differences.extend(request_differences(a['request'],b['request'],allow_command_change,varying_parameter))
    if all(validation_contracts):
        validator_identities=[{p:digest for p,digest in r['identity']['artifacts'].items() if p in c['argv']}
                              for r,c in zip((a,b),validation_contracts)]
        same('sample_validator_artifacts',*validator_identities)
    same("inputs", a["identity"]["inputs"], b["identity"]["inputs"])
    for key in ("id", "version", "mode"):
        same("tool." + key, a["tool"][key], b["tool"][key])
    for key in ("os", "kernel", "machine", "hostname", "cpu_model", "cpu_flags", "allowed_cpus", "governor"):
        same("environment." + key, a["environment"][key], b["environment"][key])
    same('environment.microcode',a['environment'].get('microcode'),b['environment'].get('microcode'))
    if metric_name not in ALL_DERIVATIONS:
        for r in (a,b):
            if any(sum(m['name']==metric_name and (metric_scope is None or m['scope']==metric_scope) for m in sample['metrics'])!=1 for sample in r['samples']):
                differences.append({'condition':'missing_or_duplicate_metric_in_sample', 'run_id':r['run_id'], 'metric':metric_name})
    collections = [[m for m in metric_samples(r,metric_name) if metric_scope is None or m['scope']==metric_scope] for r in (a,b)]
    if metric_name.startswith('phase.') and any(m.get('correctness_status')!='passed' for values in collections for m in values):
        raise LabError('incomparable','Phase correctness must pass before local performance scoring',{'acceptance':{'status':'INCONCLUSIVE','reason':'phase correctness not checked or failed'}})
    states = [evidence_state(m) for values in collections for m in values]
    if any(state != "Measured" for state in states):
        verdict = "UNSUPPORTED" if "Unsupported" in states else "INCONCLUSIVE"
        raise LabError("incomparable", "Acceptance requires measured evidence",
                       {"acceptance":{"status":verdict,"reason":"non-measured evidence"}, "evidence_states":states})
    from .perf_events import descriptor_problem
    if any(descriptor_problem(m) for values in collections for m in values):
        raise LabError('incomparable','Event descriptor evidence is missing or incompatible',
                       {'acceptance':{'status':'INCONCLUSIVE','reason':'Cache/TLB event descriptor required'}})
    if any(not values for values in collections):
        differences.append({"condition": "missing_metric", "metric": metric_name})
    else:
        meanings = [{(m["unit"], m["method"], m["scope"], m.get("event", ""),
                       tuple((v["name"],v["unit"],v["method"],v["scope"],v.get("event",""),v.get('event_semantics_key',''))
                             for v in (m.get("sources",[]) if isinstance(m.get("sources",[]),list) else [])),
                       m.get('source_counter',''), m.get('operation_count'), m.get('counter_contract',''),
                       m.get('work_definition_id',''),m.get('work_unit',''),m.get('event_descriptor_id',''),
                       m.get('event_descriptor_version',0),m.get('event_semantics_key','')) for m in values} for values in collections]
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
    return collections


def compare(a, b, metric_name="time.wall", allow_command_change=False,
            regression_fraction=0.05, policy=None, varying_parameter=None, metric_scope=None):
    policy = validate_policy(policy if policy is not None else {"schema_version":1, "min_samples":MIN_COMPARISON_SAMPLES,
                             "max_cv":MAX_COMPARISON_CV, "max_regression_fraction":regression_fraction})
    collections=validated_samples(a,b,metric_name,allow_command_change,varying_parameter,metric_scope)
    av,bv=[[m['value'] for m in values] for values in collections]
    return summarize_comparison(a,b,metric_name,collections[0][0]['unit'],av,bv,policy,
                                allow_command_change,regression_fraction,varying_parameter,metric_scope)


def summarize_comparison(a,b,metric_name,unit,av,bv,policy,allow_command_change=False,
                         regression_fraction=.05,varying_parameter=None,metric_scope=None,paired=False):
    """Shared inference engine; paired inputs are validated block representatives."""
    direction=policy.get('direction',DEFINITIONS.get(metric_name,{}).get('expected_direction','none'))
    am, bm = statistics.median(av), statistics.median(bv)
    rng = random.Random(1014)
    reference_identity = not paired and a["run_id"] == b["run_id"]
    if reference_identity:
        ratios=[1.0]*5000
    elif paired:
        ratios=[]
        for _ in range(5000):
            indices=rng.choices(range(len(av)),k=len(av))
            ratios.append(statistics.median([av[i] for i in indices])/statistics.median([bv[i] for i in indices]))
        ratios.sort()
    else:
        ratios=sorted(statistics.median(rng.choices(av,k=len(av)))/statistics.median(rng.choices(bv,k=len(bv))) for _ in range(5000))
    ci = [ratios[125], ratios[4874]]
    cv = [statistics.stdev(v) / statistics.mean(v) if len(v) > 1 else None for v in (av, bv)]
    sufficient = min(len(av), len(bv)) >= policy["min_samples"]
    paired_ratios=[x/y for x,y in zip(av,bv)] if paired else []
    paired_cv=statistics.stdev(paired_ratios)/statistics.mean(paired_ratios) if paired and len(av)>1 else None
    stable = paired_cv is not None and paired_cv<=policy['max_cv'] if paired else all(v is not None and v <= policy["max_cv"] for v in cv)
    if not sufficient or not stable:
        classification = "inconclusive"
    elif ci[1] < 1 / (1 + regression_fraction):
        classification = "observed_regression"
    elif ci[0] > 1 + regression_fraction:
        classification = "observed_improvement"
    else:
        classification = "no_clear_change"
    result = {"schema_version": 1, "baseline_id": a["run_id"], "candidate_id": b["run_id"],
            "comparable": True, "metric": metric_name, "metric_scope":metric_scope, "unit": unit,
            "baseline_samples": av, "candidate_samples": bv, "baseline_median": am,
            "candidate_median": bm, "speedup": am / bm, "change_fraction": bm / am - 1,
            "bootstrap_95_percent_speedup": ci, "coefficient_of_variation": cv,
            "classification": classification, "sufficient_samples": sufficient, "stable_samples": stable,
            "policy": {"min_samples": policy["min_samples"], "max_cv": policy["max_cv"], "material_fraction": regression_fraction,
                       "bootstrap_resamples": 0 if reference_identity else 5000, "seed": 1014, "paired": paired,
                       "stability_basis":"paired_block_ratios" if paired else "marginal_samples",
                       "reference_identity": reference_identity},
            "treatment": {"baseline_argv": a["request"]["target"]["argv"],
                          "candidate_argv": b["request"]["target"]["argv"],
                          "baseline_build": a["request"]["target"]["build"],
                          "candidate_build": b["request"]["target"]["build"],
                          "runtime_identity_expectations": {v:r["request"]["measurement"].get("runtime_identity",{}).get("required_objects",[]) for v,r in (("baseline",a),("candidate",b))},
                          "command_change_acknowledged": allow_command_change, "varying_parameter":varying_parameter},
            "limitations": ["Paired block bootstrap preserves within-block association; between-block dependence and drift remain possible." if paired else "Unpaired bootstrap is descriptive and cannot remove thermal/load/order drift.",
                            "An observed improvement is not automatic optimization acceptance; repeat interleaved A/B and review oracle coverage."]}

    result['sample_unit']='block' if paired else 'measurement'
    result['paired_ratio_cv']=paired_cv
    result['acceptance_policy'] = policy
    result['acceptance'] = acceptance(result,policy,direction)
    result['evidence_state'] = 'Estimated'
    result['input_evidence_state'] = 'Measured'
    result['correctness_status'] = 'passed'
    workflow=[r.get('workflow_context') for r in (a,b)]
    if all(workflow) and all(workflow[0].get(k)==workflow[1].get(k) for k in ('study_id','phase','freeze_digest')):
        result['workflow_context']={k:workflow[0][k] for k in ('study_id','phase','freeze_digest','freeze_monotonic_ns','clock')}
    result['ratio_semantics'] = 'baseline / candidate; not a runtime speedup unless a time metric'
    context=a.get('execution_context')
    if context is not None and context['kind']=='concurrent_ab':
        result['execution_context']={key:context[key] for key in ('kind','concurrent_runs','competition_pair_identity','resource_conditions')}
        result['execution_context']['scope']='competing pair'
        result['ratio_semantics']='baseline / candidate under concurrent competition; not isolated serial speedup'
    result['runtime_reduction'] = 1-bm/am if metric_name.startswith('time.') else None
    result['regression_status'] = result['acceptance']['status']
    from .validity import evaluate_comparison_validity, apply_validity
    apply_validity(result,evaluate_comparison_validity(a,b,metric_name,metric_scope))
    result['normalized_cost'] = normalized_cost(result)
    result['decisions'] = decisions(result)
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
    if 'claim_acceptance' in result:
        cost['claim_acceptance_status']=result['claim_acceptance']['status']
        cost['meaning']='Descriptive relative objective for this metric and scope; not causal proof or adoption approval'
    validity=result.get('validity')
    if validity is not None and validity['status'] not in ('PASS','NOT_CONFIGURED'):
        cost['reason']='Required claim or execution-state evidence did not pass; inspect validity'
        cost['evidence_state']='Invalid' if validity['status']=='FAIL' else 'Unsupported' if validity['status']=='UNSUPPORTED' else 'Missing'
        cost['acceptance_status']=result.get('claim_acceptance',validity)['status']
        return cost
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


def decisions(result):
    """Separate validity, non-regression and improvement; reuse acceptance gates."""
    valid=result['comparable']
    correctness=result.get('correctness_status')
    answer={'schema_version':1,
            'correctness':{'status':'PASS' if correctness=='passed' else 'FAIL' if correctness=='failed' else 'INCONCLUSIVE',
                           'reason':'Declared oracle coverage only; not proof of all outputs'},
            'comparability':{'status':'PASS' if valid else 'INCONCLUSIVE','reason':'Recorded conditions and metric semantics match' if valid else 'Comparison rejected; see original error'},
            'statistics':{'status':'INCONCLUSIVE','reason':'Valid sampled comparison required'},
            'non_regression':{'status':'INCONCLUSIVE','reason':'Comparable measured evidence and declared direction required'},
            'performance_target':{'status':'INCONCLUSIVE','reason':'No performance target declared'},
            'improvement':{'status':'NOT_ESTABLISHED','reason':'Evidence does not establish improvement'},
            'optimization_accepted':False}
    validity=result.get('validity',{'status':'NOT_CONFIGURED'})
    answer['validity']={'status':validity['status'],'reason':'Only declared and executed checks; inspect condition evidence'}
    answer['claim_support']={'status':'NOT_CONFIGURED' if validity['status']=='NOT_CONFIGURED' else 'NOT_ESTABLISHED',
                             'reason':'A statistical change alone does not establish the declared claim'}
    if not valid:return answer
    if correctness!='passed' or result.get('input_evidence_state')!='Measured':
        return answer
    policy=result['acceptance_policy']
    direction=policy.get('direction',DEFINITIONS.get(result['metric'],{}).get('expected_direction','none'))
    quality=result['sufficient_samples'] and result['stable_samples']
    answer['statistics']={'status':'PASS' if quality else 'INCONCLUSIVE','reason':'Sample count and CV screening; interval remains descriptive'}
    answer['non_regression']=acceptance(result,{k:v for k,v in policy.items() if k!='min_speedup'},direction)
    if 'min_speedup' in policy:answer['performance_target']=result['acceptance']
    if direction=='none':
        answer['improvement']={'status':'NOT_APPLICABLE','reason':'Diagnostic metric has no declared optimization direction'}
    elif quality:
        lo,hi=result['bootstrap_95_percent_speedup']
        if direction=='higher':lo,hi=1/hi,1/lo
        answer['improvement']={'status':'SUPPORTED_IMPROVEMENT' if lo>1 else 'SUPPORTED_REGRESSION' if hi<1 else 'NOT_ESTABLISHED',
                               'benefit_interval_95_percent':[lo,hi],
                               'reason':'Direction-aware descriptive interval compared with 1; no materiality threshold or end-to-end claim'}
    if validity.get('claim_kind')=='mechanism_hypothesis':
        answer['claim_support']={'status':'HYPOTHESIS_ONLY','reason':'Checked conditions and performance data do not prove a causal mechanism'}
    elif (validity['status']=='PASS' and validity.get('claim_kind') in ('task_cost','unit_efficiency')
          and validity.get('claim_applicable') is not False
          and result.get('claim_acceptance',{}).get('status')=='PASS'
          and answer['improvement']['status']=='SUPPORTED_IMPROVEMENT'):
        answer['claim_support']={'status':'SUPPORTED_UNDER_CONTRACT','reason':'Observed improvement and declared checks passed within their recorded scope'}
    return answer


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

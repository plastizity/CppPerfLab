"""Shared evidence-backed views for Agent queries and HTML presentation.

No measurement or statistical estimation lives here. Run summaries, comparisons
and empirical fits are produced by the existing measurement/analysis modules.
"""
from collections import Counter
from .metrics import run_observations, evidence_state, NUMERIC_STATES, ALL_DERIVATIONS
from .lab import summarize
from .comparison import analyze, MIN_COMPARISON_SAMPLES, MAX_COMPARISON_CV
from .errors import LabError
from .protocol import sha256
from .performance_summary import summarize_performance, data_movement_projection


SECTIONS = ("overview", "conditions", "summary", "metrics", "hotspots", "quality", "evidence",
            "facts", "hypotheses", "growth", "comparisons", "observations", "call_edges", "allocation_sites", "phases", "phase_summaries", "runtime_identities", "sampling_profiles", "thread_timelines", "scheduler_timelines", "lifecycle", "candidate", "validity", "workflow", "execution_state_evidence", "data_movement")


def evidence_id(run_id, path):
    return run_id + ":" + path


def recorded_validity(record):
    """Expose recorded gates without upgrading legacy or incomplete records."""
    if 'validity' in record:
        return record['validity']
    error=record.get('error')
    if error is not None and 'validity' in error.get('details',{}):
        return error['details']['validity']
    request=record.get('request', {})
    configured='claim' in request or 'execution_state' in request
    return {'schema_version':1, 'status':'INCONCLUSIVE' if configured else 'NOT_CONFIGURED',
            'assessment_scope':'claim' if 'claim' in request else 'execution_state' if configured else 'none',
            'claim_kind':request['claim']['kind'] if 'claim' in request else None,
            'claim':None, 'conditions':[],
            'limitations':['Validity result was not recorded.' if configured else
                'No independent Study-level validity verdict; inspect every comparison and source Run.' if 'study_id' in record else
                'No validity contract was recorded for this object.']}


def base_view(kind, identifier, status, error):
    return {"schema_version": 1, "view_kind": kind, "record_id": identifier,
            "status": status, "error": error, "optimization_accepted": False,
            "overview": {}, "conditions": {}, "metrics": [], "hotspots": [],
            "quality": [], "evidence": [], "facts": [], "hypotheses": [],
            "growth": [], "comparisons": [], "limitations": [], "summary": {}, "observations": [], "call_edges": [], "allocation_sites": [], "phases": [], "phase_summaries": [], "runtime_identities": [], "sampling_profiles": [], "thread_timelines": [], "scheduler_timelines": [], "source_records": [], "lifecycle":None, "candidate":None,
            "validity":recorded_validity({}), "workflow":None, "execution_state_evidence":[],
            "data_movement":data_movement_projection([])}


def source_record(run):
    """Expose source failures without repeating raw samples or execution logs."""
    error=run['error']
    request=run['request']
    def pick(source,fields):
        return {key:source[key] for key in fields if key in source}
    source=dict(run_id=run['run_id'],status=run['status'],
                correctness_status=run['correctness']['status'],
                error=None if error is None else {k:error[k] for k in ('category','message','exit_code')},
                workload=pick(request['workload'],('id','parameters')),
                tool=pick(run['tool'],('id','version','mode')) if 'tool' in run else None,
                measurement=pick(request['measurement'],('tool','repetitions','warmups','cpu_affinity')),
                build=pick(request['target']['build'],('revision','compiler','flags','mode')))
    if 'execution_context' in run:
        source['execution_context']=pick(run['execution_context'],('kind','scope','concurrent_runs','resource_conditions','status','overlap_fraction','overlap'))
    validity=recorded_validity(run)
    source['validity']={**pick(validity,('schema_version','status','assessment_scope','claim_kind')),
                        'conditions':[row for row in validity['conditions'] if row['status']!='PASS']}
    if 'workflow_context' in run:source['workflow_context']=run['workflow_context']
    return source


def run_view(result):
    rid = result["run_id"]
    view = base_view("run", rid, result["status"], result["error"])
    view['source_records']=[source_record(result)]
    view['validity']=recorded_validity(result)
    view['workflow']=result.get('workflow_context')
    view['execution_state_evidence']=result.get('execution_state_evidence',[])
    request = result["request"]
    view['lifecycle']=None if 'lifecycle' not in result else dict(progress=result['lifecycle'])
    view["overview"] = {"name": request["name"], "workload": request["workload"],
                        "correctness": result["correctness"], "tool": result.get("tool"),
                        "started_at": result["started_at"], "ended_at": result.get("ended_at"),
                        "metric_groups": len(result["summary"]), "samples": len(result["samples"]),
                        "hotspot_rows": len(result["hotspots"])}
    view["conditions"] = {"target": request["target"], "measurement": request["measurement"],
                          "environment": result["environment"], "identity": result.get("identity"),
                          "binary": result.get("binary"), "oracle": request["correctness"]}
    if 'execution_context' in result:view['conditions']['execution_context']=result['execution_context']
    for key in ('claim','execution_state'):
        if key in request:view['conditions'][key]=request[key]
    view["observations"] = run_observations(result)
    for section in ('call_edges','allocation_sites','phases','phase_summaries','runtime_identities','sampling_profiles','thread_timelines','scheduler_timelines'):
        view[section]=[{**row,'evidence_id':evidence_id(rid,row['evidence'])} for row in result.get(section,[])]
    for identity in view['runtime_identities']:
        if identity['evidence_state']!='Measured' or identity['truncations'] or identity['acceptance']['status'] not in ('PASS','NOT_REQUESTED'):
            view['quality'].append(dict(code='runtime_identity_partial',severity='review',message='Runtime identity coverage/expectation requires review; absent data is not an identity pass.',
                evidence_state=identity['evidence_state'],acceptance=identity['acceptance'],evidence_id=identity['evidence_id']))
    for summary in result["summary"]:
        matching = [(s["index"], m) for s in result["samples"] for m in s["metrics"]
                    if all(m[k] == summary[k] for k in ("name", "unit", "method", "scope"))
                    and m.get('event_semantics_key') == summary.get('event_semantics_key')
                    and evidence_state(m)==summary.get("evidence_state", evidence_state(m))]
        view["metrics"].append({"summary": summary,
                                "samples": [{"index": i, "value": m["value"], "evidence_state":evidence_state(m),
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
    derived = [m for m in view['observations'] if m['name'] in ALL_DERIVATIONS]
    for name in ALL_DERIVATIONS:
        rows = [m for m in derived if m['name']==name]
        valid = [m for m in rows if m['evidence_state'] in NUMERIC_STATES]
        summaries = summarize([{'metrics':[m]} for m in valid])
        for summary in summaries:
            view['metrics'].append({'summary':summary, 'samples':[
                {'index':m['sample_index'], 'value':m['value'], 'evidence_state':m['evidence_state'],
                 'evidence_id':evidence_id(rid,m['evidence']), 'sources':m['sources']}
                for m in valid if m['evidence_state']==summary['evidence_state']
                and all(m[k]==summary[k] for k in ('name','unit','method','scope'))]})
        for m in rows:
            if m['evidence_state'] not in NUMERIC_STATES:
                view['quality'].append({'code':'derived_metric_unavailable', 'metric':name,
                    'sample_index':m['sample_index'], 'evidence_state':m['evidence_state'],
                    'severity':'review', 'message':m['reason']})
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
    for solved in result.get('solver_samples',[]):
        view['facts'].append({'kind':'observation','solver_sample':solved})
    view["limitations"] = list(dict.fromkeys(existing["limitations"] + [
        "Hotspot rows belong to individual sampling repetitions; percentages are not summed across runs.",
        "Correctness passed means the declared command exited zero within its stated coverage."]))
    if 'sampling_precheck' in result:view['conditions']['sampling_precheck']=result['sampling_precheck']
    view['summary'] = summarize_performance([result])
    view['data_movement'] = data_movement_projection([result])
    return view


def comparison_view(result):
    from .comparison import normalized_cost, decisions
    result = dict(result, normalized_cost=normalized_cost(result), decisions=decisions(result))
    if not result["comparable"]:
        view = base_view("comparison", result["comparison_id"], "incomparable", result["error"])
        view['validity']=recorded_validity(result)
        view["overview"] = {k: result[k] for k in ("baseline_id", "candidate_id", "metric", "comparable")}
        view["comparisons"] = [result]
        view["conditions"] = result["error"]["details"]
        view["quality"] = [{"code": "incomparable", "severity": "error",
                            "message": "Measurement conditions differ; no ratios or improvement classification were produced."}]
        view["limitations"] = ["Resolve the recorded semantic/condition differences before a performance comparison."]
        view['summary'] = summarize_performance([], [result])
        return view
    view = base_view("comparison", result["comparison_id"], "completed", None)
    view['validity']=recorded_validity(result)
    view["overview"] = {key: result[key] for key in (
        "baseline_id", "candidate_id", "metric", "unit", "comparable", "classification",
        "baseline_median", "candidate_median", "speedup", "change_fraction",
        "bootstrap_95_percent_speedup", "coefficient_of_variation")}
    view["overview"]["acceptance"] = result.get("acceptance")
    if 'claim_acceptance' in result:view['overview']['claim_acceptance']=result['claim_acceptance']
    view["overview"]["decisions"] = result["decisions"]
    if result["policy"].get("paired"):
        view["overview"]["baseline_run_ids"]=result["baseline_run_ids"]
        view["overview"]["candidate_run_ids"]=result["candidate_run_ids"]
        view["overview"]["estimator"]=result["estimator"]
    view["overview"]["normalized_cost"] = result["normalized_cost"]
    view["conditions"] = {"policy": result["policy"], "treatment": result["treatment"],
                          "acceptance_policy":result.get("acceptance_policy")}
    if 'execution_context' in result:view['conditions']['execution_context']=result['execution_context']
    view["comparisons"] = [result]
    view["facts"] = [{"kind": "statistical_inference", "comparison": result}]
    view["quality"] = [{"code": result["classification"],
                        "severity": "review" if result["classification"] == "inconclusive" else "info",
                        "sufficient_samples": result["sufficient_samples"],
                        "stable_samples": result["stable_samples"],
                        "message": "Classification uses the recorded policy; no automatic optimization acceptance."}]
    view["limitations"] = result["limitations"]
    view['summary'] = summarize_performance([], [result])
    return view


def study_view(result):
    view = base_view("study", result["study_id"], result["status"], result["error"])
    view['validity']=recorded_validity(result)
    view['workflow']=result.get('workflow')
    spec = result["spec"]
    if spec.get('analysis')=='assessment':
        view['overview']={'name':spec['name'],'analysis':'assessment','collection_id':spec['collection_id'],
                          'assessment':result['assessment'],'jobs':result['jobs'],'runs':result['runs']}
        view['conditions']={'spec':spec,'execution_budget':result['execution_budget'],'identities':result['identities']}
        view['comparisons']=result['comparisons']
        view['facts']=[{'kind':'observation','profile_job':job} for job in result['jobs']]
        view['limitations']=result['execution_budget']['limitations']+result['assessment'].get('limitations',[])
        view['summary']=summarize_performance([],view['comparisons'])
        return view
    if spec.get('analysis')=='cases':
        view['overview']={'name':spec['name'],'analysis':'cases','collection_id':spec['collection_id'],'cases':result['cases'],'runs':result['runs']}
        view['conditions']={'spec':spec,'execution_budget':result['execution_budget'],'scheduled_jobs':result['scheduled_jobs']}
        view['comparisons']=result['comparisons']
        view['facts']=[{'kind':'observation','case':c} for c in result['cases']]
        view['limitations']=result['execution_budget']['limitations']+['Capture observations are diagnostic provenance; they are not a production baseline for isolated replay.']
        view['summary']=summarize_performance([],view['comparisons'])
        return view
    if spec.get('analysis')=='paired':
        view['overview']={'name':spec['name'],'analysis':'paired','metric':spec['metric'],
                          'runs':result['runs'],'blocks':result['blocks'],'resource_conditions':spec['resource_conditions']}
        view['conditions']={'spec':spec,'execution_budget':result['execution_budget'],'resource_isolation_enforced':False}
        view['comparisons']=result['comparisons']
        view['facts']=[{'kind':'observation','block':b} for b in result['blocks']]
        view['quality']=[{'code':'paired_design','severity':'review','message':
            ('Concurrent competing-pair measurements; waves and blocks are serial. '
             if spec['resource_conditions']['concurrent_runs']==2 else 'Serial paired/block design. ')+
            'Resource isolation is not enforced and block independence is not proved.'}]
        view['limitations']=result['execution_budget']['limitations']
        view['summary']=summarize_performance([],view['comparisons'])
        return view
    view["overview"] = {"name": spec["name"], "axis": spec["axis"], "metric": spec["metric"],
                        "runs": result["runs"], "variants": [v["id"] for v in spec["variants"]]}
    view["conditions"] = {"spec": spec, "started_at": result["started_at"],
                          "ended_at": result.get("ended_at")}
    if "scaling" in result:
        view["overview"]["scaling"] = result["scaling"]
        view["facts"].append({"kind":"statistical_inference","strong_scaling":result["scaling"]})
    meanings = set()
    series=list(result['growth'].items())+[(variant,fitted) for variants in result.get('secondary_growth',{}).values() for variant,fitted in variants.items()]
    by_metric={}
    for variant, fitted in series:
        points = fitted["points"]
        for point in points:
            meaning=tuple(point[k] for k in ("name", "unit", "method", "scope"))
            meanings.add(meaning)
            by_metric.setdefault(point["name"],set()).add(meaning)
        view["growth"].append({"variant": variant, **fitted})
        view["limitations"].extend(fitted["limitations"])
        view["quality"].append({"code": "strong_scaling_sweep" if spec.get("analysis")=="strong_scaling" else "empirical_growth", "severity": "review",
                                "variant": variant,
                                "message": "Fixed-work worker sweep; see scaling evidence." if spec.get("analysis")=="strong_scaling" else "Finite-range empirical fit; not a mathematical Big-O proof."})
        for point in points:
            if point["n"] < MIN_COMPARISON_SAMPLES or point["cv"] is None or point["cv"] > MAX_COMPARISON_CV:
                view["quality"].append({"code": "scaling_point_uncertain" if spec.get("analysis")=="strong_scaling" else "growth_point_uncertain", "severity": "review",
                                        "variant": variant, "scale": point["x"], "run_id": point["run_id"],
                                        "n": point["n"], "cv": point["cv"],
                                        "message": "Worker sweep includes an uncertain measurement point." if spec.get("analysis")=="strong_scaling" else "Model ranking includes an uncertain measurement point."})
    view["conditions"]["growth_metric_semantics"] = [dict(zip(("name", "unit", "method", "scope"), m))
                                                       for m in sorted(meanings)]
    if any(len(values)>1 for values in by_metric.values()):
        view["quality"].append({"code": "mixed_growth_semantics", "severity": "error",
                                "message": "Growth series contain different metric meanings; do not overlay or rank them together."})
    view["comparisons"] = result["comparisons"]
    view["facts"].extend({"kind": "observation", "run": r} for r in result["runs"])
    view["limitations"] = list(dict.fromkeys(view["limitations"] + [
        "A study evaluates reviewed prebuilt variants; it is not an automatic optimizer.",
        "Each scale comparison retains its own classification; the fastest point does not select a production algorithm."]))
    view['summary'] = summarize_performance([], result['comparisons'], view['growth'])
    return view


def build_view(store, identifier, kind="run"):
    record = store.read(identifier, kind)
    if kind == 'summary':
        return summary_view(store, record)
    builders = {"run": run_view, "comparison": comparison_view, "study": study_view}
    view = builders[kind](record)
    if kind in ('run','comparison') and 'workflow_context' in record:
        context=record['workflow_context']
        parent=store.read(context['study_id'],'study')
        workflow=parent.get('workflow')
        if not isinstance(workflow,dict):
            raise LabError('storage_failed','Parent Study lacks the workflow referenced by this record',
                           {'study_id':context['study_id']})
        for key in ('study_id','phase','freeze_digest','freeze_monotonic_ns','clock'):
            if workflow[key]!=context[key]:
                raise LabError('storage_failed','Parent Study workflow does not match this record',
                               {'study_id':context['study_id'],'condition':key})
        view['workflow']={**workflow,'parent_study_status':parent['status'],'source_context':context}
        view['conditions']={**view['conditions'],'workflow_context':context}
    if kind=='run':
        from .lifecycle import status_from_result
        current=status_from_result(store,record)
        view['candidate']=current['candidate']
        view['lifecycle']={k:current[k] for k in ('progress','observed_at','observed_state','owner','active_process','workflow_eta')}
    elif kind=='study':
        from .study_status import study_status
        view['lifecycle']=study_status(store,identifier)
    # Comparison/study evidence remains in the same run store. Failed runs can
    # legitimately have no inventory yet; their error and logs stay at their ID.
    runs = [record] if kind == 'run' else []
    if kind != "run":
        run_ids = (record.get("baseline_run_ids",[record["baseline_id"]])+record.get("candidate_run_ids",[record["candidate_id"]]) if kind == "comparison"
                   else [r["run_id"] for r in record["runs"]])
        for rid in dict.fromkeys(run_ids):
            run = store.read(rid)
            runs.append(run)
            if kind=='study' and record['spec'].get('analysis') in ('cases','assessment'):
                source=run_view(run)
                member=next(m for m in record['runs'] if m['run_id']==rid)
                case_id=member['case_id'] if record['spec']['analysis']=='assessment' else member['sample_id']
                for section in ('metrics','phases','phase_summaries','hotspots','quality','observations','call_edges','allocation_sites','facts','hypotheses','runtime_identities','sampling_profiles','thread_timelines','scheduler_timelines'):
                    view[section].extend({**v,'run_id':rid,'case_id':case_id,'variant':member['variant'],**({'profile_id':member['profile_id']} if 'profile_id' in member else {})} for v in source[section])
            else:
                view['observations'].extend({**row,'run_id':rid} for row in run_observations(run)
                                            if row['evidence_state'] in ('Missing','Invalid','Unsupported'))
                view['phase_summaries'].extend({**row,'run_id':rid,'evidence_id':evidence_id(rid,row['evidence'])} for row in run.get('phase_summaries',[]))
                view['runtime_identities'].extend({**row,'run_id':rid,'evidence_id':evidence_id(rid,row['evidence'])} for row in run.get('runtime_identities',[]))
                view['sampling_profiles'].extend({**row,'run_id':rid,'evidence_id':evidence_id(rid,row['evidence'])} for row in run.get('sampling_profiles',[]))
                view['thread_timelines'].extend({**row,'run_id':rid,'evidence_id':evidence_id(rid,row['evidence'])} for row in run.get('thread_timelines',[]))
                view['scheduler_timelines'].extend({**row,'run_id':rid,'evidence_id':evidence_id(rid,row['evidence'])} for row in run.get('scheduler_timelines',[]))
            view["evidence"].extend({**item, "id": evidence_id(rid, item["path"]), "run_id": rid}
                                    for item in run.get("evidence", []))
        from .run_notes import notes
        view['conditions']['source_candidates']=[notes(store,r['run_id']) for r in runs]
    view['source_records']=[source_record(run) for run in runs]
    view['summary'] = summarize_performance(runs, view['comparisons'], view['growth'])
    view['data_movement'] = data_movement_projection(runs, view['comparisons'], record['runs'] if kind == 'study' else ())
    if kind=='study' and record['spec'].get('analysis')=='assessment':view['summary']['assessment']=record['assessment']
    return view


def summary_view(store, record):
    runs = [store.read(rid) for rid in record['run_ids']]
    comparisons = [store.read(cid, 'comparison') for cid in record['comparison_ids']]
    view = base_view('summary', record['summary_id'], record['status'], record['error'])
    view['source_records']=[source_record(run) for run in runs]
    view['overview'] = {'runs': [{'run_id': r['run_id'], 'name': r['request']['name'],
                                 'status': r['status'], 'tool': r.get('tool')} for r in runs],
                        'comparison_ids': record['comparison_ids']}
    view['conditions'] = {'grouping': 'Explicit record collection; no cross-tool sample aggregation.',
                          'runs': [{'run_id': r['run_id'], **run_view(r)['conditions']} for r in runs]}
    from .run_notes import notes
    view['conditions']['source_candidates']=[notes(store,r['run_id']) for r in runs]
    for run in runs:
        source = run_view(run)
        view['metrics'].extend({**group, 'run_id': run['run_id']} for group in source['metrics'])
        for section in ('hotspots', 'quality', 'evidence', 'facts', 'hypotheses', 'observations', 'call_edges', 'allocation_sites', 'phases', 'phase_summaries', 'runtime_identities', 'sampling_profiles', 'thread_timelines', 'scheduler_timelines'):
            view[section].extend({**item, 'run_id': run['run_id']} for item in source[section])
        view['limitations'].extend(source['limitations'])
    view['comparisons'] = comparisons
    view['summary'] = summarize_performance(runs, comparisons)
    view['data_movement'] = data_movement_projection(runs, comparisons)
    view['limitations'] = list(dict.fromkeys(view['limitations'] + view['summary']['limitations']))
    return view


def create_summary(store, run_ids, comparison_ids):
    if not run_ids and not comparison_ids:
        raise LabError('invalid_request', 'Summary needs at least one --run or --comparison')
    run_ids = list(dict.fromkeys(run_ids))
    comparison_ids = list(dict.fromkeys(comparison_ids))
    comparisons = [store.read(cid, 'comparison') for cid in comparison_ids]
    for comparison in comparisons:
        sources=comparison.get('baseline_run_ids',[comparison['baseline_id']])+comparison.get('candidate_run_ids',[comparison['candidate_id']])
        for rid in sources:
            if rid not in run_ids:run_ids.append(rid)
    runs = [store.read(rid) for rid in run_ids]
    if any(r['status'] == 'running' for r in runs):
        raise LabError('invalid_request', 'Summary requires finished source runs')
    # A diagnostic summary of failed records is allowed, but keeps the error.
    errors = [r['error'] for r in runs if r['error']] + [c['error'] for c in comparisons if c.get('error')]
    identifier, directory = store.create('summaries')
    record = {'schema_version': 1, 'summary_id': identifier, 'run_ids': run_ids,
              'comparison_ids': comparison_ids, 'status': 'completed' if not errors else 'failed',
              'error': errors[0] if errors else None, 'source_errors': errors}
    from .storage import write_json
    write_json(directory/'summary.json', record)
    return summary_view(store, record)


def query_view(view, section="overview", *, metric=None, symbol=None, offset=0, limit=20, input_id=None, result_class=None):
    if section not in SECTIONS or offset < 0 or not 1 <= limit <= 200:
        raise LabError("invalid_request", "Invalid analysis section or pagination (limit 1..200)")
    if metric is not None and section not in ("metrics", "growth", "comparisons", "observations", "call_edges", "allocation_sites"):
        raise LabError("invalid_request", "--metric requires metrics, growth, comparisons or observations section")
    if symbol is not None and section not in ("hotspots","call_edges","allocation_sites"):
        raise LabError("invalid_request", "--symbol requires hotspots, call_edges or allocation_sites section")
    if (input_id is not None or result_class is not None) and section!="phases":
        raise LabError("invalid_request","--input-id/--result require the phases section")
    data = view[section]
    if isinstance(data, list):
        if input_id is not None:data=[r for r in data if r["input_id"]==input_id]
        if result_class is not None:data=[r for r in data if r["result"]==result_class]
        if metric is not None:
            if section == "metrics":
                data = [r for r in data if r["summary"]["name"] == metric]
            elif section == "observations":
                data = [r for r in data if r["name"] == metric]
            elif section == "growth":
                data = [r for r in data if any(p["name"] == metric for p in r["points"])]
            else:
                data = [r for r in data if r["metric"] == metric]
        if symbol is not None:
            fields={'hotspots':lambda r:r['symbol'], 'call_edges':lambda r:r['caller']+' '+r['callee'],
                    'allocation_sites':lambda r:' '.join(r['frames'])}
            data = [r for r in data if symbol.casefold() in fields[section](r).casefold()]
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

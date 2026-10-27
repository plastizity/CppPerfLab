"""Orchestration only; tools own measurement and parsers own normalization."""
import json
from pathlib import Path
import statistics
import sys
import time

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


def configured_metric_ids(request, adapter):
    """Metric candidates from configured inputs; runtime coverage still needs verification."""
    supplied=set(adapter.configured_metrics(request['measurement']['options']))
    if 'sample_validation' in request['correctness'] and not adapter.batch:
        from .solver import METRIC_IDS
        supplied.update(METRIC_IDS)
    if 'phase_report' in request['measurement'] and not adapter.batch:
        from .phases import METRIC_IDS
        supplied.update(METRIC_IDS)
    if 'work_report' in request['measurement'] and adapter.id == 'perf-stat':
        from .work import METRIC_IDS
        supplied.update(METRIC_IDS)
    from .metrics import DERIVATIONS, DME_DERIVATIONS
    supplied.update(name for name,(left,right,_,_) in DERIVATIONS.items() if left in supplied and right in supplied)
    if adapter.id == 'perf-stat':
        groups=[{EVENTS[event][0] for event in group} for group in request['measurement']['options'].get('event_groups', [])]
        for name,(left,right,_,_) in DME_DERIVATIONS.items():
            if left in supplied and right in supplied:
                group_required={left} if right == 'work.items' else {left,right}
                if any(group_required <= group for group in groups):
                    supplied.add(name)
    return supplied


def _missing_claim_metrics(request, adapter):
    claim=request.get('claim')
    if claim is None:return set()
    supplied=configured_metric_ids(request, adapter)
    needed={claim['metric']}|{row['metric'] for row in claim['conditions'] if row['required'] and row['source']=='work_metric'}
    return needed-supplied


def plan(request, store):
    from .budget import execution_budget
    selected = request["measurement"]["tool"]
    if 'work_report' in request['measurement'] and selected not in ('auto', 'perf-stat'):
        raise LabError('invalid_request', 'Work reports require the non-batch perf-stat adapter')
    budget=execution_budget(request)
    if selected != "auto" and selected not in REGISTRY:
        raise LabError("invalid_request", "Unknown adapter " + selected)
    ids = list(REGISTRY) if selected == "auto" else [selected]
    eligible, invalid = [], {}
    budget_candidates={candidate['tool']:candidate for candidate in budget['candidates']}
    for tool in ids:
        if not budget_candidates[tool]['eligible']:
            reason=budget_candidates[tool]['reason']
            invalid[tool]={'tool':tool,'available':False,'reason':'unsupported_measurement_boundary','diagnostic':reason}
            continue
        try:
            REGISTRY[tool].validate(request["measurement"]["options"])
            missing=_missing_claim_metrics(request,REGISTRY[tool])
            if missing:
                invalid[tool]={'tool':tool,'available':False,'reason':'claim_metrics_unavailable',
                               'diagnostic':', '.join(sorted(missing))}
                continue
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
        runtime=request['measurement'].get('runtime_identity')
        if runtime and tool=='google-benchmark' and 'preflight' in runtime['stages']:
            reasons.append('runtime_identity_preflight_not_available_for_integrated_target')
        if runtime and 'measurement' in runtime['stages'] and request.get('goal',{}).get('max_overhead','high')!='high':
            reasons.append('runtime_observer_measurement_overhead_is_unquantified')
        if request['measurement'].get('progress',False) and request.get('goal',{}).get('max_overhead','high')!='high':
            reasons.append('live_progress_metadata_overhead_is_unquantified')
        sample_validation=request['correctness'].get('sample_validation')
        if (sample_validation or 'phase_report' in request['measurement']) and adapter.batch:
            reasons.append('per_sample_validation_requires_one_target_execution_per_sample')
        readiness=probes['tools'][tool].get('sampling_readiness')
        if request['measurement']['options'].get('precheck') and readiness and readiness['status']!='PASS':
            reasons.append('sampling_precheck_prerequisites_unavailable')
        goal = request.get("goal")
        if goal:
            configured_metrics = configured_metric_ids(request, adapter) if tool in eligible else set()
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
    return {"schema_version": 1, "execution_budget":budget, "selected": feasible[0]["tool"] if feasible else None,
            "selection_basis": "required metric coverage, actual probe, overhead and instrumentation constraints; registry order breaks ties",
            "candidates": candidates, "probes": probes,
            "steps": ["fingerprint artifacts", "correctness command", "target preflight", "warmup", "measurement", "verify unchanged inputs", "normalize and archive"],
            "note": "No silent fallback after failure. Integrated Google Benchmark capability probes execute the configured target; external-tool probes use small system programs."}


def summarize(samples):
    from .metrics import evidence_state, NUMERIC_STATES
    groups = {}
    for sample in samples:
        for item in sample["metrics"]:
            state = evidence_state(item)
            if state not in NUMERIC_STATES:
                continue
            key = (item["name"], item["unit"], item["method"], item["scope"], state)
            groups.setdefault(key, []).append(item["value"])
    summaries = []
    for (name, unit, method, scope, state), values in groups.items():
        mean = statistics.mean(values)
        sd = statistics.stdev(values) if len(values) > 1 else None
        median = statistics.median(values)
        summaries.append({"name": name, "unit": unit, "method": method, "scope": scope,
                          "evidence_state": state, "n": len(values), "median": median, "mean": mean,
                          "min": min(values), "max": max(values), "stdev": sd,
                          "cv": sd / mean if sd is not None and mean else None,
                          "mad": statistics.median(abs(v - median) for v in values)})
    return summaries


def phase_evidence(result, folder, directory, index, report, execution_stage="measurement",phase_import=None):
    from .phases import normalize
    try:
        if phase_import is not None:
            import hashlib
            from .phase_import import convert_log
            source=folder/phase_import['source']
            if not source.is_file():raise LabError('parse_failed','Configured phase log source is missing',{'evidence_state':'Missing','source':str(source)})
            mapping=Path(phase_import['mapping']).read_bytes()
            if hashlib.sha256(mapping).hexdigest()!=phase_import['mapping_sha256']:
                raise LabError('invalid_request','Phase mapping changed after request validation')
            archived=folder/'phase-import.mapping.json';archived.write_bytes(mapping)
            imported=convert_log(source,archived,folder/report)
            result.setdefault('phase_imports',[]).append(dict(sample=index,execution_stage=execution_stage,
                source=imported['source'],mapping_evidence=str(archived.relative_to(directory)),
                source_evidence=str(source.relative_to(directory)),evidence=str((folder/report).relative_to(directory))))
        phases=normalize(folder/report)
    except LabError as error:
        from .metrics import observation
        source_name=report if (folder/report).exists() else phase_import['source'] if phase_import is not None and (folder/phase_import['source']).exists() else 'execution.json'
        result.setdefault('unavailable_observations',[]).append(observation('phase.root_wall','Missing' if source_name=='execution.json' else 'Invalid',
            reason=str(error),evidence=str(folder.relative_to(directory)/source_name)))
        raise
    if phases['clock'] is not None:
        result.setdefault('phase_clocks',[]).append(dict(clock=phases['clock'],sample=index,execution_stage=execution_stage,evidence=str(folder.relative_to(directory)/report)))
    result.setdefault('phases',[]).extend({**p,'sample':index,'execution_stage':execution_stage,'evidence':str(folder.relative_to(directory)/p['evidence'])} for p in phases['phases'])
    result.setdefault('phase_summaries',[]).extend({**p,'sample':index,'execution_stage':execution_stage,
        'evidence':str(folder.relative_to(directory)/p['evidence'])} for p in phases.get('phase_summaries',[]))
    timeline=phases['thread_timeline']
    summary=dict(timeline,tracks=[{k:v for k,v in t.items() if k!='intervals'} for t in timeline['tracks']])
    result.setdefault('thread_timelines',[]).append(dict(summary,sample=index,execution_stage=execution_stage,
        evidence=str(folder.relative_to(directory)/report)))
    return phases


def failed_phase_diagnostics(result,folder,directory,index,config,execution_stage):
    if 'phase_report' not in config:return None
    report=folder/config['phase_report']
    if not report.is_file() and not ('phase_import' in config and (folder/config['phase_import']['source']).is_file()):
        from .metrics import observation
        result.setdefault('unavailable_observations',[]).append(observation('phase.root_wall','Missing',
            reason='Target failed before writing a phase report; see original execution error',
            evidence=str(folder.relative_to(directory)/'execution.json')))
        return None
    try:phases=phase_evidence(result,folder,directory,index,config['phase_report'],execution_stage,config.get('phase_import'))
    except LabError as diagnostic:result['phase_report_error']=diagnostic.as_dict()
    else:
        result.setdefault('limitations',[]).extend(phases['limitations'])
        if phases['correctness_failed']:
            result['correctness']['status']='failed'
            return LabError('correctness_failed','Target validation failed; phase diagnostics and input provenance retained',
                            {'execution_stage':execution_stage,'evidence':str(report.relative_to(directory))})
    return None


def archive_sampling_profile(result,profile,folder,directory,stage,group):
    import copy
    prefix=folder.relative_to(directory);entry=copy.deepcopy(profile)
    entry['clock']['verified_attributes']=str(prefix/entry['clock']['verified_attributes'])
    for helper in entry['helpers'].values():helper['evidence']=str(prefix/helper['evidence'])
    for image in entry['images']:
        if 'evidence' in image:image['evidence']=str(prefix/image['evidence'])
    result.setdefault('sampling_profiles',[]).append(dict(entry,execution_stage=stage,measurement_group=group,
        evidence=str(prefix/'sampling-profile.json'),samples_evidence=str(prefix/profile['samples_evidence']),native_evidence=str(prefix/profile['native_evidence'])))


def archive_scheduler_profile(result,profile,folder,directory,stage,group):
    import copy
    entry=copy.deepcopy(profile);prefix=folder.relative_to(directory)
    for key in ('native_evidence','events_evidence','intervals_evidence'):
        entry[key]=str(prefix/entry[key])
    result.setdefault('scheduler_timelines',[]).append(dict(entry,execution_stage=stage,measurement_group=group,
        evidence=str(prefix/'scheduler-profile.json')))


def run(request, store, *, measurement_hook=None):
    workflow_started=time.monotonic_ns()
    identifier, directory = store.create()
    result = {"schema_version": 1, "run_id": identifier, "status": "running", "started_at": utc(),
              "request": request, "environment": discover(), "samples": [], "hotspots": [],
              "summary": [], "correctness": {"status": "not_run"}, "error": None,
              "evidence_directory": str(directory)}
    if measurement_hook is not None:result['execution_context']=measurement_hook.context
    from .workflowphase import current
    workflow_context=current()
    if workflow_context is not None:
        result['workflow_context']={**workflow_context,'run_started_monotonic_ns':workflow_started}
    from .lifecycle import RunProgress,start_workflow_cost,implementation_identity
    result['workflow_cost']=start_workflow_cost(workflow_started,request['measurement'].get('progress',False))
    progress=RunProgress(result,directory) if request['measurement'].get('progress',False) else None
    write_json(directory / "request.json", request)
    write_json(directory / "result.json", result)
    try:
        from .lifecycle import notify_record_created
        notify_record_created('run',identifier)
        if workflow_context is not None:
            from .environment import identity_snapshot
            from .workflowphase import verify_frozen_request
            verify_frozen_request(request,identity_snapshot(request),store)
        phase_import=request['measurement'].get('phase_import')
        if phase_import is not None:
            import hashlib
            mapping=Path(phase_import['mapping']).read_bytes()
            if hashlib.sha256(mapping).hexdigest()!=phase_import['mapping_sha256']:
                raise LabError('invalid_request','Phase mapping changed before tool discovery')
            (directory/'phase-import.mapping.json').write_bytes(mapping)
        for path,expected in request['workload'].get('expected_hashes',{}).items():
            if sha256(path)!=expected:raise LabError('invalid_request','Expected input hash mismatch before any probe',{'path':path})
        if progress:progress.save('capability_probe')
        result["plan"] = plan(request, store)
        write_json(directory / "plan.json", result["plan"])
        selected = result["plan"]["selected"]
        if selected is None:
            from .metrics import observation
            configured = request['measurement']['tool']
            names = request.get('goal', {}).get('metrics', [])
            if not names and configured in REGISTRY:
                names = REGISTRY[configured].configured_metrics(request['measurement']['options'])
            if 'work_report' in request['measurement']:
                names=list(dict.fromkeys([*names, 'work.items']))
            if 'claim' in request:
                names=list(dict.fromkeys([*names,request['claim']['metric'],
                    *(c['metric'] for c in request['claim']['conditions'] if c['required'] and c['source']=='work_metric')]))
            result['unavailable_observations'] = [observation(name,'Unsupported',reason='No feasible tool; see plan diagnostics',
                                                              evidence='plan.json') for name in names]
            raise LabError("unavailable", "No feasible tool", result["plan"])
        adapter = REGISTRY[selected]
        if progress:
            budget=next(c for c in result['plan']['execution_budget']['candidates'] if c['tool']==selected)
            progress.configure(budget)
        result["tool"] = {**adapter.describe(), **result["plan"]["probes"]["tools"][selected]}
        result["identity"] = fingerprints(request, directory)
        if workflow_context is not None:
            verify_frozen_request(request,result['identity'],store)
        from .workflow_eta import cost_key
        result['workflow_cost']['cost_key']=cost_key(result,result['workflow_cost']['implementation_sha256'])
        if measurement_hook is not None:
            result['workflow_cost']['reason']='Concurrent coordination waiting is not modeled by the serial workflow ETA'
        if progress:progress.save('fingerprinting')
        else:write_json(directory/'result.json',result)
        if any(result['identity']['inputs'][path]!=expected for path,expected in request['workload'].get('expected_hashes',{}).items()):
            raise LabError('invalid_request','Expected inputs changed during tool discovery')
        result["binary"] = binary_info(request, directory)
        target, config = request["target"], request["measurement"]

        def launch(command, folder, tool=False, execution_stage='postprocessing', measurement_group=None):
            folder.mkdir(exist_ok=True)
            # Only {run_dir} is a template; literal shell syntax stays literal argv.
            command = [s.replace("{run_dir}", str(folder)) for s in command]
            runtime=config.get('runtime_identity')
            observed=runtime is not None and execution_stage in runtime['stages']
            identity_options=dict(runtime_identity=runtime,identity_target=target['argv'][0],identity_direct=command[0]==target['argv'][0]) if observed else {}
            progress_options={}
            if progress:
                progress.save(execution_stage)
                progress_options['on_start']=lambda pid:progress.starting(execution_stage,measurement_group,command,folder,pid)
            if measurement_hook is not None and execution_stage=='measurement':
                measurement_hook.ready(identifier,measurement_group,folder)
                progress_start=progress_options.get('on_start')
                def paired_start(pid):
                    measurement_hook.started(pid,time.monotonic_ns())
                    if progress_start is not None:progress_start(pid)
                progress_options['on_start']=paired_start
                progress_options['on_exit']=measurement_hook.finished
            record = execute(command, folder, **identity_options, **progress_options,cwd=target.get("cwd", str(folder)),
                             env=target["environment"], timeout=config["timeout_seconds"],
                             affinity=config.get("cpu_affinity"), limits=config.get("limits"))
            write_json(folder / "execution.json", record)
            if progress:progress.finished(execution_stage)
            if observed:
                identity=json.loads((folder/'runtime-identity.json').read_text())
                prefix=folder.relative_to(directory)
                identity.update(execution_stage=execution_stage,measurement_group=measurement_group,evidence=str(prefix/'runtime-identity.json'))
                for snapshot in identity['snapshots']:snapshot['path']=str(prefix/snapshot['path'])
                for error in identity['errors']:
                    if 'snapshot' in error:error['snapshot']=str(prefix/error['snapshot'])
                for obj in identity['objects']+identity['actual_executables']:
                    for key in ('first_snapshot','last_snapshot'):obj[key]=str(prefix/obj[key])
                    if 'thread_observation' in obj:
                        threads=obj['thread_observation']
                        for row in [threads,*threads['observations']]:
                            for key in ('first_snapshot','last_snapshot'):
                                if row.get(key) is not None:row[key]=str(prefix/row[key])
                        for issue in threads['issues']:issue['snapshot']=str(prefix/issue['snapshot'])
                result.setdefault('runtime_identities',[]).append(identity)
            check_execution(record)
            if record["returncode"] and not tool:
                raise LabError("target_failed", "Target exited nonzero", {"returncode": record["returncode"], "evidence": str(folder)})
            if observed and record['returncode']==0 and identity['acceptance']['status'] not in ('PASS','NOT_REQUESTED'):
                raise LabError('incomparable','Runtime object identity expectations did not pass',
                    {'acceptance':identity['acceptance'],'evidence':identity['evidence']})
            return record

        try:
            launch(request["correctness"]["argv"], directory / "correctness",execution_stage='correctness')
        except LabError as error:
            if error.category == "target_failed":
                result["correctness"]["status"] = "failed"
                raise LabError("correctness_failed", "Correctness command failed", error.details) from error
            raise
        result["correctness"] = {"status": "passed", "description": request["correctness"]["description"],
                                 "evidence": "correctness/execution.json",
                                 "meaning": "user-provided oracle exited zero; not a proof of numerical correctness"}
        if selected != "google-benchmark":
            try:
                precheck=config['options'].get('precheck') if selected=='perf-record' else None
                if precheck:
                    from .sampling import evaluate_precheck
                    folder=directory/'preflight';folder.mkdir()
                    target_argv=[s.replace('{run_dir}',str(folder)) for s in target['argv']]
                    command=adapter.prepare(target_argv,folder,config['options'],repetitions=1,warmups=0)
                    record=launch(command,folder,tool=True,execution_stage='preflight')
                    record['analysis_artifact_hashes']=result['identity']['artifacts']
                    parsed=adapter.parse(folder,config['options'],record)
                    clock=None
                    if 'phase_report' in config:
                        phases=phase_evidence(result,folder,directory,None,config['phase_report'],'preflight',config.get('phase_import'));clock=phases['clock']
                        if phases['correctness_failed']:
                            result['correctness']['status']='failed'
                            raise LabError('correctness_failed','Preflight phase correctness failed')
                    profile=parsed['sampling_profile']
                    result['sampling_precheck']=evaluate_precheck(profile,precheck,clock)
                    result['sampling_precheck']['evidence']='preflight/sampling-precheck.json'
                    result['sampling_precheck']['profile_evidence']='preflight/sampling-profile.json'
                    archive_sampling_profile(result,profile,folder,directory,'preflight',None)
                    if 'scheduler_profile' in parsed:
                        archive_scheduler_profile(result,parsed['scheduler_profile'],folder,directory,'preflight',None)
                    write_json(folder/'sampling-precheck.json',result['sampling_precheck'])
                    if result['sampling_precheck']['status']!='PASS':
                        raise LabError('incomparable','Target sampling interpretability precheck did not pass',
                            {'acceptance':dict(status=result['sampling_precheck']['status'],reason='sampling interpretability precheck'),
                             'sampling_precheck':result['sampling_precheck']})
                else:
                    launch(target["argv"], directory / "preflight",execution_stage="preflight")
                    if 'phase_import' in config:
                        phases=phase_evidence(result,directory/'preflight',directory,None,config['phase_report'],'preflight',config['phase_import'])
                        if phases['correctness_failed']:
                            result['correctness']['status']='failed'
                            raise LabError('correctness_failed','Imported preflight phase correctness failed')
            except LabError as error:
                if error.category=='target_failed':
                    diagnostic=failed_phase_diagnostics(result,directory/'preflight',directory,None,config,'preflight')
                    if diagnostic:
                        diagnostic.details['target_error']=error.as_dict()
                        raise diagnostic from error
                raise
        if not adapter.batch:
            for i in range(config["warmups"]):
                launch(target["argv"], directory / ("warmup-%03d" % i),execution_stage='warmup')
        sample_validation=request['correctness'].get('sample_validation')
        if sample_validation:
            result['correctness']['sample_validation_status']='pending'
            result['solver_samples']=[]
        count = 1 if adapter.batch else config["repetitions"]
        for index in range(count):
            state_record=None
            state_directory=directory/('execution-state-%03d'%index)
            try:
                if 'execution_state' in request:
                    from .state import execute_state
                    try:
                        state_record=execute_state(request,state_directory,launch)
                    except LabError as error:
                        state_record=error.details.get('execution_state_evidence')
                        if state_record is not None:
                            state_record['measurement_group']=index
                            result.setdefault('execution_state_evidence',[]).append(state_record)
                        raise
                    state_record['measurement_group']=index
                    result.setdefault('execution_state_evidence',[]).append(state_record)
                group_started=time.monotonic()
                folder = directory / ("measurement-%03d" % index)
                folder.mkdir()
                target_argv = [s.replace("{run_dir}", str(folder)) for s in target["argv"]]
                command = adapter.prepare(target_argv, folder, config["options"],
                                          repetitions=config["repetitions"], warmups=config["warmups"])
                record = launch(command, folder, tool=True,execution_stage="measurement",measurement_group=index)
                record['analysis_artifact_hashes']=result['identity']['artifacts']
                if progress:progress.save('measurement_postprocessing')
                try:
                    parsed = adapter.parse(folder, config["options"], record)
                except LabError as error:
                    if error.category=='target_failed':
                        diagnostic=failed_phase_diagnostics(result,folder,directory,index,config,'measurement')
                        if diagnostic:
                            diagnostic.details['target_error']=error.as_dict()
                            raise diagnostic from error
                    raise
                except (OSError, ValueError, KeyError, TypeError) as error:
                    category = "tool_failed" if record["returncode"] else "parse_failed"
                    raise LabError(category, "Cannot normalize tool output", {"reason": str(error), "evidence": str(folder)}) from error
                write_json(folder / "normalized.json", parsed)
                if sample_validation:
                    from .solver import normalize
                    validation_argv=[arg.replace('{sample_dir}',str(folder)) for arg in sample_validation['argv']]
                    try:
                        launch(validation_argv,folder/'validation',execution_stage='per_sample_validation',measurement_group=index)
                        solved=normalize(folder/sample_validation['report'],folder/'validation/stdout.txt',sample_validation,request['workload']['id'])
                    except LabError as error:
                        result['correctness']['status']='failed'
                        result['correctness']['sample_validation_status']='failed'
                        result['solver_samples'].append({'sample':index,'status':'INCONCLUSIVE','evidence_state':'Invalid','error':error.as_dict(),
                                                        'evidence':str(folder.relative_to(directory)/sample_validation['report'])})
                        if error.category=='target_failed':
                            raise LabError('correctness_failed','Per-solve validator rejected output',error.details) from error
                        raise
                    parsed['metrics'].extend(solved.pop('metrics'))
                    solved['sample']=index
                    solved['evidence']=str(folder.relative_to(directory)/sample_validation['report'])
                    solved['validation_evidence']=str(folder.relative_to(directory)/'validation/stdout.txt')
                    result['solver_samples'].append(solved)
                    parsed.setdefault('limitations',[]).extend(solved['limitations'])
                    write_json(folder / 'normalized.json',parsed)
                if 'phase_report' in config:
                    phases=phase_evidence(result,folder,directory,index,config['phase_report'],phase_import=config.get('phase_import'))
                    parsed['metrics'].extend(phases['metrics'])
                    parsed.setdefault('unavailable_observations',[]).extend(phases['unavailable_observations'])
                    parsed.setdefault('limitations',[]).extend(phases['limitations'])
                    write_json(folder/'normalized.json',parsed)
                if 'work_report' in config:
                    from .work import normalize, LIMITATION
                    parsed['metrics'].append(normalize(folder/config['work_report']['path'], config['work_report']))
                    parsed.setdefault('limitations',[]).append(LIMITATION)
                    write_json(folder/'normalized.json',parsed)
                if 'sampling_profile' in parsed:
                    archive_sampling_profile(result,parsed['sampling_profile'],folder,directory,'measurement',index)
                if 'scheduler_profile' in parsed:
                    archive_scheduler_profile(result,parsed['scheduler_profile'],folder,directory,'measurement',index)
                if "library_version" in parsed:
                    result["tool"]["version"] = parsed["library_version"]
                if "sample_count" in parsed:
                    result.setdefault("sampling_quality", []).append({
                        "measurement": index, "sample_count": parsed["sample_count"],
                        "unknown_symbol_self_percent": parsed["unknown_symbol_self_percent"]})
                for metrics in parsed.get("samples", [parsed["metrics"]]):
                    for item in metrics:
                        item["evidence"] = str(folder.relative_to(directory) / item["evidence"])
                        if 'collection_evidence' in item:
                            item['collection_evidence'] = str(folder.relative_to(directory) / item['collection_evidence'])
                        item["tool"] = selected
                        item["tool_version"] = result["tool"]["version"]
                    result["samples"].append({"index": len(result["samples"]), "metrics": metrics})
                for section in ('call_edges','allocation_sites','unavailable_observations'):
                    result.setdefault(section,[]).extend({**row, 'sample':index,
                        'evidence':str(folder.relative_to(directory)/row['evidence'])} for row in parsed.get(section,[]))
                result["hotspots"].extend({**h, "sample": index,
                                           "evidence": str(folder.relative_to(directory) / h["evidence"])} for h in parsed["hotspots"])
                if 'phase_report' in config and phases['correctness_failed']:
                    result['correctness']['status']='failed'
                    raise LabError('correctness_failed','A reported phase failed correctness',{'sample':index,'evidence':config['phase_report']})
                if sample_validation and solved['status']!='PASS':
                    result['correctness']['status']='failed'
                    result['correctness']['sample_validation_status']='failed'
                    raise LabError('correctness_failed','Per-solve numerical acceptance failed',{'sample':index,'violations':solved['violations']})
                if selected == "massif":
                    # Full allocation trees are already retained; human rendering is optional.
                    from shutil import which
                    if which("ms_print"):
                        rendering = launch([which("ms_print"), str(folder / "massif.out")], folder / "render", tool=True)
                        if rendering["returncode"]:
                            raise LabError("tool_failed", "Massif rendering failed; raw data retained")
                result.setdefault("limitations", []).extend(parsed.get("limitations", []))
                if progress:progress.group_finished(time.monotonic()-group_started)
            finally:
                if 'execution_state' in request:
                    from .state import cleanup_state
                    primary_error=sys.exc_info()[1]
                    try:
                        cleanup=cleanup_state(request,state_directory,launch)
                    except LabError as cleanup_error:
                        cleanup=cleanup_error.details['execution_state_cleanup']
                        result.setdefault('execution_state_cleanup_errors',[]).append(
                            dict(measurement_group=index,error=cleanup_error.as_dict()))
                        if state_record is not None:
                            state_record['cleanup']=cleanup
                            state_record['status']='FAIL'
                            state_record['evidence_refs'].extend(cleanup['evidence_refs'])
                        if primary_error is None:raise
                    else:
                        if cleanup is not None:
                            if state_record is not None:
                                state_record['cleanup']=cleanup
                                state_record['evidence_refs'].extend(cleanup['evidence_refs'])
                            else:
                                result.setdefault('execution_state_cleanup_evidence',[]).append(
                                    dict(measurement_group=index,**cleanup))
        if len(result["samples"]) != config["repetitions"]:
            raise LabError("parse_failed", "Unexpected number of measurement samples")
        if sample_validation:
            result['correctness']['sample_validation_status']='passed'
        if progress:progress.save('verifying_artifacts')
        for category, items in result["identity"].items():
            for path, original in items.items():
                if sha256(path) != original:
                    raise LabError("invalid_request", category + " changed during measurement", {"path": path})
        from .metrics import derive_all_sample
        result["derived_observations"] = [m for sample in result["samples"] for m in derive_all_sample(sample)]
        result["summary"] = summarize(result["samples"])
        result["status"] = "completed"
    except KeyboardInterrupt:
        result["status"] = "cancelled"
        result["error"] = LabError("cancelled", "Run interrupted").as_dict()
    except LabError as error:
        result["status"] = error.category if error.category in ("cancelled", "timeout") else "failed"
        result["error"] = error.as_dict()
        if error.details.get('unavailable_observations'):
            result['unavailable_observations'] = [{**m, 'evidence':str(folder.relative_to(directory)/m['evidence'])}
                                                for m in error.details['unavailable_observations']]
    except OSError as error:
        result["status"] = "failed"
        result["error"] = LabError("storage_failed", str(error)).as_dict()
    except Exception as error:
        import traceback
        (directory / "internal-error.txt").write_text(traceback.format_exc())
        result["status"] = "failed"
        result["error"] = LabError("internal_error", str(error)).as_dict()
    finally:
        from .validity import evaluate_run_validity
        result['validity']=evaluate_run_validity(result)
        result["ended_at"] = utc()
        result["environment_after"] = discover()
        result["evidence"] = inventory(directory)
        if progress:progress.terminal()
        if implementation_identity()!=result['workflow_cost']['implementation_sha256']:
            result['workflow_cost']['evidence_state']='Invalid'
            result['workflow_cost']['cost_key']=None
            result['workflow_cost']['reason']='Platform implementation changed during the workflow'
        if result['workflow_cost']['cost_key'] is not None:
            try:
                tool_matches=sha256(Path(result['tool']['executable_path']))==result['tool']['executable_sha256']
                tool_reason='Selected tool executable changed during the workflow'
            except OSError as error:
                tool_matches=False
                tool_reason='Selected tool executable could not be verified: '+str(error)
            if not tool_matches:
                result['workflow_cost']['evidence_state']='Invalid'
                result['workflow_cost']['cost_key']=None
                result['workflow_cost']['reason']=tool_reason
        result['workflow_cost']['elapsed_seconds']=(time.monotonic_ns()-workflow_started)/1e9
        write_json(directory / "result.json", result)
    return result

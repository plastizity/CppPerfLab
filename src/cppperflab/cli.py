"""Stable JSON envelope and human-readable CLI, using the same core functions."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import traceback
from importlib.resources import files

from . import __version__
from .adapters import REGISTRY
from .metrics import catalog
from .comparison import compare
from .environment import discover
from .errors import LabError, EXIT_CODES
from .lab import plan, run, probe_tools
from .protocol import load_request
from .storage import Store, write_json
from .studies import study
from .views import SECTIONS, build_view, query_view, read_evidence, create_summary
from .html_report import export_html
from .tool_config import tool_configuration, configuration_status


def parser():
    p = argparse.ArgumentParser(prog="perflab", description="CppPerfLab: evidence-based external C++ experiments")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    def common(q):
        q.add_argument("--store", default=os.environ.get("CPPPERFLAB_STORE", ".perflab"))
        q.add_argument("--json", action="store_true", help="versioned JSON envelope")
        q.add_argument("--tool-config", type=Path, default=os.environ.get("CPPPERFLAB_TOOL_CONFIG"),
                       help="version-1 tool paths JSON (or CPPPERFLAB_TOOL_CONFIG)")
    for name in ("doctor", "budget", "plan", "run", "study", "analyze", "report", "compare", "summarize", "evidence", "history", "baseline", "workloads", "tools", "metrics", "schema", "samples", "replay", "profile", "status", "annotate", "timeline", "phases"):
        q = sub.add_parser(name)
        common(q)
        if name=='history':q.add_argument('--kind',choices=('run','study','all'),default='run')
        if name=='phases':
            q.add_argument('operation',choices=('import',))
            q.add_argument('--config',type=Path,required=True)
            q.add_argument('--input',type=Path,required=True)
            q.add_argument('--output',type=Path,required=True)
            q.add_argument('--expected-sha256',help='Optional expected SHA-256 of the raw source log')
        if name=='timeline':
            q.add_argument('run_id')
            q.add_argument('--source',choices=('application','scheduler'),default='application')
            q.add_argument('--stage',choices=('preflight','measurement'),default='measurement')
            q.add_argument('--measurement',type=int)
            q.add_argument('--track')
            q.add_argument('--offset',type=int,default=0)
            q.add_argument('--limit',type=int,default=1000)
            q.add_argument('--format',choices=('json','html'),default='json')
            q.add_argument('--output',type=Path)
        if name=='status':
            q.add_argument('run_id')
            q.add_argument('--kind',choices=('run','study'),default='run')
            q.add_argument('--verify-artifacts',action='store_true',help='Hash current declared artifact files; metadata I/O, no execution')
        if name=='annotate':
            q.add_argument('run_id')
            action=q.add_mutually_exclusive_group(required=True)
            action.add_argument('--candidate-state',choices=('retained','withdrawn'))
            action.add_argument('--superseded-by')
            action.add_argument('--clear-superseded',action='store_true')
            q.add_argument('--reason',required=True)
            q.add_argument('--expect-revision',type=int,required=True)
        if name=='profile':
            q.add_argument('run_id')
            q.add_argument('--stage',choices=('preflight','measurement'),default='measurement')
            q.add_argument('--measurement',type=int)
            q.add_argument('--phase-id')
            q.add_argument('--phase-path',help='JSON array of exact stage path components')
            q.add_argument('--call-id')
            q.add_argument('--input-id')
            q.add_argument('--phase-mode',choices=('inclusive','self'),default='inclusive')
            q.add_argument('--pid',type=int)
            q.add_argument('--tid',type=int)
            q.add_argument('--limit',type=int,default=20)
            q.add_argument('--format',choices=('json','html'),default='json')
            q.add_argument('--output',type=Path)
        if name=='samples':
            q.add_argument('operation',choices=('budget','capture','list','verify'))
            q.add_argument('collection_id',nargs='?')
            q.add_argument('--config',type=Path)
            q.add_argument('--sample')
            q.add_argument('--result')
            q.add_argument('--correctness',choices=('passed','failed','not_checked'))
            q.add_argument('--sort',choices=('id','elapsed_desc'),default='id')
            q.add_argument('--offset',type=int,default=0)
            q.add_argument('--limit',type=int,default=20)
        if name=='replay':
            q.add_argument('collection_id')
            q.add_argument('--sample',required=True)
            q.add_argument('--config',type=Path,required=True)
            q.add_argument('--mode',choices=('budget','prepare','run'),default='run')
            q.add_argument('--output',type=Path)
        if name in ("budget", "plan", "run", "study"):
            q.add_argument("--config", required=True, type=Path, help="version-1 JSON request")
        if name=="budget":
            q.add_argument("--kind",choices=("request","study"),default="request")
        if name in ("analyze", "report", "evidence"):
            q.add_argument("run_id")
        if name in ("analyze", "report"):
            q.add_argument("--kind", choices=("run", "study", "comparison", "summary"), default="run")
        if name == 'summarize':
            q.add_argument('--run', action='append', default=[], help='include a saved run (repeatable)')
            q.add_argument('--comparison', action='append', default=[], help='include a saved comparison (repeatable)')
            q.add_argument('--format', choices=('json', 'html'), default='json')
            q.add_argument('--output', type=Path, help='required HTML export destination')
        if name == "analyze":
            q.add_argument("--section", choices=SECTIONS + ("all",), default="overview")
            q.add_argument("--metric", help="exact metric ID filter")
            q.add_argument("--symbol", help="case-insensitive symbol substring filter")
            q.add_argument("--input-id", help="exact phase input ID")
            q.add_argument("--result", dest="result_class", help="exact phase business result classification")
            q.add_argument("--offset", type=int, default=0)
            q.add_argument("--limit", type=int, default=20)
        if name == "report":
            q.add_argument("--format", choices=("json", "text", "html", "agent", "both"), default="json")
            q.add_argument("--output", type=Path, help="HTML/agent file, or directory for both reports")
            q.add_argument("--limit", type=int, help="regular agent report detail limit (1..200, default 5)")
        if name == "compare":
            q.add_argument("baseline_id")
            q.add_argument("candidate_id")
            q.add_argument("--metric", default="time.wall")
            q.add_argument("--scope", help="exact metric measurement scope, e.g. one phase path")
            q.add_argument("--allow-command-change", action="store_true")
            q.add_argument("--policy", type=Path, help="version-1 acceptance policy JSON")
            q.add_argument("--format", choices=("json", "html"), default="json")
            q.add_argument("--output", type=Path, help="required HTML export destination")
        if name == "evidence":
            q.add_argument("--path", required=True, help="exact relative path from evidence inventory")
            q.add_argument("--offset", type=int, default=0, help="zero-based line offset")
            q.add_argument("--limit", type=int, default=80, help="line count, 1..500")
        if name == "metrics":
            q.add_argument("--domain")
            q.add_argument("--metric")
        if name == "tools":
            q.add_argument("operation", choices=("list", "probe"))
            q.add_argument("--tool", choices=tuple(REGISTRY))
            q.add_argument("--events", help="perf-stat event list for capability probe")
        if name == "workloads":
            q.add_argument("operation", choices=("list",))
            q.add_argument("--directory", type=Path, default=Path("workloads"))
        if name == "baseline":
            q.add_argument("name")
            q.add_argument("run_id")
        if name == "schema":
            q.add_argument("kind", choices=("request", "result", "envelope", "study", "analysis", "query", "tools", "summary", "metrics", "evidence", "policy", "solver", "solver-validation", "phases", "sample-manifest", "collection", "runtime-identity", "sampling", "run-status", "study-status", "activity-timeline", "phase-mapping", "phase-summary", "scheduler-timeline", "agent-report"))
    return p


def dispatch(args):
    store = Store(args.store)
    if args.command=='phases':
        from .phase_import import convert_log
        output=args.output.resolve()
        if output in (args.input.resolve(),args.config.resolve()):
            raise LabError('invalid_request','Phase import output must preserve the source log and mapping')
        if output.is_relative_to(store.root):raise LabError('invalid_request','Export converted logs outside the authoritative Store; run owns automatic imports')
        output.parent.mkdir(parents=True,exist_ok=True)
        converted=convert_log(args.input,args.config,output,args.expected_sha256)
        from .phases import normalize
        normalized=normalize(output)
        return dict(schema_version=1,execution_performed=False,**converted,metrics=normalized['metrics'],
            unavailable_observations=normalized['unavailable_observations'])
    if args.command=='status':
        if args.kind=='study':
            from .study_status import study_status
            return study_status(store,args.run_id,args.verify_artifacts)
        from .lifecycle import run_status
        return run_status(store,args.run_id,args.verify_artifacts)
    if args.command=='annotate':
        from .run_notes import annotate
        action='candidate_state' if args.candidate_state is not None else 'superseded_by'
        value=args.candidate_state if action=='candidate_state' else args.superseded_by
        return annotate(store,args.run_id,action=action,value=value,reason=args.reason,expected_revision=args.expect_revision)
    if args.command == "report":
        if args.format in ("html", "both") and args.output is None:
            raise LabError("invalid_request", "HTML or both report export requires --output")
        if args.format in ("json", "text") and args.output is not None:
            raise LabError("invalid_request", "--output is supported for HTML, agent or both report export")
        if args.limit is not None and args.format not in ("agent", "both"):
            raise LabError("invalid_request", "--limit applies only to agent or both report format")
        if args.limit is not None and not 1 <= args.limit <= 200:
            raise LabError("invalid_request", "Agent report limit must be 1..200")
    elif args.command in ("compare", "summarize", "profile", "timeline"):
        if args.format == "html" and args.output is None:
            raise LabError("invalid_request", "HTML export requires --output")
        if args.format != "html" and args.output is not None:
            raise LabError("invalid_request", "--output is supported for HTML export only")
    if args.command=='timeline':
        if args.source=='scheduler':
            from .scheduler_queries import query_scheduler
            from .timeline_queries import scheduler_timeline_view
            selected=query_scheduler(store,args.run_id,args.stage,args.measurement,args.track,args.offset,args.limit)
            return export_html(scheduler_timeline_view(store,selected),store,args.output) if args.format=='html' else selected
        if args.offset or args.limit!=1000:
            raise LabError('invalid_request','--offset and --limit apply only to scheduler timelines')
        from .timeline_queries import query_timeline,timeline_view
        selected=query_timeline(store,args.run_id,args.stage,args.measurement,args.track)
        return export_html(timeline_view(store,selected),store,args.output) if args.format=='html' else selected
    if args.command=='profile':
        from .profile_queries import query_profile,profile_view
        phase_path=None
        if args.phase_path is not None:
            try:phase_path=json.loads(args.phase_path)
            except ValueError as error:raise LabError('invalid_request','phase-path must be a JSON string array') from error
        result=query_profile(store,args.run_id,args.stage,args.measurement,phase_id=args.phase_id,phase_path=phase_path,
            call_id=args.call_id,input_id=args.input_id,phase_mode=args.phase_mode,pid=args.pid,tid=args.tid,limit=args.limit)
        return export_html(profile_view(store,result),store,args.output) if args.format=='html' else result
    if args.command=='samples':
        from .samples import capture_budget,capture,query_samples,verify_sample
        if args.operation in ('budget','capture'):
            if not args.config or args.collection_id or args.sample or args.result or args.correctness or args.sort!='id' or args.offset or args.limit!=20:
                raise LabError('invalid_request','Capture/budget requires --config and no collection/query filters')
            return (capture_budget if args.operation=='budget' else capture)(args.config,store)
        if not args.collection_id or args.config:
            raise LabError('invalid_request','Sample query/verify requires collection ID and no --config')
        if args.operation=='verify':
            if not args.sample or args.result or args.correctness or args.sort!='id' or args.offset or args.limit!=20:
                raise LabError('invalid_request','Verify requires --sample and no query filters')
            _,sample=verify_sample(store,args.collection_id,args.sample)
            return {'schema_version':1,'collection_id':args.collection_id,'sample_id':sample['id'],'status':'verified',
                    'input_completeness':'exporter_asserted','files':[f for role in ('input','expected') if role in sample for f in sample[role]['files']]}
        if args.sample:raise LabError('invalid_request','--sample belongs to verify; list uses result/correctness filters')
        return query_samples(store,args.collection_id,args.result,args.correctness,args.sort,args.offset,args.limit)
    if args.command=='replay':
        from .replay import bind_sample
        from .budget import execution_budget
        request=bind_sample(store,args.collection_id,args.sample,load_request(args.config))
        if args.mode=='prepare':
            if not args.output:raise LabError('invalid_request','Prepare requires --output for the reusable bound request')
            output=args.output.resolve()
            if output.is_relative_to(store.root):raise LabError('invalid_request','Export prepared request outside the authoritative experiment store')
            write_json(output,request)
            return {'schema_version':1,'execution_performed':False,'path':str(output),'collection_id':args.collection_id,'sample_id':args.sample}
        if args.output:raise LabError('invalid_request','--output belongs to prepare mode')
        if args.mode=='budget':return execution_budget(request)
        return run(request,store)
    if args.command == "metrics":
        return catalog(REGISTRY, args.domain, args.metric)
    if args.command == "doctor":
        return {"environment": discover(), "capabilities": probe_tools(store),
                "tool_configuration": configuration_status(),
                "platform_support": "Linux/WSL execution; native Windows not implemented"}
    if args.command == "tools":
        if args.operation == "list":
            return {"tools": [a.describe() for a in REGISTRY.values()],
                    "note": "Descriptors are not proof of availability; run tools probe"}
        if args.events and args.tool != "perf-stat":
            raise LabError("invalid_request", "--events requires --tool perf-stat")
        options = {"perf-stat": {"events": args.events.split(",")}} if args.events else None
        return probe_tools(store, [args.tool] if args.tool else None, options)
    if args.command == "budget":
        from .budget import execution_budget
        if args.kind=='study':
            from .studies import load_study
            from .paired import paired_budget
            spec,requests=load_study(args.config,store)
            if spec.get('analysis')=='assessment':
                from .assessment import assessment_budget
                return assessment_budget(spec,requests)
            if spec.get('analysis')=='cases':
                from .cases import case_budget
                return case_budget(spec,requests)
            if spec.get('analysis')!='paired':
                raise LabError('invalid_request','Study budget currently supports paired, file-case and assessment designs; ordinary request budget remains available')
            return paired_budget(spec,requests)
        return execution_budget(load_request(args.config))
    if args.command == "plan":
        return plan(load_request(args.config), store)
    if args.command == "run":
        return run(load_request(args.config), store)
    if args.command == "study":
        return study(args.config, store)
    if args.command == "analyze":
        view = build_view(store, args.run_id, args.kind)
        if args.section == "all":
            if args.metric or args.symbol or args.input_id or args.result_class or args.offset or args.limit != 20:
                raise LabError("invalid_request", "Full view does not accept filters/pagination; choose a section")
            return view
        return query_view(view, args.section, metric=args.metric, symbol=args.symbol,
                          offset=args.offset, limit=args.limit, input_id=args.input_id, result_class=args.result_class)
    if args.command == "evidence":
        return read_evidence(store, args.run_id, args.path, offset=args.offset, limit=args.limit)
    if args.command == "report":
        if args.format == "html":
            return export_html(build_view(store, args.run_id, args.kind), store, args.output)
        if args.format in ("agent", "both"):
            from .agent_report import build_agent_report
            view = build_view(store, args.run_id, args.kind)
            limit = args.limit if args.limit is not None else 5
            if args.format == "agent" and args.output is None:
                return build_agent_report(view, store.root, limit=limit)
            from .reporting import export_agent, export_both
            if args.format == "agent":
                return export_agent(view, store, args.output, limit=limit)
            return export_both(view, store, args.output, limit=limit)
        return store.read(args.run_id, args.kind)
    if args.command == 'summarize':
        view = create_summary(store, args.run, args.comparison)
        if args.format == 'html':
            return export_html(view, store, args.output)
        return view
    if args.command == "compare":
        baseline, candidate = store.read(args.baseline_id), store.read(args.candidate_id)
        try:
            policy = None
            if args.policy:
                try:
                    policy = json.loads(args.policy.read_text())
                except (OSError, ValueError) as exc:
                    raise LabError("invalid_request", "Cannot read acceptance policy", {"reason":str(exc)}) from exc
            result = compare(baseline, candidate, args.metric, args.allow_command_change, policy=policy, metric_scope=args.scope)
        except LabError as error:
            if error.category != "incomparable":
                raise
            # Archive the rejected comparison, preserving failure/exit semantics.
            result = {"schema_version": 1, "baseline_id": args.baseline_id,
                      "candidate_id": args.candidate_id, "metric": args.metric, "metric_scope":args.scope,
                      "comparable": False, "status": "incomparable", "error": error.as_dict(),
                      "acceptance_policy":policy,
                      "acceptance":error.details.get("acceptance", {"status":"INCONCLUSIVE", "reason":"incomparable conditions"})}
        from .comparison import normalized_cost, decisions
        result["normalized_cost"] = normalized_cost(result)
        if not result["comparable"]:
            states=[r["correctness"]["status"] for r in (baseline,candidate)]
            result["correctness_status"]="passed" if all(s=="passed" for s in states) else "failed" if "failed" in states else "not_verified"
        result["decisions"] = decisions(result)
        identifier, directory = store.create("comparisons")
        result["comparison_id"] = identifier
        write_json(directory / "comparison.json", result)
        if args.format == "html":
            return export_html(build_view(store, identifier, "comparison"), store, args.output)
        return result
    if args.command == "history":
        from .lifecycle import status_from_result
        rows=[]
        for r in store.list() if args.kind in ('run','all') else []:
            current=status_from_result(store,r)
            rows.append(dict(run_id=r['run_id'],name=r['request']['name'],status=r['status'],started_at=r['started_at'],
                observed_state=current['observed_state'],owner=current['owner'],progress=current['progress'],
                workflow_eta=current['workflow_eta'],candidate=current['candidate'],recorded_identity=r.get('identity')))
        result={'runs':rows}
        if args.kind in ('study','all'):
            from .study_status import study_status
            result['studies']=[]
            for record in store.list('study'):
                current=study_status(store,record['study_id'])
                result['studies'].append({k:current[k] for k in ('study_id','name','study_status','source_error','started_at','observed_state','owner','progress','active_path')})
        return result
    if args.command == "baseline":
        return store.baseline(args.name, args.run_id)
    if args.command == "workloads":
        return {"workloads": [{"path": str(p), "name": json.loads(p.read_text()).get("name")}
                              for p in sorted(args.directory.glob("*.json"))]}
    return json.loads(files("cppperflab").joinpath("schemas", args.kind + ".schema.json").read_text())


def main():
    args = parser().parse_args()
    # SIGTERM is a cancellation request, with the same cleanup/archive as Ctrl-C.
    def cancel(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, cancel)
    exit_code = 0
    try:
        with tool_configuration(args.tool_config, REGISTRY):
            result = dispatch(args)
        error = result.get("error")
        exit_code = error["exit_code"] if error else 0
        envelope = {"schema_version": 1, "ok": error is None, "result": result, "error": error}
    except LabError as error:
        exit_code = EXIT_CODES[error.category]
        envelope = {"schema_version": 1, "ok": False, "result": None, "error": error.as_dict()}
    except KeyboardInterrupt:
        error = LabError("cancelled", "Operation cancelled")
        exit_code = 130
        envelope = {"schema_version": 1, "ok": False, "result": None, "error": error.as_dict()}
    except OSError as error:
        failure = LabError("storage_failed", str(error))
        exit_code = 8
        envelope = {"schema_version": 1, "ok": False, "result": None, "error": failure.as_dict()}
    except Exception as error:
        # Boundary conversion preserves traceback; internal defects never become success.
        traceback.print_exc(file=sys.stderr)
        failure = LabError("internal_error", str(error))
        exit_code = 70
        envelope = {"schema_version": 1, "ok": False, "result": None, "error": failure.as_dict()}
    if args.json or (args.command == "report" and args.format in ("json", "agent")):
        print(json.dumps(envelope, indent=2, sort_keys=True, allow_nan=False))
    else:
        print("CppPerfLab " + ("OK" if envelope["ok"] else "FAILED"))
        print(json.dumps(envelope["result"] if envelope["ok"] else envelope["error"], indent=2, sort_keys=True))
    return exit_code

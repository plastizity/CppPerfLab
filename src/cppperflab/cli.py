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
    for name in ("doctor", "plan", "run", "study", "analyze", "report", "compare", "summarize", "evidence", "history", "baseline", "workloads", "tools", "metrics", "schema"):
        q = sub.add_parser(name)
        common(q)
        if name in ("plan", "run", "study"):
            q.add_argument("--config", required=True, type=Path, help="version-1 JSON request")
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
            q.add_argument("--offset", type=int, default=0)
            q.add_argument("--limit", type=int, default=20)
        if name == "report":
            q.add_argument("--format", choices=("json", "text", "html"), default="json")
            q.add_argument("--output", type=Path, help="required HTML export destination")
        if name == "compare":
            q.add_argument("baseline_id")
            q.add_argument("candidate_id")
            q.add_argument("--metric", default="time.wall")
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
            q.add_argument("kind", choices=("request", "result", "envelope", "study", "analysis", "query", "tools", "summary", "metrics", "evidence", "policy"))
    return p


def dispatch(args):
    store = Store(args.store)
    if args.command in ("report", "compare", "summarize"):
        if args.format == "html" and args.output is None:
            raise LabError("invalid_request", "HTML export requires --output")
        if args.format != "html" and args.output is not None:
            raise LabError("invalid_request", "--output is supported for HTML export only")
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
    if args.command == "plan":
        return plan(load_request(args.config), store)
    if args.command == "run":
        return run(load_request(args.config), store)
    if args.command == "study":
        return study(args.config, store)
    if args.command == "analyze":
        view = build_view(store, args.run_id, args.kind)
        if args.section == "all":
            if args.metric or args.symbol or args.offset or args.limit != 20:
                raise LabError("invalid_request", "Full view does not accept filters/pagination; choose a section")
            return view
        return query_view(view, args.section, metric=args.metric, symbol=args.symbol,
                          offset=args.offset, limit=args.limit)
    if args.command == "evidence":
        return read_evidence(store, args.run_id, args.path, offset=args.offset, limit=args.limit)
    if args.command == "report":
        if args.format == "html":
            return export_html(build_view(store, args.run_id, args.kind), store, args.output)
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
            result = compare(baseline, candidate, args.metric, args.allow_command_change, policy=policy)
        except LabError as error:
            if error.category != "incomparable":
                raise
            # Archive the rejected comparison, preserving failure/exit semantics.
            result = {"schema_version": 1, "baseline_id": args.baseline_id,
                      "candidate_id": args.candidate_id, "metric": args.metric,
                      "comparable": False, "status": "incomparable", "error": error.as_dict(),
                      "acceptance_policy":policy,
                      "acceptance":error.details.get("acceptance", {"status":"INCONCLUSIVE", "reason":"incomparable conditions"})}
        identifier, directory = store.create("comparisons")
        result["comparison_id"] = identifier
        write_json(directory / "comparison.json", result)
        if args.format == "html":
            return export_html(build_view(store, identifier, "comparison"), store, args.output)
        return result
    if args.command == "history":
        return {"runs": [{"run_id": r["run_id"], "name": r["request"]["name"],
                          "status": r["status"], "started_at": r["started_at"]} for r in store.list()]}
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
    if args.json or (args.command == "report" and args.format == "json"):
        print(json.dumps(envelope, indent=2, sort_keys=True, allow_nan=False))
    else:
        print("CppPerfLab " + ("OK" if envelope["ok"] else "FAILED"))
        print(json.dumps(envelope["result"] if envelope["ok"] else envelope["error"], indent=2, sort_keys=True))
    return exit_code

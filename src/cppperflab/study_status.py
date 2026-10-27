"""Read-only status and progress view for composed studies."""
from .errors import LabError
from .lifecycle import observe_process, status_from_result
from .storage import utc


_TERMINAL = {"completed", "failed", "cancelled", "timeout"}


def study_status(store, identifier, verify_artifacts=False):
    studies = []
    runs = []
    run_views = {}
    study_results = {}
    study_edges = {}
    visiting = set()
    visited_studies = set()
    visited_runs = set()

    def malformed(message):
        raise LabError("storage_failed", message)

    def load_study(study_id):
        if study_id not in study_results:
            result = store.read(study_id, "study")
            if result.get("record_kind", "study") != "study" or result.get("study_id") != study_id:
                malformed("Study record identity does not match its Store ID")
            _validate_status(result, "Study", malformed)
            lifecycle = result.get("lifecycle")
            if lifecycle is not None:
                required = {"schema_version", "owner", "updated_at", "planned_runs", "children"}
                if type(lifecycle) is not dict or not required.issubset(lifecycle) or type(lifecycle.get("schema_version")) is not int or lifecycle["schema_version"] != 1:
                    malformed("Unsupported or malformed Study lifecycle")
                children = lifecycle.get("children")
                if type(children) is not list:
                    malformed("Study lifecycle children must be a list")
                if type(lifecycle["planned_runs"]) is not int or lifecycle["planned_runs"] < 0:
                    malformed("Study lifecycle planned_runs must be a nonnegative integer")
                if type(lifecycle["updated_at"]) is not str:
                    malformed("Study lifecycle updated_at must be a string")
                for edge in children:
                    if (type(edge) is not dict or edge.get("record_kind") not in ("run", "study")
                            or type(edge.get("record_id")) is not str or not edge["record_id"]
                            or type(edge.get("context")) is not dict or type(edge.get("created_at")) is not str):
                        malformed("Malformed Study lifecycle child")
                if "active_children" in lifecycle:
                    active_children = lifecycle["active_children"]
                    if (type(active_children) is not list or any(type(i) is not int or i < 0 or i >= len(children) for i in active_children)
                            or len(set(active_children)) != len(active_children)):
                        malformed("Study lifecycle active_children must contain distinct child indexes")
                    active = lifecycle.get("active_child")
                    expected = active_children[0] if len(active_children) == 1 else None
                    if active != expected:
                        malformed("Study lifecycle active_child does not match active_children")
                else:
                    if "active_child" not in lifecycle:
                        malformed("Legacy Study lifecycle active_child is required")
                    active = lifecycle.get("active_child")
                    if active is not None and (type(active) is not int or active < 0 or active >= len(children)):
                        malformed("Study lifecycle active_child is outside children")
                    active_children = [] if active is None else [active]
                owner = lifecycle.get("owner")
                if (type(owner) is not dict or type(owner.get("pid")) is not int or
                        owner.get("evidence_state") not in ("Measured", "Missing", "Unsupported") or
                        (owner.get("evidence_state") != "Measured" and "reason" not in owner) or
                        (owner.get("evidence_state") == "Measured" and
                         not {"start_ticks", "boot_id", "pid_namespace"}.issubset(owner))):
                    malformed("Malformed Study lifecycle owner")
            else:
                children = _legacy_children(result)
                lifecycle = None
            study_results[study_id] = result
            study_edges[study_id] = children
        return study_results[study_id]

    def walk(study_id, context_path=(), parent_id=None):
        if study_id in visiting:
            malformed("Cycle detected in Study child records")
        result = load_study(study_id)
        if study_id in visited_studies:
            return
        visiting.add(study_id)
        visited_studies.add(study_id)
        lifecycle = result.get("lifecycle")
        owner = _study_owner(lifecycle)
        status = result["status"]
        observed = _observed_state(status, owner)
        studies.append(dict(study_id=study_id, parent_study_id=parent_id,
            context_path=list(context_path), study_status=status, source_error=result.get("error"),
            observed_state=observed, owner=owner))
        for edge in study_edges[study_id]:
            child_kind, child_id = edge["record_kind"], edge["record_id"]
            child_context = context_path + (edge["context"],)
            if child_kind == "study":
                walk(child_id, child_context, study_id)
            elif child_id not in visited_runs:
                run_result = store.read(child_id, "run")
                if run_result.get("run_id") != child_id:
                    malformed("Run record identity does not match its Store ID")
                _validate_status(run_result, "Run", malformed)
                if type(run_result.get("samples")) is not list:
                    malformed("Run samples must be a list")
                visited_runs.add(child_id)
                item = status_from_result(store, run_result, verify_artifacts)
                item["parent_study_id"] = study_id
                item["context_path"] = list(child_context)
                item["normalized_samples"] = len(run_result["samples"])
                runs.append(item)
                run_views[child_id] = item
        visiting.remove(study_id)

    root = load_study(identifier)
    walk(identifier)
    root_status = root["status"]
    root_owner = _study_owner(root.get("lifecycle"))
    counts = _counts(root, runs)
    active_paths = _active_paths(identifier, study_results, study_edges, run_views)
    active_path = _common_prefix(active_paths)
    return dict(schema_version=1, record_kind="study", study_id=identifier,
        study_status=root_status, source_error=root.get("error"), error=None,
        name=root.get("name", root.get("spec", {}).get("name")),
        started_at=root.get("started_at"), ended_at=root.get("ended_at"), observed_at=utc(),
        owner=root_owner, observed_state=_observed_state(root_status, root_owner), progress=counts,
        active_path=active_path, active_paths=active_paths, studies=studies, runs=runs,
        eta=dict(evidence_state="Missing", total_remaining_seconds=None,
            reason="无足够可比的未来完整工作量成本，不猜StudyETA"),
        limitations=["Legacy studies have no live child pointer; only persisted child links are reported.",
            "Liveness is a point-in-time process identity observation.",
            "Terminal child records and normalized samples are counted once by run ID."])


def _legacy_children(result):
    edges = []
    jobs = result.get("jobs", [])
    if type(jobs) is not list:
        raise LabError("storage_failed", "Malformed legacy Study jobs")
    linked = []
    for job in jobs:
        if type(job) is not dict or (job.get("study_id") is not None and type(job["study_id"]) is not str):
            raise LabError("storage_failed", "Malformed legacy Study job")
        if job.get("study_id"):
            linked.append(job["study_id"])
    if linked:
        edges.extend(dict(record_kind="study", record_id=value, context={}, created_at="") for value in linked)
    else:
        members = result.get("runs", [])
        if type(members) is not list:
            raise LabError("storage_failed", "Malformed legacy Study runs")
        for member in members:
            if type(member) is not dict or type(member.get("run_id")) is not str or not member["run_id"]:
                raise LabError("storage_failed", "Malformed legacy Study run")
            edges.append(dict(record_kind="run", record_id=member["run_id"], context={}, created_at=""))
    return edges


def _study_owner(lifecycle):
    if lifecycle is None or lifecycle.get("owner") is None:
        return dict(alive=None, state="unverified", evidence_state="Missing", reason="Study has no owner identity")
    return observe_process(lifecycle["owner"])


def _observed_state(status, owner):
    if status != "running":
        return "terminal"
    if owner["alive"] is True:
        return "running_confirmed"
    if owner["alive"] is False:
        return "owner_exited"
    return "unverified"


def _validate_status(result, label, malformed):
    status = result.get("status")
    if type(status) is not str or status not in {"running", "completed", "failed", "cancelled", "timeout"}:
        malformed("Missing or unknown {} status".format(label))


def _counts(root, runs):
    lifecycle = root.get("lifecycle")
    planned = lifecycle.get("planned_runs") if lifecycle is not None else None
    if planned is None:
        planned = root.get("execution_budget", {}).get("run_calls")
    if planned is None:
        spec = root.get("spec", {})
        variants, values = spec.get("variants"), spec.get("axis", {}).get("values")
        if type(variants) is list and type(values) is list:
            planned = len(variants) * len(values)
    statuses = [run["run_status"] for run in runs]
    running = [run for run in runs if run["run_status"] == "running"]
    return dict(planned_runs=planned, created_runs=len(runs), terminal_runs=sum(s in _TERMINAL for s in statuses),
        completed_runs=statuses.count("completed"), failed_runs=statuses.count("failed"),
        cancelled_runs=statuses.count("cancelled"), timeout_runs=statuses.count("timeout"),
        running_records=len(running),
        confirmed_running_runs=sum(run["observed_state"] == "running_confirmed" for run in running),
        owner_exited_records=sum(run["observed_state"] == "owner_exited" for run in running),
        unverified_running_records=sum(run["observed_state"] == "unverified" for run in running),
        normalized_samples=sum(run["normalized_samples"] for run in runs))


def _active_paths(root_id, study_results, study_edges, run_views):
    def descend(study_id, prefix):
        result = study_results[study_id]
        lifecycle = result.get("lifecycle")
        if result.get("status") != "running" or lifecycle is None:
            return []
        paths = []
        indexes = lifecycle.get("active_children")
        if indexes is None:
            index = lifecycle.get("active_child")
            indexes = [] if index is None else [index]
        for index in indexes:
            edge = study_edges[study_id][index]
            path = prefix + [dict(record_kind="study", record_id=study_id,
                context=edge["context"], status="running")]
            if edge["record_kind"] == "study":
                nested = descend(edge["record_id"], path)
                paths.extend(nested if nested else [path])
            else:
                status = run_views[edge["record_id"]]
                path.append(dict(record_kind="run", record_id=edge["record_id"], context=edge["context"],
                    status=status["run_status"], observed_state=status["observed_state"]))
                paths.append(path)
        return paths
    return descend(root_id, [])


def _common_prefix(paths):
    if not paths:
        return []
    prefix = []
    for entries in zip(*paths):
        if all(entry == entries[0] for entry in entries[1:]):
            prefix.append(entries[0])
        else:
            break
    return prefix

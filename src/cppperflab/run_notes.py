"""Append-only operator dispositions for an existing run; measurements stay immutable."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import uuid

from .errors import LabError
from .protocol import positive, sha256
from .storage import write_json, utc


_RUN_ID = re.compile(r"\d{8}T\d{6}-[0-9a-f]{12}")
_EVENT_FIELDS = {"schema_version", "run_id", "revision", "previous_sha256",
                 "action", "value", "reason", "recorded_at"}


def _folder(store, run_id):
    store.record_path(run_id, "run")
    store.read(run_id)
    parent = store.root / "run-notes"
    if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
        raise LabError("storage_failed", "Run notes root is not a regular directory")
    folder = parent / run_id
    if folder.exists() and (folder.is_symlink() or not folder.is_dir()):
        raise LabError("storage_failed", "Run notes path is not a regular directory")
    return folder


def _decode_event(path, run_id, revision, previous_sha256):
    if path.is_symlink() or not path.is_file():
        raise LabError("storage_failed", "Run note is not a regular file", {"path": str(path)})
    try:
        event = json.loads(path.read_text(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeDecodeError) as error:
        raise LabError("storage_failed", "Run note JSON is malformed", {"path": str(path)}) from error
    if not isinstance(event, dict) or set(event) != _EVENT_FIELDS:
        raise LabError("storage_failed", "Run note fields do not match schema v1", {"path": str(path)})
    valid = (event["schema_version"] == 1 and type(event["schema_version"]) is int
             and event["run_id"] == run_id and event["revision"] == revision
             and type(event["revision"]) is int and event["previous_sha256"] == previous_sha256
             and isinstance(event["reason"], str) and bool(event["reason"].strip())
             and isinstance(event["recorded_at"], str) and bool(event["recorded_at"]))
    if not valid:
        raise LabError("storage_failed", "Run note identity or hash chain is invalid", {"path": str(path)})
    if event["action"] == "candidate_state":
        valid = event["value"] in ("retained", "withdrawn")
    elif event["action"] == "superseded_by":
        valid = event["value"] is None or isinstance(event["value"], str) and bool(_RUN_ID.fullmatch(event["value"]))
    else:
        valid = False
    if not valid:
        raise LabError("storage_failed", "Run note action or value is invalid", {"path": str(path)})
    return event


def _read(store, run_id):
    folder = _folder(store, run_id)
    events = []
    previous = None
    if folder.exists():
        names = sorted(path.name for path in folder.iterdir() if not path.name.startswith(".pending-"))
        for revision, name in enumerate(names, 1):
            filename = "%08d.json" % revision
            if name != filename:
                raise LabError("storage_failed", "Run note revisions are not continuous", {"path": str(folder / name)})
            event_path = folder / filename
            event = _decode_event(event_path, run_id, revision, previous)
            digest = sha256(event_path)
            events.append(dict(event, path=str(event_path.relative_to(store.root)), sha256=digest))
            previous = digest
    state = "undecided"
    superseded_by = None
    for event in events:
        if event["action"] == "candidate_state":
            state = event["value"]
        else:
            superseded_by = event["value"]
    return dict(schema_version=1, run_id=run_id, revision=len(events), current_sha256=previous,
                candidate_state=state, superseded_by=superseded_by, events=events)


def notes(store, run_id):
    """Read a hash-chained disposition; the last event has no external hash anchor."""
    return _read(store, run_id)


def _validate_reason(reason):
    if not isinstance(reason, str) or not reason.strip():
        raise LabError("invalid_request", "A nonempty reason is required for candidate metadata")


def _check_supersession(store, source_id, source, target_id):
    if target_id is None:
        return
    if not isinstance(target_id, str) or not _RUN_ID.fullmatch(target_id):
        raise LabError("invalid_request", "superseded_by must be a valid run ID or null")
    source_time = datetime.fromisoformat(source["started_at"])
    seen = {source_id}
    cursor = target_id
    while cursor is not None:
        if cursor in seen:
            raise LabError("invalid_request", "Candidate supersession would create a cycle")
        seen.add(cursor)
        target = store.read(cursor)
        if target["status"] != "completed" or target["correctness"]["status"] != "passed":
            raise LabError("invalid_request", "Replacement run must be completed with correctness passed", {"run_id": cursor})
        target_time = datetime.fromisoformat(target["started_at"])
        if target_time <= source_time:
            raise LabError("invalid_request", "Replacement must be a later experiment", {"run_id": cursor})
        cursor = notes(store, cursor)["superseded_by"]


def annotate(store, run_id, *, action, value, reason, expected_revision):
    """Atomically append one explicitly versioned candidate disposition event."""
    _validate_reason(reason)
    positive(expected_revision, "expected_revision", integer=True, zero=True)
    folder = _folder(store, run_id)
    source = store.read(run_id)
    if source["status"] not in ("completed","failed","cancelled","timeout"):
        raise LabError("invalid_request", "Candidate dispositions require a terminal run")
    if action == "candidate_state":
        if value not in ("retained", "withdrawn"):
            raise LabError("invalid_request", "candidate_state must be retained or withdrawn")
    elif action == "superseded_by":
        _check_supersession(store, run_id, source, value)
    else:
        raise LabError("invalid_request", "Unknown run note action")

    current = _read(store, run_id)
    if current["revision"] != expected_revision:
        raise LabError("incomparable", "Run note revision changed; read current metadata before retrying",
                       {"expected_revision": expected_revision, "current_revision": current["revision"]})
    if not folder.exists():
        folder.mkdir(parents=True, exist_ok=True)
    revision = current["revision"] + 1
    event = dict(schema_version=1, run_id=run_id, revision=revision,
                 previous_sha256=current["current_sha256"], action=action, value=value,
                 reason=reason, recorded_at=utc())
    temporary = folder / (".pending-%s.json" % uuid.uuid4().hex)
    destination = folder / ("%08d.json" % revision)
    try:
        write_json(temporary, event)
        try:
            os.link(temporary, destination)
        except FileExistsError as error:
            raise LabError("incomparable", "Run note revision was concurrently appended",
                           {"revision": revision}) from error
        return _read(store, run_id)
    finally:
        if temporary.exists():
            temporary.unlink()

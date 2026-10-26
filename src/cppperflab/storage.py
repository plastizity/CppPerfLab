"""Versioned local artifacts; JSON is authoritative, no duplicate index database."""
import datetime
import json
from pathlib import Path
import re
import uuid

from .errors import LabError
from .protocol import sha256


_RECORDS={"run": ("runs", "result.json"), "study": ("studies", "study.json"),
          "comparison": ("comparisons", "comparison.json"), "summary": ("summaries", "summary.json"),
          "collection": ("collections", "collection.json")}


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    temp.replace(path)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def create(self, kind="runs"):
        identifier = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
        path = self.root / kind / identifier
        path.mkdir(parents=True, exist_ok=False)
        return identifier, path

    def record_path(self, identifier, kind="run"):
        if not re.fullmatch(r"\d{8}T\d{6}-[0-9a-f]{12}", identifier):
            raise LabError("invalid_request", "Invalid run ID")
        if kind not in _RECORDS:
            raise LabError("invalid_request", "Unknown record kind")
        folder, filename = _RECORDS[kind]
        return self.root / folder / identifier / filename

    def read(self, identifier, kind="run"):
        path = self.record_path(identifier, kind)
        if not path.is_file():
            raise LabError("not_found", "Run not found: " + identifier)
        result = json.loads(path.read_text())
        if result.get("schema_version") != 1:
            raise LabError("unsupported_schema", "Unsupported result version")
        return result

    def list(self,kind='run'):
        if kind not in _RECORDS:raise LabError('invalid_request','Unknown record kind')
        folder,filename=_RECORDS[kind]
        return [self.read(p.parent.name,kind) for p in sorted((self.root/folder).glob('*/'+filename))]

    def baseline(self, name, run_id):
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) or name in (".", ".."):
            raise LabError("invalid_request", "Invalid baseline name")
        result = self.read(run_id)
        if result["status"] != "completed":
            raise LabError("invalid_request", "Cannot baseline incomplete experiment")
        directory = self.root / "baselines"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (name + ".json")
        # Baselines are immutable names; use a new name for each accepted revision.
        with path.open("x") as stream:
            json.dump({"schema_version": 1, "name": name, "run_id": run_id}, stream)
        return {"name": name, "run_id": run_id}


def inventory(directory):
    return [{"path": str(p.relative_to(directory)), "bytes": p.stat().st_size,
             "sha256": sha256(p)} for p in sorted(directory.rglob("*"))
            if p.is_file() and not p.is_symlink() and p.name not in ("result.json", "result.json.tmp")]

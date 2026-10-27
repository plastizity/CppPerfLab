"""Version-1 request boundary. No implicit recovery or unknown-field acceptance."""
import hashlib
import json
import math
import os
import re
from pathlib import Path
import shutil

from .errors import LabError


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise LabError("invalid_request", message)


def fields(value, allowed, required, name):
    require(isinstance(value, dict), name + " must be an object")
    require(not set(value) - set(allowed), name + ": unknown fields " + str(set(value) - set(allowed)))
    require(set(required) <= set(value), name + ": missing fields " + str(set(required) - set(value)))


def argv(value, name):
    require(isinstance(value, list) and bool(value) and all(
        isinstance(x, str) and "\x00" not in x for x in value), name + " must be a nonempty argv array")
    require(bool(value[0]), name + " executable is empty")


def positive(value, name, zero=False, integer=False):
    require(type(value) in ((int,) if integer else (int, float)) and math.isfinite(value)
            and (value >= 0 if zero else value > 0), name + " is invalid")


def executable(value, base):
    if "/" in value:
        path = (base / value).resolve()
    else:
        found = shutil.which(value, path=os.defpath)
        require(found is not None, "executable not found: " + value)
        path = Path(found)
    require(path.is_file() and os.access(path, os.X_OK), "executable not runnable: " + str(path))
    return str(path)


def load_request(path):
    path = Path(path).resolve()
    try:
        request = json.loads(path.read_text(), parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
    except (OSError, ValueError) as error:
        raise LabError("invalid_request", "Cannot read JSON request", {"reason": str(error)}) from error
    fields(request, ("schema_version", "name", "workload", "target", "measurement", "correctness", "goal", "claim", "execution_state"),
           ("schema_version", "name", "workload", "target", "measurement", "correctness"), "request")
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise LabError("unsupported_schema", "Only request schema_version=1 is supported")
    require(isinstance(request["name"], str) and bool(request["name"]), "name must be nonempty")
    workload = request["workload"]
    fields(workload, ("id", "parameters", "inputs", "expected_hashes"), ("id", "parameters", "inputs"), "workload")
    require(isinstance(workload["id"], str) and bool(workload["id"]), "workload.id must be nonempty")
    require(isinstance(workload["parameters"], dict), "workload.parameters must be an object")
    require(isinstance(workload["inputs"], list), "workload.inputs must be an array of paths")
    for i, item in enumerate(workload["inputs"]):
        require(isinstance(item, str), "input path must be a string")
        p = (path.parent / item).resolve()
        require(p.is_file(), "input missing: " + str(p))
        workload["inputs"][i] = str(p)
    if 'expected_hashes' in workload:
        expected=workload['expected_hashes']
        require(isinstance(expected,dict),'expected_hashes must be an object')
        resolved={}
        for name,digest in expected.items():
            require(isinstance(name,str) and isinstance(digest,str) and re.fullmatch(r'[0-9a-f]{64}',digest),'Invalid expected input hash')
            key=str((path.parent/name).resolve())
            require(key in workload['inputs'] and key not in resolved,'Expected hashes must name distinct declared inputs')
            resolved[key]=digest
        workload['expected_hashes']=resolved
    target = request["target"]
    fields(target, ("argv", "cwd", "environment", "build"), ("argv", "environment", "build"), "target")
    argv(target["argv"], "target.argv")
    target["argv"][0] = executable(target["argv"][0], path.parent)
    require(isinstance(target["environment"], dict) and all(
        isinstance(k, str) and k and "=" not in k and "\x00" not in k and
        isinstance(v, str) and "\x00" not in v for k, v in target["environment"].items()), "invalid environment")
    if "cwd" in target:
        require(isinstance(target["cwd"], str), "cwd must be a path")
        target["cwd"] = str((path.parent / target["cwd"]).resolve())
        require(Path(target["cwd"]).is_dir(), "cwd missing")
    fields(target["build"], ("revision", "compiler", "flags", "mode", "artifacts"),
           ("revision", "compiler", "flags", "mode", "artifacts"), "target.build")
    build = target["build"]
    require(all(isinstance(build[k], str) for k in ("revision", "compiler", "mode")), "build metadata must be strings; use unknown when unknown")
    for key in ("flags", "artifacts"):
        require(isinstance(build[key], list) and all(isinstance(x, str) for x in build[key]), "invalid build." + key)
    build["artifacts"] = [str((path.parent / p).resolve()) for p in build["artifacts"]]
    require(all(Path(p).is_file() for p in build["artifacts"]), "build artifact missing")
    measurement = request["measurement"]
    fields(measurement, ("tool", "repetitions", "warmups", "timeout_seconds", "cpu_affinity", "options", "limits", "phase_report", "phase_import", "runtime_identity", "progress"),
           ("tool", "repetitions", "warmups", "timeout_seconds", "options"), "measurement")
    require(isinstance(measurement["tool"], str), "tool must be a string")
    positive(measurement["repetitions"], "repetitions", integer=True)
    positive(measurement["warmups"], "warmups", zero=True, integer=True)
    positive(measurement["timeout_seconds"], "timeout_seconds")
    require(isinstance(measurement["options"], dict), "options must be an object")
    if 'progress' in measurement:require(type(measurement['progress']) is bool,'progress must be boolean')
    if "cpu_affinity" in measurement:
        cpus = measurement["cpu_affinity"]
        require(isinstance(cpus, list) and bool(cpus) and all(type(n) is int and n >= 0 for n in cpus), "cpu_affinity must be a nonempty CPU list")
        require(hasattr(os, "sched_getaffinity") and set(cpus) <= os.sched_getaffinity(0), "requested affinity unavailable")
    if 'phase_report' in measurement:
        report=measurement['phase_report']
        require(isinstance(report,str) and Path(report).name==report and report.endswith('.phases.json') and '\x00' not in report,
                'phase_report must be a *.phases.json filename in the measurement directory')
    if 'phase_import' in measurement:
        spec=measurement['phase_import']
        fields(spec,('source','mapping','mapping_sha256'),('source','mapping'),'phase import')
        require('phase_report' in measurement,'phase_import requires a phase_report destination')
        require(isinstance(spec['source'],str) and bool(spec['source']) and Path(spec['source']).name==spec['source']
            and '\x00' not in spec['source'] and spec['source'] not in (measurement['phase_report'],'phase-import.mapping.json'),
            'phase_import source must be a distinct filename inside the execution directory')
        require(isinstance(spec['mapping'],str) and bool(spec['mapping']),'phase_import mapping path required')
        spec['mapping']=str((path.parent/spec['mapping']).resolve())
        from .phase_import import load_mapping
        load_mapping(spec['mapping'])
        actual=sha256(spec['mapping'])
        if 'mapping_sha256' in spec:
            require(isinstance(spec['mapping_sha256'],str) and re.fullmatch(r'[0-9a-f]{64}',spec['mapping_sha256'])
                and spec['mapping_sha256']==actual,'Pinned phase mapping hash does not match')
        spec['mapping_sha256']=actual
    if 'runtime_identity' in measurement:
        from .runtime_identity import validate_config
        validate_config(measurement['runtime_identity'],path.parent)
    limits = measurement.get("limits", {})
    fields(limits, ("address_space_bytes", "cpu_seconds"), (), "limits")
    for key, value in limits.items():
        positive(value, key, integer=True)
    correctness = request["correctness"]
    fields(correctness, ("argv", "description", "sample_validation"), ("argv", "description"), "correctness")
    argv(correctness["argv"], "correctness.argv")
    correctness["argv"][0] = executable(correctness["argv"][0], path.parent)
    require(isinstance(correctness["description"], str) and bool(correctness["description"]), "correctness.description required")
    if 'sample_validation' in correctness:
        from .solver import validate_contract
        validation=correctness['sample_validation']
        validate_contract(validation)
        argv(validation['argv'],'sample_validation.argv')
        validation['argv'][0]=executable(validation['argv'][0],path.parent)
    if "goal" in request:
        goal = request["goal"]
        fields(goal, ("metrics", "max_overhead", "allow_instrumentation"),
               ("metrics", "max_overhead", "allow_instrumentation"), "goal")
        require(isinstance(goal["metrics"], list) and bool(goal["metrics"]) and all(isinstance(x, str) for x in goal["metrics"]), "goal.metrics required")
        require(goal["max_overhead"] in ("low", "moderate", "high"), "invalid overhead budget")
        require(type(goal["allow_instrumentation"]) is bool, "allow_instrumentation must be boolean")
    require(measurement["tool"] != "auto" or "goal" in request, "auto selection requires a capability goal")
    from .contracts import validate_claim, validate_execution_state
    if 'claim' in request:validate_claim(request['claim'])
    if 'execution_state' in request:validate_execution_state(request['execution_state'],path.parent)
    return request


def metric(name, value, unit, method, scope, evidence, **extra):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise LabError("parse_failed", "Nonfinite or nonnumeric metric: " + name)
    state = extra.pop("evidence_state", "Measured")
    require(state in ("Measured", "Estimated", "Modeled"), "Numeric metric requires a numeric evidence state")
    return dict(name=name, value=value, unit=unit, method=method, scope=scope,
                value_type="integer" if type(value) is int else "number",
                status="observed", evidence=evidence, evidence_version=1,
                evidence_state=state, **extra)

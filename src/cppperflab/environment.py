"""Evidence about host and native build; unknown provenance stays unknown."""
import os
from pathlib import Path
import platform
import shutil

from .execution import execute
from .protocol import sha256
from .storage import write_json


def read_optional(path):
    try:
        return Path(path).read_text().strip()
    except FileNotFoundError:
        return None


def discover():
    cpu = read_optional("/proc/cpuinfo") or ""
    first = cpu.split("\n\n")[0]
    fields = dict(line.split(":", 1) for line in first.splitlines() if ":" in line)
    fields = {k.strip(): v.strip() for k, v in fields.items()}
    return {"os": platform.system(), "kernel": platform.release(), "machine": platform.machine(),
            "hostname": platform.node(), "wsl": "microsoft" in platform.release().lower(),
            "cpu_model": fields.get("model name"), "cpu_flags": fields.get("flags", "").split(),
            "logical_cpus": os.cpu_count(),
            "allowed_cpus": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
            "numa_nodes": read_optional("/sys/devices/system/node/online"),
            "cache": {p.parent.name: {k: read_optional(p.parent / k) for k in ("level", "type", "size", "shared_cpu_list")}
                      for p in Path("/sys/devices/system/cpu/cpu0/cache").glob("index*/level")},
            "governor": read_optional("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
            "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
            "perf_event_paranoid": read_optional("/proc/sys/kernel/perf_event_paranoid"),
            "scheduling_tracepoints": scheduling_tracepoints(),
            "python": platform.python_version(),
            "limitations": ["Guest CPU frequency, topology and PMU may differ from host; host load/power are uncontrolled."] if "microsoft" in platform.release().lower() else []}


def scheduling_tracepoints():
    formats={name:[dict(path=str(Path(root)/'sched'/name/'format'),readable=os.access(Path(root)/'sched'/name/'format',os.R_OK))
                   for root in ('/sys/kernel/tracing/events','/sys/kernel/debug/tracing/events')]
             for name in ('sched_switch','sched_wakeup')}
    accessible=all(any(p['readable'] for p in paths) for paths in formats.values())
    return dict(evidence_state='Missing' if accessible else 'Unsupported',formats=formats,
        reason='Scheduling format files readable; event capture permissions have not been probed' if accessible else
               'Required scheduling event format files are not accessible in this environment',
        scope='Tracepoint metadata readiness only; not proof of collection access or a collected scheduler timeline')


def identity_snapshot(request):
    paths = [request["target"]["argv"][0], request["correctness"]["argv"][0]]
    if 'sample_validation' in request['correctness']:
        paths.append(request['correctness']['sample_validation']['argv'][0])
    paths += request["target"]["build"]["artifacts"]
    artifact_hashes = {p: sha256(p) for p in sorted(set(paths))}
    inputs = {p: sha256(p) for p in request["workload"]["inputs"]}
    identity = {"artifacts": artifact_hashes, "inputs": inputs}
    if 'phase_import' in request['measurement']:
        mapping=request['measurement']['phase_import']['mapping']
        identity['measurement_controls']={mapping:sha256(mapping)}
    return identity


def fingerprints(request, directory):
    identity=identity_snapshot(request)
    write_json(directory / "fingerprints.json", identity)
    return identity


def binary_info(request, directory):
    executable = request["target"]["argv"][0]
    readelf = shutil.which("readelf")
    if not readelf:
        return {"build_id": None, "debug_info": None, "reason": "readelf_missing"}
    run = directory / "binary-info"
    record = execute([readelf, "-n", "-S", "-W", executable], run, timeout=20)
    text = (run / "stdout.txt").read_text()
    build_id = next((line.split("Build ID:", 1)[1].strip() for line in text.splitlines() if "Build ID:" in line), None)
    return {"build_id": build_id, "debug_info": ".debug_info" in text,
            "returncode": record["returncode"], "evidence": "binary-info/stdout.txt",
            "note": "ELF identity only; source/build claims supplied in request are not inferred from this."}

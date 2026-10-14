"""Tool contracts: descriptors, actual probes, argv preparation, evidence parsers.

No adapter executes shell strings. Tools keep their own metric semantics.
"""
import csv
import json
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import shlex
import os
import sys

from .errors import LabError
from .execution import execute, check_execution
from .protocol import metric, require
from .tool_config import configured_tool


EVENTS = {
    "duration_time": ("time.wall", "ns", "elapsed-count"),
    "task-clock": ("time.cpu", "ns", "software-counter"),
    "cycles": ("cpu.cycles", "count", "hardware-counter"),
    "instructions": ("cpu.instructions", "count", "hardware-counter"),
    "cache-references": ("cpu.cache_references", "count", "hardware-counter"),
    "cache-misses": ("cpu.cache_misses", "count", "hardware-counter"),
    "branches": ("cpu.branches", "count", "hardware-counter"),
    "branch-misses": ("cpu.branch_misses", "count", "hardware-counter"),
    "page-faults": ("os.page_faults", "count", "software-counter"),
    "context-switches": ("os.context_switches", "count", "software-counter"),
}


def find_tool(name):
    # Installation-specific location, never a workload-specific dependency.
    root = os.environ.get("CPPPERFLAB_TOOL_DIR")
    return shutil.which(name, path=(root + os.pathsep + os.defpath) if root else None)


def parse_hyperfine(path):
    raw = json.loads(Path(path).read_text())
    if len(raw["results"]) != 1:
        raise LabError("parse_failed", "Expected one Hyperfine command")
    result = raw["results"][0]
    values, exits = result["times"], result["exit_codes"]
    if not values or len(values) != len(exits):
        raise LabError("parse_failed", "Incomplete Hyperfine samples")
    if any(exits):
        raise LabError("target_failed", "Hyperfine target failed", {"exit_codes": exits})
    return {"metrics": [], "samples": [
        [metric("time.wall", v, "seconds", "native-process-timing", "target process including startup; shell disabled; stdout/stderr piped and discarded by Hyperfine", path.name)]
        for v in values], "hotspots": [], "unavailable_metrics": [],
        "limitations": ["Hyperfine CPU time aggregates remain in raw JSON; they are not per-sample CPU times.",
                        "Target output is retained from correctness/preflight; Hyperfine timing output uses a pipe."]}


def parse_google_benchmark(path):
    raw = json.loads(Path(path).read_text())
    samples, names = [], set()
    for row in raw["benchmarks"]:
        if row.get("error_occurred"):
            raise LabError("target_failed", "Google Benchmark reported an error", {"row": row})
        if row.get("run_type", "iteration") != "iteration":
            continue
        name = row.get("run_name", row["name"])
        names.add(name)
        factors = {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1}
        if row["time_unit"] not in factors or row["iterations"] <= 0:
            raise LabError("parse_failed", "Invalid Google Benchmark unit/iterations")
        factor = factors[row["time_unit"]]
        samples.append([metric("time.iteration.wall", row["real_time"] * factor, "seconds",
                               "in-process-benchmark", "per iteration of " + name, path.name),
                        metric("time.iteration.cpu", row["cpu_time"] * factor, "seconds",
                               "in-process-benchmark", "per iteration of " + name, path.name)])
    if len(names) != 1 or not samples:
        raise LabError("parse_failed", "Filter must select exactly one Google Benchmark case")
    return {"metrics": [], "samples": samples, "hotspots": [], "unavailable_metrics": [],
            "library_version": raw.get("context", {}).get("library_version", "unknown"),
            "limitations": ["Registered benchmark timing excludes startup; setup scope is owned by target benchmark code.",
                            "Iteration averages are independent repetition samples, not individual-iteration observations."]}


def parse_stat(path, events):
    metrics, unavailable = [], []
    seen = set()
    for row in csv.reader(Path(path).read_text().splitlines(), delimiter=";"):
        if not row or row[0].startswith("#") or len(row) < 5:
            continue
        event = row[2].strip().split(":")[0]
        if event not in events:
            continue
        if event in seen:
            raise LabError("parse_failed", "Duplicate perf event: " + event)
        seen.add(event)
        value = row[0].strip()
        if value.startswith("<"):
            unavailable.append({"event": event, "reason": value})
            continue
        try:
            count = float(value)
            running = float(row[4])
        except ValueError as error:
            raise LabError("parse_failed", "Malformed perf CSV", {"row": row}) from error
        name, unit, method = EVENTS[event]
        # perf --no-scale + CSV reports clock events in raw ns (unit empty).
        # Some perf releases label clocks msec; preserve conversion explicitly.
        reported_unit = row[1].strip()
        if event in ("duration_time", "task-clock"):
            factors = {"": 1, "ns": 1, "nsec": 1, "msec": 1000000, "ms": 1000000}
            if reported_unit not in factors:
                raise LabError("parse_failed", "Unsupported perf clock unit " + reported_unit)
            count *= factors[reported_unit]
        elif reported_unit:
            raise LabError("parse_failed", "Unexpected counter unit " + reported_unit)
        scope = "target-command elapsed including exec/startup" if event == "duration_time" else "target process and inherited threads; perf event modifiers retained in raw evidence"
        metrics.append(metric(name, count, unit, method, scope, path.name,
                              event=row[2].strip(), running_percent=running,
                              limitations=["unscaled count; incomplete running time is not extrapolated"] if running < 99 else []))
    if seen != set(events):
        raise LabError("parse_failed", "Missing perf events", {"missing": sorted(set(events) - seen)})
    return {"metrics": metrics, "unavailable_metrics": unavailable, "hotspots": []}


def parse_massif(path):
    text = Path(path).read_text()
    snapshots = []
    for block in text.split("snapshot=")[1:]:
        snapshot = {}
        for key in ("mem_heap_B", "mem_heap_extra_B", "mem_stacks_B"):
            match = re.search(r"^" + key + r"=(\d+)$", block, re.M)
            if match is None:
                raise LabError("parse_failed", "Incomplete Massif snapshot: " + key)
            snapshot[key] = int(match.group(1))
        snapshots.append(snapshot)
    if not snapshots:
        raise LabError("parse_failed", "No Massif snapshots")
    return {"metrics": [metric("memory.heap_peak_observed", max(s["mem_heap_B"] for s in snapshots),
                                "bytes", "instrumented-snapshot", "target heap payload only; excludes RSS, allocator bookkeeping and stacks", path.name)],
            "unavailable_metrics": [], "hotspots": [], "snapshot_count": len(snapshots),
            "limitations": ["Maximum recorded snapshot; Massif snapshot selection and peak-inaccuracy apply.",
                            "Allocation call trees remain in massif.out and ms_print.txt; no inferred allocation count."]}


def parse_perf_report(path):
    hotspots = []
    for line in Path(path).read_text().splitlines():
        # --field-separator ';': overhead;comm;dso;symbol. Keep symbol verbatim.
        fields = [s.strip() for s in line.split(";")]
        if len(fields) >= 4 and re.fullmatch(r"\d+(?:\.\d+)?%", fields[0]):
            hotspots.append({"self_percent": float(fields[0][:-1]), "command": fields[1],
                             "dso": fields[2], "symbol": fields[3],
                             "evidence": path.name, "method": "sample-period-weighted"})
    total_match = re.search(r"# Samples:\s*(\d+)\s+of event", Path(path).read_text())
    unknown = sum(h["self_percent"] for h in hotspots if "0x" in h["symbol"] or "unknown" in h["symbol"].lower())
    return {"metrics": [], "unavailable_metrics": [], "hotspots": hotspots,
            "sample_count": int(total_match.group(1)) if total_match else None,
            "unknown_symbol_self_percent": unknown,
            "limitations": ["Self overhead is an on-CPU sample-period share, not wall time or causal benefit.",
                            "Low sample counts and unknown symbols limit interpretation."]}


@dataclass(frozen=True)
class Adapter:
    id: str
    executable: str
    description: str
    metrics: tuple
    overhead: str
    mode: str
    source: str
    license: str
    prerequisites: tuple
    limitations: tuple
    formats: tuple

    @property
    def batch(self):
        return self.id in ("hyperfine", "google-benchmark")

    def tool_path(self):
        configured = configured_tool(self.id)
        if configured is not None and "executable" in configured:
            path = Path(configured["executable"])
            return str(path) if path.is_file() and os.access(path, os.X_OK) else None
        override = os.environ.get("CPPPERFLAB_" + self.id.upper().replace("-", "_") + "_PATH")
        return override or find_tool(self.executable)

    def describe(self):
        return {**self.__dict__, "platforms": ["Linux", "WSL2 with successful probe"],
                "compilers": ["any for external executable"], "hardware": ["tool/OS supported CPU; probe required"],
                "source_instrumentation": self.id == "google-benchmark", "recompile_required": self.id == "google-benchmark",
                "target_integration_required": self.id == "google-benchmark",
                "debug_symbols": "recommended for symbolized stacks",
                "operations": ["describe", "probe", "validate", "prepare", "parse"],
                "parser_version": 1}

    def validate(self, options):
        allowed = {"google-benchmark": {"filter", "min_time_seconds", "warmup_seconds"}, "hyperfine": set(), "perf-stat": {"events"}, "perf-record": {"frequency"}, "massif": set()}[self.id]
        require(not set(options) - allowed, self.id + ": unsupported options " + str(set(options) - allowed))
        if self.id == "perf-stat":
            events = options.get("events", ["duration_time"])
            require(isinstance(events, list) and bool(events) and all(isinstance(x, str) for x in events)
                    and len(events) == len(set(events)) and set(events) <= set(EVENTS), "unsupported or duplicate perf events")
        if self.id == "perf-record":
            rate = options.get("frequency", 99)
            require(type(rate) is int and 1 <= rate <= 1000, "sampling frequency must be 1..1000")
        if self.id == "google-benchmark":
            from .protocol import positive
            require(isinstance(options.get("filter"), str) and bool(options["filter"]), "Google Benchmark needs an explicit case filter")
            positive(options.get("min_time_seconds", .1), "min_time_seconds")
            positive(options.get("warmup_seconds", 0), "warmup_seconds", zero=True)

    def prepare(self, command, directory, options, repetitions=3, warmups=0):
        self.validate(options)
        if self.id == "google-benchmark":
            require(warmups == 0, "Use Google Benchmark options.warmup_seconds, not process warmups")
            require(not any(a.startswith("--benchmark_") for a in command[1:]), "Benchmark flags belong in adapter options")
            return command + ["--benchmark_filter=" + options["filter"],
                              "--benchmark_out=" + str(directory / "benchmark.json"),
                              "--benchmark_out_format=json", "--benchmark_report_aggregates_only=false",
                              "--benchmark_repetitions=" + str(repetitions),
                              "--benchmark_min_time=" + str(options.get("min_time_seconds", .1)) + "s",
                              "--benchmark_min_warmup_time=" + str(options.get("warmup_seconds", 0))]
        exe = self.tool_path()
        if not exe:
            raise LabError("unavailable", self.executable + " is not installed")
        if self.id == "hyperfine":
            # Hyperfine's no-shell mode accepts a shellwords-encoded argv. This is
            # quoting only: no shell interpreter, expansion, prepare or cleanup commands.
            return [exe, "--shell=none", "--style=basic", "--output=pipe",
                    "--runs", str(repetitions), "--warmup", str(warmups),
                    "--export-json", str(directory / "hyperfine.json"), "--", shlex.join(command)]
        if self.id == "perf-stat":
            return [exe, "stat", "--no-scale", "-x", ";", "-o", str(directory / "perf.csv"),
                    "-e", ",".join(options.get("events", ["duration_time"])), "--"] + command
        if self.id == "perf-record":
            return [exe, "record", "-e", "cpu-clock", "-F", str(options.get("frequency", 99)),
                    "--call-graph", "dwarf", "-o", str(directory / "perf.data"), "--"] + command
        return [exe, "--tool=massif", "--massif-out-file=" + str(directory / "massif.out")] + command

    def parse(self, directory, options, record):
        check_execution(record)
        if self.id == "google-benchmark":
            if record["returncode"]:
                raise LabError("target_failed", "Integrated benchmark exited nonzero")
            result = parse_google_benchmark(directory / "benchmark.json")
        elif self.id == "hyperfine":
            if record["returncode"]:
                diagnostic = (directory / "stderr.txt").read_text()
                category = "target_failed" if "non-zero exit code" in diagnostic else "tool_failed"
                raise LabError(category, "Hyperfine failed", {"stderr": diagnostic})
            result = parse_hyperfine(directory / "hyperfine.json")
        elif self.id == "perf-stat":
            result = parse_stat(directory / "perf.csv", options.get("events", ["duration_time"]))
        elif self.id == "massif":
            result = parse_massif(directory / "massif.out")
        else:
            report_dir = directory / "report"
            report = execute([self.tool_path(), "report", "--stdio", "--no-children",
                              "--percent-limit", "0", "--field-separator", ";",
                              "--sort", "comm,dso,symbol", "-i", str(directory / "perf.data")],
                             report_dir, timeout=60)
            check_execution(report)
            if report["returncode"]:
                raise LabError("tool_failed", "perf report failed; raw perf.data retained")
            (directory / "perf-report.txt").write_bytes((report_dir / "stdout.txt").read_bytes())
            result = parse_perf_report(directory / "perf-report.txt")
            diagnostics = (report_dir / "stderr.txt").read_text()
            if diagnostics.strip():
                result["limitations"].append(diagnostics.strip())
        if record["returncode"]:
            # A successfully parsed tool output with a nonzero workload status is target failure.
            raise LabError("target_failed", "Target returned nonzero under " + self.id,
                           {"returncode": record["returncode"]})
        if result["unavailable_metrics"]:
            raise LabError("unavailable", "Requested events could not be counted", result)
        return result

    def probe(self, directory, options=None, target=None):
        options = options or {}
        directory.mkdir(parents=True)
        if self.id == "google-benchmark":
            if target is None:
                return {"tool": self.id, "available": False, "reason": "integrated_target_required", "version": None}
            self.validate(options)
            probe = directory / "execution"
            probe.mkdir()
            # Execute the integrated program's registration listing, not a timer
            # before its correctness gate. Full collection is verified by run().
            record = execute(target["argv"] + ["--benchmark_list_tests=true", "--benchmark_filter=" + options["filter"]], probe,
                             cwd=target.get("cwd"), env=target["environment"], timeout=30)
            try:
                check_execution(record)
                cases = (probe / "stdout.txt").read_text().strip().splitlines()
                if record["returncode"] or len(cases) != 1:
                    raise LabError("unavailable", "Integrated target must list exactly one case")
                return {"tool": self.id, "available": True, "version": "unknown-until-collection",
                        "reason": None, "metrics": list(self.metrics), "cases": cases,
                        "probe_scope": "target registration listing succeeded; JSON collection/parser verified only after correctness in run"}
            except LabError as error:
                if error.category == "cancelled":
                    raise
                return {"tool": self.id, "available": False, "reason": error.category, "diagnostic": str(error), "version": None}
        self.validate(options)
        exe = self.tool_path()
        if not exe:
            return {"tool": self.id, "available": False,
                    "reason": "configured_path_missing_or_not_usable" if configured_tool(self.id) else "executable_missing",
                    "configured": configured_tool(self.id), "version": None}
        version_record = execute([exe, "--version"], directory / "version", timeout=10)
        version = ((directory / "version/stdout.txt").read_text() +
                   (directory / "version/stderr.txt").read_text()).strip()
        if version_record["returncode"]:
            return {"tool": self.id, "available": False, "reason": "version_probe_failed", "version": version}
        command = ["/bin/sleep", "0.1"] if self.id == "hyperfine" else ["/bin/true"]
        if self.id == "perf-record":
            command = [sys.executable, "-c", "sum(i*i for i in range(2000000))"]
        target = directory / "execution"
        target.mkdir()
        try:
            record = execute(self.prepare(command, target, options), target, timeout=20)
            parsed = self.parse(target, options, record)
            if self.id == "perf-record" and not parsed["hotspots"]:
                raise LabError("parse_failed", "Sampling produced no parseable hotspots")
            extra = {}
            if self.executable == "perf":
                build = execute([exe, "version", "--build-options"], directory / "build-options", timeout=10)
                text = (directory / "build-options/stdout.txt").read_text()
                extra["build_options_evidence"] = str(directory / "build-options/stdout.txt")
                extra["symbolization_available"] = bool(re.search(r"libelf:\s*\[\s*on", text)) if build["returncode"] == 0 else None
                extra["dwarf_unwind_available"] = bool(re.search(r"(?:dwarf-unwind|libunwind):\s*\[\s*on", text)) if build["returncode"] == 0 else None
            from .protocol import sha256
            return {"tool": self.id, "available": True, "version": version, "options": options, **extra,
                    "executable_path": exe, "executable_sha256": sha256(exe),
                    "limitations": parsed.get("limitations", []),
                    "probe_scope": "actual collection and parse on a small system executable; not target compatibility",
                    "metrics": sorted({m["name"] for sample in parsed.get("samples", [parsed["metrics"]]) for m in sample}
                                      | ({"cpu.hotspots"} if parsed["hotspots"] else set())), "reason": None}
        except (LabError, OSError) as error:
            if isinstance(error, LabError) and error.category == "cancelled":
                raise
            stderr = (target / "stderr.txt").read_text() if (target / "stderr.txt").exists() else ""
            if "Permission" in stderr or "Operation not permitted" in stderr or "perf_event_paranoid" in stderr:
                reason = "permission_denied"
            else:
                reason = error.category if isinstance(error, LabError) else "tool_failed"
            return {"tool": self.id, "available": False, "version": version,
                    "reason": reason, "diagnostic": str(error), "stderr": stderr}


REGISTRY = {
    a.id: a for a in (
        Adapter("hyperfine", "hyperfine", "Repeated native process benchmark; no shell",
                ("time.wall",), "low", "native-process-timing",
                "https://github.com/sharkdp/hyperfine", "MIT OR Apache-2.0",
                ("Hyperfine with --shell=none and JSON export",),
                ("process startup included", "not a microbenchmark library", "output is piped, not terminal output"),
                ("Hyperfine JSON",)),
        Adapter("google-benchmark", "", "Already integrated Google Benchmark target; per-iteration time",
                ("time.iteration.wall", "time.iteration.cpu"), "low", "in-process-benchmark",
                "https://github.com/google/benchmark", "Apache-2.0",
                ("target links Google Benchmark and registers cases", "filter selects one case", "independent correctness oracle"),
                ("cannot profile arbitrary executables", "benchmark code owns setup and optimizer barriers"),
                ("Google Benchmark JSON",)),
        Adapter("perf-stat", "perf", "Elapsed time and explicit software/hardware events",
                tuple(v[0] for v in EVENTS.values()), "low", "counting",
                "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/tools/perf", "GPL-2.0",
                ("perf_event permissions for requested events",),
                ("WSL PMU fidelity is not established by access alone", "Short commands include startup and measurement overhead",
                 "Generic cache events are not memory-bandwidth measurements"), ("perf delimiter CSV",)),
        Adapter("perf-record", "perf", "CPU-clock sampling and DWARF call stacks",
                ("cpu.hotspots",), "moderate", "sampling",
                "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/tools/perf", "GPL-2.0",
                ("software event access", "DWARF unwinding support"),
                ("on-CPU only; does not establish lock waiting", "sampling perturbs execution", "symbol quality depends on target artifacts"),
                ("perf.data", "perf report delimited text")),
        Adapter("massif", "valgrind", "Heap snapshot profiling with allocation stacks",
                ("memory.heap_peak_observed",), "high", "instrumentation",
                "https://valgrind.org/docs/manual/ms-manual.html", "GPL-2.0-or-later",
                ("Valgrind-compatible target ISA/runtime",),
                ("large slowdown; timings are not normal execution timings", "heap snapshots are neither RSS nor all allocations"),
                ("Massif snapshot text",)),
    )
}

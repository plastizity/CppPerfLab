# Actual executable and mapped-object identity v1

The opt-in `measurement.runtime_identity` observer runs inside the existing
execution lifecycle. It records actual Linux `/proc/PID/exe` identities and
executable file mappings from the owned process tree. It complements declared
artifact hashes; static RPATH, `ldd` output and a copied program's SHA-256 are not
substitutes for this runtime observation.

This is metadata observation, not a new benchmark adapter or numeric performance
metric. Execution, storage, budgets, comparison, analysis and reports remain the
existing platform interfaces. No target code changes, global settings, additional
target launches, privilege escalation or library replacement are performed.

## Enable explicitly

```json
"runtime_identity": {
  "stages": ["preflight", "measurement"],
  "interval_ms": 10,
  "max_snapshots": 80,
  "max_processes": 16,
  "required_objects": [{
    "basename": "libexample.so",
    "path": "relative/to/request/libexample.so",
    "sha256": "REPLACE_WITH_THE_REVIEWED_FILE_SHA256"
  }]
}
```

The digest placeholder must be replaced with 64 lowercase hexadecimal digits.
Query `schema request` and `schema runtime-identity` for the formal contracts.
All four observation controls are required positive integers/explicit stages.
`required_objects` is optional; omitting it requests observation without claiming
that any library expectation passed.

Only preflight and measurement launches can be observed. Oracle, warmup, validator,
metadata and probe commands do not acquire implicit observers. A requested
preflight is incompatible with an integrated Google Benchmark target, whose
lifecycle has no native preflight. No extra target execution is added to fill it.
For batch measurement the `measurement_group` labels an adapter launch, not one
function call or one internal benchmark repetition.

Choose `stages: ["preflight"]` when you need an initial loading check outside
formal timing. That evidence does **not** observe later measured launches. Enabling
measurement observation reads/writes data concurrently with the target; its
overhead is unquantified before execution. A low/moderate overhead goal therefore
cannot be declared feasible for an observed formal measurement. Use a separate
diagnostic request or explicitly permit the high-overhead path. The report gives
observer work wall time and post-execution hashing time; neither is a causal
estimate of target slowdown and neither is subtracted from timing.

`budget --config ...` lists observed launches, snapshot/process bounds, expected
objects and unknown hashing bytes. Additional target launches stay zero. Hashing
and trace I/O can warm caches; no cold-cache claim follows from this observer.

## Evidence and identification scope

The observer keeps open process-directory handles, records PID/start ticks/session
and follows first-level children of observed threads recursively. It does not scan
unrelated system processes. Direct target launches are known at the execution
boundary. Under a wrapper, a process is selected when its actual executable or
mapped image matches the configured target's device/inode. Wrapper executables
are retained separately in the raw observations, not mislabeled as the target.
This supports native targets and mapped guest images when they are observable;
an unidentified interpreter/script under a wrapper remains missing target coverage.

Each execution saves:

- `runtime-identity.json`: structured report, actual executable observations,
  selected target objects, device/inode, first/last snapshot references, file
  signatures, six-state hash evidence, expectation results and limitations.
- `runtime-snapshots/NNNNNN.json`: actual monotonic start/end times, raw stat/maps/
  cmdline/exe/children bytes encoded as base64, and parsed process/map facts.
- The existing execution record and original stdout/stderr. The run inventory
  hashes all these files just like native profiler output.

Files are hashed **after** execution, with mapped device/inode and stable
size/mtime/ctime checks. Hashes identify the observed backing files, not live code
pages or runtime patches. Deleted/ambiguous paths, inaccessible files or changed
metadata never acquire a substitute hash. Executable mappings are evidence of
mapping, not proof that a function in the file executed. Anonymous/JIT/vDSO code
does not acquire a fabricated file identity.

The snapshot count and distinct admitted process handles are bounded per launch.
Reaching a limit is recorded. Intervals include observer work and are not promised
to be an exact sampling frequency. Short-lived processes, rapid exec transitions,
loading between snapshots, thread/child races and permission limits can leave
gaps. Namespace paths are not translated to another filesystem. Non-UTF-8 proc
records remain raw evidence with Unsupported interpretation rather than invented
path names. `complete_loader_history` is always false.

These constraints follow the Linux interfaces: `/proc/PID/maps` exposes mapped
regions, `/proc/PID/exe` identifies the process executable, and children lists can
omit racing children. A retained proc handle does not become a new process when
a numeric PID is reused. See the [Linux kernel proc documentation](https://www.kernel.org/doc/html/latest/filesystems/proc.html).

## Explicit expectations

Rules select a basename in the union of **observed identified target processes**
within that execution, then check the resolved expected path and SHA-256. This
means an expected identity was observed; it does not certify every load event or
every unobserved child. Any observed conflicting match fails. Basename matching
is explicit user input, not guessed SONAME or dependency resolution.

| Status | Meaning |
|---|---|
| PASS | Every requested identity was observed with a matching path and valid hash |
| FAIL | An observed path or measured hash conflicts with its expectation |
| INCONCLUSIVE | Required object was not observed, or a reliable file hash is missing/invalid |
| UNSUPPORTED | Access/interpretation restrictions prevent a requested check |
| NOT_REQUESTED | No identity expectations were supplied; this is not an identity PASS |

Failed/inconclusive/unsupported required checks stop the run and retain evidence.
Preflight failures produce no formal samples. The algorithm's declared oracle can
still have passed: correct output and correct loaded-library identity are separate
conditions. Existing nonzero execution failures retain their original semantics.

Comparisons require the requested observation records and matching observer
controls; an observed measurement cannot silently compare with an unobserved one.
Expected library paths/hashes may differ as an explicit build treatment, and are
included in comparison treatment metadata. Required basename selectors must match
on both sides; a candidate cannot silently drop a baseline identity obligation. Each run must satisfy its own rules.
Unchanged observations do not prove that every unobserved library was identical.
Legacy runs without the optional field retain their original contract.

## Reproduce the actual synthetic case

```bash
cmake --build --preset profile --target runtime_loader runtime_candidate runtime_optional
ctest --test-dir build/profile -R runtime_ --output-on-failure
python3.11 scripts/make_runtime_examples.py
bash scripts/install.sh
source scripts/env.sh
perflab schema runtime-identity --json
perflab budget --config workloads/runtime-loaded-good.json --json
perflab run --config workloads/runtime-loaded-good.json --json
perflab analyze RUN_ID --section runtime_identities --json
perflab report RUN_ID --format html --output results/runtime-identity.html --json
```

The linked origin/candidate fixture libraries return the same exact integer 7.
The copied executable retains an absolute build RUNPATH. The stale request omits
the explicit candidate search path, so output correctness can pass while the
observed origin library violates the required candidate path/hash. The positive
request explicitly supplies the candidate search directory. This choice is in the
synthetic request, never an automatic platform modification of library loading.

```bash
perflab run --config workloads/runtime-loaded-stale.json --json
perflab run --config workloads/runtime-dlopen.json --json
perflab run --config workloads/runtime-preflight-only.json --json
```

The stale request is a deliberate failure control. The dlopen fixture additionally
validates integer 9 from a module loaded at runtime and retains its observed
mapping. The preflight-only example demonstrates its more limited coverage.
The brief sleeps keep maps observable; these examples establish integration and
identity checking, not a useful workload or an optimization speedup.

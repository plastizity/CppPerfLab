# Protocol v1 and execution contract

JSON Schema files are shipped in `src/cppperflab/schemas` and exposed through
`perflab schema KIND --json`. The CLI/runtime also checks file existence, argv,
affinity availability, supported adapter options and finite positive values.
Requests reject unknown fields/versions. No implicit migration or recovery occurs.
Result v1 readers tolerate additional descriptive fields but never reinterpret
an existing metric name/unit/method/scope. Breaking semantic changes require a new
schema version and an explicit migration. No old v0 draft-profile compatibility path
is maintained because those drafts never formed a public contract.

## Experiment request

- `workload.id` and `parameters` identify the problem and its fixed input features;
  `inputs` names files whose content hashes must match for comparison.
- `target.argv` is an array, never a shell command. Executable paths and metadata
  file paths are relative to the JSON file, or absolute. Target argument paths are
  passed literally: use absolute paths or `{run_dir}` for outputs. `cwd` defaults
  to the unique per-command directory. Explicit shared cwd is the user's choice.
- Only a minimal PATH/C locale/TZ and explicit `target.environment` reach children;
  arbitrary shell secrets/environment are not inherited. Environment is recorded.
  Thread settings belong here. Build metadata is declared, not reverse engineered;
  artifact hashes and ELF Build ID/debug-section evidence supplement it. Shared
  libraries/scripts relevant to reproducibility must be listed in build artifacts.
- `measurement` selects a tool/options, warmups, measured repetitions, a timeout
  and optional affinity/resource limits. Hyperfine timeout applies to the entire
  batch; other tools use one timeout per invocation. `RLIMIT_CPU`/address-space
  limits apply per process and are not cgroup limits for an entire process tree.
- `correctness.argv` is a trusted oracle command whose nonzero exit rejects the
  experiment. An oracle description records what was actually checked. An exit-zero
  assertion is not a mathematical proof. Correctness and preflight are outside timing.
- `goal` is optional for explicit selection and mandatory for `tool:auto`. It
  describes required metric IDs and allowed overhead/instrumentation; it is not a
  natural-language keyword map. Unsupported capabilities are returned as reasons.

## Normalized measurement

Each sample contains typed metrics: name, value, unit, value_type, status, method,
scope, tool/version and evidence. Hyperfine wall seconds, perf elapsed nanoseconds,
software clock nanoseconds, hardware event counts, sample-period percentages and
Massif heap snapshot bytes stay distinct. No RSS, allocation count or memory
bandwidth is invented from these metrics. Raw native files retain tool-specific data.

Perf uses unscaled counts and records running percentage. Unsupported/not-counted
events fail the requested run; partial native evidence survives. A mixed event set
is not silently retried as a software-only set. Event modifiers remain in raw data.
Massif's heap maximum is explicitly an observed-snapshot maximum. Timing under
Massif is diagnostic overhead, not a native performance metric.

## Lifecycle and errors

Run states: running → completed/failed/timeout/cancelled. The execution engine owns
the subprocess group; stdout/stderr are files, input is `/dev/null`, and timeout or
Ctrl-C/SIGTERM triggers cleanup. The main process is reaped. A daemon calling
`setsid()` can escape a POSIX group: such workloads are unsupported. No hostile-code
security boundary is claimed. External-tool probes use tiny system executables;
Google Benchmark probes execute the integrated target to list registered cases.

The final result stores request, environment before/after, tool/probe, identity,
oracle result, individual samples, descriptive statistics, raw evidence inventory,
and any error. Tool output with nonzero target status is classified as target failure
when native parsing succeeds; absent/broken output is tool/parse failure. Ambiguous
tool diagnostics remain preserved rather than guessed into successful results.

Exit codes: 2 invalid request/version/not-found; 3 capability/permission; 4 tool/parser;
5 target/correctness; 6 timeout; 7 incomparable; 8 storage; 70 internal; 130 cancellation.
`tools probe` reports all capability statuses and exits zero if discovery completed;
that does not assert all tools available. `run` exits nonzero when its run failed.

## Comparability

The comparer checks workload/input hashes, tools and versions, mode/options,
machine/CPU/kernel, affinity, explicit environment/cwd, warmup/resource controls,
oracle description and metric semantics. Source revision and executable hashes may
differ—that is the intended treatment. Command arguments must match unless the
caller explicitly acknowledges their difference. Build flag/compiler differences
are displayed as treatment metadata, not silently ignored in an optimization claim.
Sanitizer/native builds have different declared build modes and are rejected.

Every comparison retains sample arrays, medians, CV, descriptive unpaired bootstrap
intervals and explicit classification policy. Samples<7 or CV>10% are inconclusive.
The 5% material-change policy is a screening default, not a universal scientific
threshold. An observed improvement/regression requires reasoning about load, thermal
drift, input coverage and oracle sufficiency. No code is automatically accepted.

## Algorithm studies

Study v1 is a separate request type referencing ordinary experiment v1 templates,
not a new measurement format. One positive numeric feature varies at a declared argv
position; reviewed executable variants share the workload, controls and oracle.
`{candidate}` in oracle arguments receives the current variant. The input template
should be run through `study`, not directly while that placeholder is unresolved.
Finite experiment budget and seeded order are recorded. Failure preserves run IDs
and rejects the candidate; no score is assigned. Study output references the same
run store and comparison engine. Growth-model fits are empirical and range-limited.

The next extension should be a validated external tuner using this evaluator;
candidate generation, compilation and untrusted-code sandboxing are not implemented.

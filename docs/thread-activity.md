# Thread application activity timelines

This protocol summarizes application-declared phase activity per recorded thread
track. It complements phase timing, CPU sampling, and any operating-system
scheduler trace; it does not replace them. The machine-readable output contract is
[`activity-timeline.schema.json`](../src/cppperflab/schemas/activity-timeline.schema.json).

## Recording annotations

The phase file may include an `activity` object on a stage. Existing stage records
without it remain valid and are classified as unclassified time:

```json
{"kind":"application_work"}
{"kind":"application_wait","reason":"condition_variable"}
```

`application_work` permits only `kind`. `application_wait` requires a non-empty
caller-provided `reason`. These labels describe the scope declared by the
application; a reason such as `lock` is an annotation, not proof that lock
contention caused the observed delay.

The C++ SDK exposes `PhaseRecorder::Activity::{Work,Wait}`. Define phases before
recording scopes, for example:

```cpp
const auto compute = recorder.define_phase("compute", perflab::PhaseRecorder::Activity::Work);
const auto wait = recorder.define_phase("wait_for_result",
    perflab::PhaseRecorder::Activity::Wait, "future_get");
```

Unannotated scopes can be retained for partial instrumentation. For multiple
threads, construct one `LinuxMonotonic` recorder on each participating thread,
finish all scopes, join the threads, then call `PhaseRecorder::write_group` to
write a shared-clock phase file. That keeps file output outside the measured
scopes. Group recording requires compatible Linux monotonic clocks and process
namespaces.

## Meaning and aggregation

The analyzer groups root phase intervals by `track_id`. `observed_seconds` is the
union of those root intervals. Work and wait are unions of explicitly annotated
intervals within observed time; time not covered by either remains
`unclassified_seconds`. Identical overlapping annotations are unioned. Overlap
between different activity kinds or between different wait reasons is invalid and
is rejected. Durations across separate tracks can sum to more than the experiment
wall time because threads overlap.

`wait_fraction` is application-wait wall time divided by observed root wall time.
It is absent (`null`) unless the track has an explicit wait annotation and
nonzero observed duration. Likewise, `work_seconds` and `wait_seconds` are `null`
when the corresponding class was not declared. Missing is not measured zero.
`annotation_coverage_fraction` is the fraction of observed time classified as
application work or wait. A track with no activity annotations has
`evidence_state: Missing` at timeline level and its full observed time remains
unclassified.

The activity intervals measure application wall-clock scopes. A thread inside a
work scope may be preempted; a wait scope may include wake-up and scheduling delay.
Neither label establishes exact CPU-running time, off-CPU time, runnable delay,
context switches, or scheduler cause. `os_scheduler.evidence_state` is therefore
`Missing` in each timeline because no OS scheduler trace was collected. This does
not by itself say whether the host has a usable trace facility: environment
capability discovery reports `scheduling_tracepoints` separately as
`Unsupported` or `Missing`. The current activity feature collects application
annotations only; it does not claim to implement a kernel scheduler collector.
Do not infer lock contention or task scheduling root cause from wait labels alone;
design a discriminating experiment.

The timeline is descriptive, not a single score. Ratios and counts have no default
"smaller is better" direction. Compare only matching workload, scope, phase
definitions, and clock conditions. Keep correctness and the actual target
performance objective as separate gates. Unclassified or missing intervals are
not successful optimization evidence.

## Querying stored evidence

The intended read-only interface is:

```bash
perflab timeline RUN_ID --measurement 0 --json > timeline.json
perflab timeline RUN_ID --measurement 0 --track worker-0 --format html --output timeline.html
perflab timeline RUN_ID --stage preflight --json > preflight-timeline.json
perflab analyze RUN_ID --section thread_timelines --json
```

Timeline queries read the selected run's stored phase evidence and do not execute
the target. JSON is written to standard output (or selected with `--json`); the
`--output FILE` option applies to HTML reports. A `preflight` query is available
only when that run contains a preflight phase record. The query response is a
flat timeline object with optional run metadata (`run_id`, `run_status`,
`source_error`, `error`, `execution_stage`, `measurement_group`, and
`selection_status`), as described by the output schema. The timeline body is not
nested under a `timeline` key. Use JSON for machine processing and HTML for human
inspection; both represent the same activity evidence and limitations.

## Output fields

Each track reports its `track_id`, nullable `pid`/`tid`, identity evidence state,
observed/work/wait/unclassified seconds, wait fraction, annotation coverage,
wait reasons, and source intervals. The top-level evidence reference identifies
the stored phase evidence used. `os_scheduler` is explicitly Missing for this
run until scheduler evidence is actually collected by a supported source. The
schema is versioned independently from the phase input schema.

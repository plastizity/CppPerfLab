# Run and Study status, progress and candidate history

## Inspect a run

Use the existing run ID to inspect a live or finished experiment:

```bash
perflab status RUN_ID --json
perflab status RUN_ID --verify-artifacts --json
perflab history --json
perflab history --kind all --json
perflab analyze RUN_ID --section lifecycle --json
perflab report RUN_ID --format html --output results/run.html --json
```

Use `history --kind study` (or `--kind all`) to discover the IDs of already
created running Studies through the CLI. The default remains the run-only history.
Each Study row includes its verified owner state, current delegation chain and
child counts; no directory scan by the consuming Agent is needed.

Status separates the saved run outcome (`run_status`, with its original failure
under `source_error`) from this query's success. For opted-in runs it shows the
current lifecycle stage, launch groups started/finished, normalized sample count,
and current process identity. Process checks compare PID, start ticks, boot ID and
PID namespace, so a reused PID is not reported as the original process. A process
observer is a point-in-time liveness check; it does not prove forward progress.
The active process is the execution group leader and may be a profiler or wrapper.
Runtime executable and library observations are shown separately when captured.
`--verify-artifacts` explicitly rehashes recorded artifact paths. Missing run-time
fingerprints, removed files and denied reads remain missing/unsupported; an empty
check cannot set `artifacts_verified=true`. The current file check never changes a
binary, rebuilds it or claims to observe its loaded process image.

Live writes can overlap the target process and their slowdown has not been
calibrated. Enable progress explicitly in a request with `"measurement": {
"progress": true}`; the planner rejects that request when its declared maximum
overhead is below `high`. The budget reports zero additional target launches and
calls out the metadata interference. Requests without this option keep their
existing measurement behavior. Completed older runs have no synthesized liveness.
The enabled/disabled setting is a shared comparison control: allowing a candidate
command change does not permit comparisons that differ in progress interference.
An omitted setting is equivalent to explicit `false` for older requests.
If an owner process is missing, reused or outside the observable namespace, status
reports an unverified/exited state rather than trusting a stale `running` string.

Counts describe execution groups rather than equivalent amounts of computation.
Adaptive Google Benchmark iterations and opaque commands remain unknown. The
existing `progress.eta` remains a measurement-stage estimate: after at least two
measurement groups finish, it may report remaining pipeline time and the observed
range. It is stamped at the last group boundary, includes the current group in full
if it was then running, and excludes final hashing/summarization.

The separate root `workflow_eta` estimates the remaining whole-run workflow from
completed runs with a matching cost key. It is `Estimated` only with at least two
eligible historical runs; it reports their median total duration, observed range,
sample count and source run IDs. This is a descriptive estimate, not a confidence
interval, guarantee, benchmark metric or acceptance result. Elapsed time is derived
from `CLOCK_MONOTONIC` only while the identity-checked owner is alive and the clock
boot ID and time namespace match. UTC timestamps are never subtracted. Missing
`/proc` clock-context values carry a reason and cannot support a live elapsed-time
estimate. When elapsed
time reaches/passes the historical median, or the observed range is exceeded, the
estimate becomes `Missing` rather than reporting zero remaining time.

Cost keys bind the full request except its display name, actual execution identity,
tool identity/version/path/hash, static machine characteristics, workflow model
version and implementation source hash. Different inputs, tools or versions do not
share history. A tool whose real version is unknown (including
`unknown-until-collection`) does not produce a history key. Legacy records without
monotonic timing and a cost key cannot contribute. `workflow_cost` records the
monotonic run-lifetime evidence; it does not replace the target's performance
metrics. If the selected tool executable, CppPerfLab implementation or its schema files change during a run,
the duration remains recorded but the evidence is `Invalid` and the cost key is
cleared, so it cannot enter history. When lifecycle progress is disabled, the
workflow record carries the owner process identity for status checks. This enables
root ETA without adding progress writes during target execution; only the initial,
existing fingerprint-boundary and final result writes occur. With progress enabled,
status reuses the lifecycle owner rather than duplicating it. Study ETA remains
`Missing` because heterogeneous future jobs do not yet have a trustworthy common
cost model.

## Inspect a Study

A Study status query follows the Study's persisted child links and reports its
nested studies and ordinary runs together:

```bash
perflab status STUDY_ID --kind study --json
perflab status STUDY_ID --kind study --verify-artifacts --json
perflab analyze STUDY_ID --kind study --section lifecycle --json
perflab report STUDY_ID --kind study --format html --output results/study.html --json
```

`status` continues to mean a run unless `--kind study` is supplied. Study live
lifecycle tracking is enabled only when any request template in that Study sets
`measurement.progress=true`. In that mode the Study records its process identity,
planned run count, child record IDs, their context, creation time and active child
index. Each child link is persisted after the child's initial record is written
and before the target starts. The existing serial execution order is retained.

Composed work is represented as a tree, such as assessment → paired/cases → run.
The response exposes `studies`, deduplicated `runs`, and an explicit `active_path`.
The run list counts each run ID once even when a parent and nested Study both refer
to it; progress counts therefore describe distinct linked run records and their
normalized samples, not a sum of repeated nested references. `running_records` counts
saved `status="running"` records regardless of liveness; `confirmed_running_runs`,
`owner_exited_records` and `unverified_running_records` separate their process observations.
`planned_runs` is reported when recorded or derivable from the saved Study contract.

The Study `observed_state` is based on an identity-checked point-in-time process
observation. A persisted `status="running"` by itself does not confirm liveness.
For legacy records without a lifecycle pointer, the query reads only child IDs
already saved in the Study record; it does not search the Store directory to guess
which child is active. Legacy records do not gain synthesized process identities.

Study ETA is `Missing`: heterogeneous future jobs do not provide a comparable cost
basis for a trustworthy total estimate. A missing ETA or unsupported liveness is
not a successful measurement or completion signal. `--verify-artifacts` applies
the ordinary run checks to linked run artifacts; it does not fingerprint Study
inputs again.

## Candidate disposition and supersession

Candidate history uses append-only revision events outside the immutable numeric
result and evidence inventory. `status`, `analyze` and HTML views show the current
disposition and its event history. Every write requires the observed revision so
concurrent changes fail visibly rather than overwriting each other:

```bash
perflab annotate RUN_ID --candidate-state retained --reason "Reviewed candidate" --expect-revision 0 --json
perflab annotate RUN_ID --candidate-state withdrawn --reason "Material regression" --expect-revision 1 --json
perflab annotate OLD_RUN_ID --superseded-by NEW_RUN_ID --reason "Replaced after a new study" --expect-revision 2 --json
perflab annotate OLD_RUN_ID --clear-superseded --reason "Replacement link was entered in error" --expect-revision 3 --json
```

These commands require terminal runs. A replacement must be a later completed run
whose declared correctness command passed; a supersession link is operator context,
not a claim of comparability or acceleration. No run, source, binary, or evidence is
deleted, and candidate retention does not create an immutable baseline. Create a
baseline separately after making an evidence-based acceptance decision.

Each note stores its event action, reason, timestamp, revision and previous-event
SHA-256. Readers verify the sequential revision chain. Missing notes on legacy runs
mean `undecided`; malformed history or a broken hash chain is an error, not an
empty candidate state. This is an ordering/integrity chain, not a signed audit log:
without an external saved digest, a structurally valid edit to the last event
cannot be detected. Run `perflab schema run-status --json` and
`perflab schema study-status --json` for the query protocols.

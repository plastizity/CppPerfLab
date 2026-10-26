# Run and Study lifecycle reference

Read [`docs/run-status.md`](../../../docs/run-status.md) for commands and limits.
`perflab status RUN_ID --json` is the point-in-time process/build/candidate query;
`history` lists existing run records. A record can contain `status=running` after
its owner exited, so use the identity-checked `observed_state`. Old records have no
synthetic owner identity.

Set `measurement.progress=true` only when lifecycle writes are useful and the
experiment permits the unquantified interference. This sets no extra target runs.
Progress counts process launch groups, while adaptive internal calls and opaque
workloads stay unknown. `progress.eta` estimates only future full measurement stages
from that run's prior groups. The separate root `workflow_eta` estimates whole-run
remaining time from at least two eligible completed runs with the same cost key.
It uses monotonic elapsed time only while the identity-checked owner is alive and
the clock boot ID/time namespace match; UTC timestamps are not used for subtraction.
This works with progress disabled: `workflow_cost` stores the owner identity for
status checks, while progress-enabled runs reuse the lifecycle owner. It adds no
progress writes during target execution; the existing result writes occur at run
start, after fingerprinting and at completion. If `/proc` clock context is missing,
status retains the reason and cannot estimate elapsed time. If platform code or
schema files change during execution, duration is retained but evidence is `Invalid`
and the cost key is cleared.
Its historical median and min/max are descriptive observations, not a confidence
interval or a performance metric. If elapsed time reaches the median or exceeds
the observed range, the estimate is `Missing`, never zero. A changed request,
actual identity, tool/version/path/hash, static machine, or implementation source
does not reuse history. Unknown tool versions and old records without a timer/key
cannot contribute. Study ETA remains `Missing` because its future jobs are
heterogeneous. See [the protocol details](../../../docs/run-status.md).

For composed experiments use `perflab status STUDY_ID --kind study --json` and
`perflab analyze STUDY_ID --kind study --section lifecycle --json`. Study lifecycle
tracking is enabled only if any delegated request template opts into progress.
The Study persists each child link before target execution, so `active_path` follows
the saved assessment/paired/cases/run hierarchy. Flattened run records are
ID-deduplicated. Legacy records expose saved child membership only; stale `running`
state does not establish liveness and the Store is not scanned to infer an active
child. Study ETA remains `Missing` when heterogeneous remaining jobs lack a valid
cost basis. Do not interpret it as zero remaining work or as a failed query.

Use `annotate` for a human disposition, with its current `expected_revision`.
Candidate state and supersession are append-only context; they do not establish
correctness, comparability, non-regression, or performance improvement. An explicit
baseline remains a separate immutable operation after acceptance.

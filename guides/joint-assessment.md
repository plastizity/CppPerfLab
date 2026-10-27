# Joint performance assessment v1

`analysis: "assessment"` composes the existing paired and case Studies. It answers
one practical question in one report: does the same candidate improve the complete
execution while satisfying declared correctness, work and resource conditions?
It adds no collector, statistical estimator, executor or results database.

The manifest declares complete archived cases, reviewed baseline/candidate request
templates, explicit profiling configurations and metric-specific acceptance gates
**before execution**. Every case runs every configured profile. End-to-end timing
uses the existing serial random AB/BA or ABBA design. Resource profiles may use
ordinary repeated runs. Each comparison delegates to the same Comparison engine.

## Reproduce the standalone example

First follow [file cases](file-case-framework.md) to build only the small synthetic
adapter, export/capture its inputs and obtain a collection ID. Then:

```bash
python3.11 scripts/make_case_examples.py --collection COLLECTION_ID
bash scripts/install.sh
source scripts/env.sh
perflab schema study --json
perflab budget --kind study --config workloads/case-assessment.json --json
perflab study --config workloads/case-assessment.json --json
perflab analyze STUDY_ID --kind study --section overview --json
perflab analyze STUDY_ID --kind study --section summary --json
perflab report STUDY_ID --kind study --format html --output results/assessment.html --json
```

The generated example compares the **same program** on both sides; it validates
integration and must not be presented as an optimization. It declares one text
case, seven randomized timing blocks, seven heap repetitions per version and a
16-run budget (14 paired members + 2 heap runs). A run includes its own oracle,
preflight and declared warmups; the budget lists those costs. It cannot predict
opaque work inside an oracle, elapsed time, or background interference.

## Contract

The root requires `schema_version: 1`, `analysis: "assessment"`, `name`,
`collection_id`, distinct explicit `samples`, `requests: {baseline, candidate}`,
`profiles`, `seed`, `budget`, `constraints`, and `resource_conditions` with
`concurrent_runs: 1`, background-load declaration and notes. Optional
`allow_command_change` has the same strict treatment meaning as ordinary compare.
Request paths resolve relative to the manifest. Requests retain the same target,
input decoder, function repeat count, lifecycle and correctness contracts.

Each profile declares:

| Field | Meaning |
|---|---|
| `id` | Unique stable label in the report |
| `tool`, `options` | Explicit registered adapter and validated options |
| `warmups` | Process warmups for every member |
| `design` | `{kind: "paired", blocks, order}` or `{kind: "unpaired", repetitions}` |
| `comparisons` | Nonempty metric/scope, role, policy and gate definitions |
| `claim` | Optional per-profile claim override; an object declares a claim, `null` disables it for this profile |

Profiles override measurement tool/options/repetitions/warmups and, when
explicitly provided, `claim` on both templates. Omitting `claim` inherits the
request's claim; an object replaces it, and `null` clears only this profile's
claim. Correctness and `execution_state` remain in effect. Decide and record
each profile's claim before the experiment: for example, use a `time.wall` claim
on the native timing profile and explicit `null` on a heap profile that does not
collect that metric. If a claim is required for a profile, declare it there or
explicitly inherit the intended request claim. Clearing a claim is not evidence
that its conditions passed, nor an automatic relaxation of other gates.

Other controls and correctness commands stay explicit. Individually
scoped non-batch adapters are supported; Hyperfine and Google Benchmark batches
are rejected by this interface. Unsupported/denied tools cause a failed workflow
and no accepted joint assessment. There is no automatic fallback or skipped gate.
Discover capabilities using the existing `tools`/`plan` interface first.

Every comparison has distinct `id`, `role`, `metric`, optional exact
`metric_scope`, `policy` and `gate`. Roles are presentation semantics checked
against existing metric dimensions, not tool-specific knowledge:

| Role | Purpose |
|---|---|
| `end_to_end` | Exactly one paired `time.wall` outcome per case, with a required performance gate |
| `stage` | Local/function/model phase timing; cannot establish process speedup |
| `work` | Actual recorded operations, calls, iterations or other work semantics |
| `resource` | Heap, RSS, allocations or other resource cost |
| `numerical` | Numerical error/residual evidence with its existing validation contract |
| `diagnostic` | Informational execution or instruction-quality metrics, such as raw cache events |

`diagnostic` requires `gate: "informational"` and an `execution` or
`instruction_quality` metric dimension; the existing `resource` role is
unchanged. Estimated derived ratios remain per-Run diagnostic observations:
the existing Comparison acceptance requires Measured input and does not turn
them into a passing score. Native and instrumented profiles retain separate
samples and scopes. See [data movement diagnostics](data-movement.md).

The synthetic same-binary control Study `20261027T033745-1c304d8be760`
completed 8 Runs and 8 formal samples across independent native and counts
profiles. Its `phase.work` was present while `cpu.cache_misses_per_work` remained
Missing: phase work did not become a whole-process denominator. Evidence is in
`.perflab/validations/dme-20261027/joint-verification.json`. This two-block
control checks integration only. Although the recorded end-to-end decision says
`SUPPORTED_IMPROVEMENT`, baseline and candidate are the same binary, so the
finite-run observation is not evidence of a code optimization.

At least one work comparison is required. Counters describe only recorded work;
they cannot prove that all meaningful simulation work stayed constant. All input
cases remain mandatory; no weighted aggregate hides a serious regression. This
interface does not create suite weights or automatically remove an expensive case.

`gate` selects one **existing** Comparison decision:

- `informational`: display without adding a new performance requirement.
- `non_regression`: enforce the metric's declared regression bound.
- `performance_target`: enforce the explicit `min_speedup` objective; omission is
  rejected rather than interpreted as a default target.
- `statistics`: require the declared sample-count and variability screening.
- `correctness`: require the recorded hard correctness gate.

Correctness remains a hard requirement for every metric, including informational
ones. Merely passing an informational counter's oracle is not permission to use
Modeled/Estimated input as Measured evidence. All comparisons still enforce scope,
unit, tool mode, coverage, numerical constraints and evidence compatibility.
The example thresholds are editable example policies, not platform-wide defaults
for every workload or metric.

## Meaning of the combined conclusion

Joint acceptance is a conjunction of the declared required gates and correctness.
FAIL dominates; missing/inconclusive evidence cannot pass; unsupported evidence
is reported explicitly. The root `status: completed` means the workflow finished,
which is independent of acceptance PASS/FAIL/INCONCLUSIVE.

End-to-end improvement is separately SUPPORTED_IMPROVEMENT only when every case's
process-time decision supports improvement. A supported regression on any case
remains visible. A passing regression bound with no clear acceleration is a valid
outcome. A local phase's 30% improvement cannot establish an end-to-end benefit.
A memory gate can fail even while process timing improves. The report preserves
both results. `optimization_accepted` stays false: gates do not authorize a
production deployment or imply universal scientific correctness.

Recorded work medians appear alongside timing. If they differ, the report states
that no automatic single-step optimization attribution follows. Equal recorded
work still leaves unrecorded work unknown. No arbitrary time/work or cross-tool
ratio is fabricated; domain-valid per-operation metrics require their own declared
measurement contract.

All intervals and screening retain the existing Comparison limitations, including
descriptive bootstrap intervals and possible drift/dependence. A conjunction is
not a new confidence interval, multiple-comparison correction or causal proof.

## Identity, failure and evidence

For each case and variant, the parent pins declared input and artifact SHA-256
identities before profiling. It checks them before/after each child Study and
against every successful member's saved identity. Inputs are bound through the
same verified sample archive and expected hashes. A changed declared identity
cannot silently become the same candidate in another profiler.

These are **declared file identities**. Actual dynamically loaded libraries and
plugins use the separate, opt-in [runtime identity observer](runtime-identity.md);
matching declared file hashes do not prove their runtime identity. Profilers also retain their own overhead, sample scopes
and tool versions. Instrumented stage timings are never pooled with native times.

The parent stores all child Study IDs, source run IDs, stable case/profile labels,
comparison IDs, scheduled jobs, initial identities and exact profile request
files. Each raw tool output remains in its original run directory. JSON queries
and HTML use one shared view. Individual comparisons retain their own original
contexts; parent annotations associate them without rewriting stored evidence.

Any tool, target, input-identity or correctness failure stops the remaining jobs.
Earlier successful children remain inspectable, but the unfinished assessment
cannot pass or present a successful-subset score. No retries, sample deletion,
precision relaxation or hidden recompilation occurs. A failed child retains its
specific error category and original error under the parent profile context.

---
name: cpp-performance
description: C++ Algorithm Performance Engineering with CppPerfLab. Use for measured algorithm scaling, external C++ profiling, reviewed candidate comparison and optimization validation in this workspace. Do not use for unmeasured static reviews or automatic production-code changes.
---

# C++ Algorithm Performance Engineering

This Skill is original project guidance; third-party Skills are researched but not
copied or installed. Enter through root `AGENTS.md`. Read `README.md` for installation
and the authoritative CLI examples, then load only the references needed below.

## First establish semantics and scope

For first-time external project integration or experiment handoff, read
[project onboarding](references/onboarding.md) before creating configurations.

Record the algorithm's input domain, output meaning, numerical/geometry invariants,
precision, convergence requirements and a real performance objective. Identify the
actual public entry and compute sink. Do not replace that work with template advice.
Keep six candidate types distinct: complexity, data structure/algorithm replacement,
computation organization, parameter tuning, input-dependent selection and evolution.

Use an existing representative workload and independent oracle. If creating a test,
cover boundary/degenerate cases and several distributions. Correctness is required
before timing. Exact integer output can use an independent exact oracle; numerical
work needs explicit tolerances/convergence checks. Never weaken those for a score.

## Discover and plan

From this repository:

```bash
source scripts/env.sh
perflab doctor --json
perflab tools list --json
perflab schema request --json
perflab metrics --domain D01 --json
perflab metrics --metric cpu.ipc --json
perflab budget --config workloads/locality-baseline.json --json
perflab plan --config workloads/locality-baseline.json --json
```

Inspect `result.tools` / `result.capabilities.tools` for real probe results.
The registry gives metrics, prerequisites, overhead, mode and limitations. A missing
executable, denied event or parser failure is not a zero metric. Configure a specific
tool or `tool: auto` with required `goal.metrics`, an overhead budget and explicit
instrumentation permission. Read plan candidates/reasons before selecting a path.

For callable metric definitions, same-sample derivations and acceptance policies,
read [metric evidence and acceptance](references/metrics.md). It also covers D01–D05
call edges, heap allocation sites, cache models and Kernel work counters. The catalog describes
implemented public metrics, not full domain coverage. D13/D15/D16/D17 are deferred;
For D14 structured logs and independent per-solve hard gates, read
[solver validation](references/solvers.md). Legacy preflight-only runs do not
provide per-solve evidence.

## Measure, interpret, compare

For progressive evidence queries or human HTML reports, read
[result presentation](references/presentation.md).

```bash
perflab run --config workloads/locality-baseline.json --json
perflab analyze RUN_ID --json
perflab report RUN_ID --format json
perflab compare BASELINE_ID CANDIDATE_ID --json
```

For a smaller-is-better relative objective, read `normalized_cost` together with its
interval and acceptance status; see the metric reference. A cost alone is not acceptance.
Use returned IDs, not invented paths. Inspect `status`, `correctness`, `tool`,
`identity`, `binary`, raw `samples`, `summary`, `hotspots` and `evidence`.
Read the raw native report behind an important claim. A sampling percentage is
not exact elapsed time; heap snapshots are not RSS or allocation frequency.
The default analyze response is an overview; use --section facts, hypotheses,
metrics or hotspots for details, or --section all for the full shared view.
For uncertainty/metric semantics and errors read
[evidence interpretation](references/evidence.md) and `docs/protocol.md`.

Compilation, Python orchestration and parsing must remain outside the tool's target
timing. Hyperfine includes target startup. Pin only explicitly chosen CPUs and record
thread controls. Use the same workload/oracle and experiment controls for candidates.
Do not compare instrumented timings with native timings. `inconclusive` is a real
result; increase workload duration/repeats or reduce interference before concluding.

For scientific pipeline stage counters or single-machine strong scaling, read
[scientific experiments](references/scientific.md). These reuse Adapter/Study;
solver-specific evidence and validation use the separate solver reference above.

For ordinary-program phase logs, lightweight scopes, execution budgets and separate
acceptance/improvement conclusions, read
[production phase evidence](references/production-phases.md).

To import an existing project's text log using a deterministic format template or
field-keyword mapping, read [phase log mapping](references/phase-mapping.md).

For preregistered serial or explicitly competing concurrent AB/BA or ABBA
comparisons, read [paired studies](references/paired.md); ordinary compare remains
unpaired. Concurrent results describe a competing pair and do not estimate isolated
serial speedup.

For complete expensive/failed sample capture, file-driven C++ function/class
adapters and case matrices, read [file cases and replay](references/file-cases.md).
This path reuses existing metrics and run/compare APIs; serialized inputs and
independent validation remain consumer-defined.

For one preregistered workflow joining local/full-process timing, actual work
and resource gates, read [joint assessment](references/joint-assessment.md).
It verifies declared case/variant identity across profiles and keeps joint
acceptance separate from end-to-end acceleration evidence.

For actual executable/loaded-object identity, copied binaries and RPATH/dlopen
questions, read [runtime identity](references/runtime-identity.md). It distinguishes
declared file fingerprints from incomplete runtime observations and explicit gates.

For sampled on-CPU attribution by recorded phase/call/input and static sampling
readiness, read [CPU sampling interpretation](references/sampling.md). It covers
clock correlation, unresolved samples, period denominators and stack limitations.

For per-thread application work/wait timelines, read
[thread activity evidence](references/thread-activity.md). These are application
wall annotations and do not establish OS running/off-CPU time or wait cause.

When Linux perf switch records are available, use
[OS switch timelines](references/scheduler-timeline.md) for bounded per-thread
on/off-CPU intervals. These records do not show wakeups, runqueue delay or the
cause of an off-CPU interval.

For live run state, opt-in progress, build identity and candidate disposition,
read [run status and candidate history](references/run-status.md). Process liveness
is identity checked; reported stage ETA is limited to observed same-run measurement
pipelines. Separate whole-run ETA requires at least two completed matching workflow
histories and is explicitly Estimated; missing history or expired forecasts provide
no remaining-time value. Neither ETA substitutes for target performance metrics.

## Algorithm-scale and variant path

```bash
perflab schema study --json
perflab study --config workloads/algorithm-study.json --json
perflab history --json
```

Read [algorithm experiments](references/algorithms.md) before proposing complexity
or candidate-selection claims. A study delegates every evaluation to `lab.run`;
it varies the declared feature, records seed/order/budget, and retains each run.
Investigate source and known mature algorithms after observing a growth trend.
Search official papers/docs when selecting alternatives. State the hypothesis,
what work/traffic/allocations it removes, extra costs and acceptance conditions.

Only reviewed, independently built candidates are accepted. A trusted oracle must
receive each candidate and reject wrong output. The workspace/resource controls are
not a sandbox: do not submit untrusted LLM code. Mature tuning/evolution integrations
are deferred; no automatic optimizer is implied by a finite study matrix.

## Acceptance and feedback

Require correctness, per-size results and no unexplained regressions. Inspect compare
conditions before acknowledging argument changes. Use interleaved A/B reruns to
check drift. Separate observations, statistical inference, theoretical complexity
reasoning and untested hypotheses. Explain kernel vs full-process vs end-to-end scope.
Save accepted run IDs using `perflab baseline NAME RUN_ID --json`; name creation is
immutable. Send a concise evidence-backed proposal to the target project, rather
than silently changing its production code or algorithm dispatch.

## Capability gaps

No available tool: report probe diagnostics, then explicitly choose another metric
or collection strategy with its changed meaning. Never silently replace failed PMU
data, manufacture bandwidth or infer waiting from on-CPU samples. Consult
`docs/tool-selection.md` for assessed extensions rather
than installing overlapping tools or Skills. No global changes or remote scripts.

# Callable core metrics and acceptance

This implementation extends the existing Adapter → lab.run → Store → comparison →
views/CLI path. It does not implement all domains in the metrics specification.

## Interfaces

```bash
bash scripts/install.sh
source scripts/env.sh
perflab metrics --domain D01 --json
perflab metrics --metric cpu.ipc --json
perflab schema metrics --json
perflab schema evidence --json
perflab schema policy --json
perflab analyze RUN_ID --section observations --metric cpu.cpi --json
perflab analyze RUN_ID --section metrics --metric control.branch_mpki --json
perflab compare BASELINE_ID CANDIDATE_ID --metric time.wall --policy POLICY.json --json
```

`metrics` returns the implemented catalog, definitions, units, scope, aggregation
constraints and providers discovered from Adapter descriptors. It is not a tool
availability probe. Derived metrics require their native operands in the run
configuration; `plan` continues to select native adapter capabilities.
The Python API is `cppperflab.metrics.catalog(REGISTRY, domain, metric_id)`;
`convert(value, source, target)` checks dimensional unit compatibility.

## Evidence and derivations

New numeric samples retain v1 fields and add `evidence_version=1` and
`evidence_state`. `Measured`, `Estimated` and `Modeled` have finite numeric values.
`Unsupported`, `Missing` and `Invalid` have null values and reasons. Unavailable
observations are additive records, not fake zero-valued legacy metrics.

Existing known v1 adapter methods are interpreted on read; old files are not
rewritten. Unknown old methods are Invalid in the observation projection. Six-state
records, source operands and paths are queryable under `observations`; numeric
summaries isolate states. Current native adapters emit Measured. There is no new
estimation or simulation Adapter; tests verify those states are not promoted.

IPC, CPI, Branch MPKI and branch miss rate share one implementation. The old IPC
summary calls it rather than retaining another formula. Every pair must have matching
sample, scope, method, tool/version and event filters. Explicit counter running
coverage must be 99–100%; missing or invalid coverage does not mean 100%.
The 99% policy is a compatibility screening limit, not a PMU accuracy guarantee.
Raw counts and formulas remain available; model/estimate operands propagate their
state, while incompatible/absent operands produce Invalid/Missing.

Study retains its existing growth fit and adds adjacent growth ratios, exact
available N→2N ratios and source run IDs. Ratios/slopes of independently measured
medians are Estimated; fitted models are Modeled. Mixed metric semantics are rejected.
Neither result proves asymptotic complexity or a causal performance bottleneck.

## Policy and acceptance

A policy file requires `schema_version: 1`, `min_samples` (integer >=2), `max_cv`
and `max_regression_fraction` (nonnegative fractions). Optional `min_speedup` is
positive and `direction` is lower/higher. Set and retain these before collecting
candidate results. The validation driver provides one synthetic example, not global
threshold recommendations.

Correctness must pass, all requested samples must contain the metric exactly once,
and existing workload/tool/environment comparability rules remain mandatory.
`--allow-command-change` only acknowledges target argument differences; it does not
waive workload, evidence, correctness or scope checks. Non-Measured evidence cannot
receive a measured performance acceptance score.

Lower-direction benefit is baseline/candidate; higher-direction benefit is its
inverse. Maximum regression limits candidate relative increase for lower metrics
and relative decrease for higher metrics. A specified minimum benefit is another
gate. Samples/CV and the descriptive 95% unpaired bootstrap interval determine
PASS/FAIL/INCONCLUSIVE. Unsupported required evidence produces UNSUPPORTED. Metrics
without an objective direction remain INCONCLUSIVE unless a direction is declared.

A comparison still preserves legacy `speedup=baseline/candidate` and classification;
for non-time metrics that field is a value ratio, not runtime speedup. New
`acceptance`, `acceptance_policy`, `runtime_reduction` and ratio semantics distinguish
these meanings. Comparison inference is Estimated, with Measured input evidence.
Bootstrap remains 5000 resamples, seed 1014, 95%, unpaired; no paired-CI claim.
`regression_status` reflects the combined acceptance gate, not a separate statistic.

CLI success means a report was computed, not that its acceptance passed. Consumers
must inspect `acceptance.status`. Incomparable comparisons preserve existing error
exit semantics, are archived without a speedup, and include the acceptance reason.
PASS applies to the selected metric and declared oracle coverage, not an automatic
production approval or a multi-resource score. Use separate comparisons for resource
gates. No Suite aggregation, absolute numeric-tolerance engine or per-sample solver
validation is implemented in this increment.

## Reproducible validation

```bash
# Requires the existing independently built and reviewed examples; no implicit build.
ctest --test-dir build/profile --output-on-failure
python3.11 scripts/make_examples.py
python3.11 scripts/validate_core_metrics.py
PYTHONPATH=src python3.11 -m unittest discover -s tests
```

Run measurements serially without overlapping CPU-heavy tests. The driver invokes
the installed CLI and existing run/study/compare operations. It stores preregistered
policies, CLI envelopes, run/study/comparison IDs and a manifest beneath
`.perflab/validations`; original evidence stays beneath ordinary runs/probes.
It reuses algorithm, locality and allocation examples and builds/installs nothing.
Its manifest status describes workflow completion; individual acceptance results
can still be FAIL or INCONCLUSIVE. No fixed speedup is expected by the driver.

## Scope and next priorities

Verified entry path for Agents: AGENTS → existing cpp-performance Skill → metrics
reference → actual CLI. No model-specific service or global Skill registration was
added. D13/D15/D16/D17 remain deferred/out of scope.

Core registry/evidence/formulas/query/comparison are implemented. Next work is
workload-driven: D07 scaling treatment and D12 explicit region/counter ingestion,
then D14 per-execution output validation. DAG, scheduler, NUMA traffic, physical I/O,
Roofline, TMA and other advanced adapters are not implemented by this increment.

D01–D05 后续已实现 Callgrind/DHAT/Cachegrind、Kernel 显式计数和 secondary_metrics 多指标增长；接口及实测范围见 [domains-1-5.md](domains-1-5.md)。

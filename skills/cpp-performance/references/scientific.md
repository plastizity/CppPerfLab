# Scientific pipeline and parallel experiments

Use existing CLI discovery (`metrics --domain D12`, `metrics --domain D07`) and
`schema study`. Detailed contract and reproduction commands are in
`guides/scientific-performance.md`.

For an expensive pipeline, first query existing elapsed/work evidence. If the
reviewed target already exports Google Benchmark counters, configure a named
`stage` with plain seconds per iteration and explicit copied_bytes/traversals/
rebuilds/reuses counters. Confirm implementation units and timing boundaries before
trusting them. Inclusive stage time is not automatically removable cost, and logical
copy bytes are not physical bandwidth. No source integration is implicit permission
to modify a consuming business repository.

For single-machine parallel efficiency, use Study `analysis: strong_scaling` with
a worker argument axis including 1. Keep input, repeat count and numerical task
fixed; the oracle must cover the worker sweep. Read `overview.scaling` via analyze
or the stored Study result. Report speedup, efficiency, both intervals and nested
comparison acceptance. Do not interpret a worker sweep as algorithm complexity.
Insufficient/noisy samples remain INCONCLUSIVE; no threshold for "good efficiency"
is imposed without a workload-specific objective.

For structured solver history and per-solve validation, read [solvers](solvers.md).
Automatic stage reconstruction, lock-wait diagnosis and weak scaling are not
implemented by the pipeline/scaling interfaces. Preserve them as missing
evidence rather than inferring them from elapsed time or CPU counters.

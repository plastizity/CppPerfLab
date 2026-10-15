# Scientific workload analysis: callable first increment

This increment reuses the Google Benchmark adapter, ordinary runs and the Study
executor. It provides D12 stage/work counters and D07 single-machine strong scaling.
Solver-log ingestion is covered by [D14 validation](solver-validation.md). These
pipeline/scaling interfaces do not implement weak scaling, DAG reconstruction,
thread-wait attribution or business-specific stage discovery.

## Reproduce the standalone synthetic examples

Build explicitly; the platform never rebuilds targets during measurement:

```bash
cmake --build build/profile --target parallel_reduce pipeline_benchmark
ctest --test-dir build/profile --output-on-failure
python3.11 scripts/make_science_examples.py
bash scripts/install.sh
source scripts/env.sh
perflab metrics --domain D12 --json
perflab metrics --domain D07 --json
perflab plan --config workloads/science-pipeline.json --json
perflab run --config workloads/science-pipeline.json --json
perflab study --config workloads/science-strong-scaling.json --json
perflab analyze RUN_ID --section metrics --json
perflab analyze STUDY_ID --kind study --section overview --json
```

The pipeline target requires the existing Google Benchmark CMake integration.
For a fresh standalone build, configure `examples/cpp` with
`-DPERFLAB_GOOGLE_BENCHMARK_ROOT=/absolute/path/to/benchmark/install` first.
The generator describes the repository's profile build; adapt build metadata when
using another build. Generated workload files contain machine-local absolute paths.

## Stage clock and work contract

Google Benchmark measurement options support:

```json
{
  "filter": "^Pipeline/1048576/real_time$",
  "counters": {
    "copied_bytes": "copied_bytes",
    "traversals": "traversals",
    "operations": "operations"
  },
  "stage": {"name": "copy", "seconds_counter": "copy_seconds"}
}
```

`stage` selects one named inclusive wall-time region within each timed iteration.
The target must export average seconds per iteration as a plain custom counter;
rate and total flags are not accepted by this semantic contract. Values must be
finite, nonnegative and no larger than enclosing iteration wall time. Counter
scaling is an explicit target contract; JSON alone cannot prove its implementation.

- `pipeline.stage_wall`: inclusive seconds per iteration, Measured.
- `pipeline.stage_fraction`: stage seconds / enclosing wall seconds, paired
  measurement-derived ratio. It is not a causal estimate of removable overhead.
- `memory.copied_bytes`: target-counted logical copied bytes per iteration, not
  measured DRAM traffic. Count a copied payload once, not once for read and write.
- `pipeline.full_traversals`, `pipeline.rebuilds`, `pipeline.reuses`: explicit
  per-iteration counts with workload-defined entity/reuse semantics.

Clock overhead and the instrumented code are included. Stages can overlap; never
sum their inclusive fractions as a time breakdown. Use separate filtered cases or
runs for different stage declarations; they are not automatically correlated into
a reconstructed timeline. A different stage name changes measurement scope and
prevents direct comparison. Keep entity definitions and counter semantics stable.
Zero counts remain valid observations; the existing positive-ratio comparison
rejects zero denominators and does not manufacture an infinite speedup.

## Strong scaling contract

Study adds optional `analysis: "strong_scaling"`; absent means existing growth
analysis, preserving old studies. `axis` addresses an integer worker-count command
argument and must include 1. Primary metric must be elapsed wall time. Input size,
repeat count, precision and numerical task remain fixed. The independent oracle
must cover every supported worker count; the synthetic target additionally checks
every timed invocation's result.

For each variant, Study reuses existing comparison correctness, evidence, sample,
CV and bootstrap mechanisms against its one-worker run. Only the declared worker
parameter and its command argument may differ. Other workload parameters,
arguments, artifact fingerprints, environment and measurement controls must match.
Changing worker environment variables is not supported by this first increment.

`scaling[variant].points` records requested workers, source comparison/raw run IDs,
speedup `median T(1) / median T(p)`, efficiency `speedup / p`, and both descriptive
95% intervals. Inference is Estimated from Measured inputs. `measurement_status`
screens sample count and CV; it does not certify a useful speedup or numerical
coverage. Inspect the nested comparison acceptance for performance regressions.
The one-worker identity reference is exactly 1 with interval [1, 1], not a new
independent measurement. Other intervals are descriptive and unpaired; order, startup, frequency and background
load remain limitations. Requested workers do not prove independently observed
thread utilization. No algorithm complexity fit is performed on worker counts.

These are general infrastructure interfaces. Solver assembly/setup/solve phases,
Mesh entities and EDA/TCAD semantics must be declared by a consuming project, never
hard-coded into the platform. D14 per-solve residual/convergence ingestion and hard numerical gates are now
provided by the [separate solver interface](solver-validation.md); full external
Time-to-Validated-Solution remains unimplemented.

# D14: per-solve numerical evidence and acceptance

This increment reuses ordinary runs, nonbatch adapters, the execution boundary,
metric registry, comparison and evidence storage. It adds structured solve logs and
an independent validation command after every measured solve. Existing requests
without the optional contract retain their previous preflight-only semantics.

## Discover and reproduce

```bash
cmake --build build/profile --target solver_jacobi solver_gs
ctest --test-dir build/profile --output-on-failure
python3.11 scripts/make_science_examples.py
bash scripts/install.sh
source scripts/env.sh
perflab metrics --domain D14 --json
perflab schema solver --json
perflab schema solver-validation --json
perflab plan --config workloads/science-solver-jacobi.json --json
perflab run --config workloads/science-solver-jacobi.json --json
perflab run --config workloads/science-solver-gs.json --json
perflab compare BASELINE CANDIDATE --metric solver.total_time --json
perflab analyze RUN_ID --section facts --json
```

The synthetic SPD tridiagonal example uses identical input, tolerance and iteration
budget for Jacobi and Gauss–Seidel. Its independent Python validator computes A*x-b
and L2 error against the known solution; it never trusts the target's reported final
residual. These matrix and reference assumptions live exclusively in examples.
A smaller iteration count does not by itself imply lower total time.

## Request contract

Under `correctness`, keep the existing preflight `argv` and `description`, and add:

```json
{
  "sample_validation": {
    "argv": ["python3.11", "/reviewed/oracle.py", "{sample_dir}/solve.solver.json", "65536"],
    "report": "solve.solver.json",
    "residual_norm": "l2_absolute",
    "max_residual": 1e-8,
    "max_absolute_error": 1e-7,
    "max_relative_error": 1e-9,
    "max_iterations": 100
  }
}
```

The numbers above belong to the synthetic example, not platform defaults. All
thresholds are required, finite and explicit. Supported residual definitions are
`l2_absolute = ||Ax-b||₂` and `l2_relative = ||Ax-b||₂ / ||b||₂`.
Absolute error is `||x-x_ref||₂`; relative error is that norm divided by
`||x_ref||₂`. A zero denominator needs a workload-specific explicit definition in
the trusted validator; the platform does not invent one or substitute a norm.

The target writes its report using an argument such as
`{run_dir}/solve.solver.json`. The validator reads that exact measured execution's
report via `{sample_dir}` and writes **only the validation JSON** to stdout.
Its nonzero exit rejects correctness; timeout/parser failures remain explicit errors.
Declare oracle scripts and numerical reference/input files in existing artifact
and input lists so their hashes are retained and checked. The validator executable
is fingerprinted automatically. Commands execute with existing target environment,
affinity, timeout and resource limits; no shell or new execution engine is used.

A report filename must end in `.solver.json` and stay directly in the sample folder,
separate from adapter outputs. Batch tools (Hyperfine/Google Benchmark) are excluded
because they do not expose one distinct target output per measurement sample.
The planner reports this restriction rather than validating a final batch output as
if it covered every solve. Nonbatch tools still need actual successful probes;
solver capability declarations do not grant PMU permissions.

## Structured reports

`schema solver` defines the target report: version, problem identity, termination
reason (`converged`, `max_iterations`, `breakdown`), completed iterations, declared
residual norm, initial-plus-every-iteration residual history, solution vector and
setup/solve/total seconds. History need not decrease monotonically. Target history
is reported instrumentation; only the final residual is independently recomputed.

`schema solver-validation` defines validator stdout: version, problem identity,
residual norm, independently recomputed residual, absolute error and relative error.
Norm and problem identity must match the declared workload. Malformed JSON, NaN,
invalid dimensions/types, incomplete history and inconsistent intervals are rejected.
Nonoverlapping setup and solve durations must fit in total duration; only the
floating representation roundoff bound (half an ULP for each converted duration
and their sum) is admitted. Original durations are never repaired or replaced.

## Metrics, gates and timing scope

| Metric | Unit | Source |
|---|---|---|
| `solver.setup_time` | seconds | target setup interval |
| `solver.solve_time` | seconds | target solve interval |
| `solver.total_time` | seconds | target enclosing setup/solve interval |
| `solver.iterations` | count | target completed iterations |
| `numerical.residual` | problem units or ratio | independent validator, declared norm |
| `numerical.absolute_error` | problem units | independent validator |
| `numerical.relative_error` | ratio | independent validator |

These are Measured observations with original log references. A reported failed
solve can still have measured diagnostic values; it cannot receive a valid
performance cost. Metrics are not added together into a score.

Each measured solve must terminate as converged, respect the iteration budget and
satisfy all three numeric bounds. `solver_samples` retains residual history,
termination, verified final metrics and PASS/FAIL. Invalid logs have Invalid
evidence and INCONCLUSIVE sample status; the run fails. A numeric gate failure
sets run correctness to failed and stops later measurements. Raw logs, validation
stdout, execution records and evidence hashes survive both paths.

Comparison requires passed per-solve status, sample coverage, identical
validation contracts and matching fingerprinted validator argv artifacts, in addition to all existing correctness/comparability/
measured-evidence/statistical checks. This also applies to elapsed-time comparisons
of runs declaring this contract. Legacy preflight-only runs cannot acquire D14
scoring by naming a metric. Zero iterations/errors are valid measurements, but
existing ratio comparison requires positive samples and does not fabricate an
infinite speedup for a zero denominator.

`solver.total_time` excludes external independent validation, platform orchestration
and target serialization after its recorded total interval. Native `time.wall`
includes target startup/output with its adapter-specific scope, also excludes the
external validator. Neither is advertised as complete Time-to-Validated-Solution.
Comparisons of internal time must also inspect native end-to-end scope, preparation
costs and memory rather than accepting an isolated fast solve.

## Remaining scope

This is a generic explicit-report interface, not automatic PETSc/Trilinos/vendor
log recognition. Preconditioner statistics, multiple solves per process, nonlinear
inner/outer iterations, relative-denominator metadata, convergence-failure taxonomy,
peak solver memory and full externally validated end-to-end timing remain future
work. Full solution vectors are appropriate for the synthetic example; specialized
large-scale evidence formats need a separately specified contract. No precision,
stopping condition or business solver is changed by the platform.

# Per-solve numerical validation

Use `metrics --domain D14`, `schema solver`, `schema solver-validation` and the
contract/reproduction steps in `docs/solver-validation.md`.

Require an explicit fixed numerical task, norm, residual/error bounds and iteration
budget before measuring. Configure `correctness.sample_validation` only for a
reviewed target that emits one structured log per nonbatch measured execution.
The independent validator must actually recompute residual/error using authoritative
input/reference data, not repeat the target's convergence claim. Include scripts
and data in existing fingerprint lists. Batch final output does not validate every
iteration or sample; the planner rejects that measurement strategy.

Read `solver_samples` through `analyze --section facts`, inspect termination and
history, and follow both solver and validator raw evidence references. Compare
`solver.total_time` only when every sample passes; inspect setup, solve, iterations,
native elapsed and memory separately. A numerical gate failure must block scoring,
even if the target says converged or finishes sooner. INCONCLUSIVE from statistical
noise is not a verified speedup.

Target solver clocks exclude external validation and report serialization; do not
call them complete Time-to-Validated-Solution. Norm definitions and thresholds must
match between baseline and candidate. Never alter precision or stopping rules to
obtain a smaller cost. Existing preflight-only runs retain limited coverage and
cannot be promoted to per-solve validation.

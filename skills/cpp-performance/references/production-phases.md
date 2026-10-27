# Production phase evidence and instrumentation

Before an expensive run, use `budget --config ... --json`. This performs no probe
or target execution. Inspect oracle/preflight/warmup/measurement/validator counts;
unknown internal oracle work or adaptive iterations are not zero. `plan` still probes.

For ordinary programs, read `guides/phase-observation.md` and `schema phases`.
Use measurement.phase_report only when the reviewed target writes one report per
nonbatch execution. Preserve original logs and obtain the business-format mapping
from the consuming project; do not infer a proprietary log grammar or embed names.

Inspect `analyze --section phases`, input/call IDs, result classification and work
units/states. Incomplete child coverage means Missing self time. Inclusive overlaps
and parent/child counters cannot be summed into end-to-end elapsed. The optional
C++ header is per-thread, bounded and explicit; register data before timed scopes.
Calibrate overhead with the example and never subtract it mechanically from results.

For local comparison select exact metric scope, then inspect decisions separately:
correctness, comparability, statistics, non-regression, configured target and
improvement. PASS acceptance can coexist with NOT_ESTABLISHED acceleration. Timing
or declared correctness does not prove unobserved outputs or numerical stability.
Serial paired/block execution is covered by [paired studies](paired.md).
For complete input retention and isolated C++ adapters read [file cases](file-cases.md).
For a single cross-profiler result with explicit resource gates, read
[joint assessment](joint-assessment.md). The complete feedback worklist is in
docs/history/feedback-implementation.md.

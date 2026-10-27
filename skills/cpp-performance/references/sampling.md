# CPU sampling interpretation

Use this reference when `perf-record` CPU-clock samples need to be joined to
structured program phases or when sample interpretability must be gated before
expensive measurements.

## Configure

The optional `perf-record` fields are `sample_trace`, `call_graph` (`dwarf`, `fp`,
`none`), and `symbolizer` (`perf`, `addr2line`). Defaults are `false`, `dwarf`, and
`perf`. Selecting `addr2line` requires the actual readelf/addr2line helpers and
retains their version and raw evidence; the platform does not silently switch
symbolizers. For a profiled preflight, configure all explicit gates:

```json
"options": {
  "frequency": 199,
  "call_graph": "fp",
  "sample_trace": true,
  "symbolizer": "addr2line",
  "precheck": {
    "min_samples": 20,
    "min_resolved_self_fraction": 0.8,
    "min_resolved_stack_fraction": 0.5,
    "require_phase_clock": true
  }
}
```

Static tool readiness checks prerequisites and target ELF metadata without running
the target. When configured, the sampling precheck profiles the one preflight that
was already part of the execution plan; it does not add a launch. Failure stops
before warmups and formal measurements. Passing means only that the preflight met
the declared sample/interpretability gates. It does not prove later-run quality,
correctness, speedup, or a complete stack. Two resolved leading frames in `fp`
mode are partial-stack evidence only.

## Inspect

```bash
perflab profile RUN_ID --measurement 0 --format json
perflab profile RUN_ID --measurement 0 --phase-path '["model","kernel_a"]' --format json
perflab profile RUN_ID --measurement 0 --phase-id PHASE_ID --phase-mode self --format html --output /tmp/profile.html
perflab profile RUN_ID --stage preflight --phase-path '["model"]' --format json
```

Formal queries require `--measurement`; preflight queries use `--stage preflight`
and omit it. Filters include exact phase path (JSON string array), phase ID, call
ID, input ID, PID and TID. `self` subtracts the union of immediate children and
requires complete child coverage. A phase join requires matching explicit
`CLOCK_MONOTONIC`, boot ID, PID namespace, time namespace and zero monotonic offset.
Relative steady-clock origins and namespace conversions are never inferred.

## Interpret

Every percentage uses recorded sample period, not wall time or call count. The
query reports both selected and whole-profile sample counts/period sums, the
selected fraction, and unresolved self share. `[unknown]` samples stay in their
denominator. No selected samples yields `INCONCLUSIVE`/`Missing`, not zero work.
Self share uses the sampled leaf; observed-chain inclusive share counts each unique
symbol/DSO once per sample. It does not reconstruct absent frames. CPU-clock
sampling shows on-CPU execution and does not establish off-CPU waits, locks or
scheduler delays. `Measured` applies to the captured sample; offline symbol/source
attribution is `Estimated` and retains the ELF/helper evidence used.

For the complete field definitions, output evidence and limitations, read
[`guides/sampling-interpretability.md`](../../../guides/sampling-interpretability.md).

The optional `switch_events: true` perf-record option additionally records real
per-task context-switch boundaries in the same target launch. It requires
`sample_trace: true`; selection, clocks, censored intervals and CLI queries are
documented in [`scheduler-timeline.md`](../../../guides/scheduler-timeline.md).

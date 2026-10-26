# CPU sampling interpretability

CppPerfLab can retain Linux `perf record` CPU-clock samples, attribute sampled
instruction addresses, and query those samples within explicitly timed phases.
This is an on-CPU observation path. It does not measure lock waits, scheduler delay,
exact function duration, or operation counts.

## Readiness and collection

Before choosing sampling, inspect the actual tool probe and plan. Optional static
readiness examines the selected call graph mode, the target ELF's debug/symbol
sections, native perf support, and explicitly selected symbolization helpers. It
does not execute the target. Static readiness cannot prove that a real run will
produce enough interpretable samples.

`perf-record` accepts these sampling options:

```json
{
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

`call_graph` is `dwarf`, `fp`, or `none`; the default is `dwarf`. `symbolizer` is
`perf` or `addr2line`; the default is `perf`. There is no implicit symbolizer
fallback. The `addr2line` path retains helper identity and output, sampled ELF
identity, and address evidence. Resolved source attribution is marked `Estimated`
because it is derived from sampled addresses and offline ELF information; the
sample event itself remains `Measured`.

With `precheck`, the platform profiles the already planned preflight launch and
uses its trace to test the configured sample and interpretability gates. It replaces
that one existing preflight; it does not add a second target execution. A failed
precheck stops before warmups and formal measurements. It does not certify the
correctness of later samples, a speedup, or a complete call stack. The `fp` mode's
two-resolved-frame gate checks only the first two observed frames; it does not prove
that unwinding is complete or correct.

## Clock correlation

Samples use Linux `CLOCK_MONOTONIC` with perf's clock ID explicitly selected and
verified in the recorded event attributes. Phase events can be joined to samples
only when their clock name, boot ID, PID namespace, and time namespace match
explicitly. The namespaces must have zero monotonic offset; a relative C++ steady
clock is not treated as having a known epoch. If those facts are missing or differ,
phase filtering is `UNSUPPORTED` instead of guessing a time conversion.

Linux time namespace offsets are relative to the initial namespace, as described
in the [Linux time namespace manual](https://kernel.googlesource.com/pub/scm/docs/man-pages/man-pages/+/refs/tags/man-pages-6.13/man/man7/time_namespaces.7).
This implementation records `/proc/self/timens_offsets`, supports only an explicit
zero monotonic offset, and checks every sample against the capture's monotonic
start/end bounds. Missing or denied clock metadata is unavailable, with no inferred
epoch or correction.

The clock metadata identifies the observed process/thread. A query only attributes
samples to OS threads explicitly recorded by the phase producer. It does not infer
that uninstrumented worker threads belong to a phase.

## Querying

Run a structured profile query for a formal measurement:

```bash
perflab profile RUN_ID --measurement 0 --format json
perflab profile RUN_ID --measurement 0 --phase-path '["model","kernel_a"]' --format json
perflab profile RUN_ID --measurement 0 --phase-id PHASE_ID --phase-mode self --format html --output /tmp/profile.html
```

Use `--stage preflight` without `--measurement` to query the profiled preflight:

```bash
perflab profile RUN_ID --stage preflight --phase-path '["model"]' --format json
```

Filters can include phase ID, exact JSON-array phase path, call ID, input ID, PID,
and TID. Phase windows are half-open. `inclusive` includes child intervals;
`self` subtracts the union of declared immediate child intervals and is available
only when child coverage was declared complete. If no matching samples occur,
selection is inconclusive/missing, not zero CPU work.

A timing scope does not prevent the compiler from moving, reusing, or removing
pure computation. Microbenchmarks must explicitly ensure that the measured call
and its result remain inside the intended scope; the synthetic `phase_cpu`
example uses opaque input/result barriers for this reason. Samples outside a
recorded phase stay outside it: the query never redistributes them into a phase.

## Denominators and evidence limits

Hotspot shares are weighted by the sum of recorded `cpu-clock` sample periods for
the selected launch group. Reports retain whole-profile and selected sample counts
and period sums, the selected fraction, and unresolved selected self share. These
denominators are not wall-clock time, exact function time, or number of calls.
Unresolved samples remain in the denominator under `[unknown]`; they are not
silently discarded. Recorded loss makes the precheck sample gate inconclusive.

For each sample, self attribution uses its leaf frame. Observed-chain inclusive
attribution counts each unique `(symbol, DSO)` once per sample, so recursion does
not multiply the same sample's period. It is an observed call-chain view, not a
reconstruction of missing callers. `complete_callchain_proven` remains false.
CPU-clock samples only show where the process was executing on CPU. They cannot
establish off-CPU waiting, lock contention, or causal optimization cost.

Evidence states distinguish captured sample events (`Measured`), derived offline
symbol attribution (`Estimated`), unavailable mechanisms (`Unsupported`), absent
data (`Missing`), malformed or identity-mismatched evidence (`Invalid`), and
sampling gate conclusions (`PASS`, `FAIL`, or `INCONCLUSIVE` as appropriate).

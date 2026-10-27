# D01–D05 implemented analysis paths

This increment reuses run, study, comparison and evidence storage. It adds Callgrind,
DHAT and Cachegrind adapters and explicit Google Benchmark per-iteration work counters.
No business project, global configuration or external compiler workflow is modified.
Standalone synthetic examples are explicitly built for verification.

| Domain | Callable addition | Evidence semantics | Remaining limits |
|---|---|---|---|
| D01 | control.call_count, call_edges, callgrind.instructions | Instrumented machine calls and guest instructions | No exact native hot-path duration or native indirect-branch event adapter |
| D02 | secondary_metrics, secondary_growth, structured fit quality | Time/heap/operation curves share run IDs; ratios Estimated, fits Modeled | Single axis; no multidimensional regression or Big-O proof |
| D03 | useful FLOPs, logical bytes, operations, work rates | Target-declared useful work and same-repetition timing | No retired FP/SIMD coverage or hardware Roofline |
| D04 | allocated blocks/bytes, simultaneous live peak, allocation_sites | DHAT tracked heap v2; explicit operation_count normalization | Not RSS, allocator fragmentation or native allocation time |
| D05 | modeled cache/branch events plus native generic perf events | Separate Cachegrind IDs and Modeled state | No TLB, vendor-specific LLC attribution or physical bandwidth |

## Executable interfaces

```bash
bash scripts/install.sh
source scripts/env.sh
perflab metrics --domain D01 --json
perflab tools probe --tool callgrind --json
perflab tools probe --tool dhat --json
perflab tools probe --tool cachegrind --json
perflab analyze RUN_ID --section call_edges --symbol compute --json
perflab analyze RUN_ID --section allocation_sites --symbol main --json
perflab analyze RUN_ID --section metrics --metric kernel.useful_flop_rate --json
perflab analyze STUDY_ID --kind study --section growth --metric memory.allocations --json
```

No new scheduler or measurement database is introduced. Adapter metrics declared in
options are checked by plan through configured_metrics; selecting an optional
per-operation metric without its input does not advertise it as collectable.

## Tool contracts

Callgrind supports instruction-only, single-part format v1. Call-edge counts include
runtime and omit inlined source calls; guest Ir is not the native CPU PMU event.
Call edges retain caller/callee, repetition and raw evidence references. Original
Callgrind files retain object/file/context details beyond the normalized names.

DHAT accepts JSON v2 heap lifetime records. Cumulative tb/tbk are not simultaneous
memory. Global peak uses the sum of gb at the same t-gmax; per-site mb maxima are
not summed. Counts follow DHAT allocated-block/realloc semantics, not every possible
allocator API call. operation_count must be explicitly supplied and its whole-target
meaning recorded in workload metadata. Runtime allocations are included.

Cachegrind always collects cache and branch simulation. I1/D1/LL geometry may be
supplied as `[size_bytes, associativity, line_bytes]`; raw cache descriptors remain
part of evidence. Separate cachegrind.* metrics use Modeled, including model miss
rates and LL-data MPKI. Zero reference counts produce Missing ratios, not zero.
The measured comparison engine rejects them. Generic perf cache events are not
renamed to a particular cache level and cannot be converted to DRAM bytes.

Google Benchmark `options.counters` optionally maps useful_flops, logical_bytes,
operations to target JSON fields. Fields must be plain per-iteration counts; target
rate flags, totals or per-thread averages are outside this contract. Missing or
invalid requested counters fail parsing. Rates use the same repetition's iteration
wall time. Useful FLOPs are declared algorithm work, not hardware FP events. Logical
arithmetic intensity is a traffic model and is labeled Modeled.

## Extended algorithm output

Study v1 optionally accepts secondary_metrics, unique IDs excluding the primary.
Every requested series requires Measured samples with consistent unit/method/scope
and full hardware coverage where relevant. All metrics use the original evaluation
matrix; no extra jobs are created for secondary quantities.

Primary growth/comparisons retain their v1 meaning; additional fits live in
secondary_growth. Growth queries and HTML group by metric/unit/method/scope. Output
includes points, sample statistics, N2/N1 and available N→2N ratios, local log slopes,
nonnegative fitted expressions, residual errors, leave-one-out errors and model ranks.
Structured quality records per-point sample/CV screening and its policy. A PASS there
only passes measurement screening and does not establish model correctness.

## Reproduce actual acceptance

```bash
# Explicit build of the standalone synthetic examples; benchmark is already local.
cmake --preset profile
cmake --build --preset profile -j 2
ctest --test-dir build/profile --output-on-failure
python3.11 scripts/make_examples.py
python3.11 scripts/validate_domains_1_5.py
PYTHONPATH=src python3.11 -m unittest discover -s tests
```

The example driver requires prebuilt targets and the installed CLI; it builds and
installs nothing. It writes policies before candidate collection, runs serially,
uses the existing API, retains every result including FAIL/INCONCLUSIVE, and stores
its manifest/envelopes in `.perflab/validations`. Do not overlap CPU-heavy checks
with its measurements. Example thresholds do not enter production policy.

The dot-product sample uses exactly representable dyadic inputs and an independent
integer reference at ten sizes. Both algorithms satisfy the same exact numeric
contract. Useful work counts use a declared 2N dot-product convention; code reordering
and compiler behavior do not imply those are retired hardware operations.
The allocation sample compares separate blocks and contiguous storage, preserving
data, repeats, lifetime and exact checksum. Native timing is collected separately
from DHAT; layout timing is collected separately from Cachegrind.

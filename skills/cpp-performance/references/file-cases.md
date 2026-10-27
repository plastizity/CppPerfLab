# Complete input capture and isolated C++ cases

Use this path when the user wants to isolate a reviewed function/class or retain
expensive/failed complete inputs. This is distinct from observing an existing
production program, and shares its phase evidence and platform run/compare APIs.

1. Read `guides/sample-capture.md` and `guides/file-case-framework.md`. Query
   `perflab schema sample-manifest --json` and `perflab schema study --json`.
2. Have the consumer define the input/expected format versions, completeness,
   attachments and independent validation. Do not serialize unknown business
   objects, guess omitted state, or equate a result classification with correctness.
3. Declare selection, seed, result filters and payload-byte budget before capture.
   Use `samples budget`, then `samples capture`. Preserve full population metadata.
   Longest selection requires actual Measured durations, not estimates or missing=0.
4. Link source run/stage/sample/event when available. Check raw source evidence.
   Preflight diagnostics are not measured benchmark repetitions. Capture copies
   phase evidence; payload-to-event correspondence remains exporter asserted.
5. Use an already reviewed/built executable. A tiny C++17 adapter can implement
   load/prepare/reset/invoke/validate with `include/cppperflab/case.hpp`. Choose
   `per_call` or `reuse_reset`, explicit repeats and independent correctness.
   Never silently rebuild a consuming project or execute generated/unreviewed code.
6. Preview `replay ... --mode budget`. Discover/plan the real adapter; whole-process
   profiler metrics remain whole-process. Use phase metrics for named wall regions.
7. `replay COLLECTION --sample KEY --config TEMPLATE` delegates to normal run.
   Missing references remain explicit; failed validation stops and retains evidence.
   `replay ... --mode prepare --output workloads/bound.json` exports a normal
   request with pinned input hashes. It is usable by ordinary run and paired Study.
8. For an explicit case × variant matrix use `analysis: cases` Study, preview
   `budget --kind study`, then execute serially. The full matrix must fit the
   declared run budget; failure stops without a successful-subset score.
9. Case matrix comparisons are unpaired. For random AB/BA or ABBA use prepared
   requests with the existing paired workflow, one repetition per member. Inspect
   every case, metric scope, correctness, workload and variability before accepting.

Useful queries:

```bash
perflab samples list COLLECTION --result CLASSIFICATION --sort elapsed_desc --json
perflab samples verify COLLECTION --sample KEY --json
perflab analyze STUDY --kind study --section phases --input-id KEY --json
perflab report STUDY --kind study --format html --output results/cases.html --json
```

Captured production timing and isolated replay are different scopes; do not turn
their ratio into speedup. Preserve stage/whole-process/work/resource findings as
separate conclusions. A selected diagnostic population is not a weighted suite.
Hashing can warm input caches. Archived input is data, not sandboxed code; trusted
adapters still execute with user permissions. Agent entry is AGENTS → local Skill,
not claimed automatic registration in an external UI/catalog.

For a single assessment across native process/stage/work timing and a separate
memory profiler, read [joint assessment](joint-assessment.md). Declare its gates
before execution rather than manually combining favorable comparisons afterward.

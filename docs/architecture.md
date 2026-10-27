# Architecture and implementation gates

## Scope

The platform orchestrates trusted external tools and reviewed prebuilt candidates.
Algorithm studies extend the same run/measurement/storage contracts rather than
creating another benchmark scheduler or experimental database.

## Dependency DAG and gates

1. `protocol`, `errors`, `execution`, `storage`: strict request boundary, timeout and
   process-group cleanup, durable failure evidence. Gate: contract/lifecycle tests.
2. `adapters`: describe, validate, prepare, probe, parse; `lab`: orchestration and
   provenance. Gate: actual collection and parsing by complementary tools.
3. `comparison`, `cli`, JSON schemas: semantic comparability, statistical limits,
   stable errors and query. Gate: real A/B and intentionally incomparable runs.
4. Independent C++ fixtures + Skill: native workload, oracle, external build path.
   Gate: installed CLI, source/sanitizer tests and Agent discovery/use.
5. `studies`: constrained variant/scale matrices, budget and empirical growth.
   Gate: multiple scales, two algorithms, independent oracle, failed candidate.
6. Mature tuning/evolution and tracing adapters: deferred until dependencies and
   isolation can be verified. No planned capability is registered as executable.

## Ownership and responsibilities

`lab.run` owns one experiment and its lifecycle. `execution.execute` owns one POSIX
process group and open logs, always reaping the parent and terminating its group.
Adapters own command preparation and tool-native parsing; they do not create
optimization decisions. The registry is a set of descriptors and small explicit
adapter operations, with no plugin loading or abstract inheritance hierarchy.
`comparison` owns statistical interpretation. `studies` indexes existing run IDs.
`cli` handles presentation only. JSON files are the authoritative storage; scanning
local result files is sufficient for the current scale, so SQLite is unnecessary.

`views` creates a versioned analysis projection of run/comparison/study records.
Agent queries select sections from that projection; `html_report` renders the same
values, without a second statistics pipeline. Failed comparisons are archived as
explicit failures. `tool_config` scopes project-local path configuration to one
operation; installed paths still require actual probes. See `../guides/analysis-views.md`.

## Decisions

- Python 3.11 standard library: readable orchestration, safe argv and JSON without
  numerical/package dependency sprawl. C++ appears only in independent fixtures.
  Offline zipapp provides project-local installation without package downloads.
- External mature profilers: no custom sampling, allocator interposition or timer
  kernel. Hyperfine owns repetition/timing; perf owns event counting/sampling;
  Valgrind owns heap instrumentation. Separate native formats remain archived.
- Versioned JSON + strict request fields: reject unsupported versions rather than
  guessing migrations. Result readers ignore additive descriptive fields. New
  measurement meanings require distinct names/method/scope, not reinterpretation.
- Capability-driven planning: metric coverage, actual probe and declared overhead
  budgets rank feasible tools. Registry ordering breaks ties transparently. Failed
  collection never triggers a hidden tool substitution.
- Correctness before timing and identity check after timing: explicit target-provided
  or independent oracle, binary/input digests, ELF notes and declared build metadata.
  SHA-256 and Build ID identify artifacts; they do not prove source provenance.
- Statistical gates are conservative policy, not scientific certainty. Store every
  sample. Randomized study order reduces confounding but does not remove drift.
- Native process groups/resource limits are operational control, not hostile-code
  isolation. Generated-code execution and automatic evolution are refused by scope;
  a future backend needs verified filesystem/network/process isolation first.

## Architectural lessons from upstream

Caliper separates collection, triggering, processing and output; CppPerfLab keeps
tool operations and metric provenance separate without recreating Caliper services.
Thicket motivates retaining experimental dimensions and run identity before
aggregation. Perfetto motivates preserving typed time/track evidence instead of
flattening all future traces into a duration. These are design influences, not
installed dependencies; integration boundaries are in `tool-selection.md`.

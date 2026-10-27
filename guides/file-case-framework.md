# File-driven function and class experiments v1

This optional C++17 header makes a reviewed function/class callable from a normal
CppPerfLab request. It shares production observation's phase protocol and the
platform's existing metrics, tool discovery, execution, correctness and comparison
engine. It does not compile business code, generate an arbitrary function caller,
install tools or introduce a separate benchmark scheduler.

## Build the adapter once

Include `cppperflab/case.hpp` and implement the following hooks on your adapter:

| Hook | Responsibility |
|---|---|
| `counter_definitions()` | Return `vector<CaseCounter>` with name, unit and numeric Evidence state |
| `load(const CaseFiles&)` | Decode the declared input/version and optional expected payload; return a nonnull owned pointer-like handle |
| `prepare(const Input&)` | Construct a fresh, nonnull owned context handle supporting `reset()` |
| `reset(Context&, const Input&)` | Restore context to the declared initial state for the next operation |
| `invoke(Context&, const Input&)` | Call the target and return a movable `Adapter::Output` |
| `validate(const Input&, const Output&)` | Return `CaseVerdict{passed, classification, work}` using an independent oracle |

`work` has exactly one finite nonnegative value for each declared counter.
Counter definitions distinguish Measured, Estimated and Modeled work; invalid
values or an invalid hook contract fail visibly. Domain interpretation and input
decoding belong in the adapter. A JSON file format does not provide automatic
reflection or generic conversion to arbitrary C++ types.

The standalone [`file_case.cpp`](../examples/cpp/file_case.cpp) demonstrates an
exact byte-count function wrapped in a class. Its JSON mode intentionally measures
raw document bytes; it is not a general JSON parser. `std::count` and an optional
captured scalar provide independent checks. `oracle_case.py` runs text, JSON,
binary, both lifecycle modes and a deliberately wrong reference against the
compiled executable.

The minimal main is:

```cpp
Adapter adapter;
const auto options = perflab::case_options(argc, argv);
const auto summary = perflab::run_case(adapter, options);
return summary.passed ? 0 : 2;
```

Your executable should propagate exceptions as nonzero failures, never return a
normal result after a callback error. `CaseFiles::has_expected()` explicitly
distinguishes absence. An independent oracle is required even without a captured
reference. The normal request-level correctness command is still required.

## Declare lifecycle and scopes

`per_call` loads input once, then for each operation performs prepare → invoke →
validate → output/context destruction. `reuse_reset` prepares one context, then
performs reset → invoke → validate → output destruction per operation and context
destruction once afterward. Both measure input destruction as `unload`. The
Adapter object's own construction/destruction and process startup lie outside
these named phases and remain part of native process timing.

`operation` contains the four child steps. `invoke` wall time covers target call,
return and output emplacement. Do not add inclusive parent and child times.
Work is attached to both operation and invoke for scope-specific queries, and
must not be summed across the hierarchy. Allocation, PMU, heap and sampled CPU
metrics retain the adapter's actual scope: a whole-process counter cannot be
renamed as an exact function-region counter.

The header uses the preallocated per-thread PhaseRecorder; the actual function
scope contains clock/event writes rather than log I/O or dynamic trace growth.
Validation, classification and work recording add overhead outside invoke.
Phase serialization occurs after lifecycle timing; native time still includes it.
Use the existing phase calibration example and native timing for cost assessment.
No automatic overhead subtraction is performed. The first failed verdict stops
the remaining calls and writes its trace. Failed preflight traces remain visible
as preflight diagnostics and receive no valid performance score.

Lifecycle and repeats are explicit workload conditions. The default comparison
rejects differing lifecycle parameters. To study a new lifecycle strategy, define
and review the scientific equivalence and timing scope first; do not force a
comparison by erasing workload metadata.

## Bind archived input files

Whole argv entries supported by `replay`:

| Binding | Meaning |
|---|---|
| `{case_input}` | Verified archived input entry path; required in target argv |
| `{case_id}`, `{case_format}`, `{case_format_version}` | Archived sample identity and input protocol |
| `{case_expected}` | Reference path, rejected if absent |
| `{case_expected_or_none}` | Reference path or explicit `-` |
| `{case_expected_format_or_none}`, `{case_expected_version_or_none}` | Reference protocol or explicit `-` |
| `{case_lifecycle}`, `{case_repeats}` | Explicit workload parameter values |

These bindings also apply to correctness and per-sample validation argv. The
reserved `case.*` parameters retain collection, original input, classification,
capture observation and file hashes. Ordinary `{run_dir}` expansion still belongs
to the existing execution engine. Input content is never executed as code.

The C++ ABI is explicit:
`--case-v1 INPUT EXPECTED_OR_- ID INPUT_FORMAT INPUT_VERSION EXPECTED_FORMAT_OR_- EXPECTED_VERSION_OR_- LIFECYCLE REPEATS REPORT`.
File and metadata integrity checks happen before execution. The C++ executable is
trusted, reviewed code running with the user's permissions, not in a security sandbox.

## Reproduce the synthetic path

```bash
bash scripts/install.sh
source scripts/env.sh
cmake --preset profile
cmake --build --preset profile --target file_case
ctest --test-dir build/profile -R file_case --output-on-failure
python3.11 scripts/make_case_examples.py
perflab samples budget --config workloads/case-samples.json --json
perflab samples capture --config workloads/case-samples.json --json
# Substitute the returned ID.
python3.11 scripts/make_case_examples.py --collection COLLECTION_ID
perflab budget --kind study --config workloads/case-study.json --json
perflab study --config workloads/case-study.json --json
perflab report STUDY_ID --kind study --format html --output results/case-study.html --json
perflab analyze STUDY_ID --kind study --section phases --input-id text --json
```

The generated request uses a real `perf-stat` duration event; availability must be
checked through `plan`. Choose an available adapter explicitly if unsupported,
retaining its changed metric scope. It runs a fixture oracle, preflight, warmup and
formal repetitions with an exact validation in each target invocation. Budget
shows all launch classes before execution. `case-reuse-request.json` exercises
the reset lifecycle separately. The `mismatch` sample is an intentional negative
control: replay must fail correctness and preserve its phase/input evidence.

## Batch case Study and comparison

`schema study` includes `analysis: "cases"` with explicit collection ID, sample
IDs, named request-template files, comparisons array, seed, run budget and
constraints. Each sample × variant delegates to ordinary `run`. Order is shuffled
with the saved seed. A budget smaller than the full matrix is rejected; a failed
member stops execution without a successful-subset score. The Study records every
run ID and captured observation, and the HTML includes phases and raw run evidence.

For profiling only, use `comparisons: []`. For same-case variant comparisons:

```json
"comparisons": [{
  "baseline": "baseline", "candidate": "candidate", "metric": "phase.inclusive_time",
  "metric_scope": "case-lifecycle; phase=[\"operation\",\"invoke\"]",
  "policy": {"schema_version": 1, "min_samples": 7, "max_cv": 0.1,
             "max_regression_fraction": 0.05}
}]
```

Use the exact scope returned by `analyze`; the scope above is for the supplied
file-case runner. Every comparison goes through the
existing compatibility, correctness, statistics and regression gates. Define
multiple comparisons to display wall time, work and measured memory separately;
units are never added into a synthetic score. Cases retain individual results;
this interface does not invent suite weights or allow a failed case to disappear.

Each `comparisons` entry may preregister `gate: "required"` (the default) or
`"informational"`. An incomparable informational metric, for example a ratio
with a zero denominator, retains its inconclusive comparison and null score
without blocking other required comparisons. Correctness and required Claim
conditions still apply. Do not relabel a required metric after a failed run and
claim that the original experiment passed; use a newly preregistered experiment.

Case matrix comparisons are unpaired: randomized job order alone is not pairing.
For native AB/BA or ABBA, export each variant with `replay --mode prepare` and use
those requests in the existing [paired Study](paired-studies.md), with one
repetition per member and a declared block budget. Secondary metrics can present
invoke, operation, work and whole-process findings together. Never compare a
captured production duration directly to isolated replay as an optimization gain.

To preregister and run these timing/work comparisons together with a separate
memory profiler and combined gates, use [joint assessment](joint-assessment.md).
Its cross-profile link pins the same declared case/variant file identities.

See [sample capture](sample-capture.md) for selection bias, bundle completeness,
format validation, source evidence and integrity guarantees.

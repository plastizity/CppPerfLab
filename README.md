# CppPerfLab

Agent-native C++ performance experiments on Linux/WSL2. CppPerfLab adapts mature
tools, archives their raw evidence, and compares explicitly defined metrics.
It neither rewrites application code nor requires a runtime library in the target.

## Install and run

Requires Python 3.11+, and at least one measured tool. Installation is offline and
project-local, using Python's standard `zipapp`; no pip/network/system changes:

```bash
bash scripts/install.sh
source scripts/env.sh
perflab --version
perflab doctor --json
perflab tools list --json
perflab tools probe --tool perf-stat --events duration_time,cycles,instructions --json
```

Tool paths can be centralized in a project-local JSON file:

```bash
cp tools.example.json tools.local.json
# Edit paths to match your local installations, then:
perflab doctor --tool-config tools.local.json --json
```

Explicit paths are probed, not silently replaced. The Google Benchmark prefix is
a build hint; runtime collection requires a registered benchmark target.

`scripts/env.sh` exposes the local executable and Hyperfine directory for this
shell only. Re-run installation after code changes. For development:
`PYTHONPATH=src python3.11 -m cppperflab ...`. Standard pip packaging is also
declared in `pyproject.toml` (requires setuptools/wheel); it is not needed for the
offline installation. Native Windows execution is not implemented.

## Reproduce the independent C++ examples

To connect your own project, follow the
[project onboarding guide (使用者与 Agent)](docs/project-onboarding.md).
It covers build/oracle preparation, configuration, execution semantics and acceptance.

```bash
cmake --preset profile
cmake --build --preset profile
ctest --test-dir build/profile --output-on-failure
python3.11 scripts/make_examples.py
perflab workloads list --json
perflab plan --config workloads/locality-baseline.json --json
perflab run --config workloads/locality-baseline.json --json
perflab run --config workloads/locality-candidate.json --json
perflab history --json
perflab compare BASELINE_ID CANDIDATE_ID --json
perflab run --config workloads/cpu-profile.json --json
perflab run --config workloads/heap-profile.json --json
perflab analyze RUN_ID --json
perflab report RUN_ID --format json
perflab baseline accepted-locality-v1 RUN_ID --json
```

The supplied C++ project is independently buildable with
`cmake -S examples/cpp -B /tmp/cppperflab-external -DCMAKE_BUILD_TYPE=RelWithDebInfo`.
An external executable is configured solely through `target.argv`, build
metadata and its independent correctness command; no target integration is needed.
See [protocol](docs/protocol.md) for paths, inputs and comparison semantics.

## Algorithm experiments

```bash
perflab study --config workloads/algorithm-study.json --json
```

A study varies one declared input feature and several prebuilt algorithm
implementations. It invokes the same `run` API, archives ordinary experiment IDs,
randomizes execution order with a recorded seed and enforces an experiment budget.
Growth models use nonnegative affine fits and leave-one-out prediction error.
Model ranking is empirical evidence over the measured range, **not a Big-O proof**.
Correctness failures reject candidates without assigning a performance score.

Current study execution is a finite evaluation matrix, not an evolutionary
optimizer or mature automatic tuner. It does not compile or execute unreviewed
generated source. Resource limits and separate working directories are **not a
security sandbox**; filesystem/network isolation requires an external boundary.

## Interfaces and evidence

Agent queries and offline human reports use one shared analysis view:

```bash
perflab analyze RUN_ID --section metrics --json
perflab analyze RUN_ID --section hotspots --symbol kernel --limit 10 --json
perflab evidence RUN_ID --path measurement-000/perf-report.txt --limit 80 --json
perflab report RUN_ID --format html --output results/run.html --json
perflab compare BASELINE_ID CANDIDATE_ID --format html --output results/compare.html --json
perflab report STUDY_ID --kind study --format html --output results/growth.html --json
```

See [analysis views and local tool configuration](docs/analysis-views.md) for
query pagination, evidence integrity, comparison failure semantics and HTML controls.
`analyze` defaults to an overview; use `--section all` for the complete view.

Dimension-wise summaries separate work reduction, resource use, instruction
quality, timing and correctness. Missing measurements remain explicit gaps:

```bash
perflab analyze RUN_ID --section summary --json
perflab summarize --comparison INSTRUCTION_COMPARISON_ID \
  --comparison TIME_COMPARISON_ID --format html --output /tmp/perflab-summary.html --json
perflab analyze SUMMARY_ID --kind summary --section summary --json
```

See [multi-metric summaries](docs/performance-summary.md) for paired-counter IPC,
independent conclusions, numeric/SIMD coverage and empirical growth expressions.

`perflab schema request|result|envelope --json` exposes versioned JSON schemas.
Request configuration is JSON; YAML is deliberately not supported in v1.
`--json` returns `{schema_version, ok, result, error}`. Errors have categories,
details and stable exit codes. Run IDs identify immutable experiment directories
under `.perflab/runs`; probes, studies and comparisons refer to those same runs.
Full results, command stdout/stderr, tool-native data and SHA-256 inventories remain
available even after target failures, timeouts or cancellation.

Implemented adapters and their authoritative capability descriptions are queried
with `tools list`; only a successful `tools probe` establishes collection and
parsing on this machine. A probe does not prove a particular target is compatible.
Hyperfine performs low-overhead repeated process timing; perf counts events or
samples CPU stacks; Massif observes heap snapshots. Their timing scopes are not
interchangeable. Unknown counters are not replaced with guessed values.

The platform returns evidence and hypotheses, never an automatic code change.
`compare` rejects changed conditions and reports raw samples, variability and a
descriptive bootstrap interval. Fewer than seven samples or CV above 10% yields
`inconclusive`; those explicit initial policies are documented in each comparison.
Even an observed improvement requires an interleaved A/B rerun and oracle review
before accepting a production optimization.

## Development and Agent usage

```bash
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
cmake --preset check
cmake --build --preset check
ctest --preset check
```

[AGENTS.md](AGENTS.md) routes compatible Agents to the single
[C++ Algorithm Performance Engineering Skill](skills/cpp-performance/SKILL.md).
The session's `.agents` directory is read-only: AGENTS routing is used instead of
claiming automatic skill-catalog registration. See [architecture](docs/architecture.md),
[tool selection](docs/tool-selection.md)
and [acceptance evidence](docs/acceptance.md) for decisions and verified scope.

## Boundaries

- Commands execute with the invoking user's permissions. No sudo, network installer,
  runtime allocator replacement, fast-math, compiler rebuild or business-repo write.
- Workloads must terminate their children; daemonization/session escape is unsupported.
- Whole-process timing includes exec/startup. Python planning and parsing are outside
  tool timing; launcher elapsed time is diagnostic only.
- WSL host load, power management and virtual PMU accuracy are not controlled.
- CTest-specific result parsing, Heaptrack, Tracy, Perfetto, Caliper,
  Thicket, LLVM optimizers, tuning/evolution engines and MCP are not implemented adapters.
  CTest can already be used as a declared correctness command with its exit status.

Platform source is MIT licensed. External executables retain their own licenses;
they are optional tools rather than bundled runtime dependencies.

Google Benchmark v1.9.4 integrated-target timing is verified; build steps are in
[local dependencies](docs/downloads.md), and verification scope is in
[acceptance](docs/acceptance.md).

## Core metrics and configurable acceptance

`perflab metrics --domain D01 --json` queries implemented metric semantics and providers.
`analyze RUN_ID --section observations` exposes six-state evidence and same-sample
IPC/CPI/branch formulas. `compare A B --policy POLICY.json` applies explicit acceptance
gates through the existing comparison engine. See [core metrics](docs/core-metrics.md)
for protocols, actual commands, compatibility and the serial validation driver.

[D01–D05 callable extensions](docs/domains-1-5.md) document instrumented call/heap profiles, modeled cache analysis, explicit Kernel work rates and shared-run time/space growth. Reproduce with `scripts/validate_domains_1_5.py` after an explicit example build.

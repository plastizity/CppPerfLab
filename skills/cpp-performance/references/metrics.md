# Callable metric and acceptance workflow

Use the existing CLI; no model-specific runtime is needed for Astra to invoke it.
AGENTS routes to this Skill. Loading through AGENTS is not UI/global registration.

```bash
perflab metrics --domain D01 --json
perflab metrics --metric cpu.cpi --json
perflab schema evidence --json
perflab schema policy --json
perflab analyze RUN_ID --section observations --metric control.branch_mpki --json
perflab analyze RUN_ID --section metrics --metric cpu.ipc --json
perflab compare BASELINE_ID CANDIDATE_ID --metric time.wall --policy POLICY.json --json
```

Read `providers` and `source_capability`, then probe those operands explicitly.
Derived metrics are queried after collection; planning still takes native operand
IDs, not a derived metric as if it were an adapter event. All four formulas require
same-sample counters, matching scope/tool/version/event filters and >=99% running
coverage. That coverage policy does not certify virtual PMU accuracy.

The observations section includes Measured, Estimated, Modeled, Unsupported,
Missing and Invalid with operand evidence. No value is supplied for the last three.
Unknown old methods are Invalid in the read projection; archived files are not
rewritten. Models and estimates remain visible but cannot pass measured A/B gates.
Study growth ratios and slopes are estimates from independently measured medians;
model fits are Modeled and are not complexity proofs.

Write the policy before collecting candidate results and keep it with the experiment:
`schema_version`, `min_samples` (>=2), `max_cv`, `max_regression_fraction` are required;
`min_speedup` and `direction` (lower/higher) are optional. Choose values for the
workload, not to rescue a result after seeing it. Time defaults to lower; diagnostic
metrics have no acceptance direction unless deliberately specified. An IPC target
is not an elapsed-time target. Lower-direction benefit is baseline/candidate;
higher-direction benefit is candidate/baseline. max_regression_fraction limits the
candidate relative increase for lower metrics or decrease for higher metrics.

Inspect `acceptance.status`: PASS, FAIL, INCONCLUSIVE or UNSUPPORTED. Correctness
must pass; no option disables that gate or semantic comparability. PASS is only for
this metric, oracle coverage and declared policy, not all resource/numerical gates.
Run separate comparisons for required resource metrics; do not hide a failed gate.
Current intervals are descriptive 95% unpaired bootstrap, 5000 draws with saved seed;
min_samples and max_cv are configurable, but interval method is not yet configurable.
CLI `ok` means the operation completed, not that acceptance passed. Incomparable
results retain the existing nonzero error behavior and are archived. A measured
comparison with acceptance FAIL is still a successfully computed report.

The reproducible synthetic validation driver is `python3.11 scripts/validate_core_metrics.py`.
It requires prebuilt existing examples, compiles nothing, runs serially, saves its
policy before measurement, invokes the same CLI and archives raw CLI outputs and IDs.
Do not run CPU-heavy checks concurrently with it. It does not install tools or relax
failed gates, and its configured thresholds belong to that example only.

## D01–D05 callable extensions

Query the descriptor, configure explicit options and probe the selected adapter:

```bash
perflab tools probe --tool callgrind --json
perflab tools probe --tool dhat --json
perflab tools probe --tool cachegrind --json
perflab metrics --metric control.call_count --json
perflab metrics --metric memory.heap_peak_live --json
perflab metrics --metric kernel.useful_flop_rate --json
perflab analyze RUN_ID --section call_edges --symbol compute --json
perflab analyze RUN_ID --section allocation_sites --symbol main --json
```

Callgrind counts instrumented machine call edges and guest instructions. Source
inlined calls are absent; counts include the runtime. Never infer native time from
Callgrind execution or multiply calls by an assumed constant cost.
DHAT heap v2 counts tracked blocks and requested bytes and uses gb at t-gmax for
simultaneous live peak. Do not sum per-site mb maxima. `operation_count` is optional
positive integer input defining whole-command work; include its meaning in workload
metadata. DHAT counts do not measure RSS, allocator fragmentation or allocation time.

Cachegrind cache/branch metrics are Modeled and use separate IDs. Cache geometry can
be declared through I1/D1/LL options `[size_bytes, ways, line_bytes]`; native model
descriptors are retained. Use them to test locality hypotheses, then separately
compare native elapsed time. The measured comparison engine rejects modeled values.
A model with smaller misses is not a measured speedup or actual DRAM traffic count.

Google Benchmark accepts optional `counters` mapping with keys `useful_flops`,
`logical_bytes`, `operations` and values naming raw, plain per-iteration JSON
counter fields. Target rates, thread averages and iteration totals cannot be passed
as per-iteration counts. Review target definitions and oracle. Work rates use the
same repetition's wall time. Logical arithmetic intensity is Modeled, not DRAM AI;
useful FLOPs are algorithm work, not retired FP event counts or vector coverage.

Study supports optional `secondary_metrics` (unique IDs excluding the primary).
All are required Measured observations from the same runs. Primary growth and
comparisons retain their existing meaning; extra fits appear in secondary_growth
and `analyze STUDY_ID --kind study --section growth --metric METRIC_ID`. No additional
measurements are scheduled for them. Point-quality PASS only passes the recorded
sample/CV screen; it does not establish asymptotic complexity or model correctness.

Reproduce this increment with `python3.11 scripts/validate_domains_1_5.py` after an
explicit build and `scripts/make_examples.py`. The driver stores policy before
measurement, retains failed/inconclusive findings and does not install or rebuild.


### Relative cost (smaller is better)

Read `normalized_cost` in compare results, analysis comparison findings and HTML.
For time it is candidate median / baseline median, baseline 1. Throughput uses
baseline / candidate. Always report its interval and acceptance_status; INCONCLUSIVE
is not a verified improvement. Null means no valid scoring evidence or no declared
objective. Keep resource costs separate and inspect their gates before recommending
acceptance; no overall multi-resource acceptance or composite score is implied.
See `docs/core-metrics.md` for semantics and legacy-record behavior.

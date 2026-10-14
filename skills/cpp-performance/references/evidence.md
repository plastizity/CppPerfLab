# Interpret the existing protocol

Use `perflab schema ... --json` and `docs/protocol.md` for authoritative fields.
All measurements retain `name`, `unit`, `method`, `scope`, tool/version and a raw
evidence path. Method and scope are part of comparability, not display labels.

- `time.wall`: Hyperfine seconds versus perf duration nanoseconds have different
  provenance and scope; the v1 comparer rejects mixing adapters even if convertible.
- `time.cpu`: perf task-clock is separate from wall time. Parallel CPU time may
  exceed wall time; dividing alone does not establish useful parallel work.
- `cpu.*`: perf raw unscaled counts retain running percentage. Multiplexed or
  incomplete collection is not extrapolated, and comparisons reject <99% running.
- `cpu.hotspots`: self sample-period share. Open `perf-report.txt` and retain
  `perf.data`; inspect unknown symbols/sample count before following a call chain.
- `memory.heap_peak_observed`: maximum recorded Massif payload snapshot, not process
  RSS, bytes allocated over lifetime or actual exact continuous heap maximum.

CLI failures: 2 invalid/version/not-found, 3 unavailable/permission, 4 tool/parse,
5 target/correctness, 6 timeout, 7 incomparable, 8 persistence, 70 internal, 130
cancelled. Inspect `error.details` and the recorded logs; do not mask failure by
retrying different algorithms. Partial artifacts remain evidence, never a baseline.

Current confidence policy (recorded by compare): >=7 samples, CV<=10%, descriptive
bootstrap interval and 5% material change. These are explicit initial screening
policies, not universal scientific acceptance thresholds. Host drift/system noise
requires judgement and interleaved confirmation.

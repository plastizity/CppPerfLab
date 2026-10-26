# OS thread scheduling timeline

CppPerfLab provides a path to collect Linux `perf` context-switch records through the existing
`perf-record` Adapter. Enable `sampling` and `switch_events` together in the
request:

```json
"measurement": {
  "tool": "perf-record",
  "repetitions": 3,
  "warmups": 0,
  "timeout_seconds": 60,
  "options": {
    "sample_trace": true,
    "call_graph": "fp",
    "symbolizer": "addr2line",
    "switch_events": true
  }
}
```

The normal correctness, capability probe, preflight, warmups and formal target
launches still use the selected Adapter and execution engine. `switch_events`
adds `perf record --switch-events` to the same target recording and parses
`PERF_RECORD_SWITCH` from that recording. It does not add a target launch.
The recorded perf attributes and sampling clock context must verify monotonic
clock use and context-switch records. This path does not require tracefs scheduler
formats. On this host the native probe and end-to-end target-run validation
succeeded with the current user's permissions. Other hosts depend on their kernel
and `perf_event_open` policy and require an actual probe. Individual runs can produce
Missing or Invalid profiles when no closed interval was captured or the event stream
cannot be paired consistently.

## What each interval means

Events are grouped by process ID, thread ID and observed lifetime generation.
Within one thread, `IN` followed by `OUT` is a measured on-CPU interval; `OUT`
followed by `IN` is a measured off-CPU interval. Both endpoints must be present.
First and last intervals are censored at the capture boundary; process exit and
capture completion never fill a missing edge. The profile reports lifetime as
incomplete and leaves a duration `null` when no closed interval of that kind was
observed. Times use the recorded monotonic nanosecond clock.

The raw perf script output and normalized event/interval JSONL are retained and
referenced by the scheduler profile. The parsed timeline is available without
re-running the target:

```bash
perflab timeline RUN_ID --source scheduler --measurement 0 --json
perflab timeline RUN_ID --source scheduler --measurement 0 --track 123:123:0 --offset 0 --limit 1000 --format html --output scheduler.html
perflab analyze RUN_ID --section scheduler_timelines --json
```

Use a `track_id` returned by the query for `--track`. Pagination reports total,
returned and truncated interval counts. The HTML view displays each thread on a
shared time axis while preserving PID/TID track identity. This scheduler source is
separate from application work/wait annotations; inspect both views when both
sources were collected.

## Evidence and limits

Any recorded perf loss or inconsistent per-thread transition marks the derived
profile `Invalid` and removes usable intervals while retaining the native event
stream and issue details. A valid recording with no closed switch interval is
`Missing`, never a zero-duration measurement. A completed profile is `Measured`
for its recorded and paired intervals only; it does not prove complete thread
lifetimes, complete process execution, or capture of every CPU.

Switch records do not contain wakeup events. An off-CPU interval therefore does
not identify runnable-queue delay, the wakeup point, a blocking cause, lock
contention or synchronization cost. Per-thread times can overlap and must not be
summed into process utilization. No generic smaller-is-better score is assigned.
Combine the event timeline with application phases, CPU samples, workload/correctness
results and a discriminating experiment before assigning a cause.

The independent schema is `scheduler-timeline.schema.json`. The base run result
keeps an optional `scheduler_timelines` array so existing records without this
adapter option remain compatible. This capability uses the existing perf Adapter,
run storage, report and CLI; it creates no scheduler or results database.

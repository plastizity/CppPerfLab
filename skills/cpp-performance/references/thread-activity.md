# Thread activity evidence

Use this reference when investigating application work versus application-declared
wait time across threads. First record explicit phase annotations; then query the
stored run timeline and inspect its per-track coverage and limitations.

The phase schema accepts either `{"kind":"application_work"}` or
`{"kind":"application_wait","reason":"<caller label>"}`. In C++, annotate
with `PhaseRecorder::Activity::Work` or `Activity::Wait` and a non-empty reason
for Wait. For multi-thread traces, create one `LinuxMonotonic` recorder on each
thread and call `write_group` after joining. See [the full protocol and formulas](../../../docs/thread-activity.md).

Query stored evidence with:

```bash
perflab timeline RUN_ID --measurement 0 --json > timeline.json
perflab timeline RUN_ID --measurement 0 --format html --output timeline.html
perflab analyze RUN_ID --section thread_timelines --json
```

These are evidence queries; they must not start another target execution. Select
preflight only when the run actually has preflight phase evidence. Treat absent
annotations as unclassified, not zero. `wait_fraction` is an application wall
ratio, with no default improvement direction. The activity feature measures no OS
running/off-CPU states and does not prove wait causes; per-run scheduler evidence
is Missing. Environment capability `scheduling_tracepoints` is reported separately
as Unsupported or Missing. Use a supported scheduler trace and a discriminating
experiment before claiming a scheduling or lock-contention root cause. Keep
correctness, coverage, and target runtime acceptance distinct.

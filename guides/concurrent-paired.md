# Synchronized concurrent paired studies

Concurrent pairing is an explicit competing A/B experiment. Configure
`resource_conditions.concurrent_runs` as `2`, and provide a positive
`synchronization_timeout_seconds`, a `minimum_overlap_fraction` in `(0, 1]`, and
positive declared thread counts for both variants. Both requests still use the same
explicit non-batch adapter, one measurement repetition, and matched declared
conditions. The paired design and reporting remain the same as the serial design.

Each AB or BA block launches one synchronized wave. ABBA uses two waves, AB followed
by BA; waves and blocks run serially. Each wave prepares both ordinary `lab.run`
lifecycles, waits until both are READY, releases them together, waits until both
formal measurement launchers are running, and observes both leaders alive together.
Only then can the measured overlap be evaluated. The ordinary run lifecycle remains
responsible for probing, correctness, preflight, measurement, postprocessing and
storage. No second scheduler or results database is introduced.

The overlap evidence covers the observed formal launcher-command lifetime from
process launch through cleanup. It includes tool and startup time and excludes
postprocessing; it does not isolate target computation. Target-computation overlap
is therefore recorded as Missing. A wave must meet its preregistered overlap
fraction; otherwise it is inconclusive and has no performance score. Comparison
rejects a mixture of serial and concurrent members or waves with differing declared
competition conditions.

Concurrent measurements require Linux process observations and a single-threaded
coordinator. The platform applies requested CPU affinity at launch and records
observed CPU sets, but observations do not establish that affinity remained unchanged
throughout the full target lifetime. `threads_per_variant` and background load are
declarations; optional thread observations report only observed counts. Resource
competition is part of the measured condition, so report these results as performance
of the competing pair, not isolated serial speedup. Do not mix them with serial
history for concurrent-run ETA.

If either member fails or is cancelled, the peer is stopped and process groups are
cleaned up. The failed block remains in the study; the platform does not retry it,
drop it, or score the remaining successful blocks. Synchronization timeouts are
visible failures. Use the generated `paired-thread-activity.concurrent.study.json`
only to check the interface and evidence path: it uses the same synthetic workload
for both sides and makes no speedup claim. Real concurrent verification completed
on 2026-10-26; it likewise used a same-binary control and is not evidence of stable
performance or speedup. The random AB/BA Study
`20261026T102235-496bbcb82c75` completed 4 runs / 4 formal samples, and the ABBA
Study `20261026T102246-f675f727aaae` completed 8 runs / 8 formal samples. Across 12
valid concurrent samples, observed launcher overlap fractions ranged from
0.9983419183 to 1.0. The target's maximum observed thread count was 2; observed CPU
sets and requested affinity are recorded in the run evidence.

Failure and cancellation paths were also exercised: oracle failure
`20261026T102259-0806700300ee` retained one failed and one cancelled run, with zero
formal samples and no comparison. Cancellation during the latest concurrent formal
wave, `20261026T103941-e0a6435b786d`, left both runs cancelled; all 6 unique
observed process identities exited (10 recorded observations). A serial one-run record was used to verify that serial and
concurrent contexts cannot be mixed. Target-computation overlap remains Missing;
launcher lifetime includes tool/startup and is not CPU execution time.

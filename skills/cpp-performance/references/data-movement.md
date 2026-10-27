# Data movement diagnostics

Follow the [data movement guide](../../../guides/data-movement.md) for grouped
perf-stat probing, invocation work reports, metric formulas and CLI queries.
Establish the consumer's completed-work definition and oracle first. Check event
group capability on the actual host, evidence, scope and coverage before deriving a
ratio; a successful flat event probe does not establish group support.

`work.items` is an observed value reported by the producer, not independently
verified semantic equivalence. The four derived ratios are Estimated diagnostics;
they do not pass the measured-only comparison gate. Keep time, work and movement
observations separate. Do not multiply misses by cache-line size to assert physical
traffic, combine phase work with whole-process events, or infer memory-bound
causation from a ratio. Report missing or unsupported events and inspect raw evidence.

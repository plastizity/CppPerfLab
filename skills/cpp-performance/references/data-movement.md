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

For L1D, LLC or dTLB read accesses/misses, discover the six raw selector/metric
pairs in the guide and probe each actual event or required strong group. Inspect
the saved `event-descriptors.json` and its context/filter identity before comparing
or fitting: static Linux aliases are requests, not proof of equivalent vendor PMU
events. Missing kernel-to-physical mapping does not itself mean a collected raw
count is Unsupported. Do not relabel generic cache misses as LLC misses or invent
vendor event encodings. These six events introduce no layer-specific ratios.

The WSL2 guest validation found L1D and dTLB pairs available and LLC aliases
unsupported with perf `ENODEV`; it does not predict another environment. Consult
the guide for the preserved probes, independent paired Study IDs, runtime-identity
observation limits and commands for generating per-layer configurations.

# Native process resources

Use the [native resource guide](../../../guides/native-resources.md) to explicitly
build the project-local helper, probe the adapter and configure an ordinary Run.
This Linux wait4 path measures per-invocation RSS high-water, user/system CPU and
page faults without a hardware PMU. Its RSS is neither simultaneous tree RSS nor
heap or exclusive physical memory. Keep CPU-time resolution and startup/waited
descendant scope in the report, and preserve zero values without inventing ratios.

Collect native elapsed time separately with a suitable existing timing adapter.
Do not merge independent profiles as same-sample counters or claim physical DRAM
traffic, a causal bottleneck, or stable speedup from resource data alone. Consumer
work and oracle semantics remain consumer-owned.

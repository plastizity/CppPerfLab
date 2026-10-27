# Paired studies

Use `analysis: paired` to preregister serial AB/BA or ABBA blocks, or an explicitly
competing concurrent A/B design. Ordinary `compare` remains unpaired. Read
[`docs/paired-studies.md`](../../../docs/paired-studies.md) for the request shape,
budget, identity checks, failure behavior and block statistics.

For serial designs, members execute one at a time in their recorded block order.
For concurrent designs, explicitly set `resource_conditions.concurrent_runs` to 2
and provide the synchronization timeout, minimum observed overlap fraction, and
thread declarations. The platform prepares both regular run lifecycles, releases the
measurement launchers together, and requires measured launcher-lifetime overlap.
ABBA has two sequential concurrent waves (AB, then BA); blocks remain serial.

Launcher overlap includes tool startup and is not exact target-computation overlap.
If the required overlap is absent, the wave is inconclusive and cannot be scored.
Concurrent results describe the competing pair under recorded resource conditions;
they do not estimate isolated serial speedup. The platform applies requested CPU
affinity at launch and records CPU-set observations, but cannot establish that
affinity stays unchanged throughout the full target lifetime. Thread counts and
background load remain declarations. See
[`docs/concurrent-paired.md`](../../../docs/concurrent-paired.md) for limitations
and failure handling. Real same-binary interface verification on 2026-10-26 completed
random AB/BA Study `20261026T102235-496bbcb82c75` (4 runs) and ABBA Study
`20261026T102246-f675f727aaae` (8 runs), with 12 formal samples and launcher overlap
fractions 0.9983419183–1.0. This validates the interface, not stable performance or
speedup; target-computation overlap remains Missing.

Both designs reuse the normal `lab.run`, Store and Comparison contracts. They do not
retry or omit failed blocks. Keep serial and concurrent studies separate, and do not
combine their results or history as though they shared execution conditions.

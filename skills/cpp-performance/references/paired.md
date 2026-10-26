# Serial paired studies

Read `docs/paired-studies.md` and `schema study`. Use `analysis: paired` with two
prebuilt request files, one observation per member, explicit policies and resource
conditions. Preview `budget --kind study` first: every member includes its own
oracle/preflight/warmup. Never silently reduce those costs or reuse old correctness.

Choose random_ab_ba or ABBA and preregister seed, block count, run budget and metric
scopes. The platform invokes existing lab.run serially. It preserves member IDs,
slot/order, exact sources and pinned identities. Any failure stops without retries,
dropping blocks or scoring the successful subset. Concurrent competing runs are
unsupported by this serial design; no resource isolation is enforced.

Inspect block_pairs and policy.paired. ABBA is one cluster, not two independent
pairs. Shared-index bootstrap retains within-block association; block-ratio CV and
marginal CV have different meanings. Do not import two ordinary result arrays and
claim their row order establishes pairing. The interval is descriptive and block
independence is not proven. Report separate correctness, regression and improvement
conclusions, including work counters, internal timing and whole-process timing.

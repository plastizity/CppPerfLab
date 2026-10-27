# CppPerfLab Agent entry

Start with the root [usage guide](USAGE.md) for installation, experiment workflows,
report exports and links to focused guides. Read only the guides needed for the task.

For performance measurement, algorithm scaling, candidate comparison or tuning
requests in this repository, first read
[`skills/cpp-performance/SKILL.md`](skills/cpp-performance/SKILL.md).
This is the single local **C++ Algorithm Performance Engineering** Skill.
Discover capabilities through the CLI rather than maintaining a second tool list.

Keep platform code independent of any domain, fixture, input range or candidate
algorithm. Sample-specific assumptions belong only in `examples/` or tests.
Use existing run/measurement/storage contracts for new analyses. Do not create
another benchmark scheduler or results database.

Never alter any business repository as part of platform work.
Keep consuming-project identities, paths, business data and run evidence out of
the platform repository. Public examples must be standalone synthetic workloads.
No implicit source edits, rebuilds, fast-math, runtime allocator replacement, sudo
or global installation. External adapters execute trusted commands with user
permissions; process groups and resource limits are not security isolation.
Do not execute unreviewed generated code through this platform.

Reject malformed external inputs; propagate failures and keep evidence. Internal
contract violations must not be turned into empty/normal results. Do not add
speculative recovery, retries, compatibility paths or architecture layers.

Validate focused Python tests and actual adapters affected by changes. Independent
Agent tasks and multithreaded workloads may run concurrently; keep delegated write
sets disjoint. Independent performance experiments run serially by default.
Explicitly configured concurrent A/B comparisons may run both targets together;
record actual overlap, thread counts, CPU affinity and resource competition
conditions, and keep comparison blocks serial. Do not treat concurrent competition
results as isolated serial measurements. Unrelated CPU-heavy checks must not overlap
measurements. Preserve statistical limitations and report no unmeasured speedup.

The session's `.agents` and `.codex` are read-only. This AGENTS entry provides
project-local discovery and explicit loading; it does not claim UI/catalog
registration. Do not change their permissions or install global skills to bypass it.

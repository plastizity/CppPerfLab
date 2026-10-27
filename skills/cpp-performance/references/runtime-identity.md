# Actual program and library identity

Read `guides/runtime-identity.md` when copied binaries, RPATH/RUNPATH, dlopen or
uncertain library selection could invalidate a comparison. Discover the request
and report schemas with `perflab schema request|runtime-identity --json`.

Keep declared artifact hashes and actual runtime observations distinct. Enable
`measurement.runtime_identity` explicitly with stages, interval and snapshot/process
bounds; optionally declare reviewed basename/path/SHA-256 expectations. Do not
guess dependency identities from static RPATH or `ldd`, silently change a library
search path, rebuild a business project, or install a loader hook.

Preview the normal request budget. Preflight observation adds no extra run but
does not observe later measurements. Formal observation competes with the target
and requires an explicit high-overhead path when a goal is declared. Compare only
matching observation controls; keep hashes and collection costs in the report.

Use `analyze RUN --section runtime_identities` or the human HTML. Inspect actual
executables, target matching mode, mapped paths, PID/start ticks, hash states,
expectation gates and original snapshots. NOT_REQUESTED is not PASS; missing
objects cannot pass. A failed identity gate does not automatically mean incorrect
numerical output. Required failures stop while keeping the original evidence.

Periodic proc snapshots are incomplete. Short processes, interpreter wrappers,
rapid module loads/unloads, permission limits, deleted objects and namespace paths
can leave missing or unsupported evidence. File hashes are post-execution backing
file identities, not in-memory code hashes. Never claim a complete loader history
or unchanged libraries throughout later work from one successful observation.

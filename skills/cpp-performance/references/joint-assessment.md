# One assessment across timing, work and memory

Use this workflow when the user needs a single reproducible verdict from local
stage changes, full-process timing, actual work and resource costs. Read
`docs/joint-assessment.md`; `perflab schema study --json` exposes the contract.

Create `analysis: assessment` with archived complete cases and reviewed baseline/
candidate request templates. Declare profiles, roles, metric scopes, policies,
required/informational gates, seed and full run budget before measurement. Keep
the same input, lifecycle, function repeats and numerical requirements across
profiles. The workflow composes the existing paired/case Studies and Comparison.

Use `budget --kind study --config ...` before an expensive run. Discover explicit
tools through the existing CLI. Exactly one paired process-time outcome is required
per case, together with recorded work; choose memory/numerical gates appropriate
to the actual objective. Do not replace unsupported measurement with simulated
or estimated data, skip a required profile or change a target after seeing results.

```bash
perflab budget --kind study --config workloads/case-assessment.json --json
perflab study --config workloads/case-assessment.json --json
perflab analyze STUDY_ID --kind study --section summary --json
perflab report STUDY_ID --kind study --format html --output results/assessment.html --json
```

Read `summary.assessment`: correctness, joint acceptance, end-to-end improvement,
each mandatory case, selected gates and recorded work. PASS is not acceleration.
Local improvement cannot establish end-to-end gain, and a failed memory gate
remains a failed joint result even with faster timing. Changed work does not
establish single-step optimization; equal recorded work does not prove all work
constant. Numerical callbacks retain their independent hard constraints.

Inspect case/profile labels, initial file identities, child Study/comparison IDs
and original raw evidence. Matching declared binary/input hashes associates
profiles but does not prove actual loaded-library identity. Never pool native and
instrumented times or sum parent/child wall durations. A failed child preserves
its error and stops later jobs; completed children are not a successful-subset
score. Optimizations are not automatically approved for production deployment.

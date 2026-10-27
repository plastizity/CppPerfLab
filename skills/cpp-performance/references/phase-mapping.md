# Phase log mapping reference

Use this reference when a production program already emits stage records and
the project wants CppPerfLab to normalize them. The consumer owns the log format;
the platform mapping explicitly binds its fields to common evidence. Never infer
a value, unit, phase call count or correctness result from surrounding text.

## Select a rule mode

- `format`: literal template for a whole line, such as
  `Solve: {seconds} sec ({calls} calls)`. Placeholders are unique simple names;
  no format specifiers/conversions. Each mapped field uses `group` to name its
  placeholder. If the template has no `keyword` selector, set
  `unmatched_lines` to `error` so unrelated lines cannot be silently skipped.
- `keyword`: simple mode. The phase `keyword` selects a line; each mapped value
  uses a field `keyword`. Its value is exactly one following numeric token,
  optionally after `:` or `=`. Correctness uses one following nonseparator
  status token. Each field keyword must occur exactly once.
- `keyword` plus `pattern`: advanced mode. The phase keyword selects a line,
  then the regular expression extracts named groups. Every mapped field uses a
  `group` that exists in the expression. Python regular expression syntax is
  used. Avoid broad expressions that could overlap multiple rules.

Use rules for disjoint line types. A selected keyword line that fails extraction,
matches multiple rules, repeats a `single` rule, or contains invalid values fails
the import. `unmatched_lines: "error"` rejects every unmatched input line;
`"ignore"` skips them while recording the ignored count. A required rule must
match at least once. Optional unmatched rules remain present with null/Missing
values.

## Rule and field contract

Every mapping has `schema_version: 1`, nonempty `scope`, `rules`, and
`unmatched_lines` (`ignore` or `error`). Rule fields are strict; unknown fields
are rejected. Each rule defines unique `id`, display `name`, `parent_id` (null
for a root), `aggregation` (`single` or `sum`), `required`, and
`children_coverage` (`unknown` or `complete_nonoverlapping`). Parent references
must exist and cannot form a cycle.

Optional rule fields:

- `inclusive`: a total duration, or a `per_call_mean`. Requires explicit `unit`
  (`s`, `ms`, `us`, `ns`) and `kind` (`total`, `per_call_mean`). A mean requires
  a mapped `calls` field and yields `Estimated` total time after multiplication.
- `self`: explicitly reported self duration with a time unit. Reported as
  `Measured` per the input contract; do not map estimates as if they were
  measured values.
- `calls`: explicit nonnegative integer call count. Never inferred from rows.
- `correctness`: `values` maps exact input tokens to `passed`, `failed`, or
  `not_checked`. If absent, result is `not_checked`.
- `work`: named counters, each with `unit` and evidence state
  (`Measured`, `Estimated`, `Modeled`). Units are descriptive and are not
  converted by the importer.

All time values are converted to seconds in the summary. Numeric values must be
finite and nonnegative; calls must be integral. With `aggregation: "sum"`,
explicit measurements across matching rows are summed. With `single`, more than
one matching row is an error. Do not use `sum` for a per-call mean unless each
row's paired call count and meaning make summing those totals correct.

## Evidence and interpretation

The import records SHA-256 hashes for the input log and mapping, line counts,
matched/ignored counts and source paths. Source line ranges are traceability;
their count is not an invocation count. The canonical summary contains values
and provenance, not start/end intervals. Therefore it cannot reconstruct phase
ordering, overlap, root wall time, uncovered time or scheduler waits.

Parent and child inclusive times are independent observations and must not be
added together. Parent self time is derived only when coverage is explicitly
`complete_nonoverlapping`, the parent inclusive value exists, and every direct
child has an inclusive value. The subtraction result is `Estimated`. Child sum
greater than parent inclusive is rejected. No completeness claim means no
derived self time.

Missing or `not_checked` correctness is not a passing validation result. Keep
the run's independent correctness oracle as a hard gate. The phase report is
one evidence source for analysis; it cannot alone prove end-to-end speedup.
Inspect phase summaries using `perflab analyze RUN_ID --section phase_summaries
--json` or the HTML report. See [the user guide](../../../guides/phase-log-mapping.md)
for examples and experiment configuration.

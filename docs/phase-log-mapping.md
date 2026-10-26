# Phase log mapping

CppPerfLab can turn a project's existing phase log into the common phase-summary
evidence used by `analyze` and HTML reports. The project supplies a small JSON
mapping; the platform owns parsing, units, provenance, validation and reporting.
Mappings are deterministic rules. The importer does not ask an LLM to infer
phase names, units, calls or values.

## Two approachable mapping styles

**Format template** is for stable, structured lines. The phase `keyword` first
selects the relevant line; literal text must then match the whole line, and
`{field}` placeholders capture explicitly named values. Use
`unmatched_lines: "error"` to omit a selector, which makes every input line
subject to a format rule:

```json
{
  "schema_version": 1,
  "scope": "synthetic-model-run",
  "unmatched_lines": "ignore",
  "rules": [{
    "id": "assemble", "name": "Assemble", "parent_id": null,
    "keyword": "Assemble:",
    "format": "Assemble: {seconds} s, calls={calls}",
    "aggregation": "single", "required": true,
    "children_coverage": "unknown",
    "inclusive": {"group": "seconds", "unit": "s", "kind": "total"},
    "calls": {"group": "calls"}
  }]
}
```

**Keyword mapping** is for lines where a phase label and named fields occur in
otherwise project-specific text. Each configured field keyword must occur once;
its value is the immediately following numeric token (or status token for
correctness), with an optional `:` or `=` separator. There is no “first number”
heuristic:

```json
{
  "schema_version": 1,
  "scope": "synthetic-model-run",
  "unmatched_lines": "ignore",
  "rules": [{
    "id": "assemble", "name": "Assemble", "parent_id": null,
    "keyword": "Assemble", "aggregation": "single", "required": true,
    "children_coverage": "unknown",
    "inclusive": {"keyword": "elapsed", "unit": "ms", "kind": "total"},
    "calls": {"keyword": "calls"}
  }]
}
```

An optional advanced mode combines `keyword` with a regular-expression `pattern`
that has named capture groups. This is for logs that cannot be represented by a
literal format template or simple field keywords. The pattern is applied only
after its phase keyword selects the rule. Keep regexes anchored when the log
format permits it. See [the mapping reference](../skills/cpp-performance/references/phase-mapping.md)
for field options and precise behavior. The machine-readable contracts are
[`phase-mapping.schema.json`](../src/cppperflab/schemas/phase-mapping.schema.json)
and [`phase-summary.schema.json`](../src/cppperflab/schemas/phase-summary.schema.json).

## Import and attach to an experiment

```bash
perflab phases import --config phase-map.json --input run.log \
  --output stages.phases.json --json
```

For an experiment, keep the mapping and original program output as run inputs.
Configure the measurement as follows:

```json
{
  "measurement": {
    "phase_report": "stages.phases.json",
    "phase_import": {
      "source": "stdout.txt",
      "mapping": "phase-map.json"
    }
  }
}
```

The importer runs as post-processing outside the target's measured interval.
The original log, mapping copy and canonical summary are kept with the run's
existing evidence. A mapping hash pin can detect an unexpected configuration
change. Omit `mapping_sha256` to pin the actual file hash during request loading,
or supply its 64 lowercase hexadecimal digits explicitly. Review the import result
before interpreting stage values.

## How to read the result

The canonical `representation: "summary"` records reported stage inclusive time,
self time, call counts, work counters, correctness status and source line ranges.
It is not an interval timeline: it has no start/end timestamps and cannot
establish wall time, overlap, scheduler state or uncovered/root time. Missing
values stay missing. The number of matching log lines is parser provenance, not
a call count. Parent and child inclusive times must not be summed.

`children_coverage: "complete_nonoverlapping"` is an explicit claim by the
mapping author that the listed direct children cover the parent's work without
overlap. Only when every direct child has inclusive time may the platform derive
parent self time as parent inclusive minus child inclusive sum. That value is
marked `Estimated`; it is never promoted to measured evidence. If coverage is
unknown or child data is incomplete, no self time is inferred. A child sum
greater than its parent is invalid input.

Unreported correctness is `not_checked`; phase summaries do not replace the
experiment's independent correctness gate. A parser import or report display
does not by itself establish an accepted performance improvement. Use:

```bash
perflab analyze RUN_ID --section phase_summaries --json
perflab report RUN_ID --format html
```

The analyzer displays phase summaries separately from interval phases. Existing
interval evidence in `*.phases.json` remains supported under its existing
contract.

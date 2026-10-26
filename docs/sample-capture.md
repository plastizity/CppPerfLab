# Sample capture and replay v1

Sample capture stores opaque, complete inputs exported by the consuming program.
The consumer defines serialization, format versions, result classifications and
an independent correctness check. CppPerfLab selects, archives, verifies and binds
these inputs to reviewed requests; it does not infer a model's object graph or
execute input content as code. These are collection records in the existing Store,
not a second experiment database. Replay always delegates to ordinary `run`.

## Manifest and selection

Query the contract with `perflab schema sample-manifest --json`. A minimal example:

```json
{
  "schema_version": 1,
  "name": "synthetic-export",
  "max_payload_bytes": 1048576,
  "selection": {"mode": "longest_per_result", "limit_per_result": 1, "seed": 71},
  "samples": [{
    "id": "call-42",
    "input_id": "document-7",
    "input_complete": true,
    "input": {"format": "text", "format_version": "document-v1", "value": "example"},
    "expected": {"format": "json", "format_version": "answer-v1", "value": 7},
    "result": "uncertified",
    "correctness": "passed",
    "observation": {"duration_seconds": 0.25, "evidence_state": "Measured"}
  }]
}
```

This is a format illustration, not measured performance evidence. `id` uniquely
identifies the archived sample; optional `input_id` identifies the producer input
and can be shared by multiple calls. `result` is a business classification and
does not determine correctness. For example, an uncertified search can still have
a valid returned status. Correctness is separately `passed`, `failed` or
`not_checked`.

Each payload declares `format` (`text`, `json`, `binary`), `format_version`, and
exactly one of `path` or `value`. Binary requires a path. Text values are UTF-8;
JSON values use canonical compact UTF-8 serialization. Path payloads preserve
their bytes. Selected text/JSON files are validated after copying; invalid
content leaves a failed collection and cannot be replayed. Optional `attachments`
are `{ "name": "relative/file.bin", "path": "export/file.bin" }` entries.
Paths resolve relative to the manifest. Names must be normalized relative paths,
without traversal or overlapping entries. The producer must make references
relocatable; absolute references embedded in opaque data are not rewritten.

`input_complete: true` is an exporter assertion, not proof by the platform. Include
all required files in input/expected attachments. Environment and build metadata
still belong in the execution request. An absent expected payload is explicit;
it never becomes a default output or permission to skip independent validation.

The selection is declared before capture:

- `all`: retain every payload.
- `longest_per_result`: retain the longest `limit_per_result` calls per result;
  every eligible duration must be Measured. Missing timing is rejected, not zero.
- `random_per_result`: seeded sampling per result, with the same explicit limit.

An optional `results` array restricts eligible classifications. All population
metadata, selected IDs, rules and seed remain in the collection, including entries
whose payload was not retained. Selection is for diagnosis; it does not establish
representativeness or authorize a benchmark suite score. The payload budget covers
selected input/expected/attachment bytes; manifest and metadata bytes are reported
separately. Budget performs no program execution or writes, but reads source
metadata. Full file format validation happens during capture.

## Link an actual phase observation

Instead of exporting a duration manually, supply:

```json
"source": {
  "run_id": "ACTUAL_RUN_ID",
  "execution_stage": "measurement",
  "sample_index": 0,
  "phase_id": "ACTUAL_EVENT_ID"
}
```

The platform checks the saved phase, input ID, classification and correctness,
verifies the original raw phase hash and normalizes it again. It copies the raw
phase evidence into the collection, allowing verification without the original
run folder. The exporter still asserts correspondence between payload and call.
For a failed preflight, use `execution_stage: "preflight"` and
`sample_index: null`; that duration remains a diagnostic preflight observation,
not a formal benchmark sample. Legacy phase records without a stage mean
measurement. If both `observation` and `source` are supplied they must agree.

Observations use the six Evidence states. Measured/Estimated/Modeled require a
finite nonnegative duration; Unsupported/Missing/Invalid require null and a reason.
Only replay measurements can establish isolated execution performance; a captured
producer duration and an isolated replay are different scopes.

## Actual interfaces

```bash
perflab samples budget --config workloads/case-samples.json --json
perflab samples capture --config workloads/case-samples.json --json
# Use the returned collection_id below.
perflab samples list COLLECTION_ID --sort elapsed_desc --limit 20 --json
perflab samples list COLLECTION_ID --result uncertified --json
perflab samples verify COLLECTION_ID --sample text --json
perflab replay COLLECTION_ID --sample text --config workloads/case-request.json --mode budget --json
perflab replay COLLECTION_ID --sample text --config workloads/case-request.json --json
perflab replay COLLECTION_ID --sample text --config workloads/case-request.json \
  --mode prepare --output workloads/case-bound.json --json
perflab run --config workloads/case-bound.json --json
```

`verify` checks the collection, captured files, source-manifest and linked phase
hashes. It never repairs files or falls back to the original export location.
Prepared requests pin payload/attachment SHA-256 values through
`workload.expected_hashes`, checked before any executable probe and again as part
of normal input identity. Tampering fails before target execution. Hashing is I/O
and can warm caches; it is not a cold-cache preparation method.

Binding only accepts whole argv entries, preventing accidental embedded command
construction. Input bytes are passed as file paths, never shell commands. See the
[function/class framework](file-case-framework.md) for bindings, lifecycle hooks,
batch execution and limitations.

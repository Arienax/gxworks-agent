# Standalone semantic runtime publication

This producer accepts a compiled instruction-definition document and writes a
NEW, instruction-only runtime bundle. It does not replace an unavailable private
full-corpus builder, and must not replace a mixed instruction/device/error corpus.
No upload or publication is performed. Runtime acceptance remains a separate gate.
Unknown root fields and unknown language-feature metadata fail closed. Exact
ST/FBD language features reuse strict fact projection, preserve their own scope
and dependency graph, and stay inline in the definition bundle without
instruction aggregation. Language facts are not indexed in the instruction-only
SQLite. Source withdrawal and dependent withholding are recorded privately.

```sh
python scripts/build_runtime_projection.py \
  --input /private/instruction_definitions.json.gz \
  --output-dir /local/candidate-runtime \
  --private-evidence /private/evidence/input-evidence.json.gz
```

Use fresh output paths. The private evidence path must be outside the runtime
output directory. Original input objects are preserved in the private gzip,
alongside a private strict-withholding report and a private input/output lineage
map. Keep all three private. The runtime directory contains definitions, an
instruction-only SQLite index, and aggregate metrics only.

Pipeline:
1. Existing strict `project_definition` projection, with every withheld identity
   and reason recorded privately. A representation pass does not establish PLC
   legality, native compilation, or hardware execution.
2. Independent semantic aggregation: exact owner, value, scope, conditions,
   status, verification, authority, unknown fields and equivalent dependency
   graph. No strongest-per-dimension selection or model widening.
3. Exact content-addressed source/value transport pooling. This stage is
   lossless storage sharing, not additional semantic deduplication.
4. One complete owner graph per SQLite `runtime_definitions` row. Sources and
   values have separate exact pools. Search chunks contain searchable tokens and
   an owner reference, not repeated complete fact packets. On selection, readers
   hydrate only that owner before existing aspect/language/dependency selection.

Legacy unpooled definitions and SQLite chunks remain readable. Missing or altered
pool references fail closed. The Registry still materializes its existing typed
fact inventory; disk pooling alone is NOT evidence of lower steady-state Registry
memory. Measure JSON load, Registry load and build peaks separately.

During Registry loading each unique catalog object is hash-validated once into a
load-owned immutable parsed-tree snapshot. Fact hydration returns detached values;
there is no global trust cache or caller-controlled validation bypass. The mutable
input catalogs are released, and inventory/spec/caller values do not alias.

The receipt distinguishes raw input facts, strict withheld facts, semantic input
and output facts, stored owner facts, and zero fact *copies in chunks*. Zero chunk
copies does not mean missing owner facts. SQLite file size is not a measurement
of the original private SQLite builder's output. Full input coverage and original
producer integration must be verified separately.

For a small local audit without uploading full definitions, use:

```sh
python tools/audit_runtime_fact_package.py --help
```

Synthetic regression entry points:

```sh
PYTHONPATH=src python -m unittest discover -s checks -p 'test_runtime_fact*.py'
PYTHONPATH=src python -m unittest discover -s checks -p test_fact_task_projection.py
```

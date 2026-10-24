# Standalone semantic runtime publication

This producer accepts a compiled instruction-definition document and writes a
NEW, instruction-only runtime bundle. It does not replace an unavailable private
full-corpus builder, and must not replace a mixed instruction/device/error corpus.
No upload or publication is performed. Runtime acceptance remains a separate gate.
Unknown root fields and unknown language-feature metadata fail closed. Exact
ST/FBD language features reuse strict fact projection, preserve their own scope
and dependency graph without instruction aggregation. The owner-store SQLite
indexes language features separately as `kind=language`, supports exact CPU-scoped
target/alias selection, and hydrates referenced instruction owners when selected.
Feature aliases, declared models, reference parameters and reference model bindings
are preserved; an absent `language` field stays absent rather than being guessed.
Source locators are projected under the existing publication policy, with private
lineage and withholding evidence retained. Source withdrawal and dependent
withholding are recorded privately.

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
pool references fail closed. The legacy pooled-JSON Registry path still materializes its existing typed
fact inventory; disk pooling alone is NOT evidence of lower steady-state Registry
memory. The manifest path below uses selected-owner loading instead. Measure
JSON load, Registry load and build peaks separately.

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

## Random-access candidate store

The owner store is now consumed by the actual `InstructionRegistry`, not only by
an isolated retrieval experiment. A sibling `instruction_definitions.manifest.json`
selects the read-only SQLite store. A malformed selected manifest fails closed;
it never silently falls back to the legacy whole-JSON definition package.
The resource collector includes the manifest and database together. It still
performs an offline complete public-content audit before packaging.

For an explicit local candidate, set `GXW2_INSTRUCTION_OWNER_MANIFEST` to the
candidate manifest path. Normal catalog seeds and existing CPU admission remain
in effect; this selects the definition source only. Missing/bad explicit paths
are errors. Leaving the variable unset preserves normal resource selection.
Do not replace deployed resources merely to run a candidate comparison.

The `owner-field-pool-v2` transport adds an exact shared-field table for scope,
conditions, dependencies, source references, value references, dimension, status
and verification. Fact IDs and unknown fields remain owner-local. It preserves
missing versus empty values and ordered lists. Hash collisions, altered objects,
missing references, dependency gaps and cycles fail closed. This is reversible
structural sharing, not a claim that distinct owner-qualified facts are duplicate
knowledge. Search indexes contain actual technical values and conditions, not
source/value hashes or serialization wrappers.

The Registry reads identity headers at startup and hydrates a requested owner
only when its definition or typed fact contract is needed. Generation menus read
lightweight admission metadata without loading all fact graphs. Caller results
are detached; cached checking contexts become invalid when the manifest/database
changes. SQLite is opened read-only with `query_only` enabled. The manifest's
streamed database hash authenticates the complete physical snapshot at open;
selected owner packets and referenced objects have additional validation.

A bounded streaming migration entry point is
`scripts/build_instruction_owner_store.py`. It reads schema-1 entries or one
schema-2 family at a time and writes exact pools incrementally in SQLite; it does
not retain all owners/catalogs in Python. Inspect its current `--help` and input
review requirements before use. Unknown root metadata and private documents must
not be silently discarded. The older `build_runtime_projection.py` still loads
the input JSON and is NOT the bounded-memory full-package conversion path.

Offline audit (this deliberately scans all owners, unlike application startup):

```sh
python tools/audit_runtime_fact_package.py --owner-manifest /local/candidate/instruction_definitions.manifest.json --owner-breakdown --output /local/new-audit.json
```

The tested 4,896-owner subset shrank from 217,853,952 to 118,165,504 SQLite bytes
using exact field sharing and removal of search-index transport noise. All
168,172 projected facts and their dependency/status/scope graphs round-trip
exactly. These are subset measurements, not acceptance of the user's full
87,754-owner package. FX5UC/UJ/S manual facts and offline coverage do not establish
support in the current GX Works2 engineering workflow; that remains pending.

For a raw compiler schema-1/2 package, opt into the existing strict projection
explicitly and retain its complete withholding trail outside the runtime folder:

```sh
python scripts/build_instruction_owner_store.py --input /private/instruction_definitions.json.gz --output-dir /local/new-owner-candidate --project-raw --private-audit-dir /private/new-owner-projection-audit
```

This streaming mode applies `project_definition` per owner and preserves the
original input unchanged. It does not perform semantic merges or promote review
statuses. The private gzip JSONL audit records withheld identities/reasons and
per-owner input/output hashes. Without `--project-raw`, only already-authored,
reviewed input is accepted for exact transport migration. Final audit and the
conventional manifest are produced only after every owner validates. A candidate
is not a replacement for the missing original mixed-corpus publication pipeline.

## Language preflight and failed attempts

The streaming owner-store converter validates and projects bounded language
metadata before creating the SQLite database or per-owner audit ledger. Unknown
metadata remains an error; compatibility does not relax CPU, dependency, review
status or private-prose restrictions. The same projected language representation
is used for writing and loading, so validation is not an independent permissive
shortcut.

In explicit `--project-raw` mode, a failed attempt writes a private
`projection-failure.json` when the separate audit directory permits it. It records
the phase, progress and error without overwriting an existing failure report.
Existing partial artifacts are retained, not deleted or silently enabled; the
conventional manifest is published only after the offline audit and receipts pass.
Retry only with fresh output and private-audit directories after fixing the error.
The original input stays unchanged. Passing a small production-language fixture
does not establish acceptance of the full 87,754-owner package.

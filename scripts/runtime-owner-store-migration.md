# Migrate an existing candidate without re-reading the source bundle

This is an opt-in, exact storage migration from a sealed v2 owner-store manifest
and its sibling SQLite database to a NEW v3 candidate directory. It does not
re-run source publication, remove facts, infer CPU support, change review states,
or enable the new candidate. Existing v1/v2/v3 directories are left untouched.

From the repository root on Windows, use one command (replace the input path):

```powershell
python scripts\migrate_instruction_owner_store.py --source-manifest "<existing-v3-directory>\instruction_definitions.manifest.json" --output-dir "tmp\compact-v4-candidate"
```

The output directory must not exist and must not be inside or contain the source
directory. Its parent must exist. Symlink paths, active write journals, WAL/SHM
sidecars, changed sources, invalid manifests, and unsupported storage versions
are rejected. Do not delete sidecars to force acceptance. Close the candidate's
writers first; this tool never checkpoints or repairs the source database.

Allow space for a full backup, rewritten rows and SQLite VACUUM temporary space.
The tool requires at least three times the source database size plus 64 MiB free
on the output filesystem as a planning allowance, not a guaranteed upper bound.
A 976 MB source therefore requires roughly 3 GB free before starting. Disk errors
can still occur; partial candidate files are retained, never silently enabled.

The converter copies the database, then works one owner packet at a time without
hydrating the complete source/value fact graphs. It preserves existing source,
value and shared-field IDs; exact repeated fact IDs can gain new shared fields.
Each converted fact is decoded back and compared with its original field-level
representation. Search text is shared while retaining every chunk rowid and all
search/filter columns. The original FTS index and owner-qualified document count
are retained without rebuilding, so this is not document deduplication for BM25.

The new directory contains the database, a small physical-storage audit and a
migration receipt. The conventional manifest is published last, only after all
checks and receipts pass; an identical pending manifest may remain. A failed
attempt retains its partial files and a bounded failure receipt, not an enabled
candidate. Retry after diagnosis into another NEW directory, never by overwriting
or deleting the original input or old candidate.

Share only `owner-store-migration-receipt.json` and
`owner-store-storage-audit.json`, not the database. Total before/after size includes
VACUUM and must not all be attributed to structural sharing. Packed-format exact
roundtrip is distinct from full runtime regression, hardware verification and
native compilation. Successful conversion alone is not release acceptance.

## Software compatibility and activation

Directory names such as v1/v2/v3 are user labels, not the storage version. This
converter accepts the existing `owner-field-pool-v2` transport even when its
directory is named v3, and writes `owner-field-row-v3` plus exact search pooling.
The outer manifest remains schema version 1. Older software without the new
transport reader must reject this candidate; never silently fall back or relabel
the new database as an old format. Keep the updated reader and the normal catalog
seed together.

Migration does not set `GXW2_INSTRUCTION_OWNER_MANIFEST` or otherwise activate a
candidate. Retain the existing inactive v1/v2/v3 directories and receipts. Runtime
acceptance and an explicit activation decision remain separate from conversion.
Any size reduction measured on smaller test corpora, including a same-VACUUM
comparison, is not a prediction for the user's full database.

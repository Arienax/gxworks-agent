# Owner-store production routing and release-resource staging

Selecting `GXW2_INSTRUCTION_OWNER_MANIFEST` selects the authenticated owner
store for default production instruction/language retrieval as well as Registry
lookups. Exact targets, aliases and broad searches use the same selected store.
Device, error and general legacy knowledge remain in their existing database.
An explicit caller-supplied connection retains precedence; it is not silently
replaced by the selected owner store.

Broad results from different database domains are merged by domain-local rank,
not by comparing raw BM25 scores with different corpus statistics. Result IDs are
domain-qualified to avoid cross-database chunk-ID collisions. Raw SQL lookup keys
remain available internally so parent/child and source-page retrieval query the
correct original rows. This does not rewrite stored fact identities or primary
keys and does not expand CPU or native engineering support.

## Packaging boundary

`build_release.py --resource-root` stages the resource graph selected by the same
validated collector used by the release specification. A supported manifest and
its sibling database can replace the definition gzip; the normal catalog seed
and the other required mixed-corpus resources remain required. Legacy resource
roots without a manifest retain their existing gzip path. Missing, altered or
inconsistent selected resources fail rather than silently falling back.

The manifest must be in a collector-supported location inside the explicitly
chosen resource root, such as `resources/instructions/mitsubishi` or
`resources/knowledge/runtime`, with its authenticated sibling database. Merely
setting an environment variable to an arbitrary external temporary candidate
DOES NOT include that candidate in a release package. Resource selection and
release staging are explicit, separate from temporary runtime comparison.

The staging entry point refuses a reused nonempty runtime-input staging area.
Tests exercise the actual `build_release.main()` selection/copy path while
stubbing the external freezer/build actions. They do not constitute a completed
PyInstaller release or permission to distribute private source evidence.

## Validation scope

Routing and staging tests cover resource selection and the retriever/application
boundary. Final package checks separately exercise frozen executables from a new
ZIP extraction. Missing optional dependencies are skipped coverage, never passed
validation. Temporary candidates and private audit data remain separate from
deployed runtime resources and public source.

## Store names and compatibility

New builds write `instruction_owners.sqlite`. `--storage-layout current` selects
the current supported compact layout; the explicit layouts are `source-pool`,
`field-pool` and `fact-rows`. The semantic `fact_bundle_version` is independent
of this physical transport and is preserved, including newer semantic compiler
revisions. Format selection does not promote knowledge into call capability.
The mixed instruction/device/error corpus remains
`plc_facts.sqlite`. Existing manifests continue to resolve their declared sibling
filename; existing databases do not need to be renamed or migrated. Runtime
startup checks versions, structure and requested records. Explicit build audits
perform the existing whole-file integrity verification.

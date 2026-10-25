# Owner-store production routing and release-resource staging

Selecting `GXW2_INSTRUCTION_OWNER_MANIFEST` now selects the authenticated owner
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

The focused routing/staging regression uses the real retriever, application and
MCP entry points with declared optional dependencies. Test-only guards disable
telemetry and reject business network access; they do not change product network
settings. Representative actual small-corpus staging also checks the selected
file graph. Missing optional dependencies must be reported as skipped coverage,
not passed production validation.

No user candidate was enabled, no deployed resource was changed, no PyInstaller
binary was produced, and the user's full approximately 804 MB candidate was not
validated by these smaller staging tests. Existing source/audit data and candidate
directories remain separate from public code and must retain their review and
publication boundaries.

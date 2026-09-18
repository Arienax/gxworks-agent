# Source layout

Baseline: `fix/confirmed-generation-compatibility-20260918` at
`295378d6ac5d24949c03a1c799906f4afe0fd4c9` (PR #13).

## Navigation

| Package | Responsibility |
| --- | --- |
| application | Workflows, approval, jobs, generation context and project operations |
| model_runtime | Provider protocol, capability contracts, catalog and observations |
| knowledge | SQLite/dense retrieval, supporting-source reranking and patterns |
| plc | Deterministic IR, validation, instruction rules, specification and artifacts |
| agent_runtime | Model orchestration, neutral tool messages and shared tool runtime |
| inspection | Review findings and presentation data; no Qt imports |
| rendering | Headless SVG and native-layout rendering; not a desktop-only package |
| storage | Configuration, credentials and versioned project state |
| shared | Paths, locale, diagnostics and context auditing |
| ui/desktop | Qt windows, dialogs, workbench cards and SFC editor |
| gxw / gxworks2 / simulator / integrations | Existing format, automation, simulator and protocol packages |

`source-layout-map.json` contains the exact old-to-new module map.
`src/main.py` remains the desktop launch path and `src/api.py` remains an import
compatibility entry point. They alias their canonical implementation modules,
not copies made with `import *`; caches, private helpers and monkeypatches retain
one module identity. Product code, tests and runnable scripts use canonical imports.

Package initializers stay lightweight. Importing model contracts, tool messages
or PLC rules must not import a model SDK, Qt or the MCP SDK. Core code must not
reach upward into application workflows. The artifact writer and deterministic
candidate repair assembler therefore live in `plc`, shared by their callers.
The workbench uses ordinary relative imports, with no filename collision and no
`spec_from_file_location` loading. PyInstaller follows static editor imports.

## Behavior and data

This change relocates implementations and imports; it does not revise prompts,
control logic, instruction limits, confirmed specifications, request policy,
repair budgets, approval rules or saved IR/CSV formats. Generation, inspection,
MCP and Web continue using the same engineering implementation.

`shared.paths.source_root()` preserves existing source and frozen-build resource
and state locations. Existing `src/config.json` and model observation databases
are not deleted or silently migrated. Observation SQLite files are ignored by Git.
Read-only resources remain in `resources`; native GXW evidence and experimental
implementations are retained. They must not be deleted merely because their names
contain “experimental”. `tools/render_saved_ladder.py` replaces the old manual
`src/svg.py` utility; run it with the same working directory as before.

## Scope still intentionally deferred

The large desktop window and model-workflow implementation have been contained,
not rewritten into a new application architecture. Rendering's existing base and
native-layout algorithms and generation-context support code are preserved;
renaming their modules is not a claim that their compatibility logic is obsolete.
Splitting those implementations by behavior is a subsequent, separately tested
refactor. Do not merge feature changes or change stored schemas during that work.

## Guardrails and verification

`tests/test_source_layout.py` checks root-module allowlists, name collisions,
canonical imports, lightweight package initialization, resource locations,
compatibility identity and headless import boundaries. Existing architecture tests
inspect actual canonical implementations rather than empty compatibility shims.
Tests keep their existing names/location so CI selections remain valid.

Run `python -m compileall -q src tests scripts tools`, the source-layout and
architecture tests, then the affected regression suites. Full desktop testing
requires Qt; actual Windows packaging and GX integration require Windows.
A skipped optional dependency is not evidence that the corresponding subsystem
was verified. Compare failures with the recorded baseline, rather than hiding
pre-existing failures or weakening validation to obtain a green result.

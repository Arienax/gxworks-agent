# Source layout

This layout is based on `fix/confirmed-generation-compatibility-20260918` at
`295378d6ac5d24949c03a1c799906f4afe0fd4c9`. It preserves the existing launch
commands and persisted engineering formats. It is a package organization change,
not a new PLC implementation, a new capability catalogue, or a UI redesign.

## Ownership

| Package | Owns | Must not own |
| --- | --- | --- |
| `application` | Use cases, jobs, approval, project state, generation context | Qt widgets, vendor wire parsing |
| `model_runtime` | Model messages, transport, local catalog, contracts, observations | PLC/GX implementation, UI |
| `knowledge` | SQLite/LSA retrieval, source priority, reranking, patterns | Candidate acceptance or UI |
| `plc` | IR, instructions, confirmed specifications, deterministic checking and artifacts | Models, application orchestration, UI |
| `agent_runtime` | Neutral tool messages/runtime, agent orchestration, PLC tool implementation | A second MCP-only engineering stack |
| `inspection` | Inspection data, engine and presentation models | Qt widgets |
| `rendering` | Headless SVG and rung numbering | CSV export, Qt |
| `storage` | Configuration, credentials, project/session persistence | UI |
| `shared` | Paths, language, diagnostics, tracing, context policy | UI and application service ownership |
| `ui/desktop` | Windows, workers, dialogs, editor widgets, styles | A separate generation or validation implementation |
| `gxw`, `gxworks2`, `simulator` | Existing native-format, vendor integration and simulation capabilities | Model transport |
| `integrations` | Web and MCP protocols | Direct GUI/PLC execution instead of shared services |

`src` contains only `main.py` and `api.py` plus packages. New modules must go
straight into their owning package; no new compatibility module, dated patch
module, `old`/`new` copy, or parallel implementation should be placed at the root.
`__init__.py` files expose explicit small public surfaces. In particular importing
`agent_runtime.messages` never imports the model provider.

## Entry points and imports

The source layout is still used through `PYTHONPATH=src` as before. This change
intentionally does not combine repository organization with a packaging migration.

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m integrations.web --workspace "D:\PLCWorkspaces\my-workspace"
python src/main.py
```

`main.py` delegates to `ui.desktop.application.run`. `api.py` explicitly exports
the established public service functions; application internals use
`application.model_api`, not the root compatibility surface. The old model/PLC/
knowledge/Qt root modules are removed. `tools/source_layout.json` is the complete
old-to-new navigation map, not a runtime import hook.

The first stage's model aliases are deliberately removed after every product,
test and tool import is migrated. Tests patch the module where a dependency is
looked up, so a cached imported function is not accidentally patched elsewhere.
No new `sys.modules` aliases or wildcard facade are used to conceal old imports.

## Seams extracted before relocation

`rendering/ladder_svg.py` contains the base and wrapped SVG implementations.
`gxworks2/csv_export.py` contains CSV lowering/formatting/native step widths.
`plc/artifacts.py` composes deterministic artifacts for core/debug clients without
importing the application debug loop. The original strict/structural acceptance
choice is unchanged.

`ui/desktop/workbench` separates the specification editor, summary/review dialog,
and message bubble. It uses ordinary imports, not a sibling source file loaded
with `spec_from_file_location`. PyInstaller no longer ships Python source as a
workbench data-file workaround.

The desktop controller is separated from styles, thread workers, classic window,
activity widget, workflow dialogs, window frame and SFC dialog. Application model
prompts and analysis-result normalization have their own modules. This does not
rewrite the controller's behavior or change any model prompt content.

Required context helpers live in `application/generation_support.py`. The old
full prompt implementation is retained only under `research/baselines` for
benchmark comparisons, never in product imports. Active GXW experiments remain
in their existing bounded backend; a filename alone is not evidence that they
can be deleted safely.

## Resources and persistent data

Keep source `src/config.json`, source-era historical session lookup, packaged
executable-adjacent configuration, and frozen `_MEIPASS` resource behavior.
Moving Python code is not authorization to migrate, clear or regenerate user data.
Model observations stay attached to their existing configuration/state path.
The resource catalogue, SQLite/LSA files, original GXW evidence and snapshots are
not changed by this refactor. `model-observations.sqlite` is local state and ignored.

## Validation

```text
python -m compileall -q src
python -m pytest -q
python scripts/mcp_smoke.py
python scripts/export_web_schema.py
npm run types --prefix web
node --experimental-strip-types --test web/tests/*.test.mjs
npm run build --prefix web
```

Layout tests recursively check package/module shadowing, old imports, dependency
boundaries, inert foundation packages, resource paths, configuration source/frozen
behavior, and headless imports. Existing architecture tests inspect implementations,
not deprecated wrappers. The instruction schema remains derived from the authoritative
CPU registry; a obsolete stdlib-only assertion must not force duplication of opcodes.

Real Windows GX Works2 / MX Component / Win7 certification is separate from
Linux unit tests and Linux PyInstaller smoke. Do not equate the two. Existing
baseline failures must be reported separately from migration regressions; do not
silently deselect them or mark an incomplete suite as passing.


## Call-contract follow-up

The post-layout semantic projection, transport downgrade policy and shared
candidate entry points are documented in [generation call contracts](generation-call-contracts.md).
Shared engineering semantics do not require identical compact/full wire protocols.

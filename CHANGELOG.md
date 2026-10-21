# Release changes

## 0.3.3 — 2026-10-21

- Existing GX Works2 projects can be read and written offline: inspect an
  uploaded GXW or a local path, list its sources, then joint-edit Ladder, ST,
  FBD and SFC changes against an explicit `version_id` before native import.
- MCP exposes `inspect_gxw_project`, `read_gxw_source` and `edit_gxw_project`;
  the Web workbench adds the GXW import flow and source-bound editing views.
- ST source parsing reuses the shared tree-sitter frontend with the shared
  constraint rules and bounded execution; uncovered constructs stay explicit.
- The shared fact-rule chain, native ST plans and thread-safe session storage
  are integrated. The native UI process lease and workspace validation
  buffers are bounded, and interrupted actions stay pending for review.
- Engineering contracts and native evidence are typed; workbench services are
  split and native diagnostics indexed.
- The Japanese text table loads on demand, reducing the initial Web bundle.
- Updated English, Simplified Chinese and Japanese usage documentation.

## 0.3.2 — 2026-10-19

- Direct generation returns a program with implementation notes and optional
  adjustments. Notes do not block candidate review, opening or export.
- Input-level facts retain their device addresses across equivalent wording
  and explicit revisions. Historical clarification answers continue directly
  into generation with their original project context.
- Existing compact program responses remain compatible, including providers
  that omit the two explanation arrays.
- Updated English, Simplified Chinese and Japanese usage documentation.

## 0.3.1 — 2026-10-19

- Standalone MCP packaging follows runtime imports and excludes the Web server,
  model SDK, offline PDF ingestion and demo animations. Engineering templates,
  reviewed retrieval resources and the simulator viewer remain included.
- Full replaces the Web-only package and includes Web and MCP executables with
  shared resources. Its MCP supports independent and Web service modes.
- Release builds verify both staged directories and extracted final ZIPs.

## 0.3.0 — 2026-10-19

- Persistent native engineering sessions can continue across MCP calls, with
  explicit source revisions, pending-operation receipts and stage saves.
- Active FBD editing binds nodes and terminals to the current native document.
  ST Program/FB editing, task registration, allocation settings and current
  compiled addresses share the same engineering context.
- Native engineering retains the selected CPU, interface language and project
  encoding through editing, compilation and saving.
- Simulator results include a shared timeline viewer in both standalone MCP
  and Web packages. Missing observations and program correspondence remain
  explicit.
- Updated standalone Windows MCP/Web packages include engineering templates,
  reviewed runtime facts and the active-document helper.

Native engineering requires GX Works2 1.635.0.1.

## 0.2.1 — 2026-10-16

Project-authored Core, Web, MCP and native-adapter source is published in the
repository under Apache-2.0. Releases provide independent, ready-to-run MCP and
Web executable ZIPs, with GXW engineering templates, the reviewed runtime fact
corpus and original examples. This includes 57 CPU-specific blank FBD projects.
Previously distributed copies retain their original license.

Recent application changes in this source snapshot include:

- More focused RAG selection and context cleaning; the prompt no longer carries
  redundant copies of structured facts and matching manual text.
- Direct generation now collects related clarification answers in separate
  fields and uses a stricter Ladder response schema and local format handling.
- Ladder export limits oversized parallel branch groups during merge.
- Expanded native FBD/SFC project editing, project inspection and read-only
  simulator views, within the CPU and source-project capabilities implemented
  by the code.
- Updated Web views and the shared MCP/Web service contracts for those flows.

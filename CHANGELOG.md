# Release changes

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

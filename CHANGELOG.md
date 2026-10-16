# Release changes — 2026-10-16

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

Original manual PDFs and manual-text indexes, private
reverse-engineering evidence and internal development documents are excluded.
The source and frontend checks do not establish native import, compilation or
PLC behavior for every supported model.

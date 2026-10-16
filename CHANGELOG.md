# Release changes — 2026-10-16 source publication

This release publishes the project-authored Core, Web, MCP and native-adapter
source under Apache-2.0. It includes the GXW engineering templates needed to
create and edit native projects, including 57 CPU-specific blank FBD projects.
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

Manual PDFs, the generated knowledge index and instruction catalog, private
reverse-engineering evidence and internal development documents are excluded.
The source and frontend checks do not establish native import, compilation or
PLC behavior for every supported model.

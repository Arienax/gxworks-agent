# GXWorks Agent

[简体中文](README.zh-CN.md) | English

> **AI-native engineering workbench and agent runtime for Mitsubishi MELSEC PLC development.**

![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6.svg)
![PLC](https://img.shields.io/badge/focus-FX3U-orange.svg)
![Status](https://img.shields.io/badge/status-active%20development-yellow.svg)

<p align="center">
  <img src="resources/assets/demo.gif" alt="GXWorks Agent demo" width="1200">
</p>

**GXWorks Agent** is an experimental PLC engineering workbench focused on **FX3U + GX Works2**. It combines natural-language requirement analysis, confirmed control specifications, local PLC knowledge retrieval, model-assisted Ladder generation, deterministic validation, PLC IR, versioned project state, GX Works2 integration, and a shared engineering runtime for built-in models and MCP clients such as Codex.

The project follows one rule: **LLM output is a candidate, not an engineering result.** Engineering facts, instruction limits, validation, project state, versioning, and external side effects remain application-owned.

---

## What is distinctive

### SQLite evidence + deterministic instruction contracts

The local knowledge layer uses a packaged **SQLite schema-v3 index** rather than placing whole manuals into the prompt. Runtime retrieval combines structured instruction/device/error/debug records, entity lookup, BM25/FTS5, local dense LSA retrieval, source priority, and task/PLC scoping.

The knowledge layer is deliberately separate from `instruction_registry`:

- **SQLite** answers: what engineering evidence is relevant to this task?
- **Instruction registry** answers: what instruction forms are actually allowed, and how should deterministic code interpret them?

The registry is shared by generation, validation, import, and PLC IR analysis. CPU support, operand count/roles, read/write semantics, and Mitsubishi D/P instruction forms are kept there. The allowed `APP_INSTR` opcode set used by generation is derived from the same registry that validation later enforces.

### Task-shaped prompt assembly

`plc_generation_context.py` assembles the model context for the current operation instead of replaying one large historical prompt. A normal Ladder generation request can include the confirmed specification, current program/edit scope, targeted SQLite evidence, specialist context when needed, and a machine-readable output schema derived from the instruction contract.

Repair paths intentionally receive less context: contract repair is restricted to the failed baseline and allowed scope, while format repair does not receive PLC knowledge context at all.

### Engineering core, not just code generation

Validated candidates are converted into PLC IR and stored as versioned project state. The same engineering layer supports Diff, scoped changes, bounded repair, deterministic artifact generation, GX Works2 CSV operations, and external MCP clients. Built-in models and Codex therefore do not use separate PLC implementations.

---

## Current capability status

| Area | Current status |
| --- | --- |
| Confirmed-spec Ladder generation and editing | ✅ Available |
| SQLite-backed PLC knowledge retrieval | ✅ Available |
| Instruction registry + CPU-scoped generation contract | ✅ Available |
| Task-shaped prompt/context assembly | ✅ Available |
| PLC IR, validation, Diff, versioning and bounded repair | ✅ Available |
| GX Works2 Ladder CSV import / export / synchronization | ✅ Available |
| Model-free CSV re-export from saved PLC IR | ✅ Available |
| Web engineering workbench | ✅ Available |
| MCP server / Codex integration | ✅ Available |
| Simulation and test-plan tooling | 🧪 Experimental |
| Structured Ladder / FBD native-format generation | 🧪 **Block-level research only**: currently limited to generating a single supported block from a small set of verified templates; complete program generation is not supported |
| GXW native-format reverse engineering | 🧪 Research in progress; coverage is limited to known structures and reproducible evidence |
| Native GXW compilation | 🚧 Not implemented as a general compile workflow |
| Physical PLC read-only observation | 🔧 Lower-level read-only helper exists, but it is **not integrated into the current Web GUI** |
| Physical PLC write path | ❌ Not exposed |
| GX Works3 adapter | 📋 Planned |

“Experimental” means there is bounded implementation or reproducible research evidence. It does **not** mean a complete user-facing workflow or general support for arbitrary programs.

---

## GX Works2 path

The most mature end-to-end path is currently **Ladder → PLC IR → GX Works2 CSV**.

Current work includes Ladder/device-comment CSV generation, import/export synchronization, conflict protection, model-free re-export from saved IR, and deterministic lowering for supported Ladder structures that exceed GX Works2 native CSV layout constraints.

A locally validated or exported version does not imply successful native GX compilation, simulator execution, or physical PLC execution.

---

## Native GXW / Structured Ladder / FBD research

Native-format work is still reverse-engineering research, not a complete programming backend.

Current evidence covers selected GX Works2 Structured Ladder/FBD records and a small number of known block/object templates. The implementation can generate or rewrite **individual supported blocks** under known layouts, but it cannot yet generate a complete Structured Ladder/FBD program, arbitrary network topology, or general IEC FBD graph.

Research artifacts and evidence are kept under [`docs/research/`](docs/research/).

---

## Quick start

### Windows Web workbench

Extract the packaged `GXWorks-Agent-Web` directory and run:

```text
start-web.cmd
```

Choose a workspace and open the Web workbench. See [Web integration](docs/integrations/web.md) for operation approvals, workspace behavior, and source/runtime details.

### Source installation

```powershell
git clone https://github.com/Arienax/gxworks-agent.git
cd gxworks-agent

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements/web.txt

.\build-web.bat --no-pause

$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m integrations.web --workspace "D:\PLCWorkspaces\my-workspace" --port 8765 --open-browser
```

Run tests with:

```powershell
pytest -q
```

### Codex / MCP

Start GXWorks Agent Web, open a PLC project, then use **Settings → Model → Integrations / MCP** to connect Codex. External MCP clients use the same project state, retrieval policy, generation contract, candidate pipeline, and PLC IR as the built-in workflow.

See [Codex integration](docs/integrations/codex.md) and [MCP integration](docs/integrations/mcp.md).

---

## Documentation

- [PLC knowledge engine](resources/knowledge/README.md)
- [Web workbench](docs/integrations/web.md)
- [MCP integration](docs/integrations/mcp.md)
- [Codex integration](docs/integrations/codex.md)
- [GXW / Structured Ladder / FBD research](docs/research/)

---

## Safety and scope

GXWorks Agent is under active development. Generated PLC logic must be reviewed and validated against the actual CPU, wiring, machine behavior, safety circuit, operating mode, and applicable standards before deployment.

Safety-critical functions such as emergency stop, guarding, motion limits, pressure, and temperature protection must not rely solely on generated application logic or software simulation.

---

## License

Licensed under the [Apache License 2.0](LICENSE).

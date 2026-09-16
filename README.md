# GXWorks Agent

[简体中文](README.zh-CN.md) | English

> **AI-native engineering workbench and agent runtime for Mitsubishi MELSEC PLC development.**  
> Natural language → confirmed control specification → SQLite-backed PLC knowledge retrieval → bounded candidate processing → PLC IR → GX Works2 / GXW → simulation and validation evidence.

![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6.svg)
![PLC](https://img.shields.io/badge/demo-FX3U-orange.svg)
![Status](https://img.shields.io/badge/status-active%20development-yellow.svg)

<p align="center">
  <img src="resources/assets/demo.gif" alt="GXWorks Agent demo" width="1200">
</p>

**GXWorks Agent** is an experimental AI-native engineering workbench for Mitsubishi MELSEC PLC development. It combines confirmed engineering specifications, a local SQLite/FTS5 hybrid PLC knowledge engine, model-assisted generation, deterministic candidate processing, PLC IR, versioned project state, GX Works2 integration, simulation/debug workflows, and a shared engineering runtime for built-in and external AI agents.

The project follows one core rule: **LLM output is not an engineering result by itself.** Models interpret requirements and propose programs or actions; GXWorks Agent owns engineering state, verified instruction/device contracts, retrieval authority, structural acceptance, change scope, versioning, approvals, evidence, artifact generation, and external side effects.

The current implementation is focused on **FX3U + GX Works2**. Ladder CSV is the most mature backend. Native **GXW / Structured Ladder / FBD** remains an evidence-backed experimental track.

> Development snapshot: **2026-09-16**. Status labels below are intentionally conservative.

---

## Why this project is different

A basic LLM PLC workflow often looks like:

```text
Prompt → model → PLC code
```

GXWorks Agent keeps an explicit engineering pipeline:

```text
Natural-language requirement
          ↓
Requirement analysis
          ↓
Confirmed control specification
          ↓
Local SQLite knowledge retrieval
          ↓
Model / external agent candidate
          ↓
Deterministic compatibility + structural acceptance
          ↓
PLC IR
          ↓
Versioned project + Diff
      ┌────────┼─────────┐
      ▼        ▼         ▼
 GX Works2  Simulation  Review / evidence
```

The model is only one component. Knowledge, project state, validation, versioning and side effects remain application-owned.

---

## Current capability status

| Area | Status |
| --- | --- |
| Confirmed-spec natural-language analysis and Ladder generation | ✅ Available |
| SQLite schema-v3 PLC knowledge base | ✅ Available |
| Exact structured lookup + entity + BM25/FTS5 + local dense retrieval | ✅ Available |
| Task/model scoped retrieval, source priority and deterministic reranking | ✅ Available |
| Curated architecture-design knowledge routed through SQLite | ✅ Available |
| FX3U instruction/device/error/debug structured stores | ✅ Available |
| Program Explorer, address/comment search and reference navigation | ✅ Available |
| PLC IR, structural acceptance, static inspection, Diff and versioning | ✅ Available |
| Compact Ladder candidates with deterministic local expansion | ✅ Available |
| Rejected-candidate preservation and bounded local repair | ✅ Available |
| GX Works2 Ladder CSV import / export / synchronization | ✅ Available |
| Fresh model-free CSV re-export from saved PLC IR | ✅ Available |
| Native CSV lowering for supported oversized Ladder structures | ✅ Available |
| Local Web engineering workbench and persistent jobs | ✅ Available |
| Built-in engineering agent and structured Tool Runtime | ✅ Available |
| Standalone MCP server and Web service bridge | ✅ Available |
| Codex App MCP onboarding and client-activity visibility | ✅ Available |
| Multiple built-in model profiles plus custom OpenAI-compatible profiles | ✅ Available |
| Model discovery plus tool-calling / JSON-object capability probes | ✅ Available |
| Editable simulation workbench and test-plan workflow | 🧪 Experimental |
| GX Simulator2 automated execution and evidence capture | 🧪 Experimental |
| Evidence-bound debug planning and scoped patching | 🧪 Experimental |
| Read-only physical PLC observation for advanced maintenance | 🧪 Experimental |
| GXW inspection and controlled round-trip writing | 🧪 Experimental |
| Structured Ladder / FBD generation and editing | 🧪 Experimental |
| Structured Text generation | 🧪 Experimental |
| Native automated GXW compile command | 🚧 Not complete |
| Full arbitrary GXW / IEC FBD support | 🚧 Not complete |
| GX Works3 adapter | 📋 Planned |
| General physical PLC write path | 📋 Not exposed by the current Web workflow |

“Experimental” means a bounded implementation and test evidence exist. It does **not** imply universal production support across arbitrary PLC programs, GX Works versions, CPU families, libraries, or Windows environments.

---

# SQLite-backed PLC knowledge engine

This is currently one of the most important architectural pieces in GXWorks Agent.

Instead of placing full manuals into prompts or relying on a remote vector database, the project ships a **local schema-v3 SQLite knowledge index**. Runtime retrieval combines structured engineering records with lexical and local dense retrieval.

```text
Mitsubishi manuals             Curated design knowledge        gxw2-skill support corpus
       │                                │                              │
       ├─ PDF/layout/table parsing      ├─ analysis-only patterns      ├─ Markdown/examples
       │                                │                              │
       ├─ authoritative stores          │                              │
       │   ├─ instructions              │                              │
       │   ├─ instruction_aliases       │                              │
       │   ├─ device_records            │                              │
       │   ├─ error_records             │                              │
       │   └─ debug_cases               │                              │
       │                                │                              │
       └──────────────────────────── schema-v3 SQLite ─────────────────┘
                                  ├─ chunks
                                  ├─ entity_index
                                  ├─ FTS5
                                  └─ vector metadata
                                          │
                                          ▼
                                 local NumPy LSA index
```

The bundled runtime does not need to parse PDFs or build embeddings during normal startup. Those operations belong to the offline knowledge build pipeline.

## Runtime retrieval pipeline

```text
Query understanding
  ├─ exact instruction / device / error / debug lookup
  ├─ entity lookup
  ├─ BM25 / SQLite FTS5
  └─ lazy local dense search
             ↓
   weighted reciprocal-rank fusion
             ↓
 deterministic cross-signal reranker
             ↓
 source priority + PLC/task scope
             ↓
      final character budget
```

Important retrieval rules:

- authoritative Mitsubishi structured stores remain authoritative for instruction/device facts;
- the bundled `gxw2-skill` corpus is a **supporting** source rather than a replacement for official manuals;
- curated control-architecture patterns are stored in the same SQLite system but are scoped to `analysis` tasks rather than treated as instruction facts;
- exact opcode/device evidence is preferred before general full-text matches;
- source precedence is explicit, so supporting examples do not silently outrank audited official facts;
- retrieval is model-independent and is shared by the built-in model workflow and MCP/external-agent workflow.

## Runtime SQLite optimizations

`src/knowledge_retriever_core.py` is designed around low-latency local retrieval:

- SQLite is not opened until the first real retrieval request;
- each calling thread keeps its own reused connection and schema snapshot;
- the packaged database is opened with `mode=ro&immutable=1`;
- `PRAGMA query_only=ON` prevents accidental writes;
- temporary work is kept in memory with `PRAGMA temp_store=MEMORY`;
- the reader requests a 256 MiB SQLite memory map where supported;
- repeated retrievals use a bounded LRU cache;
- optional dense artifacts are loaded lazily rather than during application startup;
- model/task filtering and candidate limits keep retrieval bounded before final reranking.

This gives the workbench a local knowledge layer without requiring a server-side vector database or cloud embedding call for normal bundled retrieval.

## Current packaged knowledge snapshot

The repository manifest currently records approximately:

| Metric | Current snapshot |
| --- | ---: |
| SQLite database size | ~141 MB |
| Knowledge chunks | 4,714 |
| Entity index rows | 67,835 |
| Structured instructions | 285 |
| Instruction aliases | 905 |
| Device records | 2,184 |
| Error records | 376 |
| Debug cases | 26 |
| Dense dimensions | 192 |
| Dense features | 8,192 |
| Main benchmark Recall@10 | 1.0000 |
| Main benchmark Recall@5 | 0.9853 |
| Main benchmark negative accuracy | 1.0000 |
| Recorded mean retrieval latency | ~125 ms |

These benchmark numbers are repository build/evaluation results, not a latency guarantee for every computer.

See [the knowledge-base documentation](resources/knowledge/README.md) for sources, rebuilding, benchmark commands, authority rules and the third-party corpus policy.

---

# One engineering core, multiple AI entry points

Built-in models and external agents intentionally converge on the same engineering layer.

```text
                 ┌──────────────────────────────┐
                 │ Built-in ModelProvider       │
                 │ DeepSeek / GLM / compatible │
                 └──────────────┬───────────────┘
                                │
External AI agents              │
Codex / MCP clients             │
          │                     │
          ▼                     ▼
        MCP            Shared generation context
          │       spec / current program / SQLite RAG
          └────────────────┬────────────────────┘
                           ▼
                  Candidate preparation
            compatibility / scope / structure
                           ▼
                         PLC IR
                           ▼
                 Tool Runtime / PLC Core
              ┌────────────┼────────────┐
              ▼            ▼            ▼
          GX Works2     Simulator     GXW / FBD
```

Normal Ladder generation and editing share the same confirmed specification, retrieval policy, compatibility normalization, structural acceptance, PLC IR construction, artifact rendering and versioned project state whether the candidate comes from the built-in API or an MCP client.

---

# Model profiles and capability detection

The current configuration layer uses an OpenAI-compatible transport with multiple model profiles. Bundled project presets currently include:

| Provider family | Bundled profiles |
| --- | --- |
| DeepSeek | `deepseek-v4-pro`, `deepseek-v4-flash`, `deepseek-v4-flash-vision-exp` |
| Zhipu GLM | `glm-5.3-flash`, `glm-5.3`, `glm-5.2` |
| Custom | user-defined OpenAI-compatible endpoint/model |

The settings workflow can discover model IDs and actively probe capabilities that are safe to test generically:

- tool/function calling;
- JSON-object structured output.

Reasoning support, reasoning intensity, multimodal semantics and provider-specific thinking controls are **not** guessed from a generic text request. They remain explicit profile/provider configuration unless a dedicated probe can establish them safely.

```text
model capability      = what a provider/model can support
profile configuration = what GXWorks Agent currently requests
runtime probe          = what can be safely verified automatically
```

A failed best-effort probe does not automatically erase a known-good configured capability.

---

# Confirmed specification and generation pipeline

GXWorks Agent does not assume that the first prompt is a complete PLC requirement.

The analysis stage can produce a reviewable specification containing:

- requirement summary;
- candidate programming architectures;
- clarification questions and selectable answers;
- parameters and suggested defaults;
- I/O assignment;
- explicit user rules and notes.

Architecture-selection knowledge is retrieved from the SQLite knowledge layer rather than being hardcoded as a long prompt block.

After confirmation, Ladder generation follows the current compact path:

```text
Confirmed specification
        ↓
SQLite-backed generation context
        ↓
Compact Ladder candidate
        ↓
Compatibility / syntax normalization
        ↓
Structural + device/address acceptance
        ↓
PLC IR
        ↓
JSON / ST / SVG / CSV artifacts
        ↓
Versioned project + Diff
```

The generation agent is isolated from free-form analysis prose so confirmed requirements, not the previous conversation narrative, define the generation contract.

---

# Rejected candidates and bounded repair

Generation failures are retained when possible instead of disappearing as opaque model errors. The Web workbench can preserve a rejected Ladder preview and diagnostics for inspection or repair.

The current repair policy is deliberately narrow:

- validator evidence identifies what may be repaired;
- path-addressed field patches are preferred over full-object rewrites;
- deterministic syntax/format recovery stays local;
- instruction semantics remain frozen during structure-only repair;
- unsupported opcode/device guessing is not a generic fallback;
- blocked repair keeps the original validation reason;
- repaired candidates are fully revalidated before normal delivery.

This keeps “repair” from becoming a hidden semantic regeneration loop.

---

# PLC Intermediate Representation

The internal **PLC IR** is the semantic layer between model output and engineering operations.

```text
                    ┌─ Ladder CSV
                    ├─ Structured Text
AI → candidate → IR ┼─ SVG preview
                    ├─ static inspection
                    ├─ Diff / scoped change analysis
                    ├─ test planning
                    └─ GX Works2 adapters
```

Saved IR can be reused without another model call. This is used by the Web workbench to re-render diagrams and export a **fresh GX Works2 CSV bundle directly from a saved version**.

---

# GX Works2 Ladder integration

The most mature GX Works2 backend is still the **Ladder CSV workflow**.

Current capabilities include:

- Ladder CSV and device-comment CSV generation;
- GX Works2 import / export;
- program/comment synchronization;
- backup before overwrite;
- synchronization baselines and external-edit conflict protection;
- optional round-trip verification;
- model-free fresh CSV export from saved PLC IR;
- deterministic lowering of supported oversized Ladder structures into GX Works2-native CSV constraints.

Recent work tightened native CSV rendering. Supported oversized OR-style structures are split/lowered before export so native Ladder blocks remain within the expected GX Works2 row/step constraints. Supported instruction widths are also modeled explicitly for export.

A local saved version does **not** imply successful native GX compilation, simulation, or physical PLC execution.

---

# Native GXW / Structured Ladder / FBD track

GXWorks Agent also contains an experimental native **GXW** pipeline based on controlled reverse engineering.

For selected known layouts it can inspect `Program.pou` records, preserve unknown records, edit known declarations, generate supported Structured Ladder/FBD objects and wires, synchronize supported Function Block instances, update known metadata, and import/preview/version/download controlled GXW candidates through the Web workbench.

Evidence-backed templates currently include selected contacts, coils, terminals, `MOV`, `TON`, `TON_E`, `CTU`, `CTU_E`, and selected saved Function / Function Block ABI patterns.

This is still bounded research. Arbitrary libraries, unrestricted IEC FBD graphs, every GX Works2 project variant and a general native compile command are not yet supported.

Research evidence is kept under `docs/research/`.

---

# Simulation, debug and hardware boundaries

The workbench includes editable test plans, issue-to-network-to-test traceability and experimental GX Simulator2 automation/evidence capture.

Debugging remains evidence-bound and scoped rather than unrestricted regeneration.

Physical PLC access is intentionally narrower. The advanced maintenance path supports bounded **read-only observation** where configured; a general physical PLC write path is not exposed by the current Web workflow.

---

# Quick start

## Windows Web workbench

For a packaged Windows build, extract the complete `GXWorks-Agent-Web` directory and run:

```text
start-web.cmd
```

Choose a workspace. In **Settings → General → Operation approvals**, choose **Ask for approval** (default), **Approve for me**, or **Full access** for supported external GX/simulation/debug actions. Validation itself remains enabled in every mode.

Starting the workbench does **not** automatically start GX Works2, GX Simulator2, a simulator gateway, or a physical PLC connection.

See [Web workbench guide](docs/integrations/web.md).

## Connect Codex through MCP

1. Start GXWorks Agent Web and open a PLC project.
2. Go to **Settings → Model → Integrations / MCP**.
3. Click **Connect Codex**.
4. Restart Codex App and create a new task.
5. Ask for the engineering task directly.

Codex uses the same selected project, confirmed specification, SQLite-backed retrieval layer, candidate pipeline and PLC IR as the built-in workflow.

See [Codex integration](docs/integrations/codex.md) and [MCP integration](docs/integrations/mcp.md).

## Source installation

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

API keys are stored through Windows Credential Manager rather than committed into repository configuration files.

---

# Repository layout

```text
src/                    Python engineering core and runtime
web/                    React/Vite Web workbench
resources/knowledge/    packaged SQLite/FTS5 knowledge index and manifest
tools/                  knowledge builders, importers, audits and engineering tools
hardware_reader/        bounded hardware observation helper
docs/                   integration, architecture and research documentation
docs/research/          GXW / Structured Ladder / FBD reverse-engineering evidence
benchmarks/              RAG and engineering benchmark assets
packaging/               Windows packaging support
requirements/            runtime dependency sets
```

Useful documentation:

- [Local PLC knowledge engine](resources/knowledge/README.md)
- [Web workbench](docs/integrations/web.md)
- [MCP integration](docs/integrations/mcp.md)
- [Codex integration](docs/integrations/codex.md)

---

# Development principles

- engineering state lives in the application, not in chat history;
- retrieval authority is explicit: official engineering evidence outranks supporting examples;
- model output must cross deterministic acceptance boundaries;
- repair is bounded by evidence rather than semantic guessing;
- saved artifacts should remain reproducible without another model call where possible;
- external side effects remain policy/approval controlled;
- unknown GXW structures are preserved rather than fabricated;
- software validation is not reported as native GX or hardware validation.

---

# Roadmap

Near-term work is concentrated on:

- extending and auditing SQLite-backed FX3U knowledge without weakening source authority;
- improving retrieval/ranking and provider-independent generation context;
- expanding verified instruction/device contracts;
- improving model/provider capability resolution while keeping provider-specific controls explicit;
- strengthening GX Works2 CSV/native evidence workflows;
- extending bounded GXW / Structured Ladder / FBD coverage from reproducible evidence;
- improving simulation/diagnostic traceability;
- continuing consolidation around the Web workbench.

---

# Safety and scope

GXWorks Agent is engineering software under active development. Generated PLC logic must be reviewed and validated for the actual machine, CPU, wiring, safety circuit, operating mode, and applicable standards before deployment.

Emergency-stop, guarding, interlock, motion, pressure, temperature, and other safety-critical functions must not rely solely on generated application logic or software simulation.

---

# License

Licensed under the [Apache License 2.0](LICENSE).

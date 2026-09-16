# GXWorks Agent

[简体中文](README.zh-CN.md) | English

> **AI-native engineering workbench and agent runtime for Mitsubishi MELSEC PLC development.**  
> Natural language → confirmed control specification → SQLite evidence retrieval + CPU-scoped instruction contract → task-shaped prompt assembly → bounded candidate processing → PLC IR → GX Works2 / GXW → simulation and validation evidence.

![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6.svg)
![PLC](https://img.shields.io/badge/demo-FX3U-orange.svg)
![Status](https://img.shields.io/badge/status-active%20development-yellow.svg)

<p align="center">
  <img src="resources/assets/demo.gif" alt="GXWorks Agent demo" width="1200">
</p>

**GXWorks Agent** is an experimental AI-native engineering workbench for Mitsubishi MELSEC PLC development. Its current architecture is built around three cooperating layers: a local **SQLite/FTS5 PLC evidence store**, a deterministic **instruction registry / generation contract**, and a **task-shaped prompt assembler** that injects only the engineering context needed for the current operation. Model-assisted generation, PLC IR, versioned project state, GX Works2 integration, simulation/debug workflows, and MCP clients all sit above the same engineering core.

The project follows one core rule: **LLM output is not an engineering result by itself.** Models interpret requirements and propose programs or actions; GXWorks Agent owns engineering state, retrieval authority, instruction/device contracts, output schemas, structural acceptance, change scope, versioning, approvals, evidence, artifact generation, and external side effects.

The current implementation is focused on **FX3U + GX Works2**. Ladder CSV is the most mature backend. Native **GXW / Structured Ladder / FBD** remains an evidence-backed experimental track.

> Development snapshot: **2026-09-16**. Status labels below are intentionally conservative.

---

## Why this project is different

A basic LLM PLC workflow often looks like:

```text
Prompt → model → PLC code
```

GXWorks Agent instead separates **engineering evidence**, **executable instruction contracts**, and **model context assembly**:

```text
Natural-language requirement
          ↓
Requirement analysis
          ↓
Confirmed control specification
          ↓
┌─────────────────────────────────────────────────────┐
│ Task-shaped generation context                      │
│                                                     │
│ SQLite evidence              Instruction registry   │
│ manuals / devices /          CPU support / arity /  │
│ errors / debug / design      operands / D-P forms   │
│          │                            │              │
│          └──────────┬─────────────────┘              │
│                     ↓                                │
│ semantic kernel + confirmed spec + output schema    │
│ + current program/edit scope + targeted evidence    │
└─────────────────────┬───────────────────────────────┘
                      ↓
             Model / external agent
                      ↓
               Candidate program
                      ↓
     Same instruction contract + validators
                      ↓
                    PLC IR
                      ↓
         Versioned project + Diff
            ┌─────────┼─────────┐
            ▼         ▼         ▼
       GX Works2  Simulation  Review/evidence
```

The model is therefore not asked to remember the Mitsubishi instruction set, infer CPU support from prose, or reconstruct the complete project from chat history. Evidence and executable constraints are application-owned and injected according to the current task.

---

## Current capability status

| Area | Status |
| --- | --- |
| Confirmed-spec natural-language analysis and Ladder generation | ✅ Available |
| SQLite schema-v3 PLC knowledge base | ✅ Available |
| Exact structured lookup + entity + BM25/FTS5 + local dense retrieval | ✅ Available |
| Task/model-scoped retrieval, source priority and deterministic reranking | ✅ Available |
| Curated architecture-design knowledge routed through SQLite | ✅ Available |
| Structured instruction / device / error / debug evidence stores | ✅ Available |
| Shared Mitsubishi instruction registry for generation, validation, import and IR | ✅ Available |
| CPU-scoped APP_INSTR opcode contract generated from the registry | ✅ Available |
| Task-shaped prompt/context assembly with auditable included/excluded sections | ✅ Available |
| Repair-specific context reduction instead of replaying full generation context | ✅ Available |
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

The local SQLite knowledge layer is one of the central architectural components of GXWorks Agent.

Instead of putting complete manuals into prompts or depending on a remote vector database, the project ships a **schema-v3 SQLite knowledge index** that combines authoritative structured records with searchable text and local dense retrieval.

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

The bundled runtime does not parse PDFs or build embeddings during normal startup. PDF parsing, third-party imports, structured extraction and dense-index construction belong to the offline build pipeline.

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
- the same retrieval policy is shared by the built-in generation path and MCP/external-agent path.

## Runtime SQLite optimizations

`src/knowledge_retriever_core.py` is built for low-latency local retrieval:

- SQLite is opened only on the first real retrieval request;
- each calling thread keeps its own reused connection and schema snapshot;
- the packaged database is opened with `mode=ro&immutable=1`;
- `PRAGMA query_only=ON` prevents accidental writes;
- temporary SQLite work is kept in memory;
- the reader requests a 256 MiB memory map where supported;
- repeated retrievals use a bounded LRU cache;
- dense artifacts are loaded lazily rather than during application startup;
- model/task filtering and bounded candidate sets happen before final reranking.

This provides a local knowledge layer without requiring a server-side vector database or cloud embedding call during normal bundled retrieval.

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

These are repository build/evaluation results, not latency guarantees for every machine.

See [the knowledge-base documentation](resources/knowledge/README.md) for sources, rebuilding, benchmark commands, authority rules and third-party corpus policy.

---

# Instruction registry: evidence is not the executable contract

SQLite and the instruction registry deliberately solve different problems.

**SQLite answers:** “What engineering evidence is relevant to this task?”  
**The instruction registry answers:** “What instruction forms may the program actually emit and how are they interpreted by deterministic code?”

`src/instruction_registry.py` is a data-driven Mitsubishi instruction catalogue shared across **generation, validation, import and PLC IR analysis**. It centralizes metadata such as:

- mnemonic and canonical operation;
- instruction category / semantic kind;
- operand count and operand roles;
- read / write / read-write operand positions;
- allowed device prefixes where known;
- CPU support;
- contract level;
- Mitsubishi D/P modifier forms and generated variants.

The same registry is used to derive the generation subset for the selected CPU. Typed outputs such as ordinary coils/timers/counters remain dedicated Ladder node types; application instructions are restricted to the CPU-compatible `APP_INSTR` set rather than being accepted as arbitrary strings.

Unknown imported vendor instructions can still be represented conservatively for round-trip purposes, but GXWorks Agent does not guess missing write targets or semantics.

This separation matters because manual retrieval is probabilistic/ranked evidence, while instruction legality and operand semantics must remain deterministic.

---

# Prompt assembly: minimum task-shaped context

`src/plc_generation_context.py` is the bridge between the evidence layer and the deterministic contract layer.

The current prompt architecture does **not** replay one large historical PLC prompt for every request. It assembles context according to the current task:

```text
Confirmed specification
        │
        ├───────────────┐
        │               │
        ▼               ▼
SQLite retrieval   Instruction registry
relevant evidence  CPU-scoped opcode set
        │               │
        │               ▼
        │        machine-readable Ladder schema
        │               │
        └───────┬───────┘
                ▼
      compact semantic kernel
                +
      confirmed spec projection
                +
      current program / edit scope
                +
      targeted specialist delta
                +
      retrieved evidence budget
                ↓
          model / MCP client
```

For Ladder generation, `plc_generation_contract.py` embeds a machine-readable output schema into the system context. When a PLC model is known, the `APP_INSTR.opcode` field is populated from `generation_app_instr_mnemonics(plc_model)`, so the model sees the same CPU-scoped instruction subset that deterministic validation later enforces.

The authority order is explicit: output schema and the current requested change come before the confirmed specification and generation contract; PLC-model evidence and specialist context support them rather than overriding them.

The assembler also keeps context **task-shaped**:

- normal generation receives one compact semantic kernel plus targeted SQLite evidence;
- analysis can retrieve architecture-selection knowledge from SQLite instead of carrying a long hardcoded design prompt;
- edit mode adds the current program/change scope rather than rebuilding unrelated state;
- contract repair receives the immutable failed baseline and allowed repair scope, not the normal knowledge bundle;
- format repair receives no PLC knowledge context and is limited to restoring parseable JSON structure;
- optional specialist prompt deltas are bounded and only injected for matched domains such as motion/VFD/SFC workflows;
- included/excluded context sections are auditable through the prompt-context policy layer.

The practical goal is to make prompt size and authority predictable: **retrieve facts from SQLite, derive executable limits from the registry, and inject only what the current task needs.**

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
          │     spec / SQLite evidence / schema / scope
          └────────────────┬────────────────────┘
                           ▼
                  Candidate preparation
            compatibility / scope / structure
                           ▼
                  registry + validators
                           ▼
                         PLC IR
                           ▼
                 Tool Runtime / PLC Core
              ┌────────────┼────────────┐
              ▼            ▼            ▼
          GX Works2     Simulator     GXW / FBD
```

Normal Ladder generation and editing therefore use the same confirmed specification, retrieval policy, instruction contract, compatibility normalization, structural acceptance, PLC IR construction, artifact rendering and versioned project state whether the candidate comes from the built-in API or an MCP client.

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

The analysis stage can produce a reviewable specification containing requirement summary, candidate programming architectures, clarification choices, parameters, I/O assignment and explicit user rules.

Architecture-selection knowledge is retrieved through the SQLite layer rather than being duplicated as a long permanent system prompt.

After confirmation, Ladder generation follows the current compact path:

```text
Confirmed specification
        ↓
Task-shaped prompt assembly
 SQLite evidence + registry-derived schema
        ↓
Compact Ladder candidate
        ↓
Compatibility / syntax normalization
        ↓
Registry-backed structural/device/instruction acceptance
        ↓
PLC IR
        ↓
JSON / ST / SVG / CSV artifacts
        ↓
Versioned project + Diff
```

The generation agent is isolated from free-form analysis prose so confirmed engineering facts, not the previous conversation narrative, define the generation contract.

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

The internal **PLC IR** is the semantic layer between accepted candidates and engineering operations.

```text
                              ┌─ Ladder CSV
                              ├─ Structured Text
model → accepted candidate → IR ┼─ SVG preview
                              ├─ static inspection
                              ├─ Diff / scoped change analysis
                              ├─ test planning
                              └─ GX Works2 adapters
```

Instruction read/write behavior and known semantics come from the same application-owned contracts rather than being re-inferred from model prose.

Saved IR can be reused without another model call. The Web workbench can therefore re-render diagrams and export a **fresh GX Works2 CSV bundle directly from a saved version**.

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

Recent work tightened native CSV rendering. Supported oversized OR-style structures are split/lowered before export so native Ladder blocks remain within expected GX Works2 row/step constraints. Supported instruction widths are also modeled explicitly for export.

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

Codex uses the same selected project, confirmed specification, SQLite evidence layer, registry-derived instruction contract, prompt assembly policy, candidate pipeline and PLC IR as the built-in workflow.

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
src/                    Python engineering core, prompt assembly and runtime
web/                    React/Vite Web workbench
resources/knowledge/    packaged SQLite/FTS5 knowledge index and manifest
resources/instructions/ deterministic Mitsubishi instruction catalogues
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
- SQLite retrieval provides ranked engineering evidence, not executable authority;
- the instruction registry provides deterministic executable contracts, not prose knowledge;
- prompt assembly is task-shaped and should inject the minimum sufficient context;
- output schemas and validators must agree on the same CPU-scoped instruction subset;
- official engineering evidence outranks supporting examples;
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
- tightening the alignment between SQLite structured evidence and the instruction registry;
- expanding verified instruction/device contracts and CPU-specific generation subsets;
- improving task-shaped prompt assembly, retrieval budgets and prompt-context observability;
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
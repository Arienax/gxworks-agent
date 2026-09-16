# GXWorks Agent

[简体中文](README.zh-CN.md) | English

> **AI-native engineering workbench and agent runtime for Mitsubishi MELSEC PLC development.**  
> Natural language → confirmed control specification → shared generation context → bounded candidate processing → PLC IR → deterministic engineering workflows → GX Works2 / GXW → simulation and validation evidence.

![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6.svg)
![PLC](https://img.shields.io/badge/demo-FX3U-orange.svg)
![Status](https://img.shields.io/badge/status-active%20development-yellow.svg)

<p align="center">
  <img src="resources/assets/demo.gif" alt="GXWorks Agent demo" width="1200">
</p>

**GXWorks Agent** is an experimental AI-native engineering workbench for Mitsubishi MELSEC PLC development. It combines natural-language requirement analysis, confirmed engineering specifications, versioned project state, local PLC knowledge retrieval, model-assisted generation, deterministic candidate processing, PLC IR, GX Works2 integration, simulation/debug workflows, and a shared engineering runtime for built-in and external AI agents.

The project follows one core rule: **LLM output is not an engineering result by itself.** Models may interpret requirements, propose programs, review evidence, or suggest actions. GXWorks Agent owns project state, supported instruction/device contracts, structural acceptance, change scope, versioning, approvals, evidence, artifact generation, and external side effects.

The current implementation is focused on **FX3U + GX Works2**. The Ladder CSV workflow is the most mature backend. Native **GXW / Structured Ladder / FBD** support is an evidence-backed experimental track rather than a claim of general GX Works compatibility.

> Development snapshot: **2026-09-16**. Status labels below are intentionally conservative.

---

## Quick start

### 1. Windows Web workbench

For a packaged Windows build, extract the complete `GXWorks-Agent-Web` directory and run:

```text
start-web.cmd
```

Choose a workspace folder. Validated generation and local edits are stored with version history. In **Settings → General → Operation approvals**, choose **Ask for approval** (default), **Approve for me**, or **Full access**. These modes govern supported GX / simulation / debug side effects; they do not disable PLC validation. Full access requires explicit confirmation. The optional `-ReadOnly` recovery flag remains available.

The toolbar exposes **Export files**, **Read from GX**, **Send to GX**, refresh/redraw, and additional import/conversion/synchronization actions. File export does not require a live GX Works2 connection.

Starting the workbench does **not** automatically start GX Works2, GX Simulator2, a simulator gateway, or a physical PLC connection.

See [Web workbench guide](docs/integrations/web.md) for workspace locking, approvals, source installation, MCP service mode, and Windows integration details.

### 2. Connect Codex through MCP

GXWorks Agent can expose the currently selected PLC project to Codex App through its local MCP engineering interface. Codex CLI is optional.

1. Start GXWorks Agent Web and open a PLC project.
2. Go to **Settings → Model → Integrations / MCP**.
3. Click **Connect Codex**.
4. Restart Codex App and create a new task.
5. Ask for the engineering task directly, for example:

```text
Use gxworks to create a Mitsubishi start/stop latch:
X0 start, X1 stop, Y0 motor.
```

Codex uses the same selected project, confirmed specification, local PLC retrieval policy, candidate pipeline, PLC IR, and engineering boundaries as the built-in workflow. MCP is the engineering interface; optional client prompts or skills are guidance only.

See [Codex integration](docs/integrations/codex.md) and [MCP integration](docs/integrations/mcp.md).

### 3. Source installation

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

`build-web.bat` performs the frontend dependency install, type checks, and production build, then writes the built frontend to `web/dist`.

The retained Qt development entry is also available:

```powershell
python -m pip install -r requirements.txt
python src\main.py
```

Run the Python test suite with:

```powershell
pytest -q
```

A Windows 7 compatibility dependency set is retained in `requirements/win7.txt`.

API keys are stored through Windows Credential Manager rather than committed into repository configuration files.

---

## Current capability status

| Area | Status |
| --- | --- |
| Natural-language analysis and confirmed control specification | ✅ Available |
| Confirmed-spec Ladder generation and partial editing | ✅ Available |
| Compact candidate format with deterministic local expansion | ✅ Available |
| PLC IR, structural acceptance, static inspection, Diff and versioning | ✅ Available |
| Program Explorer, address/comment search and reference navigation | ✅ Available |
| Scoped modifications and affected-network / affected-device summaries | ✅ Available |
| Rejected-candidate preservation and bounded local repair | ✅ Available |
| GX Works2 Ladder CSV import / export / synchronization | ✅ Available |
| Fresh model-free CSV re-export from saved PLC IR | ✅ Available |
| GX Works2 native CSV lowering for supported oversized Ladder shapes | ✅ Available |
| FX3U manual / engineering knowledge retrieval | ✅ Available |
| Verified instruction/device contract registry for supported FX3U operations | ✅ Available |
| Local Web engineering workbench and persistent jobs | ✅ Available |
| Built-in engineering agent and structured Tool Runtime | ✅ Available |
| Standalone MCP server and Web service bridge | ✅ Available |
| Codex App MCP onboarding and client-activity visibility | ✅ Available |
| Multiple built-in model profiles plus custom OpenAI-compatible profiles | ✅ Available |
| Model discovery plus tool-calling / JSON-object capability probes | ✅ Available |
| Editable simulation workbench and test-plan workflow | 🧪 Experimental |
| Issue → network → test traceability | 🧪 Experimental |
| GX Simulator2 automated execution and evidence capture | 🧪 Experimental |
| Evidence-bound debug planning and scoped patching | 🧪 Experimental |
| Read-only physical PLC observation for advanced maintenance | 🧪 Experimental |
| GXW inspection and controlled round-trip writing | 🧪 Experimental |
| Structured Ladder / FBD generation and editing | 🧪 Experimental |
| GXW import, preview, local versioning, download and controlled open | 🧪 Experimental |
| Structured Text generation | 🧪 Experimental |
| Native automated GXW compile command | 🚧 Not complete |
| FBD simulation / diagnostics | 🚧 Not complete |
| Full arbitrary GXW / IEC FBD support | 🚧 Not complete |
| GX Works3 adapter | 📋 Planned |
| Physical PLC write path | 📋 Not exposed by the current Web workflow |

“Experimental” means that a bounded implementation and test evidence exist. It does **not** mean universal production support across arbitrary PLC programs, GX Works versions, CPU families, libraries, or Windows environments.

---

## One engineering core, multiple AI entry points

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
          │       spec / current program / RAG / rules
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

For normal Ladder generation and editing, the built-in API and external MCP clients share the confirmed specification, local retrieval policy, compatibility normalization, structural acceptance, PLC IR construction, artifact rendering, and versioned project state.

External agents therefore do not receive a separate, less-restricted PLC implementation. They can plan and propose; GXWorks Agent remains responsible for deterministic engineering state and execution boundaries.

---

## Model profiles and capability detection

The current configuration layer supports multiple model profiles and an OpenAI-compatible transport. Bundled profiles currently include project presets for:

| Provider family | Bundled model profiles | Notes |
| --- | --- | --- |
| DeepSeek | `deepseek-v4-pro`, `deepseek-v4-flash`, `deepseek-v4-flash-vision-exp` | Reasoning-enabled project presets; the vision profile is marked multimodal |
| Zhipu GLM | `glm-5.3-flash`, `glm-5.3`, `glm-5.2` | Reasoning-enabled project presets with provider-specific generation defaults |
| Custom | User-defined OpenAI-compatible endpoint/model | Capabilities and request overrides remain editable |

The model settings workflow can query the endpoint model list and actively probe capabilities that can be tested safely through a generic OpenAI-compatible request:

- tool/function calling
- JSON-object structured output

The generic detector deliberately does **not** pretend to infer every provider-specific feature. Reasoning support, reasoning intensity, multimodal semantics, and provider-specific thinking controls are preserved from the selected profile or user configuration unless a dedicated adapter/probe can establish them safely.

This distinction is intentional:

```text
model capability       = what the model/provider can support
profile configuration  = what GXWorks Agent currently requests
automatic probe         = what can be verified safely at runtime
```

A failed best-effort probe does not automatically erase a known-good configured capability.

---

## Confirmed specification and generation pipeline

GXWorks Agent does not assume that the first natural-language prompt is a complete PLC requirement.

The analysis stage can produce a reviewable specification containing:

- requirement summary
- candidate programming approaches
- clarification questions and selectable answers
- parameters and suggested defaults
- I/O assignment
- user notes and explicit rules

Generation begins after the specification is confirmed.

The current confirmed-spec Ladder path isolates program generation from free-form analysis prose and uses a compact candidate representation to reduce transport and formatting noise. Built-in models and external agents converge on the same downstream pipeline:

```text
Confirmed control specification
            ↓
Shared generation context
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

The application may deterministically expand supported compact syntax and proven-safe legacy representations. It does not silently invent unsupported instructions or bypass address/instruction validation.

---

## Rejected candidates and bounded repair

A generation failure is not discarded as an opaque chat error. When possible, GXWorks Agent preserves rejected Ladder candidates and diagnostics so the engineer can inspect what the model produced.

Supported formatting/structural failures can enter a bounded local repair path. The current repair policy is deliberately narrower than “ask the model to rewrite everything”:

- repairs are tied to validator evidence
- path-addressed field patches are preferred over full-object rewrites
- deterministic format normalization is kept local
- instruction semantics are frozen during structure-only repair
- blocked repairs preserve the original validation reason
- whole-rung structural relocation is restricted to supported, explicitly diagnosed cases
- opcode/device guessing is not used as a generic fallback

The result is still revalidated and rendered through the normal PLC pipeline before it becomes a normal saved candidate.

---

## PLC Intermediate Representation

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

PLC IR represents networks, instructions, devices, timers/counters, reads/writes, execution triggers, revisions, static findings, I/O mapping, semantic requirements, and deterministic renderable program state.

Saved IR can be reused without asking a model to regenerate the program. This is now used by the Web workbench to provide a **fresh GX Works2 CSV export** directly from a saved version.

---

## Program inspection, editing and versioning

Ordinary edits use the same candidate pipeline as first generation. A model can return only changed/new complete rungs, comment changes, and explicit deletions instead of rewriting an entire project.

```text
Current version
      ↓
Modification request
      ↓
Shared generation context
      ↓
Scoped candidate
      ↓
Structural acceptance
      ↓
Diff / impact summary
      ↓
New version
```

Program Explorer provides network selection, address/comment search, reference navigation, zoom, and model-free redraw. Saved proposals and approvals are version/hash bound so a stale browser tab cannot silently apply a candidate to a newer active version.

Evidence-scoped Debug work remains separate from ordinary editing and keeps stricter network/address scope.

A saved local version does **not** imply that the program has been imported, natively compiled, simulated, or executed on a PLC.

---

## Validation and evidence boundaries

GXWorks Agent separates different levels of evidence:

1. **Structural acceptance** — the candidate can be represented and processed by the supported pipeline.
2. **PLC IR consistency** — the deterministic internal representation is internally valid.
3. **Static engineering review** — local analyzers and optional AI reviewers report risks and findings.
4. **Behavior verification** — concrete input/timing behavior is checked by an executed test or simulation.
5. **Native / hardware verification** — GX Works2, GX Simulator2, or a physical target actually performed the reported operation.

Passing one level is never reported as proof of later levels.

The deterministic review path can inspect supported device/address rules, instruction contracts, timer/counter structure, I/O references, read/write dependencies, multiple writers, latch/reset ownership, timing paths, and other Ladder risks. Optional AI review may add interpretation but does not replace deterministic findings.

---

## GX Works2 Ladder integration

The most mature GX Works2 backend remains the **Ladder CSV import/export workflow**.

Current capabilities include:

- Ladder CSV and device-comment CSV generation
- GX Works2 import / export
- program and comment synchronization
- automatic backup before overwrite
- synchronization baselines and external-edit conflict protection
- optional round-trip verification
- model-free re-export of fresh CSV artifacts from saved PLC IR
- deterministic lowering of supported oversized Ladder structures to GX Works2-native CSV limits

Recent work specifically tightened native CSV rendering for GX Works2. Supported oversized OR-style structures are split/lowered so emitted native Ladder blocks remain within the expected row/step constraints instead of relying on an oversized visual form. Instruction widths used by native export are also tracked explicitly for supported instructions.

If GXWorks Agent detects that a GX Works2 program changed manually after the last synchronization baseline, it stops rather than silently overwriting the engineer’s changes.

Some GX Works2 operations still depend on GUI automation and remain sensitive to GX version, language, desktop state, and Windows session conditions.

---

## Native GXW / Structured Ladder / FBD track

GXWorks Agent contains an experimental native **GXW project pipeline** for GX Works2 Structured Ladder / FBD.

The work is based on controlled reverse-engineering experiments: create/save/modify/reopen known projects, compare binary records and metadata, and promote only reproducible structures into the writer. Unknown structures are preserved rather than guessed.

The current experimental pipeline can, for selected known layouts:

- inspect `Program.pou` records
- preserve unrelated and unknown records
- generate supported FX3U Structured Ladder / FBD objects and wires
- edit known declarations
- keep supported Function Block instance declarations synchronized
- grow required CFB allocations
- update known GXW metadata
- generate FBD/GXW artifacts and write reports
- import GXW into the Web workbench
- preview, version, download, and controlled-open approved copies

Evidence-backed generated templates include normally-open / normally-closed contacts, coils, terminals, `MOV`, `TON`, `TON_E`, `CTU`, `CTU_E`, and selected saved Function / Function Block ABI templates.

Important limitations remain: arbitrary custom libraries, unrestricted IEC FBD graphs, every GX Works2 project variant, and a general native compile command are **not** supported.

Research notes live under `docs/research/` and are intentionally treated as evidence for bounded implementation rather than as a universal file-format specification.

---

## Simulation, debug and hardware boundaries

The Web workbench contains an editable simulation/test-plan workflow and issue-to-network-to-test traceability. GX Simulator2 automation and evidence capture exist as experimental workflows.

Debugging is evidence-bound: the system can plan and apply scoped patches around diagnosed networks/addresses rather than treating debugging as unrestricted regeneration.

Physical PLC access is intentionally narrower. The current advanced maintenance path supports bounded **read-only observation** where configured. A general physical PLC write path is not exposed by the current Web workflow.

Starting GXWorks Agent never means that real equipment has been connected or modified.

---

## Web workbench

The Web frontend uses React/Vite with a FastAPI backend. PLC semantics stay in the Python engineering core; the browser is an operator surface rather than a second implementation of PLC logic.

The workbench currently provides:

- project and version navigation
- Ladder, FBD, ST, diagnostics, review, simulation, and delivery views
- natural-language analysis / generation / agent tasks
- editable confirmed specifications
- persistent job progress and reconnectable event history
- Program Explorer and model-free redraw
- scoped modifications and impact summaries
- issue cards linked to reports, networks, evidence, and reproduction tests
- editable version-bound simulation plans and run replay
- candidate review and Diff inspection
- GXW import and experimental FBD editing
- model profiles, capability discovery, Codex/MCP onboarding, and MCP client activity
- configurable operation approvals
- advanced bounded read-only PLC observation
- engineering delivery summaries and artifact export

Jobs continue in the backend if the browser is refreshed or closed. Their project, base version, confirmed specification, model settings, and response-language policy are frozen when submitted.

---

## Repository layout

```text
src/                 Python engineering core and application/runtime layers
web/                 React/Vite Web workbench
hardware_reader/     bounded hardware observation helper
docs/                integration, architecture and research documentation
docs/research/       GXW / Structured Ladder / FBD reverse-engineering evidence
examples/            examples and sample material
benchmarks/          benchmark assets/workflows
packaging/           Windows packaging support
requirements/        runtime dependency sets
```

Useful integration documentation:

- [Web workbench](docs/integrations/web.md)
- [MCP integration](docs/integrations/mcp.md)
- [Codex integration](docs/integrations/codex.md)

---

## Development principles

The repository currently follows several explicit constraints:

- **engineering state lives in the application, not in chat history**
- **model output must cross deterministic acceptance boundaries**
- **provider compatibility does not imply identical model capabilities**
- **repair is bounded by evidence instead of semantic guessing**
- **saved artifacts remain reproducible without another model call where possible**
- **external side effects require explicit policy/approval**
- **unknown GXW structures are preserved rather than fabricated**
- **software validation is not reported as native GX or hardware validation**

These constraints are more important to the project than maximizing the number of instructions a model can emit in one response.

---

## Roadmap

Near-term engineering work is concentrated on:

- expanding verified FX3U instruction/device contracts without weakening validation
- improving model/provider capability resolution while keeping provider-specific reasoning controls explicit
- strengthening GX Works2 import/export and native evidence capture
- extending bounded GXW / Structured Ladder / FBD coverage from reproducible reverse-engineering evidence
- improving simulation/diagnostics traceability
- continuing Web workbench consolidation and reducing legacy desktop-only paths
- adding future adapters such as GX Works3 only after the engineering boundary is clear

The roadmap does not imply that incomplete native GXW/FBD or hardware-write capabilities are production-ready today.

---

## Safety and scope

GXWorks Agent is engineering software under active development. Generated PLC logic must be reviewed and validated for the actual machine, CPU, wiring, safety circuit, operating mode, and applicable standards before deployment.

Emergency-stop, guarding, interlock, motion, pressure, temperature, and other safety-critical functions must not rely solely on generated application logic or software simulation.

---

## License

Licensed under the [Apache License 2.0](LICENSE).

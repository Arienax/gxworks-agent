<h1 align="center">GXWorks Agent</h1>

<h3 align="center">MCP tools and an AI workbench for Mitsubishi PLC engineering.</h3>

<p align="center"><strong>Read project context. Build and review programs. Deliver to GX Works2.</strong></p>

<p align="center">
  <a href="https://github.com/Arienax/gxworks-agent/releases/latest"><img src="https://img.shields.io/github/v/release/Arienax/gxworks-agent?style=flat-square&amp;color=2563eb" alt="Latest release"></a>
  <a href="#mcp"><img src="https://img.shields.io/badge/MCP-engineering_tools-7c3aed?style=flat-square" alt="MCP engineering tools"></a>
  <a href="#quick-start"><img src="https://img.shields.io/badge/Windows-10%2F11_x64-0078d4?style=flat-square" alt="Windows 10/11 x64"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache--2.0-f59e0b?style=flat-square" alt="License: Apache-2.0"></a>
</p>

<p align="center"><strong><a href="https://github.com/Arienax/gxworks-agent/releases/latest">Downloads</a> · <a href="#mcp">MCP &amp; Web</a> · <a href="https://github.com/Arienax/gxworks-agent/issues">Issues</a></strong></p>

<p align="center"><a href="#quick-start">Quick start</a> · <a href="#ladder">Ladder</a> · <a href="#st">ST</a> · <a href="#fbd">FBD</a> · <a href="#web">Web</a> · <a href="#diagnostics">Diagnostics</a></p>

<p align="center"><a href="README.zh-CN.md">简体中文</a> · <strong>English</strong> · <a href="README.ja.md">日本語</a></p>

GXWorks Agent supports Ladder, Structured Text (ST), and native FBD workflows. It gives external agents project context, CPU-specific instructions, manual evidence, and engineering tools through MCP. Connect through MCP, use your own model, and save versioned engineering artifacts. The default MCP runtime works independently; the Web workbench is optional.

<a name="ladder"></a>

## Ladder: from engineering context to a saved program

![Ladder context retrieval, instruction evidence, candidate construction and saved artifacts](resources/assets/ladder-rag-core-demo-en.gif)

An FX3U conveyor example: retrieve context for `WSFL`, `INC`, and `T200`, construct a Ladder candidate, and save it through MCP. Structured instruction facts and manual evidence inform the candidate; the saved version includes Ladder JSON, IR, SVG, ST derived from IR, and GX import CSVs.

The GIF visualizes actual SDK/MCP results and generated artifacts. The assistant constructed the candidate from the returned context; this is not a recording of an embedded model running autonomously.

<a name="st"></a>

## ST: generate and review Structured Text

ST has its own generation workflow in the Web workbench: analyze the requirement, confirm the specification, and generate `program.st` using the selected CPU's instruction context and ST signatures. Preview the candidate, inspect text differences between versions, and save the ST artifact. Ladder programs can also export traceable ST from their PLC IR.

Local ST inspection checks the supported syntax and semantics and reports unsupported constructs. It is a bounded parser, not the GX Works2 compiler; native compilation and PLC behavior remain separate. MCP can provide ST context and existing diagnostics, while `create_program_candidate` currently accepts Ladder candidates.

## What the API provides

- **Project-bound context.** Create or select a project, read its CPU and current program, discover applicable instructions and callable interfaces, and continue from a saved version.
- **Engineering RAG.** CPU-scoped instruction facts and manual excerpts carry source references. The retrieval architecture combines entity, full-text, and dense/LSA retrieval with rank fusion; structured facts cover known instruction details while residual retrieval supplies supporting evidence.
- **Candidate editing and artifacts.** Submit Ladder or FBD changes through shared engineering tools. Core checks the supported structure and operands, prepares artifacts, and returns version-bound results. Local saving, native compilation, and behavior verification have separate outcomes.
- **GX Works2 delivery.** With operator-configured execution consent, import the current Ladder version's MAIN and device comments and receive the native operation result. The optional Web bridge retains workbench review.

Your client or harness supplies the model and planning loop. GXWorks Agent supplies the engineering context, tools, persistence, and operation receipts.

<a name="web"></a>

## Web GUI: inspect and export a program

![Web workbench showing a saved Ladder program, network and device inspection, and the export menu](resources/assets/web-gui-view-deliver-en.gif)

Configure a model API directly in the workbench and use the built-in Direct generation workflow, then inspect program networks, device addresses, and available exports. Direct can reduce external agent orchestration overhead; actual token use depends on the model and task. It shares the engineering context pipeline with MCP, and workbench question-answering can still involve tool loops.

When site facts are missing, each question has its own input. Submit the answers together after completing the required fields; switching panels or reloading the result retains entered answers. Generation uses strict structured output when the endpoint supports it and normalizes unambiguous format differences locally before program validation.

<a name="fbd"></a>

## FBD: edit an existing GXW project

![MCP adds fault latching, reset and delayed enable to the first axis of an existing FBD project](resources/assets/fbd-control-logic-demo-en.gif)

Add fault latching, reset, and a 500 ms enable delay to the first axis of Mitsubishi Electric's MotionControl JEC example. The edit preserves existing FB implementations and Axis `IN_OUT` wiring, adds SR/TON logic, and connects the permit to the existing power-control block.

Source: [Mitsubishi Electric MotionControl JEC example](https://eu-assets.contentstack.com/v3/assets/blt5412ff9af9aef77f/blt9206f69c182f67c3/634ea5238602a36d8e991e63/MotionControl_JEC_a.zip).

FBD goes beyond the wiring change in the demo. Generate or edit candidates from a starting project or imported GXW; add, change, or remove objects, connections, declarations, and multiple networks; use project/library FB interfaces and `IN_OUT` parameters; and inspect graphical previews, version differences, and GXW artifacts.

Where the source project supports it, create FBs and Programs. A new Program can be registered in an existing task, a new task, or a new resource. Native saving, compilation, and code checking are separate operations with results tied to the corresponding project version.

<a name="diagnostics"></a>

## Project management, diagnostics, and validation

| Feature | Available work |
| --- | --- |
| Projects and versions | Create or select a project by CPU and program type, manage requirements and confirmed specifications, review candidates, compare versions, and inspect or export versioned artifacts. |
| Native diagnostics | Compile and check a GXW that matches the current version. Where location information is available, associate diagnostics with ST lines, declarations, FBD networks, objects, or call instances; retain an unresolved result when the location is ambiguous. |
| Existing SFC projects | Import an existing SFC project, read its program and graph, edit existing action and transition subprograms, and change supported step action numbers and jump targets. Preview candidates and version differences; creating a complete SFC project from scratch is not supported. |
| Ladder simulation | Define test plans, device inputs, and expected results; run supported simulation backends; save and review results; and relate tests to requirements. Instruction and CPU support depend on the backend. |
| Read-only PLC monitoring | With operator approval, read selected devices through a configured MX Component logical station and view values and read records. Revocable permissions are bound to the project version, address range, and expiry; this feature does not write to the PLC. |

<a name="quick-start"></a>

## Quick start

On Windows 10/11 x64, download the component you need from the [releases page](https://github.com/Arienax/gxworks-agent/releases/latest). Extract the entire ZIP. The executable packages work independently and require neither Python nor Node.js.

| Package | Usage | Entry point |
| --- | --- | --- |
| `GXWorks-Agent-MCP-windows-x64.zip` | Connect an MCP client such as Codex and use its model | `gxworks-agent-mcp.exe --local` |
| `GXWorks-Agent-Web-windows-x64.zip` | Generate, edit, and manage projects in a browser | `start-web.cmd` |

<a name="mcp"></a>

### Independent MCP

Add a stdio server in your client: use the extracted `gxworks-agent-mcp.exe` as the command and `--local` as its argument. Refresh the connection, call `list_projects`, then select a project or create one with its actual CPU and program type.

Try: “Create an FX3U Ladder project. Read the generation context and relevant instruction evidence. Count each press of X0, turn on Y0 after three counts, and reset with X1. Save the candidate and show its artifacts and remaining diagnostics.”

External clients use their own models without running Web. GX import follows configured execution consent. Web is optional when you want to share a project and its review flow.

### Web workbench

Run `start-web.cmd` in the extracted Web directory and choose an engineering workspace folder. Create a project and configure a model API under **Settings → Model → Model API** to generate programs. Viewing existing projects requires no model call. Extract updates into a new directory and retain your separate workspace and personal settings.

### Run from source

Install Python 3.13 and Node.js 22.14 or newer, then run:

```powershell
git clone https://github.com/Arienax/gxworks-agent.git
cd gxworks-agent
.\build-web.bat --no-pause
.\start-web.cmd
```

For MCP-only source use, create `.venv-mcp`, install `requirements/mcp.txt`, and use `gxworks-agent-mcp.cmd --local`. It does not need Node.js.

### Engineering templates and knowledge resources

Packages include the FX3U baseline, node and token templates, and 57 CPU-specific blank FBD projects under `gxw/templates/`; their source location is `src/gxw/templates/`. Keep these files when copying the application. Template availability does not establish native compilation or PLC behavior for every CPU.

Executable packages include the reviewed fact corpus and project-authored examples for RAG. Vendor manuals and original manual-text indexes are excluded. Generated runtime data is separate from the source repository; source users can copy a package's `knowledge/` to `resources/knowledge/` and its `resources/instructions/mitsubishi/` to the matching source directory.

Native saving, code checking, GX import, and simulation require the corresponding Mitsubishi software locally. Read-only PLC monitoring requires MX Component and a configured logical station. These Mitsubishi applications are not included.

## Current scope

- GX integration requires Windows; desktop delivery needs an interactive Windows session.
- Native FBD integration is tied to GX Works2 **1.635.0.1**. Ladder and ST generation, editing, and export do not require this version. Supported Q/L/FX CPU menu entries have matching blank FBD starting projects; imported projects retain their CPU and interfaces. FX1 is currently excluded from FBD generation. Template availability and native compilation acceptance are tracked separately.
- CPU menu availability does not imply uniform instruction or validation coverage. Unsupported validation profiles return an unverified result.
- FBD supports objects, connections, declarations, networks, and source-bound FB/Program creation. SFC supports editing existing action and transition subprograms; the Web panel can change supported step action numbers and jump targets and preview candidates. It is not a complete graphical SFC IDE.
- A saved artifact or successful import does not establish successful compilation or correct PLC behavior.

## Before operating machinery

Review generated logic against the selected CPU, wiring, motion limits, and operating modes. Back up the target GX project before import, then compile and inspect it in the intended GX Works2 environment before commissioning. Sending to GX Works2 does **not** download a program to a PLC.

Emergency stops, guarding, and other safety functions require an independent safety system. Generated application logic and software checks do not replace it. Sanitize diagnostics before sharing; never publish credentials or local session links.

## Guides and license

[Release changes](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Issues](https://github.com/Arienax/gxworks-agent/issues) · [Security](SECURITY.md)

Project-authored Core, Web, MCP and native-adapter source uses [Apache License 2.0](LICENSE); see the [license scope](LICENSE-SCOPE.md) and [third-party notices](THIRD_PARTY_NOTICES.md). Contributors retain their copyright. Previously distributed versions retain their original licenses.

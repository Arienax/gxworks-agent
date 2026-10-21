<h1 align="center">GXWorks Agent</h1>

<h3 align="center">MCP engineering tools and an AI workbench for Mitsubishi PLC workflows.</h3>

<p align="center"><strong>Read project context · Build and review programs · Drive native GX Works2 engineering and diagnostics</strong></p>

<p align="center">
  <a href="https://github.com/Arienax/gxworks-agent/releases/latest"><img src="https://img.shields.io/github/v/release/Arienax/gxworks-agent?style=flat-square&amp;color=2563eb" alt="Latest release"></a>
  <a href="#mcp"><img src="https://img.shields.io/badge/MCP-engineering_tools-7c3aed?style=flat-square" alt="MCP engineering tools"></a>
  <a href="#quick-start"><img src="https://img.shields.io/badge/Windows-10%2F11_x64-0078d4?style=flat-square" alt="Windows 10/11 x64"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache--2.0-f59e0b?style=flat-square" alt="License: Apache-2.0"></a>
</p>

<p align="center"><strong><a href="https://github.com/Arienax/gxworks-agent/releases/latest">Downloads</a> · <a href="#mcp">MCP &amp; Web</a> · <a href="https://github.com/Arienax/gxworks-agent/issues">Issues</a></strong></p>

<p align="center"><a href="#quick-start">Quick start</a> · <a href="#offline-gxw">Offline GXW editing</a> · <a href="#ladder">Ladder</a> · <a href="#st">ST</a> · <a href="#fbd">FBD</a> · <a href="#native-session">Native sessions</a> · <a href="#web">Web workbench</a> · <a href="#diagnostics">Diagnostics</a></p>

<p align="center"><a href="README.zh-CN.md">简体中文</a> · <strong>English</strong> · <a href="README.ja.md">日本語</a></p>

GXWorks Agent brings together project context, CPU-scoped instruction facts, deterministic static checks, and native GX Works2 operations into a unified engineering toolchain. It supports **Ladder**, **Structured Text (ST)**, and **Function Block Diagram (FBD)** workflows, along with controlled editing and inspection for existing **SFC** projects.

Connect any MCP client—such as Codex, Claude Desktop, or Cursor—to use the engineering tools with your own model for planning, generation, live native editing, and verification, while saving versioned engineering artifacts. The standalone MCP server runs independently; the optional **Web workbench** adds browser-based Direct generation, graphical diff review, simulation testing, and operator-approved GX Works2 delivery.

<a name="offline-gxw"></a>

## Read and edit existing GXW files offline

Supported `.gxw` files can be inspected, edited, and saved without installing or starting GX Works2.

1. **Web**: Drop a `.gxw` into the main conversation or choose **Import GXW**. No Agent project needs to exist first. Inspection selects the only ordinary Program automatically; choose a target when several programs are present, or explicitly select an existing FB or FUNCTION.
2. **MCP**: Use `inspect_gxw_project` to inspect the file's CPU, sources, and format capabilities, then `import_gxw_project` to enter the shared project/version workflow.
3. Describe the change, review its preview and differences, and save a new version. Download `program.gxw` and re-import it to continue from the saved file; the original input file is preserved.

Imported Ladder sources automatically display contacts, coils, and branches, including conditions after outputs and the original `MPS/MRD/MPP` order. The source instruction list remains available.

The four languages share an offline transaction. Ladder supports source-bound instruction, float, string, and P/I label edits and moves while preserving `MPS/MRD/MPP` order and unchanged record bytes. ST edits existing Program, FB, and FUNCTION bodies and declarations, including existing interfaces, parameter order, and return types, with caller checks. FBD edits objects, connections, networks, variables, and instances of existing FB definitions. Existing SFC charts support step, transition, action, branch, merge, jump, and child-program edits with internal registrations maintained. Declaration rows may be added, removed, or reordered; actual QCD comments retain their complete text.

After import, use **Program to edit** to select another source in the same file. MCP `read_gxw_source` returns its editing model; `edit_gxw_project` submits several source, declaration, and comment edits against one current `version_id`, with differences shown per source. The new entry defaults to `save_policy="draft"`, preserving source diagnostics in an editable intermediate version. `checked` rejects newly introduced recognized errors. Older entry points retain their previous behavior when the policy is omitted. Drafts still require valid file structure and storage references; saving does not establish compilation or runtime success.

The FX/Q/L model catalogue participates in file capability checks, with results scoped to CPU, format, object, and operation. Encoding, native compilation, and runtime validation remain separate. Unknown records retain their bytes while other safe objects remain editable; edits with unresolved dependencies return the specific object and reason. This path does not add engineering-level POUs, FB definitions, declaration tables, resources, or tasks, or change task registration.

Changing source or declarations makes previous compilation results stale. A retained `.res` is not compilation evidence for the new source; native compilation remains an explicit, separate operation. Compatibility is determined by the concrete CPU and file structure, and does not certify every GX Works2 version.

<a name="ladder"></a>

## Ladder: from engineering context to deliverable artifacts

![Ladder context retrieval, instruction evidence, candidate construction and saved artifacts](resources/assets/ladder-rag-core-demo-en.gif)

In this FX3U conveyor example, the agent retrieves CPU-specific facts and manual evidence for `WSFL`, `INC`, and `T200`, constructs a Ladder candidate, and commits it through MCP. Python Core validates structure and operands, merges shared conditions, and checks maintainability. Each saved version produces Ladder JSON, PLC IR, SVG diagrams, traceable ST derived from IR, and GX Works2 import CSVs for `MAIN` and device comments. For targeted fixes on an existing version, `read_network` and `patch_program` apply scoped network patches.

The animation renders actual SDK/MCP responses and saved artifacts; the candidate was constructed by an external client from the returned context rather than recorded from an autonomous embedded model.

<a name="st"></a>

## ST: Structured Text generation and native project editing

ST workflows cover both standalone text generation and native `.gxw` project maintenance:

- **Standalone generation and review in Web**: Analyze requirements, confirm the specification, and generate `program.st` using the selected CPU's instruction context and ST call signatures. Preview candidates, inspect text diffs across versions, and save ST artifacts. Ladder versions can also export traceable ST from their PLC IR.
- **GXW ST project editing**: `read_st_project` reads existing Program, FB, and FUNCTION bodies, declarations, and ownership; `create_st_candidate` edits their bodies and declarations offline. Creating an ordinary ST Program retains the explicit resource, task, and native-operation workflow. Persistent native sessions separately support coordinated FBD editing and compilation.

The local ST checker validates supported syntax and semantics and flags unsupported constructs. It is a bounded static parser, not a replacement for the GX Works2 compiler or PLC runtime verification.

<a name="fbd"></a>

## FBD: from blank CPU templates to full GXW editing

![MCP adds fault latching, reset and delayed enable to the first axis of an existing FBD project](resources/assets/fbd-control-logic-demo-en.gif)

Above, MCP adds fault latching, reset, and a 500 ms enable delay to the first axis of [Mitsubishi Electric's MotionControl JEC example](https://eu-assets.contentstack.com/v3/assets/blt5412ff9af9aef77f/blt9206f69c182f67c3/634ea5238602a36d8e991e63/MotionControl_JEC_a.zip). The edit preserves existing FB implementations and Axis `IN_OUT` wiring, inserts SR/TON logic, and routes the motion permit into the existing power-control block.

Beyond wiring edits on imported files, the FBD module supports end-to-end project authoring:

- **Start from templates or imported projects**: 57 CPU-specific blank FBD templates let you start a project directly by CPU model, or import an existing `.gxw` to continue editing.
- **Parameter-level and geometry-level editing**: Submit high-level `calls` and `arguments` against the schema returned by `get_fbd_catalog`—letting Core expand ports, `IN_OUT` pairs, and routing automatically—or explicitly add, modify, and remove nodes, terminals, orthogonal wires, and multiple networks.
- **Definitions, user libraries, and task registration**: Read and author project structures, ST/FBD Function Blocks, and global/local declaration tables; import user library definitions from a donor `.gxw` (`read_fbd_libraries` / `import_library`) while keeping caller programs intact; create FBs and Programs where supported by the source project; register Programs to an existing task, new task, or new resource; and configure compiler device-allocation ranges.
- **Decoupled artifacts and validation**: Every saved version produces `program.gxw`, `fbd.json`, `fbd.svg`, and a write verification report. Local readback verification, native saving, standalone compilation, and code checking run as distinct operations bound to the project version.

<a name="native-session"></a>

## Persistent native engineering sessions

With `execute_native_engineering(mode="live")`, an MCP client can drive multi-step edits inside **one continuously open GX Works2 process** across tool calls, without saving and reopening the project between every action:

- **Multi-step live authoring**: Sequentially edit global and local label tables, create or update ordinary ST Programs and ST FBs, insert FBD call instances, and rebind terminals, move nodes, or reroute wires using the active document's live node and terminal identities.
- **In-session compilation and scoped address lookup**: Compile the current source (`compile`) and query compiler-assigned addresses for global declarations, local Program variables, and `Instance.Member` paths via native cross-reference (`show_assignments`). Any subsequent source edit automatically marks earlier compilation and address results `stale`.
- **Paced execution and safe reconciliation**: Configure step dwell time (`dwell_ms`), single-step execution (`step_mode`), pause (`pause_native_engineering_session`), and resume. The session tracks active edits (`revision`), verified disk saves (`saved_revision`), and committed application versions (`version_revision`) separately. Interrupted or timed-out steps retain an `unconfirmed` receipt and reconcile against actual UI state via `rebind` rather than blindly replaying actions.

Native engineering sessions require an interactive Windows desktop, GX Works2 `1.635.0.1`, and operator-configured execution consent.

<a name="web"></a>

## Web workbench: Direct generation, visual review, and export

![Web workbench showing a saved Ladder program, network and device inspection, and the export menu](resources/assets/web-gui-view-deliver-en.gif)

Configure a model API in the workbench to use the built-in **Direct generation workflow**—analyzing requirements, confirming specifications, and generating programs—then inspect Ladder networks, FBD/SFC graphs, device usage, version diffs, and downloadable artifacts. Direct shares the same engineering context and RAG pipeline as MCP while reducing external agent orchestration overhead; workbench Q&A can still invoke read-only tools as needed.

When process facts are missing, each clarification question appears as an individual input field that preserves entered text across panel switches and reloads until submitted together. Generation uses strict structured output where supported by the endpoint and normalizes unambiguous format differences locally before Core validation.

## What the toolchain provides

- **Project-bound context and live session discovery**: Create or select versioned projects by CPU and language, or call `list_gxworks2_sessions` and `read_gxworks2_snapshot` without an Agent project to inspect a read-only snapshot of a program currently open in GX Works2.
- **CPU-scoped engineering RAG**: Retrieval combines entity matching, full-text search, and dense/LSA vector retrieval with rank fusion. Structured facts supply exact operand, step-width, and modifier rules for known instructions, while residual retrieval adds manual evidence and project-authored examples without duplicating context.
- **Deterministic Core validation and versioned artifacts**: Python Core owns PLC domain semantics—building IR, checking operands and timing, verifying FBD topology and declarations, and preparing version-bound artifacts. Local structural acceptance, native compilation, simulator observation, and physical PLC behavior remain strictly separate outcomes.
- **Controlled GX Works2 delivery**: Under operator-configured approval policies, import the current Ladder version's `MAIN` and device comments into GX Works2, or execute native FBD/ST saves, compilations, and live session steps with version-bound operation receipts.

Your client or harness owns the model and planning loop; GXWorks Agent provides the engineering context, deterministic tools, version persistence, and operation receipts.

<a name="diagnostics"></a>

## Project management, diagnostics, and simulation

| Capability | What you can do |
| --- | --- |
| Projects and versions | Create or select projects by CPU and program type (Ladder / ST / FBD / SFC), manage requirements and confirmed specifications, review candidates, compare versions, and export versioned artifacts. |
| Live GX read-only snapshots | Discover running GX Works2 windows and take a temporary read-only snapshot (`source_ref`) without creating an Agent project, then read networks and run static analysis. |
| Native compilation and source-level diagnostics | Compile and check a saved `.gxw` in an isolated workspace, query assigned addresses for declarations and FB instance members (`read_native_device_assignments`), compare saved code channels against fresh compilation (`compare_native_compiled_code`), and map diagnostics or code steps (`locate_native_code_steps`) to ST lines, declarations, FBD networks/nodes, or call instances—retaining `unresolved` when a location is ambiguous. |
| Existing SFC project editing | Read source relationships offline; edit steps, transitions, actions, branches, merges, jumps, and child programs; jointly add or remove internal objects, declarations, and registrations; preview differences and save versions. Engineering-level SFC POUs are not created from scratch. |
| Ladder simulation and live timeline viewer | Run version-bound simulation test plans with device stimuli and assertions; or use `connect_simulator` and `start_simulator_trace` to sample typed signals read-only from a connected GX Simulator2 target and inspect waveforms in the bundled Web timeline viewer (`simulator.html`). Observation performs no unapproved writes and keeps `program_correspondence="unverified"` explicit. |
| Read-only PLC monitoring | With operator approval, read selected devices through a configured MX Component logical station and review values and read history. Permissions are bound to the project version, address range, and expiry, can be revoked at any time, and never write to the PLC. |

<a name="quick-start"></a>

## Quick start

On Windows 10/11 x64, download the package you need from the [releases page](https://github.com/Arienax/gxworks-agent/releases/latest) and extract the entire ZIP. Both executable packages run standalone and require neither Python nor Node.js.

| Package | Best for | Entry point |
| --- | --- | --- |
| `GXWorks-Agent-MCP-windows-x64.zip` | Connecting an MCP client (Codex, Claude Desktop, Cursor) and using its model | `gxworks-agent-mcp.exe --local` |
| `GXWorks-Agent-Full-windows-x64.zip` | Running the Web workbench and MCP with shared runtime resources | `start-web.cmd` / `gxworks-agent-mcp.exe --local` |

The lean `MCP` package omits the Web server, model SDK, offline PDF ingestion dependencies, and demo animations, while keeping the engineering tools, RAG retrieval, engineering templates, native helpers, and the simulator timeline viewer. `Full` includes both entrypoints: its MCP server runs independently with `--local`, or connects to a running Web workbench with `--service` to share projects and review flows.

<a name="mcp"></a>

### Standalone MCP

Register a stdio server in your MCP client using the full path to `gxworks-agent-mcp.exe` as the command and `--local` as its argument. Refresh the connection, call `list_projects` to select an existing project or create one with your actual CPU and program type, use [offline GXW import](#offline-gxw) for a saved file, or call `list_gxworks2_sessions` to inspect an already-open GX Works2 window.

Try a prompt like:

> “Create an FX3U Ladder project and read the generation context and relevant instruction evidence first. Count each press of X0, turn on Y0 after 3 counts, and reset with X1. Save the candidate version and show its artifacts and any remaining diagnostics.”

External clients use their own models without starting Web. GX import and native operations follow configured execution consent; add the Web workbench whenever you want shared visual review.

### Web workbench

Run `start-web.cmd` in the extracted `Full` directory and choose an engineering workspace folder. Create a project for a new program, or [import an existing GXW](#offline-gxw). Configure a model service under **Settings → Model → Model API** for AI generation and changes; browsing existing projects, inspecting files and graphs, and exporting artifacts require no model calls. When updating, extract the new release into a fresh directory—your separate workspace and personal settings remain intact.

### Run from source

Install Python 3.13 and Node.js 22.14 or newer, then run:

```powershell
git clone https://github.com/Arienax/gxworks-agent.git
cd gxworks-agent
.\build-web.bat --no-pause
.\start-web.cmd
```

For MCP-only source use, create `.venv-mcp`, install `requirements/mcp.txt`, and launch `gxworks-agent-mcp.cmd --local`. Node.js is not required for MCP-only source runs.

### Engineering templates and knowledge resources

Release packages include the FX3U baseline project, node and token-grammar templates, and 57 CPU-specific blank FBD projects under `gxw/templates/` (`src/gxw/templates/` in source). Keep these files intact when copying the application. Template availability does not imply verified native compilation or PLC runtime behavior for every CPU.

Executable packages bundle the reviewed runtime fact corpus and project-authored examples for RAG, excluding vendor manual PDFs and raw manual-text indexes. Runtime knowledge data is distributed separately from the source repository; when running from source, you can copy a release package's `knowledge/` into `resources/knowledge/` and its `resources/instructions/mitsubishi/` into the matching source directory.

Offline GXW file operations require no Mitsubishi installation. Native saving, native compilation/code checking, delivery to GX Works2, and native simulation require the corresponding Mitsubishi software installed locally; read-only PLC monitoring requires MX Component and a configured logical station. These Mitsubishi applications are not included.

## Before operating machinery

Review all generated logic against the target CPU, field wiring, motion limits, and operating modes. Back up the target GX project before import, then compile and inspect it in the intended GX Works2 environment before commissioning. **Sending a program to GX Works2 updates the project only—it does not download a program to a PLC.**

Emergency stops, safety interlocks, and overtravel protection must be implemented in an independent safety system; generated application logic and software checks do not replace hardware safety. Sanitize diagnostic records before sharing, and never publish API credentials or local session URLs.

## Guides and license

[Release changes](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Issues](https://github.com/Arienax/gxworks-agent/issues) · [Security](SECURITY.md)

Project-authored Core, Web, MCP, and native-adapter source code is licensed under the [Apache License 2.0](LICENSE); see the [license scope](LICENSE-SCOPE.md) and [third-party notices](THIRD_PARTY_NOTICES.md) for details. Contributors retain their copyright, and previously distributed releases retain their original licenses.

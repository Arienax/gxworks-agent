# GXWorks Agent

[简体中文](README.zh-CN.md) | English

**Put an AI agent to work in a real PLC project.** GXWorks Agent exposes the current project, CPU, available instructions, and manual knowledge to external agents through MCP. It checks generated Ladder programs and, after an engineer approves, sends the current version's MAIN program and device comments to GX Works2. Requirements, program versions, and delivery results stay in one local workbench, reducing the steps spent moving between chat, manuals, files, and GX Works2.

![Recorded MCP approval and GX Works2 import in the white and blue workbench](resources/assets/demo.gif)

**FX3U demonstration:** Count each press of X0, light Y0 at three counts, and reset with X1. A Codex CLI agent used MCP to inspect the project, discover relevant capabilities, search manual knowledge, and submit a Ladder candidate. The white and blue Web workbench shows approval of the reviewed version; GX Works2 shows its imported MAIN and short device comments. This demonstration uses the optional MCP-to-Web bridge. The GIF uses real desktop footage with waits removed. CSV is the generated delivery format, so the engineer does not have to convert and re-enter the program and comments.

## From a requirement to GX Works2

1. **The agent works directly with the project.** Codex and other MCP clients can create or select a project, read its program and generation context, then save validated candidates through the independent MCP runtime. Later edits retain project and version context. The Web workbench is optional.
2. **The model receives relevant engineering knowledge.** Capability discovery finds applicable instructions and project interfaces for the request. RAG brings together instruction definitions, manual evidence, callable objects, and construction examples scoped to the selected CPU. The application checks candidate addresses, instruction operands, and Ladder structure and returns results the engineer can inspect.
3. **Authorized work goes to GX.** An external agent can request delivery of the current Ladder version under the engineer's configured execution consent. Independent MCP returns the native receipt directly; the optional Web bridge retains workbench review. The application imports MAIN and device comments into GX Works2 and saves the project. For supported GX versions and CPUs, it starts GX when needed and creates a separate project when none is open. The engineer then compiles and inspects the result in GX Works2.

The workbench also supports version comparison, scoped edits, engineering questions, and inspection of delivery artifacts. Structured Ladder/FBD has its own GXW read/write, candidate editing, and native checking path.

## Quick start

On Windows 10/11 x64, choose the component you need from [Releases](https://github.com/Arienax/gxworks-agent/releases/latest). The two packages install independently. Python is not required.

| Component | Use | Requirements |
| --- | --- | --- |
| GXWorks-Agent-MCP-windows-x64.zip | Work with projects in Codex or another MCP client, using the client's own model | No Web or Node.js required |
| GXWorks-Agent-Web-windows-x64.zip | Browser workbench for projects, model settings, program editing and diagnostics | Node.js 22.14 or newer; no MCP required |

### Web workbench

Extract the entire Web package and run in its folder:

```sh
npm install
npm run build
npm start
```

You can also clone this repository and run the same commands; the matching runtime is downloaded automatically on first use. The browser opens automatically. Projects default to `%LOCALAPPDATA%\GXWorks Agent\web-workspace`, with no workspace selection required.

Create a project in the workbench and configure your model service under **Settings → Model → Model API** for generation. Viewing and managing existing projects does not require model calls. For updates, extract the new package to a new folder; existing projects and personal settings are retained.

### Independent MCP

Extract the entire MCP package and add a stdio server in your client: set the command to the extracted `gxworks-agent-mcp.exe`, with `--local` as its argument. Refresh the client's connection, then try this request:

> Read the current FX3U project and generation context first. Find the available capabilities and manual evidence for counting and rising edges. Count each press of X0, turn on Y0 after three counts, and reset with X1. Submit a Ladder candidate for my approval, then request delivery of the current version to GX Works2.

External MCP clients use their own models; Web does not need to be running. On first use, call `list_projects`, then select a project or create one with the actual PLC model and target language. To share Web project state and approvals, configure `--service` mode as described in [components and optional bridging](docs/private-component.md).

Native saving, code checks, import and simulation require the corresponding Mitsubishi software locally. GX Works2 and MX Component are not included. Available operations depend on the CPU, project type and software version.

## More information

[Components and optional bridging](docs/private-component.md) covers independent operation and connection settings. See [contributing](CONTRIBUTING.md), [issue reporting](https://github.com/Arienax/gxworks-agent/issues) and the [security policy](SECURITY.md) for participation and feedback.

For interface development, run `npm test` and `npm run build` at the repository root; `npm start` serves the current `web/dist`. Download the runtime ahead of time with `npm run setup`, or install offline with `npm run setup -- --archive <Web-release.zip>`.

## Before operating machinery

Review generated logic against the selected CPU, wiring, motion limits, and machine operating modes. Compile and check it in the intended GX Works2 environment before commissioning. Emergency stops, guarding, and other safety functions require the machine's independent safety system; generated application logic and software tests do not replace it.

## License

The project is provided under the source-available proprietary [LICENSE](LICENSE). Keep the full license and [third-party notices](THIRD_PARTY_NOTICES.md) with permitted copies.

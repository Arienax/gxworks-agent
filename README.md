# GXWorks Agent

[简体中文](README.zh-CN.md) · [Download releases](https://github.com/Arienax/gxworks-agent/releases/latest)

GXWorks Agent provides a local PLC engineering Web workbench and an independent MCP server. Each Windows package includes the compiled engineering core, native adapters and offline knowledge resources. Python is not required. Web uses Node.js 22.14 or newer with npm; MCP does not require Node.js.

This repository contains the interface, HTTP contract and frontend tests. Core source, reverse-engineering records and reports remain in the private development repository; runnable core components are distributed with releases.

## Choose a package

| Package | Use | Other package required |
| --- | --- | --- |
| GXWorks-Agent-MCP-windows-x64.zip | Local stdio MCP: create/select projects, obtain context, submit/save programs and retrieve artifacts | No Web dependency |
| GXWorks-Agent-Web-windows-x64.zip | Browser workbench: projects, model settings, generation/editing, diagrams, diagnostics and approvals | No MCP installation required |

Extract the entire ZIP into its own folder on Windows 10/11 x64. Keep all included files together. For updates, extract to a new folder and retain existing projects and user settings.

**Web:** Extract the Web package, or clone this repository, and run in its root directory:

```sh
npm install
npm run build
npm start
```

The first source startup downloads the matching compiled core; the Web ZIP already includes it. The browser opens automatically. Projects go directly to `%LOCALAPPDATA%\GXWorks Agent\web-workspace`, without a folder selection dialog. Runtime state and personal settings are stored separately. Advanced users can use `npm start -- --workspace <directory>`. Configure your own model service and credentials for generation. Viewing and managing projects do not require a model call.

**MCP:** Configure a stdio server in your client with the extracted `gxworks-agent-mcp.exe` and `--local`. The client supplies its own model; no Web startup is required. Start with `list_projects`, then select a project or create one with an explicit PLC model and target language. See `gxworks-agent-mcp.exe --help` for options.

## Capabilities and limits

Both interfaces share the core for project management, candidate validation, version saving, Ladder artifacts, GXW reading and implemented FBD/SFC operations. Native saving/checks, import and simulation require the corresponding Mitsubishi software locally. GX Works2 and MX Component are not included. Support depends on the actual CPU, project type and version; evidence for one model does not establish support for an entire family.

Structural validation, native compilation, simulation and physical PLC execution are separate results. External operations retain the existing approval policy; installation or opening the interface does not automatically operate equipment.

Optional MCP `--service` mode connects to Web's backend and shares engineering state and approval proposals. It does not synchronize conversations or orchestrate multiple agents. Default installations remain independent; see [components and bridging](docs/private-component.md).

## Interface development

Use root-level `npm run build` and `npm test`. `npm start` serves your current `web/dist`, so rebuilt interface changes take effect. For hot reload, start the backend and run `npm run dev` in `web`; its proxy targets `127.0.0.1:8765` and uses the backend's operator token. Cloning source does not include the core. Download ahead of time with `npm run setup`, or install offline with `npm run setup -- --archive <Web-release.zip>`.

See [publication rules](docs/publishing.md) and [contributing](CONTRIBUTING.md). Project files follow [LICENSE](LICENSE); third-party components retain their [notices and licenses](THIRD_PARTY_NOTICES.md).

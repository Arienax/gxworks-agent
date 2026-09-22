# GXWorks Agent

[简体中文](README.zh-CN.md) | English

GXWorks Agent is a local engineering workbench for Mitsubishi PLC projects, focused on FX3U and GX Works2. It turns requirements into a reviewable specification, generates Ladder candidates, saves versioned PLC IR, and exports engineering artifacts. The Web workbench and external MCP clients use the same Python engineering services.

Use the workbench to analyse requirements, review I/O assignments, generate or edit a program, inspect changes, and export the artifacts recorded for that version. GX Works2 import, simulation and hardware observation have separate operating requirements; start with the [Web guide](docs/integrations/web.md).

## Quick start

### From source on Windows

Install Git with Git LFS, Python, and Node.js/npm. See [environment setup](docs/guides/getting-started.md) for dependency files and troubleshooting.

```powershell
git clone https://github.com/Arienax/gxworks-agent.git
cd gxworks-agent
git lfs install --local
git lfs pull
.\build-web.bat --no-pause
.\start-web.cmd
```

The build entry prepares the backend environment and frontend. The launcher asks for a workspace and opens the local workbench. Keep its terminal open while using the application; close the service with `Ctrl+C`.

Configure a model in **Settings → Model → Model API**, then create a project. Start with Direct analysis, review the specification and I/O, and generate a program. [Your first project](docs/guides/getting-started.md#first-project) explains the complete sequence.

### From a Web package

For a package containing `start-web.cmd` and `GXWorks-Agent-Web.exe`, extract the complete directory and run `start-web.cmd`. Keep the supplied resources beside the executable. [Package installation and updates](docs/guides/packaging.md) describes the directory layout and update procedure.

### Connect an external agent

With a project open, use **Settings → Model → Integrations / MCP**. Follow [MCP onboarding](docs/integrations/onboarding.md) for the product launcher, [Codex integration](docs/integrations/codex.md) for client setup, or the [MCP reference](docs/integrations/mcp.md) for explicit standalone and service configurations.

## Documentation

The [documentation index](docs/README.md) separates usage guides, implementation references, versioned test reports and historical work.

- [Model settings and persistent data](docs/guides/model-settings.md)
- [GX Works2 transfer](docs/guides/gxworks2.md) and [Structured Ladder / FBD](docs/guides/fbd.md)
- [Diagnostics and interaction exports](docs/guides/diagnostics.md)
- [Architecture and source navigation](docs/architecture/source-layout.md)
- [Knowledge resources](resources/knowledge/README.md) and [GXW research evidence](research/README.md)
- [Verification reports](docs/reports/README.md)

## Before operating machinery

Review generated logic against the selected CPU, wiring, motion limits and machine operating modes. Compile and check it in the intended GX Works2 environment before commissioning. Emergency stops, guarding and other safety functions require the machine's independent safety system; generated application logic and software tests do not replace it.

## License

The project is provided under the source-available proprietary [LICENSE](LICENSE). Keep the full license and [third-party notices](resources/knowledge/THIRD_PARTY_NOTICES.md) with permitted copies.

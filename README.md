# GXWorks Agent

[简体中文](README.zh-CN.md) · [日本語](README.ja.md)

GXWorks Agent is a local engineering workbench for GX Works2. Its Web and MCP
entry points share the same Python application services. The project can create
and inspect PLC projects, produce and review Ladder/ST/FBD candidates, and send
approved work to a selected GX Works2 instance. Native GXW project editing and
the bundled blank engineering templates are included in this source release.

## Run from source on Windows

Install Python 3.10+, Node.js 22.14+, and a licensed copy of GX Works2 for
native operations. Then run:

```powershell
git clone https://github.com/Arienax/gxworks-agent.git
cd gxworks-agent
.\build-web.bat --no-pause
.\start-web.cmd
```

The standalone MCP entry point is `gxworks-agent-mcp.cmd`; it uses the Python
environment created by the Web setup. For MCP-only use, install
`requirements/mcp.txt` in `.venv-mcp` and run the same command. The Web frontend
can be built separately with `npm --prefix web ci` and
`npm --prefix web run build`.

The `src/gxw/templates/` directory contains the FX3U baseline, node and token
templates, and 57 empty CPU-specific FBD projects. Keep these files beside the
source when copying or packaging the application. Blank template availability
does not establish PLC runtime behavior for every CPU.

Vendor manuals, the manual-derived knowledge database and full instruction definitions
are absent because their redistribution rights have not been established. The
application can start without them, but manual-backed RAG and some detailed
instruction facts require an index built locally from materials you are
authorized to use. The source build reports this limitation. Do not infer a
device address or instruction is supported merely because it appears in a
model response.

Before sending logic to machinery, review it against the selected CPU,
wiring and safety design, compile and check it in GX Works2, and validate
behavior on the intended equipment. Application checks and blank templates do
not replace those steps.

See [release changes](CHANGELOG.md), [contribution guidance](CONTRIBUTING.md),
[license scope](LICENSE-SCOPE.md), and [third-party notices](THIRD_PARTY_NOTICES.md).
Project-authored source is offered under [Apache License 2.0](LICENSE).
Earlier releases retain their original terms.

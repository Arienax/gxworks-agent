# Optional dependency sets

The root `requirements.txt` installs headless Web/Core development, testing and release tooling.
No Qt binding is required. Windows 7 product packaging is retired.
Additional environments are kept here so the repository root stays focused on user-facing entry points and project metadata.

- `web.txt` — FastAPI/Web runtime used by the local Web workbench and Web packaging; it also includes the MCP SDK because the Windows Web release now builds `gxworks-agent-mcp.exe` beside the workbench.
- `mcp.txt` — minimal standalone/headless MCP SDK environment for source-only MCP use.
- `gxw-test.txt` — optional independent MS-CFB reader used by GXW allocator regression tests.

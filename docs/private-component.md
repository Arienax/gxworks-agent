# Components and optional bridging

MCP and Web are independently installable packages. Each includes everything needed to run its service. Neither starts the other automatically. Keep installation folders separate.

## Independent MCP

Configure the client's stdio command as the MCP package's `gxworks-agent-mcp.exe` with `--local`. It can create/select projects and save validated versions without Web. Its default workspace is separate from Web's workspace. Use `--local --workspace <path>` and optionally `--state-dir <path>` for explicit locations; state must be outside the engineering workspace. Active writer locks remain enforced.

The host client supplies the model. No Web model profile or model API credentials are required. Diagnostics: `gxworks-agent-mcp.exe --local --check`. `--help` writes only to stderr, leaving MCP stdout for protocol messages.

## Independent Web

Run `npm install`, `npm run build` and `npm start` in the repository or extracted Web package. The service starts and opens the browser. Projects default to the current user's LocalAppData under `GXWorks Agent/web-workspace`; no folder selection is required. Web serves its own interface and engineering API on loopback. Configure a model provider in settings for generation. The browser uses relative `/api` URLs; `web/openapi.json` and `web/src/api/generated.ts` document the contract.

For UI development run `npm run dev` in `web`; the proxy targets `http://127.0.0.1:8765`. Origin, session, CSRF, approval and resource-lock checks remain enforced. Never put tokens or model credentials in committed files or browser build variables.

## Optional service bridge

Install both only if an external MCP client should work on a project managed by the running Web workbench. Start Web, bind the current project in Settings → Integrations / MCP, and launch MCP with `--service`. Windows Credential Manager holds the service credential. Explicit connections can use `--service-url`, `--service-token-env` and `--project`.

For Web's connection buttons to locate the separate MCP package, set the local environment variable `GXWORKS_MCP_EXE` to the full `gxworks-agent-mcp.exe` path before starting Web. Web remains usable without this optional setup.

Service mode forwards tools to Web's backend and uses its approval policy. MCP cannot approve pending proposals or grant device permissions. Stopping Web makes this explicit bridge unavailable; independent MCP remains a separate available mode.

This shares engineering state and results, not chat history. It does not coordinate agents, schedule tasks or provide an agent harness. Do not imitate a bridge by running independent writers against an already owned workspace.

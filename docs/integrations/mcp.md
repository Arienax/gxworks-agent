# MCP engineering interface

The normal launcher connects a client to the running local Web service. An explicit `--standalone` mode reads an existing SessionStore without that service. Legacy explicit `--workspace` also selects standalone mode. [main](../../src/integrations/mcp/__main__.py) owns the mode selection and CLI defaults.

For normal Windows setup, start with [MCP onboarding](onboarding.md). This reference covers protocol and headless use.

<a id="service-mode"></a>
## Service connection

Automatic discovery uses the local binding in [service_credentials](../../src/integrations/mcp/service_credentials.py). An explicit connection is useful for a headless client:

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
# Set GXWORKS_MCP_AGENT_TOKEN privately to the Web service's Agent token.
.\.venv\Scripts\python.exe -m integrations.mcp --stdio `
  --service-url "http://127.0.0.1:8765" `
  --service-token-env GXWORKS_MCP_AGENT_TOKEN `
  --project PROJECT_ID
```

Use the actual origin printed by the service. The explicit token is an Agent credential, not the operator session token. [validate_service_url](../../src/integrations/mcp/service_client.py) restricts the bridge to loopback HTTP and rejects redirects. Service mode stores proposals and applies the [workspace approval policy](../architecture/approval-modes.md). Agent credentials cannot change that policy or approve operator-only actions.

## Standalone saved context

Install [requirements/mcp.txt](../../requirements/mcp.txt) in a separate environment and supply an existing workspace and project:

```powershell
python -m venv .venv-mcp
.\.venv-mcp\Scripts\python.exe -m pip install -r requirements/mcp.txt
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv-mcp\Scripts\python.exe -m integrations.mcp --standalone --stdio `
  --workspace "D:\PLCWorkspaces\example" --project PROJECT_ID
```

The workspace contains `index.json` and `projects/<project-id>/project.json`. `--version` pins a saved version; without it each call follows the project's persisted active version. `PLC_AI_WORKSPACE_DIR` supplies a workspace only for the selected standalone mode. A missing project, invalid version or inconsistent artifact returns a context error rather than selecting another project.

[SessionToolContextProvider](../../src/integrations/mcp/context_provider.py) copies each context and loads with migration writes disabled. A versionless project can supply initial generation context and candidates. Pin a version for related calls that must share the same saved baseline.

## Discovery and results

Clients query `tools/list` for the actual tool set and schema. [ToolRegistry](../../src/agent_runtime/plc_tools.py) owns definitions; [SAFE_TOOL_NAMES](../../src/agent_runtime/plc_tools.py) filters the adapter surface. Calls pass through `ToolRuntime.invoke` and the shared PLC implementation.

[to_mcp_result](../../src/integrations/mcp/tool_adapter.py) provides JSON `content`, matching `structuredContent`, `isError` and bounded context metadata. The public result projection removes private candidate and specification fields. Invalid arguments, unknown tools and unavailable context retain distinct errors. stdout contains protocol data; logs and help use stderr.

## Generation and edits

The external client supplies model planning and output. `get_generation_context` returns the current specification, CPU-bound output contract, full-wire instructions and an optional snapshot-bound context ID. `create_program_candidate` accepts full or supported partial Ladder output. Argument fields and limits are owned by [plc_tools.py](../../src/agent_runtime/plc_tools.py), not a second MCP schema.

[CandidateService](../../src/plc/candidate_service.py) owns compatibility normalization, structural checking, materialization and artifact preparation. Current intent and optional source correlation follow [intent and evidence handoff](../architecture/intent-evidence-handoff.md). Ordinary edits use this shared generation path; explicit scoped debugging uses its existing strict patch path.

In standalone mode, candidate creation, patch preparation and GX import requests return `confirmation_required` without saving a version or executing GX. The process has no retained approval queue. Use the connected Web service to submit proposals for review and saving. A service result with a saved version ID identifies local persistence; external execution has its own result.

The tool surface does not expose arbitrary mouse/keyboard primitives, filesystem deletion, physical PLC writes or forced devices. Native operations use the application policy and bound artifacts.

## Verification

With the MCP environment active, run from the repository root:

```powershell
python -m pip install -r requirements/mcp.txt pytest
python -m pytest -q tests/test_mcp.py tests/test_architecture_boundaries.py
python scripts/mcp_smoke.py
```

[mcp_smoke.py](../../scripts/mcp_smoke.py) starts real stdio subprocesses against temporary projects and checks discovery, reads and confirmation-required candidate preparation. It checks that standalone workspaces remain unchanged. Missing SDK skips and unavailable GX checks must be reported separately from passes.

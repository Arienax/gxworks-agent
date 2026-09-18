"""Official MCP SDK server; transport is independent of runtime adaptation."""

from __future__ import annotations

import anyio
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
)

from agent_runtime.runtime import ToolRuntime, build_default_tool_runtime

from .context_provider import ToolContextProvider
from .tool_adapter import MCPToolAdapter


WORKFLOW_INSTRUCTIONS = (
    "When the user asks to use gxworks, the current project is the PLC project bound to this MCP server. "
    "Read get_current_project; do not scan source files or hand-write CSV/GXW to bypass the engineering tools. "
    "For generation or normal edits call get_generation_context with user_requirement containing the user's request. "
    "Use its generation_instructions, generation_request, confirmed specification and current program: "
    "these are the same engineering context, output guidance and local RAG used by the built-in API. "
    "Use search_plc_manual for remaining uncertain model or instruction facts. "
    "Design the ladder yourself and submit one full or partial response with create_program_candidate. "
    "The server uses the API compatibility normalization, structural acceptance and artifact generation, "
    "without a second model call or an automatic semantic repair loop. "
    "If a submission fails, report the returned error; do not start an automatic repeated-submission loop. "
    "Use read_network and patch_program for explicit scoped Debug patches. "
    "Report only actual tool results: structural acceptance is distinct from behavior, simulation and native validation. "
    "Never generate canonical IR or override server-owned project, profile, revision or approval fields. "
)


SERVER_INSTRUCTIONS = WORKFLOW_INSTRUCTIONS + (
    "GXWorks tools inspect the configured saved PLC project through ToolRuntime. "
    "create_program_candidate, patch_program and import_current_program_to_gxworks2 return confirmation_required; "
    "they do not save a version, change active_version_id or import into GX Works2. "
    "This standalone server has no approval or desktop bridge. MCP tool approval is not engineering confirmation. "
    "Report pending actions as pending. Never edit workspace records to bypass these tools. "
    "No physical PLC writes or low-level desktop controls are exposed."
)

SERVICE_SERVER_INSTRUCTIONS = WORKFLOW_INSTRUCTIONS + (
    "GXWorks tools use the running local engineering service's shared ToolRuntime. "
    "The service turns confirmation_required actions into proposals under the operator's existing approval policy. "
    "Report the actual proposal status: pending proposals need workbench review; only a saved version receipt proves saving. "
    "This MCP agent cannot approve pending proposals or grant permissions. "
    "Do not edit workspace records or call operator HTTP routes to bypass this boundary. "
    "No physical PLC writes or low-level desktop controls are exposed."
)


def create_server(
    context_provider: ToolContextProvider, runtime: ToolRuntime | None = None
) -> Server:
    adapter = MCPToolAdapter(
        runtime if runtime is not None else build_default_tool_runtime(), context_provider
    )
    return _create_adapter_server(adapter, SERVER_INSTRUCTIONS)


def create_service_server(client, project_id, version_id=None) -> Server:
    from .service_client import ServiceMCPToolAdapter

    return _create_adapter_server(ServiceMCPToolAdapter(client, project_id, version_id), SERVICE_SERVER_INSTRUCTIONS)


def _create_adapter_server(adapter, instructions):
    limiter = anyio.CapacityLimiter(1)

    async def list_tools(
        ctx: ServerRequestContext, params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=adapter.list_tools())

    async def call_tool(
        ctx: ServerRequestContext, params: CallToolRequestParams
    ) -> CallToolResult:
        return await anyio.to_thread.run_sync(
            adapter.call_tool, params.name, params.arguments, str(ctx.request_id),
            limiter=limiter,
        )

    return Server(
        "gxworks-agent", version="0.1.0", instructions=instructions,
        on_list_tools=list_tools, on_call_tool=call_tool,
    )


async def serve_stdio(context_provider: ToolContextProvider) -> None:
    # SDK v2 owns UTF-8 framing and diverts stray Python/native stdout to stderr.
    async with stdio_server() as (read_stream, write_stream):
        server = create_server(context_provider)
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


async def serve_service_stdio(client, project_id, version_id=None) -> None:
    async with stdio_server() as (read_stream, write_stream):
        server = create_service_server(client, project_id, version_id)
        await server.run(read_stream, write_stream, server.create_initialization_options())

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
from shared.project_info import PROJECT_URL

from .context_provider import ToolContextProvider
from .tool_adapter import MCPToolAdapter


WORKFLOW_INSTRUCTIONS = (
    f"GXWorks Agent project source and releases: {PROJECT_URL}. "
    "get_current_project queries Agent project selection; selection_status=none is normal and does not block inspection. "
    "For an already-open/imported program, call list_gxworks2_sessions, explicitly choose the matching session_id, "
    "then read_gxworks2_snapshot. Use its source_ref with get_current_program_info/read_network and static checks. "
    "A source_ref is immutable read-only analysis, not a saved editable version. Never select an unrelated project. "
    "For native validation, select the actual saved GXW version and call validate_project(native=true). "
    "Use read_native_device_assignments for current local/global declarations or FB members, compare_native_compiled_code "
    "for saved-versus-fresh code, and locate_native_code_steps for an explicit resource and step. "
    "Keep compilation, public_check, backend recheck, diagnostic_projection and inspection outcomes separate. "
    "A code difference is an observation; a match does not establish execution correspondence. "
    "For runtime faults, separately call list_simulator_targets and get_simulator_status/read_simulator_devices "
    "with the returned target_ref. get_diagnostics is static analysis; runtime reads do not prove program correspondence. "
    "For a generic live timeline, connect_simulator explicitly opens the existing Simulator2-only gateway, then "
    "start_simulator_trace takes target_ref and config.signals (id, label, address, data_type), sample_ms and duration_ms. "
    "It returns a Web view URL for all signals; no custom page code is needed. Use get_simulator_trace or stop_simulator_trace "
    "with view_ref. prepare_simulator_scenario lowers named initial/set/expect values into the existing version-bound test DSL; "
    "preparing a plan or observing its markers never executes stimulus writes. "
    "For generation, select/create the actual Agent project and get_generation_context. "
    "Follow structured next_actions when a precondition is missing. Do not scan source files or hand-write CSV/GXW to bypass the engineering tools. "
    "For generation or normal edits call get_generation_context with user_requirement containing the user's request. "
    "Use its generation_instructions, generation_request, confirmed specification and current program: "
    "these are the same engineering context, output guidance and local RAG used by the built-in API. "
    "Fresh Ladder requests use Direct generation from the complete original requirement. "
    "If a real process fact is missing, ask only the necessary question and do not submit a candidate until answered. "
    "Use search_plc_manual for remaining uncertain model or instruction facts. "
    "Design the ladder yourself and submit one full or partial response with create_program_candidate. "
    "The server uses the API compatibility normalization, structural acceptance and artifact generation, "
    "without a second model call or an automatic semantic repair loop. "
    "If a submission fails, report the returned error; do not start an automatic repeated-submission loop. "
    "Use read_network and patch_program for explicit scoped Debug patches. "
    "Use get_generation_context for the shared functional discovery, selection policy and source coverage; "
    "copy its generation_context_id when submitting a candidate. Discovery and local review do not call a model. "
    "For an FBD target use its supplied native catalog and current graph, then submit the native object model "
    "with create_fbd_candidate. Use the supplied source-bound ports and declaration tables, including "
    "direction-qualified IN_OUT endpoints. This path produces a GXW and SVG; it does not run GX Works2 compilation. "
    "Use read_fbd_libraries to inspect user-library definitions. To add a library to a saved FBD project, "
    "use create_fbd_candidate with operation=import_library, library_name and the donor GXW in data_base64; "
    "this imports definitions and library types, while preserving the target caller programs. "
    "Report only actual tool results: structural acceptance is distinct from behavior, simulation and native validation. "
    "Never generate canonical IR or override server-owned project, profile, revision or approval fields. "
)


SERVER_INSTRUCTIONS = WORKFLOW_INSTRUCTIONS + (
    "GXWorks tools inspect the configured saved PLC project through ToolRuntime. "
    "create_program_candidate, create_fbd_candidate, patch_program and import_current_program_to_gxworks2 return confirmation_required; "
    "they do not save a version, change active_version_id or import into GX Works2. "
    "This standalone server cannot approve, commit candidates or execute imports; read-only native snapshots remain available. "
    "MCP tool approval is not engineering confirmation. "
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


LOCAL_SERVER_INSTRUCTIONS = (
    "GXWorks Agent runs independently inside this MCP process; no Web workbench is required. "
    "For saved engineering work, use list_projects and select_project explicitly, or create_project with the user's actual "
    "plc_model and target_mode. Existing-program diagnosis can start with native discovery without any Agent project. "
    "Never create/select an unrelated project merely to satisfy a read precondition. "
) + WORKFLOW_INSTRUCTIONS + (
    "Explicit local generation and edits save validated versions through the shared application transaction. "
    "A saved result includes version_id; get_project_artifacts returns verified managed output paths. "
    "A saved behavior draft may remain inactive; preserve its actual save_result and validation limits. "
    "GX import uses operator-configured execution consent, the bound version and the existing native coordinator. "
    "execution_consent_required is pending; explain the startup configuration requirement without requesting workbench review. "
    "get_operation_status reads the actual proposal receipt. A failed or interrupted native result is not a successful import. "
    "Tool arguments cannot approve pending proposals or change execution permissions. "
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


def create_local_server(session) -> Server:
    from .local_adapter import LocalMCPToolAdapter

    return _create_adapter_server(LocalMCPToolAdapter(session), LOCAL_SERVER_INSTRUCTIONS)


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
        title="GXWorks Agent", website_url=PROJECT_URL,
        description=f"Local PLC engineering tools. Project source and releases: {PROJECT_URL}",
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


async def serve_local_stdio(session) -> None:
    async with stdio_server() as (read_stream, write_stream):
        server = create_local_server(session)
        await server.run(read_stream, write_stream, server.create_initialization_options())

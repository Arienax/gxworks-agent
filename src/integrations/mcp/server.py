# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

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
from shared.project_info import PROJECT_URL, MCP_SERVER_INFO

from .context_provider import ToolContextProvider
from .tool_adapter import MCPToolAdapter


READ_DELIVERY_EXTENSION = "io.github.arienax.gxworks-agent/read-delivery"


LEGACY_READ_INSTRUCTIONS = (
    "Read data.document directly when context_schema_version=4. This format has no transport references. "
    "When the client declares capacity, the server assembles the complete document within that capacity. "
    "A legacy client that receives delivery.next can follow it to the end; version 3 additionally requires resolving its object table. "
    "The host sets delivery capacity; it does not change the scope of technical retrieval. "
    "Delivery.complete reports server delivery, not proof that a host displayed all text. "
)

HOSTED_READ_INSTRUCTIONS = (
    "Read data.document directly. The host configures the delivery budget and assembles the document. "
    "If HOST_CONTEXT_TOO_LARGE is returned, request the specific missing facts or let the host adjust its configured capacity. "
    "Explicit read_format=paged is for legacy clients that assemble delivery.next themselves. "
)

WORKFLOW_INSTRUCTIONS = (
    f"GXWorks Agent project source and releases: {PROJECT_URL}. "
) + LEGACY_READ_INSTRUCTIONS + (
    "Construction/example limitations apply only to that implementation, not the whole instruction. "
    "Unconfirmed applicability conditions cannot exclude alternatives; keep them conditional. "
    "After saving, get_generation_context returns a version-bound local edit contract. Canonical ladder keeps compact partial syntax; "
    "reuse applicable manuals across saved versions and submit only changed rungs, without repeating selection. "
    "The server binds a unique generation task and resolves fact IDs locally. Supply one task reference only when selecting among concurrent tasks. "
    "Use previously delivered facts when marked $conversation_ref; gxworks_read_context_unit(ids=[...]) restores them together. "
    "top_k limits only supplementary search; explicit targets and their dependencies remain required. "
    "Manual search inherits the saved project language. Override language for a cross-language question. Report material local coverage gaps together; do not repeat a lookup or search the web solely because a fact is missing. Use returned operands, purpose, conditions and examples; "
    "coverage identifies answered aspects and additional information, while call fields describe current Core support. "
    "get_current_project queries Agent project selection; selection_status=none is normal and does not block inspection. "
    "Use list_gxworks2_sessions to check installation and the executable path, including when no window is open. "
    "Use gx_works2_installed and gx_works2_executable for installation, and sessions for visible windows. "
    "For an already-open/imported program, call list_gxworks2_sessions, explicitly choose the matching session_id, "
    "then read_gxworks2_snapshot. Use its source_ref with get_current_program_info/read_network and static checks. "
    "When GX Works2 is not running but the exported program CSV and comment CSV are at hand, call "
    "inspect_gxworks2_csv_files with both documents (data_base64 or path) and an explicit plc_model; it returns the same "
    "kind of source_ref without an Agent project. "
    "A source_ref is immutable read-only analysis, not a saved editable version. Never select an unrelated project. "
    "For native validation, select the actual saved GXW version and call validate_project(native=true). "
    "Use read_native_device_assignments for current local/global declarations or FB members, compare_native_compiled_code "
    "for saved-versus-fresh code, and locate_native_code_steps for an explicit resource and step. "
    "Keep compilation, public_check, backend recheck, diagnostic_projection and inspection outcomes separate. "
    "A code difference is an observation; a match does not establish execution correspondence. "
    "For runtime faults, separately call list_simulator_targets and get_simulator_status/read_simulator_devices "
    "with the returned target_ref. get_diagnostics is static analysis; runtime reads do not prove program correspondence. "
    "For a generic live timeline, connect_simulator(plc_model=actual_CPU) explicitly opens the existing Simulator2-only gateway for a registered FX/Q/L target, then "
    "start_simulator_trace takes target_ref and config.signals (id, label, address, data_type), sample_ms and duration_ms. "
    "It returns a Web view URL for all signals; no custom page code is needed. Use get_simulator_trace or stop_simulator_trace "
    "with view_ref. prepare_simulator_scenario lowers named initial/set/expect values into the existing version-bound test DSL; "
    "preparing a plan or observing its markers never executes stimulus writes. "
    "For generation, select/create the actual Agent project and get_generation_context. "
    "Generation and edits prefer a saved GXW for Ladder, ST, FBD and SFC. The application automatically compiles, saves and cold-reopens when GX Works2 is installed. "
    "Read the returned delivery and validation: CSV, ST text or structured candidates are explicit fallback outputs only. A failed native compile retains a diagnostic GXW, not a verified active version. "
    "For an editable implementation plan, read get_project_spec and prepare the requirements, IO, parameters and implementation_items in update_project_spec. "
    "Each implementation item states requirement_ids, language, location, method and state_owner; clarify real missing facts. "
    "Show the plan and differences to the user; call confirm_project_spec(user_confirmed=true) only after explicit approval, "
    "passing the exact current draft_revision as expected_revision. Do not treat the model's tool call as user approval. "
    "Record explicit user parameter changes with a scoped update. Material model-proposed implementation changes need renewed "
    "user confirmation before generation. Updates preserve other entries; historical version_spec remains a read-only reference. "
    "Then get_generation_context. Explicit workflow=direct_generation retains the existing direct path. "
    "Follow structured next_actions when a precondition is missing. Do not scan source files or hand-write CSV/GXW to bypass the engineering tools. "
    "For a new Direct task send the user's verbatim request. After Design confirmation, get_generation_context uses the saved specification and selected approach. For edits send only the user's current change. "
    "Use its generation_instructions, generation_request, confirmed specification and current program: "
    "these are the same engineering context, output guidance and local RAG used by the built-in API. "
    "In local mode, choose create_project(generation_mode=...) by specification completeness and follow create_project.next_actions: "
    "use generation_mode=direct for cross-vendor/cross-language program porting or translation (such as Siemens/CODESYS/Omron to Mitsubishi), already-specified sequences/timings/formulas, deterministic FB/FUNCTION authoring, or explicit direct requests; "
    "use generation_mode=design for open-ended process or machine requirements where comparing 1-3 approaches helps before human approval (get_design_context with the verbatim human requirement, read its document, propose_design_plan, wait for the human choice, confirm_design_plan, then get_generation_context(workflow=detailed)). "
    "If an unconfirmed Design session is active on Ladder, ST, FBD or SFC for a porting or already-specified task, call get_generation_context(workflow=direct_generation, user_requirement=...) to exit Design and generate directly instead of inventing artificial approaches. "
    "If a real process fact is missing, ask only the necessary question and do not submit a candidate until answered. "
    "Resume an existing Design with get_design_context without repeating the request. If several tasks are pending, choose an analysis_id or repair_id. Keep design choices separate. Use the delivered facts and batch only missing forms of instructions you intend to use. Suggested families are optional. In confirmed Design, implement the chosen plan without repeating comparisons. "
    "Read device numbering, default allocations and configurable conditions from device facts; "
    "read actual allocations and native access through current-project tools. "
    "Design the ladder yourself and submit one full or partial response with create_program_candidate. "
    "The server uses the API compatibility normalization, structural acceptance and artifact generation, "
    "without a second model call or an automatic semantic repair loop. "
    "If a submission fails, report the returned error; do not start an automatic repeated-submission loop. "
    "Use read_network and patch_program for explicit scoped Debug patches. "
    "Use get_generation_context for the shared functional discovery, selection policy and source coverage; "
    "the server binds a unique generation context. When several contexts are pending, reference the intended one using a field accepted by the submission tool. "
    "Discovery and local review do not call a model. "
    "For FBD, get_generation_context supplies generation_input.device_address_rules and language_rules, "
    "task-scoped interfaces, source_context, call_bindings, project_options and supported_paths. "
    "Use these mandatory rules; get_fbd_catalog alone is not a generation context and ST literal syntax is not an FBD substitute. "
    "get_generation_context defaults catalog_scope to task; get_fbd_catalog defaults to full. "
    "Use templates for exact additional interfaces and retain missing_templates as missing, without inventing ports. "
    "Follow output_contract: saved FBD sources expose edit_gxw_project with the exact version_id, "
    "so ST bodies, interfaces and all affected FBD callers can be checked together in one offline transaction. "
    "A new graph or a single-graph alternative uses create_fbd_candidate with the supplied parameter_schema. "
    "Use source-bound ports, declaration tables and direction-qualified IN_OUT endpoints. "
    "Preserve source_block, block titles/comments and shared-instance execution order when editing. "
    "New invalid FBD literals are rejected even with save_policy=draft; do not silently convert literals or round TIME. "
    "Read error.findings for the source, expression, port and local rule returned by source validation. "
    "Other known new source errors are rejected by checked; retained pre-existing diagnostics are not a compilation pass. "
    "Missing mandatory rules block generation; do not substitute another CPU or infer support from a template. "
    "Generation prefers GXW. Headless creation resolves GX Works2 programmable CPUs through GX Works2 1.635.0.1 and the v4 workspace adapter (CPU Program support is exposed upfront in output_contract.gxw_delivery; parameter-only targets or environments without GX Works2 fall back to source exports); "
    "when unavailable, return the language-specific fallback with its reason, without using IDE New dialogs or captured projects. "
    "For FBD import or edit, native_save controls candidate preparation for this call; "
    "shared candidate saving automatically compiles, saves and cold-reopens an available GXW when GX Works2 is found. "
    "Use read_fbd_libraries to inspect user-library definitions. To add a library to a saved FBD project, "
    "use create_fbd_candidate with operation=import_library, library_name and the donor GXW in data_base64; "
    "this transfers supported ST implementations, interfaces and library types while preserving target caller programs. "
    "Readable FBD library interfaces do not imply supported FBD implementation transfer. "
    "For a new ST project without GXW, create_st_candidate(operation=generate, st_code=...) uses the same source preparation "
    "and static inspection as Web generation, saves UTF-8 ST source through the shared transaction, and does not create GXW "
    "or perform native compilation. Source-only saved versions can be read with read_st_project and replaced with generate. "
    "For an existing ordinary ST Program use read_st_project and import the saved GXW with "
    "create_st_candidate(operation=import, program=<exact logical source>, data_base64=...); "
    "operation=generate prepares a new ST candidate from st_text and declarations; GXW creation requires the headless backend. "
    "create_st_program only adds a Program to an existing project with its actual resource and task. For background edits, first read the existing ST Program, FB "
    "or FUNCTION and reuse its exact logical source_name with create_st_candidate operation=edit_st_program, "
    "edit_st_fb, edit_st_function or edit_st_source. Omitted declarations preserve the current declarations; "
    "supplied declarations replace the complete declaration list. Candidates require confirmation before activation; "
    "report candidate saving, native compilation and execution separately. For saved Ladder/ST/FBD/SFC GXW native "
    "compilation and version delivery, prefer execute_native_engineering with plan.mode=background, schema_version=1 "
    "and commands compile, save, reopen in that order. This uses an isolated background native workspace without "
    "opening a visible editing session or writing a PLC; failures leave the active version unchanged. "
    "Use execute_native_engineering for explicitly requested visible "
    "edits in one native IDE session, including ordinary ST Programs and explicit existing FBD node/wire actions. "
    "For persistent foreground editing set execute_native_engineering plan.mode=live and session_action=start. "
    "The first start creates one managed working GXW copy per project and retains the original saved version. "
    "Later calls and MCP restarts reuse that path and the exact original GX Works2 window. Duplicate start returns "
    "the existing session without executing the submitted commands. Keep its server-issued session_id; read "
    "get_native_engineering_session, then continue with session_action=continue and controls.expected_revision. "
    "The active context spans calls without saving or reopening between edits. "
    "get_generation_context follows that active session unless execution_mode=background is explicit. "
    "Use pause_native_engineering_session to request a stop after the current step; resume pending steps with "
    "empty commands and controls.resume_pending=true. Status loads persisted state after MCP restart; rebind and "
    "continue validate the original PID, process start time, HWND, working path and CPU before mutation. Missing "
    "or mismatched targets require reconciliation, without recreation or replay. Incomplete older records fail "
    "explicitly without making another copy. Rebind only observes an interrupted action and never replays it. "
    "MCP shutdown detaches the recoverable session; explicit end ends it and leaves GX open. "
    "Adding an ST Program to an existing project requires its actual selected resource and task. Compile in that session before "
    "showing compiler-assigned addresses; each edit makes prior compilation and assignments stale. "
    "Save is an explicit phase boundary that can create an application version; active page changes and GX's "
    "own compiler Save are reported separately. Save acceptance ignores live timestamp contents while still "
    "checking actual source bodies, declarations and other substantive content. Reopen is an explicit "
    "representative persistence check. "
    "Report only actual tool results: structural acceptance is distinct from behavior, simulation and native validation. "
    "Never generate canonical IR or override server-owned project, profile, revision or approval fields. "
)


SERVER_INSTRUCTIONS = WORKFLOW_INSTRUCTIONS + (
    "GXWorks tools inspect the configured saved PLC project through ToolRuntime. "
    "update_project_spec, confirm_project_spec, create_program_candidate, create_fbd_candidate, create_st_candidate, edit_gxw_project, patch_program and import_current_program_to_gxworks2 return confirmation_required; "
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
    "plc_model; target_mode defaults to ladder. Existing-program diagnosis can start with native discovery without any Agent project. "
    "Never create/select an unrelated project merely to satisfy a read precondition. "
) + WORKFLOW_INSTRUCTIONS + (
    "Explicit local generation and edits save validated versions through the shared application transaction. "
    "A saved result includes version_id; get_project_artifacts returns verified managed output paths. "
    "A saved behavior draft may remain inactive; preserve its actual save_result and validation limits. "
    "GX import uses operator-configured execution consent; execute_native_engineering is a direct action that runs the bound version through the existing native coordinator without a startup approval mode. "
    "A pending status means the action did not execute; report it as pending without requesting workbench review. "
    "get_operation_status reads the actual proposal receipt. A failed or interrupted native result is not a successful import. "
    "Tool arguments cannot approve pending proposals or change execution permissions. "
    "No physical PLC writes or low-level desktop controls are exposed."
)


def create_server(
    context_provider: ToolContextProvider, runtime: ToolRuntime | None = None, *, read_budget_tokens=None,
) -> Server:
    adapter = MCPToolAdapter(
        runtime if runtime is not None else build_default_tool_runtime(), context_provider,
        read_budget_tokens=read_budget_tokens,
    )
    return _create_adapter_server(adapter, SERVER_INSTRUCTIONS)


def create_service_server(client, project_id, version_id=None, *, read_budget_tokens=None) -> Server:
    from .service_client import ServiceMCPToolAdapter

    return _create_adapter_server(ServiceMCPToolAdapter(client, project_id, version_id,
        read_budget_tokens=read_budget_tokens), SERVICE_SERVER_INSTRUCTIONS)


def create_local_server(session, *, read_budget_tokens=None) -> Server:
    from .local_adapter import LocalMCPToolAdapter

    return _create_adapter_server(LocalMCPToolAdapter(session, read_budget_tokens=read_budget_tokens),
                                  LOCAL_SERVER_INSTRUCTIONS)


def _create_adapter_server(adapter, instructions):
    if adapter.read_budget_tokens is not None:
        instructions = instructions.replace(LEGACY_READ_INSTRUCTIONS, HOSTED_READ_INSTRUCTIONS)
    limiter = anyio.CapacityLimiter(1)
    query_limiter = anyio.CapacityLimiter(4)
    cached_controls = {'get_operation_status', 'get_native_engineering_session',
                       'pause_native_engineering_session'}

    def read_budget(ctx):
        capacities = ctx.session.client_capabilities
        extensions = capacities.extensions if capacities else None
        advertised = ((extensions or {}).get(READ_DELIVERY_EXTENSION) or {}).get("readBudgetTokens")
        configured = adapter.read_budget_tokens
        if type(advertised) is int and 1000 <= advertised <= 128000:
            return advertised if configured is None else min(advertised, configured)
        return configured

    async def list_tools(
        ctx: ServerRequestContext, params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=adapter.list_tools(read_budget_tokens=read_budget(ctx)))

    async def call_tool(
        ctx: ServerRequestContext, params: CallToolRequestParams
    ) -> CallToolResult:
        budget = read_budget(ctx)
        return await anyio.to_thread.run_sync(
            lambda: adapter.call_tool(params.name, params.arguments, str(ctx.request_id), read_budget_tokens=budget),
            limiter=query_limiter if params.name in cached_controls else limiter,
        )

    return Server(
        MCP_SERVER_INFO["name"], version=MCP_SERVER_INFO["version"], instructions=instructions,
        title=MCP_SERVER_INFO["title"], website_url=MCP_SERVER_INFO["websiteUrl"],
        description=MCP_SERVER_INFO["description"],
        on_list_tools=list_tools, on_call_tool=call_tool,
    )


async def serve_stdio(context_provider: ToolContextProvider, read_budget_tokens=None) -> None:
    # SDK v2 owns UTF-8 framing and diverts stray Python/native stdout to stderr.
    async with stdio_server() as (read_stream, write_stream):
        server = create_server(context_provider, read_budget_tokens=read_budget_tokens)
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


async def serve_service_stdio(client, project_id, version_id=None, read_budget_tokens=None) -> None:
    async with stdio_server() as (read_stream, write_stream):
        server = create_service_server(client, project_id, version_id, read_budget_tokens=read_budget_tokens)
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def serve_local_stdio(session, read_budget_tokens=None) -> None:
    async with stdio_server() as (read_stream, write_stream):
        server = create_local_server(session, read_budget_tokens=read_budget_tokens)
        await server.run(read_stream, write_stream, server.create_initialization_options())

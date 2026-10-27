"""Optional MCP tests: deterministic fixtures, no GUI, vendor software or network."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("mcp", reason="Install requirements-mcp.txt for MCP integration tests")

import anyio
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from integrations.mcp.context_provider import (
    ContextUnavailableError,
    SessionToolContextProvider,
    StaticToolContextProvider,
)
from integrations.mcp.server import create_server
from integrations.mcp.tool_adapter import MCPToolAdapter, to_mcp_result
from agent_runtime.messages import ToolCall, ToolResult
from agent_runtime.plc_tools import (
    FORBIDDEN_TOOL_NAMES,
    SAFE_TOOL_NAMES,
    ToolDefinition,
    ToolRegistry,
    build_tool_context,
)
from plc.core import PLCCore
from plc.ir import build_plc_ir, canonical_sha256
from application.session import ProjectSession as SessionStore
from agent_runtime.runtime import InProcessToolRuntime, build_default_tool_runtime


ROOT = Path(__file__).resolve().parents[1]


def _mcp_body(result):
    body = json.loads(result.content[0].text)
    if result.structured_content is not None:
        assert body == result.structured_content
    return body


def _finish_mcp_read(adapter, result):
    from application.read_delivery import assemble_read_pages, serialized_size
    pages = []
    while True:
        page = _mcp_body(result)
        assert not result.is_error, page
        assert result.structured_content is None
        size = serialized_size(result.model_dump(by_alias=True, exclude_none=True))
        assert size['estimated_tokens'] <= 6000 and size['utf8_bytes'] <= 32768
        pages.append(page)
        continuation = page['data']['delivery']['next']
        if continuation is None:
            return assemble_read_pages(pages), pages
        result = adapter.call_tool(continuation['tool'], continuation['arguments'], f"continuation-{len(pages)}")


async def _complete_client_read(client, name, arguments, initial=None):
    from application.read_delivery import assemble_read_pages, serialized_size
    pages = []
    while True:
        result = initial if initial is not None else await client.call_tool(name, arguments)
        initial = None
        page = _mcp_body(result)
        assert not result.is_error, page
        size = serialized_size(result.model_dump(by_alias=True, exclude_none=True))
        assert size['estimated_tokens'] <= 6000 and size['utf8_bytes'] <= 32768
        pages.append(page)
        continuation = page['data']['delivery']['next']
        if continuation is None:
            return assemble_read_pages(pages)
        name, arguments = continuation['tool'], continuation['arguments']


def _complete_runtime_read(runtime, call, context):
    from application.read_delivery import assemble_read_pages
    pages = []
    while True:
        result = runtime.invoke(call, context)
        assert not result.is_error, result.data
        pages.append(result.data)
        continuation = result.data['data']['delivery']['next']
        if continuation is None:
            return assemble_read_pages(pages)
        call = ToolCall(call.id, continuation['tool'], continuation['arguments'])




@pytest.mark.parametrize('mode', ['ask', 'full'])
def test_native_engineering_mcp_uses_shared_approval_and_public_saved_result(tmp_path, monkeypatch, mode):
    from concurrent.futures import Future
    from application.engineering_session import EngineeringSession
    from application.execution import GXExecutionCoordinator
    from application.fbd import prepare_candidate
    from integrations.mcp.local_adapter import LocalMCPToolAdapter
    from tests.test_gxworks2_live_boundaries import _visible_cross_reference_witness
    from tests.test_execution_coordinator import native_delivery_receipt
    raw = _visible_cross_reference_witness()
    store = SessionStore(base_dir=tmp_path / 'workspace', legacy_dir=tmp_path / 'legacy')
    project = store.create_project('Visible native MCP', plc_model='FX3U', target_mode='fbd')
    version_id, output = store.prepare_version(project['id'])
    prepared = prepare_candidate(output, imported=raw, program_name='POU_01.Program.pou')
    store.complete_version(project['id'], version_id, {**prepared['metadata'],
        'artifacts': {name: entry['path'] for name, entry in prepared['artifacts'].items()}}, activate=True)
    calls = []
    def execute(_self, operation, payload, **options):
        assert operation == 'native_engineering' and options['approval_id']
        calls.append(copy.deepcopy(payload))
        future = Future(); future.set_result({'status': 'completed', 'passed': True,
            '_native_result': native_delivery_receipt(raw, payload['plan']['commands'])})
        return future
    monkeypatch.setattr(GXExecutionCoordinator, 'submit_approved', execute)
    session = EngineeringSession(store.base_dir, project_id=project['id'], state_dir=tmp_path / 'state',
        approval_mode=mode, confirm_full_access=mode == 'full')
    adapter = LocalMCPToolAdapter(session)
    schema = next(tool for tool in adapter.list_tools() if tool.name == 'execute_native_engineering')
    assert not schema.annotations.read_only_hint
    assert schema.annotations.destructive_hint
    plan = {'schema_version': 1, 'commands': [{'operation': 'save'}, {'operation': 'reopen'}]}
    result = adapter.call_tool('execute_native_engineering', {'plan': plan}, 'visible-native')
    assert not result.is_error, result
    public = _mcp_body(result)
    if mode == 'ask':
        assert public['status'] == 'execution_consent_required' and not calls
        assert store.get_project(project['id'])['active_version_id'] == version_id
    else:
        assert public['data']['proposal_status'] == 'accepted' and len(calls) == 1
        final = public['data']['execution_result']['version_id']
        assert final != version_id and store.get_project(project['id'])['active_version_id'] == final
        assert public['data']['execution_result']['gx_compile_status'] == 'not_run'
        assert public['data']['execution_result']['simulation_status'] == 'not_run'
    encoded = result.model_dump_json()
    assert 'private' not in encoded and 'observation' not in encoded and '_native_result' not in encoded
    replay = adapter.call_tool('execute_native_engineering', {'plan': plan}, 'visible-native')
    assert _mcp_body(replay) == public and len(calls) == int(mode == 'full')
    assert store.get_version(project['id'], version_id)['id'] == version_id


def test_local_and_service_mcp_share_persisted_live_session_and_saved_revision_receipts(tmp_path, monkeypatch):
    import io
    from concurrent.futures import Future
    from types import SimpleNamespace
    from application.engineering_session import EngineeringSession
    from application.execution import GXExecutionCoordinator
    from application.fbd import prepare_candidate
    from application.live_engineering import LiveEngineeringSession
    from gxworks2.live_driver import LiveEngineeringDriver
    from integrations.mcp.local_adapter import LocalMCPToolAdapter
    from integrations.mcp.service_client import ApplicationServiceClient, ServiceMCPToolAdapter
    from tests.test_gxworks2_live_boundaries import _LiveNativePages, _visible_cross_reference_witness
    from tests.test_live_engineering_session import authorize_offline_live_pages, current_saved_bytes
    raw = _visible_cross_reference_witness()
    store = SessionStore(base_dir=tmp_path / 'workspace', legacy_dir=tmp_path / 'legacy')
    project = store.create_project('Persistent live receipt', plc_model='FX3U', target_mode='fbd')
    base, output = store.prepare_version(project['id'])
    candidate = prepare_candidate(output, imported=raw, program_name='POU_01.Program.pou')
    store.complete_version(project['id'], base, {**candidate['metadata'],
        'artifacts': {name: entry['path'] for name, entry in candidate['artifacts'].items()}}, activate=True)
    pages = _LiveNativePages(tmp_path / 'controlled.gxw', raw)
    adapter = SimpleNamespace(_pending={}, pending_operations=lambda: [], read_graph=lambda driver, owner, kind:
        copy.deepcopy(next(row for row in pages.context['graphs'] if (row['owner'], row['owner_kind']) == (owner, kind))))
    live = LiveEngineeringDriver(pages, raw, 'FX3U', adapter)
    controller = LiveEngineeringSession(pages, 'FX3U', raw, 'b' * 32, project['id'], base, live_driver=live,
                                        operation_authorizer=authorize_offline_live_pages)
    snapshot = pages.snapshot
    def saved_pages(**kwargs):
        pages.saved_output = current_saved_bytes(raw, pages)
        return snapshot(**kwargs)
    monkeypatch.setattr(pages, 'snapshot', saved_pages)
    calls = []
    def execute(_self, operation, payload, **options):
        assert operation == 'native_engineering' and options['approval_id']
        calls.append(copy.deepcopy(payload))
        receipt = controller.run(payload['plan']['commands'], payload['plan'].get('controls'))
        future = Future()
        future.set_result({'status': receipt['status'], 'passed': receipt['passed'], '_live_result': receipt})
        return future
    monkeypatch.setattr(GXExecutionCoordinator, 'submit_approved', execute)
    def rebase(_self, project_id, session_id, old, new):
        assert (project_id, session_id, old) == (project['id'], controller.session_id, base)
        assert store.get_project(project_id)['active_version_id'] == new
        controller.set_base_version(new)
        return controller.status()
    monkeypatch.setattr(GXExecutionCoordinator, 'rebase_live_session', rebase)
    with EngineeringSession(store.base_dir, project_id=project['id'], state_dir=tmp_path / 'state',
            approval_mode='full', confirm_full_access=True) as session:
        local = LocalMCPToolAdapter(session)
        command = dict(operation='edit_global_labels', source_name=pages.context['inventory']['globals'][0],
                       declarations=[dict(name='Live_Flag', data_type='BOOL', device='M50')])
        result = local.call_tool('execute_native_engineering', {'plan': dict(schema_version=1, mode='live',
            session_action='start', commands=[command, {'operation': 'save'}])}, 'live-save')
        assert not result.is_error, _mcp_body(result)
        completed = _mcp_body(result)
        receipt = completed['data']['execution_result']
        assert receipt['mode'] == 'live' and receipt['session_id'] == 'b' * 32
        assert receipt['revision'] == receipt['saved_revision'] == receipt['data_revision'] == 1
        assert receipt['dirty'] is False and receipt['base_version_id'] == base
        assert receipt['version_id'] != base and receipt['version_id'] == completed['version_id']
        assert receipt['version_revision'] == receipt['data_revision']
        assert receipt['version_matches_active_source'] is True
        assert receipt['steps'][0]['operation'] == 'edit_global_labels'
        assert receipt['steps'][0]['verification']['status'] == 'matched'
        assert receipt['steps'][1]['operation'] == 'save' and receipt['steps'][1]['verification']['status'] == 'passed'
        assert receipt['stage_saves'][0]['revision'] == 1 and 'Live_Flag' in json.dumps(receipt['current_context'])
        operation_id = completed['execution_job_id']
        proposal_id = completed['proposal_id']
        assert len(calls) == pages.saves == 1
        assert session.service.proposals.get(proposal_id)['result']['session_id'] == 'b' * 32
        assert session.service.jobs.get(operation_id)['result']['result']['revision'] == 1
        replay = local.call_tool('execute_native_engineering', {'plan': dict(schema_version=1, mode='live',
            session_action='start', commands=[command, {'operation': 'save'}])}, 'live-save')
        assert _mcp_body(replay) == completed and len(calls) == 1
        queried = local.call_tool('get_operation_status', {'execution_job_id': operation_id}, 'local-live-receipt')
        assert _mcp_body(queried)['data']['execution_result']['steps'] == receipt['steps']
        class SharedService:
            def open(self, request, timeout):
                body = json.loads(request.data)
                assert request.full_url.endswith('/api/agent/tools/call')
                return io.BytesIO(json.dumps(session.service.agent_call(body)).encode())
        remote = ServiceMCPToolAdapter(ApplicationServiceClient('http://127.0.0.1:8765',
            'isolated-agent-token', opener=SharedService()), project['id'])
        remote_result = remote.call_tool('get_operation_status', {'execution_job_id': operation_id}, 'service-live-receipt')
        assert not remote_result.is_error, _mcp_body(remote_result)
        remote_receipt = _mcp_body(remote_result)['data']['execution_result']
        assert remote_receipt == receipt
        for encoded in (result.model_dump_json(), queried.model_dump_json(), remote_result.model_dump_json()):
            assert not any(key in encoded for key in ('project_path', 'window_handle', 'process_started',
                'raw_bytes', '_stage_context', '_stage_compile', '_live_result', str(tmp_path).replace('\\', '\\\\')))
        assert len(calls) == pages.saves == 1


@pytest.mark.parametrize('extra', [{'pid': 123}, {'path': r'D:\unmanaged.gxw'}, {'approval_mode': 'full'}])
def test_native_engineering_mcp_schema_rejects_driver_and_consent_arguments(extra):
    from tests.test_gxworks2_live_boundaries import _visible_cross_reference_witness
    context = build_tool_context({'id': 'project', 'plc_model': 'FX3U', 'target_mode': 'fbd'},
        version={'id': 'v1', 'plc_model': 'FX3U', 'target_mode': 'fbd'},
        fbd_baseline=_visible_cross_reference_witness())
    adapter = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context))
    result = adapter.call_tool('execute_native_engineering', {'plan': {'schema_version': 1,
        'commands': [{'operation': 'save', **extra}]}}, 'rejected-native')
    assert result.is_error and _mcp_body(result)['error']['code'] == 'INVALID_ARGUMENTS'


@pytest.mark.parametrize('tool,arguments,key', [
    ('read_native_device_assignments', {'queries': [{'source': '1', 'name': 'TIMER_A'}]}, 'assignments'),
    ('read_native_device_assignments', {'queries': [{'scope': 'global', 'source': 'Global1.gh', 'name': 'TIMER_A'}]}, 'assignments'),
    ('compare_native_compiled_code', {}, 'saved_code'),
    ('locate_native_code_steps', {'queries': [{'resource': 'Sample', 'step': 12562}]}, 'locations'),
])
def test_native_inspection_mcp_and_local_use_same_version_bound_service_without_writes(tmp_path, monkeypatch, tool, arguments, key):
    from gxw.object_model import generate_object_project
    from tests.test_gxw_object_model import two_timers
    from application.engineering_session import EngineeringSession
    from integrations.mcp.local_adapter import LocalMCPToolAdapter
    from agent_runtime.plc_tools import READ_ONLY_TOOL_NAMES
    raw = generate_object_project(two_timers()).data
    if any(q.get('scope') == 'global' for q in arguments.get('queries', [])):
        from tests.test_gxw_object_model import source_binding_shadow_baseline
        raw = source_binding_shadow_baseline()
    context = build_tool_context({'id': 'project', 'plc_model': 'FX3U'},
        version={'id': 'v0001', 'plc_model': 'FX3U', 'target_mode': 'fbd', 'program_name': '1.Program.pou'}, native_snapshot=raw)
    calls = []
    def check(data, **options):
        assert data == raw and options['project_id'] == 'project' and options['version_id'] == 'v0001'
        assert options['expected_cpu'] == 'FX3U' and options['program'] == '1.Program.pou'
        queries = options['inspection']; calls.append(copy.deepcopy(queries))
        assert queries[key] == (True if key == 'saved_code' else arguments['queries'])
        return {'snapshot': {'project_id': 'project', 'version_id': 'v0001'},
                'compilation': {'completed': True, 'acceptance': 'accepted'},
                'public_check': {'completed': False, 'status': 'incomplete'},
                'diagnostic_projection': {'completed': False, 'empty_public_list_means_no_errors': False},
                'inspection': {'source_context': 'current', 'execution_correspondence': 'unverified',
                               'assignments': [], 'locations': [], 'saved_code': {'status': 'differs'}},
                '_native_workspace': {'slot': 56}}
    monkeypatch.setattr('application.native_validation.validate_native_snapshot', check)
    adapter = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context))
    schema = next(t for t in adapter.list_tools() if t.name == tool)
    assert schema.annotations.read_only_hint and tool in READ_ONLY_TOOL_NAMES
    standalone = adapter.call_tool(tool, arguments, 'offline')
    assert not standalone.is_error and _mcp_body(standalone)['data']['public_check']['status'] == 'incomplete'
    Draft202012Validator(schema.output_schema).validate(_mcp_body(standalone))
    assert '_native_workspace' not in standalone.model_dump_json()
    session = EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state')
    session.project_id = 'project'
    monkeypatch.setattr(session.service.projects, 'tool_context', lambda *_: context)
    monkeypatch.setattr(session.service, 'start', lambda: pytest.fail('Read-only native inspection must not initialize a writer'))
    local = LocalMCPToolAdapter(session).call_tool(tool, arguments, 'local')
    assert not local.is_error and _mcp_body(local) == _mcp_body(standalone)
    assert len(calls) == 2 and not session.workspace.exists() and not session.service.state_dir.exists()
    missing = adapter.runtime.invoke(ToolCall('missing', tool, arguments), build_tool_context({'id': 'project'}))
    assert missing.is_error and missing.data['error']['code'] == 'NO_PROGRAM_SOURCE'
    assert len(calls) == 2


@pytest.mark.parametrize('tool,arguments', [
    ('read_native_device_assignments', {'queries': []}),
    ('read_native_device_assignments', {'queries': [{'source': '1', 'name': 'old_instance'}]}),
    ('read_native_device_assignments', {'queries': [{'source': '1', 'name': 'TIMER_A', 'slot': 56}]}),
    ('read_native_device_assignments', {'queries': [{'scope': 'global', 'source': '1', 'name': 'TIMER_A'}]}),
    ('read_native_device_assignments', {'queries': [{'scope': 'global', 'source': 'Global1.gh', 'name': 'TIMER_A'}]}),
    ('read_native_device_assignments', {'queries': [{'scope': 'unknown', 'source': '1', 'name': 'TIMER_A'}]}),
    ('locate_native_code_steps', {'queries': [{'resource': 'Sample', 'step': True}]}),
    ('locate_native_code_steps', {'queries': [{'resource': 'Sample', 'step': -1}]}),
    ('locate_native_code_steps', {'queries': [{'resource': 'Sample', 'step': 0}] * 65}),
    ('compare_native_compiled_code', {'installation': 'arbitrary'}),
])
def test_native_inspection_mcp_rejects_unbound_queries_before_vendor_calls(monkeypatch, tool, arguments):
    from gxw.object_model import generate_object_project
    from tests.test_gxw_object_model import two_timers
    raw = generate_object_project(two_timers()).data
    context = build_tool_context({'id': 'project', 'plc_model': 'FX3U'},
        version={'id': 'v0001', 'target_mode': 'fbd', 'program_name': '1.Program.pou'}, native_snapshot=raw)
    monkeypatch.setattr('application.native_validation.validate_native_snapshot', lambda *_a, **_k: pytest.fail('No native call is allowed'))
    result = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context)).call_tool(tool, arguments, 'invalid')
    assert result.is_error
    assert _mcp_body(result)['error']['code'] == 'INVALID_ARGUMENTS'


def test_local_mcp_saves_validated_generation_once_without_workbench(empty_project, tmp_path):
    from application.engineering_session import EngineeringSession
    from integrations.mcp.local_adapter import LocalMCPToolAdapter

    store, project_id = empty_project
    session = EngineeringSession(store.base_dir, project_id=project_id, state_dir=tmp_path / "state")
    adapter = LocalMCPToolAdapter(session)
    assert session.check()["approval_mode"] == "ask"
    assert not session.service.state_dir.exists()
    result = adapter.call_tool("create_program_candidate", {"ladder": _generation_ladder()}, "candidate")
    assert not result.is_error
    public = _mcp_body(result)
    assert public["status"] == "saved"
    assert public["data"]["requires_confirmation"] is False
    version_id = public["data"]["version_id"]
    project = store.get_project(project_id)
    assert project["active_version_id"] == version_id and len(project["versions"]) == 1
    assert store.load_program_ir(project_id, version_id)["networks"][0]["id"] == "N0001"
    assert "_candidate_ir" not in result.model_dump_json() and "pending_action" not in result.model_dump_json()
    assert session.service.approval.read() == {"mode": "ask", "revision": 0}
    assert session.service.proposals.get(public["proposal_id"])["summary"]["approval"]["source"] == "local_autosave"
    replay = adapter.call_tool("create_program_candidate", {"ladder": _generation_ladder()}, "candidate")
    assert _mcp_body(replay) == public and len(store.get_project(project_id)["versions"]) == 1
    changed = adapter.call_tool("create_program_candidate", {"ladder": {}}, "candidate")
    assert changed.is_error and _mcp_body(changed)["error"]["code"] == "CONFLICT"
    paths = adapter.call_tool("get_project_artifacts", {}, "artifacts").structured_content["data"]
    assert paths["version_id"] == version_id
    for artifact in paths["artifacts"]:
        assert Path(artifact["path"]).is_relative_to(store.version_dir(project_id, version_id))
        assert Path(artifact["path"]).is_file()


def test_local_mcp_patch_saves_and_retains_the_explicit_historical_baseline(saved_project, tmp_path):
    from application.engineering_session import EngineeringSession
    from integrations.mcp.local_adapter import LocalMCPToolAdapter

    store, project_id, version, program = saved_project
    session = EngineeringSession(store.base_dir, project_id=project_id, version_id=version["id"], state_dir=tmp_path / "state")
    adapter = LocalMCPToolAdapter(session)
    from plc.ir import ir_to_ladder
    rung = ir_to_ladder(program)["rungs"][0]
    rung["branches"][0]["inputs"].append({"type": "NC", "address": "X1", "label": ""})
    patch = {"operations": [{"operation": "modify_network", "network": "N0001", "ladder": rung}]}
    result = adapter.call_tool("patch_program", {"patch": patch}, "patch")
    assert not result.is_error and _mcp_body(result)["status"] == "saved"
    new_version = _mcp_body(result)["data"]["version_id"]
    assert store.get_project(project_id)["active_version_id"] == new_version
    # A pinned historical edit remains a branch of that explicit baseline.
    branch = adapter.call_tool("patch_program", {"patch": patch}, "branch")
    assert not branch.is_error and len(store.get_project(project_id)["versions"]) == 3
    assert store.get_version(project_id, _mcp_body(branch)["data"]["version_id"])["parent_version_id"] == version["id"]
    assert session.version_id == version["id"]


def test_local_mcp_project_selection_and_argument_boundaries(tmp_path):
    from application.engineering_session import EngineeringSession, SESSION_TOOL_NAMES
    from integrations.mcp.local_adapter import LocalMCPToolAdapter

    session = EngineeringSession(tmp_path / "workspace", state_dir=tmp_path / "state")
    adapter = LocalMCPToolAdapter(session)
    assert {tool.name for tool in adapter.list_tools()} == set(SAFE_TOOL_NAMES) | set(SESSION_TOOL_NAMES)
    assert not session.workspace.exists() and not session.service.state_dir.exists()
    missing = adapter.call_tool("get_current_project", {}, "none")
    assert not missing.is_error
    assert _mcp_body(missing)["data"] == {
        "selection_status": "none", "project": None,
        "next_actions": [{"tool": "list_projects"}, {"tool": "list_gxworks2_sessions"}]}
    assert not session.workspace.exists() and not session.service.state_dir.exists()
    arguments = {"name": "Direct MCP", "plc_model": "FX3U", "target_mode": "ladder"}
    created = adapter.call_tool("create_project", arguments, "create")
    assert not created.is_error
    project_id = _mcp_body(created)["data"]["selected_project_id"]
    assert adapter.call_tool("create_project", arguments, "create").structured_content == _mcp_body(created)
    assert len(session.service.projects.list_projects()) == 1
    listed = adapter.call_tool("list_projects", {}, "list").structured_content["data"]
    assert listed["selected_project_id"] == project_id and len(listed["projects"]) == 1
    unknown = adapter.call_tool("select_project", {"project_id": "missing"}, "wrong")
    assert unknown.is_error and session.project_id == project_id
    for name, arguments in (
        ("create_project", {**arguments, "approval_mode": "full"}),
        ("create_program_candidate", {"ladder": _generation_ladder(), "direct_request": True}),
        ("create_program_candidate", {"ladder": _generation_ladder(), "project_id": "other"}),
        ("import_current_program_to_gxworks2", {"confirm_full_access": True}),
        ("approve_proposal", {}), ("write_plc", {}),
    ):
        result = adapter.call_tool(name, arguments, "rejected-" + name)
        assert result.is_error
    assert session.service.store.get_project(project_id)["versions"] == []
    assert session.service.approval.read()["mode"] == "ask"


def test_local_mcp_sessions_keep_ownership_until_explicit_close_and_never_bypass_a_writer(tmp_path):
    from application.engineering_session import EngineeringSession
    from application.workspace import WorkspaceWriterLock, WorkspaceBusyError

    first = EngineeringSession(tmp_path / "workspace", state_dir=tmp_path / "state")
    second = EngineeringSession(first.workspace, state_dir=first.service.state_dir)
    created = first.invoke(ToolCall("create", "create_project", {"name": "Shared", "plc_model": "FX3U", "target_mode": "ladder"}))
    project_id = created.data["data"]["selected_project_id"]
    assert first.service.lock.acquired
    jobs, execution = first.service.jobs, first.service.execution
    busy = second.invoke(ToolCall('busy-select', 'select_project', {'project_id': project_id}))
    assert busy.is_error and busy.data['error']['code'] == 'WORKSPACE_BUSY'
    with pytest.raises(WorkspaceBusyError), WorkspaceWriterLock(first.workspace):
        pass
    try:
        assert not first.invoke(ToolCall("observe", "get_current_project", {})).is_error
        assert (first.service.jobs, first.service.execution) == (jobs, execution)
    finally:
        first.close()
    selected = second.invoke(ToolCall('select', 'select_project', {'project_id': project_id}))
    assert not selected.is_error and first.project_id == second.project_id
    second.close()
    first.close()
    assert first.invoke(ToolCall('closed', 'list_projects', {})).data['error']['code'] == 'SESSION_CLOSED'


@pytest.mark.parametrize("source_type", ["fbd", "sfc", "st"])
def test_local_mcp_source_import_saves_original_bytes_through_shared_transaction(tmp_path, source_type):
    import base64
    from application.engineering_session import EngineeringSession
    from integrations.mcp.local_adapter import LocalMCPToolAdapter

    if source_type == "fbd":
        from tests.test_gxw_object_model import source_binding_baseline
        raw = source_binding_baseline()
    elif source_type == 'st':
        from tests.test_execution_coordinator import native_st_source_witness
        raw = native_st_source_witness()
    else:
        from tests.test_gxw_lossless import sfc_object_edit_witness
        raw = sfc_object_edit_witness()["shared-save/input.gxw"]
    session = EngineeringSession(tmp_path / "workspace", state_dir=tmp_path / "state")
    adapter = LocalMCPToolAdapter(session)
    created = adapter.call_tool("create_project", {"name": source_type, "plc_model": "FX3U",
        "target_mode": source_type if source_type in ('fbd', 'st') else "ladder"}, "create")
    assert not created.is_error
    project_id = session.project_id
    arguments = {"operation": "import", "data_base64": base64.b64encode(raw).decode()}
    if source_type == 'st':
        read = adapter.call_tool('read_st_project', {'data_base64': arguments['data_base64']}, 'read-upload')
        assert not read.is_error, _mcp_body(read)
        program, = _mcp_body(read)['data']['programs']
        assert program['kind'] == 'program' and program['language'] == 'st'
        arguments['program'] = program['program']
    result = adapter.call_tool("create_" + source_type + "_candidate", arguments, "import")
    assert not result.is_error, _mcp_body(result)
    assert _mcp_body(result)["status"] == "saved"
    version_id = _mcp_body(result)["data"]["version_id"]
    assert session.service.projects.raw_version(project_id, version_id)["target_mode"] == source_type
    assert session.service.projects.artifact(project_id, version_id, "gxw").read_bytes() == raw
    if source_type == 'st':
        version = session.service.projects.raw_version(project_id, version_id)
        assert version['program_name'] == arguments['program']
        assert version['validation']['gx_compile'] == 'not_run'
        assert version['validation']['source_binding']['task_bindings'] == program['task_bindings']
        assert session.service.projects.artifact(project_id, version_id, 'st').read_bytes() == program['st_text'].encode('utf-8')
        report = json.loads(session.service.projects.artifact(project_id, version_id, 'write_report').read_bytes())
        assert report['source']['selected_program'] == program
    assert "pending_action" not in result.model_dump_json()
    assert session.service.lock.acquired
    session.close()


@pytest.mark.parametrize("mode,outcome", [("ask", "completed"), ("full", "completed"), ("full", "unavailable")])
def test_local_mcp_native_dispatch_requires_startup_consent_and_returns_actual_receipt(saved_project, tmp_path, monkeypatch, mode, outcome):
    from concurrent.futures import Future
    from application.engineering_session import EngineeringSession
    from application.execution import GXExecutionCoordinator
    from integrations.mcp.local_adapter import LocalMCPToolAdapter

    store, project_id, version, _ = saved_project
    calls = []
    def execute(self, operation, payload, **values):
        calls.append((operation, payload, values))
        future = Future()
        future.set_result({"status": outcome, "passed": outcome == "completed", "operation": operation})
        return future
    monkeypatch.setattr(GXExecutionCoordinator, "submit_approved", execute)
    with pytest.raises(PermissionError):
        EngineeringSession(store.base_dir, approval_mode="full")
    session = EngineeringSession(store.base_dir, project_id=project_id, state_dir=tmp_path / "state",
        approval_mode=mode, confirm_full_access=mode == "full")
    adapter = LocalMCPToolAdapter(session)
    result = adapter.call_tool("import_current_program_to_gxworks2", {}, "import")
    public = _mcp_body(result)
    assert len(calls) == (1 if mode == "full" else 0)
    if mode == "ask":
        assert not result.is_error and public["status"] == "execution_consent_required"
        assert "--confirm-full-access" in public["data"]["message"]
    elif outcome == "completed":
        assert not result.is_error and public["data"]["execution_result"]["passed"] is True
        assert calls[0][1]["version_id"] == version["id"] and calls[0][2]["approval_id"]
    else:
        assert result.is_error and public["data"]["execution_result"]["status"] == "unavailable"
    receipt = adapter.call_tool("get_operation_status", {"proposal_id": public["proposal_id"]}, "status")
    assert _mcp_body(receipt)["data"]["proposal"]["status"] == public["data"]["proposal_status"]
    if mode == 'full':
        assert public['execution_job_id'].startswith('job_')
        job_receipt = adapter.call_tool('get_operation_status', {'execution_job_id': public['execution_job_id']}, 'job-status')
        data = _mcp_body(job_receipt)['data']
        assert data['proposal_id'] == public['proposal_id'] and data['execution_job_id'] == public['execution_job_id']
        assert data['execution_result'] == _mcp_body(receipt)['data']['execution_result']
    replay = adapter.call_tool("import_current_program_to_gxworks2", {}, "import")
    assert _mcp_body(replay) == public and len(calls) == (1 if mode == "full" else 0)
    assert store.get_project(project_id)["active_version_id"] == version["id"]


def test_local_operation_query_uses_one_shared_schema_and_receipt_reader(tmp_path):
    from application.engineering_session import EngineeringSession
    from integrations.mcp.local_adapter import LocalMCPToolAdapter
    from agent_runtime.messages import ToolCall

    session = EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state')
    adapter = LocalMCPToolAdapter(session)
    schemas = [tool for tool in adapter.list_tools() if tool.name == 'get_operation_status']
    assert len(schemas) == 1 and schemas[0].annotations.read_only_hint
    shared = next(tool for tool in build_default_tool_runtime().list_tools()
                  if tool['function']['name'] == 'get_operation_status')
    assert schemas[0].input_schema == shared['function']['parameters']
    for arguments in ({'proposal_id': 'proposal1'}, {'execution_job_id': 'job1'}):
        assert list(Draft202012Validator(schemas[0].input_schema).iter_errors(arguments)) == []
    for arguments in ({}, {'proposal_id': 'proposal1', 'execution_job_id': 'job1'}):
        assert list(Draft202012Validator(schemas[0].input_schema).iter_errors(arguments))
    result = adapter.call_tool('get_operation_status', {'proposal_id': 'proposal1'}, 'unselected-status')
    assert _mcp_body(result)['error']['code'] == 'NO_PROJECT_SELECTED'
    unavailable = build_default_tool_runtime().invoke(ToolCall('unbound', 'get_operation_status',
        {'proposal_id': 'proposal1'}), build_tool_context({'id': 'selected-project'}))
    assert unavailable.data['error']['code'] == 'OPERATION_STATUS_UNAVAILABLE'


def test_local_mcp_waits_for_the_real_job_and_reads_its_receipt_without_restarting(saved_project, tmp_path, monkeypatch):
    from concurrent.futures import Future
    from application.engineering_session import EngineeringSession
    from application.execution import GXExecutionCoordinator

    store, project_id, _, _ = saved_project
    executions = []
    def execute(_self, operation, payload, **options):
        executions.append((operation, options['approval_id']))
        future = Future()
        future.set_result({'status': 'completed', 'passed': True})
        return future
    monkeypatch.setattr(GXExecutionCoordinator, 'submit_approved', execute)
    with EngineeringSession(store.base_dir, project_id=project_id, state_dir=tmp_path / 'state',
                            approval_mode='full', confirm_full_access=True) as session:
        session.service.start()
        jobs, execution, waited = session.service.jobs, session.service.execution, []
        wait = jobs.wait
        def observed_wait(job_id, timeout=None):
            waited.append(job_id)
            return wait(job_id, timeout)
        call = ToolCall('wait-real-job', 'import_current_program_to_gxworks2', {})
        with monkeypatch.context() as scoped:
            scoped.setattr(jobs, 'wait', observed_wait)
            scoped.setattr(jobs, 'shutdown', lambda **_: pytest.fail('A tool call must retain its worker'))
            result = session.invoke(call)
            assert not result.is_error, result.data
            assert waited == [result.data['execution_job_id']]
            assert jobs.get(waited[0])['status'] == 'completed'
            assert session.invoke(call).data == result.data
            assert len(executions) == 1 and len(waited) == 1
            scoped.setattr(session.service, 'start', lambda: pytest.fail('Read-only calls must not start a writer'))
            scoped.setattr(session.service.store, 'migrate_confirmed_specs',
                           lambda: pytest.fail('Read-only calls must not migrate projects'))
            receipt = session.invoke(ToolCall('read-real-job', 'get_operation_status',
                {'execution_job_id': waited[0]}))
            assert not receipt.is_error and receipt.data['data']['execution_job_id'] == waited[0]
            assert not session.invoke(ToolCall('read-current', 'get_current_project', {})).is_error
            assert (session.service.jobs, session.service.execution) == (jobs, execution)


@pytest.mark.parametrize('tool,arguments', [
    ('get_operation_status', {'proposal_id': 'proposal_inflight'}),
    ('get_native_engineering_session', {'session_id': 'a' * 32}),
    ('pause_native_engineering_session', {'session_id': 'a' * 32}),
])
def test_local_cached_queries_and_pause_do_not_wait_for_an_inflight_native_call(
        saved_project, tmp_path, monkeypatch, tool, arguments):
    import threading
    from concurrent.futures import Future, ThreadPoolExecutor
    from application.engineering_session import EngineeringSession
    from application.execution import GXExecutionCoordinator

    store, project_id, _, _ = saved_project
    entered, pending, queries = threading.Event(), Future(), []
    def execute(_self, operation, payload, **options):
        entered.set()
        return pending
    monkeypatch.setattr(GXExecutionCoordinator, 'submit_approved', execute)
    def cached(project, *args, **values):
        queries.append((project, args, values))
        return {'project_id': project, 'status': 'running', 'query_number': len(queries)}
    with EngineeringSession(store.base_dir, project_id=project_id, state_dir=tmp_path / 'state',
                            approval_mode='full', confirm_full_access=True) as session:
        monkeypatch.setattr(session.service, 'operation_status', cached)
        monkeypatch.setattr(session.service, 'native_session_status', cached, raising=False)
        monkeypatch.setattr(session.service, 'native_session_pause', cached, raising=False)
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(session.invoke, ToolCall('inflight-import', 'import_current_program_to_gxworks2', {}))
            try:
                assert entered.wait(10)
                with monkeypatch.context() as scoped:
                    scoped.setattr(session.service, 'start', lambda: pytest.fail('A cache query cannot start a writer'))
                    scoped.setattr(session.service.store, 'migrate_confirmed_specs',
                                   lambda: pytest.fail('A cache query cannot migrate projects'))
                    query = ToolCall('current-receipt', tool, arguments)
                    first = pool.submit(session.invoke, query).result(3)
                    second = pool.submit(session.invoke, query).result(3)
                    assert not first.is_error and not second.is_error
                    assert first.data['data']['query_number'] == 1 and second.data['data']['query_number'] == 2
                    assert all(item[0] == project_id for item in queries) and not writer.done()
                    invalid = pool.submit(session.invoke,
                        ToolCall('invalid-receipt', tool, {**arguments, 'unknown': True})).result(3)
                    assert invalid.data['error']['code'] == 'INVALID_ARGUMENTS' and len(queries) == 2
            finally:
                if not pending.done(): pending.set_result({'status': 'completed', 'passed': True})
                assert not writer.result(10).is_error


def test_local_completed_response_retains_real_job_and_saved_version_receipts():
    from types import SimpleNamespace
    from application.engineering_session import EngineeringSession

    session = object.__new__(EngineeringSession)
    proposal = {'id': 'proposal_existing', 'action': 'native_engineering', 'status': 'accepted',
                'result': {'status': 'completed', 'version_id': 'v0002'}}
    session.service = SimpleNamespace(proposals=SimpleNamespace(get=lambda identifier: proposal))
    response = {'data': {'ok': True, 'data': {}}, 'proposal_id': 'proposal_existing',
                'execution_job_id': 'job_existing'}
    result = session._completed_response(response)
    assert result['proposal_id'] == 'proposal_existing' and result['execution_job_id'] == 'job_existing'
    assert result['version_id'] == result['data']['execution_result']['version_id'] == 'v0002'
    assert result['data']['proposal_status'] == 'accepted'


def test_local_mcp_permission_escalation_does_not_execute_an_old_pending_action(saved_project, tmp_path, monkeypatch):
    from application.engineering_session import EngineeringSession
    from application.execution import GXExecutionCoordinator

    store, project_id, _, _ = saved_project
    session = EngineeringSession(store.base_dir, project_id=project_id, state_dir=tmp_path / "state")
    call = ToolCall("pending-import", "import_current_program_to_gxworks2", {})
    pending = session.invoke(call)
    monkeypatch.setattr(GXExecutionCoordinator, "submit_approved", lambda *a, **k: pytest.fail("Old pending request must not execute"))
    session.close()
    full = EngineeringSession(store.base_dir, project_id=project_id, state_dir=session.service.state_dir,
        approval_mode="full", confirm_full_access=True)
    replay = full.invoke(call)
    assert replay.data == pending.data and replay.data["status"] == "execution_consent_required"
    full.close()


def test_local_mcp_real_stdio_creates_and_saves_without_web_or_model_credentials(tmp_path):
    from mcp_test_support import isolated_mcp_environment
    from application.engineering_session import SESSION_TOOL_NAMES

    wrapper = tmp_path / "independent.py"
    wrapper.write_text('''import importlib.abc
import sys
import types
def forbidden(*args, **kwargs):
    raise AssertionError('Independent engineering tools must not access model or Web credentials')
sys.modules['storage.windows_credentials'] = types.SimpleNamespace(read_secret=forbidden, write_secret=forbidden, delete_secret=forbidden)
class BlockWeb(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('integrations.web') or fullname.split('.')[0] in {'fastapi', 'openai', 'PyQt5', 'PyQt6', 'pywinauto', 'win32com'}:
            raise AssertionError('Unexpected Web/provider/native dependency: ' + fullname)
sys.meta_path.insert(0, BlockWeb())
from integrations.mcp.__main__ import main
raise SystemExit(main())
''', encoding="utf-8")
    workspace = tmp_path / "workspace"
    parameters = StdioServerParameters(command=sys.executable,
        args=[str(wrapper)], cwd=tmp_path,
        env=isolated_mcp_environment(PYTHONPATH=str(ROOT / "src"), PLC_AI_WORKSPACE_DIR="",
                                    GXWORKS_MCP_WORKSPACE_DIR=str(workspace), PYTHONIOENCODING="utf-8"))
    async def exercise():
        with anyio.fail_after(40):
            with (tmp_path / "stderr.txt").open("w", encoding="utf-8") as errors:
                async with stdio_client(parameters, errlog=errors) as streams:
                    async with ClientSession(*streams) as client:
                        initialized = await client.initialize()
                        assert "no Web workbench is required" in initialized.instructions
                        assert "read_native_device_assignments" in initialized.instructions
                        assert "does not establish execution correspondence" in initialized.instructions
                        assert {tool.name for tool in (await client.list_tools()).tools} == set(SAFE_TOOL_NAMES) | set(SESSION_TOOL_NAMES)
                        unselected = await client.call_tool("get_current_project", {})
                        assert not unselected.is_error and _mcp_body(unselected)["data"]["selection_status"] == "none"
                        created = await client.call_tool("create_project", {"name": "Direct", "plc_model": "FX3U", "target_mode": "ladder"})
                        assert not created.is_error
                        context = await client.call_tool("get_generation_context", {"user_requirement": "X0 start, X1 stop, Y0 motor, stop priority", "workflow": "direct_generation"})
                        assert not context.is_error
                        candidate = await client.call_tool("create_program_candidate", {"ladder": _generation_ladder(),
                            "generation_context_id": (await _complete_client_read(client, "get_generation_context", {}, initial=context))["generation_context_id"]})
                        assert not candidate.is_error and _mcp_body(candidate)["status"] == "saved"
                        artifacts = await client.call_tool("get_project_artifacts", {})
                        assert not artifacts.is_error and _mcp_body(artifacts)["data"]["artifacts"]
                        assert all(Path(item["path"]).is_file() for item in _mcp_body(artifacts)["data"]["artifacts"])
                        return _mcp_body(created)["data"]["selected_project_id"], _mcp_body(candidate)["data"]["version_id"]
    project_id, version_id = anyio.run(exercise)
    store = SessionStore(base_dir=workspace, create=False)
    assert store.get_project(project_id)["active_version_id"] == version_id
    assert len(store.get_project(project_id)["versions"]) == 1


def _generation_ladder():
    return {
        "device_comments": {"X0": "Start", "X1": "Stop", "Y0": "Motor"},
        "rungs": [{
            "rung_id": 1, "header_element": None, "shared_inputs": [],
            "branches": [{
                "branch_id": 1, "y_offset_level": 0,
                "inputs": [
                    {"type": "parallel_block", "branches": [
                        [{"type": "NO", "address": "X0"}],
                        [{"type": "NO", "address": "Y0"}],
                    ]},
                    {"type": "NC", "address": "X1"},
                ],
                "outputs": [{"type": "COIL", "address": "Y0"}],
            }],
        }],
    }


@pytest.fixture
def empty_project(tmp_path):
    store = SessionStore(base_dir=tmp_path / "empty-workspace", legacy_dir=tmp_path)
    project = store.create_project("Natural-language generation", plc_model="FX3U")
    store.set_confirmed_spec(project["id"], {
        "summary": "X0 Start, X1 Stop, Y0 Motor with seal-in",
        "io_table": [
            {"address": "X0", "kind": "X", "label": "Start"},
            {"address": "X1", "kind": "X", "label": "Stop"},
            {"address": "Y0", "kind": "Y", "label": "Motor"},
        ],
        "selected_approach": {"name": "Seal-in", "generation_contract": {"required_structures": ["self_hold"]}},
    })
    return store, project["id"]


@pytest.fixture
def saved_project(tmp_path):
    ladder = {
        "device_comments": {"X0": "启动", "Y0": "运行"},
        "rungs": [{
            "rung_id": 1, "debug_note": "启动输出", "header_element": None,
            "shared_inputs": [],
            "branches": [{
                "branch_id": 1, "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": "X0", "label": ""}],
                "outputs": [{"type": "COIL", "address": "Y0", "label": ""}],
            }],
        }],
    }
    program = build_plc_ir(ladder, plc_model="FX3U", revision=1)
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("MCP 测试项目")
    version_id, directory = store.prepare_version(project["id"])
    compiled = PLCCore().compile_project(program, directory)
    version = store.complete_version(project["id"], version_id, {
        **store._ir_metadata(program), "target_mode": "ladder", "plc_model": "FX3U",
        "artifacts": dict(compiled["artifacts"]),
    })
    return store, project["id"], version, program


def _provider(saved_project, *, pin=False):
    store, project_id, version, _ = saved_project
    return SessionToolContextProvider(
        store.base_dir, project_id, version["id"] if pin else None
    )


def _files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize('cpu,has_wrong_baseline', [
    ('Q00J', True), ('Q03UDV', True), ('L02', True), ('FX1N', True),
    ('FX5U', False), ('LJ72GF15-T2', False), ('FX1', False), ('FX1', True)])
@pytest.mark.parametrize('tool', ['get_fbd_catalog', 'get_generation_context'])
def test_fbd_mcp_rejects_unbound_cpu_before_catalog_or_retrieval(monkeypatch, cpu, has_wrong_baseline, tool):
    import application.fbd as fbd
    from gxw.object_model import default_baseline

    calls = []
    monkeypatch.setattr(fbd, 'catalog_snapshot', lambda *a, **k: calls.append('catalog'))
    monkeypatch.setattr(fbd, 'build_fbd_generation_context', lambda *a, **k: calls.append('retrieval'))
    context = build_tool_context({'id': 'cpu-bound-fbd', 'target_mode': 'fbd', 'plc_model': cpu},
        fbd_baseline=default_baseline() if has_wrong_baseline else None)
    adapter = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context))
    result = adapter.call_tool(tool, {}, 'cpu-guard')
    assert result.is_error
    message = _mcp_body(result)['error']['message']
    assert 'CPU' in message or 'template' in message
    assert calls == []


@pytest.mark.parametrize('cpu,menu', [('Q00J', 'Q00J'), ('L02-P', 'L02/L02-P'),
    ('FX3UC', 'FX3U/FX3UC'), ('FXU/FX2C', 'FXU/FX2C')])
def test_fbd_mcp_catalog_uses_current_cpu_blank_template_without_import(cpu, menu):
    from gxworks2.project_profiles import native_project_profile
    context = build_tool_context({'id': 'blank-fbd', 'target_mode': 'fbd', 'plc_model': cpu})
    adapter = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context))
    result = adapter.call_tool('get_fbd_catalog', {}, 'blank-catalog')
    assert not result.is_error, _mcp_body(result)
    catalog, _ = _finish_mcp_read(adapter, result)
    assert native_project_profile(catalog['cpu']).cpu == menu
    assert len(catalog['generation_plc_models']) == 56 and 'FX1' not in catalog['generation_plc_models']
    assert catalog['unsupported_generation_plc_models'][0]['cpu'] == 'FX1'
    assert catalog['empty_model']['nodes'] == [] and catalog['empty_model']['wires'] == []
    assert sorted(catalog['declaration_tables'].values()) == ['global', 'local']
    assert catalog['task_scopes']['tasks'][0]['programs']


@pytest.mark.parametrize('cpu', ['FX3U', 'FX3UC', 'FX3U/FX3UC'])
def test_fbd_mcp_catalog_preserves_imported_merged_cpu_scope(cpu):
    from gxw.object_model import default_baseline

    context = build_tool_context({'id': 'cpu-bound-fbd', 'target_mode': 'fbd', 'plc_model': cpu},
        fbd_baseline=default_baseline())
    adapter = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context))
    result = adapter.call_tool('get_fbd_catalog', {}, 'catalog')
    assert not result.is_error
    assert _finish_mcp_read(adapter, result)[0]['cpu'] == 'FX3U/FX3UC'


def test_fbd_tools_bind_native_sources_and_keep_standalone_candidate_pending(tmp_path):
    from application.fbd import prepare_candidate
    from gxw.editor import edit_draft
    from tests.test_gxw_object_model import source_binding_baseline

    store = SessionStore(base_dir=tmp_path / 'workspace', legacy_dir=tmp_path)
    project = store.create_project('Custom FBD', target_mode='fbd')
    version_id, directory = store.prepare_version(project['id'])
    raw = source_binding_baseline()
    prepared = prepare_candidate(directory, imported=raw)
    store.complete_version(project['id'], version_id, {**prepared['metadata'],
        'artifacts': {name: entry['path'] for name, entry in prepared['artifacts'].items()}}, activate=True)
    provider = SessionToolContextProvider(store.base_dir, project['id'], version_id)
    assert StaticToolContextProvider(provider.get_context()).get_context().fbd_baseline == raw
    before = _files(store.base_dir)
    adapter = MCPToolAdapter(build_default_tool_runtime(), provider)
    catalog, _ = _complete_mcp_read(adapter, 'get_fbd_catalog', {})
    assert (catalog['schema_version'], catalog['cpu']) == (2, 'FX3U/FX3UC')
    read = adapter.call_tool('read_fbd_project', {}, 'read').structured_content['data']
    graph = read['model']
    assert graph['schema_version'] == 2
    assert [p['name'] for p in graph['nodes'][0]['ports']] == ['SIGNAL', 'in.STATE', 'RESULT', 'out.STATE']
    changed = edit_draft(graph, {'action': 'update_node', 'id': graph['nodes'][0]['id'],
        'field': 'symbol', 'value': 'MCP_STAGE_A'}, templates=catalog['nodes'])['model']
    result = adapter.call_tool('create_fbd_candidate', {'operation': 'edit', 'model': changed}, 'edit')
    assert not result.is_error
    assert _mcp_body(result)['status'] == 'confirmation_required'
    data = _mcp_body(result)['data']
    assert data['model']['schema_version'] == 2 and 'MCP_STAGE_A' in data['svg']
    assert data['pending_action']['base_version_id'] == version_id
    assert 'verification' not in data
    assert '"_fbd_candidate"' not in result.content[0].text
    assert _files(store.base_dir) == before


@pytest.mark.parametrize('cpu,saved', [('FX3U', False), ('Q13UDV', False), ('FX3U', True)])
def test_fbd_mcp_generation_delivers_cpu_rules_and_executable_output_contract(cpu, saved):
    from tests.test_gxw_offline_authoring import native_project
    from integrations.web.responses import AgentToolResult

    raw = native_project() if saved else None
    version = {'id': 'v1', 'target_mode': 'fbd', 'program_name': 'POU_01.Program.pou'} if saved else None
    context = build_tool_context({'id': 'fbd-rules', 'plc_model': cpu, 'target_mode': 'fbd'},
        version=version, fbd_baseline=raw, native_snapshot=raw)
    adapter = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context))
    result = adapter.call_tool('get_generation_context',
        {'user_requirement': 'Change ScaleValue and its callers' if saved else 'Move D0 into D10'}, 'fbd-context')
    assert not result.is_error, _mcp_body(result)
    data, pages = _finish_mcp_read(adapter, result)
    assert pages[-1]['data']['delivery']['complete'] is True
    response = {'name': 'get_generation_context', 'call_id': 'fbd-context', 'is_error': False,
                'content': result.content[0].text, 'data': _mcp_body(result)}
    assert AgentToolResult.model_validate(response).model_dump(mode='json', by_alias=True, exclude_unset=True) == response
    engineering = data['generation_input']
    assert engineering['device_address_rules']['cpu'] == cpu
    assert engineering['device_address_rules']['status'] == 'available'
    assert engineering['device_address_rules']['families']['X']['radix'] == (8 if cpu == 'FX3U' else 16)
    assert engineering['language_rules']['rules']['FBD.LITERALS']
    assert not engineering['language_rules']['mandatory_missing']
    assert engineering['catalog_scope'] == 'task'
    contract = data['output_contract']
    schemas = {tool.name: tool.input_schema for tool in adapter.list_tools()}
    if saved:
        assert contract['tool'] == 'edit_gxw_project' and contract['version_id'] == 'v1'
        assert 'ScaleValue.Program.pou' in contract['editable_sources']
        assert engineering['source_context']['st_sources']
        assert engineering['source_context']['fbd_callers']
        assert contract['alternatives'][0]['tool'] == 'create_fbd_candidate'
        Draft202012Validator(schemas[contract['tool']]).validate({
            'version_id': contract['version_id'], 'device_comments': {'D0': 'input'}})
    else:
        assert contract['tool'] == 'create_fbd_candidate' and contract['operation'] == 'generate'
        assert not engineering['supported_paths']['joint_multi_source_transaction']['supported']
        assert 'alternatives' not in contract
        Draft202012Validator(schemas[contract['tool']]).validate({
            'operation': contract['operation'], 'model': {'networks': []}})


def test_fbd_mcp_contract_submits_st_and_caller_as_one_offline_candidate(tmp_path, monkeypatch):
    from application.fbd import unpack_candidate
    from gxw.object_model import read_project_context
    from gxw.offline_project import read_offline_project
    from gxw.text_pou import parse_st_pou
    from tests.test_gxw_offline_authoring import native_project

    raw = native_project()
    selected = 'POU_01.Program.pou'
    context = build_tool_context({'id': 'joint-mcp', 'plc_model': 'FX3U', 'target_mode': 'fbd'},
        version={'id': 'v1', 'target_mode': 'fbd', 'program_name': selected},
        fbd_baseline=raw, native_snapshot=raw)
    runtime = build_default_tool_runtime()
    invoke, private_results = runtime.invoke, []

    def observe(call, bound_context):
        result = invoke(call, bound_context)
        private_results.append(result)
        return result

    monkeypatch.setattr(runtime, 'invoke', observe)
    adapter = MCPToolAdapter(runtime, StaticToolContextProvider(context))
    response = adapter.call_tool('get_generation_context',
        {'user_requirement': 'Change ScaleValue and its caller'}, 'context')
    assert not response.is_error, _mcp_body(response)
    data, pages = _finish_mcp_read(adapter, response)
    definition = next(row for row in data['generation_input']['source_context']['st_sources']
                      if row['source_name'] == 'ScaleValue.Program.pou')
    body = definition['st_text'] + '\n(* MCP joint source edit *)'
    contract = data['output_contract']
    result = adapter.call_tool(contract['tool'], {'version_id': contract['version_id'], 'save_policy': 'checked',
        'edits': [dict(source_name=definition['source_name'], command=dict(st_text=body)),
                  dict(source_name=selected, command=dict(model=dict(program=selected, networks=[dict(calls=[
                      dict(id='scale', type='ScaleValue', instance='ScaleInst',
                           arguments=dict(Enabled='TRUE', InValue='D0', OutValue='D40'))])])))]}, 'joint')
    assert not result.is_error, _mcp_body(result)
    assert _mcp_body(result)['status'] == 'confirmation_required'
    assert {row['source_name'] for row in _mcp_body(result)['data']['edited_sources']} == {
        selected, definition['source_name']}
    assert '"_gxw_candidate"' not in result.content[0].text
    pending = private_results[-1].data['data']['pending_action']['_gxw_candidate']
    payload = unpack_candidate(pending, tmp_path)
    saved = (tmp_path / payload['artifacts']['gxw']['path']).read_bytes()
    assert parse_st_pou(read_offline_project(saved).streams[definition['source_name']]).text == body
    assert any(node.symbol == 'D40' for node in read_project_context(saved, selected).program.nodes)
    assert context.version_id == 'v1' and context.native_snapshot == raw


@pytest.mark.parametrize('policy', ['draft', 'checked'])
def test_fbd_mcp_source_rejection_preserves_literal_findings(policy):
    from gxw.project_templates import blank_fbd_baseline
    from gxw.object_model import read_project_context
    from integrations.web.responses import AgentToolResult

    raw = blank_fbd_baseline('FX3U')
    source = read_project_context(raw).program.logical_name
    context = build_tool_context({'id': 'invalid-fbd', 'plc_model': 'FX3U', 'target_mode': 'fbd'},
        version={'id': 'v1', 'target_mode': 'fbd', 'program_name': source},
        fbd_baseline=raw, native_snapshot=raw)
    adapter = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context))
    result = adapter.call_tool('edit_gxw_project', {'version_id': 'v1', 'save_policy': policy,
        'edits': [{'source_name': source, 'command': {'model': {'program': source, 'networks': [{'nodes': [
            dict(id='move', template='function:MOV', symbol='MOV',
                 arguments=dict(EN='TRUE', s='INT#0', d='D0'))]}]}}}]}, 'bad-literal')
    assert result.is_error
    error = _mcp_body(result)['error']
    assert any(row['code'] == 'fbd_literal_syntax' and row['expression'] == 'INT#0'
               and row['source_name'] == source for row in error['findings'])
    assert 'pending_action' not in _mcp_body(result)
    public = {'name': 'edit_gxw_project', 'call_id': 'bad-literal', 'is_error': True,
              'content': result.content[0].text, 'data': _mcp_body(result)}
    assert AgentToolResult.model_validate(public).data.error.code == error['code']


def test_mcp_creates_empty_fb_in_an_isolated_candidate_and_keeps_approval(tmp_path, monkeypatch):
    from application.fbd import prepare_candidate, unpack_candidate
    from gxw.native_write import verify_native_source_creation
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from tests.test_gxw_object_model import source_creation_witness
    import application.execution as execution
    raw, saved, case = source_creation_witness()
    native_lock = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: native_lock(tmp_path / 'native.lock'))
    calls = []

    def create(_self, baseline, name):
        assert baseline == raw and name == case['created_name']
        calls.append(True)
        return verify_native_source_creation(baseline, name, saved, case['native_creation'])

    monkeypatch.setattr(NativeWorkspaceSourceSave, 'create', create)
    store = SessionStore(base_dir=tmp_path / 'workspace', legacy_dir=tmp_path)
    project = store.create_project('New native FB', target_mode='fbd')
    version, directory = store.prepare_version(project['id'])
    prepared = prepare_candidate(directory, imported=raw)
    store.complete_version(project['id'], version, {**prepared['metadata'],
        'artifacts': {name: row['path'] for name, row in prepared['artifacts'].items()}}, activate=True)
    before = _files(store.base_dir)
    runtime = build_default_tool_runtime()
    invoke = runtime.invoke
    private_results = []

    def observe(call, context):
        result = invoke(call, context)
        if not result.is_error:
            private_results.append(result.data)
        return result

    monkeypatch.setattr(runtime, 'invoke', observe)
    adapter = MCPToolAdapter(runtime, SessionToolContextProvider(store.base_dir, project['id'], version))
    schema = next(tool.input_schema for tool in adapter.list_tools() if tool.name == 'create_fbd_candidate')
    assert 'create_fb' in schema['properties']['operation']['enum']
    bad = adapter.call_tool('create_fbd_candidate', {'operation': 'create_fb'}, 'missing-name')
    assert bad.is_error and not calls
    result = adapter.call_tool('create_fbd_candidate', {'operation': 'create_fb', 'source_name': case['created_name']}, 'create')
    assert not result.is_error
    assert _mcp_body(result)['status'] == 'confirmation_required'
    data = _mcp_body(result)['data']
    assert data['model']['program'] == case['created_program'] and not data['model']['nodes']
    assert data['model']['schema_version'] == 2
    assert 'verification' not in data
    assert data['pending_action']['base_version_id'] == version
    assert '"_fbd_candidate"' not in result.content[0].text
    pending = private_results[0]['data']['pending_action']['_fbd_candidate']
    validation = pending['metadata']['validation']
    assert validation['native_source_save_required'] is False
    assert validation['native_save'] == 'passed'
    assert validation['native_save_requested'] is False
    assert validation['gx_compile'] == 'not_run'
    assert data['requires_confirmation'] is True
    assert data['pending_action']['type'] == 'accept_fbd_candidate'
    unpack_candidate(pending, tmp_path / 'reviewed')
    assert (tmp_path / 'reviewed' / 'program.gxw').read_bytes() == saved
    assert _files(store.base_dir) == before
    assert calls == [True]


@pytest.mark.parametrize('operation,native_save,changed', [
    (operation, requested, True) for operation in ('edit', 'generate') for requested in (None, False, True)
] + [('edit', True, False), ('generate', True, False)])
def test_mcp_fbd_save_uses_the_current_call_and_only_changed_source(tmp_path, monkeypatch, operation, native_save, changed):
    import application.fbd as fbd
    from gxw.object_model import read_project_context
    from gxw.project_templates import blank_fbd_baseline
    from gxw.project_writer import ProjectWriteResult, write_prepared_project

    raw = blank_fbd_baseline('FX3U')
    graph = read_project_context(raw).object_model()
    if changed:
        graph.update(nodes=[dict(id='signal', template='input', symbol='X0', x=2, y=2),
                            dict(id='coil', template='output', symbol='Y0', x=10, y=2)],
                     wires=[{'from': 'signal.OUT', 'to': 'coil.IN'}])
    version = {'id': 'v0001', 'plc_model': 'FX3U', 'target_mode': 'fbd',
               'validation': {'native_save': 'passed', 'native_save_requested': True,
                              'native_source_save_required': True}}
    context = build_tool_context({'id': 'selected', 'plc_model': 'FX3U', 'target_mode': 'fbd'},
        version=copy.deepcopy(version) if operation == 'edit' else None,
        fbd_baseline=raw if operation == 'edit' else None)
    calls = []

    def native(action, prepared):
        # Transport double: this checks routing and readback, not GX acceptance.
        assert action == 'save'
        calls.append(action)
        result = write_prepared_project(prepared)
        return ProjectWriteResult(result.data, {**result.report, 'validation': {'native_save': 'passed'}})

    monkeypatch.setattr(fbd, '_native_candidate', native)
    arguments = {'operation': operation, 'model': graph}
    if native_save is not None:
        arguments['native_save'] = native_save
    result = build_default_tool_runtime().invoke(ToolCall('current-save', 'create_fbd_candidate', arguments), context)
    assert not result.is_error, result.data
    assert result.data['status'] == 'confirmation_required'
    data = result.data['data']
    assert data['requires_confirmation'] is True
    pending = data['pending_action']
    assert pending['type'] == 'accept_fbd_candidate'
    assert pending['base_version_id'] == ('v0001' if operation == 'edit' else None)
    snapshot = pending['_fbd_candidate']
    validation = snapshot['metadata']['validation']
    should_save = native_save is True and changed
    assert calls == (['save'] if should_save else [])
    assert validation['native_save'] == ('passed' if should_save else 'not_run')
    assert validation['native_save_requested'] is (native_save is True)
    assert validation['native_source_save_required'] is False
    assert validation['gx_compile'] == 'not_run'
    fbd.unpack_candidate(snapshot, tmp_path / 'candidate')
    saved = read_project_context((tmp_path / 'candidate/program.gxw').read_bytes()).object_model()
    assert [row['symbol'] for row in saved['nodes']] == (['X0', 'Y0'] if changed else [])
    assert context.fbd_baseline == (raw if operation == 'edit' else None)
    assert context.version == (version if operation == 'edit' else None)


@pytest.mark.parametrize('operation', ['edit', 'generate'])
def test_mcp_explicit_fbd_save_failure_does_not_produce_a_candidate(monkeypatch, operation):
    import application.fbd as fbd
    from gxw.object_model import read_project_context
    from gxw.project_templates import blank_fbd_baseline
    from gxw.models import GXWFormatError
    raw = blank_fbd_baseline('FX3U')
    graph = read_project_context(raw).object_model()
    graph.update(nodes=[dict(id='signal', template='input', symbol='X0', x=2, y=2),
                        dict(id='coil', template='output', symbol='Y0', x=10, y=2)],
                 wires=[{'from': 'signal.OUT', 'to': 'coil.IN'}])
    context = build_tool_context({'id': 'selected', 'plc_model': 'FX3U', 'target_mode': 'fbd'},
        version={'id': 'v0001', 'target_mode': 'fbd'} if operation == 'edit' else None,
        fbd_baseline=raw if operation == 'edit' else None)
    calls = []

    def fail(action, prepared):
        calls.append(action)
        raise GXWFormatError('native save refused')

    monkeypatch.setattr(fbd, '_native_candidate', fail)
    result = build_default_tool_runtime().invoke(ToolCall('save-failure', 'create_fbd_candidate',
        {'operation': operation, 'model': graph, 'native_save': True}), context)
    assert result.is_error and calls == ['save']
    assert 'pending_action' not in result.data.get('data', {})
    assert context.fbd_baseline == (raw if operation == 'edit' else None)


@pytest.mark.parametrize('operation,value', [(operation, value) for operation in ('edit', 'generate', 'import')
                                           for value in (None, 1, 'true')] + [('create_fb', True)])
def test_mcp_fbd_native_save_requires_a_boolean_on_supported_operations(monkeypatch, operation, value):
    import application.fbd as fbd
    monkeypatch.setattr(fbd, 'prepare_candidate', lambda *a, **kw: pytest.fail('invalid save arguments reached a writer'))
    context = build_tool_context({'id': 'selected', 'plc_model': 'FX3U', 'target_mode': 'fbd'})
    arguments = {'operation': operation, 'native_save': value,
                 **({'data_base64': 'eA=='} if operation == 'import' else
                    {'source_name': 'NewFB'} if operation == 'create_fb' else {'model': {}})}
    adapter = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context))
    result = adapter.call_tool('create_fbd_candidate', arguments, 'invalid-save')
    assert result.is_error and _mcp_body(result)['error']['code'] == 'INVALID_ARGUMENTS'


def test_mcp_reads_and_imports_user_library_definitions_without_copying_callers(tmp_path, monkeypatch):
    import base64
    import application.execution as execution
    from application.fbd import unpack_candidate
    from gxw.library_transfer import verify_library_import
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from tests.test_gxw_object_model import library_transfer_witness
    before, donor, saved = library_transfer_witness()
    context = build_tool_context({'id': 'project', 'plc_model': 'L02', 'target_mode': 'fbd'},
        version={'id': 'v0001', 'target_mode': 'fbd', 'program_name': 'POU_01.Program.pou'}, fbd_baseline=before)
    lock = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock', lambda: lock(tmp_path / 'native.lock'))
    calls = []

    def import_library(_self, raw, source, name):
        assert raw == before and source == donor and name == 'MotionControl_JEC_V100A'
        calls.append(name)
        return verify_library_import(raw, source, name, saved, {})

    monkeypatch.setattr(NativeWorkspaceSourceSave, 'import_library', import_library)
    runtime = build_default_tool_runtime()
    adapter = MCPToolAdapter(runtime, StaticToolContextProvider(context))
    upload = base64.b64encode(donor).decode()
    definitions = adapter.call_tool('read_fbd_libraries', {'data_base64': upload, 'include_source': True}, 'read')
    assert not definitions.is_error and not calls
    assert len(_mcp_body(definitions)['data']['libraries'][0]['definitions']) == 16
    schema = next(tool for tool in adapter.list_tools() if tool.name == 'read_fbd_libraries')
    assert schema.annotations.read_only_hint
    arguments = {'operation': 'import_library', 'library_name': 'MotionControl_JEC_V100A', 'data_base64': upload}
    response = runtime.invoke(ToolCall('import', 'create_fbd_candidate', arguments), context)
    assert not response.is_error and calls == ['MotionControl_JEC_V100A']
    envelope = response.data
    assert envelope['status'] == 'confirmation_required'
    data = envelope['data']
    assert not data['model']['nodes'] and data['model']['cpu'] == 'L02'
    assert data['verification']['gx_compile'] == 'not_run'
    pending = data['pending_action']
    assert pending['base_version_id'] == 'v0001'
    unpack_candidate(pending['_fbd_candidate'], tmp_path / 'reviewed')
    assert (tmp_path / 'reviewed/program.gxw').read_bytes() == saved
    missing = build_tool_context({'id': 'project', 'plc_model': 'L02', 'target_mode': 'fbd'})
    result = runtime.invoke(ToolCall('missing', 'create_fbd_candidate', arguments), missing)
    assert result.is_error and result.data['error']['code'] == 'NO_PROGRAM_SOURCE'
    assert calls == ['MotionControl_JEC_V100A']


def test_mcp_authors_project_fb_source_with_reviewable_definition_and_no_library(tmp_path,monkeypatch):
    import application.execution as execution
    from gxw.fb_authoring import verify_authoring
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from tests.test_gxw_object_model import authored_fb_witness
    before,after,command,report=authored_fb_witness()
    lock=execution.DesktopResourceLock
    monkeypatch.setattr(execution,'DesktopResourceLock',lambda:lock(tmp_path/'native.lock'))
    calls=[]
    def author(_self,raw,arguments):
        assert raw==before and arguments==command
        calls.append(arguments['operation'])
        return verify_authoring(raw,arguments,after,report['native_workspace'])
    monkeypatch.setattr(NativeWorkspaceSourceSave,'author',author)
    runtime=build_default_tool_runtime()
    context=build_tool_context({'id':'project','plc_model':'FX3U','target_mode':'fbd'},
        version={'id':'v0004','target_mode':'fbd','program_name':'FBD_MATRIX.Program.pou'},fbd_baseline=before)
    result=runtime.invoke(ToolCall('author','create_fbd_candidate',command),context)
    assert not result.is_error and calls==['create_st_fb']
    assert result.data['status']=='confirmation_required'
    data=result.data['data']
    assert data['definition']['st_text']==command['st_text']
    assert data['definition']['kind']=='function_block' and data['verification']['gx_compile']=='not_run'
    pending=data['pending_action']
    assert pending['base_version_id']=='v0004'
    assert 'definition' in pending['_fbd_candidate']['artifacts']
    assert pending['_fbd_candidate']['metadata']['validation']['definition_readback']=='matched'
    missing=build_tool_context({'id':'project','plc_model':'FX3U','target_mode':'fbd'})
    rejected=runtime.invoke(ToolCall('no-base','create_fbd_candidate',command),missing)
    assert rejected.is_error and rejected.data['error']['code']=='NO_PROGRAM_SOURCE'
    saved=build_tool_context(context.project,version={'id':'v0005','target_mode':'fbd'},fbd_baseline=after)
    adapter=MCPToolAdapter(runtime,StaticToolContextProvider(saved))
    tool=next(tool for tool in adapter.list_tools() if tool.name=='read_fbd_definition')
    assert tool.annotations.read_only_hint
    read=adapter.call_tool('read_fbd_definition',{'source_name':'WordOffset'},'read')
    assert not read.is_error and _mcp_body(read)['data']['version_id']=='v0005'
    assert _mcp_body(read)['data']['definitions'][0]['st_text']==command['st_text']
    assert calls==['create_st_fb']


def test_mcp_compile_save_preserves_review_and_reports_actual_native_result(tmp_path,monkeypatch):
    import application.execution as execution
    from gxw.native_write import verify_native_compilation_save
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from tests.test_gxw_object_model import motion_compilation_witness
    before,after,report=motion_compilation_witness()
    lock=execution.DesktopResourceLock
    monkeypatch.setattr(execution,'DesktopResourceLock',lambda:lock(tmp_path/'native.lock'))
    calls=[]
    def compile_(_self,raw):
        assert raw==before
        calls.append('compile')
        return verify_native_compilation_save(raw,after,report['native_validation'],report['cold_validation'],report['native_workspace'])
    monkeypatch.setattr(NativeWorkspaceSourceSave,'compile_project',compile_)
    runtime=build_default_tool_runtime()
    context=build_tool_context({'id':'project','plc_model':'FX3U','target_mode':'fbd'},
        version={'id':'v0042','target_mode':'fbd','program_name':'PrgAxis2.Program.pou'},fbd_baseline=before)
    result=runtime.invoke(ToolCall('compile','create_fbd_candidate',{'operation':'compile'}),context)
    assert not result.is_error and result.data['status']=='confirmation_required'
    data=result.data['data']
    assert data['verification']['gx_compile']=='passed' and data['verification']['native_verified'] is False
    assert data['verification']['gui_configuration']=='missing'
    assert data['verification']['saved_code']=='matched' and data['verification']['cold_reopen']=='passed'
    assert 'GUI' in data['message'] and '尚未完整验证' in data['message']
    pending=data['pending_action']
    assert pending['base_version_id']=='v0042'
    assert pending['_fbd_candidate']['metadata']['validation']['saved_code']=='matched'
    mixed=runtime.invoke(ToolCall('mixed','create_fbd_candidate',{'operation':'compile','model':{}}),context)
    assert mixed.is_error and calls==['compile']
    missing=runtime.invoke(ToolCall('missing','create_fbd_candidate',{'operation':'compile'}),build_tool_context(context.project))
    assert missing.is_error and missing.data['error']['code']=='NO_PROGRAM_SOURCE'


@pytest.mark.parametrize('extra', [{'data_base64':'AA=='},{'model':{}},{'program':'1.Program.pou'},
                                 {'native_save':True},{'st_text':'x := TRUE;'}])
def test_mcp_structure_authoring_rejects_mixed_requests_before_native(extra,monkeypatch):
    from gxw.project_templates import blank_fbd_baseline
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    monkeypatch.setattr(NativeWorkspaceSourceSave,'author',lambda *_:pytest.fail('Unexpected native write'))
    context=build_tool_context({'id':'project','plc_model':'FX3U','target_mode':'fbd'},
        version={'id':'v0001','target_mode':'fbd'},fbd_baseline=blank_fbd_baseline('FX3U'))
    arguments=dict(operation='create_structure',source_name='State',members=[dict(name='Value',data_type='WORD')],**extra)
    result=build_default_tool_runtime().invoke(ToolCall('mixed','create_fbd_candidate',arguments),context)
    assert result.is_error


def test_discovery_and_schemas_come_from_the_runtime(saved_project):
    runtime = build_default_tool_runtime()
    adapter = MCPToolAdapter(runtime, _provider(saved_project))
    tools = adapter.list_tools()
    assert tuple(tool.name for tool in tools) == SAFE_TOOL_NAMES
    assert not {tool.name for tool in tools} & FORBIDDEN_TOOL_NAMES
    for tool, source in zip(tools, runtime.list_tools()):
        assert tool.description == source["function"]["description"]
        assert tool.input_schema == source["function"]["parameters"]
        Draft202012Validator.check_schema(tool.input_schema)
        assert tool.model_dump(by_alias=True)["inputSchema"] == tool.input_schema
    tools[0].input_schema["properties"]["injected"] = {"type": "string"}
    assert "injected" not in adapter.list_tools()[0].input_schema["properties"]


def test_mcp_client_discovers_and_calls_the_shared_runtime(saved_project):
    runtime = build_default_tool_runtime()
    calls = []
    original = runtime.invoke

    def invoke(call, context):
        calls.append((call, context))
        return original(call, context)

    runtime.invoke = invoke

    async def exercise():
        async with Client(create_server(_provider(saved_project), runtime)) as client:
            discovered = await client.list_tools()
            assert {tool.name for tool in discovered.tools} == set(SAFE_TOOL_NAMES)
            for name, arguments in [
                ("get_current_project", {}),
                ("get_current_program_info", {}),
                ("read_network", {"network_id": "N0001"}),
                ("get_diagnostics", {}),
                ("validate_project", {}),
                ("validate_current_program", {}),
                ("compile_project", {}),
            ]:
                result = await client.call_tool(name, arguments)
                assert result.is_error is False
                assert _mcp_body(result)["ok"] is True
                assert json.loads(result.content[0].text) == _mcp_body(result)
                assert result.meta["gxworks"]["project_id"] == saved_project[1]
                assert result.meta["gxworks"]["call_id"]
            assert calls[2][0].arguments == {"network_id": "N0001"}
            assert isinstance(calls[2][0], ToolCall)
            assert calls[2][1].program_ir == saved_project[3]

    anyio.run(exercise)


@pytest.mark.parametrize("name,arguments", [
    ("read_network", {}),
    ("read_network", {"network_id": 1}),
    ("get_current_project", {"auto_approve": True}),
    ("search_plc_manual", {"query": "X0", "top_k": True}),
    ("search_plc_manual", {"query": "X0", "top_k": 9}),
    ("patch_program", {"patch": []}),
])
def test_invalid_arguments_survive_the_mcp_boundary(saved_project, name, arguments):
    async def exercise():
        async with Client(create_server(_provider(saved_project))) as client:
            result = await client.call_tool(name, arguments)
            assert result.is_error is True
            assert _mcp_body(result)["error"]["code"] == "INVALID_ARGUMENTS"

    anyio.run(exercise)


@pytest.mark.parametrize("arguments", [[], "{", 3, True])
def test_adapter_delegates_invalid_argument_decoding_to_registry(saved_project, arguments):
    result = MCPToolAdapter(build_default_tool_runtime(), _provider(saved_project)).call_tool(
        "read_network", arguments, "invalid"
    )
    assert result.is_error
    assert _mcp_body(result)["error"]["code"] == "INVALID_ARGUMENTS"


@pytest.mark.parametrize("name", [*sorted(FORBIDDEN_TOOL_NAMES), "arbitrary_shell", "approve_action"])
def test_forbidden_and_unknown_calls_never_invoke_runtime(saved_project, name):
    class UntrustedRuntime:
        def list_tools(self, context=None):
            return [{"function": {"name": name, "description": "unsafe", "parameters": {"type": "object"}}}]

        def invoke(self, call, context):
            pytest.fail("A tool outside SAFE_TOOL_NAMES was invoked")

    adapter = MCPToolAdapter(UntrustedRuntime(), _provider(saved_project))
    assert adapter.list_tools() == []
    assert adapter.call_tool(name, {}, "blocked").structured_content["error"]["code"] == "UNKNOWN_TOOL"

    async def exercise():
        async with Client(create_server(_provider(saved_project))) as client:
            result = await client.call_tool(name, {})
            assert result.is_error
            assert _mcp_body(result)["error"]["code"] == "UNKNOWN_TOOL"

    anyio.run(exercise)


def test_runtime_tool_error_translation(saved_project):
    result = MCPToolAdapter(build_default_tool_runtime(), _provider(saved_project)).call_tool(
        "read_network", {"network_id": "N9999"}, "missing-network"
    )
    assert result.is_error
    assert _mcp_body(result)["error"]["code"] == "TOOL_FAILED"
    assert "N9999" in _mcp_body(result)["error"]["message"]
    assert result.meta["gxworks"]["call_id"] == "missing-network"


def test_public_projection_does_not_leak_or_truncate_structured_data():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        "get_current_project", "test", {"type": "object"},
        lambda *_: {
            "_private": "hidden-top", "long_text": "测" * 19000,
            "nested": [{"visible": 1, "_secret": "hidden-nested"}],
            "tuple": ({"_secret": "hidden-tuple", "visible": 2},),
            "audit": {"revision": 4, "hash": "public-hash"},
        },
    ))
    original = InProcessToolRuntime(registry).invoke(
        ToolCall("long", "get_current_project", {}), build_tool_context({"id": "test"})
    )
    assert len(original.content) > 19000
    assert len(json.loads(original.content)["data"]["long_text"]) == 19000
    assert "hidden-" not in original.content
    result = to_mcp_result(original)
    wire = result.model_dump_json(by_alias=True)
    assert "hidden-" not in wire
    assert "_private" not in wire
    assert json.loads(result.content[0].text) == _mcp_body(result)
    assert len(_mcp_body(result)["data"]["long_text"]) == 19000
    assert 'audit' not in _mcp_body(result)['data']
    assert original.data['data']['audit']['revision'] == 4
    assert original.data["data"]["_private"] == "hidden-top"


@pytest.mark.parametrize('name,nested', [
    ('get_current_program_info', False), ('read_fbd_project', True), ('read_sfc_project', True),
])
def test_shared_tool_envelopes_preserve_engineering_maps_through_mcp(name, nested):
    user_data = {'token': {'_state': [{'secret': 'field', 'password': 5}]}, 'source_id': 'user-value'}
    model = {'schema_version': 1, 'program': 'MAIN', 'nodes': [], 'wires': [], 'metadata': user_data,
             'declaration_edits': {'_state': {'renames': {'token': 'password'}}}}
    payload = {'model': model} if nested else model
    payload = {**payload, '_internal': 'never-return', 'token': 'credential'}
    registry = ToolRegistry()
    registry.register(ToolDefinition(name, 'contract', {'type': 'object'}, lambda *_: copy.deepcopy(payload)))
    result = InProcessToolRuntime(registry).invoke(
        ToolCall('engineering', name, {}), build_tool_context({'id': 'test'}))
    wire = to_mcp_result(result)
    expected = {'model': model} if nested else model
    assert json.loads(result.content)['data'] == expected
    assert _mcp_body(wire)['data'] == expected
    assert json.loads(wire.content[0].text) == _mcp_body(wire)


def test_catalog_tool_preserves_schema_and_named_parameters_after_technical_projection():
    names = ('token', 'password', '_state', 'source_id')
    arguments = dict.fromkeys(names, 'D0')
    edits = {key: {'renames': {'secret': '_state'}} for key in names}
    parameter_model = {'declaration_edits': edits, 'networks': [{'calls': [{'arguments': arguments}],
        'nodes': [{'arguments': arguments, 'port_edits': {key: {'negated': True} for key in names}}]}]}
    payload = {'parameter_schema': {'properties': {key: {'type': 'string'} for key in names}},
        'parameter_model': parameter_model, 'empty_parameter_model': parameter_model,
        'project_configuration': {'system_variable_ranges': {key: [1, 2] for key in names}},
        'token': 'never-return'}
    registry = ToolRegistry()
    registry.register(ToolDefinition('get_fbd_catalog', 'contract', {'type': 'object'},
        lambda *_: copy.deepcopy(payload)))
    result = InProcessToolRuntime(registry).invoke(
        ToolCall('catalog-data', 'get_fbd_catalog', {}), build_tool_context({'id': 'test', 'plc_model': 'FX3U'}))
    expected = {key: value for key, value in payload.items() if key != 'token'}
    from application.read_delivery import assemble_read_pages
    assert assemble_read_pages([json.loads(result.content)]) == expected
    assert assemble_read_pages([_mcp_body(to_mcp_result(result))]) == expected


def test_generation_context_keeps_named_parameters_and_schema_without_proof_metadata():
    from integrations.web.responses import AgentToolResult

    expression = {'op': 'device', 'name': 'D0', 'type': {'kind': 'int', 'bits': 16, 'signed': True}}
    spec = {'operation_intents': [{'id': 'move',
        'effects': [{'target': {'device': 'D1', 'kind': 'word'}, 'value': expression}],
        'enable': {'op': 'constant', 'type': {'kind': 'bool'}, 'value': True},
        'parameters': {key: expression for key in ('token', '_state', 'source_id')},
        'provenance': {'source': 'user_authored', 'evidence': ['move']}}]}
    schema = {'properties': {'token': {'type': 'number'}, '_state': {'type': 'boolean'}}}
    declarations = {'_caller': [{'name': 'token', 'data_type': 'WORD',
        'member_devices': {'_state': {'device': 'D0', 'iec_address': '%MW0', '_native_cache': 99}},
        'source_id': 'proof-only'}]}
    parameters = {'networks': [{'calls': [{'id': 'work', 'type': 'Work', 'instance': 'work1',
        'arguments': {'token': 'D0', '_state': 'M0'}}]}]}
    payload = {'target_mode': 'ladder', 'confirmed_spec': spec,
        'output_contract': {'tool': 'create_program_candidate', 'schema': schema,
            'alternatives': [{'tool': 'create_fbd_candidate', 'model_schema': schema}]},
        'generation_input': {'call_interface': declarations, 'structures': declarations,
            'existing_declarations': declarations, 'declaration_tables': {'_caller': 'local'},
            'source_context': {'declarations': declarations, 'structures': declarations,
                'fbd_callers': [{'source_name': '_caller', 'parameter_model': parameters}]}},
        'generation_handoff': {'source_id': 'proof-only'}, '_internal': 'never-return'}
    registry = ToolRegistry()
    registry.register(ToolDefinition('get_generation_context', 'contract', {'type': 'object'},
        lambda *_: copy.deepcopy(payload)))
    result = InProcessToolRuntime(registry).invoke(
        ToolCall('generation-data', 'get_generation_context', {}), build_tool_context({'id': 'test'}))
    from application.read_delivery import assemble_read_pages
    public = {'ok': True, 'tool': 'get_generation_context', 'data': assemble_read_pages([json.loads(result.content)])}
    intent = public['data']['confirmed_spec']['operation_intents'][0]
    assert intent['parameters'] == spec['operation_intents'][0]['parameters']
    assert 'provenance' not in intent and 'generation_handoff' not in public['data']
    assert public['data']['output_contract']['schema'] == schema
    assert public['data']['output_contract']['alternatives'][0]['model_schema'] == schema
    engineering = public['data']['generation_input']
    assert engineering['declaration_tables'] == {'_caller': 'local'}
    for owner in (engineering['call_interface'], engineering['structures'], engineering['existing_declarations'],
                  engineering['source_context']['declarations'], engineering['source_context']['structures']):
        assert owner['_caller'] == [{'name': 'token', 'data_type': 'WORD',
                                     'member_devices': {'_state': {'device': 'D0', 'iec_address': '%MW0'}}}]
    assert engineering['source_context']['fbd_callers'][0]['parameter_model'] == parameters
    response = {'name': result.name, 'call_id': result.call_id, 'is_error': result.is_error,
        'content': result.content, 'data': public}
    assert AgentToolResult.model_validate(response).model_dump(mode='json', by_alias=True, exclude_unset=True) == response
    assert assemble_read_pages([_mcp_body(to_mcp_result(result))]) == public['data']


def test_text_only_tool_errors_are_preserved():
    result = to_mcp_result(ToolResult("text", "get_current_project", "backend unavailable", is_error=True))
    assert result.is_error
    assert result.structured_content is None
    assert result.content[0].text == "backend unavailable"


def test_confirmations_stay_pending_and_never_write_the_workspace(saved_project):
    store, project_id, version, program = saved_project
    before = _files(store.base_dir)
    rung = copy.deepcopy(program["networks"][0]["ladder"])
    rung["branches"][0]["inputs"].append({"type": "NC", "address": "X1", "label": ""})
    patch = {"operations": [{"operation": "modify_network", "network": "N0001", "ladder": rung}]}

    async def exercise():
        async with Client(create_server(_provider(saved_project))) as client:
            for name, arguments in [
                ("patch_program", {"patch": patch}),
                ("import_current_program_to_gxworks2", {}),
            ]:
                result = await client.call_tool(name, arguments)
                public = _mcp_body(result)
                assert not result.is_error
                assert public["status"] == "confirmation_required"
                assert public["data"]["requires_confirmation"] is True
                pending = public["data"]["pending_action"]
                assert pending["project_id"] == project_id
                assert "_candidate_ir" not in result.model_dump_json()
                assert "_confirmed_spec" not in result.model_dump_json()
                if name == "patch_program":
                    assert pending["base_version_id"] == version["id"]
                    assert pending["candidate_ir_sha256"]
                    assert pending["artifact_hashes"]
                    assert pending["diff"]["modified"] == ["N0001"]
                else:
                    assert pending["version_id"] == version["id"]

    anyio.run(exercise)
    assert _files(store.base_dir) == before


def test_provider_loads_copies_and_follows_active_or_pinned_versions(saved_project):
    store, project_id, version, _ = saved_project
    provider = _provider(saved_project)
    pinned = _provider(saved_project, pin=True)
    context = provider.get_context()
    context.project["name"] = "mutated copy"
    assert provider.get_context().project["name"] == "MCP 测试项目"
    static = StaticToolContextProvider(context)
    context.project["name"] = "changed again"
    assert static.get_context().project["name"] == "mutated copy"
    snapshot = static.get_context()
    snapshot.project["name"] = "changed returned value"
    assert static.get_context().project["name"] == "mutated copy"
    second, _ = store.prepare_version(project_id)
    store.complete_version(project_id, second, {"target_mode": "st", "artifacts": {}})
    assert provider.get_context().version_id == second
    assert pinned.get_context().version_id == version["id"]


def test_session_context_binds_st_source_and_gxw_to_selected_version(tmp_path):
    store = SessionStore(base_dir=tmp_path)
    project = store.create_project(name='isolated', plc_model='L02', target_mode='st')
    version_id, _ = store.prepare_version(project['id'])
    folder = store.version_dir(project['id'], version_id)
    (folder / 'program.st').write_text('IF M0 THEN D100 := 5; END_IF;', encoding='utf-8')
    (folder / 'program.gxw').write_bytes(b'snapshot fixture, not a native acceptance receipt')
    store.complete_version(project['id'], version_id, {'target_mode':'st','plc_model':'L02',
        'artifacts':{'st':'program.st','gxw':'program.gxw'}})
    provider = SessionToolContextProvider(tmp_path, project['id'], version_id)
    before = _files(tmp_path)
    context = provider.get_context()
    assert context.version_id == version_id and context.plc_model == 'L02'
    assert context.st_text == 'IF M0 THEN D100 := 5; END_IF;'
    assert context.native_snapshot == b'snapshot fixture, not a native acceptance receipt'
    assert _files(tmp_path) == before


def test_legacy_context_and_direct_reads_require_explicit_migration(saved_project):
    store, project_id, version, _ = saved_project
    ir_file = store.version_dir(project_id, version["id"]) / version["artifacts"]["ir"]
    ir_file.unlink()
    project = store.get_project(project_id)
    project["versions"][0]["artifacts"].pop("ir")
    store.save_project(project)
    before = _files(store.base_dir)
    context = _provider(saved_project).get_context()
    assert context.program_ir["revision"] == 1
    assert context.ladder["rungs"][0]["rung_id"] == 1
    assert _files(store.base_dir) == before
    store.load_program_ir(project_id, version["id"])
    assert _files(store.base_dir) == before
    store.migrate_program_ir(project_id, version["id"])
    assert ir_file.exists()
    assert store.get_version(project_id, version["id"])["artifacts"]["ir"]


def test_missing_context_and_path_escape_fail_closed(saved_project, tmp_path):
    store, project_id, version, _ = saved_project
    with pytest.raises(ContextUnavailableError):
        SessionToolContextProvider(tmp_path / "does-not-exist", project_id)
    assert not (tmp_path / "does-not-exist").exists()
    for invalid in ("../elsewhere", "C:\\elsewhere", "..", "a/b"):
        with pytest.raises(ContextUnavailableError):
            SessionToolContextProvider(store.base_dir, invalid)
    with pytest.raises(ContextUnavailableError):
        SessionToolContextProvider(store.base_dir, project_id, "v9999").get_context()
    project = store.get_project(project_id)
    project["versions"][0]["artifacts"]["ir"] = "../../outside.json"
    store.save_project(project)
    with pytest.raises(ContextUnavailableError, match="escapes"):
        _provider(saved_project).get_context()
    result = MCPToolAdapter(build_default_tool_runtime(), _provider(saved_project)).call_tool(
        "get_current_project", {}, "bad-context"
    )
    assert result.is_error
    assert _mcp_body(result)["error"]["code"] == "CONTEXT_UNAVAILABLE"
    assert str(store.base_dir) not in result.content[0].text


def test_project_without_version_has_a_valid_context(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace")
    project = store.create_project("Empty project")
    provider = SessionToolContextProvider(store.base_dir, project["id"])
    context = provider.get_context()
    assert context.version is None and context.program_ir is None
    result = MCPToolAdapter(build_default_tool_runtime(), provider).call_tool("get_current_project", {}, "empty")
    assert not result.is_error


def test_generation_tool_schemas_describe_only_model_owned_input(empty_project):
    from plc.generation_contract import ladder_v1_schema

    store, project_id = empty_project
    adapter = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(store.base_dir, project_id))
    schemas = {tool.name: tool.input_schema for tool in adapter.list_tools()}
    context_schema = schemas["get_generation_context"]
    assert set(context_schema["properties"]) == {"user_requirement", "workflow", "execution_mode", "cursor", "catalog_scope", "templates"}
    assert context_schema["properties"]["workflow"]["enum"] == ["direct_generation", "detailed"]
    assert context_schema["properties"]["execution_mode"]["enum"] == ["background", "live"]
    assert not context_schema.get("required")
    assert context_schema["additionalProperties"] is False
    schema = schemas["create_program_candidate"]
    assert schema["required"] == ["ladder"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"program_name", "ladder", "generation_context_id"}
    assert schema["properties"]["generation_context_id"]["type"] == "string"
    assert "generation_context_id" not in schema["required"]
    assert schema["properties"]["program_name"]["default"] == "MAIN"
    assert schema["properties"]["ladder"]["type"] == "object"
    assert "oneOf" not in schema["properties"]["ladder"]
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate({"ladder": _generation_ladder()})
    for bad in [
        {"ladder": _generation_ladder(), "revision": 8},
        {"ladder": {"device_comments": {}, "rungs": [], "networks": []}},
        {"ladder": {"device_comments": {}, "rungs": "text"}},
    ]:
        # Transport owns top-level tool arguments; the shared API parser owns
        # ladder compatibility and structural errors after normalization.
        rejected = adapter.call_tool("create_program_candidate", bad, "invalid-generation")
        assert rejected.is_error


@pytest.mark.parametrize("output", [
    {"type": "COIL", "address": "Y0"},
    {"type": "PLS", "address": "M0"},
    {"type": "PLF", "address": "M1"},
    {"type": "TIMER", "address": "T0", "value": "K10"},
    {"type": "COUNTER", "address": "C0", "value": "K3"},
    {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K1", "D10"]},
])
def test_generation_schema_and_core_accept_the_documented_element_types(output):
    from plc.generation_contract import ladder_v1_schema

    ladder = _generation_ladder()
    rung = ladder["rungs"][0]
    rung["header_element"] = {"type": "BLOCK_INPUT", "expression": "= D0 K1", "label": None}
    rung["shared_inputs"] = [{"type": "NO", "address": "X3"}]
    rung["branches"][0]["inputs"].extend([
        {"type": "P", "address": "X4"}, {"type": "F", "address": "X5"},
        {"type": "COMPARE", "expression": "> D2 K10"},
    ])
    rung["branches"][0]["outputs"] = [output]
    Draft202012Validator(ladder_v1_schema()).validate(ladder)
    result = PLCCore().create_program_candidate(ladder, plc_model="FX3U")
    assert result["diagnostics"]["valid"] is True


@pytest.mark.parametrize("invalid", [
    {"type": "APP_INSTR", "opcode": "please run the motor", "operands": []},
    {"type": "BLOCK_OUTPUT", "expression": "UNVERIFIED Y0"},
    {"type": "COIL", "address": "Y0", "label": "L" * 65},
])
def test_generation_schema_and_core_reject_output_escape_hatches(invalid):
    from plc.generation_contract import ladder_v1_schema

    ladder = _generation_ladder()
    ladder["rungs"][0]["branches"][0]["outputs"] = [invalid]
    assert not Draft202012Validator(ladder_v1_schema()).is_valid(ladder)
    with pytest.raises(ValueError):
        PLCCore().create_program_candidate(ladder, plc_model="FX3U")


def test_versionless_generation_context_and_candidate_over_mcp(empty_project):
    store, project_id = empty_project
    before = _files(store.base_dir)
    spec = store.get_project(project_id)["confirmed_spec"]
    provider = SessionToolContextProvider(store.base_dir, project_id)

    async def exercise():
        async with Client(create_server(provider)) as client:
            context_result = await client.call_tool("get_generation_context", {})
            assert not context_result.is_error
            context = await _complete_client_read(client, "get_generation_context", {}, initial=context_result)
            assert context["project_id"] == project_id
            assert context["plc_model"] == "FX3U"
            assert context["target_mode"] == "ladder"
            assert context["workflow_mode"] == "generate"
            assert context["has_confirmed_spec"] is True
            assert context["generation_input"]["confirmed_spec"]["io_table"] == spec["io_table"]
            assert context["generation_input"]["confirmed_spec"]["selected_approach"]["generation_contract"] == spec["selected_approach"]["generation_contract"]
            assert context["output_contract"]["format"] == "ladder_v1"
            Draft202012Validator(context["generation_input"]["output_schema"]).validate(_generation_ladder())
            result = await client.call_tool("create_program_candidate", {"ladder": _generation_ladder()})
            assert not result.is_error
            assert _mcp_body(result)["status"] == "confirmation_required"
            assert _mcp_body(result)["data"]["requires_confirmation"] is True
            assert 'verification' not in _mcp_body(result)['data']
            pending = _mcp_body(result)["data"]["pending_action"]
            assert pending["type"] == "accept_generated_program"
            assert pending["project_id"] == project_id
            assert pending["program_name"] == "MAIN"
            assert pending["revision"] == 1
            assert pending["candidate_id"].startswith("candidate_")
            assert len(pending["candidate_ir_sha256"]) == 64
            assert pending["ladder_sha256"] == canonical_sha256(_generation_ladder())
            assert pending["confirmed_spec_hash"] == canonical_sha256(spec)
            assert 'diagnostics' not in pending
            assert len(pending["artifact_hashes"]) == 6
            assert pending["summary"]["network_count"] == 1
            assert pending["summary"]["device_count"] == 3
            assert "_candidate_ir" not in result.model_dump_json()
            assert "_confirmed_spec" not in result.model_dump_json()
            assert json.loads(result.content[0].text) == _mcp_body(result)
            assert result.meta["gxworks"]["version_id"] == ""

    anyio.run(exercise)
    assert _files(store.base_dir) == before
    project = store.get_project(project_id)
    assert project["versions"] == []
    assert project["active_version_id"] is None


def test_generation_context_has_no_private_state_or_credentials():
    secret = "never-expose-this-private-state"
    spec = {
        "summary": "confirmed motor requirement", "user_notes": "stop has priority",
        "workspace": secret, "api_key": secret, "provider_configuration": {"url": secret},
        "selected_approach": {"name": "Seal-in", "path": secret, "generation_contract": {
            "required_structures": ["self_hold"], "_secret": secret, "credentials": secret,
        }},
        "io_table": [{"address": "Y0", "label": "Motor", "path": secret}],
        "parameters": [{"name": "delay", "value": "K10", "widget": object(), "api_key": secret}],
        "hardware_profile": {"cpu_full_model": "FX3U-32MT/ES-A", "modules": ["FX3U-4AD-ADP"], "path": secret},
        "hardware_context": {"analog_module": {"input_module": "FX3U-4AD-ADP", "input_channel": 1, "provider": secret}},
        "logic_contracts": {"mutex": [["Y0", "Y1"]], "terminal_states": {"D0": [0, 9], "workspace": secret}},
        "execution_semantics": [{"semantic": "LEVEL", "devices": ["X0"], "_state": secret}],
        "approaches": [{"name": secret}], "pending_review": {"draft": secret},
    }
    context = build_tool_context({
        "id": "projection", "plc_model": "FX3U", "confirmed_spec": spec,
        "workspace": secret, "provider": secret, "api_key": secret,
        "messages": [{"content": secret}], "widget": object(),
    })
    adapter = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context))
    result = adapter.call_tool(
        "get_generation_context", {}, "projection",
    )
    assert not result.is_error
    wire = result.model_dump_json()
    assert secret not in wire
    for private_name in ("api_key", "workspace", "provider_configuration", "widget", "pending_review"):
        assert private_name not in wire
    public = _finish_mcp_read(adapter, result)[0]["generation_input"]["confirmed_spec"]
    assert public["summary"] == spec["summary"]
    assert public["selected_approach"]["generation_contract"] == {"required_structures": ["self_hold"]}
    assert public["hardware_profile"]["modules"] == ["FX3U-4AD-ADP"]
    assert public["hardware_context"]["analog_module"] == {"input_module": "FX3U-4AD-ADP", "input_channel": 1}
    assert public["parameters"] == [{"name": "delay", "value": "K10"}]
    assert public["logic_contracts"] == {"mutex": [["Y0", "Y1"]], "terminal_states": {"D0": [0, 9]}}
    assert context.project["confirmed_spec"]["api_key"] == secret


def test_generation_uses_the_context_model_and_version_spec_snapshot():
    spec = {"summary": "Selected version spec", "selected_approach": {
        "name": "no motor", "generation_contract": {"forbidden_devices": ["Y0"]},
    }}
    context = build_tool_context(
        {"id": "authority", "plc_model": "FX3U", "confirmed_spec": {"summary": "current project spec"}},
        version={"id": "v0001", "plc_model": "FX5U", "confirmed_spec_snapshot": spec},
    )
    runtime = build_default_tool_runtime()
    projected = _complete_runtime_read(runtime, ToolCall("context", "get_generation_context", {}), context)
    assert projected["plc_model"] == "FX5U"
    assert projected["generation_input"]["confirmed_spec"]["summary"] == spec["summary"]
    failed = runtime.invoke(ToolCall("candidate", "create_program_candidate", {"ladder": _generation_ladder()}), context)
    assert not failed.is_error
    pending = failed.data["data"]["pending_action"]
    assert pending["_candidate_ir"]["plc"]["cpu"] == "FX5U"
    assert pending["_confirmed_spec"] == spec
    assert pending["_validation_profile"] == "generation_structural"
    no_spec = build_tool_context({"id": "empty", "plc_model": "FX5U"})
    projected = _complete_runtime_read(runtime, ToolCall("context", "get_generation_context", {}), no_spec)
    assert projected["has_confirmed_spec"] is False
    assert projected["generation_input"]["confirmed_spec"] is None
    generated = runtime.invoke(ToolCall("candidate", "create_program_candidate", {"ladder": _generation_ladder()}), no_spec)
    assert not generated.is_error
    assert generated.data["data"]["pending_action"]["_candidate_ir"]["plc"]["cpu"] == "FX5U"


@pytest.mark.parametrize("field", [
    "project_id", "plc_model", "revision", "confirmed_spec", "candidate_id",
    "candidate_ir_sha256", "ladder_sha256", "auto_approve",
])
def test_generation_rejects_client_overrides_of_server_fields(empty_project, field):
    store, project_id = empty_project
    result = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(store.base_dir, project_id)).call_tool(
        "create_program_candidate", {"ladder": _generation_ladder(), field: "override"}, "override",
    )
    assert result.is_error
    assert _mcp_body(result)["error"]["code"] == "INVALID_ARGUMENTS"


def test_invalid_generated_ladder_is_an_mcp_tool_error_and_can_be_corrected(empty_project):
    store, project_id = empty_project
    adapter = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(store.base_dir, project_id))
    before = _files(store.base_dir)
    invalid = _generation_ladder()
    invalid["rungs"][0]["branches"][0]["outputs"] = [{"type": "APP_INSTR", "opcode": "UNVERIFIED", "operands": []}]
    result = adapter.call_tool("create_program_candidate", {"ladder": invalid}, "invalid")
    assert result.is_error
    assert _mcp_body(result)["error"]["code"] == "TOOL_FAILED"
    assert "unsupported APP_INSTR" in _mcp_body(result)["error"]["message"]
    assert "status" not in _mcp_body(result)
    corrected = adapter.call_tool("create_program_candidate", {"ladder": _generation_ladder()}, "corrected")
    assert not corrected.is_error
    assert _mcp_body(corrected)["status"] == "confirmation_required"
    assert _files(store.base_dir) == before


def test_context_rejects_concurrent_metadata_changes(saved_project, monkeypatch):
    provider = _provider(saved_project)
    original = provider._store.get_project
    calls = []

    def changing_project(project_id):
        project = original(project_id)
        calls.append(project)
        if len(calls) > 1:
            project["name"] = "changed during read"
        return project

    monkeypatch.setattr(provider._store, "get_project", changing_project)
    with pytest.raises(ContextUnavailableError, match="changed"):
        provider.get_context()


def test_unexpected_runtime_exception_is_a_sanitized_tool_error(saved_project, monkeypatch):
    runtime = build_default_tool_runtime()

    def fail(*_):
        raise RuntimeError("private failure details")

    monkeypatch.setattr(runtime, "invoke", fail)
    result = MCPToolAdapter(runtime, _provider(saved_project)).call_tool("get_current_project", {}, "error")
    assert result.is_error
    assert _mcp_body(result)["error"]["code"] == "TOOL_FAILED"
    assert "private failure details" not in result.content[0].text


@pytest.mark.parametrize("without_version", [False, True])
def test_stdio_real_process_without_qt_and_with_noisy_runtime(saved_project, tmp_path, without_version):
    wrapper = tmp_path / "headless_server.py"
    wrapper.write_text('''import importlib.abc
import os
import sys

class BlockDesktop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"PyQt6", "PyQt5", "qt_compat", "main", "api", "model_provider", "openai", "pywinauto", "win32com", "config", "config_manager", "credential_store"}:
            raise AssertionError("Unexpected desktop/provider dependency: " + fullname)

sys.meta_path.insert(0, BlockDesktop())
from integrations.mcp import server
from integrations.mcp.__main__ import main
original = server.build_default_tool_runtime

def noisy_runtime():
    runtime = original()
    invoke = runtime.invoke
    def noisy(call, context):
        print("python stdout diagnostic", flush=True)
        os.write(1, b"native stdout diagnostic\\n")
        return invoke(call, context)
    runtime.invoke = noisy
    return runtime

server.build_default_tool_runtime = noisy_runtime
raise SystemExit(main())
''', encoding="utf-8")
    store, project_id, _, _ = saved_project
    if without_version:
        project_id = store.create_project("headless initial generation")["id"]
    before = _files(store.base_dir)
    env = {key: value for key, value in os.environ.items() if not any(
        marker in key.upper() for marker in ("API_KEY", "DEEPSEEK", "OPENAI", "CREDENTIAL")
    )}
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(wrapper), "--stdio", "--workspace", str(store.base_dir), "--project", project_id],
        env={**env, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"},
        cwd=tmp_path,
    )

    async def exercise():
        with anyio.fail_after(25):
            with (tmp_path / "stderr.txt").open("w", encoding="utf-8") as errors:
                async with stdio_client(parameters, errlog=errors) as streams:
                    async with ClientSession(*streams, read_timeout_seconds=10) as client:
                        initialized = await client.initialize()
                        assert initialized.server_info.name == "gxworks-agent"
                        assert str(initialized.server_info.website_url) == "https://github.com/Arienax/gxworks-agent"
                        assert "https://github.com/Arienax/gxworks-agent" in initialized.instructions
                        assert "confirmation_required" in initialized.instructions
                        assert "do not start an automatic repeated-submission loop" in initialized.instructions
                        assert "locate_native_code_steps" in initialized.instructions
                        assert initialized.capabilities.tools is not None
                        assert {tool.name for tool in (await client.list_tools()).tools} == set(SAFE_TOOL_NAMES)
                        calls = [] if without_version else [
                            ("read_network", {"network_id": "N0001"}),
                            ("compile_project", {}),
                            ("import_current_program_to_gxworks2", {}),
                        ]
                        calls.extend([
                            ("get_generation_context", {}),
                            ("create_program_candidate", {"ladder": _generation_ladder()}),
                        ])
                        for name, arguments in calls:
                            result = await client.call_tool(name, arguments)
                            assert not result.is_error
                            assert _mcp_body(result)["ok"]
                            if name == "create_program_candidate":
                                assert _mcp_body(result)["status"] == "confirmation_required"
                                assert "_candidate_ir" not in result.model_dump_json()
                                assert "_confirmed_spec" not in result.model_dump_json()

    anyio.run(exercise)
    diagnostics = (tmp_path / "stderr.txt").read_text(encoding="utf-8")
    assert "python stdout diagnostic" in diagnostics
    assert "native stdout diagnostic" in diagnostics
    assert _files(store.base_dir) == before
    if without_version:
        assert store.get_project(project_id)["versions"] == []
        assert store.get_project(project_id)["active_version_id"] is None


def test_cli_help_and_setup_errors_use_only_stderr(tmp_path):
    from mcp_test_support import isolated_mcp_command, isolated_mcp_environment

    env = isolated_mcp_environment(PYTHONPATH=str(ROOT / "src"), PLC_AI_WORKSPACE_DIR="")
    for args, expected_code in [
        (["--help"], 0),
        (["--stdio", "--project", "absent"], 2),
        (["--stdio", "--workspace", str(tmp_path / "missing"), "--project", "absent"], 2),
    ]:
        result = subprocess.run(
            isolated_mcp_command(*args),
            env=env, cwd=tmp_path, capture_output=True, timeout=15,
        )
        assert result.returncode == expected_code
        assert result.stdout == b""
        assert result.stderr


def test_scriptable_stdio_smoke():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "mcp_smoke.py")],
        cwd=ROOT, capture_output=True, encoding="utf-8", timeout=35,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["ok"] is True
    assert summary["tool_count"] == len(SAFE_TOOL_NAMES)
    assert summary["called_tool"] == "read_network"
    assert summary["generation"] == {
        "called_tools": ["get_generation_context", "create_program_candidate"],
        "status": "confirmation_required", "revision": 1,
        "version_count": 0, "active_version_id": None, "workspace_unchanged": True,
    }


class _InspectionNative:
    def __init__(self, tmp_path):
        import csv
        from gxworks2.models import GXWorks2Session
        from gxworks2.csv_manager import GXWORKS2_HEADER, GXWORKS2_COMMENT_HEADER
        self.current = [GXWorks2Session(11, 21, "Warehouse - GX Works2", "GD2.exe", True,
            "Warehouse", True, "process-start-1", "1.635.0.1"),
            GXWorks2Session(12, 22, "Other - GX Works2", "GD2.exe", True,
            "Other", True, "process-start-2", "1.635.0.1")]
        self.program = tmp_path / "original.csv"
        self.comments = tmp_path / "original-comments.csv"
        rows = [["MAIN"], ["PLC信息:", "FXCPU FX3U/FX3UC"], GXWORKS2_HEADER,
                ["0", "Start", "LD", "X000", "", "", ""],
                ["1", "", "ANI", "X001", "", "", ""],
                ["2", "", "OUT", "Y000", "", "", ""],
                ["3", "", "END", "", "", "", ""]]
        for path, values in ((self.program, rows),
                             (self.comments, [["COMMENT"], GXWORKS2_COMMENT_HEADER, ["X000", "Enter"]])):
            with path.open("w", encoding="utf-16", newline="") as handle:
                csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows(values)
        self.calls = []
        self.cpu = "FX3U/FX3UC"

    def sessions(self):
        return self.current

    def identity(self, session, program="MAIN"):
        return {"cpu_menu": self.cpu, "program_name": program,
                "project_name": session.project_name, "program_source_confirmed": True,
                "identity_basis": "native_program_tree_and_status_bar"}

    def snapshot(self, target, identity, program):
        self.calls.append(target.process_id)
        return {"program_csv": self.program.read_bytes(), "comment_csv": self.comments.read_bytes()}


def _inspection_session(tmp_path):
    from application.engineering_session import EngineeringSession
    from application.program_inspection import ProgramInspectionService
    native = _InspectionNative(tmp_path)
    session = EngineeringSession(tmp_path / "workspace", state_dir=tmp_path / "state")
    session.inspection = ProgramInspectionService(native=native)
    return session, native


def test_generic_simulator_views_are_shared_mcp_tools_without_a_selected_project(tmp_path, monkeypatch):
    from application import simulator_view
    from integrations.mcp.local_adapter import LocalMCPToolAdapter
    static = tmp_path / "static"
    static.mkdir()
    (static / "simulator.html").write_text("<html>shared Web component</html>")
    views = simulator_view.SimulationViewService(static_dir=static)
    monkeypatch.setattr(simulator_view, "_views", views)
    session, _native = _inspection_session(tmp_path)
    adapter = LocalMCPToolAdapter(session)
    try:
        result = adapter.call_tool("prepare_simulator_scenario", {"config": {
            "title": "Unrelated BOOL case", "signals": [
                {"id": "button", "address": "X0", "data_type": "BOOL", "role": "input"},
                {"id": "lamp", "address": "Y0", "data_type": "BOOL", "role": "output"}],
            "initial": {"button": False}, "steps": [{"at_ms": 100, "set": {"button": True}},
                {"at_ms": 200, "expect": {"lamp": True}}], "duration_ms": 500}}, "view")
        assert not result.is_error, _mcp_body(result)
        data = _mcp_body(result)["data"]
        assert data["execution_started"] is False and data["status"] == "prepared"
        assert data["suite"]["tests"][0]["steps"][0]["set"] == {"X0": 1}
        assert not session.workspace.exists() and session.project_id is None
        read = adapter.call_tool("get_simulator_trace", {"view_ref": data["view_ref"]}, "read")
        assert _mcp_body(read)["data"]["sample_count"] == 0
        assert all(tool.annotations.read_only_hint for tool in adapter.list_tools() if tool.name in {
            "prepare_simulator_scenario", "start_simulator_trace", "get_simulator_trace", "stop_simulator_trace"})
    finally:
        views.close()


def test_unselected_mcp_discovers_and_analyzes_explicit_native_source_without_saving(tmp_path):
    import jsonschema
    from integrations.mcp.local_adapter import LocalMCPToolAdapter
    session, native = _inspection_session(tmp_path)
    original = native.program.read_bytes(), native.comments.read_bytes()
    adapter = LocalMCPToolAdapter(session)
    listed = adapter.call_tool("list_gxworks2_sessions", {}, "inventory")
    assert not listed.is_error
    assert _mcp_body(listed)["data"]["selection_status"] == "ambiguous"
    schemas = {tool.name: tool.output_schema for tool in adapter.list_tools()}
    jsonschema.validate(_mcp_body(listed), schemas["list_gxworks2_sessions"])
    ref = _mcp_body(listed)["data"]["sessions"][0]["session_id"]
    read = adapter.call_tool("read_gxworks2_snapshot", {"session_id": ref}, "snapshot")
    assert not read.is_error, _mcp_body(read)
    data = _mcp_body(read)["data"]
    jsonschema.validate(_mcp_body(read), schemas["read_gxworks2_snapshot"])
    assert data["cpu_menu"] == "FX3U/FX3UC" and data["editable"] is False
    assert data["saved_version_id"] is None and data["coverage"]["parse_status"] == "complete"
    source = data["source_ref"]
    info = adapter.call_tool("get_current_program_info", {"source_ref": source}, "info")
    assert not info.is_error and _mcp_body(info)["data"]["network_count"] == 1
    network = adapter.call_tool("read_network", {"source_ref": source, "network_id": "N0001"}, "network")
    assert not network.is_error, _mcp_body(network)
    assert _mcp_body(network)["data"]["raw_instructions"][0]["args"] == ["X000"]
    for tool in ("get_diagnostics", "validate_current_program", "validate_project"):
        result = adapter.call_tool(tool, {"source_ref": source}, tool)
        assert not result.is_error, _mcp_body(result)
        assert _mcp_body(result)["data"]["runtime_verified"] is False
    forbidden = adapter.call_tool("patch_program", {"source_ref": source, "patch": {"operations": []}}, "patch")
    assert forbidden.is_error
    assert native.calls == [11] and session.project_id is None
    assert (native.program.read_bytes(), native.comments.read_bytes()) == original
    assert not session.workspace.exists() and not session.service.state_dir.exists()


@pytest.mark.parametrize("change", ["expiry", "project", "process", "cpu", "refresh", "foreign"])
def test_analysis_source_rejects_stale_and_foreign_references(tmp_path, change):
    from dataclasses import replace
    from application.program_inspection import ProgramInspectionService
    session, native = _inspection_session(tmp_path)
    now = [0.0]
    session.inspection._clock = lambda: now[0]
    selected = session.invoke(ToolCall("list", "list_gxworks2_sessions", {})).data["data"]["sessions"][0]["session_id"]
    source = session.invoke(ToolCall("read", "read_gxworks2_snapshot", {"session_id": selected})).data["data"]["source_ref"]
    if change == "expiry":
        now[0] = 901
    elif change == "project":
        native.current[0] = replace(native.current[0], project_name="Switched")
    elif change == "process":
        native.current[0] = replace(native.current[0], process_started="new-process")
    elif change == "cpu":
        native.cpu = "Q03UDV"
    elif change == "refresh":
        session.invoke(ToolCall("reread", "read_gxworks2_snapshot", {"session_id": selected}))
    else:
        session.inspection = ProgramInspectionService(native=native)
    result = session.invoke(ToolCall("check", "read_network", {"source_ref": source, "network_id": "N0001"}))
    assert result.is_error and result.data["error"]["code"] == "STALE_SOURCE_REF"
    assert result.data["error"]["next_actions"][0]["tool"] == "list_gxworks2_sessions"


def test_missing_model_and_program_have_separate_recovery_actions(tmp_path):
    session, _ = _inspection_session(tmp_path)
    manual = session.invoke(ToolCall("manual", "search_plc_manual", {"query": "M8000"}))
    assert manual.is_error and manual.data["error"]["code"] == "PLC_MODEL_REQUIRED"
    no_project = session.invoke(ToolCall("no-project", "read_network", {"network_id": "N0001"}))
    assert no_project.data["error"]["code"] == "NO_PROJECT_SELECTED"
    session.invoke(ToolCall("create", "create_project", {"name": "Empty", "plc_model": "FX3U", "target_mode": "ladder"}))
    info = session.invoke(ToolCall("info", "get_current_program_info", {}))
    assert not info.is_error and info.data["data"]["available"] is False
    checks = session.invoke(ToolCall("validate", "get_diagnostics", {}))
    assert checks.is_error and checks.data["error"]["code"] == "NO_PROGRAM_SOURCE"


@pytest.mark.parametrize('project_model,version_model,supplied', [
    ('Q03UDVCPU', None, 'q03udv'), ('Q03UDV', 'q03udvcpu', 'Q03UDVCPU'),
    ('FX3U/FX3UC', None, 'fx3ucpu / fx3uccpu'),
])
def test_manual_model_normalization_reaches_both_context_layers(monkeypatch, project_model, version_model, supplied):
    from types import SimpleNamespace
    from agent_runtime.tool_context import build_tool_context, resolve_tool_context
    calls = []
    def available(model):
        calls.append(model)
        return {'status': 'facts_available'}
    monkeypatch.setattr('knowledge.fact_corpus.model_corpus_status', available)
    context = build_tool_context({'id': 'project', 'plc_model': project_model},
        version={'id': 'version', 'plc_model': version_model} if version_model else None)
    result = resolve_tool_context(SimpleNamespace(name='search_plc_manual', context_requirement='model'),
                                  context, {'plc_model': supplied})
    expected = 'FX3U/FX3UC' if '/' in project_model else 'Q03UDV'
    assert result.plc_model == expected and result.project['plc_model'] == expected
    assert calls == [expected] and result.project_id == 'project'
    if version_model:
        assert result.version['plc_model'] == expected and result.version_id == 'version'
    assert context.project['plc_model'] == project_model


@pytest.mark.parametrize('entry', ['service', 'workbench', 'local_mcp', 'legacy_mcp'])
def test_model_manual_query_does_not_load_unreadable_program_ir(saved_project, tmp_path, monkeypatch, entry):
    from dataclasses import replace
    from application.projects import ProjectService
    from application.workbench import WorkbenchService
    from application.engineering_session import EngineeringSession
    from integrations.mcp.local_adapter import LocalMCPToolAdapter
    store, project_id, version, _ = saved_project
    def broken_program(*args, **kwargs):
        raise ValueError('stored ordered IR cannot be validated')
    monkeypatch.setattr(ProjectService, 'program', broken_program)
    monkeypatch.setattr('knowledge.fact_corpus.model_corpus_status', lambda model: {'status': 'facts_available'})
    provider = _provider(saved_project, pin=True)
    with pytest.raises(ContextUnavailableError, match='stored ordered IR'):
        provider.get_context()
    metadata = provider.get_model_context()
    assert metadata.plc_model == 'FX3U' and metadata.program_ir is None
    calls = []
    owned = None
    if entry == 'legacy_mcp':
        runtime = build_default_tool_runtime()
        invoke = lambda arguments: MCPToolAdapter(runtime, provider).call_tool('search_plc_manual', arguments, 'manual')
    elif entry == 'local_mcp':
        owned = EngineeringSession(store.base_dir, project_id=project_id, version_id=version['id'], state_dir=tmp_path / 'state')
        runtime = owned.service.projects.runtime
        invoke = lambda arguments: LocalMCPToolAdapter(owned).call_tool('search_plc_manual', arguments, 'manual')
    else:
        owned = WorkbenchService(store.base_dir, tmp_path / 'state')
        runtime = owned.projects.runtime
        if entry == 'service':
            invoke = lambda arguments: owned.projects.invoke(project_id, version['id'], 'manual', 'search_plc_manual', arguments)
        else:
            owned.start()
            invoke = lambda arguments: owned.agent_call(dict(project_id=project_id, version_id=version['id'],
                call_id='manual', name='search_plc_manual', arguments=arguments))
    definition = runtime.registry._tools['search_plc_manual']
    def search(context, arguments):
        calls.append((context.plc_model, context.program_ir, context.native_snapshot))
        return {'plc_model': context.plc_model, 'query': arguments['query'], 'status': 'not_found', 'sections': []}
    runtime.registry._tools['search_plc_manual'] = replace(definition, handler=search)
    try:
        result = invoke({'query': 'MEP'})
        assert not (result['is_error'] if isinstance(result, dict) else result.is_error), result
        assert calls == [('FX3U', None, None)]
    finally:
        if owned is not None:
            owned.close()


@pytest.mark.parametrize('model,status,code', [
    ('FX3U/BOGUS', None, 'UNKNOWN_PLC_MODEL'),
    ('FX3U/FX1S', None, 'UNKNOWN_PLC_MODEL'),
    ('FX5U', 'no_facts_for_model', 'KNOWLEDGE_UNAVAILABLE'),
    ('FX5U', 'index_unavailable', 'KNOWLEDGE_INDEX_UNAVAILABLE'),
])
def test_manual_model_errors_retain_the_requested_identity(monkeypatch, model, status, code):
    from types import SimpleNamespace
    from agent_runtime.tool_context import resolve_tool_context
    from agent_runtime.tool_errors import ToolPreconditionError
    monkeypatch.setattr('knowledge.fact_corpus.model_corpus_status',
        lambda cpu: {'plc_model': cpu, 'status': status})
    with pytest.raises(ToolPreconditionError) as caught:
        resolve_tool_context(SimpleNamespace(name='search_plc_manual', context_requirement='model'),
                             None, {'plc_model': model})
    assert caught.value.code == code
    assert caught.value.details['plc_model'] == model


def test_manual_availability_distinguishes_empty_model_facts_from_a_broken_index(tmp_path, monkeypatch):
    import sqlite3
    from knowledge import index_access
    from knowledge.fact_corpus import model_corpus_status
    path = tmp_path / 'facts.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE facts(plc_model TEXT)')
        connection.execute("INSERT INTO facts VALUES('FX3U')")
    index_access.close_thread_connection()
    monkeypatch.setattr(index_access, 'index_path', lambda: path)
    assert model_corpus_status('FX3U')['status'] == 'facts_available'
    assert model_corpus_status('FX5U')['status'] == 'no_facts_for_model'
    group = model_corpus_status('FX3U/FX3UC')
    assert group['missing_members'] == ['FX3UC'] and group['status'] == 'no_facts_for_model'
    index_access.close_thread_connection()
    monkeypatch.setattr(index_access, 'index_path', lambda: None)
    assert model_corpus_status('FX3U')['status'] == 'index_unavailable'


@pytest.mark.parametrize("spelling,expected", [
    ("Q03UDVCPU", "Q03UDV"), ("q03udv", "Q03UDV"), ("FX5U", "FX5U"),
])
def test_manual_search_resolves_hardware_spellings_without_native_project_support(tmp_path, spelling, expected):
    session, _ = _inspection_session(tmp_path)
    found = session.invoke(ToolCall("manual", "search_plc_manual",
                                    {"query": "MOV 指令 操作数", "plc_model": spelling, "top_k": 3}))
    assert not found.is_error, found.data
    from integrations.mcp.local_adapter import LocalMCPToolAdapter
    document, _ = _complete_mcp_read(LocalMCPToolAdapter(session), "search_plc_manual",
        {"query": "MOV 指令 操作数", "plc_model": spelling, "top_k": 3})
    assert document["plc_model"] == expected
    unknown = session.invoke(ToolCall("bad-model", "search_plc_manual",
                                      {"query": "MOV 指令 操作数", "plc_model": "FX9Z"}))
    assert unknown.is_error and unknown.data["error"]["code"] == "UNKNOWN_PLC_MODEL"
    assert unknown.data["error"]["next_actions"]
    empty = session.invoke(ToolCall("empty-query", "search_plc_manual",
                                    {"query": "   ", "plc_model": "FX3U"}))
    assert empty.is_error and empty.data["error"]["code"] == "INVALID_ARGUMENTS"
    assert empty.data["error"]["next_actions"]


@pytest.mark.parametrize("case", ["unknown", "unsupported_shape", "project_title", "wrong_cpu"])
def test_snapshot_preserves_coverage_and_never_confuses_export_title_with_program(tmp_path, case):
    import csv
    session, native = _inspection_session(tmp_path)
    with native.program.open(encoding="utf-16", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if case == "unknown":
        rows[5][2:4] = ["ZZVENDOR", "D0"]
    elif case == "unsupported_shape":
        rows.insert(4, ["1", "", "MPS", "", "", "", ""])
        rows.insert(5, ["2", "", "MPS", "", "", "", ""])
    elif case == "project_title":
        rows[0] = ["Warehouse - 副本"]
    else:
        rows[1][1] = "QCPU(Q模式) Q03UDV"
    for step, row in enumerate(rows[3:]):
        row[0] = str(step)
    with native.program.open("w", encoding="utf-16", newline="") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows(rows)
    selected = session.invoke(ToolCall("list", "list_gxworks2_sessions", {})).data["data"]["sessions"][0]["session_id"]
    result = session.invoke(ToolCall("read", "read_gxworks2_snapshot", {"session_id": selected}))
    if case == "wrong_cpu":
        assert result.is_error and result.data["error"]["code"] == "NATIVE_SOURCE_MISMATCH"
        return
    assert not result.is_error, result.data
    data = result.data["data"]
    assert data["program_name"] == "MAIN"
    if case == "project_title":
        assert data["export_title"] == "Warehouse - 副本"
        return
    ref = data["source_ref"]
    network = session.invoke(ToolCall("network", "read_network", {"source_ref": ref, "network_id": "N0001"}))
    assert not network.is_error
    raw = network.data["data"].get("raw_instructions") or network.data["data"]["network"]["raw_instructions"]
    if case == "unknown":
        assert any(row["op"] == "ZZVENDOR" for row in raw)
    else:
        # The doubled MPS breaks the ordered stack, not just the drawing preview.
        assert data["coverage"]["ir_status"] == "failed"
        assert data["coverage"]["parse_status"] == "partial"
        assert data["coverage"]["graph_preview_status"] == "partial"
    checked = session.invoke(ToolCall("check", "get_diagnostics", {"source_ref": ref}))
    assert checked.is_error or checked.data["data"]["valid"] is False
    assert not session.workspace.exists()


def test_native_snapshot_keeps_reading_and_static_checks_when_only_the_drawing_preview_fails(tmp_path):
    import csv
    from gxworks2.csv_manager import GXWORKS2_COMMENT_HEADER, GXWORKS2_HEADER
    session, native = _inspection_session(tmp_path)
    # A condition that restarts after an output cannot be projected into the
    # older graphical model, but the stored source order is unambiguous.
    rows = [["MAIN"], ["PLC信息:", "FXCPU FX3U/FX3UC"], GXWORKS2_HEADER,
            ["0", "Start", "LD", "X000", "", "", ""],
            ["1", "", "ANI", "X001", "", "", ""],
            ["2", "", "OUT", "Y000", "", "", ""],
            ["3", "", "AND", "X002", "", "", ""],
            ["4", "", "OUT", "Y001", "", "", ""],
            ["5", "", "END", "", "", "", ""]]
    for path, values in ((native.program, rows),
                         (native.comments, [["COMMENT"], GXWORKS2_COMMENT_HEADER,
                                            ["X000", "Enter"]])):
        with path.open("w", encoding="utf-16", newline="") as handle:
            csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL,
                       lineterminator="\r\n").writerows(values)
    selected = session.invoke(ToolCall("list", "list_gxworks2_sessions", {})).data["data"]["sessions"][0]["session_id"]
    result = session.invoke(ToolCall("read", "read_gxworks2_snapshot", {"session_id": selected}))
    assert not result.is_error, result.data
    data = result.data["data"]
    coverage = data["coverage"]
    assert coverage["instruction_status"] == "complete"
    assert coverage["ir_status"] == "valid"
    assert coverage["parse_status"] == "complete"
    assert coverage["graph_preview_status"] == "partial"
    assert [row["network_id"] for row in coverage["graph_preview_failures"]] == ["N0001"]
    ref = data["source_ref"]
    network = session.invoke(ToolCall("network", "read_network", {"source_ref": ref, "network_id": "N0001"}))
    assert not network.is_error, network.data
    raw = network.data["data"]["raw_instructions"]
    assert [row["op"] for row in raw] == ["LD", "ANI", "OUT", "AND", "OUT", "END"]
    assert [row["source_row"] for row in raw] == [4, 5, 6, 7, 8, 9]
    assert [(row["instruction_index"], row["source_row"], row["step"])
            for row in network.data["data"]["source_map"]] == [
                (index, row, step) for index, (row, step) in enumerate(
                    [(4, "0"), (5, "1"), (6, "2"), (7, "3"), (8, "4"), (9, "5")])]
    assert network.data["data"]["network"]["reads"] == ["X000", "X001", "X002"]
    assert network.data["data"]["network"]["writes"] == ["Y000", "Y001"]
    checked = session.invoke(ToolCall("check", "get_diagnostics", {"source_ref": ref}))
    assert not checked.is_error, checked.data
    report = checked.data["data"]
    # The static checks ran on the ordered IR; only the drawing preview is absent.
    assert report.get("reason") != "source_coverage_incomplete"
    assert report["counts"]["error"] == 0 and report["rules_checked"]
    assert report["runtime_verified"] is False
    validated = session.invoke(ToolCall("validate", "validate_current_program", {"source_ref": ref}))
    assert not validated.is_error, validated.data
    assert validated.data["data"].get("reason") != "source_coverage_incomplete"
    assert validated.data["data"]["source_format"] == "instruction_sequence_v1"
    assert validated.data["data"]["runtime_verified"] is False
    assert not session.workspace.exists()


def _write_csv(path, rows):
    import csv
    with path.open("w", encoding="utf-16", newline="") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL,
                   lineterminator="\r\n").writerows(rows)


def _csv_only_session(tmp_path):
    """An inspection host with no native backend and no selected project."""
    from application.engineering_session import EngineeringSession
    from application.program_inspection import ProgramInspectionService
    session = EngineeringSession(tmp_path / "workspace", state_dir=tmp_path / "state")
    session.inspection = ProgramInspectionService(native=None)
    return session


def test_csv_files_give_a_source_ref_without_gxworks2_or_a_selected_project(tmp_path):
    import base64
    from gxworks2.csv_manager import GXWORKS2_COMMENT_HEADER, GXWORKS2_HEADER
    session = _csv_only_session(tmp_path)
    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    _write_csv(program, [["MAIN"], ["PLC信息:", "FXCPU FX3U/FX3UC"], GXWORKS2_HEADER,
                         ["0", "", "LD", "X000", "", "", ""],
                         ["1", "", "OUT", "Y000", "", "", ""],
                         ["2", "", "ANI", "X001", "", "", ""],
                         ["3", "", "OUT", "Y001", "", "", ""],
                         ["4", "", "END", "", "", "", ""]])
    _write_csv(comments, [["COMMENT"], GXWORKS2_COMMENT_HEADER,
                          ["X0", "启动"], ["Y0", "输出"]])
    result = session.invoke(ToolCall("csv", "inspect_gxworks2_csv_files", {
        "program_path": str(program),
        "comment_data_base64": base64.b64encode(comments.read_bytes()).decode(),
        "plc_model": "fx3u", "program_name": "MAIN"}))
    assert not result.is_error, result.data
    data = result.data["data"]
    assert data["kind"] == "csv_file_snapshot" and data["plc_model"] == "FX3U"
    assert data["program_name"] == "MAIN" and data["editable"] is False
    assert data["origin"]["program_file"] == "MAIN.csv"
    coverage = data["coverage"]
    # A condition restarting after an output cannot be drawn, but it is still
    # read in source order and the ordered IR is valid.
    assert coverage["parse_status"] == "complete"
    assert coverage["instruction_status"] == "complete"
    assert coverage["ir_status"] == "valid"
    assert coverage["graph_preview_status"] == "partial" and coverage["graph_preview_failures"]
    assert coverage["unknown_instructions"] == []
    assert coverage["runtime_verified"] is False
    assert data["comment_count"] == 2
    ref = data["source_ref"]

    listed = session.invoke(ToolCall("info", "get_current_program_info", {"source_ref": ref}))
    assert not listed.is_error, listed.data
    networks = listed.data["data"]["networks"]
    assert "".join(row["id"] for row in networks).startswith("N0001")
    network_ids = [row["id"] for row in networks]
    ordered = []
    for network_id in network_ids:
        read = session.invoke(ToolCall("net", "read_network", {
            "source_ref": ref, "network_id": network_id}))
        assert not read.is_error, read.data
        ordered.extend(row["op"] for row in read.data["data"]["raw_instructions"])
    assert ordered == ["LD", "OUT", "ANI", "OUT", "END"]
    first = session.invoke(ToolCall("first", "read_network", {
        "source_ref": ref, "network_id": "N0001"}))
    assert first.data["data"]["source_map"][0] == {
        "network_id": "N0001", "instruction_index": 0, "step": "0",
        "source_row": 4, "statement": "", "label": ""}
    checked = session.invoke(ToolCall("diag", "get_diagnostics", {"source_ref": ref}))
    assert not checked.is_error, checked.data
    assert checked.data["data"].get("reason") is None
    assert checked.data["data"]["runtime_verified"] is False
    validated = session.invoke(ToolCall("check", "validate_current_program", {"source_ref": ref}))
    assert not validated.is_error, validated.data
    assert validated.data["data"].get("reason") is None
    assert not session.workspace.exists()


def test_csv_files_reject_an_implicit_model_or_an_ambiguous_document(tmp_path):
    from gxworks2.csv_manager import GXWORKS2_COMMENT_HEADER, GXWORKS2_HEADER
    session = _csv_only_session(tmp_path)
    program, comments = tmp_path / "MAIN.csv", tmp_path / "COMMENT.csv"
    _write_csv(program, [["MAIN"], ["PLC信息:", "FXCPU FX3U/FX3UC"], GXWORKS2_HEADER,
                         ["0", "", "LD", "X000", "", "", ""],
                         ["1", "", "END", "", "", "", ""]])
    _write_csv(comments, [["COMMENT"], GXWORKS2_COMMENT_HEADER, ["X0", "启动"]])
    cases = {
        # An explicit CPU is required; a default would silently change widths.
        "model": {"program_path": str(program), "comment_path": str(comments)},
        "both_documents": {"program_path": str(program), "program_data_base64": "AAAA",
                           "comment_path": str(comments), "plc_model": "FX3U"},
        "no_document": {"program_path": str(program), "plc_model": "FX3U"},
        "missing_file": {"program_path": str(tmp_path / "absent.csv"),
                         "comment_path": str(comments), "plc_model": "FX3U"},
        "unknown_model": {"program_path": str(program), "comment_path": str(comments),
                          "plc_model": "FX9Z"},
    }
    for case, arguments in cases.items():
        result = session.invoke(ToolCall(case, "inspect_gxworks2_csv_files", arguments))
        assert result.is_error, case
        assert result.data["error"]["code"] == "INVALID_ARGUMENTS", (case, result.data)
    # A document that is not a native UTF-16 export is reported as an invalid
    # CSV source, not as a parse failure of the analysis host.
    broken = tmp_path / "BROKEN.csv"
    broken.write_text("步号\t行间声明\t指令\n0\t\tLD\n", encoding="utf-8")
    result = session.invoke(ToolCall("broken", "inspect_gxworks2_csv_files", {
        "program_path": str(broken), "comment_path": str(comments), "plc_model": "FX3U"}))
    assert result.is_error and result.data["error"]["code"] == "CSV_SOURCE_INVALID"
    assert not session.workspace.exists()


def test_supplied_csv_export_is_read_as_ordered_ir_without_a_native_session(tmp_path):
    from tools.offline_inputs import private_path
    fixtures = Path(private_path("research/fixtures/gxw-offline/warehouse"))
    session = _csv_only_session(tmp_path)
    result = session.invoke(ToolCall("csv", "inspect_gxworks2_csv_files", {
        "program_path": str(fixtures / "MAIN.csv"),
        "comment_path": str(fixtures / "COMMENT.csv"),
        "plc_model": "FX3U"}))
    assert not result.is_error, result.data
    data = result.data["data"]
    coverage = data["coverage"]
    assert coverage["instruction_count"] == 155
    assert coverage["network_count"] == coverage["parsed_network_count"] == 9
    assert coverage["parse_status"] == "complete" and coverage["ir_status"] == "valid"
    assert coverage["graph_preview_status"] == "partial"
    assert {row["network_id"] for row in coverage["graph_preview_failures"]} == {
        "N0003", "N0004", "N0009"}
    assert coverage["unknown_instructions"] == [] and coverage["runtime_verified"] is False
    assert data["comment_count"] == 22
    ref = data["source_ref"]
    for network_id in ("N0003", "N0004", "N0009"):
        read = session.invoke(ToolCall("net", "read_network", {
            "source_ref": ref, "network_id": network_id}))
        assert not read.is_error, read.data
        binding = read.data["data"]["source_map"]
        assert binding and all(row["source_row"] >= 4 for row in binding)
        assert [row["instruction_index"] for row in binding] == list(range(len(binding)))
    checked = session.invoke(ToolCall("diag", "get_diagnostics", {"source_ref": ref}))
    assert not checked.is_error, checked.data
    assert checked.data["data"].get("reason") is None
    assert not session.workspace.exists()


@pytest.mark.parametrize('mode,outcome', [
    ('ask', 'completed'), ('auto', 'completed'), ('full', 'completed'),
    ('full', 'interrupted'), ('full', 'unverified'),
])
def test_saved_st_mcp_import_then_native_execution_preserves_approval_frozen_version_and_failure_state(tmp_path, monkeypatch, mode, outcome):
    import base64
    from concurrent.futures import Future
    from application.engineering_session import EngineeringSession
    from application.execution import GXExecutionCoordinator
    from integrations.mcp.local_adapter import LocalMCPToolAdapter
    from tests.test_execution_coordinator import native_delivery_receipt, native_st_source_witness
    raw = native_st_source_witness()
    calls = []
    def execute(_self, operation, payload, **options):
        assert operation == 'native_engineering' and options['approval_id']
        calls.append(copy.deepcopy(payload))
        receipt = native_delivery_receipt(raw, payload['plan']['commands'])
        if outcome == 'interrupted':
            receipt.update(status='interrupted', passed=False, unverified_data=b'not verified')
        elif outcome == 'unverified':
            receipt['data_status'] = 'unverified'
        future = Future()
        future.set_result({'status': 'completed', 'passed': True, '_native_result': receipt})
        return future
    monkeypatch.setattr(GXExecutionCoordinator, 'submit_approved', execute)
    session = EngineeringSession(tmp_path / 'workspace', state_dir=tmp_path / 'state',
        approval_mode=mode, confirm_full_access=mode == 'full')
    adapter = LocalMCPToolAdapter(session)
    assert not adapter.call_tool('create_project', {'name': 'pure ST', 'plc_model': 'FX3U', 'target_mode': 'st'}, 'st-project').is_error
    imported = adapter.call_tool('create_st_candidate', {'operation': 'import',
        'program': 'POU_01.Program.pou', 'data_base64': base64.b64encode(raw).decode()}, 'st-source')
    assert not imported.is_error and _mcp_body(imported)['status'] == 'saved'
    base = _mcp_body(imported)['data']['version_id']
    project_id = session.project_id
    original = copy.deepcopy(session.service.store.get_version(project_id, base))
    schema = next(tool for tool in adapter.list_tools() if tool.name == 'execute_native_engineering')
    assert schema.annotations.destructive_hint and not schema.annotations.read_only_hint
    plan = {'schema_version': 1, 'commands': [{'operation': 'save'}, {'operation': 'reopen'}]}
    original_plan = copy.deepcopy(plan)
    response = adapter.call_tool('execute_native_engineering', {'plan': plan}, 'st-native')
    public = _mcp_body(response)
    if mode != 'full':
        assert not response.is_error and public['status'] == 'execution_consent_required'
        assert not calls and session.service.store.get_project(project_id)['active_version_id'] == base
    else:
        assert len(calls) == 1 and calls[0]['version_id'] == base and calls[0]['plan'] == original_plan
        if outcome == 'completed':
            assert not response.is_error and public['data']['proposal_status'] == 'accepted'
            final = public['data']['execution_result']['version_id']
            assert final != base and session.service.store.get_project(project_id)['active_version_id'] == final
            assert session.service.projects.artifact(project_id, final, 'gxw').read_bytes() == raw
            assert session.service.store.get_version(project_id, final)['target_mode'] == 'st'
            assert public['data']['execution_result']['gx_compile_status'] == 'not_run'
        else:
            assert response.is_error and public['data']['proposal_status'] == 'interrupted'
            assert public['data']['execution_result']['passed'] is False
            assert session.service.store.get_project(project_id)['active_version_id'] == base
    plan['commands'][0]['operation'] = 'compile'
    frozen = session.service.proposals.read_private(public['proposal_id'])
    assert frozen['version_id'] == base and frozen['plan'] == original_plan
    assert session.service.store.get_version(project_id, base) == original
    assert len(session.service.store.get_project(project_id)['versions']) == (2 if mode == 'full' and outcome == 'completed' else 1)
    encoded = response.model_dump_json()
    assert not any(value in encoded for value in ('_native_result', 'unverified_data', 'evidence_directory', '_st_candidate', 'bytes_base64'))
    replay = adapter.call_tool('execute_native_engineering', {'plan': original_plan}, 'st-native')
    assert _mcp_body(replay) == public and len(calls) == int(mode == 'full')


@pytest.mark.parametrize('text_change,accepted', [('LF', True), ('CRLF', True), ('space', False), ('symbol', False)])
def test_st_native_tool_ingress_allows_line_endings_and_requires_every_other_source_character(text_change, accepted):
    from gxw.fb_authoring import st_program_snapshot
    from tests.test_execution_coordinator import native_st_source_witness
    raw = native_st_source_witness()
    program, = st_program_snapshot(raw)['programs']
    text = program['st_text']
    if text_change == 'LF': text = text.replace('\r\n', '\n')
    elif text_change == 'CRLF': text = text.replace('\r\n', '\n').replace('\n', '\r\n')
    elif text_change == 'space': text += ' '
    else: text = text.replace('ST_Result', 'Different_Result')
    context = build_tool_context({'id': 'st-project', 'plc_model': 'FX3U', 'target_mode': 'st'},
        version={'id': 'v0001', 'plc_model': 'FX3U', 'target_mode': 'st', 'program_name': program['program']},
        native_snapshot=raw, st_text=text)
    adapter = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context))
    result = adapter.call_tool('execute_native_engineering', {'plan': {'schema_version': 1,
        'commands': [{'operation': 'save'}]}}, 'source-ingress')
    assert result.is_error is not accepted
    if accepted:
        assert _mcp_body(result)['data']['requires_confirmation'] is True
        assert _mcp_body(result)['data']['pending_action']['version_id'] == 'v0001'


@pytest.mark.parametrize('damage', ['st', 'gxw', 'report', 'metadata-task', 'metadata-cpu', 'metadata-pou'])
def test_packed_st_candidate_shared_import_rejects_semantic_tampering_even_with_matching_artifact_manifest(tmp_path, damage):
    import base64
    import hashlib
    from application.workbench import WorkbenchService
    from tests.test_execution_coordinator import native_st_source_witness
    service = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state')
    service.start()
    try:
        project = service.create_project(name='packed ST', plc_model='FX3U', target_mode='st')
        response = service.projects.runtime.invoke(ToolCall('st-import', 'create_st_candidate', {
            'operation': 'import', 'program': 'POU_01.Program.pou',
            'data_base64': base64.b64encode(native_st_source_witness()).decode()}),
            service.projects.tool_context(project['id']))
        assert not response.is_error, response.data
        pending = copy.deepcopy(response.data['data']['pending_action'])
        candidate = pending['_st_candidate']
        if damage in ('st', 'gxw', 'report'):
            artifact = 'write_report' if damage == 'report' else damage
            value = base64.b64decode(candidate['files'][artifact])
            if damage == 'st': value += b' '
            elif damage == 'gxw': value = native_st_source_witness(kind='creation')
            else:
                report = json.loads(value)
                report['source']['selected_program']['task_bindings'][0]['line'] = 99
                value = json.dumps(report).encode('utf-8')
            candidate['files'][artifact] = base64.b64encode(value).decode()
            candidate['artifacts'][artifact]['sha256'] = hashlib.sha256(value).hexdigest()
        elif damage == 'metadata-task':
            candidate['metadata']['validation']['source_binding']['task_bindings'][0]['line'] = 99
        elif damage == 'metadata-cpu':
            candidate['plc_model'] = candidate['metadata']['plc_model'] = 'L02'
        else:
            candidate['metadata']['program_name'] = 'POU_01'
        with pytest.raises(ValueError):
            service._pending_proposal(pending, 'tampered-st', direct_request=True)
        current = service.projects.raw_project(project['id'])
        assert not current['versions'] and current['active_version_id'] is None
        assert not service.proposals.list(project['id'])
    finally:
        service.close()


def test_saved_plain_text_st_without_gxw_cannot_request_native_execution(tmp_path, monkeypatch):
    from application.engineering_session import EngineeringSession
    from application.execution import GXExecutionCoordinator
    from integrations.mcp.local_adapter import LocalMCPToolAdapter
    store = SessionStore(base_dir=tmp_path / 'workspace', legacy_dir=tmp_path / 'legacy')
    project = store.create_project('ST text', target_mode='st', plc_model='FX3U')
    version_id, directory = store.prepare_version(project['id'])
    (directory / 'program.st').write_text('Y0 := X0;', encoding='utf-8')
    store.complete_version(project['id'], version_id, {'target_mode': 'st', 'plc_model': 'FX3U',
        'program_name': 'MAIN', 'artifacts': {'st': 'program.st'}})
    monkeypatch.setattr(GXExecutionCoordinator, 'submit_approved', lambda *_args, **_options: pytest.fail('Text-only ST reached native execution'))
    session = EngineeringSession(store.base_dir, state_dir=tmp_path / 'state', project_id=project['id'],
        approval_mode='full', confirm_full_access=True)
    result = LocalMCPToolAdapter(session).call_tool('execute_native_engineering', {
        'plan': {'schema_version': 1, 'commands': [{'operation': 'save'}]}}, 'unsaved-st')
    assert result.is_error and store.get_project(project['id'])['active_version_id'] == version_id
    assert len(store.get_project(project['id'])['versions']) == 1


@pytest.mark.parametrize('tool,arguments', [
    ('read_st_project', {'pid': 123}),
    ('create_st_candidate', {'operation': 'import', 'data_base64': '', 'program': 'POU_01.Program.pou', 'path': r'D:\unmanaged.gxw'}),
    ('create_st_candidate', {'operation': 'import', 'data_base64': '', 'program': 'POU_01.Program.pou', 'st_text': 'Y0 := X0;'}),
    ('execute_native_engineering', {'plan': {'schema_version': 1, 'commands': [{'operation': 'save'}]}, 'approval_mode': 'full'}),
])
def test_st_mcp_tool_schemas_do_not_accept_process_path_text_authoring_or_consent_overrides(tool, arguments):
    import base64
    from gxw.fb_authoring import st_program_snapshot
    from tests.test_execution_coordinator import native_st_source_witness
    raw = native_st_source_witness()
    program, = st_program_snapshot(raw)['programs']
    arguments = copy.deepcopy(arguments)
    if tool == 'create_st_candidate':
        arguments['data_base64'] = base64.b64encode(raw).decode()
    context = build_tool_context({'id': 'st-project', 'plc_model': 'FX3U', 'target_mode': 'st'},
        version={'id': 'v0001', 'plc_model': 'FX3U', 'target_mode': 'st', 'program_name': program['program']},
        native_snapshot=raw, st_text=program['st_text'])
    adapter = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context))
    schema = next(item.input_schema for item in adapter.list_tools() if item.name == tool)
    assert schema['additionalProperties'] is False
    result = adapter.call_tool(tool, arguments, 'no-override')
    assert result.is_error and _mcp_body(result)['error']['code'] == 'INVALID_ARGUMENTS'


@pytest.mark.parametrize('instruction_name', [
    'SERVER_INSTRUCTIONS', 'SERVICE_SERVER_INSTRUCTIONS', 'LOCAL_SERVER_INSTRUCTIONS',
])
def test_mcp_st_native_workflow_guidance_matches_the_shared_tool_contracts(instruction_name):
    from integrations.mcp import server

    instructions = getattr(server, instruction_name)
    adapter = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(build_tool_context({})))
    tools = {tool.name: tool for tool in adapter.list_tools()}
    for name in ('read_st_project', 'create_st_candidate', 'execute_native_engineering'):
        assert name in instructions and name in tools
    assert 'does not synthesize a GXW from ST text' in instructions
    assert 'existing resource and task' in instructions

    imported = tools['create_st_candidate'].input_schema
    assert set(imported['properties']['operation']['enum']) == {
        'import', 'create_st_program', 'edit_st_program', 'edit_st_fb',
        'edit_st_function', 'edit_st_source', 'from_candidate'}
    import_shape = next(shape for shape in imported['oneOf'] if shape['properties']['operation']['enum'] == ['import'])
    for field in ('operation', 'program', 'data_base64'):
        assert field in import_shape['required']
    assert 'operation=import' in instructions and 'program=<exact logical source>' in instructions
    assert 'data_base64' in instructions and 'st_text' not in import_shape['properties']
    assert 'background edits' in instructions and 'exact logical source_name' in instructions
    assert 'Omitted declarations preserve' in instructions
    assert 'replace the complete declaration list' in instructions
    assert 'confirmation before activation' in instructions
    for operation in ('edit_st_program', 'edit_st_fb', 'edit_st_function', 'edit_st_source'):
        assert operation in instructions
        shape = next(row for row in imported['oneOf'] if row['properties']['operation']['enum'] == [operation])
        assert {'operation', 'source_name', 'st_text'} <= set(shape['required'])
        assert 'declarations' not in shape['required']
    Draft202012Validator(imported).validate({
        'operation': 'import', 'program': '计算输出_ST.Program.pou', 'data_base64': 'c2F2ZWQgR1hX',
    })

    native = tools['execute_native_engineering'].input_schema
    command = native['properties']['plan']['properties']['commands']['items']
    assert {'create_st_program', 'edit_st_program', 'edit_fbd_graph', 'compile',
            'show_assignments', 'save', 'reopen'} <= set(command['properties']['operation']['enum'])
    assert command['properties']['scope_mode']['enum'] == ['existing_task']
    assert {'source_name', 'declarations', 'st_text', 'resource', 'task', 'scope_mode',
            'caller', 'actions'} <= command['properties'].keys()
    assert not tools['execute_native_engineering'].annotations.read_only_hint
    assert tools['execute_native_engineering'].annotations.destructive_hint
    batch, live = native['properties']['plan']['oneOf']
    for shape in (batch, live):
        variants = shape['properties']['commands']['items']['oneOf']
        visible_operations = {row['properties']['operation']['enum'][0] for row in variants}
        assert {'create_st_fb', 'edit_st_fb', 'create_st_program', 'edit_st_program'} <= visible_operations
        assert not {'edit_st_function', 'edit_st_source'} & visible_operations


def _engineering_schema_examples():
    return [
        {'operation': 'create_global_labels', 'source_name': 'FutureGlobals', 'declarations': [
            {'name': 'Command', 'data_type': 'BOOL', 'class_name': 'VAR_GLOBAL', 'device': 'M0', 'iec_address': '%MX0.0'}]},
        {'operation': 'edit_global_labels', 'source_name': 'FutureGlobals', 'declarations': [
            {'name': 'Value', 'data_type': 'INT', 'comment': 'new value'}]},
        {'operation': 'edit_pou_labels', 'source_name': 'FutureFB', 'declarations': [
            {'name': 'Input', 'data_type': 'BOOL', 'class_name': 'VAR_INPUT'}]},
        {'operation': 'create_structure', 'source_name': 'FutureStruct', 'members': [{'name': 'Value', 'data_type': 'INT'}]},
        {'operation': 'create_st_fb', 'source_name': 'FutureFB', 'declarations': [
            {'name': 'Value', 'data_type': 'FutureStruct', 'kind': 'structure'}], 'st_text': ';'},
        {'operation': 'edit_st_fb', 'source_name': 'FutureFB', 'st_text': '(* next body *) ;'},
        {'operation': 'create_st_program', 'source_name': 'FutureProgram', 'declarations': [
            {'name': 'Instance', 'data_type': 'FutureFB', 'kind': 'function_block'}],
         'st_text': ';', 'resource': 'MAIN', 'task': 'Task_01', 'scope_mode': 'existing_task'},
        {'operation': 'edit_st_program', 'source_name': 'FutureProgram', 'st_text': ';'},
        {'operation': 'configure_project', 'source_name': 'Project.prj', 'options': {'system_variable_ranges': {'D': [2000, 2999]}}},
        {'operation': 'create_fbd_call', 'caller': 'Future.Program.pou', 'instance': 'Instance', 'definition': 'FutureFB',
         'arguments': {'Input': 'Command', 'Output': 'Value'}},
        {'operation': 'edit_fbd_graph', 'caller': 'Future.Program.pou', 'actions': [
            {'action': 'rebind_terminal', 'node': 'n1', 'symbol': 'Command'},
            {'action': 'move_node', 'node': 'n1', 'x': 7, 'y': 2},
            {'action': 'delete_terminal', 'node': 'n2'}, {'action': 'delete_wire', 'wire': 'w1'},
            {'action': 'add_wire', 'id': 'newPath', 'from': {'node': 'n1', 'port': 'out'},
             'to': {'wire': 'w2', 'point': [18, 2]}, 'via': [[14, 2]]}]},
    ]


def test_native_engineering_detailed_schema_defers_future_objects_and_accepts_all_current_actions():
    from agent_runtime.plc_tools import _validate_engineering_input

    runtime = build_default_tool_runtime()
    schema = runtime.registry._tools['execute_native_engineering'].parameters
    commands = _engineering_schema_examples()
    query = {'scope': 'global', 'source': 'FutureGlobals.gh', 'name': 'Value'}
    commands += [{'operation': 'compile'}, {'operation': 'show_assignments', 'queries': [query]},
                 {'operation': 'edit_st_fb', 'source_name': 'FutureFB', 'st_text': '(* revision 2 *) ;'},
                 {'operation': 'compile'}, {'operation': 'show_assignments', 'queries': [dict(query)]},
                 {'operation': 'save'}, {'operation': 'reopen'}]
    arguments = {'plan': {'schema_version': 1, 'commands': commands}}
    original = copy.deepcopy(arguments)
    Draft202012Validator(schema).validate(arguments)
    _validate_engineering_input('execute_native_engineering', schema, arguments)
    assert arguments == original
    rejected = runtime.invoke(ToolCall('future-context', 'execute_native_engineering', arguments), build_tool_context({}))
    assert rejected.data['error']['code'] == 'NO_PROJECT_SELECTED'
    action_shape = schema['properties']['plan']['properties']['commands']['items']['properties']['actions']
    for index, actions in enumerate(action_shape['oneOf']):
        names = {shape['properties']['action']['enum'][0] for shape in actions['items']['oneOf']}
        expected = {'rebind_terminal', 'move_node', 'delete_terminal', 'delete_wire', 'add_wire'}
        assert names == (expected | {'add_terminal'} if index == 1 else expected)


@pytest.mark.parametrize('tool,arguments', [
    ('execute_native_engineering', {'plan': {'schema_version': 1, 'commands': [
        {'operation': 'edit_pou_labels', 'source_name': 'FB', 'declarations': [{'name': 'X', 'data_type': 'BOOL', 'device': 'M0'}]}]}}),
    ('execute_native_engineering', {'plan': {'schema_version': 1, 'commands': [
        {'operation': 'create_st_fb', 'source_name': 'FB', 'declarations': []}]}}),
    ('execute_native_engineering', {'plan': {'schema_version': 1, 'commands': [
        {'operation': 'create_st_program', 'source_name': 'ST', 'declarations': [], 'st_text': ';', 'resource': 'MAIN', 'scope_mode': 'existing_task'}]}}),
    ('execute_native_engineering', {'plan': {'schema_version': 1, 'commands': [
        {'operation': 'configure_project', 'source_name': 'P.prj', 'options': {'system_variable_ranges': {'D': [20, True]}}}]}}),
    ('execute_native_engineering', {'plan': {'schema_version': 1, 'commands': [
        {'operation': 'configure_project', 'source_name': 'P.prj', 'options': {'program_capacity': 64000}}]}}),
    ('execute_native_engineering', {'plan': {'schema_version': 1, 'commands': [
        {'operation': 'edit_fbd_graph', 'caller': 'P.Program.pou', 'actions': [{'action': 'add_terminal', 'symbol': 'X'}]}]}}),
    ('execute_native_engineering', {'plan': {'schema_version': 1, 'commands': [
        {'operation': 'compile'}, {'operation': 'show_assignments', 'queries': [{'scope': 'local', 'source': 'P', 'name': 'X'}]}]}}),
    ('create_fbd_candidate', {'operation': 'create_global_labels', 'source_name': 'G', 'declarations': [
        {'name': 'X', 'data_type': 'INT', 'member_devices': {'Value': {'device': 'D1', 'unexpected': True}}}]}),
    ('create_fbd_candidate', {'operation': 'configure_project', 'source_name': 'P.prj', 'options': {'system_variable_ranges': {'D': [20, 10]}}}),
    ('create_st_candidate', {'operation': 'import', 'program': 'ST.Program.pou', 'data_base64': 'eA==', 'st_text': ';'}),
    ('create_st_candidate', {'operation': 'edit_st_program', 'source_name': 'ST', 'st_text': ';', 'declarations': [
        {'name': 'X', 'data_type': 'INT', 'class_name': 'VAR_INPUT'}]}),
    ('create_fbd_candidate', {'operation': 'generate', 'model': {'unsupported_number': float('nan')}}),
    ('create_fbd_candidate', {'operation': 'create_structure', 'source_name': 'NewType', 'members': [
        {'name': 'X', 'data_type': 'INT', 'comment': '\ud800'}]}),
    ('read_native_device_assignments', {'queries': [{'source': 'P', 'name': 'X', 'scope': 'invalid'}]}),
    ('read_native_device_assignments', {'queries': [{'source': 'P', 'name': 'X\n'}]}),
    ('locate_native_code_steps', {'queries': [{'resource': 'MAIN', 'step': False}]}),
])
def test_engineering_nested_syntax_errors_precede_context_resolution(monkeypatch, tool, arguments):
    import agent_runtime.plc_tools as tools
    monkeypatch.setattr(tools, 'resolve_tool_context', lambda *a, **k: pytest.fail('Malformed input reached project context'))
    result = build_default_tool_runtime().invoke(ToolCall('invalid-nested', tool, arguments), build_tool_context({}))
    assert result.is_error and result.data['error']['code'] == 'INVALID_ARGUMENTS'


def test_engineering_schema_validation_matches_independent_json_schema_for_generated_fields():
    from hypothesis import given, settings, strategies as st
    from agent_runtime.plc_tools import _engineering_native_schema
    from agent_runtime.tool_schema import validate_schema

    schema = _engineering_native_schema()
    oracle = Draft202012Validator(schema)

    @settings(max_examples=30, deadline=None, derandomize=True)
    @given(x=st.integers(0, 4096), y=st.integers(0, 4096), invalid=st.sampled_from(['x', True, None, -1, 4097]),
           prefix=st.text(alphabet='abcXYZ012_', min_size=1, max_size=12))
    def check(x, y, invalid, prefix):
        command = {'operation': 'edit_fbd_graph', 'caller': 'Future.Program.pou', 'actions': [
            {'action': 'move_node', 'node': 'n_' + prefix, 'x': x, 'y': y}]}
        arguments = {'plan': {'schema_version': 1, 'commands': [command]}}
        oracle.validate(arguments)
        validate_schema(schema, arguments)
        command['actions'][0]['x'] = invalid
        assert list(oracle.iter_errors(arguments))
        with pytest.raises(ValueError):
            validate_schema(schema, arguments)
    check()


@pytest.mark.parametrize('value', [None, 'Long device comment', '', 0, False, [], {}])
def test_shared_schema_nullable_type_list_matches_json_schema(value):
    from agent_runtime.tool_schema import validate_schema
    rule = {'type': ['string', 'null'], 'minLength': 1}
    expected = Draft202012Validator(rule).is_valid(value)
    if expected:
        validate_schema(rule, value)
    else:
        with pytest.raises(ValueError):
            validate_schema(rule, value)


def test_engineering_logical_source_selectors_keep_suffixes_of_valid_long_owner_names():
    from agent_runtime.plc_tools import _validate_engineering_input
    from gxw.fb_authoring import _name

    owner = _name('Source_' + 'a' * 248, 65001)
    runtime = build_default_tool_runtime()
    for operation, source in [('edit_global_labels', owner + '.gh'),
                              ('remove_empty_program', owner + '.Program.pou')]:
        arguments = {'operation': operation, 'source_name': source}
        if operation == 'edit_global_labels':
            arguments['declarations'] = []
        schema = runtime.registry._tools['create_fbd_candidate'].parameters
        Draft202012Validator(schema).validate(arguments)
        _validate_engineering_input('create_fbd_candidate', schema, arguments)


@pytest.mark.parametrize('create', [False, True])
def test_background_st_candidate_routes_existing_author_and_keeps_shared_confirmation(monkeypatch, tmp_path, create):
    from agent_runtime.runtime import public_tool_result_data
    from application.fbd import unpack_candidate
    import application.fbd as fbd
    from application.native_st import validate_candidate
    from gxw.st_program import verify_background_st_program_save
    from tests.test_gxw_object_model import background_st_program_witness
    import gxw.offline_authoring as offline_authoring

    before, saved, command, observed = background_st_program_witness(create)
    calls = []

    def author(operation, baseline, supplied):
        assert operation == 'author' and baseline == before and supplied == command
        calls.append(operation)
        return verify_background_st_program_save(baseline, supplied, saved, observed)

    write_existing = offline_authoring.write_existing_definition
    def offline(baseline, supplied, **kwargs):
        assert baseline == before and supplied == command
        calls.append('offline')
        return write_existing(baseline, supplied, **kwargs)

    monkeypatch.setattr(offline_authoring, 'write_existing_definition', offline)
    monkeypatch.setattr(fbd, '_native_candidate', author if create else
        lambda *a, **kw: pytest.fail('existing ST edit must not call native authoring'))
    context = build_tool_context({'id': 'selected', 'plc_model': 'FX3U', 'target_mode': 'st'},
        version={'id': 'v0001', 'target_mode': 'st'}, native_snapshot=before)
    result = build_default_tool_runtime().invoke(ToolCall('st-background', 'create_st_candidate', command), context)
    assert not result.is_error, result.data
    assert result.data['status'] == 'confirmation_required' and calls == (['author'] if create else ['offline'])
    pending = result.data['data']['pending_action']
    assert pending['type'] == 'accept_st_candidate' and pending['base_version_id'] == 'v0001'
    assert 'version_id' not in result.data['data']
    payload = unpack_candidate(pending['_st_candidate'], tmp_path / 'candidate')
    artifacts = {key: (tmp_path / 'candidate' / value['path']).read_bytes() for key, value in payload['artifacts'].items()}
    source = validate_candidate(artifacts, plc_model='FX3U', program_name=payload['metadata']['program_name'], metadata=payload['metadata'])
    assert source['selected_program']['st_text'] == command['st_text']
    if create:
        assert artifacts['gxw'] == saved
    assert source['project']['selected_source'] == payload['metadata']['program_name']
    if not create:
        from gxw.models import GXWFormatError
        from gxw.st_program import project_context_snapshot
        from gxw.visible_engineering import source_projection
        current = project_context_snapshot(before)
        assert source['project']['globals'] == current['globals']
        assert source['project']['cpu'] == current['cpu'] == 'FX3U/FX3UC'
        assert source['project']['resources'] == current['resources']
        assert any(table.get('member_addresses') == 'unresolved' for table in current['globals'])
        # A generic read projection does not enlarge the visible write range.
        with pytest.raises(GXWFormatError, match='structure-array storage'):
            source_projection(artifacts['gxw'])
    assert payload['metadata']['validation']['native_save'] == ('passed' if create else 'not_run')
    assert payload['metadata']['validation']['gx_compile'] == 'not_run'
    assert '_st_candidate' not in public_tool_result_data(result)['data']['pending_action']


@pytest.mark.parametrize('create', [False, True])
def test_background_st_candidate_failure_does_not_create_an_approval_or_change_bound_source(monkeypatch, create):
    import application.fbd as fbd
    import gxw.offline_authoring as offline_authoring
    from tests.test_gxw_object_model import background_st_program_witness

    before, _, command, _ = background_st_program_witness(create)
    context = build_tool_context({'id': 'selected', 'plc_model': 'FX3U', 'target_mode': 'st'},
        version={'id': 'v0001', 'target_mode': 'st'}, native_snapshot=before)
    original = copy.deepcopy(command)

    def rejected(operation, baseline, supplied):
        assert operation == 'author' and baseline == before and supplied == original
        raise ValueError('Native save readback did not match the requested source')

    def rejected_offline(baseline, supplied, **kwargs):
        assert baseline == before and supplied == original
        raise ValueError('Offline save readback did not match the requested source')

    monkeypatch.setattr(offline_authoring, 'write_existing_definition', rejected_offline)
    monkeypatch.setattr(fbd, '_native_candidate', rejected if create else
        lambda *a, **kw: pytest.fail('existing ST edit must not call native authoring'))
    result = build_default_tool_runtime().invoke(ToolCall('st-rejected', 'create_st_candidate', command), context)
    assert result.is_error and result.data['error']['code'] == 'TOOL_FAILED'
    assert not {'pending_action', 'proposal_id', 'version_id'} & result.data.keys()
    assert context.version_id == 'v0001' and context.native_snapshot == before and command == original
@pytest.mark.parametrize('command', [
    {'operation':'delete_label','scope':'program','source_name':'Main','name':'Unused'},
    {'operation':'edit_fbd_graph','caller':'Main','actions':[
        {'action':'add_terminal','network':0,'template':'input','symbol':'FutureFlag','x':3,'y':7}]},
])
def test_live_only_delete_and_input_creation_have_strict_early_schemas(command):
    from agent_runtime.plc_tools import _engineering_native_schema, _validate_engineering_input
    schema = _engineering_native_schema()
    arguments = {'plan':{'schema_version':1,'mode':'live','session_action':'start','commands':[copy.deepcopy(command)]}}
    Draft202012Validator(schema).validate(arguments)
    _validate_engineering_input('execute_native_engineering',schema,arguments)
    legacy = {'plan':{'schema_version':1,'commands':[copy.deepcopy(command)]}}
    assert list(Draft202012Validator(schema).iter_errors(legacy))
    with pytest.raises(ValueError):
        _validate_engineering_input('execute_native_engineering',schema,legacy)
    arguments['plan']['commands'][0]['unknown_native_address'] = 123
    with pytest.raises(ValueError):
        _validate_engineering_input('execute_native_engineering',schema,arguments)


@pytest.mark.parametrize('command', [
    dict(operation='create_structure', source_name='NewType', members=[dict(name='Flag', data_type='BOOL')]),
    dict(operation='edit_fbd_graph', caller='Main', actions=[
        dict(action='move_node', network=1, node='b1:n0', x=3, y=6)]),
    dict(operation='edit_fbd_graph', caller='Main', actions=[
        dict(action='add_wire', network=0, id='NewWire', **{'from': dict(wire='b0:w0', point=[5,3]),
             'to': dict(node='b0:n1', port='IN')}, via=[])]),
])
def test_live_unsupported_structure_and_coordinate_forms_fail_before_native_context(command):
    from application.live_engineering import normalize_live_plan
    from agent_runtime.plc_tools import _engineering_native_schema, _validate_engineering_input
    plan = dict(schema_version=1, mode='live', commands=[command])
    assert list(Draft202012Validator(_engineering_native_schema()).iter_errors({'plan': plan}))
    with pytest.raises(ValueError):
        _validate_engineering_input('execute_native_engineering', _engineering_native_schema(), {'plan': plan})
    with pytest.raises(ValueError):
        normalize_live_plan(plan, plc_model='FX3U')


@pytest.mark.parametrize('project_mode', ['fbd', 'st'])
def test_saved_st_generation_context_uses_exact_version_program_and_core_project_context(monkeypatch, project_mode):
    from gxw.st_program import project_context_snapshot
    from tests.test_gxw_object_model import st_program_semantic_witness
    import agent_runtime.plc_tools as plc_tools

    _, raw, text = st_program_semantic_witness(False)
    logical = 'OwnedProgram_C2.Program.pou'
    original = project_context_snapshot(raw, logical)
    monkeypatch.setattr(plc_tools, '_get_fbd_catalog', lambda *_: pytest.fail('The saved ST version must not enter FBD generation'))
    context = build_tool_context({'id': 'current-st', 'plc_model': 'FX3U', 'target_mode': project_mode},
        version={'id': 'v0003', 'target_mode': 'st', 'program_name': logical}, native_snapshot=raw, st_text=text)
    adapter = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context))
    result = adapter.call_tool(
        'get_generation_context', {'execution_mode': 'background', 'user_requirement': 'Update the existing ST body'}, 'saved-st-context')
    assert not result.is_error, _mcp_body(result)
    data, pages = _finish_mcp_read(adapter, result)
    assert data['target_mode'] == 'st' and data['execution_mode'] == 'background'
    assert data['current_version_id'] == 'v0003'
    current = data['current_context']
    selected = current['pous'][int(data['selected_program_ref'].rsplit('/', 1)[1])]
    assert current['origin'] == 'supplied_saved_project' and current['selected_source'] == logical
    assert selected == next(row for row in current['pous'] if row['program'] == logical)
    assert selected['name'] == 'OwnedProgram_C2' and selected['kind'] == 'program' and selected['language'] == 'st'
    assert selected['st_text'] == text and selected['task_bindings'] == [{'resource': 'MAIN', 'task': 'TASK_MATRIX', 'line': 2}]
    assert current['tasks'] == original['tasks'] and current['resources'] == original['resources']
    assert current['configuration'] == original['configuration']
    assert [row['program'] for row in current['pous']] == [row['program'] for row in original['pous']]
    assert any(row['language'] == 'fbd' and row['model']['nodes'] for row in current['pous'])
    function_block = next(row for row in current['pous'] if row['kind'] == 'function_block' and row.get('interface'))
    assert function_block['declarations'] and function_block['interface']['inputs']
    for before, after in zip(original['globals'], current['globals'], strict=True):
        assert (after['name'], after['source']) == (before['name'], before['source'])
        for source, declaration in zip(before['declarations'], after['declarations'], strict=True):
            for key in ('name', 'data_type', 'class_name', 'kind', 'initial_value', 'comment', 'device', 'iec_address'):
                assert declaration[key] == source[key]
    assert current['structures'] and current['structures'][0]['members']
    contract = data['output_contract']
    assert contract['tool'] == 'create_st_candidate' and contract['operation'] == 'edit_st_program'
    assert contract['source_name'] == logical and contract['editable_fields'] == ['st_text', 'declarations']
    Draft202012Validator(build_default_tool_runtime().registry._tools['create_st_candidate'].parameters).validate(
        {'operation': contract['operation'], 'source_name': contract['source_name'], 'st_text': ';'})
    wire = json.dumps(data)
    for key in ('storage', '_configuration_storage', 'base64', 'source_offset', 'definition_offset',
                'node_offset', 'record_id', 'type_code', 'class_code', 'unknown_record_count'):
        assert '"' + key + '":' not in wire
    assert context.native_snapshot == raw and context.st_text == text


@pytest.mark.parametrize('damage', ['missing-program', 'owner-alias', 'other-language', 'function-block', 'foreign-text', 'cpu'])
def test_saved_st_generation_context_never_substitutes_another_program_or_template(damage):
    from gxw.st_program import project_context_snapshot
    from tests.test_gxw_object_model import st_program_semantic_witness

    _, raw, text = st_program_semantic_witness(False)
    logical = 'OwnedProgram_C2.Program.pou'
    if damage == 'missing-program':
        logical = 'Missing.Program.pou'
    elif damage == 'owner-alias':
        logical = 'OwnedProgram_C2'
    elif damage in ('other-language', 'function-block'):
        snapshot = project_context_snapshot(raw)
        selected = next(row for row in snapshot['pous'] if row['language'] == 'fbd' and row['kind'] == 'program') if damage == 'other-language' else next(
            row for row in snapshot['pous'] if row['kind'] == 'function_block')
        logical = selected['program']
    context = build_tool_context({'id': 'saved-st', 'plc_model': 'FX3U', 'target_mode': 'fbd'},
        version={'id': 'v0003', 'target_mode': 'st', 'program_name': logical,
                 'plc_model': 'L02' if damage == 'cpu' else 'FX3U'}, native_snapshot=raw,
        st_text='Y0 := FALSE;' if damage == 'foreign-text' else text)
    result = build_default_tool_runtime().invoke(ToolCall('wrong-st-context', 'get_generation_context',
        {'execution_mode': 'background'}), context)
    assert result.is_error and result.data['error']['code'] == 'TOOL_FAILED'
    assert 'current_context' not in result.data and 'output_contract' not in result.data


def test_saved_st_generation_context_filters_native_storage_before_raw_and_model_outputs(monkeypatch):
    import gxw.st_program as st_program
    from tests.test_gxw_object_model import st_program_semantic_witness

    _, raw, text = st_program_semantic_witness(False)
    logical, private = 'OwnedProgram_C2.Program.pou', 'never-expose-native-storage'
    snapshot = st_program.project_context_snapshot(raw, logical)
    declaration = snapshot['globals'][0]['declarations'][0]
    declaration.update(storage={'opaque': private}, raw_fields=[private], vendor_layout=private,
        bytes_base64=private, native_bytes=private.encode(), hwnd=12345678,
        comment='Source note from D:/private-lab/current/input.gxw')
    snapshot['_configuration_storage'] = {'parameter_source_base64': private}
    monkeypatch.setattr(st_program, 'project_context_snapshot', lambda supplied, selected:
        copy.deepcopy(snapshot) if (supplied, selected) == (raw, logical) else pytest.fail('Wrong source binding'))
    context = build_tool_context({'id': 'private-projection', 'plc_model': 'FX3U', 'target_mode': 'fbd'},
        version={'id': 'v0003', 'target_mode': 'st', 'program_name': logical}, native_snapshot=raw, st_text=text)
    runtime = build_default_tool_runtime()
    result = runtime.invoke(ToolCall('private-st-context', 'get_generation_context',
        {'execution_mode': 'background'}), context)
    assert not result.is_error, result.data
    for value in (result.data, json.loads(result.content)):
        wire = json.dumps(value)
        assert private not in wire and 'D:/private-lab' not in wire and '12345678' not in wire
        document = _complete_runtime_read(runtime, ToolCall('private-read', 'get_generation_context',
            {'execution_mode': 'background'}), context)
        projected = document['current_context']['globals'][0]['declarations'][0]
        assert projected['name'] == declaration['name'] and projected['data_type'] == declaration['data_type']
        assert projected['device'] == declaration['device'] and '[private path]' in projected['comment']
    assert snapshot['globals'][0]['declarations'][0]['storage']['opaque'] == private


def test_text_only_saved_st_generation_context_uses_its_version_mode_without_requiring_native_binding():
    context = build_tool_context({'id': 'text-st-version', 'plc_model': 'FX3U', 'target_mode': 'fbd'},
        version={'id': 'v0003', 'target_mode': 'st'}, st_text='Y0 := X0;')
    data = _complete_runtime_read(build_default_tool_runtime(), ToolCall('text-st-context', 'get_generation_context',
        {'execution_mode': 'background'}), context)
    assert data['target_mode'] == 'st' and data['output_contract']['format'] == 'st_body_json'
    assert 'current_context' not in data and 'selected_program' not in data


@pytest.mark.parametrize('status,pending,action', [
    ('ready', 0, 'continue'), ('running', 1, 'query'), ('busy', 0, 'query'),
    ('pause_requested', 2, 'query'), ('unconfirmed', 1, 'rebind'), ('failed', 0, 'rebind'),
    ('paused', 2, 'resume'), ('paused', 0, 'continue'),
])
def test_live_generation_context_routes_current_session_state_without_replaying(monkeypatch, status, pending, action):
    native = {'mode': 'live', 'status': status, 'session_id': 'a' * 32, 'revision': 4,
              'pending_commands': pending, 'current_context': {'origin': 'active_native_project'}}
    context = build_tool_context({'id': 'live-project', 'plc_model': 'FX3U', 'target_mode': 'fbd'},
        version={'id': 'v0003', 'target_mode': 'st'}, native_session_status=lambda project, session: copy.deepcopy(native))
    provider = StaticToolContextProvider(context)
    monkeypatch.setattr(provider, 'get_context', lambda: context)
    adapter = MCPToolAdapter(build_default_tool_runtime(), provider)
    result = adapter.call_tool(
        'get_generation_context', {}, 'live-state-context')
    assert not result.is_error, _mcp_body(result)
    data, pages = _finish_mcp_read(adapter, result)
    assert data['target_mode'] == 'st' and data['current_context'] == native['current_context']
    contract = data['output_contract']
    assert contract['session_id'] == native['session_id']
    if action == 'query':
        assert contract == {'tool': 'get_native_engineering_session', 'session_id': native['session_id']}
    else:
        assert contract['tool'] == 'execute_native_engineering'
        assert contract['controls']['expected_revision'] == native['revision']
        assert contract['session_action'] == ('rebind' if action == 'rebind' else 'continue')
        assert contract['controls'].get('resume_pending', False) is (action == 'resume')
        if action in ('resume', 'rebind'):
            assert contract['commands'] == []
    assert native['status'] == status and native['pending_commands'] == pending



# Paged read protocol: exercise the actual public envelope, not only a builder.
def _complete_mcp_read(adapter, name, arguments, call_id='paged'):
    from application.read_delivery import assemble_read_pages, serialized_size
    pages = []
    while True:
        result = adapter.call_tool(name, arguments, f'{call_id}-{len(pages)}')
        page = json.loads(result.content[0].text)
        assert not result.is_error, page
        assert result.structured_content is None
        wire_size = serialized_size(result.model_dump(by_alias=True, exclude_none=True))
        assert wire_size['estimated_tokens'] <= 6000 and wire_size['utf8_bytes'] <= 32768
        size = serialized_size(page)
        assert size['estimated_tokens'] <= 6000 and size['utf8_bytes'] <= 32768
        pages.append(page)
        continuation = page['data']['delivery']['next']
        if continuation is None:
            return assemble_read_pages(pages), pages
        assert not page['data']['delivery']['complete']
        name, arguments = continuation['tool'], continuation['arguments']


from hypothesis import given, settings, strategies as st


@given(st.lists(st.text(alphabet='abc中文123', min_size=5, max_size=150), min_size=1, max_size=35))
@settings(max_examples=35, deadline=None)
def test_paged_read_roundtrip_preserves_complete_objects_and_limits(values):
    from application.read_delivery import ReadDeliveryStore, assemble_read_pages, serialized_size
    document = {'knowledge': [{'facts': [{'id': str(i), 'dimension': 'effects.manual',
        'value': value, 'scope': {'languages': ['st']}, 'depends_on': []}]} for i, value in enumerate(values)],
        'source': '完整源码保持不变', 'schema': {'properties': {'_user_key': {'type': 'boolean'}}}}
    store = ReadDeliveryStore(max_tokens=1000, max_bytes=4000)
    page = store.prepare({'ok': True, 'tool': 'get_generation_context', 'data': document}, {}, ('v1',))
    pages = [page]
    while page['data']['delivery']['next']:
        page = store.resume('get_generation_context', page['data']['delivery']['next']['arguments'], ('v1',))
        pages.append(page)
    assert assemble_read_pages(pages) == document
    for page in pages:
        size = serialized_size(page)
        assert size['estimated_tokens'] <= 1000 and size['utf8_bytes'] <= 4000
    objects = [identity for page in pages for identity in page['data']['objects']]
    assert len(objects) == len(set(objects))


@pytest.mark.parametrize('changed', ['query', 'cpu', 'project', 'version', 'facts'])
def test_read_cursor_rejects_changed_request_and_bindings(changed):
    from application.read_delivery import ReadDeliveryStore
    from agent_runtime.tool_errors import ToolPreconditionError
    store = ReadDeliveryStore(max_tokens=1200, max_bytes=4800)
    binding = ('FX3U', 'p1', 'v1', 'facts-v1')
    arguments = {'query': 'ANDP ANDF'}
    page = store.prepare({'ok': True, 'tool': 'search_plc_manual',
        'data': {'facts': [{'id': str(i), 'value': 'x' * 500} for i in range(20)]}}, arguments, binding)
    next_args = page['data']['delivery']['next']['arguments']
    if changed == 'query':
        next_args = {**next_args, 'query': 'MOV'}
    else:
        binding = tuple(value + '-changed' if index == ['cpu', 'project', 'version', 'facts'].index(changed)
                        else value for index, value in enumerate(binding))
    with pytest.raises(ToolPreconditionError, match='游标'):
        store.resume('search_plc_manual', next_args, binding)


def test_paged_read_rejects_indivisible_source_instead_of_truncating():
    from application.read_delivery import ReadDeliveryStore
    from agent_runtime.tool_errors import ToolPreconditionError
    source = {'source_name': 'MAIN', 'st_text': 'IF X0 THEN Y0 := TRUE; END_IF;\n' * 2000}
    with pytest.raises(ToolPreconditionError) as caught:
        ReadDeliveryStore().prepare({'ok': True, 'tool': 'get_generation_context',
            'data': {'st_sources': [source]}}, {}, ('v1',))
    assert caught.value.payload()['code'] == 'READ_OBJECT_TOO_LARGE'
    assert source['st_text'].endswith('END_IF;\n')


def test_paged_schema_keeps_its_own_objects_and_merge_properties():
    from application.read_delivery import ReadDeliveryStore, assemble_read_pages
    schema = {'$ref': '#/objects/user_type', 'objects': {'user_type': {'type': 'string'}},
              'properties': {'$merge': {'type': 'number'}}}
    document = {'input_schema': schema, 'schema': {'$ref': '#/objects/local_schema'}}
    page = ReadDeliveryStore().prepare({'ok': True, 'tool': 'get_generation_context',
        'data': document}, {}, ('v1',))
    assert assemble_read_pages([page]) == document


@pytest.mark.parametrize('top_k', [1, 3, 8])
def test_manual_explicit_targets_survive_top_k_in_actual_public_return(top_k):
    from integrations.web.responses import AgentToolResult
    context = build_tool_context({'id': 'instruction-delivery', 'plc_model': 'FX3U'})
    adapter = MCPToolAdapter(build_default_tool_runtime(), StaticToolContextProvider(context))
    query = 'FX3U LDP ANDP ANDF pulse contact operation previous scan device state ANDF S101 SET RST same scan ST equivalent'
    document, pages = _complete_mcp_read(adapter, 'search_plc_manual', {'query': query, 'top_k': top_k})
    targets = {row['target']: row for row in document['coverage']['targets']}
    assert {'LDP', 'ANDP', 'ANDF', 'SET', 'RST'} <= targets.keys()
    for target in ('LDP', 'ANDP', 'ANDF', 'SET', 'RST'):
        assert targets[target]['corpus'] == 'present'
        assert targets[target]['reason'] != 'exact_fact_not_found'
        assert {'effects', 'execution', 'st_call'} <= {row['aspect'] for row in targets[target]['aspects']}
    assert len([row for row in document['results'] if row.get('fact_target') == 'LDP']) == 1
    assert all(item['delivery'] == ('delivered' if item['corpus'] == 'present' else 'not_available')
               for item in targets.values())
    assert pages[-1]['data']['delivery']['coverage']['delivery'] == 'delivered'
    for i, page in enumerate(pages):
        AgentToolResult.model_validate({'name': 'search_plc_manual', 'call_id': str(i),
            'is_error': False, 'content': json.dumps(page), 'data': page})


def test_large_target_coverage_is_paged_without_repeating_it_in_each_header():
    from application.read_delivery import ReadDeliveryStore, assemble_read_pages, _fits
    store = ReadDeliveryStore()
    targets = [{'kind': 'instruction', 'target': 'OP' + str(i), 'corpus': 'present',
                'knowledge': 'incomplete', 'delivery': 'delivered'} for i in range(1000)]
    pages = [store.prepare({'ok': True, 'tool': 'get_generation_context',
        'data': {'knowledge_coverage': {'targets': targets}}}, {}, ('version',))]
    while not pages[-1]['data']['delivery']['complete']:
        followup = pages[-1]['data']['delivery']['next']
        pages.append(store.resume(followup['tool'], followup['arguments'], ('version',)))
    assert len(pages) > 1
    assert all(_fits(page, 6000, 32768) for page in pages)
    assert assemble_read_pages(pages)['knowledge_coverage']['targets'] == targets


def test_paged_large_models_split_networks_but_preserve_interface_lists():
    from application.read_delivery import ReadDeliveryStore, assemble_read_pages, _fits
    document = {'current_model': {'networks': [{'id': str(i), 'text': 'x' * 900} for i in range(30)]},
        'previous': {'nodes': [{'id': str(i), 'text': 'y' * 700} for i in range(30)]},
        'call_interface': {'FB': [{'name': 'port' + str(i), 'type': 'BOOL'} for i in range(140)]}}
    store = ReadDeliveryStore()
    pages = [store.prepare({'ok': True, 'tool': 'get_generation_context', 'data': document}, {}, ('v1',))]
    while pages[-1]['data']['delivery']['next']:
        follow = pages[-1]['data']['delivery']['next']
        pages.append(store.resume(follow['tool'], follow['arguments'], ('v1',)))
    assert len(pages) > 1
    assert assemble_read_pages(pages) == document
    objects = [v for page in pages for v in page['data']['objects'].values()]
    assert document['call_interface']['FB'] in objects
    assert all(_fits(page, 6000, 32768) for page in pages)
    assert all(len(json.dumps(page['data']['document'])) < 100 for page in pages)


def test_payload_delivery_version_cannot_bypass_public_projection():
    from agent_runtime.runtime import _model_tool_value
    payload = {'ok': True, 'tool': 'get_generation_context', 'data': {'delivery': {'version': 1},
        'objects': {'unit-1': {'api_key': 'secret', 'safe': 'visible'}}}}
    public = _model_tool_value('get_generation_context', payload)
    assert 'secret' not in json.dumps(public)
    assert public['data']['objects']['unit-1']['safe'] == 'visible'


def test_read_binding_does_not_retain_native_project_bytes():
    from application.read_delivery import read_binding
    context = build_tool_context({'id': 'p', 'plc_model': 'FX3U'}, native_snapshot=b'raw-gxw-secret' * 1000)
    binding = read_binding(context)
    def has_bytes(value):
        return isinstance(value, bytes) or isinstance(value, tuple) and any(has_bytes(v) for v in value)
    assert not has_bytes(binding)


def test_unpooled_http_read_contract_rejects_malformed_data_and_private_fields():
    from integrations.web.responses import validate_public_read_document
    from agent_runtime.tool_errors import ToolPreconditionError
    for tool, document in [('get_fbd_catalog', {'nodes':'not-a-list'}),
        ('get_generation_context', {'generation_input':{'api_key':'secret'}}),
        ('search_plc_manual', {'results':[{'staging_dir':'secret'}]})]:
        with pytest.raises(ToolPreconditionError,match='公开读取契约'):
            validate_public_read_document(tool,document)


def test_fbd_source_block_binding_survives_only_in_engineering_models():
    from agent_runtime.runtime import _model_tool_value
    model = {'schema_version': 2, 'networks': [{'source_block': 71, 'nodes': [], 'wires': []}]}
    value = {'ok': True, 'tool': 'get_generation_context', 'data': {
        'generation_input': {'previous': model},
        'debug_record': {'source_block': 'private manual layout'}}}
    public = _model_tool_value('get_generation_context', value)['data']
    assert public['generation_input']['previous'] == model
    assert 'source_block' not in public['debug_record']


def test_business_result_is_checked_before_debug_projection_and_pagination():
    registry = ToolRegistry()
    registry.register(ToolDefinition('get_generation_context', 'contract', {'type': 'object'},
        lambda *_: {'generation_input': {}, 'validation': 'invalid hidden business value'}))
    result = registry.call('get_generation_context', {}, build_tool_context({'id': 'p', 'plc_model': 'FX3U'}))
    assert result['ok'] is False
    assert result['error']['code'] == 'INVALID_TOOL_RESULT'


def test_managed_versionless_context_keeps_read_revision_for_continuations(tmp_path):
    from application.workbench import WorkbenchService
    from application.read_delivery import read_binding
    service = WorkbenchService(tmp_path/'workspace', tmp_path/'state')
    try:
        service.start()
        project = service.create_project(name='Revision', plc_model='FX3U')
        left = service.projects.tool_context(project['id'], requirement='model')
        right = service.projects.tool_context(project['id'], requirement='model')
        assert read_binding(left) == read_binding(right)
    finally:
        service.close()


def test_static_context_provider_keeps_session_revision_during_pagination():
    from integrations.mcp.context_provider import StaticToolContextProvider
    from application.read_delivery import read_binding
    provider = StaticToolContextProvider(build_tool_context({'id': 'memory', 'plc_model': 'FX3U'}))
    assert read_binding(provider.get_context()) == read_binding(provider.get_context())

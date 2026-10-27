"""Explicit service bridge tests; all HTTP runs against an isolated loopback stub."""

import io
import copy
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from integrations.mcp.service_client import ApplicationServiceClient, ServiceConnectionError, validate_service_url
from agent_runtime.plc_tools import SAFE_TOOL_NAMES
from agent_runtime.runtime import build_default_tool_runtime
from gxworks2.project_profiles import NATIVE_PROJECT_PROFILES


ROOT = Path(__file__).resolve().parents[1]


def test_connected_paging_preserves_engineering_keys_and_rejects_marker_only_payloads():
    from agent_runtime.plc_tools import ToolDefinition, ToolRegistry, build_tool_context
    from application.read_delivery import assemble_read_pages
    from integrations.mcp.tool_adapter import to_mcp_result
    from agent_runtime.runtime import InProcessToolRuntime
    from agent_runtime.messages import ToolCall
    arguments = {'password': 'D0', '_state': 'M0', 'token': 'D1'}
    payload = {'parameter_model': {'networks': [{'nodes': [{'arguments': arguments}]}]},
               'parameter_schema': {'properties': {key: {'type': 'string'} for key in arguments}},
               'staging_dir': 'private-metadata'}
    registry = ToolRegistry()
    registry.register(ToolDefinition('get_fbd_catalog', 'synthetic', {'type': 'object'}, lambda *_: payload))
    page = InProcessToolRuntime(registry).invoke(ToolCall('connected', 'get_fbd_catalog', {}),
        build_tool_context({'id': 'p1', 'plc_model': 'FX3U'})).data
    response = {'data': page, 'content': json.dumps(page), 'name': 'get_fbd_catalog',
                'call_id': 'connected', 'is_error': False}
    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(json.dumps(response).encode())
    client = ApplicationServiceClient('http://127.0.0.1:8765', 'isolated-agent-token', opener=Opener())
    result = client.call_tool('get_fbd_catalog', {}, 'connected', project_id='p1')
    wire = to_mcp_result(result)
    document = assemble_read_pages([json.loads(wire.content[0].text)])
    assert document['parameter_model']['networks'][0]['nodes'][0]['arguments'] == arguments
    assert document['parameter_schema'] == payload['parameter_schema']
    assert 'private-metadata' not in wire.content[0].text
    response['data'] = {'ok': True, 'tool': 'get_fbd_catalog', 'data': {
        'context_schema_version': 3, 'delivery': {'version': 1}, 'objects': {'bad': {'api_key': 'private'}}}}
    response['content'] = json.dumps(response['data'])
    with pytest.raises(ServiceConnectionError, match='paged read'):
        client.call_tool('get_fbd_catalog', {}, 'connected', project_id='p1')


@pytest.mark.parametrize('field', ['api_key', 'file_path', 'password'])
def test_connected_complete_read_does_not_trust_private_object_fields(field):
    from application.read_delivery import ReadDeliveryStore
    page = ReadDeliveryStore().prepare({'ok': True, 'tool': 'get_fbd_catalog',
        'data': {'parameter_model': {'networks': []}}}, {}, 'binding')
    root = page['data']['document']['$ref'].removeprefix('#/objects/')
    page['data']['objects'][root][field] = 'private-value'
    response = {'data': page, 'content': json.dumps(page), 'name': 'get_fbd_catalog',
                'call_id': 'connected', 'is_error': False}
    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(json.dumps(response).encode())
    client = ApplicationServiceClient('http://127.0.0.1:8765', 'isolated-agent-token', opener=Opener())
    with pytest.raises(ServiceConnectionError, match='paged read'):
        client.call_tool('get_fbd_catalog', {}, 'connected', project_id='p1')


def test_connected_multi_page_read_checks_later_objects_and_keeps_schema_references():
    from application.read_delivery import ReadDeliveryStore, assemble_read_pages
    from agent_runtime.runtime import _model_tool_value
    name = 'get_fbd_catalog'
    document = {'parameter_model': {'networks': [
        {'id': str(i), 'nodes': [{'id': str(i), 'arguments': {'api_key': 'D0', 'token': 'D1'},
                                'comment': 'public source ' * 25}]} for i in range(32)]},
        'parameter_schema': {'$defs': {'item': {'type': 'string'}},
                             '$ref': '#/objects/user-schema-ref'}}
    store = ReadDeliveryStore(max_tokens=1800, max_bytes=8000)
    first = store.prepare({'ok': True, 'tool': name, 'data': _model_tool_value(name, document)}, {}, 'binding')
    pages = [first]
    while pages[-1]['data']['delivery']['next']:
        nxt = pages[-1]['data']['delivery']['next']
        pages.append(store.resume(name, nxt['arguments'], 'binding'))
    assert len(pages) > 1
    client = ApplicationServiceClient('http://127.0.0.1:8765', 'isolated-agent-token')
    current = {'index': 0, 'tamper': False}
    def request(route, payload, **kwargs):
        page = copy.deepcopy(pages[current['index']])
        if current['tamper']:
            network = next(value for value in page['data']['objects'].values()
                           if isinstance(value, dict) and 'nodes' in value)
            network['file_path'] = 'private-value'
        return {'data': page, 'content': json.dumps(page), 'name': name,
                'call_id': payload['call_id'], 'is_error': False}
    client._request = request
    received = []
    arguments = {}
    for i in range(len(pages)):
        current['index'] = i
        received.append(client.call_tool(name, arguments, str(i), project_id='p1').data)
        continuation = pages[i]['data']['delivery']['next']
        arguments = continuation['arguments'] if continuation else {}
    assert assemble_read_pages(received) == document
    # A later page still passes ownership and envelope checks, but its object
    # metadata must be checked before it can be exposed by the MCP adapter.
    client = ApplicationServiceClient('http://127.0.0.1:8765', 'isolated-agent-token')
    client._request = request
    current.update(index=0, tamper=False)
    client.call_tool(name, {}, 'first', project_id='p1')
    current.update(index=1, tamper=True)
    with pytest.raises(ServiceConnectionError, match='paged read'):
        client.call_tool(name, pages[0]['data']['delivery']['next']['arguments'], 'tampered', project_id='p1')


@pytest.fixture
def service_server():
    calls = []
    mode = {"redirect": False, "error": False}
    schemas = build_default_tool_runtime().list_tools()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def response(self, code, data):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            calls.append((self.path, None, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != "Bearer isolated-agent-token":
                return self.response(403, {})
            if mode["redirect"]:
                self.send_response(302)
                self.send_header("Location", "/api/operator/proposals/approve")
                self.end_headers()
                return
            if self.path != "/api/agent/tools":
                return self.response(403, {})
            self.response(200, {"tools": schemas})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != "Bearer isolated-agent-token" or self.path != "/api/agent/tools/call":
                return self.response(403, {})
            self.response(200, {
                "data": {"ok": not mode["error"], "tool": body["name"], "data": {}, "status": "confirmation_required", "report": "x" * 21000, "pending_action": {"kind": "create_program_candidate", "_candidate_ir": {"private": True}, "_confirmed_spec": {"private": True}}},
                "content": "truncated", "is_error": mode["error"],
                "proposal_id": "proposal_saved_by_backend", "call_id": body["call_id"], "name": body["name"],
            })

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_address[1], calls, mode, schemas
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("url", [
    "https://localhost:8080", "http://example.org", "http://127.0.0.1:8080/api", "http://127.0.0.1:8080?x=1",
    "http://user:password@localhost", "http://localhost/#secret", "http://192.168.0.1", "file:///tmp/backend", "http://localhost:invalid",
])
def test_service_url_accepts_only_uncredentialed_loopback_origins(url):
    with pytest.raises(ValueError):
        validate_service_url(url)


def test_service_reuses_runtime_schema_and_public_complete_result(service_server):
    url, calls, mode, schemas = service_server
    client = ApplicationServiceClient(url, "isolated-agent-token")
    assert client.list_tools() == schemas
    result = client.call_tool("create_program_candidate", {"ladder": {}}, "unique-call", project_id="p1", version_id="v1")
    assert result.data["proposal_id"] == "proposal_saved_by_backend"
    assert result.data["status"] == "confirmation_required"
    assert len(result.data["report"]) == 21000
    assert "_candidate_ir" not in result.content
    assert "_confirmed_spec" not in result.content
    assert calls[-1][0] == "/api/agent/tools/call"
    assert calls[-1][1] == {"project_id": "p1", "version_id": "v1", "name": "create_program_candidate", "arguments": {"ladder": {}}, "call_id": "unique-call"}
    assert calls[-1][2] == "Bearer isolated-agent-token"
    mode["error"] = True
    assert client.call_tool("read_network", {}, "failure", project_id="p1").is_error


@pytest.mark.parametrize('identifiers', [{}, {'proposal_id': 'proposal_123'},
    {'proposal_id': 'proposal_123', 'execution_job_id': 'job_456', 'version_id': 'v0002'}])
def test_service_transport_preserves_only_actual_backend_operation_identifiers(identifiers):
    response = {'data': {'ok': True, 'data': {'counts': {'completed': 1}}}, 'is_error': False, **identifiers}

    class Opener:
        def open(self, request, timeout):
            assert request.full_url.endswith('/api/agent/tools/call')
            return io.BytesIO(json.dumps(response).encode())

    result = ApplicationServiceClient('http://127.0.0.1:8765', 'isolated-agent-token', opener=Opener()).call_tool(
        'get_operation_status', {'proposal_id': 'proposal_123'}, 'read-existing', project_id='p1')
    for key in ('proposal_id', 'execution_job_id', 'version_id'):
        if key in identifiers:
            assert result.data[key] == identifiers[key]
        else:
            assert key not in result.data
    assert result.call_id == 'read-existing'


@pytest.mark.parametrize('tool', ['execute_native_engineering', 'get_operation_status', 'get_native_engineering_session'])
def test_service_public_live_receipt_preserves_operational_layers_without_native_layout_or_paths(tool):
    receipt = dict(mode='live', session_id='a' * 32, project_id='p1', base_version_id='v1', version_id='v2',
        session_status='paused', status='completed', passed=True, revision=5, saved_revision=4, version_revision=4,
        version_matches_active_source=False, dirty=True, pending_commands=2,
        identity=dict(process_id=42, software_version='1.635.0.1', cpu_menu='FX3U/FX3UC',
            project_path=r'D:\private\active.gxw', window_handle=101),
        steps=[dict(index=3, operation='edit_global_labels', status='completed', revision=5,
            verification=dict(status='matched', counts={'rows': 2}, raw_native_records='private-native-layout',
                native_workspace={'slot': 56}, vtable='private-vtable', bytes_base64='private-cfb'))],
        compile=dict(status='failed', revision=5, compile_run_id='run-current', errors=1,
            diagnostics=dict(status='observed', rows=[dict(severity='Error', code='C5054',
                message=r'D:\private\diagnostic.txt failed')], raw_records='private-diagnostic-layout')),
        current_context=dict(tables=[dict(owner='Global1', rows=[dict(name='Ready', data_type='BOOL',
            device='M50', iec_address='%MX0.50')])], graphs=[dict(owner='Main', data_id=123,
                nodes=[dict(symbol='Ready', address=1100000, view_handle=111)])]),
        data_revision=4, stage_saves=[dict(revision=4, sequence=2, validation={'status': 'matched'})],
        _stage_context={'private': True}, _stage_compile={'private': True}, unknown_top_level='private-extra')
    response = {'data': {'ok': True, 'tool': tool, 'data':
        receipt if tool == 'get_native_engineering_session' else {'execution_result': receipt}}}
    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(json.dumps(response).encode())
    result = ApplicationServiceClient('http://127.0.0.1:8765', 'isolated-agent-token', opener=Opener()).call_tool(
        tool, {}, 'same-logical-call', project_id='p1')
    value = result.data['data']
    if tool != 'get_native_engineering_session':
        value = value['execution_result']
    assert value['mode'] == 'live' and value['session_id'] == 'a' * 32
    assert (value['revision'], value['saved_revision'], value['version_revision']) == (5, 4, 4)
    assert value['version_id'] == 'v2' and value['base_version_id'] == 'v1' and value['data_revision'] == 4
    assert value['dirty'] and value['pending_commands'] == 2 and value['session_status'] == 'paused'
    assert value['steps'][0]['verification'] == {'status': 'matched', 'counts': {'rows': 2}}
    assert value['compile']['diagnostics']['rows'][0]['code'] == 'C5054'
    assert value['compile']['diagnostics']['rows'][0]['message'] == '[private path] failed'
    assert value['current_context']['tables'][0]['rows'][0]['iec_address'] == '%MX0.50'
    assert value['current_context']['graphs'][0]['nodes'] == [{'symbol': 'Ready'}]
    assert value['stage_saves'][0]['validation'] == {'status': 'matched'}
    assert not any(marker in result.content for marker in ('private-native-layout', 'private-vtable', 'private-cfb',
        'private-diagnostic-layout', 'project_path', 'window_handle', 'view_handle', 'data_id', 'unknown_top_level',
        '_stage_context', '_stage_compile'))


@pytest.fixture(scope='module')
def native_live_tool_schema():
    return build_default_tool_runtime().registry._tools['execute_native_engineering'].parameters


@pytest.mark.parametrize('profile', NATIVE_PROJECT_PROFILES, ids=lambda profile: profile.cpu)
@pytest.mark.parametrize('language,codepage', [('zh-CN', 936), ('en', 936), ('ja', 932)])
def test_all_menu_language_contracts_have_local_service_normalization_and_receipt_parity(
        profile, language, codepage, native_live_tool_schema):
    """180 transport contracts only; synthetic not-run receipts grant no native evidence."""
    from copy import deepcopy
    from application.live_engineering import normalize_live_plan
    from agent_runtime.messages import ToolResult
    from agent_runtime.plc_tools import _validate_engineering_input
    from agent_runtime.runtime import public_tool_result_data
    from plc.target_support import TargetSupportRegistry

    arguments = dict(plan=dict(schema_version=1, mode='live', commands=[dict(operation='save')]))
    before = deepcopy(arguments)
    _validate_engineering_input('execute_native_engineering', native_live_tool_schema, arguments)
    if profile.program_supported:
        assert normalize_live_plan(arguments['plan'], plc_model=profile.cpu)['commands'] == [dict(operation='save')]
    else:
        with pytest.raises(ValueError, match='parameter-only'):
            normalize_live_plan(arguments['plan'], plc_model=profile.cpu)
    capabilities = TargetSupportRegistry().live_capabilities(profile.cpu,
        software_version='1.635.0.1', native_ui_language=language)
    expected_status = 'unverified' if profile.program_supported else 'unsupported'
    assert all(row['status'] == expected_status for row in capabilities.values())
    identity = dict(process_id=42, software_version='1.635.0.1', cpu_menu=profile.cpu,
        native_ui_language=language, project_codepage=codepage,
        window_handle=123, project_path=r'D:\private\isolated-native.gxw')
    receipt = dict(mode='live', session_id='a' * 32, project_id='p1', base_version_id='v1',
        status='not_started', passed=False, revision=0, saved_revision=0, version_revision=0,
        identity=identity, native_ui_language=language, project_codepage=codepage,
        operation_capabilities=capabilities, compile=dict(status='not_run', revision=None),
        current_context=dict(identity=deepcopy(identity), tables=[dict(owner='Global1',
            comment='声明 / Declaration / 宣言', source_binding=dict(identity=deepcopy(identity)))]))

    def backend(name, supplied):
        if name == 'get_native_engineering_session':
            assert supplied == dict(session_id='a' * 32)
            envelope = dict(ok=True, tool=name, data=receipt)
        else:
            assert name == 'execute_native_engineering' and supplied == arguments
            _validate_engineering_input(name, native_live_tool_schema, supplied)
            try:
                normalized = normalize_live_plan(supplied['plan'], plc_model=profile.cpu)
            except ValueError as error:
                envelope = dict(ok=False, tool=name, error=dict(code='INVALID_ARGUMENTS', message=str(error), next_actions=[]))
            else:
                envelope = dict(ok=True, tool=name, data=dict(plan=normalized, execution_result=receipt))
        return ToolResult('matrix-call', name, '', envelope, is_error=not envelope['ok'])

    class Opener:
        def open(self, request, timeout):
            payload = json.loads(request.data)
            assert payload['project_id'] == 'p1' and payload['version_id'] == 'v1'
            assert payload['call_id'] == 'matrix-call'
            result = backend(payload['name'], payload['arguments'])
            return io.BytesIO(json.dumps(dict(data=result.data, is_error=result.is_error), ensure_ascii=False).encode())

    client = ApplicationServiceClient('http://127.0.0.1:8765', 'isolated-agent-token', opener=Opener())
    for name, supplied in [('execute_native_engineering', arguments),
                           ('get_native_engineering_session', dict(session_id='a' * 32))]:
        local = backend(name, supplied)
        remote = client.call_tool(name, supplied, 'matrix-call', project_id='p1', version_id='v1')
        assert remote.data == public_tool_result_data(local) and remote.is_error == local.is_error
        if name == 'get_native_engineering_session':
            observed = remote.data['data']
            assert observed['identity']['cpu_menu'] == profile.cpu
            assert observed['identity']['native_ui_language'] == observed['native_ui_language'] == language
            assert observed['identity']['project_codepage'] == observed['project_codepage'] == codepage
            assert observed['operation_capabilities'] == capabilities
            assert observed['compile']['status'] == 'not_run' and observed['passed'] is False
            assert observed['current_context']['identity']['native_ui_language'] == language
            assert observed['current_context']['tables'][0]['comment'] == '声明 / Declaration / 宣言'
        assert 'window_handle' not in remote.content and 'project_path' not in remote.content
    assert arguments == before


def test_live_generation_output_contract_is_not_mistaken_for_an_operation_receipt():
    from agent_runtime.messages import ToolResult
    from agent_runtime.runtime import public_tool_result_data
    contract = dict(tool='execute_native_engineering', mode='live', session_id='a' * 32,
                    session_action='continue', expected_revision=5, schema_version=1)
    envelope = {'ok': True, 'data': {'execution_mode': 'live', 'output_contract': contract}}
    projected = public_tool_result_data(ToolResult('same-call', 'get_generation_context', '', envelope))
    assert projected['data']['output_contract'] == contract


@pytest.mark.parametrize('key,value', [('execution_job_id', r'D:\private\job.json'),
    ('version_id', {'unexpected': 'object'}), ('proposal_id', 'different-proposal')])
def test_service_transport_rejects_invalid_or_conflicting_operation_identifiers(key, value):
    response = {'data': {'ok': True, 'proposal_id': 'proposal_123'}, 'is_error': False, key: value}

    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(json.dumps(response).encode())

    client = ApplicationServiceClient('http://127.0.0.1:8765', 'isolated-agent-token', opener=Opener())
    with pytest.raises(ServiceConnectionError, match='identifier'):
        client.call_tool('get_operation_status', {'proposal_id': 'proposal_123'}, 'read-existing', project_id='p1')


def test_client_cannot_approve_or_follow_redirect_to_operator_route(service_server):
    url, calls, mode, schemas = service_server
    client = ApplicationServiceClient(url, "isolated-agent-token")
    with pytest.raises(ValueError):
        client._request("/api/operator/proposals/approve", {"approved": True})
    for name in ("approve_proposal", "write_plc", "force_device"):
        with pytest.raises(ValueError):
            client.call_tool(name, {}, "call", project_id="p1")
    assert calls == []
    mode["redirect"] = True
    with pytest.raises(ServiceConnectionError, match="redirect"):
        client.list_tools()
    assert [path for path, _, _ in calls] == ["/api/agent/tools"]


def test_token_environment_and_http_errors_do_not_expose_secrets(service_server, monkeypatch):
    url, calls, _, _ = service_server
    monkeypatch.setenv("ISOLATED_MCP_AGENT_TOKEN", "isolated-agent-token")
    client = ApplicationServiceClient.from_environment(url, "ISOLATED_MCP_AGENT_TOKEN")
    assert {schema["function"]["name"] for schema in client.list_tools()} == set(SAFE_TOOL_NAMES)
    with pytest.raises(ValueError):
        ApplicationServiceClient.from_environment(url, "DOES_NOT_EXIST_FOR_TEST")
    with pytest.raises(ServiceConnectionError) as raised:
        ApplicationServiceClient(url, "operator-secret-not-an-agent-token").list_tools()
    assert "operator-secret" not in str(raised.value)


def test_mcp_service_call_identity_is_stable_on_retry_and_unique_after_restart(service_server):
    pytest.importorskip("mcp")
    from integrations.mcp.service_client import ServiceMCPToolAdapter

    url, calls, _, _ = service_server
    first = ServiceMCPToolAdapter(ApplicationServiceClient(url, "isolated-agent-token"), "p1")
    second = ServiceMCPToolAdapter(ApplicationServiceClient(url, "isolated-agent-token"), "p1")
    for adapter in (first, first, second):
        result = adapter.call_tool("create_program_candidate", {}, "1")
        assert result.structured_content["proposal_id"] == "proposal_saved_by_backend"
        assert result.meta["gxworks"]["call_id"] == "1"
    ids = [body["call_id"] for _, body, _ in calls]
    assert ids[0] == ids[1]
    assert ids[0] != ids[2]
    assert ids[0] != "1"


def test_service_transport_failure_keeps_existing_call_identity_without_claiming_no_execution():
    from integrations.mcp.service_client import ServiceMCPToolAdapter

    class Client:
        def __init__(self):
            self.calls = []

        def call_tool(self, name, arguments, call_id, **context):
            self.calls.append(call_id)
            raise ServiceConnectionError('response unavailable after dispatch')

    client = Client()
    adapter = ServiceMCPToolAdapter(client, 'selected-project')
    for _ in range(2):
        result = adapter.call_tool('execute_native_engineering', {'plan': {}}, 'same-call')
        assert result.is_error
        assert 'receipt' in result.structured_content['error']['message']
        assert 'No approval was granted' not in result.structured_content['error']['message']
        assert not {'proposal_id', 'execution_job_id', 'version_id'} & result.structured_content.keys()
    assert client.calls[0] == client.calls[1]


@pytest.mark.parametrize('tool, arguments, expected_timeout', [
    ('validate_project', {}, 60.0),
    ('validate_project', {'native': False}, 60.0),
    ('validate_project', {'native': True}, None),
    ('read_native_device_assignments', {'queries': [{'source': 'PrgAxis1', 'name': 'Axis1_Stop'}]}, None),
    ('compare_native_compiled_code', {}, None),
    ('locate_native_code_steps', {'queries': [{'resource': 'Sample', 'step': 12562}]}, None),
    ('read_network', {'network_id': 'N0001'}, 60.0),
])
def test_native_check_transport_waits_for_existing_task_completion(tool, arguments, expected_timeout):
    class Response(io.BytesIO):
        pass
    class Opener:
        def open(self, request, timeout):
            assert timeout == expected_timeout
            payload = json.loads(request.data)
            assert payload['name'] == tool and payload['arguments'] == arguments and payload['call_id'] == 'bound-check'
            return Response(json.dumps({'data': {'ok': True, 'data': {}}, 'is_error': False}).encode())
    client = ApplicationServiceClient('http://127.0.0.1:8765', 'isolated-agent-token', opener=Opener())
    assert not client.call_tool(tool, arguments, 'bound-check', project_id='p1', version_id='v1').is_error


def test_service_stdio_process_needs_no_workspace_or_model_credentials(service_server, tmp_path):
    pytest.importorskip("mcp")
    import anyio
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    url, calls, _, _ = service_server
    wrapper = tmp_path / "headless_service_bridge.py"
    wrapper.write_text("""import importlib.abc
import sys
class BlockDesktop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PyQt6', 'PyQt5', 'qt_compat', 'main', 'api', 'model_provider', 'openai', 'pywinauto', 'gxworks2', 'simulator', 'config_manager', 'credential_store', 'windows_credentials'}:
            raise AssertionError('Unexpected execution dependency: ' + fullname)
sys.meta_path.insert(0, BlockDesktop())
from integrations.mcp.__main__ import main
raise SystemExit(main())
""", encoding="utf-8")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(wrapper), "--stdio", "--service-url", url, "--service-token-env", "ISOLATED_MCP_AGENT_TOKEN", "--project", "p1"],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "ISOLATED_MCP_AGENT_TOKEN": "isolated-agent-token", "PLC_AI_WORKSPACE_DIR": ""}, cwd=tmp_path,
    )

    async def exercise():
        with anyio.fail_after(20):
            with (tmp_path / "stderr.txt").open("w", encoding="utf-8") as errors:
                async with stdio_client(parameters, errlog=errors) as streams:
                    async with ClientSession(*streams, read_timeout_seconds=10) as client:
                        initialized = await client.initialize()
                        assert "cannot approve" in initialized.instructions
                        assert {tool.name for tool in (await client.list_tools()).tools} == set(SAFE_TOOL_NAMES)
                        result = await client.call_tool("create_program_candidate", {"ladder": {}})
                        assert result.structured_content["proposal_id"] == "proposal_saved_by_backend"
                        assert result.structured_content["status"] == "confirmation_required"
                        assert "_candidate_ir" not in result.model_dump_json()

    anyio.run(exercise)
    assert calls[-1][0] == "/api/agent/tools/call"
    assert not (tmp_path / "projects").exists()


def test_service_cli_rejects_ambiguous_or_unavailable_configuration(tmp_path):
    from mcp_test_support import isolated_mcp_command, isolated_mcp_environment

    env = isolated_mcp_environment(PYTHONPATH=str(ROOT / "src"), PLC_AI_WORKSPACE_DIR="")
    for extra in (
        ["--service-url", "http://localhost:8000"],
        ["--service-token-env", "ISOLATED_MCP_AGENT_TOKEN"],
        ["--service-url", "http://example.org", "--service-token-env", "ISOLATED_MCP_AGENT_TOKEN"],
    ):
        result = subprocess.run(isolated_mcp_command("--project", "p1", *extra), cwd=tmp_path, env=env, capture_output=True, timeout=15)
        assert result.returncode == 2
        assert result.stdout == b""
        assert result.stderr

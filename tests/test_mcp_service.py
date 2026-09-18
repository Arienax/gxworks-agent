"""Explicit service bridge tests; all HTTP runs against an isolated loopback stub."""

import io
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


ROOT = Path(__file__).resolve().parents[1]


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
                "data": {"ok": not mode["error"], "status": "confirmation_required", "report": "x" * 21000, "pending_action": {"kind": "create_program_candidate", "_candidate_ir": {"private": True}, "_confirmed_spec": {"private": True}}},
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

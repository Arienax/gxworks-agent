from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from integrations.dsh_runtime import DshRuntime, find_dsh_bin
from integrations.dsh_runtime import runtime as dsh_module
from integrations.dsh_runtime.vendor.deepseek_harness.errors import JsonRpcError, SdkProtocolError, TransportClosedError
from scripts.stage_dsh_runtime import EXE, LICENSES, NODE_LICENSE, RIPGREP, RIPGREP_LICENSE, stage_dsh_runtime


_TOOL = {
    "type": "function",
    "function": {
        "name": "gxworks_read_fixture",
        "description": "Read a fixture project value.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
}
_IMAGE_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAKklEQVR4nGMwSplGU8QwasGoBaMWjFowasGoBaMWjFowasGoBaMW"
    "DBULACZlsD22UPxwAAAAAElFTkSuQmCC"
)


@contextmanager
def _local_server(*, spill=False, host_read=False, model_hold=None, responses_tool_args=None):
    calls = {"tools": [], "invoke": [], "event": [], "batches": [], "model": []}
    tool_name = "get_generation_context" if host_read else "gxworks_read_fixture"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _json(self, result):
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path != "/tools" or self.headers.get("Authorization") != "Bearer bridge-secret":
                self.send_error(403)
                return
            calls["tools"].append(True)
            self._json({"tools": [{**_TOOL, "function": {**_TOOL["function"], "name": tool_name}}]})

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/v1/chat/completions":
                calls["model"].append((data, dict(self.headers.items())))
                if model_hold is not None:
                    entered, release = model_hold
                    entered.set()
                    release.wait(10)
                    return
                self._model(data)
                return
            if self.path == "/v1/responses":
                calls["model"].append((data, dict(self.headers.items())))
                self._responses(data)
                return
            if self.headers.get("Authorization") != "Bearer bridge-secret":
                self.send_error(403)
                return
            if self.path == "/invoke":
                calls["invoke"].append(data)
                self._json({"value": "marker" + " abcdef" * 24_000} if spill else {"value": 42})
            elif self.path == "/event":
                if "frames" in data:
                    calls["batches"].append(len(data["frames"]))
                    calls["event"].extend({"session_id": data["session_id"], "frame": frame}
                                          for frame in data["frames"])
                else:
                    calls["event"].append(data)
                self._json({"cancelled": False})
            else:
                self.send_error(404)

        def _model(self, data):
            tool = {
                "index": 0,
                "id": "call_gxworks_fixture",
                "type": "function",
                "function": {"name": tool_name, "arguments": '{"name":"D0"}'},
            }
            seen_tool = any(message["role"] == "tool" for message in data["messages"])
            if spill and seen_tool and len(data["messages"]) < 7:
                locator = re.search(r"stored at: (.+?\.txt)\.", data["messages"][-1]["content"])
                if locator:
                    tool = {
                        **tool,
                        "id": "call_read_fixture_spill",
                        "function": {"name": "read", "arguments": json.dumps(
                            {"file_path": locator.group(1), "offset": 0, "limit": 100})},
                    }
                    seen_tool = False
            if seen_tool:
                delta = {"content": "Fixture value is 42."}
            else:
                delta = {"tool_calls": [tool]}
            finish = "stop" if seen_tool else "tool_calls"
            common = {"id": "chatcmpl-gxworks-test", "object": "chat.completion.chunk",
                      "created": 1, "model": data["model"]}
            chunks = [
                {**common, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
                {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {**common, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
                {**common, "choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}},
            ]
            self._sse(chunks, done=True)

        def _responses(self, data):
            events = [{"type": "response.created", "response": {"id": "resp_fixture"}}]
            item = {"type": "message", "id": "msg_fixture", "role": "assistant",
                    "content": [{"type": "output_text", "text": "Responses reply.", "annotations": []}]}
            if responses_tool_args is not None and not any(
                value.get("type") == "function_call_output" for value in data["input"]
            ):
                args = json.dumps(responses_tool_args)
                item = {"type": "function_call", "id": "fc_fixture", "call_id": "call_fixture",
                        "name": "gxworks_read_fixture", "arguments": args}
                events.extend([
                    {"type": "response.output_item.added", "output_index": 0, "item": {**item, "arguments": ""}},
                    {"type": "response.function_call_arguments.delta", "output_index": 0,
                     "item_id": "fc_fixture", "delta": args},
                ])
            else:
                events.append({"type": "response.output_item.added", "output_index": 0, "item": item})
            self._sse([*events,
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": {
                    "id": "resp_fixture", "status": "completed", "output": [item],
                    "usage": {"input_tokens": 4, "output_tokens": 3, "total_tokens": 7},
                }},
            ])

        def _sse(self, chunks, *, done=False):
            body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks).encode()
            if done:
                body += b"data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", calls
        finally:
            server.shutdown()
            thread.join()


@pytest.fixture
def installed_dsh():
    if not find_dsh_bin():
        pytest.skip("DeepSeek Harness is unavailable")


def _runtime(url, home, cwd, **config):
    return DshRuntime(
        {"adapter": "openai_compatible", "base_url": url + "/v1", "api_key": "fixture-key",
         "model": "gxworks-local-test", **config},
        url, "bridge-secret", home, cwd,
    )


def test_dsh_binary_override_precedes_discovery(monkeypatch, tmp_path):
    binary = tmp_path / "dsh-fixture"
    binary.touch()
    monkeypatch.setenv("GXWORKS_DSH_BIN", str(binary))
    assert find_dsh_bin() == str(binary.resolve())
    binary.unlink()
    with pytest.raises(FileNotFoundError, match="GXWORKS_DSH_BIN"):
        find_dsh_bin()


def test_stages_official_runtime_sidecars_and_licenses(monkeypatch, tmp_path):
    source = tmp_path / "official-source"
    artifacts = source / "dist-exe"
    resources = artifacts / "win-x64"
    resources.mkdir(parents=True)
    for filename in (EXE, RIPGREP, NODE_LICENSE, RIPGREP_LICENSE):
        (artifacts / filename).write_text(filename)
    for filename in LICENSES:
        (source / filename).write_text(filename)
    (resources / "downloads.json").write_text('{"version":"0.2.1-rc.2"}')
    monkeypatch.setenv("GXWORKS_DSH_SOURCE", str(source))
    monkeypatch.delenv("GXWORKS_DSH_NODE_LICENSE", raising=False)
    destination = tmp_path / "dsh-runtime"
    assert stage_dsh_runtime(destination) == destination / EXE
    assert (destination / RIPGREP).is_file()
    assert (destination / NODE_LICENSE).is_file()
    assert (destination / RIPGREP_LICENSE).is_file()
    assert all((destination / filename).is_file() for filename in LICENSES)
    assert json.loads((destination / "runtime-source.json").read_text())["version"] == "0.2.1-rc.2"


@pytest.mark.skipif(dsh_module.os.name != "nt", reason="Windows runtime carrier")
def test_dsh_prefers_packaged_runtime(monkeypatch, tmp_path):
    root = tmp_path / "dsh-runtime"
    root.mkdir()
    executable = root / EXE
    executable.touch()
    monkeypatch.delenv("GXWORKS_DSH_BIN", raising=False)
    monkeypatch.setattr(dsh_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(dsh_module.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(dsh_module.shutil, "which", lambda _name: pytest.fail("PATH was consulted"))
    monkeypatch.setattr(dsh_module, "_windows_desktop_bin", lambda: pytest.fail("Registry was consulted"))
    assert find_dsh_bin() == str(executable)


def test_dsh_installed_sdk_starts_and_closes_without_model_call(installed_dsh, tmp_path):
    with _local_server() as (url, calls):
        runtime = _runtime(url, tmp_path / "home", tmp_path)
        try:
            runtime.start()
            assert calls["tools"] == [True]
        finally:
            runtime.close()
        assert calls["model"] == []


def test_dsh_installed_sdk_tool_stream_and_resume(installed_dsh, tmp_path):
    with _local_server() as (url, calls):
        home = tmp_path / "home"
        config = {
            "reasoning_effort": "high",
            "request_parameters": {
                "temperature": 0.1,
                "extra_body": {"custom_option": "fixture"},
                "extra_headers": {"X-GXWorks-Fixture": "yes"},
            },
        }
        runtime = _runtime(url, home, tmp_path, **config)
        try:
            result = runtime.run("Read fixture value D0.", session_id="gxworks-test")
        finally:
            runtime.close()
        assert result.final_response == "Fixture value is 42."
        assert len(calls["model"]) == 2
        model, headers = calls["model"][0]
        assert {tool["function"]["name"] for tool in model["tools"]} == {_TOOL["function"]["name"], "read"}
        assert model["custom_option"] == "fixture"
        assert model["temperature"] == 0.1
        assert model["reasoning_effort"] == "high"
        assert headers["x-gxworks-fixture"] == "yes"
        assert calls["invoke"] == [{
            "name": "gxworks_read_fixture", "arguments": {"name": "D0"},
            "call_id": "call_gxworks_fixture", "session_id": "gxworks-test",
        }]
        assistant_frames = [entry["frame"] for entry in calls["event"] if entry["frame"]["type"] != "request"]
        assert {frame["type"] for frame in assistant_frames} == {"start", "chunk", "end"}
        assert all(entry["session_id"] == "gxworks-test" for entry in calls["event"])
        assert calls["batches"]
        revisions = [frame["revision"] for frame in assistant_frames]
        assert revisions == sorted(set(revisions))
        assert any(entry["frame"].get("chunk", {}).get("type") == "usage" for entry in calls["event"])

        resumed = _runtime(url, home, tmp_path, **config)
        try:
            resumed.resume("gxworks-test")
            answer = resumed.run("Confirm the fixture value.", session_id="gxworks-test")
        finally:
            resumed.close()
        assert answer.final_response == "Fixture value is 42."
        assert len(calls["invoke"]) == 1
        assert any(
            message["role"] == "user" and "Confirm the fixture value." in str(message["content"])
            for message in calls["model"][-1][0]["messages"]
        )


@pytest.mark.skipif(dsh_module.os.name != "nt", reason="Windows runtime carrier")
def test_dsh_bundled_sdk_persists_project_session_on_workspace_drive(monkeypatch):
    runtime_root = Path(dsh_module.__file__).resolve().parents[3] / ".runtime"
    executable = runtime_root / "dsh-runtime" / EXE
    if not executable.is_file():
        pytest.skip("Build or stage the standalone Windows DSH SDK executable")
    monkeypatch.setenv("GXWORKS_DSH_BIN", str(executable))
    session_id = "session-gxworks-" + "a" * 12 + "-job_" + "b" * 32
    # The repository can be on a different drive from Windows' temp directory.
    # Start the independent SDK from that project drive to exercise native JSONL.
    with TemporaryDirectory(prefix="dsh-session-persistence-", dir=runtime_root) as root:
        design = Path(root) / "design"
        home = design / "state/dsh/home"
        workspace = design / "workspace"
        workspace.mkdir(parents=True)
        with _local_server() as (url, calls):
            first = _runtime(url, home, workspace)
            try:
                result = first.run("Read the saved project fixture.", session_id=session_id)
                assert result.final_response == "Fixture value is 42."
            finally:
                first.close()

            logs = list((home / "sessions").rglob("session.*.jsonl"))
            assert len(logs) == 1
            assert logs[0].parent.name == session_id
            assert "Read the saved project fixture." in logs[0].read_text(encoding="utf-8")

            resumed = _runtime(url, home, workspace)
            try:
                resumed.resume(session_id)
                answer = resumed.run("Continue the saved project.", session_id=session_id)
                assert answer.final_response == "Fixture value is 42."
            finally:
                resumed.close()
            user_messages = [message["content"] for message in calls["model"][-1][0]["messages"]
                             if message["role"] == "user"]
            assert user_messages[0] == "Read the saved project fixture."
            assert user_messages[-1] == "Continue the saved project."


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses"])
@pytest.mark.parametrize("config,expected_field,expected_limit", [
    ({}, None, None),
    ({"reasoning_effort": "high"}, None, None),
    ({"request_parameters": {"max_completion_tokens": 1200}}, "max_completion_tokens", 1200),
    ({"request_parameters": {"extra_body": {"max_output_tokens": 32768}}}, "max_tokens", 32768),
])
def test_dsh_output_cap_is_only_sent_when_configured(installed_dsh, tmp_path, api,
                                                     config, expected_field, expected_limit):
    with _local_server() as (url, calls):
        runtime = _runtime(url, tmp_path / "home", tmp_path, api=api, **config)
        try:
            result = runtime.run("Read the fixture value.", session_id="gxworks-output-cap")
        finally:
            runtime.close()

        assert result.final_response
        assert len(calls["model"]) == (2 if api == "openai-completions" else 1)
        if expected_field and api == "openai-responses":
            expected_field = "max_output_tokens"
        expected = {expected_field: expected_limit} if expected_field else {}
        for payload, _headers in calls["model"]:
            assert {key: payload[key] for key in (
                "max_tokens", "max_completion_tokens", "max_output_tokens"
            ) if key in payload} == expected


def test_dsh_responses_native_options(installed_dsh, tmp_path):
    with _local_server() as (url, calls):
        runtime = _runtime(url, tmp_path / "home", tmp_path, api="openai-responses",
                           reasoning_effort="high", request_parameters={
                               "max_completion_tokens": 1200,
                               "extra_body": {"custom_option": "fixture"},
                           })
        try:
            result = runtime.run("Reply with the fixture response.", session_id="gxworks-responses")
        finally:
            runtime.close()
        assert result.final_response == "Responses reply."
        (model, _headers), = calls["model"]
        assert model["reasoning"]["effort"] == "high"
        assert model["max_output_tokens"] == 1200
        assert model["custom_option"] == "fixture"
        assert {"max_completion_tokens", "max_tokens", "reasoning_effort", "messages"}.isdisjoint(model)


def test_dsh_responses_preserves_optional_tool_parameters(installed_dsh, tmp_path, monkeypatch):
    import sys

    schema = {"type": "object", "properties": {
        "name": {"type": "string"},
        "cursor": {"type": "string"},
        "filter": {"type": "object", "properties": {"prefix": {"type": "string"}, "limit": {"type": "integer"}},
                   "required": ["prefix"], "additionalProperties": False},
    }, "required": ["name"], "additionalProperties": False}
    fixture = {**_TOOL, "function": {**_TOOL["function"], "parameters": schema}}
    monkeypatch.setattr(sys.modules[__name__], "_TOOL", fixture)
    args = {"name": "D0", "filter": {"prefix": "D"}}
    with _local_server(responses_tool_args=args) as (url, calls):
        runtime = _runtime(url, tmp_path / "home", tmp_path, api="openai-responses")
        try:
            result = runtime.run("Read the fixture.", session_id="gxworks-optional-tool-fields")
        finally:
            runtime.close()
        assert result.final_response == "Responses reply."
        assert len(calls["model"]) == 2
        for payload, _headers in calls["model"]:
            tools = {tool["name"]: tool for tool in payload["tools"]}
            assert {name: tool["strict"] for name, tool in tools.items()} == {"gxworks_read_fixture": False, "read": False}
            assert tools["gxworks_read_fixture"]["parameters"] == schema
            assert tools["read"]["parameters"]["required"] == ["file_path"]
        assert [call["arguments"] for call in calls["invoke"]] == [args]


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses"])
def test_dsh_sdk_sends_encoded_image_to_model(installed_dsh, tmp_path, api):
    with _local_server() as (url, calls):
        runtime = _runtime(url, tmp_path / "home", tmp_path, api=api, supports_images=True)
        try:
            result = runtime.run([
                {"type": "text", "text": "Read the attached picture."},
                {"type": "image", "mimeType": "image/png", "data": _IMAGE_BASE64},
            ], session_id="gxworks-image")
        finally:
            runtime.close()

        assert result.final_response
        model, _headers = calls["model"][0]
        if api == "openai-responses":
            user = next(item for item in model["input"] if item.get("role") == "user")
            image = next(item for item in user["content"] if item["type"] == "input_image")
            image_url = image["image_url"]
        else:
            user = next(item for item in model["messages"] if item["role"] == "user")
            image = next(item for item in user["content"] if item["type"] == "image_url")
            image_url = image["image_url"]["url"]
        assert image_url == f"data:image/png;base64,{_IMAGE_BASE64}"


def test_dsh_spilled_result_is_retrievable(installed_dsh, tmp_path):
    with _local_server(spill=True) as (url, calls):
        runtime = _runtime(url, tmp_path / "home", tmp_path)
        try:
            result = runtime.run("Read and retrieve the spilled fixture value.", session_id="gxworks-spill")
        finally:
            runtime.close()
        assert result.final_response == "Fixture value is 42."
        assert len(calls["model"]) == 3
        messages = calls["model"][-1][0]["messages"]
        assert "Full formatted result stored at" in messages[-3]["content"]
        assert "marker abcdef" in messages[-1]["content"]
        assert "Continue reading at offset 100" in messages[-1]["content"]


def test_dsh_host_read_reaches_model_without_spilling(installed_dsh, tmp_path):
    with _local_server(spill=True, host_read=True) as (url, calls):
        runtime = _runtime(url, tmp_path / "home", tmp_path)
        try:
            runtime.run("Read the engineering context.", session_id="gxworks-host-read")
        finally:
            runtime.close()
        assert [call["name"] for call in calls["invoke"]] == ["get_generation_context"]
        assert len(calls["model"]) == 2
        content = calls["model"][-1][0]["messages"][-1]["content"]
        assert json.loads(content)["value"] == "marker" + " abcdef" * 24_000


def test_dsh_close_stops_a_running_turn(installed_dsh, tmp_path):
    entered, release = threading.Event(), threading.Event()
    with _local_server(model_hold=(entered, release)) as (url, calls):
        runtime = _runtime(url, tmp_path / "home", tmp_path)
        outcomes = []

        def run():
            try:
                outcomes.append(runtime.run("Wait for the local model.", session_id="gxworks-cancel"))
            except TransportClosedError as error:
                outcomes.append(error)

        runtime.start()
        process = runtime._harness.client._proc
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            assert entered.wait(5)
            runtime.close()
            thread.join(5)
            assert not thread.is_alive()
            assert process.poll() is not None
            assert outcomes
            assert calls["invoke"] == []
        finally:
            release.set()
            runtime.close()


def test_dsh_completed_turn_reports_shutdown_failure(installed_dsh, monkeypatch, tmp_path):
    with _local_server() as (url, _calls):
        runtime = _runtime(url, tmp_path / "home", tmp_path)
        try:
            result = runtime.run("Read the fixture.", session_id="gxworks-close-error")
            assert result.final_response == "Fixture value is 42."
            client = runtime._harness.client
            process = client._proc
            failed = JsonRpcError(-32000, "Fixture session write failed.")

            def reject_shutdown(method, *_args, **_kwargs):
                assert method == "shutdown"
                raise failed

            monkeypatch.setattr(client, "request", reject_shutdown)
            with pytest.raises(SdkProtocolError, match="durably close") as error:
                runtime.close()
            assert error.value.__cause__ is failed
            assert process.poll() is not None
        finally:
            runtime.close()

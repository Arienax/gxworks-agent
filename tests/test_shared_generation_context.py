"""API/MCP context parity without model calls or private workspace state."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import application.model_api as api
import knowledge.retriever as knowledge_retriever
from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
from application.generation_context import generation_user_input, public_generation_ladder
from application.confirmed_generation_context import CONFIRMED_GENERATION_REQUEST
from shared.context_policy import POLICY_NAMES, context_policy_scope


def _ladder():
    return {
        "device_comments": {"X0": "Start", "X3": "Permit", "Y0": "Motor"},
        "rungs": [{"rung_id": 1, "header_element": None,
                   "shared_inputs": [{"type": "NO", "address": "X3"}],
                   "branches": [{"branch_id": 1, "y_offset_level": 0,
                                 "inputs": [{"type": "parallel_block", "branches": [
                                     [{"type": "NO", "address": "X0"}],
                                     [{"type": "NO", "address": "Y0"}],
                                 ]}],
                                 "outputs": [{"type": "COIL", "address": "Y0"}]}]}],
    }


@pytest.mark.parametrize("policy", POLICY_NAMES)
@pytest.mark.parametrize("editing", [False, True])
def test_api_and_external_context_have_identical_generation_instructions(monkeypatch, policy, editing):
    calls = []

    def retrieve(query, **kwargs):
        calls.append((query, kwargs))
        return "Official timer evidence: T0 to T199 use the documented time base."

    monkeypatch.setattr(knowledge_retriever, "build_knowledge_context", retrieve)
    monkeypatch.setattr(api, "load_full_config", lambda: pytest.fail("Bound context must not read global config"))
    monkeypatch.setattr(api, "get_active_provider", lambda: pytest.fail("No model call is allowed"))
    requirement = "Use FX3U T1 with a timer preset K30; X0 starts Y0 and X3 permits running."
    spec = {"summary": "Confirmed timer requirement", "io_table": [
        {"address": "X0", "label": "Start"}, {"address": "X3", "label": "Permit"},
        {"address": "Y0", "label": "Motor"}], "parameters": [{"name": "delay", "value": "K30"}]}
    ladder = _ladder() if editing else None
    context = build_tool_context(
        {"id": "shared-context", "plc_model": "FX3U", "confirmed_spec": spec},
        version={"id": "v0001", "plc_model": "FX3U", "confirmed_spec_snapshot": spec} if editing else None,
        ladder=ladder,
    )
    model_request = generation_user_input(requirement, is_edit_mode=editing)
    with context_policy_scope(policy):
        messages, history, persist = api._prepare_api_call(
            model_request, "offline", "high", "ladder", plc_model="FX3U",
            is_edit_mode=editing, confirmed_spec=spec, current_version_json=ladder,
            conversation_history=[{"role": "assistant", "content": "Private old conversation"}],
        )
        api_calls = copy.deepcopy(calls)
        calls.clear()
        result = build_default_tool_registry().call("get_generation_context", {"user_requirement": requirement}, context)
    assert result["ok"], result
    data = result["data"]
    assert data["generation_instructions"] == messages[0]["content"]
    assert data["generation_request"] == (model_request if editing else CONFIRMED_GENERATION_REQUEST)
    assert calls == api_calls
    for _query, options in calls:
        assert options["plc_model"] == "FX3U"
        assert options["task_type"] == ("edit" if editing else "generate")
        assert options["top_k"] == 5
        assert options["token_budget"] == 12000
        assert options["char_budget"] == sys.maxsize
    assert data["current_version_id"] == ("v0001" if editing else None)
    assert "Private old conversation" not in json.dumps(data)
    assert persist is False and len(history) == (2 if editing else 1)
    if not editing:
        assert "Private old conversation" not in json.dumps(messages + history)
        assert history == [{"role": "user", "content": CONFIRMED_GENERATION_REQUEST}]
    assert context.ladder == ladder


@pytest.mark.parametrize("model,target_mode", [("FX3U", "ladder"), ("FX5U", "ladder"), ("FX5U", "st")])
def test_empty_arguments_use_bound_model_and_api_retrieval_failure_fallback(monkeypatch, model, target_mode, capsys):
    def unavailable(*args, **kwargs):
        raise RuntimeError("C:/private/index.sqlite api_key=must-not-be-disclosed")

    monkeypatch.setattr(knowledge_retriever, "build_knowledge_context", unavailable)
    context = build_tool_context({"id": "empty", "plc_model": model, "target_mode": target_mode})
    with context_policy_scope("legacy"):
        result = build_default_tool_registry().call("get_generation_context", {}, context)
    assert result["ok"]
    data = result["data"]
    assert data["plc_model"] == model
    assert data["confirmed_spec"] is None and data["has_confirmed_spec"] is False
    assert data["current_version_id"] is None
    assert '"special_m"' in data["generation_instructions"]
    assert "retrieved for the current request" not in data["generation_instructions"]
    assert "private/index" not in json.dumps(result)
    assert "must-not-be-disclosed" not in json.dumps(result)
    assert "must-not-be-disclosed" not in capsys.readouterr().err
    if model == "FX5U":
        assert '"model": "FX5U"' in data["generation_instructions"]


def test_public_context_projects_current_ladder_and_cleans_annotation_paths_and_credentials(monkeypatch):
    secret = "do-not-copy-private-state"
    private_path = "C:/Users/private-user/project/program.json"
    spec = {"summary": "Motor", "user_notes": "See " + private_path,
            "api_key": secret, "history": [{"content": secret}],
            "hardware_context": {"notes": "agent_token=hidden-value", "provider": secret}}
    ladder = _ladder()
    ladder.update(api_key=secret, messages=[{"content": secret}], source={"path": private_path})
    ladder["rungs"][0]["ui_state"] = {"token": secret}
    ladder["rungs"][0]["branches"][0]["inputs"][0]["private_path"] = private_path
    ladder["device_comments"]["Y0"] = "Motor " + private_path
    ladder["device_comments"]["api_key"] = secret
    context = build_tool_context({"id": "private", "plc_model": "FX3U", "confirmed_spec": spec,
                                  "workspace": private_path, "credentials": {"key": secret},
                                  "messages": [{"content": secret}]}, ladder=ladder)
    monkeypatch.setattr(knowledge_retriever, "build_knowledge_context", lambda *args, **kwargs:
                        "Official documentation https://example.test/manual.pdf; file " + private_path)
    result = build_default_tool_registry().call("get_generation_context", {
        "user_requirement": "X0 starts Y0. api_key=client-secret Bearer private-bearer",
    }, context)
    assert result["ok"], result
    wire = json.dumps(result, ensure_ascii=False)
    for private in (secret, private_path, "hidden-value", "client-secret", "private-bearer", "private-user"):
        assert private not in wire
    assert "https://example.test/manual.pdf" in wire
    assert "[private path]" in wire and "[private credential]" in wire
    assert '"ui_state"' not in wire and '"messages"' not in wire
    assert "X3" in wire and "parallel_block" in wire
    assert context.project["confirmed_spec"]["api_key"] == secret


def test_source_projection_retains_real_branch_conditions_and_output_operands():
    ladder = _ladder()
    ladder["rungs"][0]["branches"][0]["outputs"] += [
        {"type": "TIMER", "address": "T1", "value": "K30"},
        {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K1", "D0"]},
    ]
    assert public_generation_ladder(ladder) == ladder


@pytest.mark.parametrize("arguments", [{"user_requirement": 1}, {"user_requirement": "a" * 24001},
                                        {"plc_model": "FX5U"}, {"confirmed_spec": {}}])
def test_optional_requirement_does_not_open_server_owned_context(arguments):
    result = build_default_tool_registry().call("get_generation_context", arguments,
                                               build_tool_context({"id": "bound", "plc_model": "FX3U"}))
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENTS"


def test_context_can_run_in_a_process_with_provider_config_and_desktop_imports_blocked(tmp_path):
    script = "import importlib.abc, json, sys, types\nclass BlockPrivate(importlib.abc.MetaPathFinder):\n    def find_spec(self, fullname, path=None, target=None):\n        if fullname.split('.')[0] in {'api', 'model_provider', 'openai', 'config_manager', 'credential_store',\n                                     'PyQt5', 'PyQt6', 'qt_compat', 'main', 'pywinauto', 'win32com'}:\n            raise AssertionError('Unexpected dependency: ' + fullname)\nsys.meta_path.insert(0, BlockPrivate())\nsys.modules['knowledge.retriever'] = types.SimpleNamespace(build_knowledge_context=lambda *a, **k: '')\nfrom agent_runtime.plc_tools import build_default_tool_registry, build_tool_context\nresult = build_default_tool_registry().call('get_generation_context', {'user_requirement': 'FX3U T1 K30'},\n    build_tool_context({'id': 'isolated', 'plc_model': 'FX3U'}))\nassert result['ok'], result\nassert result['data']['generation_instructions']\nprint(json.dumps({'ok': True}))\n"
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                   "GXWORKS_CONTEXT_POLICY": "legacy", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"ok": True}

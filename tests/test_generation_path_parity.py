"""Same fixed model response through the API workflow and a real MCP client.

These are isolated software regressions, not live-model or GX Simulator tests.
Only model transport is replaced; parsing, ToolRuntime, IR, renderers, proposal
validation, persistence and readback are the application implementations.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="MCP dependencies are required for path parity")

import anyio
from mcp import Client

from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow
from plc.candidate_repair import GenerationValidationError
from application.workbench import WorkbenchService
from integrations.mcp.context_provider import SessionToolContextProvider
from integrations.mcp.server import create_server
from plc.ir import canonical_sha256, ir_to_ladder
from plc.validation import PLCJsonValidationError, validate_ladder_full
from storage.session import SessionStore
from agent_runtime.runtime import build_default_tool_runtime


PROGRAM_NAME = "PARITY_MAIN"
PROFILE = "generation_structural"
ARTIFACT_NAMES = {"json", "ir", "svg", "st_from_ir", "program_csv", "comment_csv"}


def _rung(identifier=1, *, condition="X0", target="Y0", output=None):
    return {
        "rung_id": identifier,
        "header_element": None,
        "shared_inputs": [],
        "branches": [{
            "branch_id": 1, "y_offset_level": 0,
            "inputs": [{"type": "NO", "address": condition}],
            "outputs": [copy.deepcopy(output or {"type": "COIL", "address": target})],
        }],
    }


def _ladder(*rungs):
    return {"device_comments": {"X0": "启动", "Y0": "运行"},
            "rungs": list(rungs) or [_rung()]}


def _spec(*, conflict=False):
    return {
        "summary": "合成路径一致性工程：按已确认输入生成程序。",
        "io_table": [{"address": "X0", "kind": "X", "label": "启动"},
                     {"address": "Y0", "kind": "Y", "label": "运行"}],
        "selected_approach": {
            "name": "MOV 方案" if conflict else "直接输出",
            "generation_contract": {"required_opcodes": ["MOV"], "enforce": True} if conflict else {},
        },
    }


def _project(tmp_path, *, conflict=False):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path / "unused-legacy")
    project = store.create_project("路径一致性", plc_model="FX3U")
    # Use the persisted canonical specification on both paths, not two subtly
    # different pre/post-canonicalization versions of the fixture.
    store.set_confirmed_spec(project["id"], _spec(conflict=conflict))
    return store, project["id"]


def _files(directory, manifest):
    return {name: (Path(directory) / filename).read_bytes() for name, filename in manifest.items()}


def _api(candidate, spec, directory, *, previous=None):
    original = copy.deepcopy(candidate)
    calls = []

    def stream(*args, **kwargs):
        calls.append((args, kwargs))
        return "", json.dumps(candidate, ensure_ascii=False)

    def forbidden_retry(*args, **kwargs):
        pytest.fail("A fixed accepted response must not trigger another model request")

    request = GenerationRequest(
        user_input="合成测试：生成或修改当前程序", model_name="unit-test-no-model",
        response_language="zh-CN", plc_model="FX3U", program_name=PROGRAM_NAME,
        revision=previous["revision"] + 1 if previous else 1,
        confirmed_context=spec, previous_ir=previous,
        previous_json=ir_to_ladder(previous) if previous else None,
    )
    workflow = GenerationWorkflow(request, directory, dependencies=GenerationDependencies(
        stream_response=stream, generate_json=forbidden_retry,
    ))
    try:
        result = workflow.run()
    finally:
        assert len(calls) == 1
        assert candidate == original
        if previous:
            assert calls[0][1]["current_version_json"] == ir_to_ladder(previous)
    artifacts = _files(directory, result["artifacts"])
    return {"metadata": result, "program": json.loads(artifacts["ir"]), "artifacts": artifacts}


def _mcp(candidate, store, project_id, monkeypatch):
    """Observe actual private runtime output and compile bytes before cleanup.

    No validation result is substituted. The MCP public response must still
    redact the private IR, and the standalone call must not save a version.
    """
    import plc.generation as plc_generation

    original = copy.deepcopy(candidate)
    runtime = build_default_tool_runtime()
    invoke = runtime.invoke
    render = plc_generation.render_generation_artifacts
    calls, rendered = [], []
    before = {str(p.relative_to(store.base_dir)): p.read_bytes()
              for p in store.base_dir.rglob("*") if p.is_file()}

    def observe_render(program, directory):
        result = render(program, directory)
        rendered.append({"program": copy.deepcopy(program),
                         "artifacts": _files(directory, result["artifacts"])})
        return result

    def observe_invoke(call, context):
        result = invoke(call, context)
        calls.append((call, context, result))
        return result

    async def exercise():
        provider = SessionToolContextProvider(store.base_dir, project_id)
        async with Client(create_server(provider, runtime)) as client:
            return await client.call_tool("create_program_candidate", {
                "ladder": candidate, "program_name": PROGRAM_NAME,
            })

    with monkeypatch.context() as scoped:
        scoped.setattr(runtime, "invoke", observe_invoke)
        scoped.setattr(plc_generation, "render_generation_artifacts", observe_render)
        public_result = anyio.run(exercise)
    assert len(calls) == 1
    assert calls[0][0].name == "create_program_candidate"
    assert calls[0][0].arguments["ladder"] == original
    assert candidate == original
    assert before == {str(p.relative_to(store.base_dir)): p.read_bytes()
                      for p in store.base_dir.rglob("*") if p.is_file()}
    assert "_candidate_ir" not in public_result.model_dump_json()
    assert "_confirmed_spec" not in public_result.model_dump_json()
    assert json.loads(public_result.content[0].text) == public_result.structured_content
    return {"response": public_result, "private": calls[0][2], "rendered": rendered}


def _parity(candidate, store, project_id, directory, monkeypatch, *, previous=None):
    spec = store.get_project(project_id)["confirmed_spec"]
    api = _api(candidate, spec, directory, previous=previous)
    mcp = _mcp(candidate, store, project_id, monkeypatch)
    response = mcp["response"]
    assert response.is_error is False, response.structured_content
    assert response.structured_content["status"] == "confirmation_required"
    data = response.structured_content["data"]
    action = mcp["private"].data["data"]["pending_action"]
    assert len(mcp["rendered"]) == 1
    program = api["program"]
    assert program == action["_candidate_ir"] == mcp["rendered"][0]["program"]
    assert json.loads(api["artifacts"]["json"]) == ir_to_ladder(action["_candidate_ir"])
    assert api["metadata"]["ir_sha256"] == action["candidate_ir_sha256"] == canonical_sha256(program)
    assert api["metadata"]["ladder_sha256"] == action["ladder_sha256"]
    assert program["revision"] == (previous["revision"] + 1 if previous else 1)
    assert program["program_name"] == PROGRAM_NAME
    assert program["plc"]["cpu"] == "FX3U"
    assert action["_confirmed_spec"] == spec
    assert data["validation_profile"] == api["metadata"]["validation_profile"] == PROFILE
    assert data["normalization"] == api["metadata"]["normalization"]
    for key in ("changes", "skipped"):
        assert all(set(item) == {"message", "network_ids"} for item in data["normalization"][key])
    assert set(api["artifacts"]) == ARTIFACT_NAMES
    # Check actual CSV, comments, SVG, ST, ladder JSON and IR bytes, not just
    # that both paths claim to have compiled or supply identically named files.
    assert api["artifacts"] == mcp["rendered"][0]["artifacts"]
    assert action["artifact_hashes"] == {
        name: hashlib.sha256(content).hexdigest() for name, content in api["artifacts"].items()
    }
    assert data["verification"]["behavior_verified"] is False
    assert data["verification"]["native_verified"] is False
    return api, action


@pytest.mark.parametrize(("output", "expected"), [
    ({"type": "APP_INSTR", "opcode": "OUT", "operands": ["Y0"]},
     {"type": "COIL", "address": "Y0"}),
    ({"type": "APP_INSTR", "opcode": "OUT", "operands": ["T2", "K20"]},
     {"type": "TIMER", "address": "T2", "value": "K20"}),
    ({"type": "APP_INSTR", "opcode": "OUT", "operands": ["C3", "K8"]},
     {"type": "COUNTER", "address": "C3", "value": "K8"}),
    ({"type": "TIMER", "address": "C1", "value": "K10"},
     {"type": "COUNTER", "address": "C1", "value": "K10"}),
    ({"type": "BLOCK_OUTPUT", "expression": "OUT T2 K20"},
     {"type": "TIMER", "address": "T2", "value": "K20"}),
    ({"type": "BLOCK_OUTPUT", "expression": "MOV K1 D0"},
     {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K1", "D0"]}),
    ({"type": "BLOCK_OUTPUT", "expression": "PLS M10"},
     {"type": "PLS", "address": "M10"}),
], ids=["out-coil", "out-timer", "out-counter", "timer-C-legacy", "block-out", "block-mov", "block-pls"])
def test_legacy_output_encodings_have_identical_normalized_ir_and_artifacts(
    tmp_path, monkeypatch, output, expected,
):
    store, project_id = _project(tmp_path)
    api, _ = _parity(_ladder(_rung(output={**output, "label": "保留标签"})), store,
                     project_id, tmp_path / "api", monkeypatch)
    result = ir_to_ladder(api["program"])["rungs"][0]["branches"][0]["outputs"][0]
    assert result == {**expected, "label": "保留标签"}


def test_duplicate_coils_and_selected_approach_conflict_are_not_new_mcp_blockers(tmp_path, monkeypatch):
    store, project_id = _project(tmp_path, conflict=True)
    candidate = _ladder(_rung(), _rung(2, condition="X1"))
    # This fixture would fail the former strict path for BOTH reasons.
    with pytest.raises(PLCJsonValidationError):
        validate_ladder_full(_ladder(), "FX3U", store.get_project(project_id)["confirmed_spec"])
    with pytest.raises(PLCJsonValidationError):
        validate_ladder_full(candidate, "FX3U")
    api, _ = _parity(candidate, store, project_id, tmp_path / "api", monkeypatch)
    assert [n["id"] for n in api["program"]["networks"]] == ["N0001", "N0002"]
    assert ir_to_ladder(api["program"]) == candidate


def test_public_condition_summary_and_factored_program_match(tmp_path, monkeypatch):
    store, project_id = _project(tmp_path)
    candidate = _ladder(_rung(), _rung(2, target="Y1"))
    candidate["rungs"][0]["branches"][0]["inputs"] *= 2
    api, _ = _parity(candidate, store, project_id, tmp_path / "api", monkeypatch)
    normalized = ir_to_ladder(api["program"])
    assert len(normalized["rungs"]) == 1
    assert len(normalized["rungs"][0]["branches"]) == 2
    assert normalized["rungs"][0]["shared_inputs"] == [{"type": "NO", "address": "X0"}]
    assert api["metadata"]["normalization"]["changes"]


@pytest.mark.parametrize("invalid", [
    {"type": "COIL", "address": "NOT_A_DEVICE"},
    {"type": "APP_INSTR", "opcode": "UNVERIFIED", "operands": ["D0"]},
    {"type": "BLOCK_OUTPUT", "expression": "UNVERIFIED D0"},
    {"type": "APP_INSTR", "opcode": "OUT", "operands": ["X0"]},
], ids=["invalid-address", "unverified-app-opcode", "unverified-block-opcode", "invalid-out-target"])
def test_invalid_candidates_are_rejected_by_both_paths_without_retry_or_artifacts(tmp_path, monkeypatch, invalid):
    store, project_id = _project(tmp_path)
    candidate = _ladder(_rung(output=invalid))
    with pytest.raises(GenerationValidationError) as rejected:
        _api(candidate, store.get_project(project_id)["confirmed_spec"], tmp_path / "api")
    assert rejected.value.diagnostics["attempt_count"] == 0
    assert rejected.value.diagnostics["max_attempts"] == 0
    assert not list((tmp_path / "api").iterdir())
    mcp = _mcp(candidate, store, project_id, monkeypatch)
    assert mcp["response"].is_error is True
    assert mcp["response"].structured_content["ok"] is False
    assert mcp["rendered"] == []
    assert "pending_action" not in mcp["private"].data.get("data", {})
    assert store.get_project(project_id)["versions"] == []


@pytest.mark.parametrize("candidate", [
    {"device_comments": {}, "rungs": []},
    {"device_comments": {}, "rungs": "not-a-list"},
    {"device_comments": {}, "rungs": [{"rung_id": 1, "branches": [None]}]},
    {"mode": "partial", "device_comments": {}, "rungs": [_rung()]},
], ids=["empty-program", "invalid-rungs-container", "invalid-branch-container", "partial-without-baseline"])
def test_structurally_invalid_responses_are_rejected_on_both_paths(tmp_path, monkeypatch, candidate):
    store, project_id = _project(tmp_path)
    with pytest.raises(GenerationValidationError) as rejected:
        _api(candidate, store.get_project(project_id)["confirmed_spec"], tmp_path / "api")
    assert rejected.value.diagnostics["attempt_count"] == 0
    assert not list((tmp_path / "api").iterdir())
    mcp = _mcp(candidate, store, project_id, monkeypatch)
    assert mcp["response"].is_error is True
    assert mcp["response"].structured_content["ok"] is False
    assert mcp["rendered"] == []
    assert store.get_project(project_id)["versions"] == []


def _edit(previous, mode):
    ladder = ir_to_ladder(previous)
    changed = copy.deepcopy(ladder["rungs"][0])
    changed["branches"][0]["inputs"] = [{"type": "NO", "address": "X2"}] * 2
    if mode == "partial":
        return {"mode": "partial", "rungs": [changed], "device_comments": {"X2": "新输入"}}
    ladder["rungs"][0] = changed
    ladder["device_comments"]["X2"] = "新输入"
    return ladder


def _save(service, project_id, api, action, *, path, base_id=None, request_id):
    if path == "mcp":
        proposal = service._pending_proposal(action, request_id, base_version_id=base_id)
        assert proposal["status"] == "pending"
    else:
        payload = {"project_id": project_id, "target_mode": "ladder", "plc_model": "FX3U",
                   "_confirmed_spec": service.store.get_project(project_id)["confirmed_spec"],
                   "_candidate_ir": api["program"], "_validation_profile": api["metadata"]["validation_profile"],
                   "normalization": api["metadata"]["normalization"]}
        payload = service._with_candidate_diff(project_id, base_id, payload)
        proposal = service.proposals.create("accept_local", project_id, payload,
            base_version_id=base_id, request_id=request_id)
    # Real operator preview and acceptance; never a standalone MCP auto-approve.
    preview = service.proposal_preview(proposal["id"])
    assert preview["ladder"] == ir_to_ladder(api["program"])
    accepted = service._save_local_proposal(proposal)
    assert accepted["status"] == "accepted"
    version_id = accepted["result"]["version_id"]
    version = service.store.get_version(project_id, version_id)
    assert version["validation_profile"] == PROFILE
    assert version["parent_version_id"] == base_id
    assert version["ir_sha256"] == api["metadata"]["ir_sha256"]
    assert _files(service.store.version_dir(project_id, version_id), version["artifacts"]) == api["artifacts"]
    return version_id


@pytest.mark.parametrize("path", ["api", "mcp"])
@pytest.mark.parametrize("mode", ["full", "partial"])
def test_full_and_partial_edits_save_reload_and_preview_with_generation_profile(
    tmp_path, monkeypatch, path, mode,
):
    store, project_id = _project(tmp_path, conflict=True)
    # Both versions retain duplicate Y0 writers and a selected-approach
    # conflict, so any silent fallback to strict validation really does fail.
    baseline = _ladder(_rung(), _rung(2, condition="X1"))
    service = WorkbenchService(store.base_dir, tmp_path / "state")
    service.start()
    try:
        api, action = _parity(baseline, store, project_id, tmp_path / "initial", monkeypatch)
        base_id = _save(service, project_id, api, action, path=path, request_id="initial")
        previous = service.store.load_program_ir(project_id, base_id, persist_legacy=False)
        before = copy.deepcopy(previous)
        candidate = _edit(previous, mode)
        edited, pending = _parity(candidate, store, project_id, tmp_path / "edit", monkeypatch, previous=previous)
        after = ir_to_ladder(edited["program"])
        assert after["rungs"][1] == ir_to_ladder(previous)["rungs"][1]
        assert after["rungs"][0]["branches"][0]["inputs"] == [{"type": "NO", "address": "X2"}]
        assert after["device_comments"]["X2"] == "新输入"
        assert pending["base_version_id"] == base_id
        assert pending["base_ir_sha256"] == canonical_sha256(previous)
        version_id = _save(service, project_id, edited, pending, path=path, base_id=base_id, request_id="edit")
        assert previous == before
    finally:
        service.close()

    # A new service/store instance must read the persisted profile and IR; no
    # cached candidate, open proposal or test override supplies this preview.
    reopened = WorkbenchService(store.base_dir, tmp_path / "state", read_only=True)
    try:
        project = reopened.store.get_project(project_id)
        assert len(project["versions"]) == 2
        assert project["active_version_id"] == version_id
        reloaded = reopened.store.load_program_ir(project_id, version_id, persist_legacy=False)
        assert reloaded == edited["program"]
        for saved_id in (base_id, version_id):
            expected = api if saved_id == base_id else edited
            preview = reopened.version_preview(project_id, saved_id)
            assert preview["read_only"] is True
            assert preview["ladder"] == ir_to_ladder(expected["program"])
            assert "<svg" in preview["svg"]
            assert preview["st"] == expected["artifacts"]["st_from_ir"].decode("utf-8").replace("\r\n", "\n")
    finally:
        reopened.close()

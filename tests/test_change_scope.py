"""Local change boundaries preserve Web autosave and explicit repair contracts."""
import copy
import json

import pytest

from application.workbench import WorkbenchService
from application.workspace import ConflictError, canonical_hash
from plc.change_scope import (ChangeScopeError, candidate_impact, enforce_change_scope,
                              normalize_change_scope, validate_scope_baseline)
from plc.core import PLCCore
from plc.ir import build_plc_ir, ir_to_ladder
from storage.session import SessionStore


def program(first="X0", second="X1", *, comments=None, reverse=False, name="MAIN"):
    rungs = [{"rung_id": number, "debug_note": "输出控制", "header_element": None,
              "shared_inputs": [], "branches": [{"branch_id": 1, "y_offset_level": 0,
              "inputs": [{"type": "NO", "address": address, "label": ""}],
              "outputs": [{"type": "COIL", "address": "Y" + str(number - 1), "label": ""}]}]}
             for number, address in enumerate((first, second), 1)]
    return build_plc_ir({"rungs": rungs[::-1] if reverse else rungs, "device_comments": comments or {}},
                        plc_model="FX3U", program_name=name, revision=2)


@pytest.fixture
def engineering(tmp_path):
    store = SessionStore(tmp_path / "workspace")
    project = store.create_project("范围测试")
    before = program()
    version_id, output = store.prepare_version(project["id"])
    metadata = store._ir_metadata(before)
    metadata.update(target_mode="ladder", plc_model="FX3U", artifacts=PLCCore().compile_project(before, output)["artifacts"])
    version = store.complete_version(project["id"], version_id, metadata)
    workbench = WorkbenchService(store.base_dir, tmp_path / "state", model_factory=lambda: (None, {"model": "fake"}))
    workbench.start()
    try:
        yield workbench, store, project["id"], version["id"], before
    finally:
        workbench.close()


def snapshot_files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_normalizes_scope_and_rejects_empty_or_invalid_dimensions():
    assert normalize_change_scope({"addresses": [" x00 ", "X0", "y010"]}) == {"addresses": ["X0", "Y10"]}
    for value in ({}, {"network_ids": []}, {"addresses": ["X8"]}, {"network_ids": "N0001"},
                  {"addresses": ["D999999"]}, {"extra": True}, {"network_ids": [True]}):
        with pytest.raises(ValueError):
            normalize_change_scope(value)


def test_scoped_change_accepts_intersection_and_summarizes_actual_dependencies():
    scope = {"network_ids": ["N0001"], "addresses": ["X0", "X2", "Y0"]}
    impact = enforce_change_scope(program(), program(first="X2"), scope)
    assert impact == {"network_ids": ["N0001"], "addresses": ["X0", "X2", "Y0"],
                      "device_comments": [], "metadata_fields": [], "network_order_changed": False}


@pytest.mark.parametrize("scope,after,expected", [
    ({"network_ids": ["N0001"]}, program(second="X2"), "N0002"),
    ({"addresses": ["X0", "X2"]}, program(first="X2"), "Y0"),
    ({"addresses": ["X2", "Y0"]}, program(first="X2"), "X0"),
    ({"network_ids": ["N0001"], "addresses": ["X0", "Y0"]}, program(first="X2"), "X2"),
    ({"network_ids": ["N0001"]}, program(reverse=True), "N0002"),
    ({"network_ids": ["N0001"]}, program(name="OTHER"), "program_name"),
    ({"network_ids": ["N0001"]}, program(comments={"X5": "其他注释"}), "X5"),
])
def test_rejects_every_outside_dimension(scope, after, expected):
    with pytest.raises(ChangeScopeError, match=expected):
        enforce_change_scope(program(), after, scope)


def test_shared_comment_impacts_all_references_and_cannot_escape_network_limit():
    before, after = program(second="X0"), program(second="X0", comments={"X0": "共享输入"})
    assert candidate_impact(before, after)["network_ids"] == ["N0001", "N0002"]
    with pytest.raises(ChangeScopeError, match="N0002"):
        enforce_change_scope(before, after, {"network_ids": ["N0001"]})


@pytest.mark.parametrize("opcode,operands", [("MOV", ["D100Z0", "D0"]), ("DMOV", ["D100", "D0"]),
                                            ("MOV", ["K4M0", "D0"])])
def test_address_scope_rejects_implicit_or_dynamic_spans(opcode, operands):
    ladder = ir_to_ladder(program())
    ladder["rungs"][0]["branches"][0]["outputs"] = [{"type": "APP_INSTR", "opcode": opcode, "operands": operands}]
    after = build_plc_ir(ladder, plc_model="FX3U", program_name="MAIN", revision=3)
    with pytest.raises(ChangeScopeError, match="无法界定地址范围"):
        enforce_change_scope(program(), after, {"addresses": ["X0", "Y0", "D100", "D0", "Z0", "M0"]})
    assert enforce_change_scope(program(), after, {"network_ids": ["N0001"]})["network_ids"] == ["N0001"]


@pytest.mark.parametrize("mode,before", [("st", None), ("fbd", {"kind": "fbd"}), ("ladder", None)])
def test_unsupported_or_initial_scope_is_explicitly_rejected(mode, before):
    with pytest.raises(ValueError, match="仅支持已有梯形图"):
        validate_scope_baseline({"network_ids": ["N0001"]}, before, target_mode=mode)


def test_unknown_network_is_not_silently_treated_as_whole_program():
    with pytest.raises(ValueError, match="不存在"):
        validate_scope_baseline({"network_ids": ["N9999"]}, program())


def test_transaction_freezes_scope_and_impact_before_local_save(engineering):
    workbench, store, project_id, version_id, _ = engineering
    before = snapshot_files(store.base_dir)
    payload = {"_candidate_ir": program(first="X2"), "change_scope": {"network_ids": ["N0001"]}}
    proposal = workbench.proposals.create("accept_local", project_id, payload,
        public_summary={"impact": {"network_ids": ["fake"]}}, base_version_id=version_id)
    assert proposal["status"] == "pending"
    assert proposal["summary"]["change_scope"] == {"network_ids": ["N0001"]}
    assert proposal["summary"]["impact"]["network_ids"] == ["N0001"]
    assert proposal["summary"]["impact"]["addresses"] == ["X0", "X2", "Y0"]
    assert snapshot_files(store.base_dir) == before
    result = workbench.proposals.accept(proposal["id"])
    assert result["status"] == "accepted"
    assert store.get_project(project_id)["active_version_id"] == result["result"]["version_id"]


def test_proposal_rejects_outside_change_before_saving_any_proposal(engineering):
    workbench, store, project_id, version_id, _ = engineering
    before = snapshot_files(store.base_dir)
    with pytest.raises(ChangeScopeError, match="N0002"):
        workbench.proposals.create("accept_local", project_id,
            {"_candidate_ir": program(second="X2"), "change_scope": {"network_ids": ["N0001"]}},
            base_version_id=version_id)
    assert workbench.proposals.list(project_id) == []
    assert snapshot_files(store.base_dir) == before


def test_accept_recomputes_scope_even_when_payload_hash_is_consistent(engineering):
    workbench, store, project_id, version_id, _ = engineering
    proposal = workbench.proposals.create("accept_local", project_id,
        {"_candidate_ir": program(first="X2"), "change_scope": {"network_ids": ["N0001"]}}, base_version_id=version_id)
    # Simulate a persisted candidate created by an older, permissive producer.
    record = workbench.proposals._load(proposal["id"])
    record["private_payload"]["_candidate_ir"] = program(second="X2")
    record["private_payload"]["candidate_ir_sha256"] = canonical_hash(program(second="X2"))
    record["payload_hash"] = canonical_hash(record["private_payload"])
    workbench.proposals._save(record)
    before = snapshot_files(store.base_dir)
    with pytest.raises(ConflictError):
        workbench.proposals.accept(proposal["id"])
    assert snapshot_files(store.base_dir) == before
    assert workbench.proposals.get(proposal["id"])["status"] == "conflict"


def test_tool_generated_candidate_cannot_bypass_selected_scope(engineering):
    workbench, store, project_id, version_id, _ = engineering
    command = {"project_id": project_id, "version_id": version_id, "name": "create_program_candidate",
               "arguments": {"ladder": ir_to_ladder(program(second="X2"))}, "call_id": "outside-tool",
               "change_scope": {"network_ids": ["N0001"]}}
    before = snapshot_files(store.base_dir)
    with pytest.raises(ChangeScopeError, match="N0002"):
        workbench.agent_call(command)
    assert workbench.proposals.list(project_id) == []
    assert snapshot_files(store.base_dir) == before
    command.update(call_id="allowed-tool", arguments={"ladder": ir_to_ladder(program(first="X2"))})
    response = workbench.agent_call(command)
    assert response["is_error"] is False
    assert workbench.proposals.get(response["proposal_id"])["summary"]["change_scope"] == command["change_scope"]


@pytest.mark.parametrize("kind", ["generation", "agent"])
def test_async_candidates_reject_outside_scope_and_report_reason(engineering, monkeypatch, kind):
    workbench, store, project_id, version_id, _ = engineering
    after = program(second="X2")
    if kind == "generation":
        from application.generation import GenerationWorkflow
        def generate(self):
            assert '"network_ids": ["N0001"]' in self.request.user_input
            return {"target_mode": "ladder", "validation": {},
                    "artifacts": PLCCore().compile_project(after, self.output_dir)["artifacts"]}
        monkeypatch.setattr(GenerationWorkflow, "run", generate)
    else:
        from agent_runtime.agent import AgentRunResult
        def agent(text, **kwargs):
            assert '"network_ids": ["N0001"]' in text
            return AgentRunResult("候选待确认", [{"type": "accept_generated_program", "project_id": project_id,
                "_candidate_ir": after, "change_scope": None}])
        monkeypatch.setattr('agent_runtime.agent.run_tool_agent', agent)
    before = snapshot_files(store.base_dir)
    job = workbench.submit({"project_id": project_id, "version_id": version_id, "kind": kind,
        "text": "修改第一网络", "response_language": "zh-CN", "request_id": "scoped-" + kind,
        "change_scope": {"network_ids": ["N0001"]}})
    workbench.jobs._futures[job["id"]].result(timeout=10)
    result = workbench.jobs.get(job["id"])
    assert result["status"] == "failed"
    assert result["error_code"] == "change_scope_violation"
    assert any("N0002" in event["payload"].get("message", "") for event in workbench.jobs.events(job["id"]))
    assert workbench.proposals.list(project_id) == []
    assert snapshot_files(store.base_dir) == before


def test_unbound_or_wrong_baseline_agent_candidate_is_rejected(engineering):
    workbench, _, project_id, version_id, _ = engineering
    pending = {"type": "accept_generated_program", "project_id": project_id, "version_id": "different",
               "_candidate_ir": program(first="X2")}
    with pytest.raises(ConflictError, match="基线"):
        workbench._pending_proposal(pending, "other-baseline", base_version_id=version_id,
                                    change_scope={"network_ids": ["N0001"]})


@pytest.mark.parametrize("mode", ["st", "fbd"])
def test_scoped_st_fbd_proposal_rejected_before_staging_is_touched(engineering, mode):
    workbench, _, project_id, version_id, _ = engineering
    with pytest.raises(ValueError, match="仅支持已有梯形图"):
        workbench.proposals.create("accept_local", project_id,
            {"target_mode": mode, "change_scope": {"network_ids": ["N0001"]}}, base_version_id=version_id)


def test_scope_is_part_of_request_identity(engineering):
    workbench, _, project_id, version_id, _ = engineering
    command = {"project_id": project_id, "version_id": version_id, "name": "create_program_candidate",
               "arguments": {"ladder": ir_to_ladder(program(first="X2"))}, "call_id": "scope-identity",
               "change_scope": {"network_ids": ["N0001"]}}
    response = workbench.agent_call(command)
    assert workbench.agent_call(command) == response
    command["change_scope"] = None
    with pytest.raises(ConflictError):
        workbench.agent_call(command)


class _ScopedProvider:
    def __init__(self, outputs):
        self.outputs = outputs
        self.requests = []

    def stream(self, request):
        from model_runtime.provider import TextDelta
        self.requests.append(request)
        yield TextDelta(json.dumps(self.outputs[len(self.requests) - 1], ensure_ascii=False))


def _submit_scoped(workbench, project_id, version_id, request_id="actual-generation"):
    return workbench.submit({"project_id": project_id, "version_id": version_id, "kind": "generation",
        "text": "修改第一网络", "response_language": "zh-CN", "request_id": request_id,
        "change_scope": {"network_ids": ["N0001"]}})


def _finish(workbench, job):
    workbench.jobs._futures[job["id"]].result(timeout=15)
    return workbench.jobs.get(job["id"])


@pytest.mark.parametrize("mode", ["ask", "auto", "full"])
def test_scoped_generation_autosaves_once_and_preserves_structural_profile(engineering, mode):
    from plc.validation import PLCJsonValidationError, validate_ladder_full
    workbench, store, project_id, version_id, _ = engineering
    workbench.update_approval_settings(mode=mode, expected_revision=0, confirm_full_access=True)
    spec = {"summary": "局部修改", "io_table": [], "parameters": [],
            "selected_approach": {"name": "MOV approach", "generation_contract": {
                "required_opcodes": ["MOV"], "enforce": True}}}
    store.set_confirmed_spec(project_id, spec)
    candidate = ir_to_ladder(program(first="X2"))
    # A strict semantic acceptance would reject this otherwise structural program.
    with pytest.raises(PLCJsonValidationError):
        validate_ladder_full(candidate, confirmed_spec=spec)
    provider = _ScopedProvider([candidate])
    workbench.model_factory = lambda: (provider, {"model": "offline"})
    job = _submit_scoped(workbench, project_id, version_id)
    completed = _finish(workbench, job)
    assert completed["status"] == "completed", completed
    assert completed["result"]["status"] == "saved"
    saved_id = completed["result"]["version_id"]
    assert store.get_version(project_id, saved_id)["validation_profile"] == "generation_structural"
    assert store.get_project(project_id)["active_version_id"] == saved_id
    proposal = workbench.proposals.get(completed["result"]["proposal_id"])
    assert proposal["status"] == "accepted"
    assert proposal["summary"]["approval"]["source"] == "local_autosave"
    assert proposal["summary"]["impact"]["network_ids"] == ["N0001"]
    assert len(provider.requests) == 1
    assert "N0001" in str(provider.requests[0].messages[-1].content)
    assert _submit_scoped(workbench, project_id, version_id)["id"] == job["id"]
    assert len(provider.requests) == 1
    assert len(store.get_project(project_id)["versions"]) == 2


def test_real_generation_scope_violation_does_not_autosave_or_retry(engineering):
    workbench, store, project_id, version_id, _ = engineering
    provider = _ScopedProvider([ir_to_ladder(program(second="X2"))])
    workbench.model_factory = lambda: (provider, {"model": "offline"})
    before = snapshot_files(store.base_dir)
    completed = _finish(workbench, _submit_scoped(workbench, project_id, version_id))
    assert completed["status"] == "failed"
    assert completed["error_code"] == "change_scope_violation"
    assert len(provider.requests) == 1
    assert workbench.proposals.list(project_id) == []
    assert snapshot_files(store.base_dir) == before


@pytest.mark.parametrize("outside", [False, True])
def test_gx_read_scope_is_enforced_before_local_autosave(engineering, monkeypatch, outside):
    from concurrent.futures import Future
    workbench, store, project_id, version_id, _ = engineering
    after = program(second="X2") if outside else program(first="X2")
    def read(*args, **kwargs):
        result = Future()
        result.set_result({"status": "completed", "_candidate_ir": after, "_confirmed_spec": None})
        return result
    monkeypatch.setattr(workbench.execution, "submit_read", read)
    before = snapshot_files(store.base_dir)
    job = workbench.submit({"project_id": project_id, "version_id": version_id, "kind": "gx_read",
        "response_language": "zh-CN", "request_id": "read-scoped",
        "change_scope": {"network_ids": ["N0001"]}})
    completed = _finish(workbench, job)
    if outside:
        assert completed["error_code"] == "change_scope_violation"
        assert any("N0002" in event["payload"].get("message", "") for event in workbench.jobs.events(job["id"]))
        assert workbench.proposals.list(project_id) == []
        assert snapshot_files(store.base_dir) == before
    else:
        assert completed["status"] == "completed", completed
        assert completed["result"]["version_id"] != version_id
        proposal = workbench.proposals.get(completed["result"]["proposal_id"])
        assert proposal["status"] == "accepted"
        assert proposal["summary"]["change_scope"] == {"network_ids": ["N0001"]}
        assert proposal["summary"]["approval"]["source"] == "local_autosave"


@pytest.mark.parametrize("outside", [False, True])
def test_explicit_structural_repair_inherits_original_scope(engineering, outside):
    workbench, store, project_id, version_id, _ = engineering
    candidate = ir_to_ladder(program(first="X2"))
    invalid = copy.deepcopy(candidate)
    invalid["rungs"][0]["debug_note"] = "过长说明" * 20
    repaired = ir_to_ladder(program(second="X2")) if outside else candidate
    provider = _ScopedProvider([invalid, repaired])
    workbench.model_factory = lambda: (provider, {"model": "offline"})
    failed = _finish(workbench, _submit_scoped(workbench, project_id, version_id))
    assert failed["status"] == "completed", failed
    assert failed["error_code"] is None
    assert failed["result"]["status"] == "saved_invalid"
    diagnostic_version = failed["result"]["version_id"]
    assert store.get_project(project_id)["active_version_id"] == diagnostic_version
    assert len(store.get_project(project_id)["versions"]) == 2
    assert len(provider.requests) == 1
    diagnostic_snapshot = snapshot_files(store.base_dir)

    repair = workbench.repair_generation(failed["id"], "explicit-repair")
    completed = _finish(workbench, repair)
    assert workbench.jobs._load(repair["id"])["snapshot"]["change_scope"] == {"network_ids": ["N0001"]}
    assert len(provider.requests) == 2
    assert "用户明确确认的一次结构修复" in str(provider.requests[1].messages[-1].content)
    if outside:
        assert completed["error_code"] == "change_scope_violation"
        assert workbench.proposals.list(project_id) == []
        assert snapshot_files(store.base_dir) == diagnostic_snapshot
        assert len(store.get_project(project_id)["versions"]) == 2
    else:
        assert completed["status"] == "completed", completed
        assert completed["result"]["status"] == "saved"
        assert len(store.get_project(project_id)["versions"]) == 3

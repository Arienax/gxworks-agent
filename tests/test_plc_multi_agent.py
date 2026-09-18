import copy

import pytest

from plc.ir import build_plc_ir, canonical_sha256
from agent_runtime.multi_agent import (
    DEBUG_AGENT,
    PATCH_AGENT,
    REVIEWER,
    TIMING_PLANNER,
    DeterministicMultiAgentSupervisor,
    MultiAgentError,
    build_review_context,
)
from storage.session import SessionStore


def _contact(kind, address):
    return {"type": kind, "address": address, "label": ""}


def _coil(address):
    return {"type": "COIL", "address": address, "label": ""}


def _ladder():
    return {
        "device_comments": {"X0": "启动", "X1": "停止", "Y0": "电机"},
        "rungs": [
            {
                "rung_id": 1,
                "debug_note": "启动电机",
                "header_element": None,
                "shared_inputs": [],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [_contact("NO", "X0"), _contact("NC", "X1")],
                        "outputs": [_coil("Y0")],
                    }
                ],
            }
        ],
    }


def _program():
    return build_plc_ir(_ladder(), plc_model="FX3U", revision=4)


def _local_report(program):
    from inspection.engine import run_local_inspection

    return run_local_inspection(
        _ladder(),
        report_type="program_review",
        request={"review_focus": "启动停止"},
        plc_model="FX3U",
        base_version_id="v0004",
        depth="basic",
    )


def _specialist_output(payload, *, title):
    binding = copy.deepcopy(payload["context"]["binding"])
    return {
        "binding": binding,
        "summary": title,
        "findings": [
            {
                "severity": "warning",
                "category": "specialist_note",
                "title": title,
                "message": "请结合已确认需求核对此网络。",
                "evidence": [
                    {"rung_id": 1, "json_path": "$.rungs[0]", "address": "Y0"}
                ],
                "recommendation": "保留当前行为并由工程师复核。",
                "fixable": False,
                "confidence": "medium",
            }
        ],
    }


def test_program_review_has_fixed_route_and_version_bound_audit():
    program = _program()
    calls = []

    def runner(role, payload):
        calls.append((role, copy.deepcopy(payload)))
        return _specialist_output(payload, title=role)

    result = DeterministicMultiAgentSupervisor(runner).review_program(
        program,
        project_id="project-a",
        version_id="v0004",
        request={"review_focus": "全部"},
        local_report=_local_report(program),
    )

    assert [role for role, _ in calls] == [REVIEWER, TIMING_PLANNER]
    assert result["audit"]["route"] == [REVIEWER, TIMING_PLANNER]
    assert result["audit"]["binding"] == {
        "project_id": "project-a",
        "version_id": "v0004",
        "revision": 4,
        "ir_sha256": canonical_sha256(program),
    }
    assert result["audit"]["authority"]["may_import"] is False
    assert result["audit"]["authority"]["may_run_simulator"] is False
    assert len(result["reports"]) == 2
    assert calls[1][1]["upstream"]["role"] == REVIEWER


def test_program_review_rejects_cross_version_specialist_output():
    program = _program()

    def runner(_role, payload):
        result = _specialist_output(payload, title="wrong")
        result["binding"]["version_id"] = "v9999"
        return result

    with pytest.raises(MultiAgentError, match="其他程序版本"):
        DeterministicMultiAgentSupervisor(runner).review_program(
            program,
            project_id="project-a",
            version_id="v0004",
            request={},
            local_report=_local_report(program),
        )


def test_program_review_rejects_invented_device_or_rung_evidence():
    program = _program()

    def runner(_role, payload):
        result = _specialist_output(payload, title="invented")
        result["findings"][0]["evidence"] = [
            {"rung_id": 999, "json_path": "$.rungs[999]", "address": "Y999"}
        ]
        return result

    with pytest.raises(MultiAgentError, match="当前程序之外"):
        DeterministicMultiAgentSupervisor(runner).review_program(
            program,
            project_id="project-a",
            version_id="v0004",
            request={},
            local_report=_local_report(program),
        )


def test_review_context_is_read_only_bounded_and_has_no_operational_authority():
    program = _program()
    local = _local_report(program)
    context = build_review_context(
        program,
        project_id="project-a",
        version_id="v0004",
        request={},
        local_report=local,
    )
    context["networks"][0]["comment"] = "changed"

    assert program["networks"][0]["comment"] != "changed"
    assert context["authority"] == {
        "advisory_only": True,
        "may_execute_tools": False,
        "may_modify_program": False,
        "may_import": False,
        "may_run_simulator": False,
        "deterministic_validator_is_authoritative": True,
    }


def test_debug_route_normalizes_diagnosis_before_patch_and_uses_plan_builder():
    evidence = {
        "binding": {
            "project_id": "project-a",
            "version_id": "v0004",
            "revision": 4,
            "ir_sha256": "a" * 64,
        },
        "related_networks": ["N0001"],
        "allowed_evidence_refs": ["network:N0001", "assertion:t:s:Y0"],
    }
    calls = []
    seen = {}

    def runner(role, payload):
        calls.append(role)
        if role == DEBUG_AGENT:
            return {
                "schema_version": 1,
                "root_cause": "停止条件未生效",
                "confidence": "high",
                "affected_networks": ["N0001"],
                "evidence_refs": ["network:N0001"],
                "recommended_change": "修正停止触点条件",
            }
        assert role == PATCH_AGENT
        assert payload["diagnosis"]["confidence"] == 0.9
        return {"schema_version": 1, "operations": [{"operation": "modify_network"}]}

    def builder(diagnosis, patch):
        seen["diagnosis"] = diagnosis
        seen["patch"] = patch
        return {"plan_id": "plan-one", "diagnosis": diagnosis, "patch": patch}

    plan = DeterministicMultiAgentSupervisor(runner).prepare_debug_plan(
        evidence=evidence,
        plan_builder=builder,
    )

    assert calls == [DEBUG_AGENT, PATCH_AGENT]
    assert seen["diagnosis"]["confidence"] == 0.9
    assert plan["multi_agent"]["route"] == [DEBUG_AGENT, PATCH_AGENT]
    assert plan["multi_agent"]["authority"]["may_modify_program"] is False


def _saved_project(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project(name="multi-agent", plc_model="FX3U")
    version_id, version_dir = store.prepare_version(project["id"])
    program = _program()
    from application.debug_loop import render_candidate_artifacts

    artifacts = render_candidate_artifacts(program, version_dir)
    store.complete_version(
        project["id"],
        version_id,
        {
            "target_mode": "ladder",
            "plc_model": "FX3U",
            "program_name": "MAIN",
            "revision": program["revision"],
            "ir_sha256": canonical_sha256(program),
            "ladder_sha256": program["source"]["ladder_sha256"],
            "artifacts": artifacts,
        },
    )
    return store, project["id"], version_id, program


def test_session_store_persists_append_only_multi_agent_audit(tmp_path):
    store, project_id, version_id, program = _saved_project(tmp_path)
    local = _local_report(program)
    result = DeterministicMultiAgentSupervisor(
        lambda role, payload: _specialist_output(payload, title=role)
    ).review_program(
        program,
        project_id=project_id,
        version_id=version_id,
        request={},
        local_report=local,
    )

    saved = store.save_multi_agent_run(project_id, version_id, result["audit"])
    loaded = store.load_multi_agent_run(project_id, version_id, saved["run_id"])

    assert loaded == saved
    assert store.get_version(project_id, version_id)["multi_agent_runs"][0][
        "route"
    ] == [REVIEWER, TIMING_PLANNER]
    with pytest.raises(ValueError, match="already exists"):
        store.save_multi_agent_run(project_id, version_id, result["audit"])


def test_session_store_rejects_cross_version_multi_agent_audit(tmp_path):
    store, project_id, version_id, program = _saved_project(tmp_path)
    run = {
        "run_id": "agents_wrong",
        "workflow": "program_review",
        "status": "accepted",
        "binding": {
            "project_id": project_id,
            "version_id": "v9999",
            "revision": program["revision"],
            "ir_sha256": canonical_sha256(program),
        },
        "route": [REVIEWER],
        "stages": [{"role": REVIEWER}],
    }
    with pytest.raises(ValueError, match="stale or cross-version"):
        store.save_multi_agent_run(project_id, version_id, run)


def test_api_specialist_prompts_expose_no_operational_or_delegation_authority():
    import application.model_workflows as api

    assert set(api.MULTI_AGENT_SPECIALIST_PROMPTS) == {REVIEWER, TIMING_PLANNER}
    for prompt in api.MULTI_AGENT_SPECIALIST_PROMPTS.values():
        lowered = prompt.lower()
        assert "cannot call tools" in lowered
        assert "delegate" in lowered
        assert "never output" in lowered


def test_review_specialists_receive_p8_knowledge_context(monkeypatch):
    import application.model_workflows as api

    captured = {}

    def fake_knowledge(primary_query, **kwargs):
        captured["query"] = primary_query
        captured["knowledge_kwargs"] = kwargs
        return "\n# Retrieved FX3U evidence\nPLSY operands are documented here.\n"

    def fake_call(prompt, payload, **kwargs):
        captured["prompt"] = prompt
        captured["payload"] = payload
        captured["call_kwargs"] = kwargs
        return {"binding": payload["context"]["binding"], "findings": []}

    monkeypatch.setattr(api, "_build_knowledge_context", fake_knowledge)
    monkeypatch.setattr(api, "_call_debug_evidence_json", fake_call)
    payload = {
        "context": {
            "binding": {
                "project_id": "project-a",
                "version_id": "v0004",
                "revision": 4,
                "ir_sha256": "a" * 64,
            },
            "plc": {"cpu": "FX3U"},
            "request": {"review_focus": "PLSY 定位时序"},
            "confirmed_spec": {"plc_model": "FX3U"},
            "networks": [{"instructions": [{"op": "PLSY", "args": ["K1000"]}]}],
            "logic": {},
            "timing": {},
            "deterministic_analysis": {},
            "local_report": {},
        }
    }

    result = api.run_multi_agent_specialist(REVIEWER, payload)

    assert result["findings"] == []
    assert "PLSY" in captured["query"]
    assert captured["knowledge_kwargs"]["plc_model"] == "FX3U"
    assert captured["knowledge_kwargs"]["task_type"] == "program_review"
    assert "# Retrieved FX3U evidence" in captured["prompt"]


def test_p9_exports_only_routes_with_real_supervisor_entrypoints():
    import agent_runtime.multi_agent as plc_multi_agent

    assert not hasattr(plc_multi_agent, "REQUIREMENT_AGENT")

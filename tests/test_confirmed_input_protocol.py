"""Compact transport and confirmed input facts, minimized from an operator failure.

No provider credentials, diagnostic archives, hardware, or paid requests.
"""
import copy
import json

import pytest

from application.compact_protocol import CompactProtocolError, expand_compact_ladder, normalize_compact
from application.confirmed_generation_context import build_confirmed_generation_context, project_confirmed_specification
from application.context_compiler import ContextCompiler, ContextCompilerInput
from plc.specification.bindings import confirmed_input_levels
from plc.specification.checks import check_direct_self_hold
from plc.validation import PLCJsonValidationError


OPAQUE = "输出触点与启动触点并联自锁，停止触点串联断开输出"


def old_confirmed_spec(start=1, stop=0):
    """Legacy persisted identities, no output row, and compound physical answers."""
    def answer(address, level):
        return f"{address} {'常开' if level else '常闭'}（按下为{'ON' if level else 'OFF'}）"
    params = [
        {"id": "start_signal", "name": "启动信号输入及极性", "value": answer("X0", start),
         "required": True, "source": "user", "note": "X002 / X003", "options": ["X002", "X003"]},
        {"id": "stop_signal", "name": "停止信号输入及极性", "value": answer("X1", stop),
         "required": True, "source": "user", "note": "X004 / X005", "options": ["X004", "X005"]},
        {"id": "output_device", "name": "被控输出接在哪个点？", "value": "Y000",
         "required": True, "source": "user", "note": "Y001 / Y002", "options": ["Y001", "Y002"]},
    ]
    rows, bindings = [], []
    for parameter, address in zip(params, ("X0", "X1")):
        identity = "question_" + parameter["id"]
        rows.append({"kind": "X", "address": address, "label": parameter["name"],
                     "binding_id": identity, "source_parameter_id": parameter["id"], "source": "user"})
        bindings.append({"kind": "X", "address": address, "binding_id": identity,
                         "row_binding_id": identity, "source_parameter_id": parameter["id"],
                         "name": parameter["name"], "value": parameter["value"], "source": "user"})
    return {
        "plc_model": "FX3U", "summary": "起保停", "parameters": params,
        "io_table": rows, "io_bindings": bindings,
        "selected_approach": {"approach_id": "hold", "name": "自锁起保停", "description": OPAQUE,
            "generation_guide": "", "generation_contract": {"required_structures": [],
                "source": "analysis_sanitized", "enforce": True,
                "unverified_constraints": {"required_structures": [OPAQUE]}}},
        "engineering_context": {"requests": [{"id": "original", "source": "user_request", "text": "起保停"}]},
    }


def compact(start=1, stop=0, wrapped=False, wrong=False):
    stop_contact = "NO" if stop == 0 else "NC"
    if wrong:
        stop_contact = "NC" if stop_contact == "NO" else "NO"
    inputs = [{"or": [[f"{'NO' if start else 'NC'} X0"], ["NO Y0"]]}, f"{stop_contact} X1"]
    return {"r": [{"h": None, "s": [], "b": [{"i": [inputs] if wrapped else inputs, "o": ["COIL Y0"]}]}]}


def test_single_wrapper_is_representation_only_and_idempotent():
    source = compact(wrapped=True, wrong=True)
    original = copy.deepcopy(source)
    normalized, changes = normalize_compact(source)
    assert source == original
    assert normalized == compact(wrong=True)
    assert changes == [{"rule": "single_series_input_wrapper", "path": "content.r.0.b.0.i",
                        "from_type": "wrapped_array", "to_type": "array"}]
    assert normalize_compact(normalized) == (normalized, [])
    assert expand_compact_ladder(source)["rungs"][0]["branches"][0]["inputs"][1]["type"] == "NC"


@pytest.mark.parametrize("inputs", [
    [["NO X0"], ["NO X1"]], ["NO X0", ["NO X1"]], [[["NO X0"]]], [[]],
    [[{"or": []}]], [[{"or": [[{"or": [["NO X0"]]}]]}]],
])
def test_ambiguous_or_invalid_array_shapes_are_not_guessed(inputs):
    value = {"r": [{"b": [{"i": inputs, "o": ["COIL Y0"]}]}]}
    with pytest.raises(CompactProtocolError):
        expand_compact_ladder(value)


@pytest.mark.parametrize(("text", "active"), [
    ("X0 常开（按下为ON）", 1), ("X1 常闭（按下为OFF）", 0),
    ("X1 常闭：未按下时接通，按下时断开", 0),
    ("X1 常开：未按下时断开，按下时接通", 1),
    ("X0 normally_closed_active_low", 0), ("X0 active_high", 1),
    ("X0 常閉", 0), ("X0 常開", 1),
    ("X0 按下为ON；按下为OFF", None), ("X0 常开或常闭", None), ("X0", None),
])
def test_confirmed_electrical_answers_are_not_ladder_contacts(text, active):
    assert confirmed_input_levels(text) == ({} if active is None else {"active_level": active, "inactive_level": 1-active})


def test_old_snapshot_recovers_output_and_input_facts_without_mutating_storage():
    spec = old_confirmed_spec()
    before = copy.deepcopy(spec)
    result = project_confirmed_specification(spec)
    assert spec == before and project_confirmed_specification(result) == result
    assert {r["address"] for r in result["io_table"]} == {"X0", "X1", "Y0"}
    bindings = {r["role"]: r for r in result["io_bindings"]}
    assert bindings["start"]["active_level"] == 1 and bindings["stop"]["active_level"] == 0
    assert bindings["output"]["address"] == "Y0"
    assert result["selected_approach"]["generation_contract"]["unverified_constraints"]["required_structures"] == [OPAQUE]
    assert expand_compact_ladder(compact(), result)["device_comments"]["Y0"]


def test_editing_or_deleting_bound_io_does_not_resurrect_old_answers():
    from plc.specification.confirmed import canonicalize_confirmed_spec
    spec = canonicalize_confirmed_spec(old_confirmed_spec())
    next(r for r in spec["io_table"] if r["address"] == "X1")["address"] = "X3"
    next(p for p in spec["parameters"] if p["id"] == "stop_signal")["value"] = "X1 常开（按下为ON）"
    result = project_confirmed_specification(spec)
    stop = next(r for r in result["io_bindings"] if r["role"] == "stop")
    assert stop["address"] == "X3" and stop["active_level"] == 1
    spec["io_table"] = [r for r in spec["io_table"] if r["address"] != "Y0"]
    result = project_confirmed_specification(spec)
    assert all(r["address"] != "Y0" for r in result["io_table"])
    assert all(r.get("role") != "output" for r in result["io_bindings"])


@pytest.mark.parametrize("start", [0, 1])
@pytest.mark.parametrize("stop", [0, 1])
def test_confirmed_levels_check_all_states_and_never_flip_generated_logic(start, stop):
    spec = project_confirmed_specification(old_confirmed_spec(start, stop))
    result = check_direct_self_hold(expand_compact_ladder(compact(start, stop)), spec)
    assert result["status"] == "verified" and result["states"] == 8
    wrong = expand_compact_ladder(compact(start, stop, wrong=True))
    before = copy.deepcopy(wrong)
    with pytest.raises(PLCJsonValidationError, match="start/stop behavior differs"):
        check_direct_self_hold(wrong, spec)
    assert wrong == before


def test_missing_or_edge_semantics_are_not_claimed_verified():
    spec = old_confirmed_spec()
    spec["parameters"][0]["value"] = "X0 常开，上升沿启动"
    result = project_confirmed_specification(spec)
    assert any("上升沿" in p["value"] for p in result["parameters"])
    assert check_direct_self_hold(expand_compact_ladder(compact()), result)["status"] == "not_covered"


def test_query_excludes_form_options_and_paths_but_generation_keeps_confirmed_facts():
    spec = project_confirmed_specification(old_confirmed_spec())
    output = ContextCompiler().compile(ContextCompilerInput(confirmed_spec=spec))
    query = output.retrieval_packet["query"]
    assert all(t not in query for t in ("parameters", "binding_id", "question_", "X002", "Y001", "note", "source"))
    assert output.generation_packet["confirmed_spec"] == spec


def test_resolved_self_hold_does_not_query_unrelated_manuals(monkeypatch):
    import knowledge.retriever as retriever
    monkeypatch.setattr(retriever, "build_knowledge_context", lambda *a, **k: pytest.fail("No specific fact target"))
    context = build_confirmed_generation_context(old_confirmed_spec(), "FX3U")
    assert context.knowledge_context == ""
    assert {r["address"] for r in context.io_bindings} == {"X0", "X1", "Y0"}


@pytest.mark.parametrize("text", [
    "SFTL M10 M100 K128 K1", "PLSY K1000 K2000 Y0", "DRVI D0 K1000 Y0 Y1",
    "M8013", "FX3U-4DA TO", "Modbus 寄存器", "查阅手册常闭触点", "timer T0 K10",
])
def test_specific_fact_targets_are_kept(text):
    from knowledge.analysis_router import has_generation_fact_target
    assert has_generation_fact_target(text)


def test_edit_request_contributes_new_fact_target():
    output = ContextCompiler().compile(ContextCompilerInput(confirmed_spec=project_confirmed_specification(old_confirmed_spec()),
        task_type="edit", generation_request="增加 SFTL M10 M100 K128 K1"))
    assert "SFTL M10 M100 K128 K1" in output.retrieval_packet["query"]


def test_model_window_does_not_override_task_evidence_allowance(monkeypatch):
    import knowledge.retriever as retriever
    from application.generation_context import _build_knowledge_context
    from knowledge.evidence import KnowledgeQuery
    seen = {}
    def capture(query, **kwargs):
        seen.update(kwargs)
        return ""
    monkeypatch.setattr(retriever, "build_knowledge_context", capture)
    _build_knowledge_context(KnowledgeQuery("SFTL M10 M100 K128 K1", precompiled=True,
        metadata={"rag_evidence_token_budget": 32000}), plc_model="FX3U")
    assert seen["char_budget"] == 7000 and seen["token_budget"] == 32000


class OneResponse:
    profile = {"id": "offline-input-regression", "adapter": "openai_compatible", "model": "offline-input-regression",
               "capabilities": {"structured_output": True}}
    def __init__(self, payload):
        self.payload = payload
        self.requests = []
    def stream(self, request):
        from model_runtime.provider import TextDelta
        self.requests.append(request)
        assert len(self.requests) == 1
        yield TextDelta(json.dumps(self.payload))


@pytest.mark.parametrize("wrong", [False, True])
def test_real_http_confirmation_and_generation_have_one_call_and_no_wrong_artifact(tmp_path, wrong):
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from integrations.web.app import create_app
    from test_web_api import ORIGIN, OPERATOR, _login
    provider = OneResponse(compact(wrapped=True, wrong=wrong))
    service = WorkbenchService(tmp_path/"workspace", tmp_path/"state",
                               model_factory=lambda: (provider, {"model": provider.profile["model"]}))
    app = create_app(service.store.base_dir, service=service, origin=ORIGIN, operator_token=OPERATOR)
    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        pid = client.post("/api/projects", headers=headers, json={"name": "input regression"}).json()["id"]
        confirmed = client.put(f"/api/projects/{pid}/spec", headers=headers,
            json={"spec": old_confirmed_spec(), "expected_hash": None})
        assert confirmed.status_code == 200 and confirmed.json()["valid"]
        assert provider.requests == []
        job = client.post("/api/jobs", headers=headers, json={"kind": "generation", "project_id": pid,
                          "request_id": "single-call", "text": "按确认规格生成", "response_language": "zh-CN"})
        assert job.status_code == 202, job.text
        jid = job.json()["id"]
        service.jobs._futures[jid].result(timeout=20)
        state = client.get(f"/api/jobs/{jid}").json()
        assert len(provider.requests) == 1 and provider.requests[0].max_retries == 0
        sent = provider.requests[0].messages[0].content
        assert '"active_level":0' in sent and '"active_level":1' in sent
        assert "# Retrieved PLC evidence" not in sent
        if wrong:
            assert state["status"] == "failed", state
            assert not service.projects.project(pid).get("versions")
        else:
            assert state["status"] == "completed", state
            result = client.get(f"/api/jobs/{jid}/output").json()
            assert result["status"] == "saved", result
            for artifact in ("json", "ir", "svg", "program_csv", "comment_csv"):
                assert service.projects.artifact(pid, result["version_id"], artifact).stat().st_size > 0
            saved = json.loads(service.projects.artifact(pid, result["version_id"], "json").read_text(encoding="utf-8"))
            assert "Y0" in saved["device_comments"]


def test_distinct_typed_machine_bindings_are_not_merged():
    from plc.specification.bindings import generation_io_snapshot
    spec = {"io_table": [], "parameters": [
        {"id": "motor1_start", "name": "一号启动", "value": "X0 常开", "source": "user",
         "io_binding": {"binding_id": "m1", "role": "start", "kind": "X"}},
        {"id": "motor2_start", "name": "二号启动", "value": "X2 常闭", "source": "user",
         "io_binding": {"binding_id": "m2", "role": "start", "kind": "X"}},
    ]}
    result = generation_io_snapshot(spec)
    assert {r["binding_id"]: (r["address"], r["active_level"]) for r in result["io_bindings"]} == {
        "m1": ("X0", 1), "m2": ("X2", 0)}


def test_saved_old_snapshot_can_generate_without_reconfirmation_or_analysis(tmp_path):
    from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow
    original = old_confirmed_spec()
    before = copy.deepcopy(original)
    provider = OneResponse(compact(wrapped=True))
    metadata = GenerationWorkflow(GenerationRequest(user_input="按确认规格生成", confirmed_context=original,
        plc_model="FX3U", model_name=provider.profile["model"]), tmp_path,
        dependencies=GenerationDependencies(provider=provider)).run()
    assert metadata["validation"]["status"] == "candidate_ready"
    assert len(provider.requests) == 1 and original == before


def test_form_options_never_supply_unanswered_physical_polarity():
    spec = old_confirmed_spec()
    for parameter in spec["parameters"][:2]:
        parameter["value"] = parameter["value"].split()[0]
        parameter["options"] = ["X0 常开（按下为 ON）", "X1 常闭（按下为 OFF）"]
    result = project_confirmed_specification(spec)
    assert all("active_level" not in row for row in result["io_bindings"])
    assert check_direct_self_hold(expand_compact_ladder(compact()), result)["status"] == "not_covered"


def test_compact_and_ladder_v1_prompts_distinguish_physical_polarity():
    from application.generation_agent import _COMPACT_PROTOCOL
    from application.generation_context import LADDER_SYSTEM_PROMPT
    assert 'i 本身是一维串联列表' in _COMPACT_PROTOCOL
    for prompt in (_COMPACT_PROTOCOL, LADDER_SYSTEM_PROMPT):
        assert 'active_level=0' in prompt
        assert '程序 NO 检查位=1' in prompt and 'NC 检查位=0' in prompt

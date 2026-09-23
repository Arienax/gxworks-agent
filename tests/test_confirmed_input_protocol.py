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


@pytest.mark.parametrize(("mnemonic", "expected"), [
    ("LD", "NO"), ("AND", "NO"), ("OR", "NO"),
    ("LDI", "NC"), ("ANI", "NC"), ("ORI", "NC"),
    ("LDP", "P"), ("ANDP", "P"), ("ORP", "P"),
    ("LDF", "F"), ("ANDF", "F"), ("ORF", "F"),
])
def test_mitsubishi_contact_mnemonics_are_canonicalized_at_compact_boundary(mnemonic, expected):
    value = {"r": [{"h": f"{mnemonic} M8002", "s": [], "b": [{"i": [], "o": ["COIL Y0"]}]}]}
    header = expand_compact_ladder(value)["rungs"][0]["header_element"]
    assert header == {"type": expected, "address": "M8002"}


def test_unknown_contact_mnemonic_is_still_rejected():
    value = {"r": [{"h": "LDX M8002", "s": [], "b": [{"i": [], "o": ["COIL Y0"]}]}]}
    with pytest.raises(CompactProtocolError):
        expand_compact_ladder(value)


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


def test_declared_io_survives_analysis_draft_v4_and_runtime_without_special_checker():
    from application.model_api import _normalize_analysis_result
    from plc.specification.confirmed import build_review_draft, canonicalize_confirmed_spec
    from plc.specification.provenance import build_confirmed_spec
    from plc.specification.semantic_validation import validate_confirmed_semantics

    raw = {
        "summary": "declared I/O",
        "approaches": [{
            "approach_id": "direct",
            "name": "direct",
            "generation_guide": "",
            "implementation_semantics": [
                {"kind": "structure", "status": "required", "value": "self_hold"},
            ],
        }],
        "suggested_io": {
            "X": {"X0": "启动按钮", "X1": "停止按钮"},
            "Y": {"Y0": "主输送带"},
            "M": {"M0": "模型擅自分配的运行位"},
            "T": {"T1": "模型擅自分配的定时器"},
        },
        "missing_info": [],
        "assumptions": [],
    }
    user_text = (
        "X0：启动按钮，常开，按下时 ON\n"
        "X1：停止按钮，常闭，未按下时 ON\n"
        "Y0：主输送带\n"
        "按下启动后保持运行，按下停止立即停止。"
    )
    normalized = _normalize_analysis_result(raw, "FX3U", user_text)
    assert set(normalized["suggested_io"]) == {"X", "Y"}
    assert "M0" not in json.dumps(normalized["suggested_io"], ensure_ascii=False)
    assert "T1" not in json.dumps(normalized["suggested_io"], ensure_ascii=False)

    draft = build_review_draft(normalized)
    spec = canonicalize_confirmed_spec(draft)
    bindings = {row["address"]: row for row in spec["io_bindings"]}
    assert set(bindings) == {"X0", "X1", "Y0"}
    assert bindings["X0"]["role"] == "start" and bindings["X0"]["active_level"] == 1
    assert bindings["X1"]["role"] == "stop" and bindings["X1"]["active_level"] == 0
    assert bindings["Y0"]["label"] == "主输送带"

    v4 = build_confirmed_spec(spec)
    assert v4["schema_version"] == 4
    projected = project_confirmed_specification(v4)
    runtime = ContextCompiler().compile(
        ContextCompilerInput(confirmed_spec=projected)
    ).generation_packet["confirmed_spec"]
    assert runtime["io_bindings"] == projected["io_bindings"]
    assert {row["address"] for row in runtime["io_bindings"]} == {"X0", "X1", "Y0"}

    ladder = {
        "device_comments": {},
        "rungs": [{
            "rung_id": 1,
            "header_element": None,
            "shared_inputs": [],
            "branches": [{
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [
                    {"type": "parallel_block", "branches": [
                        [{"type": "NO", "address": "X0"}],
                        [{"type": "NO", "address": "M0"}],
                    ]},
                    {"type": "NO", "address": "X1"},
                ],
                "outputs": [{"type": "COIL", "address": "M0"}],
            }],
        }],
    }
    report = validate_confirmed_semantics(ladder, runtime, plc_model="FX3U")
    self_hold = next(
        row for row in report["checks"]
        if row.get("kind") == "structure" and row.get("expected") == "self_hold"
    )
    assert self_hold["check"] == "contract_feature"
    assert self_hold["status"] == "verified"


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


def test_real_http_confirmation_and_generation_have_one_call(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from application.workbench import WorkbenchService
    from integrations.web.app import create_app
    from test_web_api import ORIGIN, OPERATOR, _login
    from plc.candidate_service import CandidateService
    prepare_calls = []
    original_prepare = CandidateService.prepare

    def counted_prepare(self, *args, **kwargs):
        prepare_calls.append(kwargs.get("candidate_origin"))
        return original_prepare(self, *args, **kwargs)

    monkeypatch.setattr(CandidateService, "prepare", counted_prepare)
    provider = OneResponse(compact(wrapped=True))
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
        assert prepare_calls == ["compact_agent"]
        sent = provider.requests[0].messages[0].content
        assert '"active_level":0' in sent and '"active_level":1' in sent
        assert "# Retrieved PLC evidence" not in sent
        assert sent.count("# Generation execution policy") == 1
        assert '"run_permit_when":"NO X1"' in sent
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
    from plc.specification.conditions import generation_input_conditions
    assert generation_input_conditions(result["io_bindings"]) == {
        "level_predicates": [],
        "unresolved_input_bindings": [],
    }


def test_compact_and_ladder_v1_prompts_distinguish_physical_polarity():
    from application.generation_agent import _COMPACT_PROTOCOL
    from application.generation_context import LADDER_SYSTEM_PROMPT
    assert 'i 本身是一维串联列表' in _COMPACT_PROTOCOL
    for prompt in (_COMPACT_PROTOCOL, LADDER_SYSTEM_PROMPT):
        assert 'active_level=0' in prompt
        assert '程序 NO 检查位=1' in prompt and 'NC 检查位=0' in prompt


@pytest.mark.parametrize("active", [0, 1])
@pytest.mark.parametrize("role", ["start", "stop", "sensor", "interlock"])
def test_execution_input_predicates_are_settled_levels_not_a_circuit(active, role):
    from plc.specification.conditions import generation_input_conditions
    bindings = [{"binding_id": "input", "kind": "X", "address": "x003", "role": role,
                 "active_level": active, "inactive_level": 1 - active}]
    before = copy.deepcopy(bindings)
    result = generation_input_conditions(bindings)
    row = result["level_predicates"][0]
    assert row["address"] == "X3"
    assert row["active_when"] == ("NO X3" if active else "NC X3")
    assert row["inactive_when"] == ("NC X3" if active else "NO X3")
    assert ("run_permit_when" in row) == (role == "stop")
    if role == "stop":
        assert row["run_permit_when"] == row["inactive_when"]
    assert result["unresolved_input_bindings"] == [] and bindings == before


@pytest.mark.parametrize("level", [None, "常闭", "0", 2, True])
def test_execution_missing_level_stays_unknown_without_rejection(level):
    from plc.specification.conditions import generation_input_conditions
    result = generation_input_conditions([{"binding_id": "unknown", "kind": "X", "address": "X3",
        "role": "stop", "name": "常闭停止", "active_level": level}])
    assert result == {"level_predicates": [], "unresolved_input_bindings": ["unknown"]}


def test_io_binding_role_is_machine_semantics_not_comment_text():
    from plc.specification.bindings import binding_hint

    explicit = binding_hint({
        "id": "custom_stop",
        "io_binding": {
            "binding_id": "machine.stop",
            "kind": "X",
            "role": "stop",
            "label": "停机按钮",
        },
    })
    assert explicit["role"] == "stop"
    assert explicit["label"] == "停机按钮"

    legacy = binding_hint({
        "id": "stop_input",
        "io_binding": {
            "binding_id": "stop_input",
            "kind": "X",
            "label": "停止按钮",
        },
    })
    assert legacy["role"] == "stop"
    assert legacy["label"] == "停止按钮"

    unknown = binding_hint({
        "id": "sensor_a",
        "io_binding": {
            "binding_id": "sensor_a",
            "kind": "X",
            "label": "停止字样只是显示文本",
        },
    })
    assert "role" not in unknown


def test_analysis_prompt_requires_role_when_control_semantics_are_known():
    from application.analysis_context import _IO_BINDING_PROMPT

    assert "role 是控制语义身份" in _IO_BINDING_PROMPT
    assert "label 只是人类可读用途/注释" in _IO_BINDING_PROMPT
    assert "未知 role 不猜测" in _IO_BINDING_PROMPT


def test_execution_typed_bits_do_not_turn_word_registers_or_outputs_into_inputs():
    from plc.specification.conditions import generation_input_conditions
    rows = [{"binding_id": kind, "kind": kind, "address": kind + "10", "role": "input", "active_level": 0}
            for kind in ("X", "M", "S", "Y", "D", "T", "C")]
    result = generation_input_conditions(rows)
    assert {row["address"] for row in result["level_predicates"]} == {"X10", "M10", "S10"}
    assert all("run_permit_when" not in row for row in result["level_predicates"])
    assert generation_input_conditions(None)["level_predicates"] == []


def test_execution_conflicting_levels_or_malformed_input_are_not_guessed():
    from plc.specification.conditions import generation_input_conditions
    result = generation_input_conditions([
        {"binding_id": "conflict", "kind": "X", "address": "X3", "active_level": 0, "inactive_level": 0},
        {"binding_id": "bad-address", "kind": "X", "address": "X8", "active_level": 1},
        None,
    ])
    assert result["level_predicates"] == []
    assert result["unresolved_input_bindings"] == ["conflict", "bad-address"]


def test_execution_predicates_recompute_after_confirmed_io_edits_and_deletion():
    from plc.specification.confirmed import canonicalize_confirmed_spec
    from plc.specification.conditions import generation_input_conditions
    spec = canonicalize_confirmed_spec(old_confirmed_spec())
    def predicates():
        return generation_input_conditions(project_confirmed_specification(spec)["io_bindings"])["level_predicates"]
    before = predicates()
    assert next(row for row in before if row["role"] == "stop")["run_permit_when"] == "NO X1"
    next(row for row in spec["io_table"] if row["address"] == "X1")["address"] = "X3"
    next(row for row in spec["parameters"] if row["id"] == "stop_signal")["value"] = "X1 常开（按下为ON）"
    after = predicates()
    assert next(row for row in after if row["role"] == "stop")["run_permit_when"] == "NC X3"
    spec["io_table"] = [row for row in spec["io_table"] if row["address"] != "X3"]
    assert not any(row["role"] == "stop" for row in predicates())
    assert next(row for row in before if row["role"] == "stop")["address"] == "X1"


def test_execution_compact_and_full_share_one_execution_contract_and_keep_evidence(monkeypatch):
    from application.confirmed_generation_context import GENERATION_EXECUTION_POLICY, generation_execution_prompt
    from application.generation_agent import _build_agent_b_prompt
    from application.generation_context import build_generation_instructions
    spec = old_confirmed_spec()
    original = copy.deepcopy(spec)
    calls = []
    evidence = "[KNOWLEDGE source=fixture-1]\nAn intact technical fact block.\n[/KNOWLEDGE]"
    def retrieve(*args, **kwargs):
        calls.append((args, kwargs))
        return evidence
    context = build_confirmed_generation_context(spec, "FX3U", knowledge_builder=retrieve)
    prompt = _build_agent_b_prompt(context.confirmed_spec, "FX3U", context=context)
    assert len(calls) == 1  # constructing compact prompt must not retrieve again
    expected = generation_execution_prompt(context.confirmed_spec, evidence_text=context.knowledge_context)
    assert prompt.endswith(expected) and prompt.count(GENERATION_EXECUTION_POLICY) == 1
    assert evidence in prompt and spec == original
    full = build_generation_instructions("generate", plc_model="FX3U", confirmed_context=spec,
        knowledge_builder=retrieve, profile_builder=lambda *a, **k: "",
        prompt_builder=lambda *a, **k: "existing full protocol")
    assert len(calls) == 2  # one retrieval per adapter, no extra planning/model pass
    assert full.endswith(expected) and full.count(GENERATION_EXECUTION_POLICY) == 1
    assert evidence in full and spec == original
    assert '"run_permit_when":"NO X1"' in full
    assert context.handoff["generation_execution_policy"] == "settled-facts-v1"


@pytest.mark.parametrize("task", ["format_repair", "contract_repair", "analysis", "program_review"])
def test_execution_policy_does_not_leak_into_other_tasks(task):
    from application.confirmed_generation_context import generation_execution_prompt
    assert generation_execution_prompt({}, task_type=task) == ""


def test_execution_policy_keeps_user_amendments_and_does_not_claim_evidence_coverage():
    from application.confirmed_generation_context import GENERATION_EXECUTION_POLICY, generation_execution_prompt
    spec = {"io_bindings": [{"binding_id": "stop", "role": "stop", "kind": "X", "address": "X3", "active_level": 0}],
        "parameters": [{"id": "stop.track_reset", "value": "preserve tracking"}],
        "selected_approach": {"generation_contract": {"unverified_constraints": {"required_structures": ["reset tracking"]}}}}
    before = copy.deepcopy(spec)
    prompt = generation_execution_prompt(spec, task_type="edit")
    facts = json.loads(prompt.rsplit("\n", 1)[1])
    assert facts["basis"] == "edit_baseline" and facts["retrieved_text_present"] is False
    assert "本轮明确修改优先" in prompt and "参数/绑定优先" in prompt
    assert spec == before  # no unreliable prose reconciliation, no lost intent
    provided = generation_execution_prompt(spec, evidence_text="one incomplete fact")
    assert json.loads(provided.rsplit("\n", 1)[1])["retrieved_text_present"] is True
    assert "不代表覆盖全部事实" in provided
    assert all(term not in GENERATION_EXECUTION_POLICY for term in ("WSFL", "SFTL", "M8012", "T0"))


def test_execution_protocol_does_not_blanket_ban_internal_special_devices():
    from application.generation_agent import _COMPACT_PROTOCOL
    assert "模块寄存器或特殊软元件" not in _COMPACT_PROTOCOL
    assert "当前型号资料/手册证据" in _COMPACT_PROTOCOL
    assert "已有显式禁用仍须遵守" in _COMPACT_PROTOCOL


def test_execution_current_snapshot_is_not_cached_or_written_back():
    from plc.specification.conditions import generation_input_conditions
    rows = [{"binding_id": "stop", "kind": "X", "address": "X003", "role": "stop", "active_level": 0}]
    previous = generation_input_conditions(rows)
    rows[0].update(address="X005", active_level=1)
    current = generation_input_conditions(rows)
    assert previous["level_predicates"][0]["run_permit_when"] == "NO X3"
    assert current["level_predicates"][0]["run_permit_when"] == "NC X5"
    assert "run_permit_when" not in rows[0] and rows[0]["address"] == "X005"
    rows.clear()
    assert generation_input_conditions(rows)["level_predicates"] == []

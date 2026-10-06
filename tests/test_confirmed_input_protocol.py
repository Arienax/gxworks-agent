"""Compact transport and confirmed input facts, minimized from an operator failure.

No provider credentials, diagnostic archives, hardware, or paid requests.
"""
import copy
import json

import pytest
from hypothesis import given, settings, strategies as st

from application.compact_protocol import CompactProtocolError, expand_compact_ladder, normalize_compact
from application.confirmed_generation_context import build_confirmed_generation_context, project_confirmed_specification
from application.context_compiler import ContextCompiler, ContextCompilerInput
from plc.specification.bindings import confirmed_input_levels
from model_profile_fixtures import offline_runtime_profile


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


@pytest.mark.parametrize('start,stop', [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_Core_predicate_binding_corrects_confirmed_levels_without_changing_topology(start, stop):
    from plc.specification.semantic_validation import bind_confirmed_predicates, validate_confirmed_semantics
    spec = {'io_bindings': [
        {'role': 'start', 'kind': 'X', 'address': 'X0', 'active_level': start, 'inactive_level': 1 - start},
        {'role': 'stop', 'kind': 'X', 'address': 'X1', 'active_level': stop, 'inactive_level': 1 - stop},
        {'role': 'output', 'kind': 'Y', 'address': 'Y0'}],
        'selected_approach': {'implementation_semantics': [
            {'kind': 'structure', 'status': 'required', 'value': 'self_hold'}]}}
    source = expand_compact_ladder(compact(start, stop, wrong=True))
    before = copy.deepcopy(source)
    assert validate_confirmed_semantics(source, spec)['status'] == 'violated'
    result, receipt = bind_confirmed_predicates(source, spec)
    assert result == expand_compact_ladder(compact(start, stop))
    assert source == before
    assert receipt['stage'] == 'Core_confirmed_predicate_binding' and receipt['model_calls'] == 0
    assert len(receipt['changes']) == 1
    assert validate_confirmed_semantics(result, spec)['status'] == 'verified'
    assert bind_confirmed_predicates(result, spec)[1]['changes'] == []


@pytest.mark.parametrize('damage', ['unknown_levels', 'other_output', 'wrong_address', 'no_confirmed_structure'])
def test_Core_predicate_binding_keeps_unsettled_or_nonisolated_logic(damage):
    from plc.specification.semantic_validation import bind_confirmed_predicates
    spec = {'io_bindings': [
        {'role': 'start', 'kind': 'X', 'address': 'X0', 'active_level': 1, 'inactive_level': 0},
        {'role': 'stop', 'kind': 'X', 'address': 'X1', 'active_level': 0, 'inactive_level': 1},
        {'role': 'output', 'kind': 'Y', 'address': 'Y0'}],
        'selected_approach': {'implementation_semantics': [
            {'kind': 'structure', 'status': 'required', 'value': 'self_hold'}]}}
    raw = compact(wrong=True)
    if damage == 'unknown_levels':
        spec['io_bindings'][1].pop('active_level')
        spec['io_bindings'][1].pop('inactive_level')
    elif damage == 'other_output':
        raw['r'][0]['b'][0]['o'].append('COIL Y1')
    elif damage == 'wrong_address':
        raw['r'][0]['b'][0]['i'][1] = 'NC X2'
    else:
        spec['selected_approach'] = {}
    source = expand_compact_ladder(raw)
    result, receipt = bind_confirmed_predicates(source, spec)
    assert result == source and receipt['changes'] == []


@pytest.mark.parametrize('placement', ['header', 'shared', 'branch'])
def test_confirmed_device_and_structure_checks_include_every_condition_position(placement):
    from plc.specification.semantic_validation import validate_confirmed_semantics
    from plc.specification.approach import inspect_ladder_features
    spec = {'io_bindings': [
        {'role': 'start', 'kind': 'X', 'address': 'X0', 'active_level': 1, 'inactive_level': 0},
        {'role': 'stop', 'kind': 'X', 'address': 'X1', 'active_level': 0, 'inactive_level': 1},
        {'role': 'output', 'kind': 'Y', 'address': 'Y0'}],
        'selected_approach': {'implementation_semantics': [
            {'kind': 'structure', 'status': 'required', 'value': 'self_hold'}],
            'explicit_user_constraints': {'required_devices': ['X0', 'X1', 'Y0']}}}
    ladder = expand_compact_ladder(compact())
    rung = ladder['rungs'][0]
    contact = rung['branches'][0]['inputs'].pop()
    assert contact == {'type': 'NO', 'address': 'X1'}
    if placement == 'header':
        rung['header_element'] = contact
    elif placement == 'shared':
        rung['shared_inputs'] = [contact]
    else:
        rung['branches'][0]['inputs'].append(contact)
    assert inspect_ladder_features(ladder)['devices'] == ['X0', 'X1', 'Y0']
    assert validate_confirmed_semantics(ladder, spec)['status'] == 'verified'


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


@pytest.mark.parametrize("task_type,user_delta,expected", [
    ("generate", "查 TCMP", []),
    ("edit", "改用 OR X0", ["OR"]),
])
def test_generation_protocol_text_does_not_create_instruction_lookup_needs(task_type, user_delta, expected):
    queries = []

    def capture(query, **kwargs):
        queries.append(query)
        return ""

    build_confirmed_generation_context(old_confirmed_spec(), "FX3U", task_type=task_type,
                                       user_requirement=user_delta, knowledge_builder=capture)
    assert len(queries) == 1
    assert [row["opcode"] for row in queries[0].metadata["structured_fact_targets"]["instructions"]] == expected


def test_equals_and_named_input_declarations_reach_the_spec_editor_with_levels():
    from application.model_api import _normalize_analysis_result
    from plc.specification.confirmed import build_review_draft

    text = "确认如下现场规格：X0=1为安全许可正常，X1启动按下=1，X2停止按下=1，D200=单瓶目标，Y0=输送电机。"
    raw = {"summary": "已声明现场I/O", "missing_info": [], "suggested_io": {"M": {"M1": "模型自分配"}},
           "approaches": [{"name": "直控", "implementation_semantics": []}]}
    draft = build_review_draft(_normalize_analysis_result(raw, "FX3U", text))
    assert {row["address"]: row["label"] for row in draft["io_table"]} == {
        "X0": "安全许可正常", "X1": "启动", "X2": "停止", "D200": "单瓶目标", "Y0": "输送电机"}
    inputs = {row["address"]: row for row in draft["io_bindings"] if row["kind"] == "X"}
    assert all(row["active_level"] == 1 and row["inactive_level"] == 0 for row in inputs.values())
    assert inputs["X1"]["role"] == "start" and inputs["X2"]["role"] == "stop"


@pytest.mark.parametrize("text", [
    "X0=1时启动Y0", "X0=1启动Y0", "X0=1且X1=0时运行", "X0=ON", "X0启动Y0=1",
    "X0启动时=1", "X0=1", "D0=0表示无料", "D0=1~3蓝色", "X0、X1为两个输入",
    "X0=1启动Y0=1", "X0=1时X1=0", "X0/X1=1分别启动Y0/Y1", "X0/X1=1时启动",
    "I/O：X0=1时启动Y0", "I/O: X0=ON",
])
def test_new_declaration_syntax_does_not_promote_states_or_logic_to_wiring(text):
    from plc.specification.bindings import extract_declared_bindings
    assert extract_declared_bindings(text, "FX3U") == []


@pytest.mark.parametrize(("text", "expected"), [
    ("使用FX3U。I/O：X0：启动按钮，按下为1；X1：停止按钮，按下为1；Y0：电机输出。",
     {"X0": 1, "X1": 1, "Y0": None}),
    ("I/O: X0: Start, active=1; Y0: Output", {"X0": 1, "Y0": None}),
    ("X7=1泵A过载，X10=1泵B过载，X11/X12=1分别手动点动A/B",
     {"X7": 1, "X10": 1, "X11": 1, "X12": 1}),
    ("X0／X1=0分别限位A/B，X2：复位按钮，按下为ON", {"X0": 0, "X1": 0, "X2": 1}),
    ("X5=1废品/0合格X6为编码器每节距脉冲", {"X5": 1, "X6": None}),
    ("X5=1废品/0合格，X6编码器每节距脉冲，所有脉冲至少50ms", {"X5": 1}),
    ("X0=1安全许可正常，X1启动按下=1，X2停止按下=1，X3复位按下=1，X4入口有件=1，"
     "X5=1废品/0合格X6为编码器每节距脉冲", {"X0": 1, "X1": 1, "X2": 1, "X3": 1, "X4": 1, "X5": 1, "X6": None}),
    ("X0=1安全许可正常，X3停止按钮按下=1，X4复位按钮按下=1，X5=1低液位，"
     "X11 / X12=1分别手动点动A/B", {"X0": 1, "X3": 1, "X4": 1, "X5": 1, "X11": 1, "X12": 1}),
])
def test_grouped_and_adjacent_input_declarations_keep_explicit_levels(text, expected):
    from plc.specification.bindings import extract_declared_bindings
    result = extract_declared_bindings(text, "FX3U")
    assert {row["address"]: row.get("active_level") for row in result} == expected


@settings(max_examples=100, deadline=None)
@given(addresses=st.lists(st.integers(min_value=1, max_value=63), min_size=2, max_size=5, unique=True),
       level=st.integers(min_value=0, max_value=1),
       slash=st.sampled_from(("/", " / ", "／", " ／ ")),
       comma=st.sampled_from((",", "，", "、")))
def test_grouped_input_level_roundtrip_with_spaces_aliases_and_separators(addresses, level, slash, comma):
    from plc.specification.bindings import extract_declared_bindings
    text = ("X0=1安全许可正常" + comma + slash.join(f"X{index:03o}" for index in addresses)
            + f" = {level}分别手动点动A/B" + comma + "X177编码器脉冲")
    result = extract_declared_bindings(text, "FX3U")
    expected = {"X0": 1, **{f"X{index:o}": level for index in addresses}}
    assert {row["address"]: row.get("active_level") for row in result} == expected


@settings(max_examples=100, deadline=None)
@given(index=st.integers(min_value=0, max_value=63), first_level=st.integers(min_value=0, max_value=1))
def test_conflicting_user_declarations_cannot_materialize_a_settled_level(index, first_level):
    from plc.specification.conditions import generation_input_conditions
    address = f"X{index:o}"
    spec = {"plc_model": "FX3U", "io_table": [{"kind": "X", "address": address, "label": "sensor"}],
        "intent_context": {"requests": [{"text":
            f"{address}={first_level}sensor\n{address}={1-first_level}sensor"}]}}
    projected = project_confirmed_specification(spec)
    assert projected["io_bindings"][0]["active_level"] is None
    assert generation_input_conditions(projected["io_bindings"]) == {
        "level_predicates": [], "unresolved_input_bindings": [f"declared.x.{address}"]}


def test_edit_request_contributes_new_fact_target():
    output = ContextCompiler().compile(ContextCompilerInput(confirmed_spec=project_confirmed_specification(old_confirmed_spec()),
        task_type="edit", generation_request="增加 SFTL M10 M100 K128 K1"))
    assert "SFTL M10 M100 K128 K1" in output.retrieval_packet["query"]


def test_model_window_drives_evidence_allowance_without_fixed_char_cap(monkeypatch):
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
    assert seen["token_budget"] == 32000
    assert seen["char_budget"] == 256000
    assert seen["top_k"] == 8


class OneResponse:
    profile = offline_runtime_profile("offline-input-regression")

    def __init__(self, payload):
        self.payload = payload
        self.requests = []
    def stream(self, request):
        from model_runtime.provider import TextDelta
        self.requests.append(request)
        assert len(self.requests) == 1
        yield TextDelta(json.dumps(self.payload))


def test_real_http_confirmation_and_generation_have_one_call(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="HTTP acceptance requires Web dependencies")
    pytest.importorskip("httpx", reason="HTTP acceptance requires Web dependencies")
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


@pytest.mark.parametrize("model,address,valid", [
    ("FX3U", "X7", True), ("FX3U", "X8", False), ("FX3U", "X10", True),
    ("FX5U", "X8", True), ("FX5U", "X18", True), ("FX5U", "X1778", False),
])
def test_input_predicates_use_the_selected_cpu_address_policy(model, address, valid):
    from plc.specification.conditions import generation_input_conditions
    binding = {"binding_id": "stop", "kind": "X", "address": address, "role": "stop", "active_level": 0}
    facts = generation_input_conditions([binding], plc_model=model)
    assert bool(facts["level_predicates"]) is valid
    if valid:
        assert facts["level_predicates"][0]["active_when"] == "NC " + address
        assert facts["level_predicates"][0]["run_permit_when"] == "NO " + address
    else:
        assert facts["unresolved_input_bindings"] == ["stop"]


def test_fx5u_compact_and_full_final_requests_keep_decimal_input_predicates():
    from application.generation_agent import _build_agent_b_prompt
    from application.generation_context import build_generation_instructions
    spec = {"plc_model": "FX5U", "io_table": [{"kind": "X", "address": "X18", "label": "停止"}],
            "io_bindings": [{"binding_id": "stop", "kind": "X", "address": "X18", "role": "stop", "active_level": 0}]}
    context = build_confirmed_generation_context(spec, "FX5U", knowledge_builder=lambda *a, **k: "")
    compact = _build_agent_b_prompt(context.confirmed_spec, "FX5U", context=context)
    full = build_generation_instructions("generate", plc_model="FX5U", confirmed_context=spec,
        knowledge_builder=lambda *a, **k: "", profile_builder=lambda *a, **k: "",
        prompt_builder=lambda *a, **k: "existing full protocol")
    assert '"active_when":"NC X18"' in compact and '"run_permit_when":"NO X18"' in compact
    assert '"active_when":"NC X18"' in full and '"run_permit_when":"NO X18"' in full


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
    from application.confirmed_generation_context import (
        GENERATION_EXECUTION_POLICY, GENERATION_EXECUTION_POLICY_VERSION, generation_execution_prompt)
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
    assert 'NO 在位=1时导通，NC 在位=0时导通' in prompt
    assert 'NO 在位=1时导通，NC 在位=0时导通' in full
    assert '现场接线不直接决定程序触点' in full
    assert evidence in full and spec == original
    assert '"run_permit_when":"NO X1"' in full
    assert context.handoff["generation_execution_policy"] == GENERATION_EXECUTION_POLICY_VERSION


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
    assert "本轮修改优先" in prompt and "绑定、参数" in prompt
    assert spec == before  # no unreliable prose reconciliation, no lost intent
    provided = generation_execution_prompt(spec, evidence_text="one incomplete fact")
    assert json.loads(provided.rsplit("\n", 1)[1])["retrieved_text_present"] is True
    assert "检索文本不代表完整覆盖" in provided
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


@pytest.mark.parametrize("context_key", ["intent_context", "engineering_context"])
def test_legacy_declarations_deliver_named_bit_values_to_both_generation_adapters(context_key):
    from application.confirmed_generation_context import generation_execution_prompt
    from application.generation_agent import _build_agent_b_prompt
    from application.generation_context import build_generation_instructions
    from plc.specification.confirmed import canonicalize_confirmed_spec

    spec = {"plc_model": "FX3U", "io_table": [
        {"address": "X0", "kind": "X", "label": "安全许可正常"},
        {"address": "X1", "kind": "X", "label": "停止按钮"},
        {"address": "Y0", "kind": "Y", "label": "泵"}], "parameters": [],
        context_key: {"requests": [{"id": "operator", "source": "user_request",
            "text": "X0=1安全许可正常\nX1：停止按钮，按下为OFF\nY0：泵"}]}}
    original = copy.deepcopy(spec)
    context = build_confirmed_generation_context(spec, "FX3U", knowledge_builder=lambda *a, **k: "")
    expected = generation_execution_prompt(context.confirmed_spec)
    facts = json.loads(expected.rsplit("\n", 1)[1])
    by_address = {row["address"]: row for row in facts["level_predicates"]}
    assert by_address["X0"]["label"] == "安全许可正常"
    assert by_address["X0"]["active_level"] == 1 and by_address["X0"]["active_when"] == "NO X0"
    assert by_address["X1"] == {
        "binding_id": "declared.stop.X1", "address": "X1", "label": "停止按钮", "role": "stop",
        "active_level": 0, "inactive_level": 1, "active_when": "NC X1", "inactive_when": "NO X1",
        "run_permit_when": "NO X1"}
    compact_prompt = _build_agent_b_prompt(context.confirmed_spec, "FX3U", context=context)
    full_prompt = build_generation_instructions("generate", plc_model="FX3U", confirmed_context=spec,
        knowledge_builder=lambda *a, **k: "", profile_builder=lambda *a, **k: "",
        prompt_builder=lambda *a, **k: "full protocol")
    assert compact_prompt.endswith(expected) and full_prompt.endswith(expected)
    assert spec == original
    assert project_confirmed_specification(context.confirmed_spec) == context.confirmed_spec
    canonical = project_confirmed_specification(canonicalize_confirmed_spec(spec))
    assert canonical["io_bindings"] == context.confirmed_spec["io_bindings"]


@pytest.mark.parametrize("current_level", [0, 1, None])
def test_historical_declaration_cannot_override_current_level_or_label(current_level):
    from plc.specification.confirmed import canonicalize_confirmed_spec
    from plc.specification.conditions import generation_input_conditions

    spec = {"plc_model": "FX3U", "io_table": [{"kind": "X", "address": "X1",
            "binding_id": "operator.stop", "label": "改名后的停机输入"}],
        "io_bindings": [{"kind": "X", "address": "X1", "binding_id": "operator.stop",
            "row_binding_id": "operator.stop", "role": "stop", "label": "旧标签",
            "active_level": current_level}], "parameters": [],
        "intent_context": {"requests": [{"text": "X1：停止按钮，按下为OFF"}]}}
    original = copy.deepcopy(spec)
    for projected in (project_confirmed_specification(spec),
                      project_confirmed_specification(canonicalize_confirmed_spec(spec))):
        assert len(projected["io_bindings"]) == 1
        assert projected["io_bindings"][0]["active_level"] == current_level
        assert projected["io_bindings"][0]["label"] == "改名后的停机输入"
        facts = generation_input_conditions(projected["io_bindings"])
        if current_level is None:
            assert facts == {"level_predicates": [], "unresolved_input_bindings": ["operator.stop"]}
        else:
            assert facts["level_predicates"][0]["active_level"] == current_level
    assert spec == original


def test_removed_or_explicitly_cleared_intent_does_not_recover_legacy_inputs():
    spec = {"plc_model": "FX3U", "io_table": [{"kind": "Y", "address": "Y0", "label": "泵"}],
        "intent_context": {}, "engineering_context": {"requests": [{"text": "X1：停止按钮，按下为OFF\nY0：泵"}]}}
    projected = project_confirmed_specification(spec)
    assert not projected.get("io_bindings")
    spec.pop("intent_context")
    projected = project_confirmed_specification(spec)
    assert [row["address"] for row in projected["io_bindings"]] == ["Y0"]
    assert [row["address"] for row in projected["io_table"]] == ["Y0"]


@settings(max_examples=150, deadline=None)
@given(
    signals=st.lists(st.tuples(st.integers(min_value=0, max_value=63), st.integers(min_value=0, max_value=1)),
                     min_size=1, max_size=8, unique_by=lambda item: item[0]),
    mutation=st.sampled_from(("move", "delete", "polarity")),
)
def test_recovered_signal_facts_follow_owned_row_edits_not_historical_text(signals, mutation):
    from plc.specification.bindings import generation_io_snapshot
    from plc.specification.conditions import generation_input_conditions

    # The independently supplied declarations are the oracle. The generator
    # does not create the expected signal levels or later operator edits.
    expected = {f"X{index:o}": level for index, level in signals}
    spec = {"plc_model": "FX3U", "io_table": [{"kind": "X", "address": f"X{index:03o}",
                "label": f"sensor_{index}"} for index, _ in signals],
        "intent_context": {"requests": [{"text": "\n".join(
            f"X{index:o}={level}sensor_{index}" for index, level in signals)}]}}
    original = copy.deepcopy(spec)
    saved = generation_io_snapshot(spec)
    old_address = f"X{signals[0][0]:o}"
    row = next(row for row in saved["io_table"] if row["address"] == old_address)
    binding = next(item for item in saved["io_bindings"] if item["address"] == old_address)
    if mutation == "move":
        new_address = f"X{max(index for index, _ in signals) + 1:o}"
        row["address"] = new_address
        expected[new_address] = expected.pop(old_address)
    elif mutation == "delete":
        saved["io_table"].remove(row)
        expected.pop(old_address)
    else:
        expected[old_address] = 1 - expected[old_address]
        binding.update(active_level=expected[old_address], inactive_level=1 - expected[old_address])
    projected = project_confirmed_specification(saved)
    facts = generation_input_conditions(projected["io_bindings"])
    assert {item["address"]: item["active_level"] for item in facts["level_predicates"]} == expected
    assert {row["address"] for row in projected["io_table"]} == set(expected)
    assert facts["unresolved_input_bindings"] == []
    assert project_confirmed_specification(projected) == projected
    assert spec == original


@settings(max_examples=150, deadline=None, derandomize=True)
@given(namespace=st.text(alphabet="abcXYZ_中文", min_size=1, max_size=24),
       signals=st.lists(st.tuples(st.integers(0, 63), st.integers(0, 1)), min_size=1, max_size=4,
                        unique_by=lambda pair: pair[0]),
       mutations=st.lists(st.tuples(st.integers(0, 3), st.sampled_from(("move", "delete", "polarity"))),
                          min_size=1, max_size=8), data=st.data())
def test_split_submission_facts_survive_permutations_and_owned_edits(namespace, signals, mutations, data):
    from plc.specification.confirmed import canonicalize_confirmed_spec, validate_spec_draft
    from application.confirmed_generation_context import project_confirmed_specification
    from plc.specification.conditions import generation_input_conditions
    parameters = []
    expected = {}
    for index, level in signals:
        identity = f"{namespace}.{index}"
        hint = {"binding_id": identity, "kind": "X", "role": "stop", "label": f"sensor_{index}"}
        expected[f"X{index:o}"] = level
        parameters.extend([
            {"id": f"{identity}.z_address", "name": f"{identity} input address?", "value": f"x{index:03o}", "io_binding": hint},
            {"id": f"{identity}.a_level", "name": f"{identity} input active level?", "value": "按下为 ON" if level else "按下为 OFF", "io_binding": hint},
        ])
    order = data.draw(st.permutations(range(len(parameters))))
    draft = {"plc_model": "FX3U", "io_table": [], "parameters": [parameters[i] for i in order]}
    original = copy.deepcopy(draft)
    assert not validate_spec_draft(draft)["errors"]
    saved = canonicalize_confirmed_spec(draft)
    assert canonicalize_confirmed_spec(saved) == saved
    for slot, mutation in mutations:
        number = signals[slot % len(signals)][0]
        identity = f"{namespace}.{number}"
        row = next((r for r in saved["io_table"] if r.get("binding_id") == identity), None)
        if row is not None:
            old = row["address"]
            if mutation == "move":
                new = f"X{number+64:o}" if old == f"X{number:o}" else f"X{number:o}"
                row["address"] = new.lower()
                expected[new] = expected.pop(old)
            elif mutation == "delete":
                saved["io_table"].remove(row)
                expected.pop(old)
            else:
                expected[old] = 1-expected[old]
                parameter = next(p for p in saved["parameters"] if p["io_binding"]["binding_id"] == identity)
                parameter["value"] = "按下为 ON" if expected[old] else "按下为 OFF"
        assert not validate_spec_draft(saved)["errors"]
        saved = canonicalize_confirmed_spec(saved)
        projected = project_confirmed_specification(saved)
        facts = generation_input_conditions(projected.get("io_bindings", []))
        assert {p["address"]: p["active_level"] for p in facts["level_predicates"]} == expected
        assert {r["address"] for r in projected["io_table"]} == set(expected)
        assert facts["unresolved_input_bindings"] == []
        assert canonicalize_confirmed_spec(saved) == saved
        assert project_confirmed_specification(projected) == projected
    assert draft == original

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from application import construction_examples as examples
from application.compact_protocol import CompactProtocolError, expand_compact_ladder, validate_compact_structure
from plc.ir import IR_SCHEMA_VERSION, ir_to_ladder, validate_plc_ir

from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow
from model_runtime.provider import TextDelta
from model_profile_fixtures import offline_runtime_profile


@pytest.mark.parametrize("mode,source", [
    ("st", {"st_code": "(* MOV(X0,Y0) *)\nY0 := X0;\nCopyData(src := D0);"}),
    ("fbd", {"nodes": [{"template": "function:CopyData"}]}),
])
def test_local_review_reports_actual_coverage_without_a_model_or_behavior_claim(mode, source):
    from plc.maintainability import review_maintainability
    review = review_maintainability(source, target_mode=mode)
    assert review["model_calls"] == 0 and review["behavior_verified"] is False
    assert review["used_capabilities"] == [{"name": "CopyData" if mode == "st" else "function:CopyData", "count": 1}]
    assert any(row["status"] == "unverified" for row in review["coverage"])


def test_repeated_writes_are_advisory_and_retain_locations_without_requiring_a_special_instruction():
    from plc.maintainability import review_maintainability
    from plc.candidate_service import CandidateService
    ladder = _ladder()
    ladder["rungs"][0]["branches"][0]["outputs"] = [
        {"type": "APP_INSTR", "opcode": "RST", "operands": [f"M{i}"]} for i in range(3)]
    review = review_maintainability(ladder)
    assert review["findings"][0]["replacement_verified"] is False
    assert {loc["rung_id"] for loc in review["findings"][0]["locations"]} == {1}
    prepared = CandidateService().prepare(ladder, plc_model="FX3U")
    assert prepared["maintainability_review"]["findings"]


def test_local_review_reports_shared_conditions_and_effect_gaps_without_modification():
    from plc.maintainability import review_maintainability
    from plc.candidate_service import CandidateService
    ladder = _ladder()
    second = copy.deepcopy(ladder["rungs"][0])
    second["rung_id"] = 2
    second["branches"][0]["outputs"][0]["address"] = "Y1"
    ladder["rungs"].append(second)
    original = copy.deepcopy(ladder)
    review = review_maintainability(ladder)
    assert ladder == original
    finding = next(row for row in review["findings"] if row["code"] == "repeated_shared_conditions")
    assert finding["conditions_reducible"] > 0
    assert finding["locations"] == [{"rung_id": 1}, {"rung_id": 2}]
    prepared = CandidateService().prepare(ladder)
    assert not any(f["code"] == "repeated_shared_conditions" for f in prepared["maintainability_review"]["findings"])
    second["branches"][0]["outputs"] = [{"type": "APP_INSTR", "opcode": "UNKNOWN", "operands": []}]
    review = review_maintainability(ladder)
    assert next(c for c in review["coverage"] if c["check"] == "output_effect_footprints")["status"] == "unverified"
    assert review["condition_review"]["barriers"][0]["reason"] == "instruction_fact_gap"
    assert review["model_calls"] == 0 and review["behavior_verified"] is False
    second["branches"][0]["outputs"] = [{"type": "APP_INSTR", "opcode": "BMOV", "operands": ["D0", "D9", "D20"]}]
    review = review_maintainability(ladder)
    assert review["condition_review"]["output_effect_precision"]["family"] == 1
    assert any(b["reason"] == "conservative_write_extent" for b in review["condition_review"]["barriers"])


@pytest.mark.parametrize("mode,source,location", [
    ("st", {"st_code": "(* outer (* inner *) Fake(); *)\nS := 'Fake($'text$')';\nCopy();\nCopy();\nCopy();"},
     {"line": 3, "column": 1}),
    ("fbd", {"nodes": [{"id": str(i), "template": "function:Copy"} for i in range(3)]},
     {"node_id": "0", "source_offset": None, "position": None}),
])
def test_repeated_language_calls_have_inspectable_positions_and_no_equivalence_claim(mode, source, location):
    from plc.maintainability import review_maintainability
    review = review_maintainability(source, target_mode=mode)
    assert review["used_capabilities"] == [{"name": "Copy" if mode == "st" else "function:Copy", "count": 3}]
    finding = review["findings"][0]
    assert finding["locations"][0] == location
    assert finding["severity"] == "advisory" and finding["replacement_verified"] is False


@pytest.mark.parametrize("coordinate_only", [False, True])
def test_fbd_review_retains_connection_context_without_inventing_coordinate_bindings(coordinate_only):
    from plc.maintainability import review_maintainability
    source = {"nodes": [{"id": str(i), "template": "function:Copy"} for i in range(3)],
        "wires": [{"from": "0.OUT", "to": "1.IN"}, {"from": "1.OUT", "to": "2.IN"}]}
    if coordinate_only:
        source["wires"][0] = {"source_offset": 1234, "start": [1, 2], "end": [3, 4]}
    original = copy.deepcopy(source)
    review = review_maintainability(source, target_mode="fbd")
    assert source == original
    finding = review["findings"][0]
    assert not finding["replacement_verified"] and not review["behavior_verified"]
    assert review["model_calls"] == 0
    assert len(review["connection_inventory"]) == 2
    middle = finding["locations"][1]["connections"]
    assert [row["connection_index"] for row in middle] == ([1] if coordinate_only else [0, 1])
    if coordinate_only:
        assert review["connection_inventory"][0] == {"connection_index": 0, "source_offset": 1234,
            "start": [1, 2], "end": [3, 4], "endpoint_binding": "unverified"}
        review["connection_inventory"][0]["start"][0] = 99
        assert source["wires"][0]["start"] == [1, 2]
    else:
        assert middle[0]["from"] == "0.OUT" and middle[1]["to"] == "2.IN"
    coverage = {row["check"]: row["status"] for row in review["coverage"]}
    assert coverage["explicit_connection_endpoints"] == ("unverified" if coordinate_only else "checked")


@pytest.mark.parametrize("tail,expected", [("", 1), ("\n(* incomplete", 0)])
def test_st_literal_write_review_preserves_locations_and_does_not_infer_shared_guards(tail, expected):
    from plc.maintainability import review_maintainability
    source = "(* Fake := 0; *)\nIF X0 THEN\n  First := 0;\nEND_IF;\nSecond := 0;\nThird := 0;\nText := 'Fake := 0;';\nVAR\n  Local : INT := 0;\nEND_VAR;" + tail
    review = review_maintainability(source, target_mode="st")
    findings = [row for row in review["findings"] if row["code"] == "repeated_literal_assignments"]
    assert len(findings) == expected
    if expected:
        assert [row["line"] for row in findings[0]["locations"]] == [3, 5, 6]
        assert [row["destination"] for row in findings[0]["locations"]] == ["First", "Second", "Third"]
        assert not findings[0]["control_conditions_verified"] and not findings[0]["replacement_verified"]
    assert not review["behavior_verified"] and review["model_calls"] == 0


def _spec():
    return {
        "summary": "X0 controls Y0",
        "io_table": [
            {"address": "X0", "kind": "X", "label": "start"},
            {"address": "Y0", "kind": "Y", "label": "motor"},
        ],
        "parameters": [],
        "selected_approach": {
            "approach_id": "direct",
            "name": "direct",
            "generation_contract": {
                "required_structures": ["direct_logic"],
                "forbidden_structures": [],
                "required_opcodes": ["OUT"],
                "forbidden_opcodes": [],
                "required_devices": ["X0", "Y0"],
                "forbidden_devices": [],
            },
        },
        "analysis_internal": "MUST_NOT_REACH_GENERATOR",
    }


def _ladder():
    return {
        "device_comments": {"X0": "start", "Y0": "motor"},
        "rungs": [{
            "rung_id": 1,
            "header_element": None,
            "shared_inputs": [],
            "branches": [{
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": "X0", "label": None}],
                "outputs": [{"type": "COIL", "address": "Y0", "label": None}],
            }],
        }],
    }


class OneShotProvider:
    def __init__(self):
        self.requests = []
        # Canonical v3 runtime profile: the model path materializes provider.profile.
        self.profile = offline_runtime_profile("offline-one-shot")

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) > 1:
            raise AssertionError("confirmed generation must not request a second full ladder")
        yield TextDelta(json.dumps({"r": [{"b": [{"i": ["NO X0"], "o": ["COIL Y0"]}]}]}, ensure_ascii=False))


@pytest.mark.parametrize(('cpu', 'aliases', 'native'), [
    ('Q02/Q02H', ['ADD', 'SUBP', 'MUL', 'DIV', 'DADDP', 'DMULP', '*P'],
     ['+', '-P', '*', '/', 'D+P', 'D*P', '*P']),
    ('FX3U', ['ADD', 'SUBP', 'MUL', 'DIV'], ['ADD', 'SUBP', 'MUL', 'DIV']),
])
def test_fresh_candidate_uses_target_native_integer_arithmetic(cpu, aliases, native):
    from application.compact_protocol import expand_compact_ladder
    from plc.generation import prepare_ladder_candidate
    from plc.ir import lower_rung_instructions

    args = [["D0", "K10", f"D{100 + 10 * index}"] for index in range(len(aliases))]
    candidate = expand_compact_ladder({"r": [{"c": ["NO X0"], "b": [{"i": [], "o": [
        ' '.join([opcode, *operands]) for opcode, operands in zip(aliases, args)
    ]}]}], "device_comments": []})
    prepared = prepare_ladder_candidate(candidate, plc_model=cpu, confirmed_spec={})
    instructions = lower_rung_instructions(prepared['ladder']['rungs'][0])
    assert [(item['op'], item['args']) for item in instructions if item['op'] != 'LD'] == list(zip(native, args))
    assert [item['opcode'] for item in candidate['rungs'][0]['branches'][0]['outputs']] == aliases


def test_raw_direct_generates_once_without_analysis_or_model_compaction(monkeypatch, tmp_path):
    import application.model_api as api
    import application.context_compactor as compactor
    import application.generation_agent as agent
    monkeypatch.setattr(api, "analyze_requirement_streaming", lambda *a, **k: pytest.fail("Direct invoked Agent A"))
    monkeypatch.setattr(compactor, "compact_if_needed", lambda *a, **k: pytest.fail("Direct invoked model compaction"))
    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **k: "")
    provider = OneShotProvider()
    raw = "X0：启动按钮，按下为1，释放为0。Y0：运行输出。按住启动时运行，松开停止。"
    metadata = GenerationWorkflow(GenerationRequest(user_input=raw, direct_generation=True,
        plc_model="FX3U", model_name=provider.profile["model"]), tmp_path,
        dependencies=GenerationDependencies(provider=provider)).run()
    assert len(provider.requests) == 1
    assert provider.requests[0].max_retries == 0
    messages = provider.requests[0].messages
    payload = json.loads(messages[-1].content)
    assert payload['context_schema_version'] == 3 and payload['request'] == raw
    assert "# Confirmed project specification" not in messages[0].content
    assert metadata["first_pass_pipeline"]["mode"] == "direct_generation"
    assert metadata["first_pass_pipeline"]["model_calls"] == 1
    assert {"operation_binding", "construction_binding", "confirmed_predicate_binding"} <= metadata["first_pass_pipeline"].keys()
    assert metadata["generation_handoff"]["raw_requirement"] == raw
    assert metadata["generation_handoff"]["local_check_coverage"]["unverified"]
    assert (tmp_path / metadata["artifacts"]["program_csv"]).stat().st_size > 0


def test_compact_annotations_preserve_logic_and_confirmed_names():
    from hypothesis import given, settings, strategies as st
    from application.generation_agent import prepare_model_candidate
    from plc.ir import lower_rung_instructions

    names = st.text(alphabet="ABCDEFabcdef启动停止电机使能运行", min_size=1, max_size=16)

    @settings(max_examples=40, deadline=None)
    @given(number=st.integers(min_value=0, max_value=4095), proposed=names, confirmed=names,
           envelope=st.sampled_from(["bare", "result", "transport", "root"]))
    def check(number, proposed, confirmed, envelope):
        compact = {"r": [{"c": ["NO X0"], "b": [{"i": [], "o": [f"COIL M{number}"]}]}]}
        annotated = {**copy.deepcopy(compact), "device_comments": [
            {"address": "x000", "comment": proposed},
            {"address": f"M{number:04d}", "comment": proposed},
        ]}
        if envelope == "result":
            annotated = {"result": annotated}
        elif envelope == "transport":
            annotated["type"] = "json_object"
        elif envelope == "root":
            annotated["root"] = annotated.pop("r")
        before = copy.deepcopy(annotated)
        spec = {"io_table": [{"address": "X0", "label": confirmed}]}
        ladder = prepare_model_candidate(annotated, spec, "FX3U")["ladder"]
        assert ladder["device_comments"] == {"X0": confirmed, f"M{number}": proposed}
        baseline = expand_compact_ladder(compact, spec)
        assert [lower_rung_instructions(r) for r in ladder["rungs"]] == [
            lower_rung_instructions(r) for r in baseline["rungs"]]
        assert annotated == before

    check()


@pytest.mark.parametrize("comments", [
    None, {"X0": "启动"}, ["X0 启动"], [{"address": "X0", "comment": 1}],
    [{"address": "X0", "comment": "长" * 17}], [{"address": "X0"}],
])
def test_compact_annotations_reject_malformed_values(comments):
    from application.compact_protocol import CompactProtocolError
    from application.generation_agent import prepare_model_candidate

    with pytest.raises(CompactProtocolError):
        prepare_model_candidate({"r": [{"c": [], "b": [{"i": [], "o": ["COIL M0"]}]}],
                                 "device_comments": comments}, {}, "FX3U")


@pytest.mark.parametrize("mixed", [False, True])
def test_direct_missing_fact_never_delivers_program(monkeypatch, tmp_path, mixed):
    import application.generation_agent as agent
    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **k: "")
    class Questions(OneShotProvider):
        def stream(self, request):
            self.requests.append(request)
            payload = {"status": "needs_input", "missing_info": [{"id": "delay", "question": "延时几秒？", "required": True}]}
            if mixed:
                payload["r"] = [{"b": [{"i": [], "o": ["COIL Y0"]}]}]
            yield TextDelta(json.dumps(payload, ensure_ascii=False))
    provider = Questions()
    workflow = GenerationWorkflow(GenerationRequest(user_input="延时启动", direct_generation=True,
        model_name=provider.profile["model"]), tmp_path, dependencies=GenerationDependencies(provider=provider))
    with pytest.raises(CompactProtocolError):
        workflow.run()
    assert len(provider.requests) == 1
    assert not list(tmp_path.glob("*.csv"))
    assert not list(tmp_path.glob("*.ir.json"))


@pytest.mark.parametrize("program,calls", [({}, 1), ({"r": []}, 1),
    ({"r": [{"b": [{"i": [], "o": ["COIL <output>"]}]}]}, 2)])
def test_direct_invalid_program_does_not_accept_unscoped_replacement(monkeypatch, tmp_path, program, calls):
    import application.generation_agent as agent
    from plc.candidate_repair import GenerationValidationError
    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **k: "")
    class Provider(OneShotProvider):
        def stream(self, request):
            self.requests.append(request)
            yield TextDelta(json.dumps({"result": {"program": program}}))
    provider = Provider()
    with pytest.raises((CompactProtocolError, GenerationValidationError)):
        GenerationWorkflow(GenerationRequest("X0控制Y0", direct_generation=True,
            model_name=provider.profile["model"]), tmp_path,
            dependencies=GenerationDependencies(provider=provider)).run()
    assert len(provider.requests) == calls and not list(tmp_path.glob("*.csv"))


def test_confirmed_generation_uses_one_isolated_agent_call(tmp_path):
    provider = OneShotProvider()
    request = GenerationRequest(
        user_input="RAW USER REQUIREMENT MUST NOT REACH GENERATOR",
        target_mode="ladder",
        conversation_history=[
            {"role": "user", "content": "OLD ANALYSIS HISTORY MUST NOT REACH GENERATOR"},
            {"role": "assistant", "content": "old analysis answer"},
        ],
        confirmed_context=_spec(),
        plc_model="FX3U",
        model_name="offline-one-shot",
    )
    metadata = GenerationWorkflow(
        request,
        tmp_path,
        dependencies=GenerationDependencies(provider=provider),
    ).run()

    assert len(provider.requests) == 1
    sent = "\n".join(str(getattr(message, "content", "")) for message in provider.requests[0].messages)
    assert "RAW USER REQUIREMENT MUST NOT REACH GENERATOR" not in sent
    assert "OLD ANALYSIS HISTORY MUST NOT REACH GENERATOR" not in sent
    assert "MUST_NOT_REACH_GENERATOR" not in sent
    assert "X0" in sent and "Y0" in sent
    assert provider.requests[0].stream is True
    assert provider.requests[0].max_retries == 0
    assert provider.requests[0].response_contract.name == "compact_ladder"
    assert metadata["first_pass_pipeline"] == {"mode": "confirmed_spec", "model_calls": 1}
    assert metadata["validation"]["status"] == "candidate_ready"


@pytest.mark.parametrize("operands", [["D0", "D100", "K56", "K1"], ["D20", "D200", "K16", "K2"]])
def test_operand_usage_and_execution_form_reach_actual_agent_b_messages(tmp_path, operands, private_manual_evidence):
    from knowledge import core
    if core._index_identity(core._index_path())[0] == "missing":
        pytest.skip("Bundled index is not installed")

    class UsageProvider(OneShotProvider):
        def stream(self, request):
            self.requests.append(request)
            yield TextDelta(json.dumps({"r": [{"b": [{"i": ["NO X0"], "o": ["WSFL " + " ".join(operands)]}]}]}))

    provider = UsageProvider()
    specification = _spec()
    contract = specification["selected_approach"]["generation_contract"]
    contract["required_opcodes"] = ["WSFL"]
    contract["instruction_instances"] = [{"opcode": "WSFL", "operands": operands}]
    metadata = GenerationWorkflow(
        GenerationRequest(user_input="Generate", confirmed_context=specification, plc_model="FX3U", model_name=provider.profile["model"]),
        tmp_path, dependencies=GenerationDependencies(provider=provider),
    ).run()
    assert len(provider.requests) == 1
    sent = "\n".join(str(getattr(message, "content", "")) for message in provider.requests[0].messages)
    import re
    groups = [g for match in re.finditer(r'(?m)^\[PLC KNOWLEDGE\]\n(\{[^\n]+\})', sent)
              for g in json.loads(match[1]).get('value', {}).get('facts', [])]
    assert any(g['value'].get('facet') == 'purpose' for g in groups)
    delivered = next(row for row in metadata['generation_handoff']['instruction_facts']['records']
                     if row.get('definition_fact_groups'))
    assert [slot["symbol"] for slot in delivered["operand_slots"]] == ["S", "D", "N1", "N2"]
    assert [slot["value"] for slot in delivered["operand_slots"]] == operands
    length, count = delivered["operand_slots"][2:]
    assert "Word data length of the shift data" in length["usage_facts"][0]["value"]
    assert "Number of words to be shifted leftward" in count["usage_facts"][0]["value"]
    for slot in (length, count):
        assert slot["purpose_status"] == "candidate_evidence"
        source = slot["usage_facts"][0]["sources"][0]
        assert source["manual_id"] == "fx3_programming_r"
        assert source["revision"] == "R"
        assert source["row_span"]
        assert slot["symbol_status"] == "source_verified"
    assert '"form":"continuous"' in sent
    assert '"trigger":"level"' in sent
    assert "STEP_WIDTH:" not in sent


def test_conflicting_usage_candidates_reach_agent_b_only_as_an_unresolved_facet(tmp_path, monkeypatch, private_manual_evidence):
    import knowledge.structured_facts as structured
    from tests.test_instruction_fact_context import _usage_source, _fixture_definition

    sources = [_usage_source([("S", value, "Bit")], identity=identity)
               for identity, value in (("a", "First conflicting purpose"), ("b", "Second conflicting purpose"))]
    sources[0]['instruction_definition'] = _fixture_definition(sources, sources[0], ['S', 'D', 'N1', 'N2'])
    monkeypatch.setattr(structured, "resolve_instruction_records", lambda *a, **k: copy.deepcopy(sources))

    class UsageProvider(OneShotProvider):
        def stream(self, request):
            self.requests.append(request)
            yield TextDelta(json.dumps({"r": [{"b": [{"i": ["NO X0"], "o": ["SFTL M0 M100 K16 K2"]}]}]}))

    provider = UsageProvider()
    specification = _spec()
    contract = specification["selected_approach"]["generation_contract"]
    contract["required_opcodes"] = ["SFTL"]
    contract["instruction_instances"] = [{"opcode": "SFTL", "operands": ["M0", "M100", "K16", "K2"]}]
    metadata = GenerationWorkflow(
        GenerationRequest(user_input="Generate", confirmed_context=specification, plc_model="FX3U", model_name=provider.profile["model"]),
        tmp_path, dependencies=GenerationDependencies(provider=provider),
    ).run()
    assert len(provider.requests) == 1
    sent = "\n".join(str(getattr(message, "content", "")) for message in provider.requests[0].messages)
    assert "First conflicting purpose" not in sent
    assert "Second conflicting purpose" not in sent
    assert '"unresolved_operands"' in sent and '"facet":"purpose"' in sent
    assert 'candidate_conflict' in sent
    source = next(row['operand_slots'][0] for row in metadata['generation_handoff']['instruction_facts']['records']
                  if row.get('operand_slots'))
    assert source["purpose_status"] == "unresolved"
    assert source["usage_conflicts"][0]["facet"] == "purpose"
    assert len(source["usage_conflicts"][0]["candidates"]) == 2
    receipt = metadata["generation_handoff"]["instruction_facts"]
    assert next(row for row in receipt["operand_facts"] if row["opcode"] == "SFTL" and row["position"] == 1 and row["facet"] == "purpose")["status"] == "unresolved"


@pytest.mark.parametrize(("required_opcode", "expected_status"), [
    ("OUT", "verified"), ("MOV", "violated"),
])
def test_fresh_semantic_receipt_survives_one_call_delivery(tmp_path, monkeypatch, required_opcode, expected_status):
    import application.generation_agent as agent_b

    # Keep the legacy snapshot above as a separate compatibility case. Fresh
    # semantics must not pass only because an old contract was deferred.
    specification = _spec()
    selected = specification["selected_approach"]
    selected.pop("generation_contract")
    selected["implementation_semantics"] = []
    selected["explicit_user_constraints"] = {"required_opcodes": [required_opcode]}
    before = copy.deepcopy(specification)
    monkeypatch.setattr(agent_b, "_build_knowledge_context", lambda *a, **k: "")
    provider = OneShotProvider()
    metadata = GenerationWorkflow(
        GenerationRequest(user_input="Generate the confirmed program", confirmed_context=specification,
                          plc_model="FX3U", model_name=provider.profile["model"]),
        tmp_path,
        dependencies=GenerationDependencies(provider=provider),
    ).run()

    assert specification == before
    assert len(provider.requests) == 1
    assert provider.requests[0].max_retries == 0
    assert metadata["first_pass_pipeline"] == {"mode": "confirmed_spec", "model_calls": 1}
    assert metadata["validation"]["status"] == "candidate_ready"
    assert metadata["candidate_origin"] == "compact_agent"
    receipt = metadata["semantic_validation"]
    assert receipt["legacy_compatibility"] is False
    assert receipt["status"] == expected_status
    assert any(row.get("kind") == "opcode" and row.get("expected") == required_opcode
               and row["status"] == expected_status for row in receipt["checks"])
    assert bool(receipt["violations"]) == (expected_status == "violated")
    if expected_status == "violated":
        assert any(row.get("expected") == required_opcode for row in receipt["violations"])
    ladder = json.loads((tmp_path / metadata["artifacts"]["json"]).read_text(encoding="utf-8"))
    assert ladder["rungs"]
    assert (tmp_path / metadata["artifacts"]["ir"]).is_file()


def test_compact_agent_sends_the_compiler_budgeted_application_wire(monkeypatch):
    import application.generation_agent as agent_b
    import application.model_api as api
    from application.generation_wire import wire_sha256, wire_token_estimate

    provider = OneShotProvider()
    captured = {}
    monkeypatch.setattr(agent_b, "_build_knowledge_context", lambda *a, **k: "")

    def request_model(messages, **kwargs):
        captured["messages"] = messages
        return SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(
                {"r": [{"h": None, "s": [], "b": [{"i": ["NO X0"], "o": ["COIL Y0"]}]}]},
                ensure_ascii=False,
            )
        ))

    monkeypatch.setattr(api, "request_model", request_model)
    with api.provider_scope(provider, model_name="offline-one-shot"):
        result = agent_b.generate_confirmed_ladder(
            _spec(), "FX3U", model_name="offline-one-shot"
        )

    actual = {"messages": captured["messages"]}
    handoff = result["generation_handoff"]
    assert handoff["wire_sha256"] == wire_sha256(actual)
    assert handoff["budget_report"]["budget_basis"] == "application_wire_messages"
    assert handoff["budget_report"]["compiled_budget_payload_tokens"] == wire_token_estimate(actual)


def test_over_budget_confirmed_generation_compacts_before_agent_b(monkeypatch):
    import application.generation_agent as agent_b
    import application.model_api as api

    provider = OneShotProvider()
    provider.profile["context_window"] = 24_000
    provider.profile["generationDefaults"] = {"max_completion_tokens": 2_048}
    specification = _spec()
    specification["intent_context"] = {
        "schema_version": 1,
        "requests": [
            {
                "id": f"r{i}",
                "source": "user_request",
                "text": f"OLD_CONTEXT_{i} X{i} K{i} " + ("历史上下文" * 350),
            }
            for i in range(10)
        ],
    }
    monkeypatch.setattr(agent_b, "_build_knowledge_context", lambda *a, **k: "")
    calls = []

    def request_model(messages, **kwargs):
        name = kwargs["response_contract"].name
        calls.append((name, copy.deepcopy(messages)))
        if name == "context_checkpoint":
            return SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "checkpoint": "Older intent checkpoint; preserve prior X/K references only for continuity."
            })))
        assert name == "compact_ladder"
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            "r": [{"h": None, "s": [], "b": [{"i": ["NO X0"], "o": ["COIL Y0"]}]}]
        })))

    monkeypatch.setattr(api, "request_model", request_model)
    with api.provider_scope(provider, model_name="offline-one-shot"):
        result = agent_b.generate_confirmed_ladder(
            specification, "FX3U", model_name="offline-one-shot"
        )

    assert [name for name, _ in calls] == ["context_checkpoint", "compact_ladder"]
    sent = json.dumps(calls[-1][1], ensure_ascii=False)
    assert "# Compacted historical context" in sent
    assert "Older intent checkpoint" in sent
    assert "OLD_CONTEXT_0" not in sent
    assert "OLD_CONTEXT_9" in sent
    assert result["model_calls"] == 2
    compaction = result["generation_handoff"]["budget_report"]["context_compaction"]
    assert compaction["status"] == "installed"
    assert compaction["model_calls"] == 1


def test_injected_direct_generator_remains_one_call(tmp_path):
    calls = []

    def direct(*args, **kwargs):
        calls.append((args, kwargs))
        return "", json.dumps(_ladder(), ensure_ascii=False)

    request = GenerationRequest(
        user_input="X0 controls Y0",
        target_mode="ladder",
        confirmed_context=_spec(),
        plc_model="FX3U",
        model_name="offline-direct",
    )
    metadata = GenerationWorkflow(
        request,
        tmp_path,
        dependencies=GenerationDependencies(stream_response=direct),
    ).run()
    assert len(calls) == 1
    assert metadata["first_pass_pipeline"] == {"mode": "direct"}
    assert metadata["validation"]["status"] == "candidate_ready"


def test_builtin_deepseek_chat_profile_uses_json_object_transport():
    from types import SimpleNamespace
    from application.generation_agent import _response_options
    from storage.config import DEFAULT_MODEL_PROFILES

    profile = next(item for item in DEFAULT_MODEL_PROFILES if item["id"] == "deepseek-default")
    assert _response_options(SimpleNamespace(profile=profile)) == {
        "response_format": {"type": "json_object"}
    }


def _profile_declaring_structured_output(modes):
    """Canonical v3 profile whose contract offers exactly these structured-output modes.

    The scope is computed with the same helper a saved profile uses, because a
    contract whose scope does not match its profile is ignored in favour of the
    generic template.
    """
    from model_runtime.contract import contract_scope

    profile = offline_runtime_profile()
    capabilities = {
        "structured_output": {
            "status": "supported", "source": "manual", "modes": list(modes),
        },
    }
    return {
        **profile,
        "capabilityContract": {
            "schema_version": 3,
            "scope": contract_scope(profile, {}, model=profile["model"], api_key=None),
            "capabilities": capabilities,
            "parameters": {},
        },
    }


def test_json_schema_transport_requires_explicit_profile_capability():
    from types import SimpleNamespace
    from application.generation_agent import _response_options

    # json_schema is offered only when the v3 contract declares it as a mode.
    provider = SimpleNamespace(profile=_profile_declaring_structured_output(["json_schema"]))
    response_format = _response_options(provider)["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


def test_json_schema_is_not_offered_without_a_declared_mode():
    from types import SimpleNamespace
    from application.generation_agent import _response_options

    provider = SimpleNamespace(profile=_profile_declaring_structured_output(["json_object"]))
    assert _response_options(provider)["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("modes", [["json_schema"], ["json_object"]])
def test_direct_delivers_header_contract_in_prompt_and_supported_wire_schema(monkeypatch, modes):
    import application.generation_agent as agent
    import application.model_api as api
    from application.compact_protocol import canonical_compact_example, compact_response_schema
    from jsonschema import Draft202012Validator

    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **k: "")
    provider = OneShotProvider()
    provider.profile = _profile_declaring_structured_output(modes)
    requirement = "X0 控制 Y0。暂停期间保留已登记事件，X1 触发后从原状态继续。"
    with api.provider_scope(provider):
        agent.generate_direct_ladder(requirement, model_name=provider.profile["model"])
    request = provider.requests[0]
    prompt = request.messages[0].content
    assert "先比较可行的控制结构，再生成程序" in prompt
    assert "以原文和确认规格中的工艺约束筛选候选并核对生成的程序" in prompt
    assert "停机立即撤销现场输出" not in prompt
    assert "暂停期间保留已登记事件" not in prompt
    assert "同一逻辑仅地址、索引或常数不同" in prompt
    assert "按处理规则分组" in prompt and "未知参数不构成逐项展开的理由" in prompt
    assert "按 decisions、program、open_items 的顺序" in prompt
    assert 'c 是所有输出分支共用的串联条件列表' in prompt
    example = canonical_compact_example()
    Draft202012Validator(compact_response_schema()).validate(example)
    assert json.dumps(example, separators=(",", ":")) in prompt
    wire = request.options["response_format"]
    assert wire["type"] == modes[0]
    from application.generation_wire import expand_context_document
    payload = expand_context_document(json.loads(request.messages[-1].content))
    assert payload['request'] == requirement
    assert ('output_schema' in payload) == (modes != ['json_schema'])
    if modes == ["json_schema"]:
        schema = wire["json_schema"]["schema"]
        assert schema["type"] == "object" and "anyOf" not in schema
        result = schema["properties"]["result"]
        assert set(result["required"]) == {"program", "decisions", "open_items"}
        assert next(iter(result["properties"])) == "decisions"
        assert "decision_basis" in result["properties"]["decisions"]["properties"]["candidates"]["items"]["required"]
        assert "needs_input" not in json.dumps(schema) and "blocked" not in json.dumps(schema)
        program = result["properties"]["program"]
        assert program == compact_response_schema(bindings=False)
        assert program["properties"]["r"]["items"]["properties"]["c"]["type"] == "array"
        assert set(program["required"]) == set(program["properties"])
        annotation = program["properties"]["device_comments"]["items"]
        assert annotation["additionalProperties"] is False
        assert set(annotation["required"]) == set(annotation["properties"])


@pytest.mark.parametrize('direct', [False, True])
@pytest.mark.parametrize('strict', [False, True])
def test_construction_reference_uses_one_generation_schema(monkeypatch, direct, strict):
    import application.generation_agent as agent
    import application.model_api as api
    from application.generation_wire import expand_context_document
    from jsonschema import Draft202012Validator

    spec = {'behavior_constraints': [{'id': 'boot', 'kind': 'initialize',
        'status': 'confirmed', 'provenance': {'source': 'user_confirmed', 'evidence': ['initialize M10']},
        'values': {'M10': False}, 'execution_context': {'program_type': 'scan'}}],
        'selected_approach': {'construction_plan': {'instances': [
            {'id': 'boot_instance', 'requirement_id': 'boot', 'method': 'first_scan_isolation'}]}}}
    program = {'r': [{'construct': 'boot_instance'},
        {'c': ['NO X0'], 'b': [{'i': [], 'o': ['COIL Y0']}]}], 'device_comments': []}
    answer = {'result': {'decisions': {'candidates': [{'method': '确认方案', 'tradeoff': '沿用首扫初始化',
                                                      'instruction_choices': [], 'decision_basis': []}],
                                      'selected': 0, 'rationale': '按确认规格实现'},
                         'program': program, 'open_items': []}} if direct else program
    captured = []
    def request(messages, **kwargs):
        captured.append((messages, kwargs))
        payload = expand_context_document(json.loads(messages[-1]['content']))
        assert 'boot_instance' in payload['construction_bindings']
        expected = agent._generation_schema(spec, direct=direct, bindings=False)
        if strict:
            assert 'output_schema' not in payload
            assert kwargs['options']['response_format']['json_schema']['schema'] == expected
        else:
            assert payload['output_schema'] == expected
        Draft202012Validator(expected).validate(answer)
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))
    monkeypatch.setattr(agent, '_build_knowledge_context', lambda *a, **k: '')
    monkeypatch.setattr(api, 'request_model', request)
    provider = OneShotProvider()
    provider.profile = _profile_declaring_structured_output(['json_schema' if strict else 'json_object'])
    with api.provider_scope(provider):
        result = (agent.generate_direct_ladder('Initialize M10, X0 controls Y0', confirmed_spec=spec)
                  if direct else agent.generate_confirmed_ladder(spec))
    assert len(captured) == 1 and result['model_calls'] == 1
    assert result['ladder']['rungs'] and not result['construction_binding']['reference_violations']


def test_direct_image_keeps_compiled_requirement_facts_and_knowledge(monkeypatch):
    import application.generation_agent as agent
    import application.model_api as api
    from application.generation_wire import expand_context_document
    from model_runtime.provider import ImageAttachment, UserMessage

    captured = []
    image = ImageAttachment('diagram.png', 'image/png', b'image-content')
    monkeypatch.setattr(agent, '_build_knowledge_context', lambda *a, **k: 'KEEP_KNOWLEDGE_CONTEXT')
    def request(messages, **kwargs):
        captured.append(messages)
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(
            {'r': [{'c': ['NO X0'], 'b': [{'i': [], 'o': ['COIL Y0']}]}], 'device_comments': []})))
    monkeypatch.setattr(api, 'request_model', request)
    with api.provider_scope(OneShotProvider()):
        agent.generate_direct_ladder('X0 controls Y0', confirmed_spec=_spec(), image_attachments=[image])
    assert len(captured) == 1
    message = captured[0][-1]
    assert isinstance(message, UserMessage) and message.images == (image,)
    payload = expand_context_document(json.loads(message.content))
    assert payload['request'] == 'X0 controls Y0'
    assert payload['confirmed_spec']['io_table']
    assert 'KEEP_KNOWLEDGE_CONTEXT' in json.dumps(payload['knowledge'])
    assert payload['output_schema']['properties']['result']


def test_compact_partial_preserves_ids_instruction_order_and_unedited_rungs():
    from hypothesis import given, settings, strategies as st
    from application.compact_protocol import expand_compact_partial
    from plc.candidate_repair import materialize_partial
    from plc.ir import lower_rung_instructions

    @settings(max_examples=35, deadline=None)
    @given(identity=st.integers(min_value=2, max_value=1000), number=st.integers(min_value=0, max_value=4095),
           contact=st.sampled_from([('NO', 'LD'), ('NC', 'LDI'), ('P', 'LDP'), ('F', 'LDF')]))
    def check(identity, number, contact):
        base = _ladder()
        other = copy.deepcopy(base['rungs'][0])
        other['rung_id'] = identity
        base['rungs'].append(other)
        patch = {'mode': 'partial', 'r': [{'rung_id': identity, 'c': [],
            'b': [{'i': [f'{contact[0]} X1'], 'o': [f'MOV K{number} D0', 'COIL M5']}]}],
            'device_comments': [], 'delete_rung_ids': []}
        before = copy.deepcopy((base, patch))
        merged = materialize_partial(base, expand_compact_partial(patch))
        assert [row['rung_id'] for row in merged['rungs']] == [1, identity]
        assert merged['rungs'][0] == base['rungs'][0]
        assert merged['device_comments'] == base['device_comments']
        instructions = lower_rung_instructions(merged['rungs'][1])
        assert [(row['op'], row['args']) for row in instructions] == [
            (contact[1], ['X1']), ('MOV', [f'K{number}', 'D0']), ('OUT', ['M5'])]
        assert (base, patch) == before
    check()


def _expand_wire_case(row, partial):
    from application.compact_protocol import expand_compact_partial
    payload = {'r': [copy.deepcopy(row)], 'device_comments': []}
    if partial:
        payload.update(mode='partial', delete_rung_ids=[])
        payload['r'][0]['rung_id'] = 37
        result = expand_compact_partial(payload)
        assert result['rungs'][0]['rung_id'] == 37
        return {'rungs': result['rungs'], 'device_comments': result['device_comments']}
    return expand_compact_ladder(payload)


def _assert_wire_csv_instructions(tmp_path, ladder, expected):
    from gxworks2.csv_export import generate_gx_works2_csv
    from gxworks2.csv_importer import parse_gxworks2_csv
    from plc.ir import lower_rung_instructions
    from plc.validation import validate_ladder_full
    validate_ladder_full(ladder, plc_model='FX3U')
    lowered = [' '.join([item['op'], *item['args']])
               for rung in ladder['rungs'] for item in lower_rung_instructions(rung)]
    assert lowered == expected
    program, comments = tmp_path / 'program.csv', tmp_path / 'comments.csv'
    assert generate_gx_works2_csv(ladder, program, comments, plc_model='FX3U')
    imported = parse_gxworks2_csv(program, comments)
    actual = [' '.join([row['op'], *row['args']])
              for network in imported.network_instructions for row in network]
    assert actual == expected


@pytest.mark.parametrize('partial', [False, True], ids=['new', 'edit'])
@pytest.mark.parametrize('output,expected', [
    ('MOV K-7 D10', 'MOV K-7 D10'),
    ('MOV H00FF D10', 'MOV H00FF D10'),
    ('MOV D0 D10', 'MOV D0 D10'),
    ('MOV K4M100 D10', 'MOV K4M100 D10'),
    ('MOV D0Z0 D10', 'MOV D0Z0 D10'),
    ('DMOV K123456 D10', 'DMOV K123456 D10'),
    ('ADD D0 K5 D10', 'ADD D0 K5 D10'),
    ('DADD D0 D2 D10', 'DADD D0 D2 D10'),
    ('BMOV D0 D100 K8', 'BMOV D0 D100 K8'),
    ('CMP D0 K10 M20', 'CMP D0 K10 M20'),
    ('ZRST M100 M115', 'ZRST M100 M115'),
    ('SFTL M100 M1000 K128 K1', 'SFTL M100 M1000 K128 K1'),
    ('INC D10', 'INC D10'),
    ('SET M10', 'SET M10'),
    ('RST M10', 'RST M10'),
    ('PLS M10', 'PLS M10'),
    ('PLF M10', 'PLF M10'),
    ('TIMER T0 K10', 'OUT T0 K10'),
    ('COUNTER C0 K3', 'OUT C0 K3'),
    ('OUT M10', 'OUT M10'),
    ('OUT T0 K10', 'OUT T0 K10'),
    ('OUT C0 K3', 'OUT C0 K3'),
])
def test_compact_operand_forms_survive_ir_and_csv(tmp_path, partial, output, expected):
    ladder = _expand_wire_case({'c': ['NO M0'], 'b': [{'i': [], 'o': [output]}]}, partial)
    _assert_wire_csv_instructions(tmp_path, ladder, ['LD M0', expected])


@pytest.mark.parametrize('partial', [False, True], ids=['new', 'edit'])
@pytest.mark.parametrize('row,expected', [
    pytest.param({'c': [], 'b': [{'i': [{'or': [['NO M0'], ['NC M1'], ['NO M2']]}], 'o': ['COIL M100']}]},
        ['LD M0', 'ORI M1', 'OR M2', 'OUT M100'], id='three-single-arms'),
    pytest.param({'c': [], 'b': [{'i': [{'or': [['NO M0', 'NC M1'], ['NO M2', '>= D0 K5']]}], 'o': ['COIL M100']}]},
        ['LD M0', 'ANI M1', 'LD M2', 'AND>= D0 K5', 'ORB', 'OUT M100'], id='series-arms-with-compare'),
    pytest.param({'c': [], 'b': [{'i': ['NO M0', {'or': [['NC M1'], ['NO M2', 'NO M3']]}, 'NC M4'], 'o': ['COIL M100']}]},
        ['LD M0', 'LDI M1', 'LD M2', 'AND M3', 'ORB', 'ANB', 'ANI M4', 'OUT M100'], id='series-around-parallel'),
    pytest.param({'c': [], 'b': [{'i': [{'or': [['NO M0'], ['NC M1']]}, {'or': [['NO M2'], ['NC M3']]}], 'o': ['MOV K1 D10', 'COIL M100']}]},
        ['LD M0', 'ORI M1', 'LD M2', 'ORI M3', 'ANB', 'MOV K1 D10', 'OUT M100'], id='two-parallel-blocks'),
    pytest.param({'c': ['NO M0', 'NC M1'], 'b': [
        {'i': ['NO M2'], 'o': ['MOV K-7 D10']},
        {'i': ['NC M3'], 'o': ['BMOV D20 D100 K8']},
        {'i': [], 'o': ['TIMER T0 K10', 'COIL M100']}]},
        ['LD M0', 'ANI M1', 'MPS', 'AND M2', 'MOV K-7 D10', 'MRD', 'ANI M3',
         'BMOV D20 D100 K8', 'MPP', 'OUT T0 K10', 'OUT M100'], id='shared-three-output-branches'),
    pytest.param({'c': ['NO M0'], 'b': [
        {'i': [{'or': [['NO M1', 'NC M2'], ['NO M3']]}], 'o': ['COIL M100']},
        {'i': ['>= D0 K5'], 'o': ['ADD D0 K1 D10']}]},
        ['LD M0', 'MPS', 'LD M1', 'ANI M2', 'OR M3', 'ANB', 'OUT M100',
         'MPP', 'AND>= D0 K5', 'ADD D0 K1 D10'], id='shared-input-and-parallel-branch'),
    pytest.param({'c': [], 'b': [{'i': ['P M0'], 'o': ['PLS M100']}, {'i': ['F M1'], 'o': ['PLF M101']}]},
        ['LDP M0', 'PLS M100', 'LDF M1', 'PLF M101'], id='independent-edge-branches'),
    pytest.param({'c': [], 'b': [{'i': [{'or': [['P M0'], ['F M1'], ['>= D0 K5']]}], 'o': ['COUNTER C0 K3']}]},
        ['LDP M0', 'ORF M1', 'OR>= D0 K5', 'OUT C0 K3'], id='edge-and-compare-parallel'),
    pytest.param({'c': ['NC M10'], 'b': [{'i': [{'or': [['M1'], ['M0']]}], 'o': ['COIL M100']}]},
        ['LDI M10', 'LD M1', 'OR M0', 'ANB', 'OUT M100'], id='bare-bit-contacts-in-or'),
])
def test_compact_parallel_topologies_preserve_instruction_order_and_csv(tmp_path, partial, row, expected):
    _assert_wire_csv_instructions(tmp_path, _expand_wire_case(row, partial), expected)


def test_bare_word_is_not_an_implicit_boolean_comparison():
    with pytest.raises(CompactProtocolError):
        _expand_wire_case({'c': ['D0'], 'b': [{'i': [], 'o': ['COIL Y0']}]}, partial=False)


def test_compact_parallel_boolean_paths_preserve_csv_roundtrip_behavior():
    import tempfile
    from pathlib import Path
    from hypothesis import given, settings, strategies as st
    from gxworks2.csv_export import generate_gx_works2_csv
    from gxworks2.csv_importer import parse_gxworks2_csv
    from simulator.bounded import ScanMachine

    @settings(max_examples=40, deadline=None)
    @given(arms=st.lists(st.lists(st.booleans(), min_size=1, max_size=4), min_size=2, max_size=6))
    def check(arms):
        # Each generated arm has a known conducting assignment. Independently
        # block every arm, then open each arm alone: catches AND/OR inversion,
        # missing branches and accidental reliance on a different branch.
        paths, conducting, blockers = [], {}, []
        index = 10
        for arm in arms:
            path = []
            for normally_open in arm:
                address = f'M{index}'
                index += 1
                path.append(('NO ' if normally_open else 'NC ') + address)
                conducting[address] = normally_open
            paths.append(path)
            blockers.append(path[0].split()[1])
        ladder = _expand_wire_case({'c': ['NO M0'], 'b': [
            {'i': [{'or': paths}, 'NC M1'], 'o': ['COIL M100']},
            {'i': ['NO M2'], 'o': ['COIL M101']}]}, partial=True)
        with tempfile.TemporaryDirectory() as directory:
            program, comments = Path(directory) / 'p.csv', Path(directory) / 'c.csv'
            assert generate_gx_works2_csv(ladder, program, comments, plc_model='FX3U')
            roundtrip = parse_gxworks2_csv(program, comments).ladder
        all_blocked = dict(conducting)
        for address in blockers:
            all_blocked[address] = not conducting[address]
        states = [(all_blocked, False), (conducting, True)]
        states.extend(({**all_blocked, address: conducting[address]}, True) for address in blockers)
        for candidate in (ladder, roundtrip):
            machine = ScanMachine(candidate, plc_model='FX3U', initial={}, execution_context={})
            for bits, enabled in states:
                for common, stop, second in [(True, False, False), (False, False, True), (True, True, True)]:
                    after = machine.scan({'inputs': {**bits, 'M0': common, 'M1': stop, 'M2': second}})['after']
                    assert bool(after['M100']) == (common and enabled and not stop)
                    assert bool(after['M101']) == (common and second)
            assert not machine.gaps
    check()


@pytest.mark.parametrize('mutation', ['missing_id', 'boolean_id', 'mixed_shapes', 'missing_outputs', 'duplicate_id'])
def test_compact_partial_rejects_ambiguous_or_invalid_edits(mutation):
    from application.compact_protocol import canonical_compact_example, expand_compact_partial
    from plc.candidate_repair import materialize_partial, RepairAssemblyError
    patch = canonical_compact_example(partial=True)
    if mutation == 'missing_id':
        patch['r'][0].pop('rung_id')
    elif mutation == 'boolean_id':
        patch['r'][0]['rung_id'] = True
    elif mutation == 'mixed_shapes':
        patch['rungs'] = _ladder()['rungs']
    elif mutation == 'missing_outputs':
        patch['r'][0]['b'][0]['o'] = []
    else:
        patch['r'].append(copy.deepcopy(patch['r'][0]))
    with pytest.raises((CompactProtocolError, RepairAssemblyError)):
        materialize_partial(_ladder(), expand_compact_partial(patch))


@pytest.mark.parametrize("notes", [[], ["采用 M0 作为内部状态，用户可主动调整。"]])
def test_direct_strict_envelope_produces_program_with_nonblocking_notes(monkeypatch, tmp_path, notes):
    import application.generation_agent as agent
    from application.compact_protocol import canonical_compact_example
    from jsonschema import Draft202012Validator
    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **k: "")
    selection = {"candidates": [{"method": "触点驱动", "tradeoff": "按扫描跟随输入",
                                "instruction_choices": [], "decision_basis": []},
                               {"method": "独立状态位", "tradeoff": "占用内部位",
                                "instruction_choices": [{"opcode": "SET", "role": "锁存状态"},
                                                        {"opcode": "RST", "role": "清除状态"}], "decision_basis": []}],
                 "selected": 0, "rationale": "输出只需跟随输入"}
    payload = {"result": {"decisions": selection, "program": canonical_compact_example(), "open_items": notes}}
    class Provider(OneShotProvider):
        def stream(self, request):
            self.requests.append(request)
            schema = request.options["response_format"]["json_schema"]["schema"]
            Draft202012Validator(schema).validate(payload)
            yield TextDelta(json.dumps(payload, ensure_ascii=False))
    provider = Provider()
    provider.profile = _profile_declaring_structured_output(["json_schema"])
    result = GenerationWorkflow(GenerationRequest("M0 控制 M1", direct_generation=True,
        model_name=provider.profile["model"]), tmp_path,
        dependencies=GenerationDependencies(provider=provider)).run()
    assert len(provider.requests) == 1
    assert result["generation_handoff"]["output_contract"]["transport"] == "json_schema"
    assert (tmp_path / result["artifacts"]["program_csv"]).is_file()
    assert result["generator_notes"] == {"source": "generator", "open_items": notes,
        "selection": selection,
        "decisions": ["触点驱动 (按扫描跟随输入)", "独立状态位 (占用内部位)", "触点驱动 (输出只需跟随输入)"]}
    assert result["generation_handoff"]["generator_notes"] == result["generator_notes"]
    assert result["validation"]["status"] == "candidate_ready"
    assert {"result.decisions.candidates.*.tradeoff", "result.decisions.rationale", "result.open_items.*"} <= set(
        provider.requests[0].response_contract.human_paths)


@pytest.mark.parametrize("header", ["NO M0", ["NO M0"]])
def test_direct_legacy_header_is_accepted_on_first_call_without_repair(monkeypatch, tmp_path, header):
    import application.generation_agent as agent
    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **k: "")
    class Provider(OneShotProvider):
        def stream(self, request):
            self.requests.append(request)
            yield TextDelta(json.dumps({"r": [
                {"h": header, "s": [], "b": [{"i": [], "o": [f"COIL M{n + 1}"]}]}
                for n in range(36)
            ]}))
    provider = Provider()
    result = GenerationWorkflow(GenerationRequest("M0 控制输出", direct_generation=True,
        model_name=provider.profile["model"]), tmp_path,
        dependencies=GenerationDependencies(provider=provider, preserve_rejected_candidate=True)).run()
    assert len(provider.requests) == 1 and result["repair_attempts"] == 0
    assert result["validation"]["status"] == "candidate_ready"
    assert (tmp_path / result["artifacts"]["program_csv"]).is_file()
    assert not (tmp_path / "repair_candidate.json").exists()
    if isinstance(header, list):
        assert len(result["generation_handoff"]["representation_normalization"]) == 36


@pytest.fixture(autouse=True)
def isolated_flag(monkeypatch):
    monkeypatch.delenv(examples.ENV_NAME, raising=False)


def test_default_is_off_and_does_not_build_examples(monkeypatch):
    def forbidden():
        raise AssertionError("disabled generation must not build example IR")

    monkeypatch.setattr(examples, "_ir_examples", forbidden)
    block = examples.prepare_construction_examples("FX3U")
    assert block.text == ""
    assert block.manifest()["requested"] is False
    assert block.manifest()["enabled"] is False
    assert block.manifest()["example_ids"] == []


@pytest.mark.parametrize("value", ["1", "true", "ON", " yes "])
def test_environment_opt_in_and_explicit_false_override(monkeypatch, value):
    monkeypatch.setenv(examples.ENV_NAME, value)
    assert examples.resolve_construction_examples() is True
    assert examples.resolve_construction_examples(False) is False


@pytest.mark.parametrize("value", ["", "0", "false", "OFF", " no "])
def test_environment_off_and_explicit_true_override(monkeypatch, value):
    monkeypatch.setenv(examples.ENV_NAME, value)
    assert examples.resolve_construction_examples() is False
    assert examples.resolve_construction_examples(True) is True


@pytest.mark.parametrize("value", ["false", "true", 0, 1, {}, []])
def test_explicit_option_does_not_coerce_strings_or_integers(value):
    with pytest.raises(TypeError):
        examples.resolve_construction_examples(value)


def test_invalid_environment_is_reported_not_silently_enabled(monkeypatch):
    monkeypatch.setenv(examples.ENV_NAME, "maybe")
    with pytest.raises(ValueError, match=examples.ENV_NAME):
        examples.resolve_construction_examples()
    assert examples.resolve_construction_examples(False) is False


def test_other_model_is_not_given_fx3u_examples():
    block = examples.prepare_construction_examples("FX5U", True)
    assert block.text == ""
    assert block.manifest()["requested"] is True
    assert block.manifest()["enabled"] is False
    assert block.manifest()["reason"] == "unsupported_model"


def test_all_examples_are_current_valid_ir_and_round_trip_exactly():
    items = examples.construction_examples_ir()
    assert len(items) == 8
    assert len({item["id"] for item in items}) == len(items)
    for item in items:
        program = item["program_ir"]
        assert program["kind"] == "plc_program_ir"
        assert program["schema_version"] == IR_SCHEMA_VERSION
        assert program["plc"]["cpu"] == "FX3U"
        validate_plc_ir(program)
        ladder = ir_to_ladder(program)
        compact = examples._compact_from_ir(program)
        validate_compact_structure(compact)
        projected = {"io_table": [
            {"address": address, "label": label}
            for address, label in ladder["device_comments"].items()
        ]}
        from plc.ir import lower_rung_instructions
        restored = expand_compact_ladder(compact, projected)
        assert restored["device_comments"] == ladder["device_comments"]
        assert [lower_rung_instructions(r) for r in restored["rungs"]] == [lower_rung_instructions(r) for r in ladder["rungs"]]
        assert not any(address.startswith(("SM", "SD", "M8", "D8")) and address != "M8002" for address in program["devices"])


@pytest.mark.parametrize("structure,expected", [("bit_state_machine", "timed_bit_sequence"),
                                                ("register_state_machine", "timed_register_sequence")])
def test_complex_state_plan_selects_its_process_skeleton_before_generic_edges(structure, expected):
    specification = {"selected_approach": {"generation_contract": {
        "required_structures": [structure, "edge_trigger", "hardware_counter"]}},
        "execution_semantics": [{"semantic": value} for value in ("FIRST_SCAN", "RISING_EDGE", "FALLING_EDGE")]}
    block = examples.prepare_construction_examples("FX3U", True, specification)
    assert block.example_ids == (expected,)
    assert block.route["primary_process_structures"] == [structure]
    assert "counter_reset_priority" not in block.text and "edge_and_level" not in block.text
    program = next(row["program_ir"] for row in examples.construction_examples_ir() if row["id"] == expected)
    codes = {f["code"] for f in program["analysis"]["findings"]}
    assert not codes.intersection({"REPEATED_GUARD_INVALIDATED", "TIMER_OUTPUT_FEEDBACK", "LATCH_WITHOUT_RESET"})


def test_ir_exports_and_manifests_cannot_mutate_cached_examples():
    routed_spec = {
        "selected_approach": {
            "generation_contract": {"required_structures": ["self_hold"]}
        }
    }
    before = examples.prepare_construction_examples("FX3U", True, routed_spec)
    exported = examples.construction_examples_ir()
    exported[0]["program_ir"]["networks"].clear()
    assert examples.construction_examples_ir()[0]["program_ir"]["networks"]
    manifest = before.manifest()
    manifest["example_ids"].clear()
    assert before.manifest()["example_ids"] == ["reset_dominant_hold"]
    assert before == examples.prepare_construction_examples("fx3u", True, routed_spec)
    assert before.manifest()["sha256"] == hashlib.sha256(before.text.encode()).hexdigest()


def _construction_spec():
    return {
        "summary": "X5 controls Y5",
        "io_table": [{"address": "X5", "kind": "X", "label": "input"},
                     {"address": "Y5", "kind": "Y", "label": "output"}],
        "parameters": [],
        "selected_approach": {
            "approach_id": "direct", "name": "direct",
            "generation_contract": {
                "required_structures": ["self_hold"], "forbidden_structures": [],
                "required_opcodes": ["OUT"], "forbidden_opcodes": [],
                "required_devices": ["X5", "Y5"], "forbidden_devices": [],
            },
        },
    }


def test_wire_renderer_changes_only_the_example_block_and_freezes_flag(monkeypatch):
    from application.generation_agent import _compact_wire_renderer, _COMPACT_PROTOCOL
    from application.compact_protocol import compact_capability_prompt
    from application.confirmed_generation_context import generation_execution_prompt
    from application.generation_wire import context_system_prompt, expand_context_document
    from plc.specification.provenance import SOURCE_PRECEDENCE

    spec, evidence, request = _construction_spec(), "\nEVIDENCE_CONTROL_SENTINEL\n", "Generate confirmed program"
    args = (spec, evidence, request, None, "", [])
    off_block = examples.prepare_construction_examples("FX3U", False, spec)
    on_block = examples.prepare_construction_examples("FX3U", True, spec)
    off = _compact_wire_renderer("FX3U", example_block=off_block)
    from plc.instruction_knowledge import programming_system_prompt
    expected = (_COMPACT_PROTOCOL + "\n\n" + programming_system_prompt() + "\n\n" + SOURCE_PRECEDENCE
                + generation_execution_prompt(spec, evidence_text=evidence, task_type="generate"))
    assert off(*args)['messages'][0]['content'] == context_system_prompt(expected)
    on = _compact_wire_renderer("FX3U", example_block=on_block)
    off_packet, on_packet = off(*args), on(*args)
    block = on_block
    assert block.example_ids == ("reset_dominant_hold",)
    assert on_packet['messages'][0] == off_packet['messages'][0]
    on_document = expand_context_document(json.loads(on_packet['messages'][1]['content']))
    off_document = expand_context_document(json.loads(off_packet['messages'][1]['content']))
    assert off_document['request'] == request and off_document['confirmed_spec'] == spec
    assert off_document['knowledge'] == [{'text': 'EVIDENCE_CONTROL_SENTINEL'}]
    assert on_document['examples'] == block.text and off_document['examples'] == ''
    on_document['examples'] = ''
    assert on_document == off_document


def test_actual_wire_keeps_rules_and_evidence_stable_across_project_values_and_key_order():
    from application.generation_agent import _compact_wire_renderer
    from application.generation_wire import expand_context_document
    spec = _construction_spec()
    other = copy.deepcopy(spec)
    other['summary'] = '另一份已确认规格，地址及参数另行给定'
    evidence = '\n# Technical evidence\nUNCHANGED_SOURCE_' * 300
    block = examples.prepare_construction_examples('FX3U', False, spec)
    render = _compact_wire_renderer('FX3U', example_block=block)
    first = render(spec, evidence, 'generate', None, '', [])['messages']
    second = render(other, evidence, 'generate', None, '', [])['messages']
    def document(messages):
        return expand_context_document(json.loads(messages[1]['content']))
    assert first[0] == second[0]
    assert document(first)['knowledge'] == document(second)['knowledge'] == [{'text': evidence.strip()}]
    assert document(first)['confirmed_spec'] == spec
    assert document(second)['confirmed_spec'] == other
    reversed_keys = dict(reversed(list(spec.items())))
    assert document(render(reversed_keys, evidence, 'generate', None, '', [])['messages']) == document(first)
    changed = render(spec, '\nCHANGED_SOURCE\n', 'generate', None, '', [])['messages']
    assert changed[0] == first[0] and document(changed)['knowledge'] == [{'text': 'CHANGED_SOURCE'}]


def test_actual_agent_request_budget_handoff_and_retrieval_are_isolated(monkeypatch):
    from application import generation_agent as agent
    from application import model_api as api
    from application.generation_wire import wire_sha256, wire_token_estimate
    from model_profile_fixtures import offline_runtime_profile

    specification = _construction_spec()
    before = copy.deepcopy(specification)
    calls, lookups, callbacks, results = [], [], [], []
    provider = SimpleNamespace(profile=offline_runtime_profile("offline-examples"))

    def knowledge(query, **kwargs):
        lookups.append((str(query), copy.deepcopy(kwargs.get("confirmed_context"))))
        return "\nEVIDENCE_CONTROL_SENTINEL\n"

    def request_model(messages, **kwargs):
        calls.append((copy.deepcopy(messages), kwargs.copy()))
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps({"r": [
            {"h": None, "s": [], "b": [{"i": ["NO X5"], "o": ["COIL Y5"]}]}
        ]})))

    monkeypatch.setattr(agent, "_build_knowledge_context", knowledge)
    monkeypatch.setattr(api, "request_model", request_model)
    for enabled in (False, True):
        with api.provider_scope(provider, model_name="offline-examples"):
            result = agent.generate_confirmed_ladder(
                specification, "FX3U", model_name="offline-examples",
                construction_examples=enabled, on_context=callbacks.append,
            )
        results.append(result)
        packet = {"messages": calls[-1][0]}
        handoff = result["generation_handoff"]
        assert handoff["wire_sha256"] == wire_sha256(packet)
        assert handoff["budget_report"]["compiled_budget_payload_tokens"] == wire_token_estimate(packet)
        assert handoff["construction_examples"]["enabled"] is enabled
        assert callbacks[-1] == handoff
        assert result["model_calls"] == 1
        assert calls[-1][1]["effort"] is None
        assert calls[-1][1]["max_retries"] == 0
        assert "Optional construction examples" not in json.dumps(handoff["construction_examples"])
    assert len(calls) == 2
    assert specification == before
    assert results[0]["ladder"] == results[1]["ladder"]
    assert lookups[0] == lookups[1]
    assert len(lookups) == 2
    assert "C0" not in lookups[0][0] and "compound_condition" not in lookups[0][0]
    off, on = copy.deepcopy(calls[0][0]), copy.deepcopy(calls[1][0])
    block = examples.prepare_construction_examples("FX3U", True, specification)
    assert block.example_ids == ("reset_dominant_hold",)
    from application.generation_wire import expand_context_document
    assert on[0] == off[0]
    on_document = expand_context_document(json.loads(on[1]['content']))
    off_document = expand_context_document(json.loads(off[1]['content']))
    assert on_document['examples'] == block.text and off_document['examples'] == ''
    on_document['examples'] = ''
    assert on_document == off_document
    assert calls[0][1] == calls[1][1]
    assert results[1]["generation_handoff"]["budget_report"]["compiled_budget_payload_tokens"] > results[0]["generation_handoff"]["budget_report"]["compiled_budget_payload_tokens"]



@pytest.mark.parametrize("enabled", [False, True])
def test_generation_workflow_forwards_explicit_construction_example_arm(monkeypatch, tmp_path, enabled):
    import application.generation_agent as agent_b
    import application.model_api as api

    captured = []

    def fake_generate(spec, plc_model="FX3U", **kwargs):
        captured.append(kwargs.get("construction_examples"))
        return {
            "ladder": _ladder(),
            "model_calls": 1,
            "generation_handoff": {
                "construction_examples": {"enabled": bool(kwargs.get("construction_examples"))}
            },
            "operation_binding": {"stage": "Core_operation_binding", "model_calls": 0,
                                  "receipts": [{"binding_mode": "native_read_parameter_rebinding"}]},
        }

    monkeypatch.setattr(agent_b, "generate_confirmed_ladder", fake_generate)
    provider = OneShotProvider()
    with api.provider_scope(provider, model_name="offline-one-shot"):
        metadata = GenerationWorkflow(
            GenerationRequest(
                "Generate confirmed program",
                confirmed_context=_spec(),
                plc_model="FX3U",
                model_name="offline-one-shot",
                construction_examples=enabled,
            ),
            tmp_path,
            dependencies=GenerationDependencies(provider=provider),
        ).run()

    assert captured == [enabled]
    assert metadata["generation_handoff"]["construction_examples"]["enabled"] is enabled
    assert metadata['first_pass_pipeline']['operation_binding']['receipts'][0]['binding_mode'] == 'native_read_parameter_rebinding'


@pytest.mark.parametrize("value", ["1", "false", 0, 1, {}, []])
def test_generation_request_rejects_non_boolean_construction_example_arm(value):
    with pytest.raises(TypeError, match="construction_examples"):
        GenerationRequest("Generate", construction_examples=value)



@pytest.mark.parametrize("value", [None, "1", "false", 0, 1, {}, []])
def test_generation_request_rejects_non_boolean_fresh_confirmed_mode(value):
    with pytest.raises(TypeError, match="fresh_confirmed_generation"):
        GenerationRequest("Generate", fresh_confirmed_generation=value)


def test_direct_instruction_lookup_preserves_tool_and_cache_prefix(monkeypatch, tmp_path):
    import application.generation_agent as agent
    import application.model_api as api
    from agent_runtime.messages import ToolCall, ToolResult
    from application.conversation_model import ModelTurn, model_turn_scope
    from model_runtime.provider import ToolCallEnd, _ProviderState, _chat_message

    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *args, **kwargs: "")
    profile = _profile_declaring_structured_output(["json_object"])
    profile["capabilityContract"]["capabilities"]["tools"] = {"status": "supported", "source": "manual"}

    origin = (profile["baseUrl"], profile["model"], "test")
    reasoning = "Provider-native reasoning for the instruction lookup."

    class LookupProvider:
        def __init__(self):
            self.profile = profile
            self.requests = []

        def stream(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                yield _ProviderState({"origin": origin, "message": {"reasoning_content": reasoning}})
                yield TextDelta("先查证加法指令。")
                yield ToolCallEnd(ToolCall("lookup1", "lookup_plc_instructions", '{"opcodes":["ADD","UNKNOWN_FORM"]}'))
                return
            tool = request.messages[-1]
            assert isinstance(tool, ToolResult)
            assert tool.call_id == "lookup1"
            assert tool.data["missing_operand_tables"] == ["UNKNOWN_FORM"]
            add = next(item for item in tool.data["knowledge"] if item.get("target") == "ADD")
            assert add["scope"]["models"] == ["FX3U"]
            parameters = add["value"]["parameter_tables"][0]["parameters"]
            assert [item["name"] for item in parameters] == ["S1", "S2", "D"]
            yield TextDelta(json.dumps({"result": {"decisions": [], "open_items": [],
                "program": {"r": [{"c": ["NO X0"], "b": [{"i": [], "o": ["ADD D0 D1 D2"]}]}],
                            "device_comments": []}}}))

    provider = LookupProvider()
    model = {"model": profile["model"]}
    turn = ModelTurn(tmp_path, (), model, profile, "zh-CN")
    with api.provider_scope(provider), model_turn_scope(turn):
        result = agent.generate_direct_ladder("X0 接通时把 D0 与 D1 相加写入 D2。", model_name=profile["model"])
    assert result["model_calls"] == 2
    assert result["generation_handoff"]["instruction_lookup"][0]["requested"] == ["ADD", "UNKNOWN_FORM"]
    initial, continued = provider.requests
    assert initial.tools == continued.tools and initial.options == continued.options
    assert continued.messages[:len(initial.messages)] == initial.messages
    turn.save("job_lookup")
    restored = ModelTurn(tmp_path, ("job_lookup",), model, profile, "zh-CN")
    assert restored.history[-3].tool_calls[0].id == "lookup1"
    assert _chat_message(restored.history[-3], origin)["reasoning_content"] == reasoning
    assert restored.history[-2].content == continued.messages[-1].content


def test_compact_references_bind_confirmed_levels_and_named_group_operands():
    from application.generation_agent import prepare_model_candidate

    spec = {"io_bindings": [{"binding_id": "stop", "kind": "X", "role": "stop",
                            "address": "X3", "active_level": 0}]}
    program = {"r": [{"c": [{"condition_ref": "stop", "when": "run_permit"}],
                     "b": [{"i": [], "o": [
                         {"form_ref": "FX3U:BMOV:3", "parameters": [
                             {"name": "N", "value": "K4"}, {"name": "D", "value": "D20"},
                             {"name": "S", "value": "D0"}]}]}]},
                    {"c": [{"condition_ref": "stop", "when": "active"}],
                     "b": [{"i": [], "o": ["RST M1"]}]}]}
    rungs = prepare_model_candidate(program, spec, "FX3U")["ladder"]["rungs"]
    assert rungs[0]["shared_inputs"] == [{"type": "NO", "address": "X3"}]
    assert rungs[0]["branches"][0]["outputs"] == [
        {"type": "APP_INSTR", "opcode": "BMOV", "operands": ["D0", "D20", "K4"]}]
    assert rungs[1]["shared_inputs"] == [{"type": "NC", "address": "X3"}]


@pytest.mark.parametrize("form_ref,value,reason", [
    ("Q02/Q02H:D+:2", "K70000", None),
    ("Q02/Q02H:D+:2", {"duration_ms": 7000, "tick_ms": 0.1}, None),
    ("Q02/Q02H:+:2", "K70000", "invalid_instruction_parameters"),
    ("Q02/Q02H:+:2", {"duration_ms": 7000, "tick_ms": 0.1}, "invalid_instruction_parameters"),
    ("Q02/Q02H:+:2", {"duration_ms": 7, "tick_ms": 2}, "invalid_instruction_parameters"),
    ("FX3U:DADD:2", "K1", "instruction_form_unresolved"),
    ("Q02/Q02H:UNKNOWN:2", "K1", "instruction_form_unresolved"),
])
def test_compact_instruction_reference_uses_only_target_width(form_ref, value, reason):
    from application.compact_bindings import CompactBindingError
    from application.compact_protocol import expand_compact_ladder

    program = {"r": [{"c": ["NO X0"], "b": [{"i": [], "o": [
        {"form_ref": form_ref, "parameters": [{"name": "S", "value": value}, {"name": "D", "value": "D0"}]}
    ]}]}]}
    if reason:
        with pytest.raises(CompactBindingError) as exc:
            expand_compact_ladder(program, plc_model="Q02/Q02H")
        assert exc.value.reason == reason
        if reason == 'invalid_instruction_parameters':
            assert exc.value.binding_diagnostic['actual']['parameter'] == 'S'
    else:
        outputs = expand_compact_ladder(program, plc_model="Q02/Q02H")["rungs"][0]["branches"][0]["outputs"]
        assert outputs == [{"type": "APP_INSTR", "opcode": "D+", "operands": ["K70000", "D0"]}]


def test_compact_condition_reference_cannot_resolve_unconfirmed_level_or_role():
    from application.compact_bindings import CompactBindingError
    from application.compact_protocol import expand_compact_ladder

    spec = {"io_bindings": [{"binding_id": "guard", "address": "X3", "kind": "X", "label": "停止按钮"}]}
    program = {"r": [{"c": [{"condition_ref": "guard", "when": "run_permit"}],
                     "b": [{"i": [], "o": ["COIL Y0"]}]}]}
    with pytest.raises(CompactBindingError) as exc:
        expand_compact_ladder(program, spec)
    assert exc.value.reason == "input_condition_unresolved"


def test_ordered_instructions_use_shared_literal_width_and_edit_location():
    from application.compact_bindings import CompactBindingError, CompactBindings, positional_form_issues
    from plc.ordered_source import validate_ordered_rung
    from plc.validation import PLCJsonValidationError

    cpu = 'Q02/Q02H'
    path = 'instruction_edits[2].instructions[1]'
    bindings = CompactBindings(cpu)
    parameters = [{'name': 'D', 'value': 'D0'}, {'name': 'S', 'value': 'K70000'}]
    with pytest.raises(CompactBindingError) as error:
        bindings.ordered_record({'form_ref': f'{cpu}:+:2', 'parameters': parameters}, path)
    diagnostic = error.value.binding_diagnostic
    assert diagnostic['path'] == 'content.instruction_edits.2.instructions.1'
    assert diagnostic['actual']['parameter'] == 'S' and diagnostic['actual']['operand_index'] == 0
    assert '16 bits' in diagnostic['actual']['detail']

    record = bindings.ordered_record({'form_ref': f'{cpu}:D+:2', 'parameters': parameters}, path)
    rung = {'rung_id': 1, 'ordered_instructions': [{'op': 'LD', 'args': ['X0']}, record]}
    validate_ordered_rung(rung, plc_model=cpu)
    with pytest.raises(PLCJsonValidationError) as ordered_error:
        validate_ordered_rung({**rung, 'ordered_instructions': [
            rung['ordered_instructions'][0], {**record, 'op': '+'}]}, plc_model=cpu)
    assert ordered_error.value.path == 'ordered_instructions[1].operands[0]'
    issue, = positional_form_issues({'rungs': [{'rung_id': 4, 'branches': [{'outputs': [
        {'type': 'APP_INSTR', 'opcode': '+', 'operands': record['args']},
    ]}]}]}, {}, bindings)
    assert issue['path'] == '$.rungs[0].branches[0].outputs[0].operands[0]'
    assert issue['rung_ids'] == [4]

    with pytest.raises(CompactBindingError) as address_error:
        CompactBindings('FX3U').ordered_record({'timer': 'T999', 'duration_ms': 600, 'tick_ms': 100}, path)
    assert address_error.value.reason == 'invalid_timer_address'
    assert address_error.value.binding_diagnostic['path'] == diagnostic['path']


def test_partial_ladder_resolves_conditions_and_exact_timer_durations():
    from application.compact_bindings import CompactBindingError, CompactBindings
    from application.compact_protocol import expand_compact_partial

    spec = {'io_bindings': [{'binding_id': 'stop', 'kind': 'X', 'role': 'stop',
                            'address': 'X3', 'active_level': 0}]}
    timer = {'timer': 'T0', 'duration_ms': 600, 'tick_ms': 100}
    program = {'mode': 'partial', 'r': [{'rung_id': 40, 'c': [{'condition_ref': 'stop', 'when': 'run_permit'}],
                                         'b': [{'i': [], 'o': [timer]}]}],
               'device_comments': [], 'delete_rung_ids': []}
    rung, = expand_compact_partial(program, spec)['rungs']
    assert rung['rung_id'] == 40
    assert rung['shared_inputs'] == [{'type': 'NO', 'address': 'X3'}]
    assert rung['branches'][0]['outputs'] == [{'type': 'TIMER', 'address': 'T0', 'value': 'K6'}]
    assert CompactBindings('FX3U', spec).ordered_record(
        {'op': 'AND', 'condition_ref': 'stop', 'when': 'active'}, 'source[0]') == {'op': 'ANI', 'args': ['X3']}
    timer['duration_ms'] = 650
    with pytest.raises(CompactBindingError) as inexact:
        expand_compact_partial(program, spec)
    assert inexact.value.reason == 'invalid_timer_duration'
    timer['tick_ms'] = 10
    with pytest.raises(CompactBindingError) as mismatch:
        expand_compact_partial(program, spec)
    assert mismatch.value.reason == 'timer_timebase_mismatch'
    assert mismatch.value.binding_diagnostic['expected']['tick_ms'] == 100

import json

import pytest

import application.generation_agent as agent
import application.model_api as api
from agent_runtime.messages import ToolCall, ToolResult
from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow
from application.ladder_model_session import DECISION, LOOKUP, PATCH, SUBMIT
from model_runtime.provider import TextDelta, ToolCallEnd, Usage


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _call(name, identity, value):
    return ToolCallEnd(ToolCall(identity, name, value if isinstance(value, str) else _json(value)))


class Provider:
    def __init__(self, responses, *, tools=True):
        self.requests = []
        self.responses = iter(responses)
        self.profile = {
            "adapter": "openai_compatible", "baseUrl": "https://api-inference.modelscope.cn/v1",
            "model": "deepseek-ai/DeepSeek-V4.1-Flash-1017" if tools else "offline",
        }

    def stream(self, request):
        self.requests.append(request)
        yield from next(self.responses)
        yield Usage(100, 30, 130, raw_usage={"prompt_cache_hit_tokens": 70})


def _rung(output, contact="NO X0", identity=None):
    return {"c": [contact], "b": [{"i": [], "o": [output]}],
            **({"rung_id": identity} if identity is not None else {})}


def _program(*rows):
    return {"r": list(rows), "device_comments": []}


def _partial(*rows):
    return {**_program(*rows), "mode": "partial", "delete_rung_ids": []}


def _decision():
    return {"decisions": {"candidates": [{"method": "加法后写入", "tradeoff": "单个结果字",
                                         "instruction_choices": [{"opcode": "ADD", "role": "计算结果"}],
                                         "decision_basis": []}],
                          "selected": 0, "rationale": "工艺只要求一次计算"},
            "open_items": []}


@pytest.fixture(autouse=True)
def local_context(monkeypatch):
    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **k: "")
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *a, **k: "")
    monkeypatch.setattr("application.gxw_delivery.installation", lambda: None)


@pytest.mark.parametrize('direct', [False, True])
@pytest.mark.parametrize('tools', [False, True])
def test_plain_json_uses_original_compact_protocol_and_tools_keep_bindings(direct, tools):
    from application.generation_wire import expand_context_document
    from jsonschema import Draft202012Validator

    program = _program(_rung('COIL Y0'))
    answer = {'result': {**_decision(), 'program': program}} if direct else program
    provider = Provider([[TextDelta(_json(answer))]], tools=tools)
    spec = {'summary': 'X0 控制 Y0', 'io_table': [
        {'address': 'X0', 'kind': 'X'}, {'address': 'Y0', 'kind': 'Y'}]}
    with api.provider_scope(provider, model_name=provider.profile['model']):
        result = (agent.generate_direct_ladder(spec['summary'], confirmed_spec=spec)
                  if direct else agent.generate_confirmed_ladder(spec))
    assert len(provider.requests) == 1
    request = provider.requests[0]
    payload = expand_context_document(json.loads(request.messages[-1].content))
    prompt = request.messages[0].content
    assert ('compact_ladder/1.4' if tools else 'compact_ladder/1.3') in prompt
    assert ('form_ref' in prompt) is tools
    assert ('condition_ref' in prompt) is tools
    assert '不自行补造或省略操作数' in prompt
    assert '每个连接的并联网络最多 24 行' in prompt
    assert '增加同一网络的 b 分支不能降低总高度' in prompt
    assert bool(request.tools) is tools
    schema = payload['output_schema']
    Draft202012Validator(schema).validate(answer)
    assert ('form_ref' in _json(schema)) is tools
    if not tools:
        assert not any(unit.get('kind') == 'ladder_instruction_form' for unit in payload['knowledge'])
    assert result['ladder']['rungs'] and result['model_calls'] == 1


def test_export_repair_receives_whole_network_and_branch_heights(tmp_path):
    from application.ladder_candidate_flow import LadderCandidateFlow
    from application.ladder_model_session import LadderModelSession
    from plc.candidate_service import CandidateService

    flow = LadderCandidateFlow(candidates=CandidateService(), output_dir=tmp_path,
        plc_model='FX3U', program_name='MAIN', revision=1, previous_ladder=None,
        acceptance_cases=([], {'status': 'not_configured'}))
    flow.bind_specification({})
    flow.assess(_program({'c': ['P M0'], 'b': [
        {'i': [], 'o': [f'COIL M{100 + index}' for index in range(start, end)]}
        for start, end in [(0, 10), (10, 21), (21, 31)]]}))
    session = LadderModelSession(phase='direct', lookup=None, candidate=flow)
    repair = session._repair()
    assert repair['allowed_rung_ids'] == [1]
    assert repair['baseline_format'] == 'compact_ladder/1.3'
    from application.compact_protocol import expand_compact_partial
    restored = expand_compact_partial({'mode': 'partial', 'r': repair['baseline_rungs'],
                                       'device_comments': [], 'delete_rung_ids': []})
    assert restored['rungs'] == flow.repair_base['rungs']
    assert repair['diagnostics'][0]['details'] == {
        'parallel_rows': 31, 'max_parallel_rows': 24,
        'branch_rows': [{'branch_id': 1, 'parallel_rows': 10},
                        {'branch_id': 2, 'parallel_rows': 11},
                        {'branch_id': 3, 'parallel_rows': 10}],
    }
    assert '增加同一网络内的输出分支不能降低总高度' in repair['diagnostics'][0]['actual']
    assert flow.rendered is None


def test_direct_keeps_program_when_separate_decision_is_invalid(tmp_path):
    from application.conversation_model import ModelTurn, model_turn_scope

    program = _program(_rung("ADD D0 D1 D2"))
    provider = Provider([
        [_call(SUBMIT, "program", {"program": program}), _call(DECISION, "bad-notes", '{"decisions":')],
        [_call(DECISION, "notes", _decision())],
    ])
    model = provider.profile["model"]
    turn = ModelTurn(tmp_path, (), {"model": model}, provider.profile, "zh-CN")
    with model_turn_scope(turn):
        result = GenerationWorkflow(GenerationRequest("X0 导通时计算 D0 加 D1 到 D2。", direct_generation=True,
                                                      model_name=model),
                                    tmp_path, dependencies=GenerationDependencies(provider=provider)).run()
    first, repair = provider.requests
    assert first.tools == repair.tools and first.options == repair.options
    assert first.messages == repair.messages[:len(first.messages)]
    checked = repair.messages[-2].data
    assert checked["status"] == "accepted"
    assert checked["checks"] == {"artifact_render": "passed", "behavior": "unverified", "native_compile": "not_run"}
    assert checked["program_size"]["steps"] == 9
    assert repair.messages[-1].data["repair"]["action"] == "decision"
    assert result["repair_attempts"] == 1
    assert result["generation_handoff"]["candidate_flow"]["transport"] == "tools"
    assert result["generation_handoff"]["candidate_flow"]["diagnostics"] == []
    assert result["generator_notes"]["selection"]["candidates"][0]["instruction_choices"] == [{"opcode": "ADD", "role": "计算结果"}]
    assert (tmp_path / result["artifacts"]["program_csv"]).is_file()
    turn.save("job_tool")
    saved = ModelTurn(tmp_path, ("job_tool",), {"model": model}, provider.profile, "zh-CN")
    assert isinstance(saved.history[-1], ToolResult) and saved.history[-1].call_id == "notes"


def test_direct_accepts_program_after_separate_decision_repair(tmp_path):
    provider = Provider([
        [_call(DECISION, "bad-notes", '{"decisions":')],
        [_call(DECISION, "notes", _decision())],
        [_call(SUBMIT, "program", {"program": _program(_rung("ADD D0 D1 D2"))})],
    ])
    result = GenerationWorkflow(GenerationRequest("X0 接通时计算 D0 加 D1 到 D2。", direct_generation=True,
                                                  model_name=provider.profile["model"]),
                                tmp_path, dependencies=GenerationDependencies(provider=provider)).run()
    assert result["repair_attempts"] == 1
    assert provider.requests[-1].messages[-1].data == {"status": "accepted", "next": SUBMIT}
    assert result["generation_handoff"]["candidate_flow"]["diagnostics"] == []


def test_direct_citation_repair_preserves_selection_and_program(tmp_path):
    from copy import deepcopy

    text = "X0 控制 Y0"
    original = _decision()
    selected = original["decisions"]["candidates"][0]
    selected["decision_basis"] = [{"variant": "直接输出", "premise": text, "status": "known",
        "conclusion": "applicable", "references": [{"source": "request", "ref": "", "quote": "不存在的引文"}]}]
    corrected = deepcopy(original)
    corrected["decisions"]["candidates"][0]["decision_basis"][0]["references"][0]["quote"] = text
    changed = deepcopy(corrected)
    changed["decisions"]["candidates"][0]["method"] = "改用另一个结构"
    patch = {"decision_repairs": [{"path": "/decisions/candidates/0/decision_basis/0",
        "value": {"references": corrected["decisions"]["candidates"][0]["decision_basis"][0]["references"]}}]}
    provider = Provider([
        [TextDelta(_json({"result": {**original, "program": _program(_rung("COIL Y0"))}}))],
        [TextDelta(_json(changed))],
        [TextDelta(_json(patch))],
    ], tools=False)
    result = GenerationWorkflow(GenerationRequest(text, direct_generation=True, model_name="offline"), tmp_path,
                                dependencies=GenerationDependencies(provider=provider)).run()
    repair = json.loads(provider.requests[-1].messages[-1].content)["candidate_repair"]
    assert repair["diagnostics"][0]["repair_violation"] == "decision_repair_changed_implementation"
    assert repair["decision_paths"] == [patch["decision_repairs"][0]["path"]]
    assert result["generator_notes"]["selection"] == corrected["decisions"]
    assert result["repair_attempts"] == 2


def test_tool_candidate_normalizes_shared_parallel_before_schema(tmp_path):
    row = _rung("COIL Y0", {"or": [["NO X0"], ["NO X1"]]})
    provider = Provider([[_call(SUBMIT, "program", {"program": _program(row)}),
                         _call(DECISION, "notes", _decision())]])
    result = GenerationWorkflow(GenerationRequest("X0 或 X1 接通时开启 Y0。", direct_generation=True,
                                                  model_name=provider.profile["model"]),
                                tmp_path, dependencies=GenerationDependencies(provider=provider)).run()
    assert len(provider.requests) == 1
    assert result["generation_handoff"]["representation_normalization"][0]["rule"] == "single_branch_shared_parallel"


def test_confirmed_tool_reports_observable_implementation_difference(tmp_path):
    from application.conversation_model import ModelTurn, model_turn_scope

    provider = Provider([[_call(SUBMIT, "program", {"program": _program(
        _rung("MOV D10 D20"), _rung("MOV D11 D21"), _rung("MOV D12 D22"),
    )})]])
    model = provider.profile["model"]
    spec = {"summary": "X0 导通时复制三个数据字", "selected_approach": {
        "approach_id": "block_copy", "name": "成组复制",
        "generation_guide": "使用块传送复制连续的三个字。",
        "instruction_choices": [{"opcode": "BMOV", "role": "一次复制连续三个字"}],
    }}
    turn = ModelTurn(tmp_path, (), {"model": model}, provider.profile, "zh-CN")
    with model_turn_scope(turn):
        result = GenerationWorkflow(
            GenerationRequest("按已确认方案生成", confirmed_context=spec, model_name=model),
            tmp_path, dependencies=GenerationDependencies(provider=provider),
        ).run()
    turn.save("selected_implementation")
    saved = ModelTurn(tmp_path, ("selected_implementation",), {"model": model}, provider.profile, "zh-CN")
    tool_result = next(message.data for message in saved.history
                       if isinstance(message, ToolResult) and message.call_id == "program")
    assert tool_result["status"] == "accepted"
    [finding] = tool_result["advisories"]
    assert finding["expected"] == [{"kind": "opcode", "value": "BMOV", "role": "一次复制连续三个字"}]
    assert finding["actual"]["instruction_counts"]["MOV"] == 3
    assert finding in result["maintainability_review"]["findings"]


def test_design_lookup_coverage_repair_confirm_generation_and_edit_share_history(tmp_path, monkeypatch):
    from application.workbench import WorkbenchService
    from plc.ir import ir_to_ladder

    text = "X0 接通时计算 D0 与 D1 之和。把结果写入 D2。"
    requirements = [{"id": "enable", "text": "X0 接通时计算 D0 与 D1 之和。"},
                    {"id": "result", "text": "把结果写入 D2。"}]
    choice = {"approach_id": "arithmetic", "name": "直接计算",
              "generation_guide": "X0 作为执行条件，结果存到 D2。",
              "output_logic": [{
                  "target": "求和结果", "state": "D2 保存最后一次求和结果。",
                  "activation": "X0 接通时开始更新。", "update": "执行一次字加法后写入 D2。",
                  "release": "X0 断开时停止写入，保留最后结果。",
                  "interruption": "依 X0 的执行条件处理。",
                  "selection": "直接执行字加法，固定一次计算，无需中间结果状态。",
              }],
              "implementation_semantics": [], "decision_basis": [
                  {"variant": "ADD", "premise": "当前型号有三参数原生形式", "status": "known",
                   "conclusion": "applicable", "references": [
                       {"source": "model_fact", "ref": "knowledge", "quote": "FX3U:ADD:3"}]}],
              "instruction_choices": [{"opcode": "ADD", "role": "把两个字相加写入 D2"}]}
    item = {"id": "main", "language": "ladder", "location": "MAIN", "method": "按使能计算并写结果",
            "state_owner": "D2", "requirement_ids": ["enable"]}
    analysis = {"summary": text, "approaches": [choice], "execution_intent_claims": [], "explicit_constraint_claims": [],
                "requirements": requirements, "implementation_items": [item]}
    coverage_patch = {"repairs": [{"path": "/implementation_items", "value": {"main": ["result"]}}]}
    fix = {"form_ref": "FX3U:ADD:3", "parameters": [
        {"name": "D", "value": "D2"}, {"name": "S2", "value": "D1"}, {"name": "S1", "value": "D0"}]}
    provider = Provider([
        [_call(LOOKUP, "查证", {"opcodes": ["ADD"]})],
        [TextDelta(_json(analysis))],
        [TextDelta(_json(coverage_patch))],
        [_call(SUBMIT, "候选", {"program": _program(_rung("ADD D0 D2"))})],
        [_call(PATCH, "修复", {"patch": _partial(_rung(fix, identity=1))})],
        [_call(SUBMIT, "修改", {"program": _partial(_rung("ADD D0 K2 D2", identity=1))})],
    ])
    model = provider.profile["model"]
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state",
                               model_factory=lambda: (provider, {"model": model})).start()
    try:
        project_id = service.create_project(name="Tool Design", plc_model="FX3U")["id"]
        cases = service.store.project_dir(project_id) / "acceptance-cases.json"
        acceptance = {"schema_version": 1, "project_id": project_id, "plc_model": "FX3U", "spec_revision": None,
                      "cases": [{"case_id": "add_case", "scan_ms": 10, "steps": [
                          {"at_ms": 0, "set": {"X0": 1, "D0": 0, "D1": 2}},
                          {"at_ms": 10, "expect": {"D2": 2}}]}]}
        cases.write_text(_json(acceptance), encoding="utf-8")
        base = {"project_id": project_id, "response_language": "zh-CN"}
        analysis_job = service.submit({**base, "kind": "analysis", "request_id": "analysis",
                                       "analysis_mode": "design", "text": text, "start_conversation": True})
        status = service.jobs.wait(analysis_job["id"], timeout=30)
        assert status["status"] == "completed", status
        assert status["model_usage"]["tokens"] == {"input_tokens": 300, "output_tokens": 90, "cached_input_tokens": 210}
        commands = service.project_commands
        draft = commands.get_spec_document(project_id)
        assert draft["draft"]["implementation_items"][0]["requirement_ids"] == ["enable", "result"]
        draft = commands.update_spec_document(project_id, [{"section": "selected_approach", "value": draft["draft"]["approaches"][0]}],
                                              draft["draft_revision"])
        confirmed = commands.confirm_spec_document(project_id, draft["draft_revision"], allow_selected_approach=True)
        from application.spec_document import project_acceptance_cases
        assert project_acceptance_cases(service.projects.tool_context(project_id).project, "FX3U") == (
            None, "acceptance_spec_revision_mismatch")
        acceptance["spec_revision"] = confirmed["base_revision"]
        cases.write_text(_json(acceptance), encoding="utf-8")
        selected = confirmed["confirmed_spec"]["selected_approach"]
        assert selected["instruction_choices"] == choice["instruction_choices"]
        assert selected["decision_basis"] == choice["decision_basis"]
        assert selected["output_logic"] == choice["output_logic"]
        assert not selected["generation_contract"]["required_opcodes"]
        continued = {**base, "kind": "generation", "conversation_id": analysis_job["id"]}
        job = service.submit({**continued, "request_id": "generate", "text": "按确认方案生成"})
        generated = service.jobs.wait(job["id"], timeout=30)
        assert generated["status"] == "completed", generated
        version_id = generated["result"]["version_id"]
        version = service.store.get_version(project_id, version_id)
        assert version["generation_handoff"]["candidate_flow"]["repair_attempts"] == 1
        assert version["behavior_result"]["status"] == "passed_in_cases"
        acceptance["cases"][0]["steps"][0]["set"]["D0"] = 1
        acceptance["cases"][0]["steps"][1]["expect"]["D2"] = 3
        cases.write_text(_json(acceptance), encoding="utf-8")
        edited_job = service.submit({**continued, "request_id": "edit", "version_id": version_id,
                                     "generation_action": "edit", "text": "改成把 D0 加 2 写入 D2。"})
        edited = service.jobs.wait(edited_job["id"], timeout=30)
        assert edited["status"] == "completed", edited
        assert service.store.get_version(project_id, edited["result"]["version_id"])["behavior_result"]["status"] == "passed_in_cases"
        final = ir_to_ladder(service.projects.program(project_id, edited["result"]["version_id"]))
        assert final["rungs"][0]["branches"][0]["outputs"][0]["operands"] == ["D0", "K2", "D2"]
        assert service.projects.project(project_id)["version_count"] == 2
        first, looked_up, repaired, generation, corrected, edit = provider.requests
        assert '"case_id":"add_case"' in first.messages[0].content
        assert sum('"case_id":"add_case"' in message.content for message in edit.messages) == 1
        assert all(request.tools == first.tools for request in provider.requests)
        assert looked_up.messages[:len(first.messages)] == first.messages
        assert repaired.messages[:len(looked_up.messages)] == looked_up.messages
        assert generation.messages[:len(repaired.messages)] == repaired.messages
        assert corrected.messages[:len(generation.messages)] == generation.messages
        assert edit.messages[:len(corrected.messages)] == corrected.messages
        continuation = next(message.content for message in reversed(edit.messages)
                            if message.content.startswith("# GXWorks continuation\n"))
        changes = json.loads(continuation.split("\n", 1)[1])["rule_changes"]["set"]
        assert {k: v for k, v in changes.items() if k.startswith("/Project acceptance cases")} == {
            "/Project acceptance cases/0/steps/0/set/D0": 1,
            "/Project acceptance cases/0/steps/1/expect/D2": 3,
        }
        assert "FX3U:ADD:3" in looked_up.messages[-1].content
        assert sum('"kind":"ladder_instruction_form"' in message.content for message in generation.messages) == 1
        assert "<svg" not in "".join(message.content for message in edit.messages)
    finally:
        service.close()


def test_json_repair_reuses_confirmed_behavior_cases_and_stops_at_two(tmp_path, monkeypatch):
    cases = tmp_path / "cases.json"
    cases.write_text(_json({"schema_version": 1, "project_id": "project", "plc_model": "FX3U",
                            "cases": [{"case_id": "start", "scan_ms": 10, "steps": [
                                {"at_ms": 0, "set": {"X0": 1}, "expect": {"Y0": 1}}, {"at_ms": 10}]}]}), encoding="utf-8")
    monkeypatch.setenv("GXWORKS_ACCEPTANCE_CASES_FILE", str(cases))
    metadata_calls = []

    def native(_output, metadata):
        metadata_calls.append(metadata)
        metadata["validation"].update(gx_compile="failed", native_compile_findings=[
            {"severity": "error", "code": "0x00000042", "rung_ids": [1], "device": "Y0"}])

    monkeypatch.setattr("application.gxw_delivery.finalize_saved", native)
    provider = Provider([
        [TextDelta(_json({"result": {"program": _program(_rung("COIL Y0", "NC X0"))}}))],
        [TextDelta(_json(_partial(_rung("COIL Y0", identity=1))))],
        [TextDelta(_json(_partial(_rung("COIL Y0", identity=1))))],
    ], tools=False)
    result = GenerationWorkflow(GenerationRequest("X0 控制 Y0", direct_generation=True, project_id="project",
                                                  model_name="offline"),
                                tmp_path, dependencies=GenerationDependencies(provider=provider, native_delivery=True)).run()
    first, behavior, compile_error = provider.requests
    assert '"case_id":"start"' in first.messages[0].content
    assert not first.tools and first.messages == behavior.messages[:len(first.messages)]
    assert behavior.messages == compile_error.messages[:len(behavior.messages)]
    mismatch = json.loads(behavior.messages[-1].content)["candidate_repair"]["diagnostics"][0]
    assert (mismatch["expected"], mismatch["actual"], mismatch["rung_ids"]) == (1, False, [1])
    assert mismatch["input_sequence"] == [{"at_ms": 0, "set": {"X0": 1}}]
    assert len(metadata_calls) == 1
    assert result["behavior_result"]["status"] == "passed_in_cases"
    assert result["repair_attempts"] == 2
    assert result["generation_handoff"]["candidate_flow"]["transport"] == "json"
    assert result["activation_blocked"] and result["validation"]["gx_compile"] == "failed"
    assert (tmp_path / result["artifacts"]["json"]).is_file()


def test_json_format_repair_uses_local_substitutions_in_same_request_history(tmp_path):
    import hashlib

    original = _json({"result": {"program": _program(_rung("COIL Y0"))}})
    before, after = '["NO X0"]"b"', '["NO X0"],"b"'
    broken = original.replace(after, before, 1)
    patch = {"schema_version": 1, "mode": "format_patch",
             "base_sha256": hashlib.sha256(broken.encode()).hexdigest(),
             "patches": [{"before": before, "after": after}]}
    provider = Provider([[TextDelta(broken)], [TextDelta(_json(patch))]], tools=False)
    result = GenerationWorkflow(GenerationRequest("X0 控制 Y0", direct_generation=True, model_name="offline"), tmp_path,
                                dependencies=GenerationDependencies(provider=provider)).run()
    first, repair = provider.requests
    assert first.messages == repair.messages[:len(first.messages)]
    assert repair.messages[-2].content == broken and broken not in repair.messages[-1].content
    assert json.loads(repair.messages[-1].content)["candidate_repair"]["action"] == "format_patch"
    assert result["repair_attempts"] == 1
    ladder = json.loads((tmp_path / result["artifacts"]["json"]).read_text(encoding="utf-8"))
    assert ladder["rungs"][0]["branches"][0]["outputs"] == [{"type": "COIL", "address": "Y0"}]


def test_operator_case_uses_explicit_clock_phase():
    from simulator.io_example import check_io_example

    ladder = agent.prepare_model_candidate(_program(_rung("COIL Y0", "NO M8012")), {}, "FX3U")["ladder"]
    example = {"scan_ms": 10, "steps": [{"at_ms": at, "expect": {"Y0": level}}
                                       for at, level in ((0, False), (50, True), (100, False))]}
    assert check_io_example(ladder, "FX3U", example)["status"] == "unverified"
    assert check_io_example(ladder, "FX3U", example,
                            operator_context={"clock_phases_ms": {"M8012": 0}})["status"] == "passed_in_example"


def test_acceptance_restarts_latched_state_for_each_case():
    from simulator.io_example import check_independent_acceptance

    program = _program(_rung("SET M0"), _rung("COIL Y0", "NO M0"))
    ladder = agent.prepare_model_candidate(program, {}, "FX3U")["ladder"]
    cases = [{"case_id": case_id, "scan_ms": 5, "steps": [
        {"at_ms": 0, "set": {"X0": level}}, {"at_ms": 5, "expect": {"Y0": level}}]}
        for case_id, level in (("latches", 1), ("fresh_start", 0))]
    result = check_independent_acceptance(ladder, "FX3U", project_id=None, loaded_cases=(cases, None))
    assert result["status"] == "passed_in_cases"
    assert [case["status"] for case in result["cases"]] == ["passed_in_example", "passed_in_example"]


@pytest.mark.parametrize("tools,invalid", [(False, "rung"), (True, "address"), (False, "syntax")])
def test_invalid_local_patch_uses_remaining_budget_with_specific_feedback(tmp_path, tools, invalid):
    cases = [{"case_id": "start", "scan_ms": 5, "steps": [
        {"at_ms": 0, "set": {"X0": 1}}, {"at_ms": 5, "expect": {"Y0": 1}}]}]
    program = _program(_rung("COIL Y0", "NC X0"))
    patch = _partial(_rung("COIL Y0", identity=1))
    bad = {"rung": _partial(_rung("COIL Y0", identity=3)),
           "address": _partial(_rung("COIL Y0", contact="NO M97", identity=1)),
           "syntax": "{"}[invalid]
    provider = Provider([
        [_call(SUBMIT, "program", {"program": program}), _call(DECISION, "notes", _decision())] if tools else
        [TextDelta(_json({"result": {"program": program, **_decision()}}))],
        [_call(PATCH, "bad-patch", {"patch": bad})] if tools else [TextDelta(bad if isinstance(bad, str) else _json(bad))],
        [_call(PATCH, "patch", {"patch": patch})] if tools else [TextDelta(_json(patch))],
    ], tools=tools)
    result = GenerationWorkflow(GenerationRequest("X0 控制 Y0", direct_generation=True,
                                model_name=provider.profile["model"], acceptance_cases=(cases, None)), tmp_path,
                                dependencies=GenerationDependencies(provider=provider)).run()
    last = provider.requests[2].messages[-1]
    repair = last.data["repair"] if tools else json.loads(last.content)["candidate_repair"]
    feedback = repair["rejected_patch"]
    if invalid == "syntax":
        assert feedback["reason"] == "invalid_json_object"
    elif invalid == "rung":
        assert feedback["submitted_rung_ids"] == [3] and feedback["allowed_rung_ids"] == [1]
    else:
        assert feedback["submitted_addresses"] == ["M97"] and set(feedback["allowed_addresses"]) == {"X0", "Y0"}
    assert result["behavior_result"]["status"] == "passed_in_cases"
    assert result["repair_attempts"] == 2 and len(provider.requests) == 3

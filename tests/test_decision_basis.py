import copy
import json

from application.analysis_response import _parse_analysis_response
from application.confirmed_generation_context import project_confirmed_specification
from plc.specification.confirmed import build_review_draft
from plc.specification.decision_basis import DecisionSources, review_decision_basis


def test_fact_reference_uses_a_stable_id_and_local_record():
    from plc.specification.fact_citations import cite_units

    scope = {"model": "FX3U", "language": "ladder", "version": "library-v1"}
    source = {"kind": "instruction", "text": "Published argument order and conditions."}
    [unit], catalog = cite_units([source], **scope)
    other, _ = cite_units([{"text": "Other fact"}, source], **scope, path="/results/2/knowledge")
    identifier = unit["citation_id"]
    assert other[1]["citation_id"] == identifier
    [changed], _ = cite_units([source], **{**scope, "version": "library-v2"})
    assert changed["citation_id"] != identifier

    reference = {"source": "model_fact", "ref": identifier}
    sources = DecisionSources(citations=catalog)
    assert sources.resolve(reference)["quote"]
    assert sources.resolve({**reference, "quote": "fabricated"}) is None
    assert DecisionSources().resolve(reference) is None
    assert catalog[identifier]["path"] == "/knowledge/0"


def test_merged_instruction_citation_keeps_only_its_contributing_sources():
    from knowledge.evidence import KnowledgeContext, evidence_record
    from plc.specification.fact_citations import cite_units
    from shared.model_context import project_knowledge_context, technical_context_units, technical_knowledge_text

    records = []
    for opcode, family, model, page in [('MOV', 'MOV', 'FX3U', 10), ('DMOV', 'MOV', 'FX3U', 11),
                                       ('MOV', 'MOV', 'Q03UDE', 22)]:
        unit = {'kind': 'instruction', 'target': opcode, 'scope': {'models': [model]},
                'value': {'family': family, 'forms': [{'opcode': opcode}]}}
        row = {'id': f'{opcode}-{model}', 'manual_id': f'{model}-manual', 'revision': 'M', 'pdf_page': page,
               'text': json.dumps(unit)}
        records.append({**evidence_record(row, include_origin=True), 'model_text': technical_knowledge_text(row)})
    context = KnowledgeContext('\n'.join(row['model_text'] for row in records), {'records': records})
    context = project_knowledge_context(project_knowledge_context(context), cpu_version=False)
    origins = {}
    units = technical_context_units(context, origins=origins)
    units, citations = cite_units(units, model='FX3U', language='ladder', version='catalog-v1', origins=origins)
    fx, = [unit for unit in units if unit['scope']['models'] == ['FX3U']]
    assert {form['opcode'] for form in fx['value']['forms']} == {'MOV', 'DMOV'}
    witnesses = citations[fx['citation_id']]['sources']
    assert {row['pdf_page'] for row in witnesses} == {10, 11}
    assert {row['source_text_status'] for row in witnesses} == {'not_in_record'}
    assert 'manual_id' not in json.dumps(units)


def test_parent_citation_repair_keeps_child_patch_and_immutable_text():
    import pytest
    from application.analysis_repair import apply_analysis_repair, plan_analysis_repair
    from application.analysis_results import AnalysisProtocolError

    request = "Process the next item on the input edge."
    decision = {"variant": "Edge", "premise": "Detect the input edge.", "status": "known",
                "conclusion": "applicable", "references": [{"source": "request", "ref": "", "quote": request}],
                "applicability_conditions": [{"premise": "Input stays high for two scans.", "status": "known",
                                               "references": []}]}
    approach = {"approach_id": "edge", "name": "Edge", "description": "Capture one event.",
                "implementation_semantics": [], "decision_basis": [decision]}
    plan = plan_analysis_repair({"summary": request, "approaches": [approach]}, request,
                                decision_sources=DecisionSources(request))
    row = "/approaches/0/decision_basis/0"
    condition = row + "/applicability_conditions/0"
    assert {(target["path"], target["mode"]) for target in plan["targets"]} == {
        (row, "decision_citation"), (condition, "decision_citation")}
    patches = [{"path": row, "value": {"conclusion": "conditional"}},
               {"path": condition, "value": {"status": "unknown"}}]
    result, _ = apply_analysis_repair(plan, {"repairs": patches}, request)
    updated = result["approaches"][0]["decision_basis"][0]
    assert updated["variant"] == "Edge" and updated["premise"] == decision["premise"]
    assert updated["conclusion"] == "conditional"
    assert updated["applicability_conditions"][0] == {**decision["applicability_conditions"][0], "status": "unknown"}
    patches[0]["value"]["variant"] = "Replace the design"
    with pytest.raises(AnalysisProtocolError, match="immutable"):
        apply_analysis_repair(plan, {"repairs": patches}, request)
from plc.specification.provenance import build_confirmed_spec, seal_confirmation


def _basis(variant, premise, source, ref, quote, conclusion="applicable"):
    return {
        "variant": variant, "premise": premise, "status": "known", "conclusion": conclusion,
        "references": [{"source": source, "ref": ref, "quote": quote}],
    }


def test_decision_basis_distinguishes_known_conditions_from_unanswered_questions_and_missing_facts():
    request = "X0 按下时把一批连续寄存器归档。"
    spec = {
        "parameters": [{"id": "batch_size", "value": "", "options": ["16"], "suggested_default": "16"}],
        "io_bindings": [{"address": "X0", "active_level": 1}],
        "selected_approach": {"description": "假设每批有 16 个数据。"},
    }
    sources = DecisionSources(request, spec, {"knowledge": "按连续字块复制，支持重叠范围。"})
    rows = [
        _basis("块复制", "归档的是连续寄存器", "request", "", "一批连续寄存器归档"),
        _basis("高电平触点", "X0 高电平有效", "confirmed_spec", "/io_bindings/0/active_level", "1"),
        _basis("重叠范围复制", "资料说明支持重叠范围", "model_fact", "knowledge", "支持重叠范围"),
        _basis("批量归档", "每批固定 16 个字", "answer", "batch_size", "16"),
        _basis("索引循环", "型号不支持变址", "model_fact", "knowledge", "不支持变址", "excluded"),
        {**_basis("定长块复制", "批量是否固定尚未提供", "request", "", "一批连续寄存器归档", "excluded"),
         "status": "unknown", "references": []},
        _basis("逐项复制", "每批固定 16 个字", "confirmed_spec", "/selected_approach/description",
               "假设每批有 16 个数据。"),
    ]

    reviewed, issues = review_decision_basis(rows, path="decision_basis", reference_matches=sources.matches)

    assert reviewed[:3] == rows[:3]
    assert [(row["status"], row["conclusion"], row["references"]) for row in reviewed[3:]] == [
        ("unknown", "conditional", []) for _ in range(4)
    ]
    assert {item["path"] for item in issues} == {f"decision_basis[{index}]" for index in range(3, 7)}
    level = rows[1]["references"][0]
    assert sources.matches({**level, "ref": "/confirmed_spec" + level["ref"]})
    assert not sources.matches({**level, "ref": "/confirmed_spec/io_bindings/1/active_level"})
    assert level in json.loads(sources.reference_prompt().split("\n", 3)[-1])


def test_confirming_and_revising_parameters_preserves_proposed_basis_as_metadata():
    request = "每批采样完成后归档寄存器。"
    previous = {
        "plc_model": "FX3U", "parameters": [{"id": "batch_size", "name": "每批字数", "value": "8"}],
    }
    known = _basis("固定宽度块复制", "已确认每批 8 个字", "answer", "batch_size", "8")
    unknown = {"variant": "块复制到第二个区域", "premise": "目标区地址待定",
               "status": "unknown", "conclusion": "conditional", "references": [],
               "applicability_conditions": [{"premise": "目标地址连续", "status": "unknown", "references": []}]}
    approach = {"approach_id": "archive", "name": "按批归档", "description": "归档时写连续数据区。",
                "implementation_semantics": [], "decision_basis": [known, unknown],
                "instruction_choices": [{"opcode": "BMOV", "role": "保存本批数据"}]}
    raw = {"summary": "完成后归档", "approaches": [approach], "assumptions": ["兼容旧备注"]}
    analysis = _parse_analysis_response(json.dumps(raw, ensure_ascii=False), user_text=request,
                                        confirmed_spec=previous, decision_sources=DecisionSources(request, previous))
    assert analysis["assumptions"] == ["兼容旧备注"]
    draft = build_review_draft(analysis, previous)
    confirmed, receipt = seal_confirmation(draft)
    assert confirmed["selected_approach"]["decision_basis"] == [known, unknown]
    assert project_confirmed_specification(confirmed)["selected_approach"]["decision_basis"] == [known, unknown]
    assert confirmed["selected_approach"]["generation_contract"]["required_opcodes"] == []

    amended = copy.deepcopy(confirmed)
    amended["parameters"][0]["value"] = "16"
    updated, _ = seal_confirmation(amended, previous_receipt=receipt)
    basis = updated["selected_approach"]["decision_basis"]
    assert basis == [{**known, "status": "unknown", "conclusion": "conditional", "references": []}, unknown]
    assert receipt["proposals"][0]["proposal"]["decision_basis"] == [known, unknown]
    assert not DecisionSources(confirmed_spec=updated).matches(known["references"][0])
    assert DecisionSources(confirmed_spec=updated).matches({**known["references"][0], "quote": "16"})

    amended["selected_approach"].pop("decision_basis")
    assert "decision_basis" not in build_confirmed_spec(amended)["selected_approach"]


def test_confirmed_notes_allow_verbatim_passages_and_offer_repair_sources():
    notes = "每批数据独立归档。暂停后保留尚未完成的记录。"
    sources = DecisionSources(confirmed_spec={"user_notes": notes})
    ref = {"source": "confirmed_spec", "ref": "/user_notes", "quote": "暂停后保留尚未完成的记录"}
    assert sources.matches(ref)
    assert not sources.matches({**ref, "quote": "暂停后清空尚未完成的记录"})
    assert notes in sources.reference_prompt()
def test_rule_does_not_confirm_its_unknown_application_conditions():
    sources = DecisionSources('按批保存数据', model_facts={
        'knowledge': '此模板只复制固定长度连续区。'})
    row = _basis('固定长度复制模板', '模板需要固定长度', 'model_fact', 'knowledge',
                 '此模板只复制固定长度连续区。', 'excluded')
    row['applicability_conditions'] = [
        {'premise': '现场每批长度变化', 'status': 'unknown', 'references': []}]
    reviewed, issues = review_decision_basis([row], path='basis', reference_matches=sources.matches)
    assert reviewed[0]['status'] == 'known'  # The rule remains true.
    assert reviewed[0]['conclusion'] == 'conditional'
    assert issues[0]['code'] == 'unconfirmed_applicability_condition'

    # Even marking the missing condition known cannot manufacture its source.
    row['applicability_conditions'][0]['status'] = 'known'
    reviewed, issues = review_decision_basis([row], path='basis', reference_matches=sources.matches)
    assert reviewed[0]['applicability_conditions'][0]['status'] == 'unknown'
    assert reviewed[0]['conclusion'] == 'conditional'

    # An explicit user answer can settle the condition, and changing that
    # answer invalidates it without rewriting the underlying instruction fact.
    row['applicability_conditions'][0]['references'] = [
        {'source': 'answer', 'ref': 'length_changes', 'quote': '是'}]
    for answer, expected in [('是', 'excluded'), ('否', 'conditional')]:
        sources = DecisionSources('按批保存数据', model_facts=sources.model_facts,
                                  answers={'length_changes': answer})
        reviewed, _ = review_decision_basis([row], path='basis', reference_matches=sources.matches)
        assert reviewed[0]['conclusion'] == expected


def test_direct_keeps_program_and_reports_conditional_selection():
    from application.generation_agent import direct_response_parts
    sources = DecisionSources('批量归档', model_facts={'knowledge': '本构造使用固定大小缓冲区。'})
    row = _basis('固定缓冲区', '固定大小构造', 'model_fact', 'knowledge', '本构造使用固定大小缓冲区。')
    row['applicability_conditions'] = [{'premise': '数据量不超过缓冲区', 'status': 'inferred', 'references': []}]
    program = {'r': [{'c': ['NO X0'], 'b': [{'o': ['SET M0']}]}]}
    result, notes = direct_response_parts({'program': program, 'decisions': {
        'candidates': [{'method': '按批保存', 'tradeoff': '固定容量是实现选择', 'decision_basis': [row]}],
        'selected': 0, 'rationale': '保留各批数据'}, 'open_items': []}, decision_sources=sources)
    assert result == program
    assert notes['selection']['candidates'][0]['decision_basis'][0]['conclusion'] == 'conditional'
    assert notes['decision_basis_issues'][0]['code'] == 'unconfirmed_applicability_condition'



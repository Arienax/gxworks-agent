from pathlib import Path
import json
R=Path.cwd()
def edit(name,fn):
 p=R/name;s=p.read_text();t=fn(s);assert s!=t,name;p.write_text(t)
def sub(s,a,b):
 assert a in s,a[:100];return s.replace(a,b)
def function(s,name,fn):
 a=s.index('def '+name+'(');b=s.find('\ndef ',a+4);b=len(s) if b==-1 else b
 return s[:a]+fn(s[a:b])+s[b:]
def measurement(s):
 s=sub(s,'import pytest\n','import pytest\n\nfrom model_profile_fixtures import offline_runtime_profile\n')
 s=sub(s,'profile = {"id": "offline", "adapter": "openai_compatible", "model": "offline", "capabilities": {"structured_output": True}}','profile = offline_runtime_profile()')
 s=sub(s,'assert record["behavior"]["status"] == "verified" and record["structural_valid"]','assert record["behavior"] == {"status": "not_covered", "reason": "no_behavior_evaluator"}\n    assert record["structural_valid"] and record["semantic_validation"]["legacy_compatibility"] is True')
 a=s.index('def test_dry_run')
 return s[:a]+'''@pytest.mark.parametrize("behavior_status", ["verified", "failed", "not_covered"])
def test_benchmark_keeps_semantic_receipts_separate_from_behavior(monkeypatch, behavior_status):
    from test_generation_agent_boundary import OneShotProvider, _spec
    import application.generation_agent as agent
    monkeypatch.setattr(agent, "_build_knowledge_context", lambda *a, **k: "")
    specification = _spec()
    specification["selected_approach"] = {
        "approach_id": "direct", "name": "direct",
        "implementation_semantics": [{"kind": "structure", "status": "required", "value": "direct_logic"}],
        "explicit_user_constraints": {"required_opcodes": ["MOV"]},
    }
    inspected = []
    def evaluate(case, result):
        inspected.append(result["ladder"])
        return {"status": behavior_status, "source": "offline_evaluator"}
    provider = OneShotProvider()
    record = run_case({"case_id": "semantic-receipt", "confirmed_spec": specification},
                      "automatic", provider=provider, evaluator=evaluate)
    assert record["generation_status"] == "completed", record
    assert record["structural_valid"]
    assert record["semantic_validation"]["status"] == "violated"
    assert record["semantic_validation"]["violations"]
    assert record["behavior"] == {"status": behavior_status, "source": "offline_evaluator"}
    assert len(inspected) == len(provider.requests) == record["model_calls"] == 1


'''+s[a:]
edit('tests/test_agent_b_measurement.py',measurement)
edit('tests/test_model_runtime_profile.py',lambda s:sub(s,'capabilities["disable_tool_choice_with_thinking"].source == "legacy"','capabilities["disable_tool_choice_with_thinking"].source == "catalog"'))
def motion(s):
 a=s.index('    snapshot = compact_capability_prompt(',s.index('def test_known_operand_count'))
 return s[:a]+'''    from knowledge.structured_facts import resolve_instruction_contract
    # Exact facts have one owner; the retired prompt snapshot must stay empty.
    assert compact_capability_prompt("FX3U", {}) == ""
    instruction = resolve_instruction_contract({"opcode": "ZRN"}, plc_model="FX3U")
    assert instruction["min_operands"] == instruction["max_operands"] == 4
    assert instruction["native_operand_order"] == ["S1", "S2", "S3", "D"]
    assert [r["name"] for r in instruction["operand_annotations"]] == [
        "return_speed", "creep_speed", "zero_signal", "pulse_output"]
    assert instruction["completion"]["device"] == "M8029"
    assert instruction["completion"]["placement"] == "same_rung_parallel_branch"
    assert instruction["sources"]
'''
edit('tests/test_motion_control_regressions.py',motion)
edit('tests/test_instruction_applicability_rag.py',lambda s:sub(sub(s,'import knowledge.core as core','from knowledge.retriever import retrieve_fact_aware_knowledge'),'core.retrieve_knowledge(','retrieve_fact_aware_knowledge('))
def fx3(s):
 s=sub(s,'import knowledge.dense as dense_retriever','import knowledge.dense as dense_retriever\nimport knowledge.core as knowledge_core')
 s=s.replace('monkeypatch.setattr(knowledge_retriever, "_retrieve_uncached",','monkeypatch.setattr(knowledge_core, "_retrieve_uncached",').replace('monkeypatch.setattr(knowledge_retriever, helper_name,','monkeypatch.setattr(knowledge_core, helper_name,').replace('getattr(knowledge_retriever, helper_name)','getattr(knowledge_core, helper_name)')
 for name in ['test_structured_errors_exclude_glyph_and_device_false_codes','test_m8013_clock_query_prefers_internal_clock_section','test_exact_entity_candidates_are_fair_across_multiple_query_terms','test_exact_opcode_prefers_its_instruction_section_over_reference_tables']:
  s=function(s,name,lambda v:v.replace('retrieve_knowledge(','retrieve_fact_aware_knowledge('))
 s=sub(s,'assert all(item["source"] and item["page"] for item in results)','''assert all(item["source"] for item in results)
    official = [item for item in results if item.get("structured_lookup")]
    assert official and all(item["page"] for item in official)
    assert all(item["manual_number"] != "LOCAL-INSTRUCTION-FACT" for item in official)''')
 s=function(s,'test_timer_semantics_queries_retrieve_timer_manual_and_debug_cases',lambda v:sub(v,'''    assert any("Internal clock [M8011 to M8014]" in item["section"] for item in results)
    assert any("CASE_ID: timer_m8000_not_oscillator" in item["text"] for item in results)''','''    assert any("CASE_ID: timer_m8000_not_oscillator" in item["text"] for item in results)
    # Troubleshooting recall and a cited device definition have separate owners.
    clock = retrieve_fact_aware_knowledge("FX3U M8013 clock", task_type="debug", char_budget=12000)
    assert any("Internal clock [M8011 to M8014]" in item["section"] for item in clock)
    assert all(item["structured_lookup"] for item in clock if "Internal clock" in item["section"])'''))
 s=sub(s,'    budget = 2600','    budget = 16000')
 s=sub(s,'matched = [item.get("matched_entity") for item in results]','matched = [item.get("structured_fact_requested_target") for item in results]')
 s=sub(s,'assert {"X001", "X000"}.issubset(set(matched))','assert {"X001", "X000"}.issubset(set(matched))\n    assert {"X1", "X0"}.issubset({item.get("structured_fact_target") for item in results})')
 s=sub(s,'def test_model_profile_keeps_full_fallback_when_retrieval_is_unavailable():','def test_model_profile_is_authoritative_independently_of_retrieval_compaction():')
 return sub(s,'''    assert '\"special_m\"' not in compact
    assert '\"manual_evidence\"' in compact''','''    assert compact == full
    assert '\"manual_evidence\"' not in compact''')
edit('tests/test_fx3u_rag.py',fx3)
def lookup(s):
 s=sub(s,'def test_local_instruction_fallback_keeps_contract_and_step_width_provenance_separate():','''def test_local_instruction_fallback_keeps_contract_and_step_width_provenance_separate(tmp_path, monkeypatch):
    # Exercise a genuinely unavailable manual index, not a particular mnemonic
    # that happened to be missing from a prior catalogue revision.
    monkeypatch.setattr(core, "_index_path", lambda: tmp_path / "not-installed.sqlite")''')
 s=sub(s,'''            assert rows[0]["manual_id"] == "structured_instruction_registry"
            assert rows[0]["source"] == "local_structured_instruction_owners"''','''            assert rows[0]["instruction_lookup_basis"] == "official_section_heading"
            assert rows[0]["manual_number"] == "JY997D16601"''')
 return s
edit('tests/test_structured_fact_lookup.py',lookup)
def natural(s):
 s=function(s,'test_explicit_loop_instruction_remains_discoverable',lambda v:v.replace('retriever.retrieve_knowledge(','retriever.retrieve_fact_aware_knowledge('))
 s=function(s,'test_timer_preset_question_includes_model_range_evidence_within_tool_budget',lambda v:v.replace('retriever.retrieve_knowledge(query, plc_model="FX3U",','retriever.retrieve_fact_aware_knowledge(query, plc_model="FX3U", task_type="generate",'))
 return s+'''

def test_explicit_property_lookup_uses_scope_and_body_before_broad_recall(timer_range_index):
    results = retriever.retrieve_fact_aware_knowledge("FX3U timer T20 K units", task_type="generate", char_budget=6500)
    assert results and results[0]["manual_number"] == "OFFICIAL"
    assert results[0]["device_lookup_basis"] == "official_property_section"
    assert results[0]["fact_dimensions"] == ["range", "time_base"]
    assert not retriever.retrieve_fact_aware_knowledge("FX3U timer T9999 K units", task_type="debug")
    assert not retriever.retrieve_fact_aware_knowledge("FX3U timer T20 K units", task_type="generate", char_budget=1)


def test_device_definition_lookup_does_not_prefer_incidental_instruction_mentions():
    from knowledge.structured_facts import resolve_device_records

    rows = resolve_device_records(["M8013"], plc_model="FX3U", task_type="generate")
    assert rows and all(row["device_lookup_basis"] == "official_definition_heading" for row in rows)
    assert all("Internal clock" in row["section"] for row in rows)
    assert any("M8013" in row["text"] for row in rows)
'''
edit('tests/test_rag_natural_language_recall.py',natural)
p=R/'tests/test_rag_instruction_recall.py';s=p.read_text();p.write_text(s+'''

def test_scoped_index_override_does_not_leak_or_get_overwritten(tmp_path, monkeypatch):
    from knowledge.structured_facts import resolve_instruction_records

    original_path = core._index_path
    database = tmp_path / "scoped.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, text TEXT)")
    with monkeypatch.context() as scoped:
        scoped.setattr(core, "_index_path", lambda: database)
        assert retriever.retrieve_knowledge("MPS", char_budget=6500) == []
        # A facade must never mirror stale aliases over the configured Core.
        assert core._index_path() == database
    assert core._index_path is original_path
    records = resolve_instruction_records(["MPS"], plc_model="FX3U")
    assert any(row.get("manual_number") == "JY997D16601" for row in records)
    assert all(row.get("structured_lookup") for row in records)


def test_declared_natural_language_aliases_resolve_outside_broad_ranking():
    from knowledge.structured_facts import structured_fact_targets

    targets = structured_fact_targets("FX3U 批量清零连续 M 软元件区间应该使用什么指令？")
    reset = next(row for row in targets["instructions"] if row["opcode"] == "ZRST")
    assert reset["target_resolution"] == "declared_manual_alias"
    assert reset["requested_alias"] == "批量清零"
    assert all(row["opcode"] != "ZRST" for row in structured_fact_targets("FX3U 不需要批量清零。") ["instructions"])
    assert structured_fact_targets("please revise this program")["instructions"] == []


def test_section_fallback_keeps_manual_bytes_and_instance_step_facts():
    from knowledge.structured_facts import resolve_instruction_records, compact_structured_fact_record

    full = resolve_instruction_records([{"opcode": "RST", "operands": ["D10"]}])[0]
    assert full["instruction_lookup_basis"] == "official_section_heading"
    assert "STEP_WIDTH: 3 program step(s)" in full["text"]
    public = compact_structured_fact_record(full)
    assert public["instruction_step_width"]["steps"] == 3
    assert public["instruction_contract"]["opcode"] == "RST"
    with sqlite3.connect(resource_path("knowledge/fx3u_knowledge.sqlite").resolve().as_uri()+"?mode=ro", uri=True) as db:
        assert public["text"] == db.execute("SELECT text FROM chunks WHERE id=?", (full["original_id"],)).fetchone()[0]
''')
translations={
'候选程序已保留；已确认语义检查发现不一致，未触发额外模型调用':('The candidate program was retained; confirmed semantic checks found inconsistencies. No additional model call was made.','候補プログラムを保持しました。確認済みの意味要件との不一致が見つかりましたが、追加のモデル呼び出しは行っていません。'),
'候选程序已保留；部分已确认语义无法由当前本地检查完整覆盖':('The candidate program was retained; current local checks cannot fully cover some confirmed semantic requirements.','候補プログラムを保持しました。確認済みの意味要件の一部は、現在のローカル検査では完全に検証できません。'),
'模型分配的未声明内部软元件已移除；内部地址由生成阶段决定。':('Undeclared internal devices allocated by the model were removed; internal addresses are assigned during generation.','モデルが割り当てた未宣言の内部デバイスを削除しました。内部アドレスは生成段階で決定します。'),
'独立生成 Agent 未返回梯形图候选':('The independent generation agent did not return a ladder candidate.','独立した生成エージェントからラダーの候補が返されませんでした。'),
'候选结构与当前可机检的已确认语义已通过；其余工程检查保留给 Review':('The candidate structure and currently machine-checkable confirmed semantics passed; remaining engineering checks belong to Review.','候補の構造と、現在機械検証できる確認済みの意味要件は合格しました。その他のエンジニアリング検査は Review で実施します。'),
}
for i,lang in enumerate(['en','ja']):
 p=R/f'resources/locales/{lang}.json';s=p.read_text().rstrip();obj=json.loads(s);assert not set(translations)&set(obj)
 p.write_text(s[:-1].rstrip()+',\n'+',\n'.join('  '+json.dumps(k,ensure_ascii=False)+': '+json.dumps(vals[i],ensure_ascii=False) for k,vals in translations.items())+'\n}\n')

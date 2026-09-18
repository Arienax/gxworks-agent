import inspect
import json

import application.model_workflows as api
import knowledge.retriever as knowledge_retriever
from knowledge.patterns import build_workflow_prompt
from shared.context_policy import context_policy_scope


def test_phase_one_routes_as_analysis():
    for function in (api.analyze_requirement, api.analyze_requirement_streaming):
        source = inspect.getsource(function)
        assert 'forced_task=task_type or "analysis"' in source
        assert 'forced_task=task_type or "generate"' not in source


def test_analysis_prompt_has_meta_search_rules_without_candidate_few_shots():
    prompt = api.ANALYSIS_SYSTEM_PROMPT
    assert "Retrieved PLC knowledge" in prompt
    assert "不算新的架构方案" in prompt
    assert "设计空间很窄时允许只给 1 个" in prompt
    assert "方案示例（分拣/顺序控制）" not in prompt
    assert "方案A「直接逻辑法」" not in prompt
    assert "方案示例（三泵轮换）" not in prompt

    example = prompt.split("返回纯JSON（不要```json包裹），格式：\n", 1)[1]
    example = example.split("\n# suggested_io", 1)[0]
    parsed = json.loads(example)
    assert parsed["approaches"] == []
    assert parsed["missing_info"] == []
    assert parsed["suggested_io"] == {}


def test_adaptive_analysis_forces_sqlite_lookup_but_generic_generation_does_not(monkeypatch):
    calls = []

    def fake_context(query, **kwargs):
        calls.append((query, kwargs))
        return "# retrieved"

    monkeypatch.setattr(knowledge_retriever, "build_knowledge_context", fake_context)
    with context_policy_scope("adaptive"):
        result = api._build_knowledge_context(
            "普通三工位顺序控制",
            plc_model="FX3U",
            task_type="analysis",
        )
    assert "# retrieved" in result
    assert calls and calls[-1][1]["task_type"] == "analysis"

    calls.clear()
    with context_policy_scope("adaptive"):
        result = api._build_knowledge_context(
            "普通三工位顺序控制",
            plc_model="FX3U",
            task_type="generate",
        )
    assert result == ""
    assert calls == []


def test_workflow_router_marks_analysis_without_embedding_architecture_catalog():
    prompt, route = build_workflow_prompt(
        "FX3U 三工位依次执行并延时",
        target_mode="ladder",
        forced_task="analysis",
    )
    assert route.task_type == "analysis"
    assert "task_type: analysis" in prompt
    assert "Control architecture search" not in prompt


def test_bundled_design_knowledge_is_injected_through_production_analysis_path():
    query = "FX3U 三个工位依次执行，包含多阶段顺序和延时，应该如何组织控制架构"
    with context_policy_scope("adaptive"):
        context = api._build_knowledge_context(
            query,
            plc_model="FX3U",
            task_type="analysis",
        )
    assert "Curated PLC Control Architecture Design Knowledge" in context
    assert "CONTROL ARCHITECTURE:" in context
    assert "Retrieved-knowledge precedence" not in context


def test_design_chunks_are_task_scoped_at_the_retriever_boundary():
    query = "FX3U 三个工位依次执行，包含多阶段顺序和延时"
    analysis = knowledge_retriever.retrieve_design_knowledge(
        query,
        plc_model="FX3U",
        task_type="analysis",
        top_k=2,
        char_budget=2400,
    )
    assert analysis
    assert all(item.get("manual_id") == "curated_control_design" for item in analysis)
    assert all(item.get("chunk_type") == "design_pattern" for item in analysis)

    generation = knowledge_retriever.retrieve_design_knowledge(
        query,
        plc_model="FX3U",
        task_type="generate",
        top_k=20,
        char_budget=30000,
    )
    assert generation == []


def test_motion_parameter_fact_remains_owned_by_official_sqlite_manual():
    results = knowledge_retriever.retrieve_knowledge(
        "FX3U D8345 DRVI 最高速度 回原点 爬行速度",
        plc_model="FX3U",
        task_type="analysis",
        top_k=8,
        char_budget=20000,
    )
    assert any(item.get("manual_id") == "fx3_positioning_k" for item in results)
    assert any("D8345" in item.get("text", "") for item in results)

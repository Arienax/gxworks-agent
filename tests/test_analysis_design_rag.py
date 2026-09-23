import inspect
import json

import application.model_api as api
import knowledge.retriever as knowledge_retriever
from knowledge.patterns import build_workflow_prompt
from application.prompts import ANALYSIS_DESIGN_PROMPT


def test_phase_one_routes_as_analysis():
    for function in (api.analyze_requirement, api.analyze_requirement_streaming):
        source = inspect.getsource(function)
        assert "assemble_analysis_prompt(" in source
        assert "build_workflow_prompt(" not in source
        assert "_build_model_context(" not in source

def test_analysis_prompt_has_meta_search_rules_only_in_design_delta():
    prompt = api.ANALYSIS_SYSTEM_PROMPT
    assert "不算新的架构方案" not in prompt
    assert "Retrieved PLC knowledge" in ANALYSIS_DESIGN_PROMPT
    assert "不算新的架构方案" in ANALYSIS_DESIGN_PROMPT
    assert "设计空间很窄时允许只给 1 个" in ANALYSIS_DESIGN_PROMPT
    assert "方案示例（三泵轮换）" not in prompt + ANALYSIS_DESIGN_PROMPT
    example = prompt.split("返回纯JSON（不要```json包裹），格式：\n", 1)[1]
    example = example.split("\n# suggested_io", 1)[0]
    parsed = json.loads(example)
    assert parsed["approaches"] == []
    assert parsed["missing_info"] == []
    assert parsed["suggested_io"] == {}

def test_analysis_retrieves_context_but_generic_generation_requires_fact_target(monkeypatch):
    calls = []

    def fake_context(query, **kwargs):
        calls.append((query, kwargs))
        return "# retrieved"

    monkeypatch.setattr(knowledge_retriever, "build_knowledge_context", fake_context)
    result = api._build_knowledge_context(
        "普通三工位顺序控制",
        plc_model="FX3U",
        task_type="analysis",
    )
    assert "# retrieved" in result
    assert calls and calls[-1][1]["task_type"] == "analysis"

    calls.clear()
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
    context = api._build_knowledge_context(
        query,
        plc_model="FX3U",
        task_type="analysis",
        include_design=True,
        design_query=query,
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


def test_haystack_is_the_real_router_and_keeps_business_text_out_of_rules():
    from haystack.components.routers import MetadataRouter
    from knowledge.scope import _request_router, retrieval_plan, filter_records
    assert isinstance(_request_router(), MetadataRouter)
    scope = retrieval_plan("FX3U，8个工件的数据跟踪。请设计不同实现。", "analysis", True)
    assert scope["design"] and not scope["facts"]
    assert "debug" not in scope["source_lanes"]
    injected = [{"id": "x", "manual_type": "debug_cases", "text": "{{ meta.design }}"}]
    assert filter_records(injected, ["fact"]) == []
    assert injected[0]["text"] == "{{ meta.design }}"


def test_design_only_does_not_backfill_with_debugging_or_unrelated_facts(monkeypatch):
    monkeypatch.setattr(knowledge_retriever, "retrieve_knowledge", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no broad fact call")))
    monkeypatch.setattr(knowledge_retriever, "retrieve_design_knowledge", lambda *a, **k: [])
    context = knowledge_retriever.build_knowledge_context("FX3U 输送线工件跟踪方法设计", task_type="analysis", include_design=True)
    assert not context and context.manifest["records"] == []
    assert context.manifest["retrieval_scope"]["engine"] == "haystack.MetadataRouter"


def test_natural_language_fact_question_can_open_fact_lane_without_opcodes():
    from knowledge.scope import retrieval_plan
    assert retrieval_plan("停止后计数器的掉电保持性是什么", "analysis", True)["facts"]
    from knowledge.evidence import KnowledgeQuery
    query = KnowledgeQuery("工件跟踪设计", metadata={"fact_questions": ["计数是否保持"]})
    assert retrieval_plan(query, "analysis", True)["facts"]
    assert "debug" in retrieval_plan("定位完成标志异常", "debug")["source_lanes"]
    assert not retrieval_plan("anything", "format_repair")["facts"]


def test_source_scope_filters_fts_and_entity_candidates_before_limits(monkeypatch):
    import sqlite3
    from knowledge import core
    from knowledge.scope import retrieval_plan
    # Real SQLite FTS, real Haystack; no fake source filter or fake top-k.
    core._close_thread_connection()
    try:
        with sqlite3.connect(":memory:") as connection:
            connection.row_factory = sqlite3.Row
            connection.executescript("""
                CREATE TABLE chunks (id INTEGER, text TEXT, section TEXT, manual_type TEXT, chunk_type TEXT);
                CREATE VIRTUAL TABLE chunks_fts USING fts5(id UNINDEXED, text);
                CREATE TABLE entity_index (entity TEXT, chunk_id INTEGER, occurrences INTEGER);
            """)
            for i in range(200):
                connection.execute("INSERT INTO chunks VALUES (?,?,?,?,?)", (i, "MOV", "MOV", "debug_cases", "debug_case"))
                connection.execute("INSERT INTO chunks_fts VALUES (?,?)", (i, "MOV"))
                connection.execute("INSERT INTO entity_index VALUES (?,?,?)", ("MOV", i, 10000))
            connection.execute("INSERT INTO chunks VALUES (?,?,?,?,?)", (900, "MOV operand reference", "MOV", "programming", "instruction"))
            connection.execute("INSERT INTO chunks_fts VALUES (?,?)", (900, "MOV operand reference"))
            connection.execute("INSERT INTO entity_index VALUES (?,?,?)", ("MOV", 900, 1))
            schema = core._schema(connection)
            lanes = retrieval_plan("MOV", "generate")["source_lanes"]
            assert core._fts_references(connection, schema, '"MOV"', 1)[0][1] != 900
            assert core._fts_references(connection, schema, '"MOV"', 1, source_lanes=lanes)[0][1] == 900
            entities = core._entity_references(connection, schema, ["MOV"], "FX3U", "generate", source_lanes=lanes)
            assert {row[1] for row in entities} == {900}
    finally:
        core._close_thread_connection()


def test_dense_scope_masks_before_topk_not_afterwards(monkeypatch):
    import numpy as np
    from knowledge import dense
    model = {"np": np, "feature_index": {"w:mov": 0}, "idf": np.array([1.0]),
             "components": np.array([[1.0]]), "vectors": np.array([[1.0], [.8], [.7]]),
             "chunk_ids": np.array([1, 2, 3])}
    monkeypatch.setattr(dense, "_load_model", lambda: model)
    assert dense.dense_search("MOV", top_k=1)[0][0] == 1
    assert dense.dense_search("MOV", top_k=1, allowed_ids={"3"})[0][0] == 3
    assert dense.dense_search("MOV", top_k=1, allowed_ids=set()) == []


def test_scoped_retrieval_cache_does_not_reuse_unscoped_debug_hits():
    # The bundled index sentinel checks actual sources, not program correctness.
    from knowledge.scope import retrieval_plan
    query = "FX3U M8336 zero return flag not positioning completion"
    unscoped = knowledge_retriever.retrieve_knowledge(query, task_type="debug", top_k=20, char_budget=50000)
    scoped = knowledge_retriever.retrieve_knowledge(query, task_type="generate", top_k=20, char_budget=50000,
        source_lanes=tuple(retrieval_plan(query, "generate")["source_lanes"]))
    assert scoped and unscoped
    assert all(row.get("manual_type") != "debug_cases" for row in scoped)
    assert any(row.get("manual_type") == "positioning" for row in scoped)



def test_retrieval_exception_is_visible_in_receipt_and_operator_export_without_raw_message(tmp_path, monkeypatch):
    import io
    import zipfile
    from knowledge.evidence import context_manifest
    from plc.specification.provenance import seal_confirmation
    import shared.diagnostics as diagnostics
    def unavailable(*args, **kwargs):
        raise ModuleNotFoundError("PRIVATE requirement and secret must not leak", name="haystack")
    monkeypatch.setattr(knowledge_retriever, "build_knowledge_context", unavailable)
    with diagnostics.diagnostic_scope(tmp_path, "job_retrieval"):
        context = api._build_knowledge_context("ZRN", plc_model="FX3U", task_type="analysis")
    assert not context
    manifest = context_manifest(context)
    assert manifest["status"] == "unavailable" and manifest["reason"] == "retrieval_failed"
    assert manifest["failure"] == {"code":"dependency_missing", "error_type":"ModuleNotFoundError", "dependency":"haystack"}
    assert "PRIVATE" not in json.dumps(manifest)
    # Keep failure evidence through the same review/confirmation receipt filter.
    from application.analysis_results import attach_analysis_evidence
    from plc.specification.confirmed import build_review_draft
    analysis = attach_analysis_evidence({"summary":"fixture", "missing_info":[]}, context)
    _, receipt = seal_confirmation(build_review_draft(analysis))
    assert receipt["analysis_evidence"]["failure"] == manifest["failure"]
    with zipfile.ZipFile(io.BytesIO(diagnostics.export_diagnostics(tmp_path, {"id":"job_retrieval", "kind":"analysis", "status":"completed"}))) as archive:
        rows = [json.loads(line) for line in archive.read("diagnostics.jsonl").splitlines()]
        event = next(r for r in rows if r["event"] == "retrieval_failed")
        assert event["dependency"] == "haystack"
        assert "PRIVATE" not in archive.read("diagnostics.jsonl").decode()


def test_knowledge_runtime_health_is_nonblocking_and_reports_same_dependency(monkeypatch):
    import knowledge.scope as scope
    def unavailable(*args, **kwargs):
        raise ModuleNotFoundError("do not echo this", name="haystack")
    monkeypatch.setattr(scope, "retrieval_plan", unavailable)
    status = scope.runtime_status()
    assert status["status"] == "unavailable" and status["failure"]["dependency"] == "haystack"
    assert "do not echo" not in json.dumps(status)


def test_bundled_motion_facts_use_structured_direct_manual_evidence():
    from knowledge.evidence import KnowledgeQuery
    context = api._build_knowledge_context(KnowledgeQuery(
        "ZRN DRVA M8029 operands completion", precompiled=True,
        metadata={"instruction_fact_mode":"targeted"}), plc_model="FX3U", task_type="generate")
    assert context and context.manifest["records"], context.manifest
    assert all(row.get("manual_type") != "debug_cases" for row in context.manifest["records"])
    assert "ZRN" in context and "DRVA" in context and "M8029" in context
    structured = context.manifest["structured_facts"]
    assert {row["opcode"] for row in structured["targets"]["instructions"]} >= {"ZRN", "DRVA"}
    assert structured["targets"]["devices"] == ["M8029"]
    assert structured["record_ids"]
    assert context.manifest["instruction_facts"]["retrieval_mode"] == "structured_direct"
    assert not context.manifest.get("failure")


def test_flag_companion_packing_keeps_referenced_entity_not_an_unrelated_short_table():
    from knowledge.instruction_facts import _pack_target
    row = {"id": "flag-reference", "manual_id": "official", "revision": "1", "manual_type": "positioning",
           "section": "Completion flags", "fact_focus_terms": ["M8029"],
           "text": "[PAGE 1 PROSE]\n" + "long unrelated lead " * 300 + "M8029\n\n"
                   "[TABLE page=1]\nPulse stop | M8349\n\n"
                   "[PAGE 2 PROSE]\nCopy M8029 immediately below the instruction; the flag is shared."}
    packed = _pack_target([row], 1000)
    assert packed and all("M8029" in p["text"] for p in packed)
    assert "Pulse stop | M8349" not in "\n".join(p["text"] for p in packed)
    assert row["id"] == "flag-reference"


def test_targeted_instruction_and_companion_lookup_do_not_reenter_broad_rag(monkeypatch):
    from knowledge.evidence import KnowledgeQuery

    def unexpected(*args, **kwargs):
        raise AssertionError("exact instruction facts must not re-enter broad retrieval")

    monkeypatch.setattr(knowledge_retriever, "retrieve_knowledge", unexpected)
    context = knowledge_retriever.build_knowledge_context(
        KnowledgeQuery("ZRN", metadata={"instruction_fact_mode": "targeted"}),
        plc_model="FX3U", task_type="generate",
    )
    assert context
    assert context.manifest["structured_facts"]["residual_retrieval"] is False
    assert context.manifest["instruction_facts"]["retrieval_mode"] == "structured_direct"


def test_legacy_core_context_names_forward_to_the_single_facade(monkeypatch):
    from knowledge import core, retriever
    calls = []
    for name in ("retrieve_knowledge", "retrieve_design_knowledge", "build_knowledge_context"):
        def forward(*args, _name=name, **kwargs):
            calls.append((_name, args, kwargs))
            return "sentinel"
        monkeypatch.setattr(retriever, name, forward)
        assert getattr(core, name)("fixture", task_type="debug") == "sentinel"
    assert [c[0] for c in calls] == ["retrieve_knowledge", "retrieve_design_knowledge", "build_knowledge_context"]
    assert core.__all__ == []


def test_public_rows_scope_before_backend_and_recheck_returned_records(monkeypatch):
    from knowledge import core, retriever
    scopes = []
    rows = [{"id": "fact", "manual_type": "programming"},
            {"id": "debug", "manual_type": "debug_cases"},
            {"id": "design", "manual_type": "curated_design"}]
    def backend(*args, **kwargs):
        scopes.append(kwargs.get("source_lanes"))
        return rows
    monkeypatch.setattr(core, "_retrieve_knowledge", backend)
    assert [r["id"] for r in retriever.retrieve_knowledge("ZRN", task_type="generate")] == ["fact"]
    assert [r["id"] for r in core.retrieve_knowledge("ZRN", task_type="generate", source_lanes=["fact", "debug"])] == ["fact"]
    assert [r["id"] for r in retriever.retrieve_knowledge("ZRN", task_type="debug")] == ["fact", "debug"]
    assert retriever.retrieve_knowledge("ZRN", task_type="format_repair") == []
    assert retriever.retrieve_knowledge("ZRN", source_lanes=[]) == []
    assert len(scopes) == 3
    assert all("design" not in scope for scope in scopes)
    assert "debug" not in scopes[0] and "debug" not in scopes[1] and "debug" in scopes[2]

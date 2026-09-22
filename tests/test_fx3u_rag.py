import json
from pathlib import Path
import re
import sqlite3
import time

import application.model_api as api
import knowledge.dense as dense_retriever
import knowledge.retriever as knowledge_retriever
import pytest
from knowledge.retriever import build_knowledge_context, retrieve_knowledge
from shared.paths import resource_path


INDEX_PATH = Path(resource_path("knowledge/fx3u_knowledge.sqlite"))
MANIFEST_PATH = Path(resource_path("knowledge/manifest.json"))
BENCHMARK_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "fx3u_rag_benchmark.jsonl"
BENCHMARK_REPORT_PATH = (
    Path(__file__).resolve().parents[1] / "benchmarks" / "fx3u_rag_benchmark_report.json"
)


def test_bundled_fx3u_index_is_complete_and_integral():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert INDEX_PATH.is_file()
    assert manifest["primary_manual"]["manual_number"] == "JY997D16601"
    assert manifest["primary_manual"]["revision"] == "R"
    assert manifest["stats"]["pages"] == sum(
        int(item["pdf_pages"]) for item in manifest["manuals"]
    )
    assert manifest["stats"]["pages"] == 3064
    assert len(manifest["manuals"]) == 7
    assert manifest["structured"]["instructions"] >= 250
    assert manifest["structured"]["debug_cases"] == 26
    assert manifest["retrieval"]["dense_embeddings"] is True
    assert manifest["retrieval"]["fusion"] == "entity_bm25_vector_weighted_rrf"
    assert manifest["retrieval"]["benchmark"]["cases"] == 220
    assert manifest["retrieval"]["benchmark"]["recall_at_10"] == 1.0
    assert Path(resource_path("knowledge/fx3u_dense_lsa.npz")).is_file()
    positioning = next(
        item for item in manifest["manuals"] if item["id"] == "fx3_positioning_k"
    )
    assert positioning["manual_number"] == "JY997D16801"
    assert positioning["revision"] == "K"
    assert positioning["manual_type"] == "positioning"

    with sqlite3.connect(INDEX_PATH) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM page_artifacts").fetchone()[0] == 3064
        assert connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] >= 3000
        assert connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == connection.execute(
            "SELECT COUNT(*) FROM chunks"
        ).fetchone()[0]
        assert connection.execute(
            "SELECT COUNT(*) FROM vector_embeddings"
        ).fetchone()[0] == connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert connection.execute(
            "SELECT COUNT(*) FROM vector_embeddings v JOIN chunks c ON c.id=v.chunk_id "
            "WHERE v.content_sha256=c.text_sha256"
        ).fetchone()[0] == connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        operands = json.loads(
            connection.execute(
                "SELECT operands_json FROM instructions "
                "WHERE manual_id='fx3_programming_r' AND opcode_norm='plsy'"
            ).fetchone()[0]
        )
        assert {item["position"] for item in operands} >= {"S1", "S2", "D"}


def test_positioning_manual_preserves_instruction_devices_and_operand_placeholders():
    with sqlite3.connect(INDEX_PATH) as connection:
        opcodes = {
            row[0].upper()
            for row in connection.execute(
                "SELECT DISTINCT opcode_norm FROM instructions "
                "WHERE manual_id='fx3_positioning_k'"
            )
        }
        assert {"DRVI", "DRVA", "ZRN", "DSZR", "DVIT"}.issubset(opcodes)

        indexed_devices = {
            row[0].upper()
            for row in connection.execute(
                "SELECT DISTINCT entity_norm FROM entity_index "
                "WHERE manual_id='fx3_positioning_k' "
                "AND entity_norm IN ('d8342','d8343','d8345','d8348','d8349','m8336')"
            )
        }
        assert indexed_devices == {"D8342", "D8343", "D8345", "D8348", "D8349", "M8336"}

        wrong_operands = connection.execute(
            """
            SELECT COUNT(*) FROM entity_index e
            JOIN chunks c ON c.id=e.chunk_id
            WHERE e.manual_id='fx3_positioning_k'
              AND c.chunk_type='instruction'
              AND e.entity_norm IN ('s1','s2','s3')
              AND e.entity_type='device'
            """
        ).fetchone()[0]
        assert wrong_operands == 0


def test_positioning_queries_retrieve_manual_and_debug_evidence():
    parameter_results = retrieve_knowledge(
        "FX3U DRVI D8342 D8343 D8348 D8349 positioning parameters",
        plc_model="FX3U",
        task_type="debug",
        top_k=5,
        char_budget=10000,
    )
    assert parameter_results
    assert any(item.get("manual_id") == "fx3_positioning_k" for item in parameter_results)
    assert any("D8342-D8349" in item["section"] for item in parameter_results)

    flag_results = retrieve_knowledge(
        "FX3U M8336 zero return flag not positioning completion",
        plc_model="FX3U",
        task_type="debug",
        top_k=5,
        char_budget=10000,
    )
    assert flag_results
    assert any(item.get("manual_id") == "fx3_positioning_k" for item in flag_results)
    assert any("M8336" in item["section"] for item in flag_results)


@pytest.mark.parametrize("query", [
    "FX3U 使用 Y0 发定位脉冲时，方向信号是否必须固定为 Y4？可以用 Y7 吗？",
    "FX3U Y000 pulse output: must the direction signal be fixed to Y004 or can I use Y007?",
    "FX3U-2HSY-ADP 的方向输出是否固定配对？",
])
def test_direction_assignment_retrieves_official_hardware_distinction(query):
    results = retrieve_knowledge(query, plc_model="FX3U", task_type="program_review",
                                 top_k=10, char_budget=50000)
    assignment = next(item for item in results if "Assignment of Output Numbers" in item["section"])
    assert assignment["manual_id"] == "fx3_positioning_k"
    assert "Connect a line to any output" in assignment["text"]
    assert "High-speed output special adapter" in assignment["text"]
    assert assignment["match_type"] == "manual_section"


def test_direction_assignment_route_does_not_capture_plain_y_address_queries():
    assert not knowledge_retriever._query_is_direction_output_assignment("FX3U 将 Y0 指定为指示灯输出")
    assert not knowledge_retriever._query_is_direction_output_assignment("FX3U DRVI direction signal ON/OFF timing")


def test_plsy_and_completion_flag_retrieve_detailed_manual_pages_first():
    results = retrieve_knowledge(
        "FX3U PLSY K1000 K0 Y000 M8029 脉冲输出完成",
        plc_model="FX3U",
        task_type="generate",
        top_k=5,
        char_budget=6200,
    )

    assert results
    assert results[0]["match_type"] == "structured_instruction"
    assert "PLSY" in results[0]["section"].upper()
    assert 377 <= int(results[0]["pdf_page"]) <= 381
    assert any("M8029" in "".join(item["text"].split()) for item in results)
    assert all(item["source"] and item["page"] for item in results)


def test_plsy_operands_tables_and_ladder_fidelity_are_preserved():
    with sqlite3.connect(INDEX_PATH) as connection:
        instruction = connection.execute(
            "SELECT operands_json,chunk_id FROM instructions "
            "WHERE manual_id='fx3_programming_r' AND opcode_norm='plsy'"
        ).fetchone()
        operands = json.loads(instruction[0])
        assert {item["position"] for item in operands} == {"S1", "S2", "D"}
        assert all(item["description"] for item in operands)

        wrong_devices = connection.execute(
            """
            SELECT COUNT(*) FROM entity_index e
            JOIN chunks c ON c.id=e.chunk_id
            WHERE c.manual_id='fx3_programming_r'
              AND c.instruction_opcode='PLSY'
              AND e.entity_norm IN ('s1','s2')
              AND e.entity_type='device'
            """
        ).fetchone()[0]
        placeholders = connection.execute(
            """
            SELECT COUNT(*) FROM entity_index e
            JOIN chunks c ON c.id=e.chunk_id
            WHERE c.manual_id='fx3_programming_r'
              AND c.instruction_opcode='PLSY'
              AND e.entity_norm IN ('s1','s2')
              AND e.entity_type='operand_placeholder'
            """
        ).fetchone()[0]
        assert wrong_devices == 0
        assert placeholders >= 2

        first_chunk = connection.execute(
            "SELECT text,char_count FROM chunks WHERE id=?", (instruction[1],)
        ).fetchone()
        assert first_chunk[1] <= 5900
        assert all(value in first_chunk[0] for value in ("S1", "S2", "M8029"))
        assert connection.execute(
            "SELECT COUNT(*) FROM tables "
            "WHERE manual_id='fx3_programming_r' AND pdf_page BETWEEN 377 AND 381"
        ).fetchone()[0] >= 10
        fidelity = connection.execute(
            "SELECT fidelity_flags FROM page_artifacts "
            "WHERE manual_id='fx3_programming_r' AND pdf_page=377"
        ).fetchone()[0]
        assert {"tables", "diagram_text", "word_geometry"}.issubset(
            set(fidelity.split(","))
        )


def test_structured_errors_exclude_glyph_and_device_false_codes():
    with sqlite3.connect(INDEX_PATH) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM error_records "
            "WHERE error_code IN ('F050','D8067','D8068','32767')"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM error_records WHERE error_code='6105'"
        ).fetchone()[0] >= 1

    results = retrieve_knowledge(
        "FX3U 错误码 6105 原因和处理方法",
        plc_model="FX3U",
        task_type="debug",
        top_k=5,
        char_budget=8000,
    )
    assert results
    assert any("6105" in item["text"] for item in results)


def test_benchmark_size_and_latest_baseline_are_valid():
    cases = [
        json.loads(line)
        for line in BENCHMARK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = json.loads(BENCHMARK_REPORT_PATH.read_text(encoding="utf-8"))
    assert 100 <= len(cases) <= 300
    assert report["cases"] == len(cases)
    assert report["recall_at_5"] >= 0.95
    assert report["negative_accuracy"] >= 0.95


def test_dense_model_is_local_complete_and_returns_semantic_candidates():
    info = dense_retriever.dense_model_info()
    assert info["model"] == "fx3u_multilingual_lsa_v1"
    assert info["algorithm"] == "tfidf_truncated_svd_lsa"
    assert info["chunks"] >= 3000
    assert info["dimensions"] >= 128
    results = dense_retriever.dense_search(
        "相对位置移动时怎样指定方向端子",
        top_k=20,
    )
    assert results
    assert all(0.0 <= score <= 1.000001 for _chunk_id, score, _rank in results)


def test_hybrid_retrieval_exposes_entity_bm25_and_vector_signals():
    results = retrieve_knowledge(
        "FX3U DRVI 相对定位方向输出怎么指定",
        plc_model="FX3U",
        task_type="debug",
        top_k=10,
        char_budget=30000,
    )
    assert results
    signals = {signal for item in results for signal in item["retrieval_signals"]}
    assert {"entity", "bm25", "vector"}.issubset(signals)


def test_dense_model_is_not_loaded_by_import_alone():
    source = Path(dense_retriever.__file__).read_text(encoding="utf-8")
    assert "import numpy as np" in source
    assert source.index("def _load_model") < source.index("import numpy as np")


@pytest.mark.parametrize(
    "query",
    ["Siemens S7-1200 TIA Portal 定时器", "Arduino UNO PWM 引脚说明"],
)
def test_non_mitsubishi_queries_do_not_trigger_fx3u_entities(query):
    assert retrieve_knowledge(query, plc_model="FX3U", task_type="analysis") == []


def test_inc_counter_hmi_query_retrieves_inc_semantics_without_read_requirement():
    results = retrieve_knowledge(
        "INC C1，计数值只供HMI读取",
        plc_model="FX3U",
        task_type="program_review",
        top_k=5,
        char_budget=6200,
    )

    assert results
    assert any(item.get("instruction_opcode") == "INC" for item in results)
    assert any("CASE_ID: counter_hmi_write_only" in item["text"] for item in results)


def test_chinese_fts_query_reaches_high_speed_counter_frequency_section():
    results = retrieve_knowledge(
        "高速计数器响应频率和综合频率",
        plc_model="FX3U",
        task_type="debug",
        top_k=5,
        char_budget=6200,
    )

    assert results
    assert results[0]["match_type"] in {"debug_case", "bm25"}
    assert any("综合频率" in item["text"] or "combined frequency" in item["text"].lower() for item in results)


def test_timer_semantics_queries_retrieve_timer_manual_and_debug_cases():
    results = retrieve_knowledge(
        "FX3U M8000 timer T3 flashing oscillator reset",
        plc_model="FX3U",
        task_type="debug",
        top_k=5,
        char_budget=12000,
    )

    assert results
    assert any("Internal clock [M8011 to M8014]" in item["section"] for item in results)
    assert any("CASE_ID: timer_m8000_not_oscillator" in item["text"] for item in results)


def test_timer_time_base_query_prefers_ordinary_timer_device_section():
    results = retrieve_knowledge(
        "FX3U timer T0 K100 10 seconds time base",
        plc_model="FX3U",
        task_type="generate",
        top_k=5,
        char_budget=12000,
    )

    assert results
    assert "Timer [T]" in results[0]["section"]
    assert all(item.get("manual_type") != "debug_cases" for item in results)
    debug = retrieve_knowledge("FX3U timer T0 K100 10 seconds time base", plc_model="FX3U",
                               task_type="debug", top_k=5, char_budget=12000)
    assert any("CASE_ID: timer_time_base_by_device_range" in item["text"] for item in debug)


def test_m8013_clock_query_prefers_internal_clock_section():
    results = retrieve_knowledge(
        "FX3U M8013 clock relay 1 second flashing",
        plc_model="FX3U",
        task_type="generate",
        top_k=5,
        char_budget=12000,
    )

    assert results
    assert "Internal clock [M8011 to M8014]" in results[0]["section"]


def test_exact_opcode_prefers_its_instruction_section_over_reference_tables():
    results = retrieve_knowledge(
        "RS2 串行通信无协议发送接收",
        plc_model="FX3U",
        task_type="debug",
        top_k=5,
        char_budget=6200,
    )

    assert results
    assert "RS2" in results[0]["text"]
    assert any(
        item.get("instruction_opcode") == "RS2" and item["chunk_type"] == "instruction"
        for item in results
    )


def test_fx3u_manual_is_not_injected_into_fx5u_requests():
    assert retrieve_knowledge(
        "FX5U SM8029 PLSY",
        plc_model="FX5U",
        task_type="generate",
        top_k=5,
        char_budget=6200,
    ) == []


def test_context_budget_keeps_complete_database_chunks():
    budget = 2600
    context = build_knowledge_context(
        "PLSY M8029",
        plc_model="FX3U",
        task_type="debug",
        top_k=5,
        char_budget=budget,
    )
    results = retrieve_knowledge(
        "PLSY M8029",
        plc_model="FX3U",
        task_type="debug",
        top_k=5,
        char_budget=budget,
    )

    assert context
    assert len(context) <= budget
    with sqlite3.connect(INDEX_PATH) as connection:
        for item in results:
            stored = connection.execute(
                "SELECT text FROM chunks WHERE id=?", (int(item["id"]),)
            ).fetchone()[0]
            assert item["text"] == stored


def test_warm_retrieval_cache_is_low_latency():
    query = "FX3U PLSY Y000 M8029"
    retrieve_knowledge(query, plc_model="FX3U", task_type="generate")

    started = time.perf_counter()
    for _ in range(100):
        retrieve_knowledge(query, plc_model="FX3U", task_type="generate")
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5


def test_exact_entity_candidates_are_fair_across_multiple_query_terms():
    results = retrieve_knowledge(
        "X001 X000",
        plc_model="FX3U",
        task_type="generate",
        top_k=10,
        char_budget=12000,
    )

    matched = [item.get("matched_entity") for item in results]
    assert matched[0] == "X001"
    assert {"X001", "X000"}.issubset(set(matched))


@pytest.mark.parametrize(
    "query",
    [
        "please revise this program",
        "weather tomorrow",
        "\u628a\u8fd9\u4e2a\u7a0b\u5e8f\u6539\u4e00\u4e0b",
        "\u5e2e\u6211\u4fee\u6539\u7a0b\u5e8f",
        "\u8bf7\u68c0\u67e5\u662f\u5426\u6709\u95ee\u9898",
        "\u8bf7\u8f93\u51fa\u4fee\u6539\u540e\u7684\u7a0b\u5e8f",
    ],
)
def test_generic_requests_do_not_inject_unrelated_manual_pages(query):
    assert retrieve_knowledge(
        query,
        plc_model="FX3U",
        task_type="edit",
    ) == []
    assert api._build_knowledge_context(
        query,
        plc_model="FX3U",
        task_type="edit",
    ) == ""


def test_transient_retrieval_failure_is_retried_instead_of_cached(monkeypatch):
    calls = []

    def flaky_retrieve(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise sqlite3.OperationalError("temporary read failure")
        return []

    knowledge_retriever._retrieve_cached.cache_clear()
    monkeypatch.setattr(knowledge_retriever, "_retrieve_uncached", flaky_retrieve)
    try:
        assert retrieve_knowledge("FX3U PLSY transient-test") == []
        assert retrieve_knowledge("FX3U PLSY transient-test") == []
        assert len(calls) == 2
    finally:
        knowledge_retriever._retrieve_cached.cache_clear()


@pytest.mark.parametrize(
    "helper_name",
    ["_load_meta", "_entity_references", "_fts_references"],
)
def test_transient_subquery_failure_is_not_cached(monkeypatch, helper_name):
    original = getattr(knowledge_retriever, helper_name)
    calls = []

    def flaky_helper(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise sqlite3.OperationalError("temporary subquery failure")
        return original(*args, **kwargs)

    knowledge_retriever._retrieve_cached.cache_clear()
    monkeypatch.setattr(knowledge_retriever, helper_name, flaky_helper)
    query = f"FX3U PLSY retry-{helper_name}"
    try:
        assert retrieve_knowledge(query) == []
        assert retrieve_knowledge(query)
        assert len(calls) >= 2
    finally:
        knowledge_retriever._retrieve_cached.cache_clear()


def test_st_prompt_uses_only_the_selected_model_special_device_prefixes():
    fx5_prompt = api._select_system_prompt(
        "st",
        user_requirement="FX5U ST pulse program",
        plc_model="FX5U",
    )
    fx3_prompt = api._select_system_prompt(
        "st",
        user_requirement="FX3U ST pulse program",
        plc_model="FX3U",
    )

    assert "FX5U" in fx5_prompt and "iQ-F" in fx5_prompt
    assert "Never copy FX3U M8xxx/D83xx addresses into an FX5U project." in fx5_prompt
    assert not re.search(
        r"(?<![A-Za-z0-9_])M8\d{3}(?![A-Za-z0-9_])",
        fx5_prompt,
    )
    assert "FX3U" in fx3_prompt
    assert "SM8002" not in fx3_prompt
    assert "FX5U/iQ-F" not in fx3_prompt


def test_api_query_compaction_uses_values_not_json_field_names():
    query = api._build_knowledge_query(
        "INC C1",
        {"rungs": [{"type": "instruction", "value": "INC C1"}]},
    )

    assert "INC C1" in query
    assert "rungs" not in query
    assert "type" not in query
    assert "value" not in query


def test_model_profile_keeps_full_fallback_when_retrieval_is_unavailable():
    full = api._build_model_context("FX3U", compact=False)
    compact = api._build_model_context("FX3U", compact=True)

    assert '"special_m"' in full
    assert '"special_d"' in full
    assert '"special_m"' not in compact
    assert '"manual_evidence"' in compact


def test_model_profile_keeps_confirmed_analog_hardware_and_access_rules():
    context = api._build_model_context(
        "FX3U",
        {
            "hardware_profile": {"plc_family": "FX3U", "control_method": "analog"},
            "hardware_context": {
                "analog_module": {
                    "input_module": "FX3U-4AD-ADP",
                    "output_module": "FX3U-4DA-ADP",
                }
            },
        },
        compact=True,
    )

    assert '"confirmed_hardware_context"' in context
    assert "FX3U-4AD-ADP" in context
    assert "FX3U-4DA-ADP" in context
    assert "D8260-D8263" in context
    assert "不得使用 WR3A" in context


def test_ladder_prompt_forbids_out_as_an_app_instruction():
    assert "OUT 的协议表示" in api.LADDER_SYSTEM_PROMPT
    assert "禁止生成 `{\"type\":\"APP_INSTR\",\"opcode\":\"OUT\"" in api.LADDER_SYSTEM_PROMPT


def test_generation_prompt_receives_retrieved_context_lazily(monkeypatch):
    captured = {}

    def fake_knowledge(primary_query, **kwargs):
        captured["primary_query"] = primary_query
        captured.update(kwargs)
        return "\n\n# RAG_SENTINEL\n"

    monkeypatch.setattr(api, "_build_knowledge_context", fake_knowledge)
    monkeypatch.setattr(api, "_select_system_prompt", lambda *args, **kwargs: "BASE")
    monkeypatch.setattr(api, "_build_model_context", lambda *args, **kwargs: "MODEL")
    messages, _history, _persist = api._prepare_api_call(
        "用PLSY输出1000个脉冲",
        "test-model",
        "high",
        "ladder",
        plc_model="FX3U",
        current_version_json={"rungs": []},
    )

    assert "# RAG_SENTINEL" in messages[0]["content"]
    assert captured["plc_model"] == "FX3U"
    assert captured["task_type"] == "generate"
    assert captured["evidence"] == {"rungs": []}


def test_analysis_debug_and_review_prompts_receive_retrieved_context(monkeypatch):
    prompts = []

    class PromptCaptured(RuntimeError):
        pass

    def capture_prompt(_history, system_prompt):
        prompts.append(system_prompt)
        raise PromptCaptured

    monkeypatch.setattr(
        api,
        "_build_knowledge_context",
        lambda *args, **kwargs: "\n\n# RAG_PHASE_SENTINEL\n",
    )
    monkeypatch.setattr(api, "_build_clean_messages", capture_prompt)
    monkeypatch.setattr(api, "load_full_config", lambda: {"plc_model": "FX3U"})
    monkeypatch.setattr(api, "_active_model_name", lambda _config=None: "fake")

    calls = [
        lambda: api.analyze_requirement("PLSY脉冲输出"),
        lambda: api.analyze_requirement_streaming("PLSY脉冲输出"),
        lambda: api.debug_ladder(
            "检查PLSY完成条件",
            {"device_comments": {}, "rungs": []},
        ),
        lambda: api.inspect_ladder(
            "program_review",
            {"review_focus": "PLSY完成逻辑"},
            {"device_comments": {}, "rungs": []},
            {},
        ),
    ]
    for call in calls:
        with pytest.raises(PromptCaptured):
            call()

    assert len(prompts) == 4
    assert all("# RAG_PHASE_SENTINEL" in prompt for prompt in prompts)


# Benchmark release-manifest isolation
from pathlib import Path

from tools.evaluate_rag_benchmark import _is_release_benchmark


def test_only_canonical_benchmark_updates_release_manifest(tmp_path: Path):
    root = tmp_path
    benchmarks = root / "benchmarks"
    benchmarks.mkdir()

    canonical = benchmarks / "fx3u_rag_benchmark.jsonl"
    custom = benchmarks / "fx3u_rag_benchmark_pre_skill.jsonl"
    canonical.write_text("{}\n", encoding="utf-8")
    custom.write_text("{}\n", encoding="utf-8")

    assert _is_release_benchmark(canonical, root) is True
    assert _is_release_benchmark(custom, root) is False

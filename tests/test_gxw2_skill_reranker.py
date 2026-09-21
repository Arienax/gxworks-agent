import knowledge.retriever as retriever
import pytest
from knowledge.gxworks2_concepts import query_skill_concepts
from knowledge.supporting_reranker import rerank


def _candidate(chunk_type, *, task_signal=True, matched="CONTINUE"):
    return {
        "manual_type": "third_party_skill",
        "chunk_type": chunk_type,
        "retrieval_signals": ["entity", "vector"] if task_signal else ["vector"],
        "matched_entity": matched,
        "score": 1500.0,
    }


def test_supporting_boost_requires_third_party_entity_route():
    candidate = _candidate("st_rule")
    assert retriever._gxw2_supporting_boost(candidate, "st") > 0

    no_entity = _candidate("st_rule", task_signal=False)
    assert retriever._gxw2_supporting_boost(no_entity, "st") == 0

    official = dict(candidate, manual_type="programming")
    assert retriever._gxw2_supporting_boost(official, "st") == 0


def test_supporting_boost_is_task_and_chunk_scoped():
    assert retriever._gxw2_supporting_boost(_candidate("st_rule"), "st") == 320.0
    assert retriever._gxw2_supporting_boost(_candidate("data_type", matched="DINT"), "analysis") == 220.0
    assert retriever._gxw2_supporting_boost(_candidate("compatibility", matched="FX3S"), "analysis") == 300.0
    assert retriever._gxw2_supporting_boost(_candidate("skill_instruction", matched="MOV"), "st") == 0
    assert retriever._gxw2_supporting_boost(_candidate("st_rule"), "debug") == 0


def test_supporting_boost_rejects_nonconcept_entity_hits():
    candidate = _candidate("st_rule", matched="MOV")
    assert retriever._gxw2_supporting_boost(candidate, "st") == 0


def test_weak_concepts_do_not_expand_generic_requests():
    assert retriever._query_has_gxw2_skill_concept("please revise this program") is False
    assert retriever._query_has_gxw2_skill_concept("check the output") is False
    assert retriever._query_has_gxw2_skill_concept("memory usage") is False


def test_weak_concepts_expand_when_gxworks_context_is_explicit():
    assert retriever._query_has_gxw2_skill_concept("FX3U GX Works2 ST program structure") is True
    assert retriever._query_has_gxw2_skill_concept("GX Works2 STRING support") is True


def test_strong_skill_concepts_expand_without_generic_context():
    assert retriever._query_has_gxw2_skill_concept("VAR_IN_OUT supported?") is True
    assert retriever._query_has_gxw2_skill_concept("INT_TO_REAL_E return value") is True


@pytest.mark.parametrize("query", [
    "please revise this program", "check the output", "memory usage",
    "new/delete a comment", "real time text processing", "case range label instance",
])
def test_generic_queries_have_no_derived_route(query):
    assert query_skill_concepts(query, "edit") == []


@pytest.mark.parametrize(("query", "concept"), [
    ("GX Works2 ST 注释应该用 // 吗", "GXW2_COMMENT_STYLE"),
    ("GX Works2 ST CASE 范围标签", "GXW2_CASE_LABELS"),
    ("GX Works2 FB、FUN 文件命名规则", "GXW2_POU_NAMING"),
    ("FX3G Structured Text STRING support", "GXW2_STRING_SUPPORT"),
    ("GX Works2 ST SR/RS 双稳态功能块", "GXW2_SR_RS"),
    ("GX Works2 ST __NEW", "__NEW"),
    ("GX Works2 ST __DELETE", "__DELETE"),
])
def test_qualified_routes_recognize_topics_and_preserve_identifiers(query, concept):
    assert concept in query_skill_concepts(query, "st")
    assert query_skill_concepts(query, "debug") == []


def test_rs_serial_instruction_and_structured_text_alone_do_not_route_to_rules():
    assert "GXW2_SR_RS" not in query_skill_concepts("FX3U RS serial communication", "st")
    assert query_skill_concepts("GX Works2 Structured Text", "st") == []
    assert "GXW2_POU_NAMING" not in query_skill_concepts("GX Works2 ST CASE 命名状态标签", "st")


def test_reserved_supporting_slot_keeps_authoritative_first_and_is_bounded():
    official = [dict(_candidate("section"), manual_type="programming", id=str(i),
                     score=2500-i) for i in range(10)]
    support = [dict(_candidate("st_rule", matched="GXW2_COMMENT_STYLE"), id="support1"),
               dict(_candidate("st_rule", matched="GXW2_CASE_LABELS"), id="support2")]
    ranked = rerank(official + support, "st")
    assert ranked[0] == official[0]
    assert ranked[1]["id"] == "support1"
    assert ranked[1]["score"] == 1820.0
    assert sum(bool(item.get("gxw2_supporting_slot")) for item in ranked) == 1
    assert support[0]["score"] == 1500.0


def test_supporting_candidate_budget_is_applied_after_rerank(monkeypatch):
    calls = []
    def retrieve(query, **kwargs):
        calls.append(kwargs)
        return [dict(_candidate("st_rule"), id="rule", text="Use IF/ELSE")]
    monkeypatch.setattr(retriever._core, "_retrieve_knowledge", retrieve)
    result = retriever.retrieve_knowledge("CONTINUE", task_type="st", top_k=5, char_budget=1000)
    assert calls[0]["top_k"] == 40
    assert calls[0]["char_budget"] > 160000
    assert result[0]["id"] == "rule"

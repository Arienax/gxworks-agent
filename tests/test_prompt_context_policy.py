"""Pure tests. No PLC, model SDK, network, credentials or manual database."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import dataclasses
import json
import threading
from types import SimpleNamespace

import pytest
from shared.context_policy import (
    ContextAudit, ContextPolicy, ContextPolicyError, POLICY_ENV, POLICY_NAMES,
    audit_request, audit_section, audit_retrieval_fragment, context_policy_scope, controlled_dynamic_prompt,
    manual_lookup_decision, resolve_context_policy, select_base_prompt,
)


@pytest.fixture(autouse=True)
def clean_policy(monkeypatch):
    monkeypatch.delenv(POLICY_ENV, raising=False)


def test_release_default_is_legacy():
    assert resolve_context_policy().name == "legacy"


@pytest.mark.parametrize("name", POLICY_NAMES)
def test_policy_snapshot_round_trip(name):
    assert resolve_context_policy(ContextPolicy(name).snapshot()) == ContextPolicy(name)


@pytest.mark.parametrize("bad", ["", "unsafe", 123, None, {"name": "minimal", "version": True},
                                {"name": "minimal", "version": 2}, {"name": "minimal"},
                                {"name": "minimal", "version": 1, "api_key": "secret"}])
def test_invalid_policy_is_rejected(bad, monkeypatch):
    if bad is None:
        monkeypatch.setenv(POLICY_ENV, "invalid")
    with pytest.raises(ContextPolicyError):
        resolve_context_policy(bad)


def test_policy_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        ContextPolicy("minimal").name = "combined"


def test_scope_freezes_environment_and_restores_after_exception(monkeypatch):
    monkeypatch.setenv(POLICY_ENV, "minimal")
    with pytest.raises(RuntimeError):
        with context_policy_scope():
            monkeypatch.setenv(POLICY_ENV, "manual")
            with context_policy_scope():
                assert resolve_context_policy().name == "minimal"
            raise RuntimeError()
    assert resolve_context_policy().name == "manual"


def test_nested_explicit_scope_restores_parent():
    with context_policy_scope("minimal"):
        with context_policy_scope("manual"):
            assert resolve_context_policy().name == "manual"
        assert resolve_context_policy().name == "minimal"
    assert resolve_context_policy().name == "legacy"


def test_threads_do_not_share_policy_or_audit():
    barrier = threading.Barrier(2)
    def run(name):
        collector = ContextAudit()
        with context_policy_scope(name, audit=collector):
            barrier.wait(timeout=5)
            audit_section("section", name)
            audit_request([{"role": "user", "content": "private request"}])
            return collector.snapshot()["requests"][0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run, "minimal")
        b = pool.submit(run, "manual")
        reports = [a.result(), b.result()]
    assert [x["policy"]["name"] for x in reports] == ["minimal", "manual"]
    assert all(len(x["sections"]) == 1 for x in reports)


def test_async_scopes_are_isolated():
    async def run(name):
        with context_policy_scope(name):
            await asyncio.sleep(0)
            return resolve_context_policy().name
    async def both():
        return await asyncio.gather(run("minimal"), run("manual"))
    assert asyncio.run(both()) == ["minimal", "manual"]


@pytest.mark.parametrize("name,expected", [("legacy", True), ("minimal", False), ("manual", True),
                                         ("examples", False), ("combined", True), ("adaptive", False)])
def test_manual_axis(name, expected):
    with context_policy_scope(name):
        assert manual_lookup_decision("start a motor")[0] is expected


@pytest.mark.parametrize("query", ["DRVI D100 D200 Y0", "FX3U-4AD-ADP", "M8029", "SD400", "C2015 编译错误",
                                  "VAR_IN_OUT 参数", "请查阅手册", "TO K0 K1 D0 K1"])
def test_adaptive_has_deterministic_evidence_triggers(query):
    with context_policy_scope("adaptive"):
        assert manual_lookup_decision(query)[0]


@pytest.mark.parametrize("query", ["motor version rapid", "move to the next step", "从启动到停止", "X0 控制 Y0", ""])
def test_adaptive_does_not_match_generic_prose(query):
    with context_policy_scope("adaptive"):
        assert not manual_lookup_decision(query)[0]


# Small synthetic fixtures exercise marker contracts, not PLC correctness.
LADDER = ("SCHEMA AND HARD RULES\n### FX3U 的 M8029 正例：同一 rung 双 branch\nMOTION_DEMO\n"
          "### 一、 JSON Schema 协议架构规范\nPRIVATE_SCHEMA\n"
          "### 二、 经典多模范例（Few-Shot Skill）\nTRAFFIC_LIGHT_DEMO\n"
          "# ✅ 输出前自检清单\nCHECKS\n3. **自动步进化**：old preference\nFINAL_JSON")
ST = ("ROLE\n# 分析流程（内部）\nAUTO_ADD_X12\n# 【严格遵守的语法与类型规范】\nST_SYNTAX\n"
      "# 【工业常识模式库】\nPUMP_PATTERN\n# 【输出前自检清单】\nCHECKS\n"
      "5. □ 步进流程从初始化 anything\n# 范例\nCYLINDER_DEMO\n# 【最终输出约束】\nST_JSON")


@pytest.mark.parametrize("name", POLICY_NAMES)
def test_base_prompt_policy(name):
    with context_policy_scope(name):
        result = select_base_prompt(LADDER, "ladder")
    if name == "legacy":
        assert result == LADDER
    else:
        assert "MOTION_DEMO" not in result and "TRAFFIC_LIGHT_DEMO" not in result
        assert "old preference" not in result
        assert all(x in result for x in ("PRIVATE_SCHEMA", "CHECKS", "FINAL_JSON", "shared_inputs"))


@pytest.mark.parametrize("name", ["minimal", "manual", "examples", "combined", "adaptive"])
def test_all_controlled_arms_share_one_base(name):
    with context_policy_scope("minimal"):
        baseline = select_base_prompt(LADDER, "ladder")
    with context_policy_scope(name):
        assert select_base_prompt(LADDER, "ladder") == baseline


def test_st_removes_unsolicited_completion_and_examples_but_not_contract():
    with context_policy_scope("minimal"):
        result = select_base_prompt(ST, "st")
    assert not any(x in result for x in ("AUTO_ADD_X12", "PUMP_PATTERN", "CYLINDER_DEMO", "步进流程从初始化"))
    assert all(x in result for x in ("ST_SYNTAX", "ST_JSON", "CHECKS"))


def test_unknown_backend_is_not_rewritten():
    with context_policy_scope("minimal"):
        assert select_base_prompt("FBD_CONTRACT", "fbd") == "FBD_CONTRACT"


def test_prompt_marker_drift_fails_without_silent_legacy_fallback():
    with context_policy_scope("minimal"):
        with pytest.raises(ContextPolicyError):
            select_base_prompt(LADDER.replace("# ✅ 输出前自检清单", "renamed"), "ladder")


LIBRARY = {"examples": [
    {"id": "example_self_lock", "header": "self lock", "content": "SELF_LOCK", "priority": 10},
    {"id": "example_counter", "header": "counter", "content": "COUNTER", "priority": 20},
    {"id": "example_st", "header": "ST", "content": "ST_EXAMPLE", "target_mode": "st", "plc_models": ["FX3U"]},
]}


def test_legacy_assembler_sentinel():
    assert controlled_dynamic_prompt({}, LIBRARY, "ladder", "FX3U") is None


def test_no_match_means_no_example():
    with context_policy_scope("examples"):
        assert controlled_dynamic_prompt({"matched_ids": set()}, LIBRARY, "ladder", "FX3U") == ""


def test_example_axis_is_independent_of_manual_axis():
    classification = {"matched_ids": {"example_counter"}}
    results = {}
    for name in ("minimal", "manual", "examples", "combined"):
        with context_policy_scope(name):
            results[name] = controlled_dynamic_prompt(classification, LIBRARY, "ladder", "FX3U")
    assert results["minimal"] == results["manual"] == ""
    assert results["examples"] == results["combined"]
    assert "COUNTER" in results["examples"] and "SELF_LOCK" not in results["examples"]


def test_examples_do_not_cross_target_or_model():
    classification = {"matched_ids": {"example_self_lock", "example_st"}}
    with context_policy_scope("examples"):
        assert controlled_dynamic_prompt(classification, LIBRARY, "ladder", "FX5U") == ""
        result = controlled_dynamic_prompt(classification, LIBRARY, "st", "FX3U")
    assert "ST_EXAMPLE" in result and "SELF_LOCK" not in result


def test_whole_example_budget_no_truncation():
    with context_policy_scope("examples"):
        result = controlled_dynamic_prompt({"matched_ids": {"example_counter"}}, LIBRARY, "ladder", "FX3U", char_budget=5)
    assert result == ""


def test_example_count_cap_and_deterministic_order():
    classification = {"matched_ids": {"example_counter", "example_self_lock"}}
    with context_policy_scope("examples"):
        one = controlled_dynamic_prompt(classification, LIBRARY, "ladder", "FX3U", max_examples=1)
        two = controlled_dynamic_prompt(classification, LIBRARY, "ladder", "FX3U", max_examples=2)
    assert "SELF_LOCK" in one and "COUNTER" not in one
    assert two.index("SELF_LOCK") < two.index("COUNTER")


def test_audit_contains_no_text_paths_or_credentials():
    collector = ContextAudit()
    secret = "SECRET_API_KEY C:\\private\\customer.gxw manual excerpt"
    with context_policy_scope("minimal", audit=collector):
        audit_section("base_prompt", secret)
        audit_request([{"role": "system", "content": secret},
                       SimpleNamespace(role="user", content="需求", images=(object(),)),
                       {"role": "user", "content": [{"type": "text", "text": "问题"},
                                                      {"type": "image_url", "image_url": secret}]}])
    result = collector.snapshot()
    serialized = json.dumps(result)
    assert secret not in serialized and "SECRET_API_KEY" not in serialized
    report = result["requests"][0]
    assert report["measurement"] == "characters_not_tokens"
    assert report["section_counts_are_additive"] is False
    assert [m["images"] for m in report["messages"]] == [0, 1, 1]
    assert report["message_text_chars"] == len(secret) + 4


def test_audit_is_optional_and_writes_nothing(capsys):
    with context_policy_scope("minimal"):
        audit_section("base_prompt", "secret")
        audit_request([])
    assert capsys.readouterr() == ("", "")


def test_audit_resets_sections_per_request_and_defends_snapshot_mutation():
    collector = ContextAudit()
    with context_policy_scope("minimal", audit=collector):
        audit_section("first", "abc")
        audit_request([])
        audit_request([])
    report = collector.snapshot()
    assert len(report["requests"][0]["sections"]) == 1
    assert report["requests"][1]["sections"] == []
    report["requests"][0]["sections"].clear()
    assert collector.snapshot()["requests"][0]["sections"]


def test_explicit_nested_policy_does_not_mix_audits():
    collector = ContextAudit()
    with context_policy_scope("minimal", audit=collector):
        with context_policy_scope("manual"):
            audit_section("child", "data")
            audit_request([])
        audit_request([])
    assert len(collector.snapshot()["requests"]) == 1
    assert collector.snapshot()["requests"][0]["sections"] == []


def test_bad_sink_does_not_change_workflow_or_leak_exception(capsys):
    def sink(report):
        raise RuntimeError("DO_NOT_LOG_THIS_SECRET")
    collector = ContextAudit(sink)
    with context_policy_scope("minimal", audit=collector):
        audit_request([])
    capture = capsys.readouterr()
    assert not capture.out and "unavailable" in capture.err
    assert "DO_NOT_LOG_THIS_SECRET" not in capture.err


def test_audit_has_bounded_pending_sections():
    collector = ContextAudit()
    with context_policy_scope("minimal", audit=collector):
        for _ in range(300):
            audit_section("constant", "text")
        audit_request([])
    report = collector.snapshot()["requests"][0]
    assert len(report["sections"]) == 256 and report["dropped_sections"] == 44


def test_fragment_audit_keeps_identifiers_not_document_text_or_location():
    collector = ContextAudit()
    with context_policy_scope("manual", audit=collector):
        audit_retrieval_fragment({"manual_id": "fx3_programming_r", "id": 27,
                                  "url": "file:///private/location", "text": "private body"}, "private body")
    record = collector.snapshot()["pending_sections"][0]
    assert record["source"] == "fx3_programming_r"
    assert record["section"] == "manual_chunk:27"
    assert record["chars"] == len("private body")
    assert not any(x in json.dumps(record) for x in ("private body", "location", "file:"))


def test_fragment_audit_hashes_non_identifier_metadata_and_marks_budget_exclusion():
    collector = ContextAudit()
    with context_policy_scope("manual", audit=collector):
        audit_retrieval_fragment({"manual_id": "C:/private/manual.pdf", "id": "chunk with spaces"},
                                 "private fragment", included=False)
    record = collector.snapshot()["pending_sections"][0]
    assert record["source"].startswith("unknown_source_")
    assert record["status"] == "excluded" and record["reason"] == "context_budget"
    assert "private" not in json.dumps(record)


@pytest.mark.parametrize("query", ["manual mode switch", "manual control", "manual operation"])
def test_manual_operating_mode_is_not_a_document_lookup(query):
    with context_policy_scope("adaptive"):
        assert manual_lookup_decision(query) == (False, "no_lookup_signal")

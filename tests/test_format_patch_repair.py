import hashlib
import json
from types import SimpleNamespace

import pytest

import application.model_workflows as api
from application.base import model_call
from application.format_patch_repair import (
    FORMAT_PATCH_SYSTEM_PROMPT,
    apply_format_patch,
    format_repair_response,
)
from application.generation import GenerationDependencies, GenerationRequest, GenerationWorkflow


def _payload(candidate):
    return {
        "repair_mode": "format",
        "plc_model": "FX3U",
        "instruction": (
            "这是用户明确确认的一次 JSON 格式修复。\n"
            "失败位置：content$.r[0]\n\n失败候选 JSON：\n" + candidate
        ),
    }


def test_format_repair_never_instructs_model_to_rewrite_complete_ladder():
    lowered = FORMAT_PATCH_SYSTEM_PROMPT.casefold()
    assert "never output the full repaired json" in lowered
    assert "return the complete corrected ladder json" not in lowered
    assert "complete top-level ladder json" not in lowered
    assert '"mode":"format_patch"' in FORMAT_PATCH_SYSTEM_PROMPT


def test_recurring_compact_missing_quote_repairs_locally_without_model(monkeypatch):
    broken = '{"r":[{"b":[{"i":["> D220 D106],"o":["COIL M200"]}]}]}'
    monkeypatch.setattr(api, "_request_model", lambda *a, **k: pytest.fail("deterministic syntax repair called model"))

    reasoning, content = model_call(
        api.repair_ladder_response,
        _payload(broken),
        "offline-model",
        "high",
        mode="format",
    )

    assert reasoning == ""
    ladder = json.loads(content)
    assert len(ladder["rungs"]) == 1
    branch = ladder["rungs"][0]["branches"][0]
    assert branch["inputs"] == [{"type": "COMPARE", "expression": "> D220 D106"}]
    assert branch["outputs"] == [{"type": "COIL", "address": "M200"}]


def test_format_patch_can_change_json_punctuation_but_not_plc_tokens():
    raw = '{"r":[{"b":[{"i":["NO X0"] "o":["COIL Y0"]}]}]}'
    digest = hashlib.sha256(raw.encode()).hexdigest()
    patch = {
        "schema_version": 1,
        "mode": "format_patch",
        "base_sha256": digest,
        "patches": [{"before": '["NO X0"] "o"', "after": '["NO X0"],"o"'}],
    }
    repaired = apply_format_patch(raw, patch)
    assert json.loads(repaired)["r"][0]["b"][0]["o"] == ["COIL Y0"]

    semantic_change = {
        **patch,
        "patches": [{"before": '["NO X0"] "o"', "after": '["NO X1"],"o"'}],
    }
    with pytest.raises(ValueError, match="changes PLC/content tokens"):
        apply_format_patch(raw, semantic_change)


def test_ambiguous_format_repair_model_returns_only_patch_then_local_code_expands(monkeypatch):
    raw = '{"r":[{"b":[{"i":["NO X0"] "o":["COIL Y0"]}]}]}'
    digest = hashlib.sha256(raw.encode()).hexdigest()
    patch = {
        "schema_version": 1,
        "mode": "format_patch",
        "base_sha256": digest,
        "patches": [{"before": '["NO X0"] "o"', "after": '["NO X0"],"o"'}],
    }
    captured = {}

    def fake_request(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return SimpleNamespace(message=SimpleNamespace(
            reasoning="local punctuation fix",
            content=json.dumps(patch, separators=(",", ":")),
        ))

    monkeypatch.setattr(api, "_request_model", fake_request)
    reasoning, content = format_repair_response(
        _payload(raw), "offline-model", "high"
    )

    assert reasoning == "local punctuation fix"
    assert "rejected_candidate" in captured["messages"][1]["content"]
    assert "full repaired" not in captured["messages"][0]["content"].casefold().replace("never output the full repaired json", "")
    assert captured["kwargs"]["response_contract"].name == "ladder_format_patch"
    ladder = json.loads(content)
    assert ladder["rungs"][0]["branches"][0]["inputs"][0] == {"type": "NO", "address": "X0"}
    assert ladder["rungs"][0]["branches"][0]["outputs"][0] == {"type": "COIL", "address": "Y0"}


def test_failed_patch_request_returns_original_candidate_for_renderable_failure(monkeypatch):
    raw = '{"r":[{"b":[{"i":["NO X0"] "o":["COIL Y0"]}]}]}'
    monkeypatch.setattr(api, "_request_model", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provider down")))

    reasoning, content = format_repair_response(_payload(raw), "offline-model", "high")

    assert reasoning == ""
    assert content == raw


def test_deterministically_repaired_compact_candidate_always_renders_svg_and_csv(monkeypatch, tmp_path):
    broken = '{"r":[{"b":[{"i":["NO X0","> D220 D106],"o":["COIL Y0"]}]}]}'
    monkeypatch.setattr(api, "_request_model", lambda *a, **k: pytest.fail("repair should be local"))
    request = GenerationRequest(
        user_input=(
            "这是用户明确确认的一次 JSON 格式修复。\n"
            "失败位置：content$.r[0]\n\n失败候选 JSON：\n" + broken
        ),
        target_mode="ladder",
        format_repair=True,
        confirmed_context={"summary": "X0 controls Y0", "io_table": [], "parameters": []},
        plc_model="FX3U",
        model_name="offline-model",
    )

    result = GenerationWorkflow(
        request,
        tmp_path,
        dependencies=GenerationDependencies(),
    ).run()

    assert result["target_mode"] == "ladder"
    assert result["validation"]["status"] == "candidate_ready"
    for artifact in ("svg", "program_csv", "comment_csv"):
        path = tmp_path / result["artifacts"][artifact]
        assert path.is_file() and path.stat().st_size > 0
    assert "<svg" in (tmp_path / result["artifacts"]["svg"]).read_text(encoding="utf-8")

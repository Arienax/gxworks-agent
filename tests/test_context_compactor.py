from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import application.model_api as api
from application.context_compactor import compact_if_needed
from application.context_compiler import ContextCompiler, ContextCompilerInput
from application.generation_wire import (
    render_context_checkpoint,
    render_wire_messages,
)


def _profile(window=24_000, output=2_048):
    return {
        "context_window": window,
        "generationDefaults": {"max_completion_tokens": output},
    }


def _spec(requests=10):
    rows = []
    for index in range(requests):
        rows.append({
            "id": f"r{index}",
            "source": "user_request",
            "text": f"历史请求{index} X{index} K{index} " + ("历史上下文" * 350),
        })
    return {
        "schema_version": 4,
        "plc_model": "FX3U",
        "summary": "confirmed",
        "parameters": [{"id": "delay", "name": "delay", "value": "K10"}],
        "io_table": [
            {"kind": "X", "address": "X0", "label": "start"},
            {"kind": "Y", "address": "Y0", "label": "run"},
        ],
        "io_bindings": [
            {"binding_id": "start", "role": "start", "kind": "X", "address": "X0"},
            {"binding_id": "run", "role": "output", "kind": "Y", "address": "Y0"},
        ],
        "selected_approach": {
            "approach_id": "a1",
            "name": "direct",
            "implementation_semantics": [],
            "generation_contract": {
                "required_structures": ["direct_logic"],
                "required_opcodes": [],
            },
        },
        "intent_context": {"schema_version": 1, "requests": rows},
    }


def _renderer(runtime, evidence, request, _program, checkpoint, history):
    system = (
        "# SYSTEM\n"
        + json.dumps(runtime, ensure_ascii=False, separators=(",", ":"))
        + render_context_checkpoint(checkpoint)
        + "\n# EVIDENCE\n"
        + str(evidence or "")
    )
    visible_history = history or [{"role": "user", "content": request}]
    return {"messages": render_wire_messages(system, visible_history)}


def _input(specification, *, history=None, window=24_000):
    return ContextCompilerInput(
        confirmed_spec=specification,
        intent_context=specification.get("intent_context"),
        selected_approach=specification.get("selected_approach") or {},
        model_profile=_profile(window),
        plc_model="FX3U",
        task_type="edit",
        generation_request="最新修改：保持 X0/Y0，不改变 K10。",
        wire_renderer=_renderer,
        wire_history=copy.deepcopy(history or [
            {"role": "user", "content": "较早聊天 " + ("A" * 1000)},
            {"role": "assistant", "content": "较早回复 " + ("B" * 1000)},
            {"role": "user", "content": "最新修改：保持 X0/Y0，不改变 K10。"},
        ]),
    )


def test_over_budget_context_is_replaced_by_checkpoint_and_recent_tail(monkeypatch):
    source = _spec()
    before = copy.deepcopy(source)
    compiler = ContextCompiler()
    value = _input(source)
    initial = compiler.compile(value)
    assert initial.budget_report["budget_exceeded_after_compaction"] is True

    calls = []

    def compact(messages, **kwargs):
        calls.append((copy.deepcopy(messages), copy.deepcopy(kwargs)))
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            "checkpoint": (
                "Earlier intent established a direct FX3U control path. "
                "Preserve the historical X/K identifiers only as continuity; "
                "current confirmed I/O and K10 remain authoritative."
            )
        })))

    monkeypatch.setattr(api, "request_model", compact)
    result, compacted_input = compact_if_needed(
        compiler, value, initial, evidence_text=""
    )

    assert len(calls) == 1
    report = result.budget_report["context_compaction"]
    assert report["status"] == "installed"
    assert report["model_calls"] == 1
    assert report["post_wire_tokens"] < report["pre_wire_tokens"]
    assert result.budget_report["compression_mode"] == "checkpoint_compaction"
    assert result.provenance_receipt["compression_mode"] == "checkpoint_compaction"

    runtime = result.generation_packet["confirmed_spec"]
    rows = runtime["intent_context"]["requests"]
    compacted_ids = set(report["compacted_request_ids"])
    assert compacted_ids
    for row in rows:
        if row["id"] in compacted_ids:
            assert "text" not in row
            assert row["runtime_text_status"] == "compacted_checkpoint"
            assert row["text_sha256"]
    assert rows[-1]["text"] == source["intent_context"]["requests"][-1]["text"]
    assert rows[-2]["text"] == source["intent_context"]["requests"][-2]["text"]

    wire = json.dumps(result.wire_packet, ensure_ascii=False)
    assert "# Compacted historical context" in wire
    assert "Earlier intent established" in wire
    assert source == before
    assert compacted_input.context_checkpoint


def test_compactor_failure_does_not_create_a_new_hard_error(monkeypatch):
    source = _spec()
    compiler = ContextCompiler()
    value = _input(source)
    initial = compiler.compile(value)
    assert initial.budget_report["budget_exceeded_after_compaction"] is True

    monkeypatch.setattr(
        api,
        "request_model",
        lambda *a, **k: SimpleNamespace(
            message=SimpleNamespace(content='{"checkpoint":""}')
        ),
    )
    result, returned_input = compact_if_needed(compiler, value, initial)

    assert result.generation_packet == initial.generation_packet
    assert result.budget_report["context_compaction"]["status"] == "model_failed"
    assert returned_input == value


def test_under_budget_context_does_not_call_compactor(monkeypatch):
    source = _spec(requests=2)
    compiler = ContextCompiler()
    value = _input(source, history=[{"role": "user", "content": "current"}], window=200_000)
    initial = compiler.compile(value)
    assert initial.budget_report["budget_exceeded_after_compaction"] is False

    monkeypatch.setattr(
        api,
        "request_model",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("compactor must stay idle")),
    )
    result, returned_input = compact_if_needed(compiler, value, initial)
    assert result == initial
    assert returned_input == value

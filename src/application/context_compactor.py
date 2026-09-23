"""Checkpoint compaction for over-budget generation context.

The compiler decides what is protected/recent/compactable. The model only
summarizes the selected historical span; it never rewrites confirmed facts.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from collections.abc import Mapping

from knowledge.evidence import estimate_tokens
from model_runtime.provider import ModelProviderError
from model_runtime.responses import ResponseContract


COMPACTION_VERSION = "checkpoint-v1"
_CONTEXT_CHECKPOINT_RESPONSE = ResponseContract("context_checkpoint", "json")

_COMPACTOR_SYSTEM_PROMPT = """You are a context compactor for an engineering agent.

Summarize ONLY the historical fragments supplied in compactable_fragments.
The current confirmed engineering specification, recent verbatim tail, current
program, and structured PLC facts are carried separately and are authoritative.

Preserve information needed to continue the task:
- active user intent, corrections, decisions, and unresolved requirements;
- exact identifiers, PLC addresses, opcodes, operands, numeric values, error
  strings, file/object names, and other concrete technical details present in
  the supplied historical fragments;
- relevant progress, failed approaches, and pending work.

Do not invent facts. Do not upgrade old prose into a confirmed requirement.
When old statements conflict with a correction visible in recent_tail, retain
the correction and omit the superseded statement. Avoid restating recent_tail.

Return one JSON object only:
{"checkpoint":"..."}

The checkpoint should be compact, information-dense prose. No markdown fences.
"""


def _message_tokens(message):
    if not isinstance(message, Mapping):
        return 0
    return estimate_tokens(str(message.get("content") or ""))


def _recent_history(history, token_budget):
    rows = [
        copy.deepcopy(dict(row))
        for row in (history or ())
        if isinstance(row, Mapping) and row.get("role") in {"user", "assistant"}
    ]
    if not rows:
        return [], []
    budget = max(0, int(token_budget or 0))
    selected = []
    used = 0
    for row in reversed(rows):
        cost = _message_tokens(row)
        # Always preserve the latest model-visible message.
        if selected and used + cost > budget:
            break
        selected.append(row)
        used += cost
    selected.reverse()
    old_count = max(0, len(rows) - len(selected))
    return rows[:old_count], selected


def _recent_request_ids(requests, minimum=2):
    text_rows = [
        row for row in (requests or ())
        if isinstance(row, Mapping) and str(row.get("text") or "").strip()
    ]
    return {
        str(row.get("id") or "")
        for row in text_rows[-max(1, int(minimum)):]
        if str(row.get("id") or "")
    }


def select_compactable_context(compiled, compiler_input):
    """Select an oldest prefix while retaining recent model-visible context verbatim."""
    report = compiled.budget_report
    usable = report.get("usable_input_tokens")
    if not isinstance(usable, int) or usable <= 0:
        return None

    runtime = compiled.generation_packet.get("confirmed_spec") or {}
    intent = runtime.get("intent_context") if isinstance(runtime, Mapping) else {}
    requests = intent.get("requests") if isinstance(intent, Mapping) else []
    requests = requests if isinstance(requests, list) else []

    recent_request_ids = _recent_request_ids(requests, minimum=2)
    compactable_requests = [
        row for row in requests
        if isinstance(row, Mapping)
        and str(row.get("text") or "").strip()
        and str(row.get("id") or "") not in recent_request_ids
    ]

    recent_budget = max(1024, min(16000, int(usable * 0.16)))
    old_history, recent_history = _recent_history(
        compiler_input.wire_history, recent_budget,
    )

    fragments = []
    seen_text = set()

    def add(identifier, role, text):
        value = str(text or "").strip()
        if not value:
            return
        marker = value
        if marker in seen_text:
            return
        seen_text.add(marker)
        fragments.append({"id": identifier, "role": role, "text": value})

    for row in compactable_requests:
        add(
            "request:" + str(row.get("id") or ""),
            "historical_user_intent",
            row.get("text"),
        )
    for index, row in enumerate(old_history):
        add(
            f"message:{index}:{row.get('role')}",
            "historical_" + str(row.get("role") or "message"),
            row.get("content"),
        )

    if not fragments:
        return None

    recent_tail = []
    for row in requests:
        if isinstance(row, Mapping) and str(row.get("id") or "") in recent_request_ids:
            recent_tail.append({
                "id": "request:" + str(row.get("id") or ""),
                "role": "recent_user_intent",
                "text": str(row.get("text") or ""),
            })
    for index, row in enumerate(recent_history):
        recent_tail.append({
            "id": f"recent_message:{index}:{row.get('role')}",
            "role": "recent_" + str(row.get("role") or "message"),
            "text": str(row.get("content") or ""),
        })

    source_tokens = estimate_tokens(json.dumps(
        fragments, ensure_ascii=False, separators=(",", ":")
    ))
    pre_tokens = int(report.get("compiled_budget_payload_tokens") or 0)
    required_reduction = max(0, pre_tokens - usable)
    target_tokens = max(
        256,
        min(4096, max(256, source_tokens - required_reduction - 512)),
    )
    return {
        "version": COMPACTION_VERSION,
        "compactable_fragments": fragments,
        "recent_tail": recent_tail,
        "compacted_request_ids": [
            str(row.get("id") or "") for row in compactable_requests
            if str(row.get("id") or "")
        ],
        "recent_history": recent_history,
        "old_history_messages": len(old_history),
        "source_tokens": source_tokens,
        "target_tokens": target_tokens,
        "pre_wire_tokens": pre_tokens,
        "usable_input_tokens": usable,
    }


def _checkpoint_from_response(content):
    value = json.loads(str(content or "").strip())
    if not isinstance(value, Mapping):
        raise ValueError("context checkpoint response must be an object")
    checkpoint = str(value.get("checkpoint") or "").strip()
    if not checkpoint:
        raise ValueError("context checkpoint is empty")
    return checkpoint


def compact_if_needed(compiler, compiler_input, compiled, *, evidence_text=""):
    """Run at most one checkpoint compaction call and recompile the actual wire."""
    if not compiled.budget_report.get("budget_exceeded_after_compaction"):
        return compiled, compiler_input
    selection = select_compactable_context(compiled, compiler_input)
    if selection is None:
        return compiled, compiler_input

    import application.model_api as api

    payload = {
        "version": COMPACTION_VERSION,
        "target_tokens": selection["target_tokens"],
        "compactable_fragments": selection["compactable_fragments"],
        "recent_tail": selection["recent_tail"],
    }
    try:
        response = api.request_model(
            [
                {"role": "system", "content": _COMPACTOR_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                )},
            ],
            effort=None,
            stream=False,
            max_retries=0,
            response_contract=_CONTEXT_CHECKPOINT_RESPONSE,
        )
        checkpoint = _checkpoint_from_response(response.message.content)
    except (ModelProviderError, ValueError, TypeError, json.JSONDecodeError):
        report = copy.deepcopy(compiled.budget_report)
        report["context_compaction"] = {
            "version": COMPACTION_VERSION,
            "status": "model_failed",
            "model_calls": 1,
            "source_tokens": selection["source_tokens"],
            "pre_wire_tokens": selection["pre_wire_tokens"],
        }
        return replace(compiled, budget_report=report), compiler_input

    compacted_input = replace(
        compiler_input,
        context_checkpoint=checkpoint,
        compacted_request_ids=tuple(selection["compacted_request_ids"]),
        wire_history=copy.deepcopy(selection["recent_history"]),
    )
    compacted = compiler.compile(compacted_input, evidence_text=evidence_text)
    post_tokens = int(
        compacted.budget_report.get("compiled_budget_payload_tokens") or 0
    )
    if post_tokens >= selection["pre_wire_tokens"]:
        report = copy.deepcopy(compiled.budget_report)
        report["context_compaction"] = {
            "version": COMPACTION_VERSION,
            "status": "not_installed_no_shrink",
            "model_calls": 1,
            "source_tokens": selection["source_tokens"],
            "checkpoint_tokens": estimate_tokens(checkpoint),
            "pre_wire_tokens": selection["pre_wire_tokens"],
            "candidate_post_wire_tokens": post_tokens,
        }
        return replace(compiled, budget_report=report), compiler_input

    report = copy.deepcopy(compacted.budget_report)
    report["compression_mode"] = "checkpoint_compaction"
    report["compaction_saved_tokens"] = max(
        0, selection["pre_wire_tokens"] - post_tokens
    )
    report["context_compaction"] = {
        "version": COMPACTION_VERSION,
        "status": "installed",
        "model_calls": 1,
        "source_tokens": selection["source_tokens"],
        "checkpoint_tokens": estimate_tokens(checkpoint),
        "target_tokens": selection["target_tokens"],
        "pre_wire_tokens": selection["pre_wire_tokens"],
        "post_wire_tokens": post_tokens,
        "compacted_request_ids": list(selection["compacted_request_ids"]),
        "old_history_messages": selection["old_history_messages"],
        "recent_history_messages": len(selection["recent_history"]),
        "remaining_over_budget": bool(
            compacted.budget_report.get("budget_exceeded_after_compaction")
        ),
    }
    receipt = copy.deepcopy(compacted.provenance_receipt)
    receipt["compression_mode"] = "checkpoint_compaction"
    receipt["context_compaction_version"] = COMPACTION_VERSION
    return (
        replace(compacted, budget_report=report, provenance_receipt=receipt),
        compacted_input,
    )

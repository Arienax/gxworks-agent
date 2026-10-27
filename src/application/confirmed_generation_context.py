# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Shared engineering facts for compact and ladder_v1 generation adapters.

No provider, credentials, SDK or wire-schema selection belongs here. All callers
receive detached snapshots; private analysis prose is not an engineering fact.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, fields

from application.generation_support import (
    public_generation_ladder, public_generation_specification, public_generation_value,
)
from plc.device_identity import canonical_device, canonical_io_rows


CONFIRMED_GENERATION_REQUEST = (
    "根据已经由用户确认的规格生成完整梯形图。"
    "不得重新分析需求、提出问题或改变已确认 I/O、触点极性、参数和所选方案。"
    "输入条件的 OR 必须在同一个输出分支中表示；"
    "不得把 (A OR B) -> 同一输出 拆成多个 output branch/多个 branches 来表达。"
    "只返回一份最终 JSON；顶层对象闭合后立即结束回复，"
    "不得在同一次 completion 中自检后再重写或追加第二份完整 JSON。"
)



# Prompt policy only: no reasoning-token limit, truncation, retry or acceptance gate.
GENERATION_EXECUTION_POLICY_VERSION = "settled-facts-v8-native-timing"
GENERATION_EXECUTION_POLICY = """# Generation execution policy
只实现当前确认规格与所选方案。已确认绑定、参数及本轮修改优先于过时的参考说明；保留其余方案约束。仅因具体矛盾、新证据或用户修订重审。
技术事实按需核对；检索文本不代表完整覆盖，缺证据不等于禁用，不编造查证或测试结果。
NO 在位=1时导通，NC 在位=0时导通；二者只读位值，不设定初始值。按下/释放含义取自确认电平，现场接线不直接决定程序触点。P 检测0→1，F 检测1→0。
下方谓词是当前绑定的电平事实；active 表示信号用途成立，run_permit 表示停止未生效。未定电平保持未知；edit 时本轮修改优先。边沿与完整工艺逻辑仍按规格实现。
检查启动可达性、串联条件一致性及停止/故障与计数、状态转移的同扫描优先级。按既有协议输出一份最终程序。"""

DIRECT_EXECUTION_POLICY = """# Direct execution policy
完整原文和明确答复都是工艺事实来源。结构化事实只是辅助视图，未提取不等于未提供；缺少确认规格不构成生成条件。
内部地址、指令、数据结构和实现方法由生成器自行决定，可由已知条件推导的参数直接推导。
用户未要求的额外功能不成为前置条件；真实现场信息不能伪造为已确认事实。
已有明确确认事实继续继承，本轮明确修订更新对应字段。只输出一份程序及简短的已采用选择和可选调整说明。
NO 在位=1时导通，NC 在位=0时导通；输入含义取自原文、明确答复和已有电平事实。P 检测0→1，F 检测1→0。
下方谓词只是已提取的辅助事实；未解析字段仍须结合完整原文理解，不等于用户没有提供。"""


def generation_execution_prompt(confirmed_spec, *, evidence_text="", task_type="generate", plc_model=None, raw_request=False):
    """Render one shared materialization policy from the current public snapshot.

    No persisted spec fields are removed or reconciled by parsing natural language.
    Evidence availability is explicitly NOT an evidence-coverage assertion.
    """
    if task_type not in {"generate", "edit"}:
        return ""
    from plc.specification.conditions import generation_input_conditions
    spec = confirmed_spec if isinstance(confirmed_spec, Mapping) else {}
    facts = generation_input_conditions(spec.get("io_bindings"), plc_model=plc_model or spec.get("plc_model") or "FX3U")
    facts["basis"] = "explicit_user_facts" if raw_request else "edit_baseline" if task_type == "edit" else "current_confirmed_bindings"
    facts["retrieved_text_present"] = bool(str(evidence_text or "").strip())
    policy = DIRECT_EXECUTION_POLICY if raw_request else GENERATION_EXECUTION_POLICY
    from plc.timing import scan_timing_guidance
    timing = scan_timing_guidance(plc_model or spec.get("plc_model") or "FX3U")
    return ("\n\n" + policy + "\n\n" + timing + "\n# Settled input predicates (not a new requirement)\n"
            + json.dumps(public_generation_value(facts), ensure_ascii=False, separators=(",", ":")))


def project_confirmed_specification(confirmed_spec):
    """Expose the same allowlisted, canonical confirmed facts to every adapter."""
    from plc.specification.bindings import generation_io_snapshot
    from plc.hardware_profiles import QUESTION_IDS
    source = (generation_io_snapshot(dict(confirmed_spec), protected_ids=QUESTION_IDS)
              if isinstance(confirmed_spec, Mapping) else None)
    projected = public_generation_specification(source) or {}
    if projected:
        projected["schema_version"] = 4
    # Preserve the existing public-projection contract: malformed optional
    # label metadata is omitted, not replaced while enriching electrical facts.
    raw_bindings = confirmed_spec.get("io_bindings") if isinstance(confirmed_spec, Mapping) else None
    invalid_labels = {row.get("binding_id") for row in raw_bindings
                      if isinstance(row, Mapping) and "label" in row
                      and not isinstance(row["label"], str)} if isinstance(raw_bindings, list) else set()
    bindings = source.get("io_bindings") if source is not None else None
    if isinstance(bindings, list):
        projected["io_bindings"] = [
            {key: public_generation_value(copy.deepcopy(row[key]))
             for key in ("binding_id", "role", "kind", "address", "source_parameter_id", "name",
                         "active_level", "inactive_level", "label")
             if key in row and (key != "label" or isinstance(row[key], str) and row.get("binding_id") not in invalid_labels)}
            for row in bindings if isinstance(row, Mapping)
        ]
    if isinstance(projected.get("io_table"), list):
        projected["io_table"] = canonical_io_rows(projected["io_table"])
    for row in projected.get("io_bindings", []):
        if "address" in row:
            row["address"] = canonical_device(row["address"])
    return projected


@dataclass(frozen=True)
class ConfirmedGenerationContext:
    """A detached engineering snapshot, independent of compact/full wire format."""
    plc_model: str
    confirmed_spec: dict
    io_bindings: list
    generation_contract: dict
    knowledge_context: str
    current_program: dict | None
    generation_request: str
    wire_packet: dict = field(default_factory=dict)
    handoff: dict = field(default_factory=dict)

    def __post_init__(self):
        for item in fields(self):
            object.__setattr__(self, item.name, copy.deepcopy(getattr(self, item.name)))

    def to_dict(self):
        return {item.name: copy.deepcopy(getattr(self, item.name)) for item in fields(self)}


def build_confirmed_generation_context(
    confirmed_spec, plc_model, *, user_requirement="", current_program=None,
    task_type="generate", evidence=None, knowledge_builder=None, model_profile=None,
    decision_receipt_id=None, wire_renderer=None, wire_history=None, raw_request=False,
):
    """Project before routing/retrieval; first generation cannot replay Agent A.

    Edits retain the explicit delta and the existing immutable merge baseline.
    Repair/format-only callers must use their own bounded repair contexts.
    """
    if task_type not in {"generate", "edit"}:
        raise ValueError("confirmed generation context is only for generation/editing")
    projected = project_confirmed_specification(confirmed_spec)
    if not projected:
        raise ValueError("confirmed generation specification is empty")
    model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    current = public_generation_ladder(current_program)
    request = (str(user_requirement or "") if raw_request else
               public_generation_value(str(user_requirement or ""))
               if task_type == "edit" else CONFIRMED_GENERATION_REQUEST)
    if knowledge_builder is None:
        from application.generation_context import _build_knowledge_context
        knowledge_builder = _build_knowledge_context
    from application.context_compiler import ContextCompiler, ContextCompilerInput
    from knowledge.evidence import KnowledgeQuery
    from knowledge.fact_coverage import included_evidence_ids, reconcile_fact_coverage
    from knowledge.instruction_facts import delivered_fact_report
    from knowledge.structured_facts import structured_fact_targets
    compiler = ContextCompiler()
    compiler_input = ContextCompilerInput(
        confirmed_spec=projected,
        intent_context=projected.get("intent_context"),
        selected_approach=projected.get("selected_approach") or {},
        evidence=public_generation_value(evidence),
        plc_model=model,
        model_profile=copy.deepcopy(model_profile or {}),
        task_type=task_type,
        generation_request=request,
        include_generation_request=raw_request,
        current_program=current,
        wire_renderer=wire_renderer,
        wire_history=copy.deepcopy(wire_history or []),
    )
    precompiled = compiler.compile(compiler_input)
    # Exact device/error facts come from the active request, not from the
    # compiler's projection of settled implementation devices. Required opcodes
    # still come from the confirmed generation contract inside the resolver.
    # The fixed first-generation prompt contains transport/topology guidance
    # (for example OR), not additional user lookup needs. Edits still route the
    # actual user delta, alongside the confirmed specification.
    from knowledge.analysis_router import generation_fact_query
    from application.generation_support import repair_retrieval_scope
    repair_scope = repair_retrieval_scope(request, current, plc_model=model)
    fact_requirement = (repair_scope["requirement"] + "\n" + "\n".join(repair_scope["baseline_terms"])
                        if repair_scope else request)
    fact_query = generation_fact_query(
        fact_requirement if task_type == "edit" or raw_request else "", projected, model,
    )
    structured_targets = structured_fact_targets(fact_query, projected, plc_model=model)
    retrieval_query = KnowledgeQuery(
        precompiled.retrieval_packet["query"],
        precompiled=True,
        metadata={
            "context_plan": precompiled.provenance_receipt,
            "structured_fact_mode": "direct",
            "structured_fact_targets": structured_targets,
            "hardware_context": copy.deepcopy(projected.get('hardware_profile') or {}),
            # First generation uses the explicit fact plan. Flattened I/O and
            # process prose must not backfill arbitrary manuals about pulses or
            # unrelated instructions. Edits retain the current user delta.
            "residual_fact_query": fact_query,
            "capability_requirement": request if raw_request or task_type == "edit" else str(projected.get("summary") or ""),
            "repair_scope": ({k: v for k, v in repair_scope.items() if k != "requirement"}
                             if repair_scope else None),
            "target_mode": "ladder",
            # Compatibility for older diagnostics/readers while the structured
            # fact receipt becomes the canonical handoff.
            "instruction_fact_mode": "targeted",
            "instruction_fact_targets": structured_targets["instructions"],
        },
    )
    knowledge = knowledge_builder(
        retrieval_query, plc_model=model, task_type=task_type,
        confirmed_context=precompiled.generation_packet["confirmed_spec"],
        evidence=public_generation_value(evidence),
    )
    from plc.specification.provenance import handoff_snapshot
    from knowledge.evidence import context_manifest, text_sha256
    from shared.model_context import technical_context_text
    from knowledge.evidence import KnowledgeContext
    knowledge_text = KnowledgeContext(technical_context_text(public_generation_value(knowledge or "")),
                                      getattr(knowledge, "manifest", {}))
    compiled = compiler.compile(compiler_input, evidence_text=knowledge_text)
    from application.context_compactor import compact_if_needed
    if not raw_request:
        compiled, compiler_input = compact_if_needed(
            compiler, compiler_input, compiled, evidence_text=knowledge_text,
        )
    elif compiled.budget_report.get("budget_exceeded_after_compaction"):
        raise ValueError("原始需求及事实超出当前模型上下文预算；请缩小本次需求或选择更大上下文的模型。")
    knowledge_text = compiled.generation_packet["evidence"]
    runtime_spec = compiled.generation_packet["confirmed_spec"]
    selected = runtime_spec.get("selected_approach") or {}
    manifest = context_manifest(knowledge, stage=task_type)
    # Reconcile after final budget compilation, not merely after retrieval.
    delivered_records = manifest.get('records') or (manifest.get('instruction_facts') or {}).get('records', [])
    delivered_ids = included_evidence_ids(knowledge_text, delivered_records)
    if isinstance(manifest.get("fact_coverage"), dict):
        manifest["fact_coverage"] = reconcile_fact_coverage(
            manifest["fact_coverage"],
            delivered_ids,
        )
    if isinstance(manifest.get("instruction_facts"), dict):
        manifest["instruction_facts"] = delivered_fact_report(
            manifest["instruction_facts"],
            delivered_ids,
        )
    # A custom builder may return unsanitized text. The source hashes still
    # identify retrieved blocks; the context hash must identify the actual
    # privacy-cleaned text delivered to either generation adapter.
    manifest["context_sha256"] = text_sha256(knowledge_text)
    handoff = handoff_snapshot(projected, evidence=manifest, stage=task_type,
                               decision_receipt_id=decision_receipt_id)
    # Retrieval receipts are application-owned audit data, not stored PLC specs.
    if isinstance(manifest.get("fact_coverage"), dict):
        handoff["fact_coverage"] = copy.deepcopy(manifest["fact_coverage"])
    if isinstance(manifest.get("instruction_facts"), dict):
        handoff["instruction_facts"] = copy.deepcopy(manifest["instruction_facts"])
    if isinstance(manifest.get("structured_facts"), dict):
        handoff["structured_facts"] = copy.deepcopy(manifest["structured_facts"])
    if isinstance(manifest.get("capability_discovery"), dict):
        from application.capability_context import reconcile_discovery
        handoff["capability_discovery"] = reconcile_discovery(manifest["capability_discovery"], knowledge_text)
    handoff.update(copy.deepcopy(compiled.provenance_receipt))
    handoff["budget_report"] = copy.deepcopy(compiled.budget_report)
    # This receipt identifies the policy, not a claimed reduction in model tokens.
    handoff["generation_execution_policy"] = "direct-program-v1" if raw_request else GENERATION_EXECUTION_POLICY_VERSION
    if raw_request:
        handoff.update(
            mode="direct_generation", raw_requirement=request,
            local_check_coverage={
                "checked": ["protocol", "addresses", "instruction_forms", "IR", "CSV"],
                "unverified": ["原文中的时序、重启、互斥及连续周期须另行验收；结构检查不证明工艺正确。"],
            },
        )
    return ConfirmedGenerationContext(
        plc_model=model, confirmed_spec=runtime_spec,
        io_bindings=runtime_spec.get("io_bindings") or [],
        generation_contract=selected.get("generation_contract") or {},
        knowledge_context=knowledge_text,
        current_program=current, generation_request=request,
        wire_packet=compiled.wire_packet,
        handoff=public_generation_value(handoff),
    )


def project_direct_user_facts(user_requirement, plc_model, confirmed_spec=None, *, answers=()):
    """Use only user facts and Core declarations; never manufacture a confirmed plan."""
    from plc.specification.bindings import (
        extract_declared_bindings, merge_declared_bindings, apply_direct_answers,
        discard_superseded_level_parameters,
    )
    facts = project_confirmed_specification(confirmed_spec)
    declared = extract_declared_bindings(user_requirement, plc_model)
    for answer in answers:
        # Each answer is its own address scope. Joining "X0" from one answer
        # and "low active" from another would manufacture a physical fact.
        declared.extend(extract_declared_bindings(answer.get("answer", ""), plc_model))
    rows = [*facts.get("io_table", []), *facts.get("io_bindings", []), *declared]
    bindings = merge_declared_bindings(rows, existing=facts.get("io_bindings", []), declared=declared)
    if bindings:
        facts["io_bindings"] = bindings
        table = {row["address"]: copy.deepcopy(row) for row in facts.get("io_table", [])}
        for row in bindings:
            existing = table.get(row["address"], {})
            table[row["address"]] = {**existing, "kind": row["kind"], "address": row["address"],
                                      "label": row.get("label") or existing.get("label", "")}
        facts["io_table"] = list(table.values())
    if "parameters" in facts:
        facts["parameters"] = discard_superseded_level_parameters(
            facts["parameters"], bindings, {row["address"] for row in declared if "active_level" in row})
    # An empty public fact snapshot is valid for a raw request. The marker is
    # schema metadata, never a selected method or a model-authored user fact.
    facts.setdefault("schema_version", 4)
    return apply_direct_answers(facts, answers) if answers else facts


def build_direct_generation_context(user_requirement, plc_model, *, confirmed_spec=None,
                                    user_fact_text=None, **kwargs):
    # Clarification questions are model context, never user declarations.
    declarations = user_requirement if user_fact_text is None else user_fact_text
    facts = project_direct_user_facts(declarations, plc_model, confirmed_spec)
    return build_confirmed_generation_context(
        facts, plc_model, user_requirement=user_requirement, raw_request=True, **kwargs,
    )

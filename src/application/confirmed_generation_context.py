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
GENERATION_EXECUTION_POLICY_VERSION = "settled-facts-v1"
GENERATION_EXECUTION_POLICY = """# Generation execution policy
当前是实现阶段：确认规格 → 只核对未定技术事实 → 固定一个实现 → 输出。即使前阶段选择了 Design，此处也只实现已选方案，不重新列方案或比较等价写法。
已确认 I/O、参数和本轮明确修改是已定输入。参数/绑定优先于方案说明和 unverified_constraints 中过时的表述；保留其他未冲突的方案语义，不把参考文字升级成额外硬约束。已有结构化硬约束仍须遵守。
形成决定后不反复重开；只有新的用户修订、技术证据或具体正确性矛盾才重审受影响部分。不要因冗余触点、等价布局或猜测评测器偏好重做整个方案。
技术事实只作必要的定向核对（操作数/方向/触发/范围）；反复回忆不增加证据。检索文本存在不代表覆盖全部事实，缺证据也不等于禁止；不得编造已查证、测试通过或工具调用。不为缩短推理而跳过实际发现的错误。
下面的输入谓词由当前绑定确定性推导，只表示电平测试，不替代边沿语义或完整控制逻辑；edit 时本轮明确修改优先于这些 baseline 谓词。未定电平不从名称或物理常闭字样猜测。
完成一次必要的一致性检查后按既有协议输出一份程序；不输出中间计划，不在同次 completion 反复重写。"""


def generation_execution_prompt(confirmed_spec, *, evidence_text="", task_type="generate"):
    """Render one shared materialization policy from the current public snapshot.

    No persisted spec fields are removed or reconciled by parsing natural language.
    Evidence availability is explicitly NOT an evidence-coverage assertion.
    """
    if task_type not in {"generate", "edit"}:
        return ""
    from plc.specification.conditions import generation_input_conditions
    spec = confirmed_spec if isinstance(confirmed_spec, Mapping) else {}
    facts = generation_input_conditions(spec.get("io_bindings"))
    facts["basis"] = "edit_baseline" if task_type == "edit" else "current_confirmed_bindings"
    facts["retrieved_text_present"] = bool(str(evidence_text or "").strip())
    return ("\n\n" + GENERATION_EXECUTION_POLICY + "\n# Settled input predicates (not a new requirement)\n"
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
    handoff: dict = field(default_factory=dict)

    def __post_init__(self):
        for item in fields(self):
            object.__setattr__(self, item.name, copy.deepcopy(getattr(self, item.name)))

    def to_dict(self):
        return {item.name: copy.deepcopy(getattr(self, item.name)) for item in fields(self)}


def build_confirmed_generation_context(
    confirmed_spec, plc_model, *, user_requirement="", current_program=None,
    task_type="generate", evidence=None, knowledge_builder=None, model_profile=None, decision_receipt_id=None,
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
    request = (public_generation_value(str(user_requirement or ""))
               if task_type == "edit" else CONFIRMED_GENERATION_REQUEST)
    if knowledge_builder is None:
        from application.generation_context import _build_knowledge_context
        knowledge_builder = _build_knowledge_context
    from application.context_compiler import ContextCompiler, ContextCompilerInput
    from knowledge.evidence import KnowledgeQuery
    from knowledge.instruction_facts import instruction_fact_targets, delivered_fact_report, included_knowledge_ids
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
        current_program=current,
    )
    precompiled = compiler.compile(compiler_input)
    retrieval_query = KnowledgeQuery(
        precompiled.retrieval_packet["query"],
        precompiled= True,
        metadata={
            "context_plan": precompiled.provenance_receipt,
            "rag_evidence_token_budget": precompiled.budget_report.get("rag_evidence_token_budget"),
            "instruction_fact_mode": "targeted",
            "instruction_fact_targets": instruction_fact_targets(precompiled.retrieval_packet["query"], projected),
        },
    )
    knowledge = knowledge_builder(
        retrieval_query, plc_model=model, task_type=task_type,
        confirmed_context=precompiled.generation_packet["confirmed_spec"],
        evidence=public_generation_value(evidence),
    )
    from plc.specification.provenance import handoff_snapshot
    from knowledge.evidence import context_manifest, text_sha256
    knowledge_text = public_generation_value(knowledge or "")
    compiled = compiler.compile(compiler_input, evidence_text=knowledge_text)
    knowledge_text = compiled.generation_packet["evidence"]
    runtime_spec = compiled.generation_packet["confirmed_spec"]
    selected = runtime_spec.get("selected_approach") or {}
    manifest = context_manifest(knowledge, stage=task_type)
    # Reconcile after final budget compilation, not merely after retrieval.
    if isinstance(manifest.get("instruction_facts"), dict):
        manifest["instruction_facts"] = delivered_fact_report(
            manifest["instruction_facts"], included_knowledge_ids(knowledge_text, manifest["instruction_facts"].get("records", [])))
    # A custom builder may return unsanitized text. The source hashes still
    # identify retrieved blocks; the context hash must identify the actual
    # privacy-cleaned text delivered to either generation adapter.
    manifest["context_sha256"] = text_sha256(knowledge_text)
    handoff = handoff_snapshot(projected, evidence=manifest, stage=task_type,
                               decision_receipt_id=decision_receipt_id)
    # The generic provenance allowlist predates instruction-fact receipts. Keep
    # this application-owned audit intact without changing stored PLC specs.
    if isinstance(manifest.get("instruction_facts"), dict):
        handoff["instruction_facts"] = copy.deepcopy(manifest["instruction_facts"])
    handoff.update(copy.deepcopy(compiled.provenance_receipt))
    handoff["budget_report"] = copy.deepcopy(compiled.budget_report)
    # This receipt identifies the policy, not a claimed reduction in model tokens.
    handoff["generation_execution_policy"] = GENERATION_EXECUTION_POLICY_VERSION
    return ConfirmedGenerationContext(
        plc_model=model, confirmed_spec=runtime_spec,
        io_bindings=runtime_spec.get("io_bindings") or [],
        generation_contract=selected.get("generation_contract") or {},
        knowledge_context=knowledge_text,
        current_program=current, generation_request=request, handoff=public_generation_value(handoff),
    )


def selected_instruction_capability_prompt(plc_model, confirmed_spec):
    """Describe selected catalogue contracts, not simulated/hardware correctness."""
    from knowledge.instruction_facts import instruction_fact_targets
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, generation_app_instr_mnemonics
    targets = instruction_fact_targets("", confirmed_spec)
    if not targets:
        return ""
    allowed = set(generation_app_instr_mnemonics(plc_model))
    rows = []
    for target in targets:
        opcode = target["opcode"]
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode, cpu=plc_model)
        row = {"opcode": opcode, "generation_catalogued": opcode in allowed}
        if form is not None:
            row.update(DEFAULT_INSTRUCTION_REGISTRY.describe_contract(opcode, cpu=plc_model))
            row.pop("operand_annotations", None)
            row.update(
                       min_operands=form.spec.min_operands, max_operands=form.spec.max_operands,
                       operands=[{"name": item.name, "role": item.role.value,
                                  "data_type": item.data_type,
                                  **({"device_prefixes": list(item.device_prefixes)} if item.device_prefixes else {})}
                                 for item in form.spec.operands])
            if form.spec.notes:
                row["notes"] = form.spec.notes
        rows.append(row)
    value = {"source": "current_python_catalogue", "plc_model": plc_model,
             "instructions": rows, "runtime_semantics": "requires_manual_evidence",
             "simulation_verification": "not_claimed"}
    return "\n# Selected instruction capability snapshot\n" + json.dumps(value, ensure_ascii=False, separators=(",", ":"))

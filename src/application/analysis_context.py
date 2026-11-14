# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""One analysis prompt assembler shared by streaming and non-streaming calls."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from knowledge.analysis_router import AnalysisRoute, route_analysis_request
from application.prompts import (
    ANALYSIS_SYSTEM_PROMPT, ANALYSIS_DIRECT_PROMPT, ANALYSIS_PINNED_PROMPT, ANALYSIS_DESIGN_PROMPT,
    ANALYSIS_IMPLEMENTATION_PROMPT, IMPLEMENTATION_COMPARISON_PROMPT, ANALYSIS_OUTPUT_LOGIC_PROMPT,
    ANALYSIS_BOUND_PROMPT, ANALYSIS_REQUIREMENTS_PROMPT,
    ANALYSIS_VFD_PROMPT, ANALYSIS_MOTION_PROMPT, ANALYSIS_MOTION_FAMILY_PROMPTS,
)

_DEVICE = re.compile(r"(?<![A-Za-z0-9_])(?:SM|SD|[XYMDTCSVZ])\d+(?![A-Za-z0-9_])", re.I)

# Transport metadata for the shared Core binding, not an inferred PLC rule.
_IO_BINDING_PROMPT = """# I/O purpose metadata
地址和极性分题共用 io_binding.binding_id，kind 为地址类别，沿用已有 row_id。极性答案无需重复地址，寄存器数值含义不是地址选择。
role 是控制语义身份（start/stop/output/interlock），明确才写并沿用，未知 role 不猜测。label 只是人类可读用途/注释，不含问题、选项、极性或解释。用户编辑优先，不为注释提问。
参数可带 semantic_key、value_kind（text/choice/number/boolean）、unit。硬件 id 用 hardware.<Core参数ID>。"""


def _address_notes(value, requested, result):
    """Select complete matching registry entries; never truncate source facts."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).upper() in requested and isinstance(child, str):
                result[str(key).upper()] = child
            else:
                _address_notes(child, requested, result)


def minimal_analysis_profile(model, registry, route):
    source = registry.get(model) or {}
    result = {"model": model}
    if not isinstance(source, Mapping) or not source:
        result["profile_status"] = "unavailable; use targeted evidence, do not guess another model"
        return result
    result["addressing"] = source.get("addressing")
    kinds = {re.match(r"[A-Z]+", address).group() for address in route.devices}
    kinds.update(("X", "Y"))
    from plc.validation import device_address_radix
    result["addressing_scope"] = "addressing describes X/Y only; other devices use their own radix"
    result["device_address_radix"] = {kind: radix for kind in sorted(kinds)
                                       if (radix := device_address_radix(kind, model)) is not None}
    result["soft_limits"] = {kind: value for kind, value in source.get("soft_limits", {}).items() if kind in kinds}
    requested = set(route.devices)
    specials = {}
    for key in ("special_m", "special_d"):
        for alias, value in source.get(key, {}).items():
            match = _DEVICE.search(str(value))
            if match and match.group().upper() in requested:
                specials[match.group().upper()] = f"{alias}: {value}"
    for key in ("special_m_reference", "special_d_reference"):
        _address_notes(source.get(key, {}), requested, specials)
    if specials:
        result["special_devices"] = specials
    if "D" in kinds and source.get("register_rules"):
        result["register_rules"] = source["register_rules"]
    if "motion" in route.topics and source.get("positioning"):
        result["positioning"] = source["positioning"]
    if "analog" in route.topics:
        for key in ("analog_input", "analog_output"):
            if source.get(key):
                result[key] = source[key]
    if "hsc" in route.topics and source.get("hsc"):
        result["high_speed_counter"] = source["hsc"]
    return result


def _baseline_for_analysis(value):
    """Remove presentation/retrieval duplication, not engineering decisions."""
    if not isinstance(value, Mapping):
        return str(value or "").strip()
    from plc.specification.provenance import confirmed_spec_fields
    baseline = confirmed_spec_fields(value)
    from shared.model_context import technical_value
    return json.dumps(technical_value(baseline), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class AnalysisPrompt:
    system_prompt: str
    knowledge_context: object
    route: AnalysisRoute
    contract_stage: str = "bound"
    model_facts: str = ""
    decision_spec: dict | None = None
    fact_citations: dict | None = None


def analysis_contract_stage(user_request, confirmed_context=None):
    """Select available information, independently of mode and model tuning."""
    from plc.device_identity import device_tokens
    from plc.specification.bindings import binding_reference
    from plc.specification.parameters import parameter_is_applicable
    baseline = confirmed_context if isinstance(confirmed_context, Mapping) else {}
    parameters = baseline.get("parameters") or []
    if any(isinstance(p, Mapping) and p.get("required") and binding_reference(p)
           and parameter_is_applicable(p, parameters) and not str(p.get("value") or "").strip()
           for p in parameters):
        return "requirements"
    if device_tokens(str(user_request or "")):
        return "bound"
    # Current rows are authoritative. Historical bindings and removed addresses
    # cannot make a new unbound request eligible for address-based protocols.
    if any(isinstance(row, Mapping) and device_tokens(str(row.get("address") or ""))
           for row in baseline.get("io_table") or []):
        return "bound"
    return "requirements"


def assemble_analysis_prompt(user_request, *, plc_model, confirmed_context=None,
                             model_loader, knowledge_builder, resolve_opcode=None, audit=None,
                             analysis_mode="direct", target_mode="ladder"):
    """Compose first; a route is not a label added after broad prompt assembly."""
    route = route_analysis_request(
        user_request, confirmed_context, analysis_mode=analysis_mode, resolve_opcode=resolve_opcode,
    )
    profile = minimal_analysis_profile(plc_model, model_loader(), route)
    instruction_facts = {}
    if resolve_opcode is not None:
        for opcode in route.opcodes:
            resolution = resolve_opcode(opcode)
            spec = getattr(resolution, "spec", None)
            if spec is None:
                continue
            from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, InstructionResolution
            instruction_facts[opcode] = {
                "base_mnemonic": getattr(resolution, "base_mnemonic", opcode),
                "min_operands": spec.min_operands,
                "max_operands": spec.max_operands,
                "contract_level": spec.contract_level,
                "cpu_support": sorted(spec.cpu_support),
                "notes": spec.notes,
            }
            if isinstance(resolution, InstructionResolution) and DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode) is spec:
                instruction_facts[opcode].update(DEFAULT_INSTRUCTION_REGISTRY.describe_contract(opcode, cpu=plc_model))
    if instruction_facts:
        profile["instruction_facts"] = instruction_facts
    targets = [*route.opcodes, *profile.get("special_devices", {})]
    fact_query = "\n".join([plc_model, " ".join(targets)]) if targets else route.query_text
    from knowledge.evidence import KnowledgeContext, KnowledgeQuery
    # The exact lookup can be narrowed to addresses/opcodes. Discovery still
    # needs the whole process description before a representation is chosen.
    query_metadata = {"target_mode": target_mode}
    if route.mode != "pinned":
        query_metadata["capability_requirement"] = route.query_text
    query = KnowledgeQuery(fact_query, precompiled=True, metadata=query_metadata)
    knowledge = knowledge_builder(
        query, plc_model=plc_model, task_type="analysis",
        confirmed_context=confirmed_context if isinstance(confirmed_context, Mapping) else None,
        include_design=route.include_design,
        design_query=route.query_text if route.include_design else None,
    )
    deltas = []
    if "vfd" in route.topics:
        deltas.append(ANALYSIS_VFD_PROMPT)
    if "motion" in route.topics:
        deltas.append(ANALYSIS_MOTION_PROMPT)
        deltas.extend(ANALYSIS_MOTION_FAMILY_PROMPTS[name] for name in route.motion_families
                      if name in ANALYSIS_MOTION_FAMILY_PROMPTS)
    mode_prompt = ANALYSIS_DESIGN_PROMPT if route.include_design else ANALYSIS_DIRECT_PROMPT
    if route.mode == "pinned":
        mode_prompt += "\n\n" + ANALYSIS_PINNED_PROMPT
    else:
        mode_prompt += "\n\n" + IMPLEMENTATION_COMPARISON_PROMPT
    mode_prompt += "\n\n" + ANALYSIS_IMPLEMENTATION_PROMPT
    if route.mode != "pinned":
        mode_prompt += "\n\n" + ANALYSIS_OUTPUT_LOGIC_PROMPT
    from shared.model_context import technical_value, project_knowledge_context, technical_context_units, technical_block
    from plc.specification.fact_citations import cite_units, fact_version
    knowledge = project_knowledge_context(knowledge)
    origins = {}
    projected = technical_context_units(knowledge, origins=origins)
    units, citations = cite_units(projected, model=plc_model,
                                 language=target_mode, version=fact_version(), origins=origins)
    if units:
        knowledge = KnowledgeContext("\n".join(technical_block(unit) for unit in units), knowledge.manifest)
    model_context = "# Relevant PLC model facts\n" + json.dumps(technical_value(profile), ensure_ascii=False, separators=(",", ":"))
    baseline = _baseline_for_analysis(confirmed_context)
    stage = analysis_contract_stage(user_request, confirmed_context)
    stage_prompt = ANALYSIS_BOUND_PROMPT if stage == "bound" else ANALYSIS_REQUIREMENTS_PROMPT
    core_prompt = ANALYSIS_SYSTEM_PROMPT + "\n\n" + stage_prompt + "\n\n" + _IO_BINDING_PROMPT
    from plc.timing import scan_timing_guidance
    # Analysis proposals and both generation adapters receive the same Core
    # timing guidance; the old generic pattern bundle is intentionally omitted.
    timing = scan_timing_guidance(plc_model)
    parts = [core_prompt, mode_prompt, *deltas, timing, model_context, str(knowledge)]
    if baseline:
        parts.append("# Confirmed project specification\n" + baseline)
    system_prompt = "\n\n".join(part.strip() for part in parts if part and part.strip())
    if audit is not None:
        audit("base_prompt", core_prompt, reason="analysis_" + stage, source="analysis_assembler")
        audit("dynamic_prompt", "\n\n".join([mode_prompt, *deltas]), reason=route.reason, source="analysis_router")
        audit("model_profile", model_context, reason="analysis_targeted", source="model_registry")
        audit("workflow_prompt", status="excluded", reason="analysis_routed_before_assembly", source="analysis_router")
        audit("system_prompt", system_prompt, reason="analysis_" + route.mode, source="analysis_assembler")
    decision_spec = json.loads(baseline) if isinstance(confirmed_context, Mapping) else None
    return AnalysisPrompt(system_prompt, knowledge, route, stage, model_context, decision_spec, citations)

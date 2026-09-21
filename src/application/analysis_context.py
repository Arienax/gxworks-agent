"""One analysis prompt assembler shared by streaming and non-streaming calls."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from knowledge.analysis_router import AnalysisRoute, route_analysis_request
from application.prompts import (
    ANALYSIS_SYSTEM_PROMPT, ANALYSIS_DIRECT_PROMPT, ANALYSIS_PINNED_PROMPT, ANALYSIS_DESIGN_PROMPT,
    ANALYSIS_VFD_PROMPT, ANALYSIS_MOTION_PROMPT, ANALYSIS_MOTION_FAMILY_PROMPTS,
)

_DEVICE = re.compile(r"(?<![A-Za-z0-9_])(?:SM|SD|[XYMDTCSVZ])\d+(?![A-Za-z0-9_])", re.I)

# Transport metadata for the shared Core binding, not an inferred PLC rule.
_IO_BINDING_PROMPT = """# I/O purpose metadata
地址或输入极性确认问题用 io_binding 标识同一物理点：binding_id 为稳定标识，kind 为地址类别，label 为独立用途名称；已有 row_id 时沿用。已知地址只确认极性时，答案不必重复地址；仅涉及寄存器数值含义的参数不当作地址选择。
label 是独立的简短用途名称，不是 question：不含提问、选项、触点极性推导或实现解释；用途未知可省略或留空，不为注释新增确认问题。question 保留完整确认问题，只询问尚未提供的地址或极性。已有 I/O 用途以用户编辑的值为准；suggested_io 中的说明也只写用途名称。"""


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
    return json.dumps(baseline, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class AnalysisPrompt:
    system_prompt: str
    knowledge_context: object
    route: AnalysisRoute


def assemble_analysis_prompt(user_request, *, plc_model, confirmed_context=None,
                             model_loader, knowledge_builder, resolve_opcode=None, audit=None,
                             analysis_mode="direct"):
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
            instruction_facts[opcode] = {
                "base_mnemonic": getattr(resolution, "base_mnemonic", opcode),
                "min_operands": spec.min_operands,
                "max_operands": spec.max_operands,
                "contract_level": spec.contract_level,
                "cpu_support": sorted(spec.cpu_support),
                "notes": spec.notes,
            }
    if instruction_facts:
        profile["instruction_facts"] = instruction_facts
    targets = [*route.opcodes, *profile.get("special_devices", {})]
    fact_query = "\n".join([plc_model, " ".join(targets)]) if targets else route.query_text
    knowledge = knowledge_builder(
        fact_query, plc_model=plc_model, task_type="analysis",
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
    model_context = "# Relevant PLC model facts\n" + json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
    baseline = _baseline_for_analysis(confirmed_context)
    core_prompt = ANALYSIS_SYSTEM_PROMPT + "\n\n" + _IO_BINDING_PROMPT
    parts = [core_prompt, mode_prompt, *deltas, model_context, str(knowledge)]
    if baseline:
        parts.append("# Confirmed project specification\n" + baseline)
    system_prompt = "\n\n".join(part.strip() for part in parts if part and part.strip())
    if audit is not None:
        audit("base_prompt", core_prompt, reason="analysis_core", source="analysis_assembler")
        audit("dynamic_prompt", "\n\n".join([mode_prompt, *deltas]), reason=route.reason, source="analysis_router")
        audit("model_profile", model_context, reason="analysis_targeted", source="model_registry")
        audit("workflow_prompt", status="excluded", reason="analysis_routed_before_assembly", source="analysis_router")
        audit("system_prompt", system_prompt, reason="analysis_" + route.mode, source="analysis_assembler")
    return AnalysisPrompt(system_prompt, knowledge, route)

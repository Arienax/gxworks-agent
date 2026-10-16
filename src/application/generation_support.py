"""Model profile, privacy, and query helpers used by generation context."""
from __future__ import annotations


import copy


import json


import re


import sys


from plc.specification.approach import normalize_approach, normalize_generation_contract


from shared.paths import resource_path


from shared.context_audit import audit_section
from shared.model_context import technical_value


def _engineering_hardware_snapshot(value):
    # Preserve the API's historical filtering without importing model transport.
    return {key: copy.deepcopy(item) for key, item in value.items()
            if key not in {"reasoning_content", "raw_response", "raw_attempts", "_provider_reasoning", "_provider_fields"}}


_KNOWLEDGE_GENERIC_VALUES = {
    "",
    "branch",
    "coil",
    "contact",
    "false",
    "instruction",
    "ladder",
    "normally_closed",
    "normally_open",
    "parallel",
    "rung",
    "series",
    "true",
}


_KNOWLEDGE_TASK_TOP_K = {
    "analysis": 4,
    "debug": 5,
    "edit": 5,
    "generate": 5,
    "program_review": 5,
    "review": 5,
}


def _build_knowledge_query(*values, char_limit=None):
    """Flatten useful project values into a compact retrieval-only query.

    JSON field names and repeated ladder structure add no retrieval value and
    can crowd real opcodes out of the exact-match window, so only scalar values
    are retained.  The primary user text is passed first by every caller and
    therefore keeps the highest exact-match priority.
    """

    fragments = []
    seen = set()

    def add(value):
        text = " ".join(str(value if value is not None else "").strip().split())
        if not text or text.casefold() in _KNOWLEDGE_GENERIC_VALUES:
            return
        marker = text.casefold()
        if marker in seen:
            return
        seen.add(marker)
        fragments.append(text)

    # Iterative traversal preserves full facts and tolerates repeated/cyclic
    # containers without a character, fragment-count or nesting quota.
    pending = list(values)
    pending.reverse()
    visited = set()
    while pending:
        value = pending.pop()
        if isinstance(value, (dict, list, tuple, set)):
            if id(value) in visited:
                continue
            visited.add(id(value))
            children = value.values() if isinstance(value, dict) else value
            pending.extend(reversed(list(children)))
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            add(value)
    from knowledge.evidence import KnowledgeQuery
    return KnowledgeQuery("\n".join(fragments), truncated=False)


def _routing_text_with_selected_approach(user_requirement, confirmed_context=None):
    """Route from the chosen engineering plan, never from unselected alternatives."""
    from plc.specification.provenance import retrieval_projection
    return _build_knowledge_query(user_requirement, retrieval_projection(confirmed_context))


def _load_plc_models():
    """加载 plc_models.json"""
    path = resource_path("plc_models.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_model_context(model: str, confirmed_context=None, compact=False) -> str:
    """Build the authoritative target PLC profile independently of retrieval.

    The compact argument is retained for caller compatibility, but no longer
    switches profile contents or asserts that manual evidence was delivered.
    """
    from plc.target_support import DEFAULT_TARGET_SUPPORT, normalize_target_cpu
    model = normalize_target_cpu(model)
    m = _load_plc_models().get(model) or {}
    target = DEFAULT_TARGET_SUPPORT.target(model)

    profile = {
        "model": model,
        "family": m.get("family") or target.get("family"),
        "profile_status": m.get("coverage", "available") if m else "unavailable; use exact-target manual evidence, never another CPU profile",
        "description": m.get("desc"),
        "addressing": m.get("addressing"),
        "soft_limits": m.get("soft_limits", {}),
        "register_rules": m.get("register_rules", {}),
        "positioning": m.get("positioning", {}),
        "analog_input": m.get("analog_input", {}),
        "analog_output": m.get("analog_output", {}),
        "high_speed_counter": m.get("hsc", {}),
        "notes": m.get("notes", ""),
    }
    profile["special_m"] = m.get("special_m", {})
    profile["special_d"] = m.get("special_d", {})
    confirmed_hardware = None
    confirmed_hardware_context = None
    if isinstance(confirmed_context, dict):
        candidate = confirmed_context.get("hardware_profile")
        if isinstance(candidate, dict):
            confirmed_hardware = _engineering_hardware_snapshot(candidate)
        candidate_context = confirmed_context.get("hardware_context")
        if isinstance(candidate_context, dict):
            confirmed_hardware_context = _engineering_hardware_snapshot(
                candidate_context
            )
    if confirmed_hardware:
        profile["confirmed_hardware_profile"] = confirmed_hardware
    if confirmed_hardware_context:
        profile["confirmed_hardware_context"] = confirmed_hardware_context
    result = (
        "\n# Selected PLC model profile (authoritative for this request)\n"
        "Use the per-Y capability and output-type notes below. A global "
        "maximum is not permission to use every Y at that frequency. Do not "
        "invent module registers, buffer addresses, or unsupported aliases.\n"
        + json.dumps(technical_value(profile), ensure_ascii=False, indent=2)
        + "\n"
    )
    audit_section("model_profile", result, reason="canonical_full_profile" if m else "target_identity_only",
                  source="model_registry")
    return result


_PRIVATE_PATH = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\[A-Za-z0-9._-]+[\\/]|/(?:Users|home|tmp|var|etc|private|mnt)/)"
    r"[^\s\"<>|]*"
)


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|agent[_-]?token|operator[_-]?token|gateway[_-]?token|"
    r"access[_-]?token|password|secret)\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


def public_generation_value(value):
    """Clean an already allowlisted engineering value for external clients.

    Projection must happen before this function: it does not make arbitrary
    project metadata public. Paths and labelled credentials can also occur in
    user annotations or retrieved text, so strings get a final privacy pass.
    """
    if isinstance(value, dict):
        return {key: public_generation_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_generation_value(item) for item in value]
    if isinstance(value, str):
        value = _PRIVATE_PATH.sub("[private path]", value)
        value = _SECRET_ASSIGNMENT.sub("[private credential]", value)
        return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [private credential]", value)
    if value is None or isinstance(value, (bool, int, float)):
        return copy.deepcopy(value)
    return None


def repair_retrieval_scope(requirement, current_program=None, *, plc_model="FX3U"):
    """Recognize a concrete repair baseline without changing user facts.

    An embedded candidate must be a single complete protocol object. Labels,
    JSON keys and prior code are not new functional requirements. This selects
    retrieval evidence only; it never accepts the candidate or authorizes edits.
    """
    text = str(requirement or "")
    if not re.search(r"修复|修正|纠正|\brepair\b|\bfix\b", text, re.I):
        return None
    candidates = []
    decoder = json.JSONDecoder()
    for marker in re.finditer(r'\{\s*"(?:r|rungs|device_comments)"\s*:', text):
        try:
            value, length = decoder.raw_decode(text[marker.start():])
            if "r" in value:
                from application.compact_protocol import expand_compact_ladder
                value = expand_compact_ladder(value)
            if isinstance(value.get("rungs"), list) and value["rungs"]:
                candidates.append((value, marker.start(), marker.start() + length))
        except (ValueError, TypeError, KeyError):
            continue
    if current_program is not None:
        baseline = public_generation_ladder(current_program)
    elif len(candidates) == 1:
        baseline = public_generation_ladder(candidates[0][0])
    else:
        return None
    if not baseline or not baseline.get("rungs"):
        return None
    for _, start, end in reversed(candidates):
        text = text[:start] + " " + text[end:]
    terms = []
    from plc.device_policy import device_address, DevicePolicyError
    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "APP_INSTR":
                terms.append(str(value.get("opcode") or ""))
            elif value.get("type") in {"BLOCK_INPUT", "BLOCK_OUTPUT"}:
                terms.append(str(value.get("expression") or ""))
            address = value.get("address")
            if address and value.get("type") in {"TIMER", "COUNTER"}:
                terms.append(str(address))
            # Special contacts remain lookup targets; ordinary internal wiring
            # need not retrieve a full device chapter for every address.
            if address and value.get("type") in {"NO", "NC", "P", "F"}:
                try:
                    device_address(address, plc_model, access="reset")
                except (DevicePolicyError, ValueError):
                    if not str(address).upper().startswith(("X", "Y")):
                        terms.append(str(address))
            for key, child in value.items():
                if key not in {"label", "debug_note", "device_comments"}:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(baseline.get("rungs", []))
    return {"requirement": text, "baseline_terms": list(dict.fromkeys(terms)),
            "source": "current_program" if current_program is not None else "embedded_candidate",
            "rung_count": len(baseline["rungs"])}


def public_generation_ladder(ladder):
    """Project only ladder source fields, never IR or UI/session metadata."""
    if not isinstance(ladder, dict):
        return None

    def element(value):
        if not isinstance(value, dict):
            return None
        result = {key: copy.deepcopy(value[key]) for key in (
            "type", "address", "label", "expression", "value", "opcode",
        ) if key in value and (value[key] is None or isinstance(value[key], (str, int, float, bool)))}
        if isinstance(value.get("operands"), list):
            result["operands"] = [item for item in value["operands"] if isinstance(item, str)]
        if value.get("type") == "parallel_block" and isinstance(value.get("branches"), list):
            # The ladder protocol permits only one local parallel level.
            result["branches"] = [[element(item) for item in branch
                                   if isinstance(item, dict) and item.get("type") != "parallel_block"]
                                  for branch in value["branches"] if isinstance(branch, list)]
        return result

    rungs = []
    for rung in ladder.get("rungs", []) if isinstance(ladder.get("rungs"), list) else []:
        if not isinstance(rung, dict):
            continue
        projected = {key: copy.deepcopy(rung[key]) for key in ("rung_id", "debug_note")
                     if key in rung and (rung[key] is None or isinstance(rung[key], (str, int)))}
        if "header_element" in rung:
            projected["header_element"] = element(rung["header_element"])
        if isinstance(rung.get("shared_inputs"), list):
            projected["shared_inputs"] = [element(item) for item in rung["shared_inputs"]]
        projected["branches"] = []
        for branch in rung.get("branches", []) if isinstance(rung.get("branches"), list) else []:
            if not isinstance(branch, dict):
                continue
            item = {key: branch[key] for key in ("branch_id", "y_offset_level")
                    if key in branch and isinstance(branch[key], int)}
            for key in ("inputs", "outputs"):
                if isinstance(branch.get(key), list):
                    item[key] = [element(output) for output in branch[key]]
            projected["branches"].append(item)
        rungs.append(projected)
    comments = ladder.get("device_comments", {})
    return public_generation_value({
        "device_comments": {address: text for address, text in comments.items()
                            if isinstance(address, str) and re.fullmatch(r"[A-Za-z]+\d+", address)
                            and isinstance(text, str)} if isinstance(comments, dict) else {},
        "rungs": rungs,
    })


def public_generation_specification(specification):
    """Normalize legacy selected-plan metadata, then apply the Core allowlist.

    The caller-owned specification is never mutated. Legacy compatibility lives
    at this application boundary so plc.generation_contract remains a pure,
    model-free projection used by API and external-tool contracts.
    """
    from plc.generation_contract import generation_specification
    from plc.specification.parameters import generation_parameter_view
    from plc.specification.legacy_migration import migrate_legacy_approach

    normalized = generation_parameter_view(specification)
    if isinstance(normalized, dict) and isinstance(normalized.get("selected_approach"), dict):
        selected = migrate_legacy_approach(normalized["selected_approach"])
        normalized["selected_approach"] = selected
        contract = normalize_generation_contract(selected.get("generation_contract"))
        if contract.get("unverified_constraints"):
            selected["generation_contract"] = contract

    def source_order(source, projected):
        if isinstance(source, dict) and isinstance(projected, dict):
            keys = [key for key in source if key in projected]
            keys.extend(key for key in projected if key not in source)
            return {key: source_order(source.get(key), projected[key]) for key in keys}
        if isinstance(source, (list, tuple)) and isinstance(projected, list):
            shared = min(len(source), len(projected))
            ordered = [source_order(source[index], projected[index]) for index in range(shared)]
            ordered.extend(copy.deepcopy(projected[shared:]))
            return ordered
        return projected

    return public_generation_value(source_order(specification, generation_specification(normalized)))

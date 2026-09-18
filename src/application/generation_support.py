"""Model profile, privacy, and query helpers used by generation context."""
from __future__ import annotations


import copy


import json


import re


import sys


from plc.specification.approach import normalize_approach


from shared.paths import resource_path


from knowledge.patterns import assemble_prompt, build_workflow_prompt, classify_request


from shared.context_policy import (
    audit_section, manual_lookup_decision, resolve_context_policy, select_base_prompt,
)


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


_KNOWLEDGE_TASK_SETTINGS = {
    "analysis": (4, 7000),
    "debug": (5, 7600),
    "edit": (5, 7000),
    "generate": (5, 7000),
    "program_review": (5, 7600),
    "review": (5, 7600),
}


def _build_knowledge_query(*values, char_limit=24000):
    """Flatten useful project values into a compact retrieval-only query.

    JSON field names and repeated ladder structure add no retrieval value and
    can crowd real opcodes out of the exact-match window, so only scalar values
    are retained.  The primary user text is passed first by every caller and
    therefore keeps the highest exact-match priority.
    """

    fragments = []
    seen = set()

    def add(value):
        text = " ".join(str(value or "").strip().split())
        if not text or text.casefold() in _KNOWLEDGE_GENERIC_VALUES:
            return
        if len(text) > 600:
            text = text[:600]
        marker = text.casefold()
        if marker in seen:
            return
        seen.add(marker)
        fragments.append(text)

    def walk(value, depth=0):
        if value is None or depth > 12 or len(fragments) >= 400:
            return
        if isinstance(value, dict):
            for nested in value.values():
                walk(nested, depth + 1)
            return
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                walk(nested, depth + 1)
            return
        if isinstance(value, str):
            add(value)

    for item in values:
        walk(item)

    selected = []
    used = 0
    for fragment in fragments:
        cost = len(fragment) + (1 if selected else 0)
        if used + cost > char_limit:
            break
        selected.append(fragment)
        used += cost
    return "\n".join(selected)


def _routing_text_with_selected_approach(user_requirement, confirmed_context=None):
    """Route generation using both the request and the user's chosen method.

    The confirmed specification is appended to the final prompt later, but
    pattern routing happens earlier.  Without this bridge a user-selected
    state-machine, counter, motion, analog or communication method could miss
    its specialist pattern merely because the original request did not name
    that method.
    """

    parts = [str(user_requirement or "")]
    if isinstance(confirmed_context, dict):
        selected = normalize_approach(
            confirmed_context.get("selected_approach") or {}
        )
        if selected:
            parts.extend(
                [
                    selected.get("name", ""),
                    selected.get("description", ""),
                    selected.get("generation_guide", ""),
                    json.dumps(
                        selected.get("generation_contract") or {},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ]
            )
    return "\n".join(str(item) for item in parts if str(item).strip())


def _load_plc_models():
    """加载 plc_models.json"""
    path = resource_path("plc_models.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_model_context(model: str, confirmed_context=None, compact=False) -> str:
    """Build the generation-relevant profile for one PLC model.

    When retrieved manual evidence is available, the large generic M/D lookup
    tables are omitted.  If retrieval is unavailable the complete legacy
    profile is retained, so the offline index is an enhancement rather than a
    new point of failure.
    """
    policy = resolve_context_policy()
    if not policy.legacy:
        # All controlled arms keep the SAME full target profile. Disabling RAG
        # must not silently alter special-device facts supplied to the model.
        compact = False
    models = _load_plc_models()
    m = models.get(model, models.get("FX3U", {}))
    if not m:
        return ""

    profile = {
        "model": model,
        "family": m.get("family"),
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
    if compact:
        profile["manual_evidence"] = "retrieved for the current request"
    else:
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
        + json.dumps(profile, ensure_ascii=False, indent=2)
        + "\n"
    )
    audit_section("model_profile", result, reason="legacy_auto" if policy.legacy else "fixed_full",
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
    """Keep the established engineering allowlist and the API's source order."""
    from plc.generation_contract import generation_specification

    def source_order(source, projected):
        if isinstance(source, dict) and isinstance(projected, dict):
            keys = [key for key in source if key in projected]
            keys.extend(key for key in projected if key not in source)
            return {key: source_order(source.get(key), projected[key]) for key in keys}
        if isinstance(source, (list, tuple)) and isinstance(projected, list):
            return [source_order(original, public) for original, public in zip(source, projected)]
        return projected

    return public_generation_value(source_order(specification, generation_specification(specification)))


"""Hardware context for PLC generation and requirement confirmation.

PLC nameplate/profile details are optional metadata.  Drive- and motion-side
choices such as the command interface, pulse axis, homing method and a selected
positioning module are different: they can change the generated program
topology and must survive analysis normalization.
"""

from __future__ import annotations

import copy
import json
import re
from shared.i18n import tr
from plc.specification.parameters import hardware_parameter_id, parameter_is_applicable
from plc.specification.approach import normalize_instruction_instances


HARDWARE_PROFILE_SCHEMA_VERSION = 1

QUESTION_IDS = {
    "cpu_full_model": "PLC CPU完整型号（含输出形式后缀）",
    "output_type": "PLC基本单元输出类型",
    "firmware": "PLC固件/硬件版本",
    "modules": "已安装扩展模块/适配器完整型号（无则填“无”）",
    "drive_model": "变频器/驱动器完整型号",
    "control_method": "变频器频率给定控制方式",
    "wiring_mapping": "变频器控制端子或信号映射",
    "motion_drive_model": "伺服/步进驱动器完整型号",
    "motion_control_method": "伺服/步进驱动器控制方式",
    "motion_wiring_mapping": "伺服/步进驱动器端子或信号映射",
    "positioning_implementation": "运动控制实现方式",
    "positioning_module_model": "定位模块/高速输出适配器完整型号",
    "pulse_output_axis": "脉冲输出轴",
    "direction_output": "方向输出端子",
    "motion_speed": "运动速度/脉冲频率",
    "positioning_mode": "定位方式（相对/绝对）",
    "position_target": "目标位置/脉冲数",
    "homing_required": "是否需要回原点",
    "homing_method": "回原点方式",
}

# Kept separate from the model so a second FX3U-2HSY-ADP can be represented
# without overloading or duplicating the model-name row.
QUESTION_IDS["positioning_module_quantity"] = "定位模块/高速输出适配器数量"

# These are the retired PLC profile fields that used to be injected into every
# review as mandatory rows.  Keep drive-side design questions out of this set:
# multi-speed terminals, analog output and Modbus require materially different
# programs even though they are not PLC nameplate parameters.
RETIRED_PLC_PROFILE_QUESTION_IDS = frozenset(
    {"cpu_full_model", "output_type", "firmware", "modules"}
)

_HARDWARE_MARKERS = (
    "变频器",
    "vfd",
    "inverter",
    "伺服",
    "servo",
    "步进电机",
    "步进驱动",
    "stepper",
    "脉冲",
    "pulse",
    "plsy",
    "plsv",
    "drvi",
    "drva",
    "模拟量",
    "0-10v",
    "4-20ma",
    "modbus",
    "rs485",
    "高速输出",
    "频率给定",
)
_VFD_MARKERS = ("变频器", "vfd", "inverter", "stf", "多段速", "频率给定")
_PULSE_MARKERS = ("高速脉冲", "脉冲频率", "pulse", "plsy", "plsv", "drvi", "drva")
_MOTION_MARKERS = (
    "伺服",
    "servo",
    "步进电机",
    "步进驱动",
    "stepper",
    "定位",
    "positioning",
    "回原点",
    "原点回归",
    "plsy",
    "plsv",
    "drvi",
    "drva",
    "zrn",
    "dszr",
)
_VFD_CONTROL_METHOD_OPTIONS = (
    "Y输出多段速端子（STF/RH/RM/RL，固定档位优先）",
    "模拟量输出（0-10V或4-20mA）",
    "RS485通讯（Modbus）",
    "高速脉冲频率给定（需晶体管输出及变频器支持）",
)

# Agent A proposes implementation candidates, but its low-level implementation
# guesses are not user-confirmed facts.  Close every structured contract before
# it reaches ``normalize_approach`` so omitted fields cannot be re-inferred from
# free-form generation_guide prose and silently promoted into hard constraints.
_ANALYSIS_CONTRACT_VALUE_FIELDS = (
    "required_opcodes",
    "forbidden_opcodes",
    "required_devices",
    "forbidden_devices",
    "required_structures",
    "forbidden_structures",
)
_ANALYSIS_CONTRACT_GROUP_FIELDS = (
    "any_of_opcode_groups",
    "any_of_structure_groups",
)
_ANALYSIS_OPCODE_FIELDS = ("required_opcodes", "forbidden_opcodes")
_ANALYSIS_DEVICE_FIELDS = ("required_devices", "forbidden_devices")
_COUNTER_STRUCTURES = {"hardware_counter", "data_register_counter"}
_COUNTER_INTENT_RE = re.compile(
    r"计数|计次|次数|counter|(?<![A-Za-z])count(?:ing|s|ed)?(?![A-Za-z])",
    re.IGNORECASE,
)


def _requirement_mentions_token(requirement, token):
    value = str(token or "").strip()
    if not value:
        return False
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])",
            str(requirement or ""),
            re.IGNORECASE,
        )
    )

def _requirement_mentions_instruction_instance(requirement, instance):
    """Promote an exact call only when the user's text contains that full call."""
    if not isinstance(instance, dict):
        return False
    opcode = str(instance.get("opcode") or "").strip()
    operands = instance.get("operands") or []
    if not opcode or not isinstance(operands, (list, tuple)):
        return False
    tokens = [opcode, *(str(value).strip() for value in operands)]
    if any(not token for token in tokens):
        return False
    separator = r"[\s,，]+"
    pattern = (
        r"(?<![A-Za-z0-9_.$@+<>!=\-])"
        + separator.join(re.escape(token) for token in tokens)
        + r"(?![A-Za-z0-9_.$@+<>!=\-])"
    )
    return bool(re.search(pattern, str(requirement or ""), re.IGNORECASE))


def _string_list(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result



def _sanitize_analysis_approaches(result, user_text):
    """Prevent Agent-A guesses from becoming hard confirmed constraints.

    Explicit low-level opcodes/devices survive only when the user's own request
    names them.  High-level architecture remains available for approach choice,
    except counter structures when the request contains no counter intent at all.
    Every contract field is materialized, including empty lists, which blocks the
    legacy generation_guide inference path from recreating removed constraints.
    Removed obligations remain model-sourced preferences; an empty hard contract
    does not invalidate or delete a candidate.
    """
    requirement = str(user_text or "").strip()
    approaches = result.get("approaches")
    if not requirement or not isinstance(approaches, list):
        return

    has_counter_intent = bool(_COUNTER_INTENT_RE.search(requirement))
    sanitized = []
    for raw_approach in approaches:
        if not isinstance(raw_approach, dict):
            continue
        approach = copy.deepcopy(raw_approach)
        raw_contract = approach.get("generation_contract")
        contract = copy.deepcopy(raw_contract) if isinstance(raw_contract, dict) else {}

        for field in _ANALYSIS_CONTRACT_VALUE_FIELDS:
            contract[field] = _string_list(contract.get(field))
        for field in _ANALYSIS_CONTRACT_GROUP_FIELDS:
            groups = contract.get(field)
            contract[field] = [
                _string_list(group)
                for group in groups
                if isinstance(group, (list, tuple)) and _string_list(group)
            ] if isinstance(groups, (list, tuple)) else []

        for field in _ANALYSIS_OPCODE_FIELDS:
            contract[field] = [
                token for token in contract[field]
                if _requirement_mentions_token(requirement, token)
            ]
        for field in _ANALYSIS_DEVICE_FIELDS:
            contract[field] = [
                token for token in contract[field]
                if _requirement_mentions_token(requirement, token)
            ]
        contract["any_of_opcode_groups"] = [
            [token for token in group if _requirement_mentions_token(requirement, token)]
            for group in contract["any_of_opcode_groups"]
        ]
        contract["any_of_opcode_groups"] = [
            group for group in contract["any_of_opcode_groups"] if group
        ]

        if "instruction_instances" in contract:
            contract["instruction_instances"] = [
                instance
                for instance in normalize_instruction_instances(contract.get("instruction_instances"))
                if _requirement_mentions_instruction_instance(requirement, instance)
            ]

        if not has_counter_intent:
            for field in ("required_structures", "forbidden_structures"):
                contract[field] = [
                    item for item in contract[field]
                    if item.casefold() not in _COUNTER_STRUCTURES
                ]
            contract["any_of_structure_groups"] = [
                [item for item in group if item.casefold() not in _COUNTER_STRUCTURES]
                for group in contract["any_of_structure_groups"]
            ]
            contract["any_of_structure_groups"] = [
                group for group in contract["any_of_structure_groups"] if group
            ]

        # Retain the proposal under its real origin. Removing an unconfirmed
        # hard obligation must not delete the architecture or its implementation
        # choices. These preferences are context only, never validator inputs.
        prior = approach.get("implementation_preferences")
        original = (prior if isinstance(prior, dict) and isinstance(raw_contract, dict)
                    and raw_contract.get("source") == "analysis_sanitized" else raw_contract)
        preferences = copy.deepcopy(original) if isinstance(original, dict) else {}
        preferences["source"] = "model_proposal"
        preferences["enforce"] = False
        approach["implementation_preferences"] = preferences
        contract["source"] = "analysis_sanitized"
        approach["generation_contract"] = contract
        sanitized.append(approach)

    result["approaches"] = sanitized


_FLAG_NAMES = ("hardware_dependent", "vfd", "pulse", "motion", "analog", "serial")
_INTENT_VERSION = 1


def _positive_hardware_text(value):
    """Exclude explicit equipment/interface denials, not all negative sentences.

    'Do not stop the VFD' still contains a drive. Only a declaration such as
    'without a VFD' is negative hardware evidence. Never scan model suggestions
    to decide whether the user's project contains that hardware.
    """
    text = str(value or "").casefold()
    equipment = r"(?:变频器|vfd|inverter|インバータ(?:ー)?|伺服|servo|stepper|步进电机|高速脉冲|模拟量|modbus|rs-?485)"
    text = re.sub(r"(?:不需要|不使用|不采用|不涉及|不用|无需|不接入|不添加|没有|非)\s*(?:任何|外部的?)?\s*" + equipment,
                  " ", text)
    text = re.sub(r"\b(?:without|no|not\s+(?:using|requiring))\s+(?:(?:a|an|any|the)\s+)?" + equipment + r"\b", " ", text)
    text = re.sub(equipment + r"\s*(?:は不要|を使用しない|不要)", " ", text)
    return text


def _has_marker(text, marker):
    if marker.isascii():
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(marker) + r"(?![a-z0-9])", text))
    return marker in text


def _flags_from_evidence(text):
    text = _positive_hardware_text(text)
    vfd = any(_has_marker(text, marker) for marker in _VFD_MARKERS) or "インバータ" in text
    # A motor with explicit Hz setpoints is positive frequency-command intent;
    # bare 'motor speed' could equally mean a servo or another mechanism.
    hz_setpoints = bool(re.search(r"\d+(?:\s*[/、,，]\s*\d+)+\s*hz(?![a-z])", text))
    vfd = vfd or hz_setpoints and any(x in text for x in ("电机", "motor", "调速", "频率"))
    pulse = any(_has_marker(text, marker) for marker in _PULSE_MARKERS)
    motion = any(_has_marker(text, marker) for marker in _MOTION_MARKERS)
    analog = any(marker in text for marker in ("模拟量", "0-10v", "4-20ma", "4da"))
    serial = any(marker in text for marker in ("modbus", "rs485", "rs-485", "通信", "通讯"))
    return {"hardware_dependent": bool(vfd or pulse or motion or analog or serial),
            "vfd": bool(vfd), "pulse": pulse, "motion": motion, "analog": analog, "serial": serial}


def _confirmed_hardware_evidence(spec):
    """Use confirmed values and the selected contract, not old derived flags."""
    if not isinstance(spec, dict):
        return "", ""
    parts, method_selected = [], ""
    for item in spec.get("parameters", []) or []:
        if not isinstance(item, dict) or not str(item.get("value") or "").strip():
            continue
        identifier = hardware_parameter_id(item, QUESTION_IDS)
        value = str(item["value"])
        if value.strip().casefold() in {"none", "no", "无", "不需要", "不使用"}:
            continue
        if identifier in {"drive_model", "control_method", "wiring_mapping"}:
            parts.append("vfd " + value)
            if identifier == "control_method":
                method_selected = value
        elif identifier in QUESTION_IDS:
            parts.append(value)
    profile = spec.get("hardware_profile") or {}
    if isinstance(profile, dict):
        for key in ("drive_model", "control_method", "control_method_label", "wiring_mapping"):
            value = profile.get(key)
            if value:
                parts.append("vfd " + str(value))
                if key in {"control_method", "control_method_label"}:
                    method_selected = str(value)
    selected = spec.get("selected_approach") or {}
    contract = (selected.get("generation_contract") or {}) if isinstance(selected, dict) else {}
    if isinstance(contract, dict):
        parts.extend(_string_list(contract.get("required_structures")))
    for row in spec.get("io_table", []) or []:
        if isinstance(row, dict):
            parts.append(str(row.get("label") or ""))
    # This field is produced from user evidence after parsing Agent A. Raw model
    # output cannot establish it: a call with user_text always recomputes it.
    intent = spec.get("hardware_intent")
    if isinstance(intent, dict) and intent.get("version") == _INTENT_VERSION:
        known = intent.get("flags") or {}
        if known.get("vfd") is True:
            parts.append("vfd")
        method_selected = str(intent.get("vfd_method_value") or method_selected)
    return "\n".join(parts), method_selected


def _hardware_intent(analysis, user_text="", confirmed_spec=None):
    requirement = str(user_text or "").strip()
    prior_text, prior_method = _confirmed_hardware_evidence(confirmed_spec)
    prior = _flags_from_evidence(prior_text)
    if requirement:
        current = _flags_from_evidence(requirement)
        # An explicit removal in the current request overrides an old confirmed
        # drive. No unrelated new analysis field can turn the drive back on.
        denied_vfd = any(marker in requirement.casefold() for marker in (*_VFD_MARKERS, "インバータ")) and not current["vfd"]
        flags = {k: current[k] or prior[k] for k in _FLAG_NAMES}
        if denied_vfd:
            flags["vfd"] = False
        flags["hardware_dependent"] = any(flags[k] for k in _FLAG_NAMES[1:])
        method = _selected_vfd_method(requirement) or prior_method
        return {"version": _INTENT_VERSION, "source": "user_request", "flags": flags,
                "vfd_method_selected": bool(flags["vfd"] and method),
                "vfd_method_value": method if flags["vfd"] else ""}
    saved = analysis.get("hardware_intent") if isinstance(analysis, dict) else None
    if isinstance(saved, dict) and saved.get("version") == _INTENT_VERSION and isinstance(saved.get("flags"), dict):
        return {"version": _INTENT_VERSION, "source": saved.get("source", "legacy_unknown"),
                "flags": {k: saved["flags"].get(k) is True for k in _FLAG_NAMES},
                "vfd_method_selected": saved.get("vfd_method_selected") is True,
                "vfd_method_value": str(saved.get("vfd_method_value") or "")}
    # Legacy data without original-user evidence is unknown, not an invitation
    # to manufacture a mandatory question from hardware_requirements booleans.
    return {"version": _INTENT_VERSION, "source": "confirmed_spec" if prior["vfd"] else "legacy_unknown",
            "flags": prior, "vfd_method_selected": bool(prior_method), "vfd_method_value": prior_method}


def hardware_requirement_flags(analysis, user_text="", confirmed_spec=None):
    """Feature flags derive from user evidence, never from Agent-A questions."""
    return _hardware_intent(analysis, user_text, confirmed_spec)["flags"]


def _without_unsupported_vfd(analysis, requirement):
    """Do not promote speculative drive prose/contracts to confirmed facts."""
    removed = []
    approaches = analysis.get("approaches")
    if isinstance(approaches, list):
        kept = []
        for item in approaches:
            if not isinstance(item, dict):
                continue
            contract = item.get("generation_contract") or {}
            positive = " ".join(str(item.get(k) or "") for k in ("name", "description", "generation_guide"))
            if isinstance(contract, dict):
                positive += " " + " ".join(_string_list(contract.get("required_structures")))
                positive += " " + json.dumps(contract.get("any_of_structure_groups", []), ensure_ascii=False)
            if _flags_from_evidence(positive)["vfd"]:
                removed.append("approaches")
            else:
                kept.append(item)
        analysis["approaches"] = kept
    if requirement and _flags_from_evidence(analysis.get("summary", ""))["vfd"]:
        # Keep the real requirement, not a model-added drive specification.
        analysis["summary"] = requirement
        removed.append("summary")
    hardware = analysis.get("hardware_config")
    if isinstance(hardware, dict):
        has_motion = bool(analysis.get("hardware_requirements", {}).get("motion"))
        kept = {key: value for key, value in hardware.items()
                if (has_motion or key not in {"drive", "drive_model", "control_method", "wiring_mapping"})
                and not _flags_from_evidence(str(key) + " " + json.dumps(value, ensure_ascii=False))["vfd"]}
        if kept != hardware:
            removed.append("hardware_config")
            analysis["hardware_config"] = kept
    if removed:
        diagnostics = analysis.setdefault("format_diagnostics", [])
        if isinstance(diagnostics, list):
            record = {"code": "unsupported_drive_assumptions_removed", "path": "analysis",
                      "message": "未将缺少用户依据的变频器假设作为确认规格。"}
            if record not in diagnostics:
                diagnostics.append(record)


def _question_dependencies(item):
    result = set()
    def visit(value):
        if isinstance(value, dict):
            parameter = value.get("parameter")
            if isinstance(parameter, str):
                result.add(parameter)
            for key in ("all", "any"):
                for child in value.get(key, []) or []:
                    visit(child)
    visit(item.get("required_when"))
    return result



def _infer_question_id(question):
    # Compatibility for exact historical labels only. Wording is presentation,
    # not a schema: “步进式还是连续” must not acquire a speed/model identity.
    return hardware_parameter_id({"name": str(question or "")}, QUESTION_IDS)


def _hardware_question_id(item):
    """Migrate only the retired generic modules id, never a declared identity."""
    identifier = hardware_parameter_id(item, QUESTION_IDS)
    if "semantic_key" not in item and str(item.get("id") or "").strip() == "modules":
        legacy = _infer_question_id(item.get("question") or item.get("name"))
        if legacy and legacy not in RETIRED_PLC_PROFILE_QUESTION_IDS:
            return legacy
    return identifier


def _selected_vfd_method(text):
    """One positively chosen interface, not a list of alternatives or a denial."""
    value = _positive_hardware_text(text)
    candidates = []
    for words, label in (
        (("多段速", "stf", "rh/rm/rl", "multi_speed"), _VFD_CONTROL_METHOD_OPTIONS[0]),
        (("模拟量", "0-10v", "4-20ma", "4da", "analog"), _VFD_CONTROL_METHOD_OPTIONS[1]),
        (("rs485", "rs-485", "modbus", "serial"), _VFD_CONTROL_METHOD_OPTIONS[2]),
        (("高速脉冲频率给定", "脉冲给定", "pulse"), _VFD_CONTROL_METHOD_OPTIONS[3]),
    ):
        if any(word in value for word in words):
            candidates.append(label)
    return candidates[0] if len(candidates) == 1 else ""


def _explicit_vfd_control_method(text):
    return bool(_selected_vfd_method(text))


def is_automatic_hardware_question(item):
    """Return whether a row is one of the retired PLC profile fields.

    The historical function name is retained for compatibility.  Do not infer
    removal from ``source`` alone: older deterministic rules also produced VFD
    control questions, and those questions affect the generated logic.
    """
    if not isinstance(item, dict):
        return False
    return _hardware_question_id(item) in RETIRED_PLC_PROFILE_QUESTION_IDS


def ensure_hardware_questions(analysis, plc_model="FX3U", user_text="", confirmed_spec=None):
    """Remove retired PLC profile rows while preserving drive design choices.

    The function name is retained for compatibility with existing callers.
    Legacy PLC nameplate rows are removed so cached analyses cannot bring the
    retired mandatory fields back into the review dialog.  Recognized VFD rows
    receive stable IDs so they remain hardware facts during canonicalization.
    """
    result = copy.deepcopy(analysis or {})
    if str(user_text or "").strip():
        _sanitize_analysis_approaches(result, user_text)

    intent = _hardware_intent(result, user_text, confirmed_spec)
    flags = intent["flags"]
    result["hardware_requirements"] = flags
    result["hardware_intent"] = intent
    bounded = intent["source"] != "legacy_unknown"
    if bounded and not flags["vfd"]:
        _without_unsupported_vfd(result, str(user_text or "").strip())

    existing = result.get("missing_info")
    if isinstance(existing, list):
        normalized_questions = []
        excluded_questions = set()
        for raw_item in existing:
            if not isinstance(raw_item, dict) or is_automatic_hardware_question(raw_item):
                continue
            item = copy.deepcopy(raw_item)
            inferred_id = _hardware_question_id(item)
            if inferred_id in QUESTION_IDS:
                identifier = str(item.get("id") or "").strip()
                if not identifier or (
                    identifier == "modules" and "semantic_key" not in item
                    and inferred_id not in RETIRED_PLC_PROFILE_QUESTION_IDS
                ):
                    item["id"] = inferred_id
            is_vfd_question = inferred_id in {"control_method", "drive_model", "wiring_mapping"} or (
                _flags_from_evidence(item.get("question", ""))["vfd"]
                and not inferred_id.startswith("motion_"))
            unsupported = bounded and not flags["vfd"] and is_vfd_question
            answered = flags["vfd"] and inferred_id == "control_method" and intent["vfd_method_selected"]
            item.pop("confirmed_value", None)
            if unsupported:
                excluded_questions.update({str(item.get("id") or ""), str(item.get("question") or "")})
                continue
            if answered and intent.get("vfd_method_value"):
                # Keep dependency controllers visible but already answered from
                # actual user facts; do not make the user confirm a model default.
                item.update(required=False, source="confirmed_request_fact",
                            confirmed_value=intent["vfd_method_value"])
            normalized_questions.append(item)
        # Only remove dependents of an unsupported feature, not independent
        # analog/motion questions or dependencies on a known answered method.
        if bounded and not flags["vfd"]:
            while True:
                dropped = [q for q in normalized_questions
                           if _question_dependencies(q) and _question_dependencies(q) <= excluded_questions]
                if not dropped:
                    break
                for q in dropped:
                    excluded_questions.update({str(q.get("id") or ""), str(q.get("question") or "")})
                    normalized_questions.remove(q)

        question_ids = {
            str(item.get("id", "")).strip()
            or _infer_question_id(item.get("question") or item.get("name"))
            for item in normalized_questions
        }
        should_restore_control_method = (
            flags["vfd"] and "control_method" not in question_ids
            and not intent["vfd_method_selected"] and bounded
        )
        if should_restore_control_method:
            normalized_questions.insert(
                0,
                {
                    "id": "control_method",
                    "question": str(tr("变频器频率给定控制方式")),
                    "options": list(_VFD_CONTROL_METHOD_OPTIONS),
                    "default": _VFD_CONTROL_METHOD_OPTIONS[0],
                    "source": "deterministic_drive_design_rule",
                    "required": True,
                },
            )
        result["missing_info"] = normalized_questions
    else:
        result["missing_info"] = []

    hardware = result.get("hardware_config")
    if isinstance(hardware, dict):
        hardware = copy.deepcopy(hardware)
        hardware.pop("confirmation_required", None)
        if hardware.get("capability_source") == "deterministic_profile_before_ai":
            hardware.pop("capability_source", None)
        if hardware:
            result["hardware_config"] = hardware
        else:
            result.pop("hardware_config", None)
    return result


def parameter_values(spec):
    values = {}
    indices = {}
    bound = {}
    parameters = (spec or {}).get("parameters", []) or []
    for index, parameter in enumerate(parameters):
        if not isinstance(parameter, dict) or not parameter_is_applicable(parameter, parameters):
            continue
        name = str(parameter.get("name", "")).strip()
        question_id = str(parameter.get("id", "")).strip()
        value = str(parameter.get("value", "")).strip()
        field = hardware_parameter_id(parameter, QUESTION_IDS)
        if field:
            bound.setdefault(field, set()).add(value)
            indices[f"@bound:{field}"] = index
        if question_id:
            values[f"@id:{question_id}"] = value
            indices[f"@id:{question_id}"] = index
        if not name:
            continue
        values[name] = value
        indices[name] = index
    # Conflicting owners remain ordinary parameters; do not choose whichever
    # one happened to be last in a dictionary.
    values.update({f"@bound:{field}": next(iter(answers)) for field, answers in bound.items() if len(answers) == 1})
    return values, indices


def _lookup(values, question_id):
    return values.get(f"@bound:{question_id}", "")


def control_method_key(value):
    text = str(value or "").casefold()
    if "多段速" in text or "stf" in text:
        return "multi_speed"
    if "脉冲" in text or "pulse" in text:
        return "pulse"
    if "模拟量" in text or "0-10v" in text or "4-20ma" in text:
        return "analog"
    if "rs485" in text or "modbus" in text or "通信" in text or "通讯" in text:
        return "serial"
    return ""


def build_hardware_profile(spec, plc_model=None):
    """Build the user-confirmed HardwareProfileV1 stored with a spec/version."""
    values, _indices = parameter_values(spec or {})
    existing = (spec or {}).get("hardware_profile")
    profile = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    family = str(plc_model or (spec or {}).get("plc_model") or "FX3U").strip().upper()
    profile.update(
        {
            "schema_version": HARDWARE_PROFILE_SCHEMA_VERSION,
            "min_reader_version": 1,
            "plc_family": family,
            "cpu_full_model": _lookup(values, "cpu_full_model"),
            "output_type": _lookup(values, "output_type"),
            "firmware": _lookup(values, "firmware"),
            "modules": _split_modules(_lookup(values, "modules")),
            "drive_model": _lookup(values, "drive_model"),
            "control_method": control_method_key(_lookup(values, "control_method")),
            "control_method_label": _lookup(values, "control_method"),
            "wiring_mapping": _lookup(values, "wiring_mapping"),
            "motion_drive_model": _lookup(values, "motion_drive_model"),
            "motion_control_method": control_method_key(
                _lookup(values, "motion_control_method")
            ),
            "motion_control_method_label": _lookup(values, "motion_control_method"),
            "motion_wiring_mapping": _lookup(values, "motion_wiring_mapping"),
            "positioning_implementation": _lookup(values, "positioning_implementation"),
            "positioning_module_model": _lookup(values, "positioning_module_model"),
            "positioning_module_quantity": _lookup(
                values, "positioning_module_quantity"
            ),
            "source": "user_confirmed_spec",
        }
    )
    return profile


def _split_modules(value):
    text = str(value or "").strip()
    if not text or text.startswith("无") or re.search(
        r"(?:^|\s)none(?:\s|$)", text, re.IGNORECASE
    ):
        return []
    return [item.strip() for item in re.split(r"[,，;；\n]+", text) if item.strip()]


def _has_high_speed_output_adapter(value):
    text = str(value or "").casefold().replace(" ", "")
    return any(
        marker in text
        for marker in ("fx3u-2hsy-adp", "2hsy-adp", "高速输出适配器")
    )


def validate_hardware_spec(spec, plc_model=None):
    """Validate explicit hardware contradictions, never missing details."""
    requirements = (spec or {}).get("hardware_requirements")
    if not isinstance(requirements, dict) or not requirements.get("hardware_dependent"):
        return {"errors": [], "warnings": []}

    values, indices = parameter_values(spec or {})
    errors = []
    warnings = []

    def path_for(question_id):
        name = QUESTION_IDS[question_id]
        index = indices.get(f"@bound:{question_id}", indices.get(f"@id:{question_id}", indices.get(name)))
        return f"$.parameters[{index}].value" if index is not None else "$.parameters"

    def issue(code, message, question_id):
        errors.append({"code": code, "message": message, "path": path_for(question_id)})

    output_type = _lookup(values, "output_type")
    modules = _lookup(values, "modules")
    positioning_module = _lookup(values, "positioning_module_model")
    output_is_relay = "继电器" in output_type or "relay" in output_type.casefold()
    has_pulse_adapter = _has_high_speed_output_adapter(positioning_module) or any(
        _has_high_speed_output_adapter(item) for item in _split_modules(modules)
    )

    if requirements.get("pulse") and output_is_relay and not has_pulse_adapter:
        issue(
            "pulse_output_conflict",
            "已填写的继电器输出类型与内置高速脉冲输出不兼容",
            "output_type",
        )

    if requirements.get("vfd"):
        method_label = _lookup(values, "control_method")
        method = control_method_key(method_label)
        selected = (spec or {}).get("selected_approach")
        if isinstance(selected, dict) and method:
            approach_text = " ".join(
                str(selected.get(key, "") or "")
                for key in ("name", "description", "generation_guide")
            )
            approach_method = control_method_key(approach_text)
            if approach_method and approach_method != method:
                # Prose can mention a rejected/previous interface. A lexical
                # match is not proof of a physical or user-constraint conflict.
                warnings.append({
                    "code": "control_method_approach_conflict",
                    "message": "方案文字提及其他控制方式；按已确认的控制方式生成，保留文字供核对",
                    "path": path_for("control_method"), "blocking": False,
                })
        if (
            method == "pulse"
            and output_is_relay
            and not has_pulse_adapter
            and not requirements.get("pulse")
        ):
            issue("pulse_output_conflict", "已填写的继电器输出类型与高速脉冲频率给定不兼容", "output_type")
        if method == "analog" and modules.strip() in {"无", "无扩展模块/适配器"}:
            issue("analog_module_conflict", "已选择模拟量给定，但填写的硬件信息表示未安装模拟量输出模块", "modules")

    return {"errors": errors, "warnings": warnings}

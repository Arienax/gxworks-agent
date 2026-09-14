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
from i18n import tr


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
_VERBATIM_REQUIREMENT_MARKER = "【当前用户明确要求（逐字保留）】"
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


def _preserve_verbatim_requirement(result, user_text):
    requirement = str(user_text or "").strip()
    if not requirement:
        return
    block = _VERBATIM_REQUIREMENT_MARKER + "\n" + requirement
    summary = str(result.get("summary") or "").strip()
    if block not in summary:
        result["summary"] = (summary + "\n\n" if summary else "") + block


def _sanitize_analysis_approaches(result, user_text):
    """Prevent Agent-A guesses from becoming hard confirmed constraints.

    Explicit low-level opcodes/devices survive only when the user's own request
    names them.  High-level architecture remains available for approach choice,
    except counter structures when the request contains no counter intent at all.
    Every contract field is materialized, including empty lists, which blocks the
    legacy generation_guide inference path from recreating removed constraints.
    """
    requirement = str(user_text or "").strip()
    approaches = result.get("approaches")
    if not requirement or not isinstance(approaches, list):
        return

    has_counter_intent = bool(_COUNTER_INTENT_RE.search(requirement))
    sanitized = []
    dropped = 0
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

        contract["source"] = "analysis_sanitized"
        approach["generation_contract"] = contract
        has_constraint = any(
            contract.get(field)
            for field in (*_ANALYSIS_CONTRACT_VALUE_FIELDS, *_ANALYSIS_CONTRACT_GROUP_FIELDS)
        )
        if has_constraint:
            sanitized.append(approach)
        else:
            dropped += 1

    result["approaches"] = sanitized
    if dropped:
        diagnostics = result.get("format_diagnostics")
        diagnostics = list(diagnostics) if isinstance(diagnostics, list) else []
        diagnostics.append(
            {
                "code": "analysis_approach_hard_constraints_removed",
                "path": "approaches",
                "message": "已丢弃仅由模型低层猜测构成、没有用户证据的硬约束方案。",
                "count": dropped,
            }
        )
        result["format_diagnostics"] = diagnostics


def _analysis_text(analysis, user_text=""):
    try:
        payload = json.dumps(analysis or {}, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload = str(analysis or "")
    return (str(user_text or "") + "\n" + payload).casefold()


def _hardware_evidence(value):
    """Exclude derived flags and explicit prohibitions from feature detection.

    A model can correctly forbid ``vfd_multi_speed``/``PLSY`` in an ordinary
    relay program. Those contract values constrain generation; they do not
    establish a drive or pulse-output requirement. Keep required/alternative
    contract fields and the original analysis unchanged.
    """
    if isinstance(value, dict):
        excluded = {
            "hardware_requirements", "forbidden_opcodes", "forbidden_devices",
            "forbidden_structures", "forbidden_instructions", "forbidden_features",
        }
        return {key: _hardware_evidence(item) for key, item in value.items() if key not in excluded}
    if isinstance(value, (list, tuple)):
        return [_hardware_evidence(item) for item in value]
    return value


def hardware_requirement_flags(analysis, user_text=""):
    """Return deterministic feature flags without consulting the model again."""
    scan_source = _hardware_evidence(analysis)
    text = _analysis_text(scan_source, user_text)
    hardware_dependent = any(marker.casefold() in text for marker in _HARDWARE_MARKERS)
    vfd = any(marker.casefold() in text for marker in _VFD_MARKERS)
    pulse = any(marker.casefold() in text for marker in _PULSE_MARKERS)
    motion = any(marker.casefold() in text for marker in _MOTION_MARKERS)
    analog = any(marker in text for marker in ("模拟量", "0-10v", "4-20ma", "4da"))
    serial = any(marker in text for marker in ("modbus", "rs485", "通信", "通讯"))
    return {
        "hardware_dependent": hardware_dependent,
        "vfd": vfd,
        "pulse": pulse,
        "motion": motion,
        "analog": analog,
        "serial": serial,
    }


def _infer_question_id(question):
    text = str(question or "").casefold()
    vfd_marked = any(item in text for item in ("变频器", "vfd", "inverter"))
    motion_marked = any(
        item in text
        for item in (
            "伺服",
            "servo",
            "步进",
            "stepper",
            "定位",
            "运动控制",
            "脉冲输出",
            "回原点",
            "原点回归",
        )
    )
    drive_marked = vfd_marked or motion_marked or any(
        item in text for item in ("驱动器", "驱动设备")
    )

    module_marked = any(
        item in text
        for item in ("扩展模块", "定位模块", "高速输出模块", "高速输出适配器", "适配器")
    )
    model_marked = any(
        item in text for item in ("型号", "订货号", "清单", "安装", "版本", "完整")
    )
    if module_marked and any(
        item in text for item in ("\u6570\u91cf", "\u51e0\u5757", "\u51e0\u4e2a", "\u53f0\u6570", "quantity")
    ):
        return "positioning_module_quantity"
    if module_marked and model_marked and motion_marked:
        return "positioning_module_model"
    if motion_marked and any(
        item in text
        for item in ("控制方式", "给定方式", "接口方式", "指令方式", "通讯方式", "通信方式")
    ):
        return "motion_control_method"
    if any(item in text for item in ("实现方式", "实现方案")) and motion_marked:
        return "positioning_implementation"
    if "脉冲输出" in text and any(item in text for item in ("轴", "端子", "输出点", "y点")):
        return "pulse_output_axis"
    if any(item in text for item in ("方向输出", "方向信号")) and any(
        item in text for item in ("端子", "输出", "y点", "映射")
    ):
        return "direction_output"
    if any(item in text for item in ("是否回原点", "是否需要回原点", "需要回原点吗")):
        return "homing_required"
    if any(item in text for item in ("回原点", "原点回归")) and any(
        item in text for item in ("方式", "方法", "模式", "指令")
    ):
        return "homing_method"
    if any(item in text for item in ("相对/绝对", "相对还是绝对", "定位方式", "绝对定位或相对定位")):
        return "positioning_mode"
    if any(item in text for item in ("目标位置", "目标脉冲", "脉冲数", "移动量", "移动距离")):
        return "position_target"
    if motion_marked and any(item in text for item in ("速度", "频率", "运行频率")):
        return "motion_speed"
    if vfd_marked and any(
        item in text
        for item in ("控制方式", "给定方式", "频率给定", "通讯方式", "通信方式")
    ):
        return "control_method"
    if any(item in text for item in ("cpu", "plc")) and any(
        item in text for item in ("型号", "订货号", "机型", "铭牌", "输出后缀")
    ):
        return "cpu_full_model"
    if any(item in text for item in ("输出类型", "输出形式", "晶体管输出", "继电器输出")) and any(
        item in text for item in ("plc", "cpu", "基本单元")
    ):
        return "output_type"
    if any(item in text for item in ("固件", "硬件版本", "cpu版本")):
        return "firmware"
    if module_marked and model_marked:
        return "modules"
    if motion_marked and any(item in text for item in ("型号", "订货号", "品牌", "铭牌")):
        return "motion_drive_model"
    if drive_marked and any(item in text for item in ("型号", "订货号", "品牌", "铭牌")):
        return "drive_model"
    if motion_marked and any(
        item in text
        for item in ("端子", "信号映射", "接线", "站号", "波特率", "寄存器")
    ):
        return "motion_wiring_mapping"
    if drive_marked and any(
        item in text
        for item in ("端子", "信号映射", "接线", "站号", "波特率", "寄存器")
    ):
        return "wiring_mapping"
    return ""


def _explicit_vfd_control_method(text):
    """Return whether the user's own text selects a concrete VFD interface."""
    value = str(text or "").casefold()
    markers = (
        "多段速",
        "stf",
        "rh/rm/rl",
        "模拟量",
        "0-10v",
        "4-20ma",
        "4da",
        "rs485",
        "rs-485",
        "modbus",
        "高速脉冲频率给定",
        "脉冲给定",
    )
    return any(marker in value for marker in markers)


def is_automatic_hardware_question(item):
    """Return whether a row is one of the retired PLC profile fields.

    The historical function name is retained for compatibility.  Do not infer
    removal from ``source`` alone: older deterministic rules also produced VFD
    control questions, and those questions affect the generated logic.
    """
    if not isinstance(item, dict):
        return False
    explicit_id = str(item.get("id", "")).strip()
    inferred_id = _infer_question_id(item.get("question") or item.get("name"))
    # Older/cached analyses often labelled every module question as ``modules``.
    # Prefer a design-specific inference so a positioning adapter or module is
    # not deleted merely because the model supplied the old generic ID.
    question_id = (
        inferred_id
        if inferred_id and inferred_id not in RETIRED_PLC_PROFILE_QUESTION_IDS
        else explicit_id or inferred_id
    )
    return question_id in RETIRED_PLC_PROFILE_QUESTION_IDS


def ensure_hardware_questions(analysis, plc_model="FX3U", user_text=""):
    """Remove retired PLC profile rows while preserving drive design choices.

    The function name is retained for compatibility with existing callers.
    Legacy PLC nameplate rows are removed so cached analyses cannot bring the
    retired mandatory fields back into the review dialog.  Recognized VFD rows
    receive stable IDs so they remain hardware facts during canonicalization.
    """
    result = copy.deepcopy(analysis or {})
    if str(user_text or "").strip():
        _preserve_verbatim_requirement(result, user_text)
        _sanitize_analysis_approaches(result, user_text)

    existing_flags = result.get("hardware_requirements")
    if not str(user_text or "").strip() and isinstance(existing_flags, dict):
        flags = {
            key: bool(existing_flags.get(key, False))
            for key in ("hardware_dependent", "vfd", "pulse", "motion", "analog", "serial")
        }
    else:
        flags = hardware_requirement_flags(result, user_text)
    result["hardware_requirements"] = flags

    existing = result.get("missing_info")
    if isinstance(existing, list):
        normalized_questions = []
        for raw_item in existing:
            if not isinstance(raw_item, dict) or is_automatic_hardware_question(raw_item):
                continue
            item = copy.deepcopy(raw_item)
            explicit_id = str(item.get("id", "")).strip()
            text_id = _infer_question_id(item.get("question") or item.get("name"))
            inferred_id = (
                text_id
                if text_id and text_id not in RETIRED_PLC_PROFILE_QUESTION_IDS
                else explicit_id or text_id
            )
            if inferred_id in QUESTION_IDS:
                item["id"] = inferred_id
            normalized_questions.append(item)

        question_ids = {
            str(item.get("id", "")).strip()
            or _infer_question_id(item.get("question") or item.get("name"))
            for item in normalized_questions
        }
        should_restore_control_method = False
        if flags.get("vfd") and "control_method" not in question_ids:
            if str(user_text or "").strip():
                should_restore_control_method = not _explicit_vfd_control_method(user_text)
            else:
                # Older normalized analyses lost the question but retained the
                # analog/serial/pulse flags derived from its candidate options.
                # Two or more candidates are a narrow, deterministic migration
                # signal and avoid re-asking when the user explicitly selected
                # one interface in the original request.
                candidate_count = sum(
                    bool(flags.get(key)) for key in ("analog", "serial", "pulse")
                )
                should_restore_control_method = candidate_count >= 2
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
    for index, parameter in enumerate((spec or {}).get("parameters", []) or []):
        if not isinstance(parameter, dict):
            continue
        name = str(parameter.get("name", "")).strip()
        question_id = str(parameter.get("id", "")).strip()
        value = str(parameter.get("value", "")).strip()
        if question_id:
            values[f"@id:{question_id}"] = value
            indices[f"@id:{question_id}"] = index
        if not name:
            continue
        values[name] = value
        indices[name] = index
    return values, indices


def _lookup(values, question_id):
    stable_key = f"@id:{question_id}"
    if stable_key in values:
        return values[stable_key]
    exact = QUESTION_IDS[question_id]
    if exact in values:
        return values[exact]
    markers = {
        "cpu_full_model": ("cpu", "完整型号"),
        "output_type": ("输出类型", "输出形式"),
        "firmware": ("固件", "硬件版本"),
        "modules": ("已安装扩展模块", "通用扩展模块", "模拟量模块"),
        "drive_model": ("变频器", "驱动器"),
        "control_method": ("控制方式", "给定方式", "频率给定"),
        "wiring_mapping": ("端子", "信号映射", "接线"),
        "motion_drive_model": ("伺服", "步进", "运动驱动器"),
        "motion_control_method": ("伺服/步进驱动器控制方式", "脉冲+方向", "运动控制接口"),
        "motion_wiring_mapping": ("伺服/步进驱动器端子", "脉冲/方向映射", "运动接线"),
        "positioning_implementation": ("运动控制实现方式", "定位实现方式"),
        "positioning_module_model": ("定位模块", "高速输出适配器", "fx3u-2hsy", "fx3u-1pg", "fx2n-10pg"),
        "positioning_module_quantity": ("定位模块数量", "高速输出适配器数量", "几块适配器"),
        "pulse_output_axis": ("脉冲输出轴",),
        "direction_output": ("方向输出端子", "方向信号输出"),
        "motion_speed": ("运动速度", "脉冲频率"),
        "positioning_mode": ("定位方式", "相对/绝对"),
        "position_target": ("目标位置", "目标脉冲", "脉冲数"),
        "homing_required": ("是否需要回原点", "是否回原点"),
        "homing_method": ("回原点方式", "原点回归方式"),
    }[question_id]
    vfd_ids = {"drive_model", "control_method", "wiring_mapping"}
    motion_ids = {
        "motion_drive_model",
        "motion_control_method",
        "motion_wiring_mapping",
    }
    for name, value in values.items():
        if name.startswith("@id:"):
            continue
        lowered = name.casefold()
        inferred = _infer_question_id(name)
        if question_id in vfd_ids and inferred in motion_ids:
            continue
        if question_id in motion_ids and inferred in vfd_ids:
            continue
        if any(marker.casefold() in lowered for marker in markers):
            return value
    return ""


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
        index = indices.get(f"@id:{question_id}", indices.get(name))
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
                issue(
                    "control_method_approach_conflict",
                    "已确认的变频器控制方式与当前选择方案不一致；请重新选择匹配方案，或恢复该方案对应的控制方式",
                    "control_method",
                )
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

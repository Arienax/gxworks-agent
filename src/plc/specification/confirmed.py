import copy
import difflib
import re

from plc.device_identity import canonical_device
from plc.specification.parameters import parameter_metadata, parameter_value, text_origin
from plc.specification.bindings import (
    bind_answers,
    bind_known_question_rows,
    binding_hint,
    single_address,
    restore_bound_choices,
    resolve_parameter_address,
    parameter_uses_bound_address,
    bound_parameter_is_removed,
    confirmed_input_levels,
)

from plc.specification.approach import (
    contract_definition_issues,
    contract_definition_warnings,
    normalize_approach,
)
from plc.hardware_profiles import (
    QUESTION_IDS,
    build_hardware_profile,
    ensure_hardware_questions,
    is_automatic_hardware_question,
    validate_hardware_spec,
)


DEVICE_RE = re.compile(r"((?:SM|SD|[XYMTCSDVZ])\d+)", re.IGNORECASE)
ASSIGNMENT_RE = re.compile(
    r"((?:SM|SD|[XYMTCSDVZ])\d+)\s*=\s*([^,\n]+)",
    re.IGNORECASE,
)
IO_KIND_ORDER = ("X", "Y", "M", "T", "C", "D", "S", "V", "Z", "特殊")

_EXACT_DEVICE_RE = re.compile(r"^(SM|SD|[XYMTCSDVZ])(\d+)$", re.IGNORECASE)
_VALID_IO_KINDS = set(IO_KIND_ORDER)
_SUGGESTED_IO_DEVICE_KINDS = {"X", "Y", "M", "T", "C", "D", "S", "V", "Z"}
_SUGGESTED_IO_SPECIAL_KINDS = {
    "special_relays": {"M", "SM"},
    "special_registers": {"D", "SD"},
}

# These are deterministic, CPU-provided read-only contacts that are safe to
# reference as ladder inputs.  In particular, M8013 does not represent an
# optional module or a writable allocation, so asking the user to acknowledge
# a generic hardware/read-write warning is misleading.
_KNOWN_READ_ONLY_SPECIAL_INPUTS = {
    "M8000": "RUN监控常开触点（PLC运行时ON，只读）",
    "M8001": "RUN监控常闭触点（PLC运行时OFF，只读）",
    "M8002": "进入RUN后的首扫描脉冲（只读）",
    "M8003": "进入RUN后的首扫描反向脉冲（只读）",
    "M8011": "10ms周期时钟（只读）",
    "M8012": "100ms周期时钟（只读）",
    "M8013": "1s周期时钟（ON 500ms/OFF 500ms，只读）",
    "M8014": "1min周期时钟（只读）",
    "SM400": "FX5U常开系统触点（只读）",
    "SM401": "FX5U常闭系统触点（只读）",
    "SM402": "FX5U首扫描脉冲（只读）",
    "SM403": "FX5U首扫描反向脉冲（只读）",
    "SM409": "FX5U 10ms周期时钟（只读）",
    "SM410": "FX5U 100ms周期时钟（只读）",
    "SM411": "FX5U 200ms周期时钟（只读）",
    "SM412": "FX5U 1s周期时钟（只读）",
    "SM413": "FX5U 2s周期时钟（只读）",
    "SM414": "FX5U 1min周期时钟（只读）",
    "SM8000": "FX5U兼容RUN监控常开触点（只读）",
    "SM8002": "FX5U兼容首扫描脉冲（只读）",
}

_VFD_CONTROL_OPTIONS = (
    "Y输出多段速端子（STF/RH/RM/RL，固定档位优先）",
    "模拟量输出（0-10V或4-20mA）",
    "RS485通讯（Modbus）",
    "高速脉冲频率给定（需晶体管输出及变频器支持）",
)
_DEVICE_LIMITS = {
    "FX3U": {
        "X": 0o367,
        "Y": 0o367,
        "M": 8511,
        "T": 511,
        "C": 255,
        "S": 4095,
        "D": 8511,
        "V": 7,
        "Z": 7,
    },
    "FX5U": {
        # FX5U X/Y use decimal device numbers. 1777 is the documented
        # device-number ceiling; the actually installed I/O is normally less.
        "X": 1777,
        "Y": 1777,
        "M": 7679,
        "T": 1023,
        "C": 1023,
        "S": 4095,
        "D": 7999,
    },
}

# FX3U special relay numbers explicitly marked reserved/unavailable in the
# bundled device reference. Keeping this table here makes validate_spec_draft
# deterministic and side-effect free.
_FX3U_RESERVED_M_RANGES = (
    (8010, 8010),
    (8023, 8023),
    (8080, 8089),
    (8100, 8103),
    (8112, 8120),
    (8140, 8149),
    (8256, 8259),
    (8300, 8303),
    (8305, 8305),
    (8307, 8315),
    (8317, 8317),
    (8319, 8327),
    (8335, 8335),
    (8337, 8337),
    (8339, 8339),
    (8396, 8397),
    (8399, 8400),
    (8406, 8408),
    (8410, 8420),
    (8430, 8437),
    (8439, 8448),
    (8450, 8459),
    (8468, 8511),
)


def _validation_issue(code, message, path, **details):
    issue = {"code": code, "message": message, "path": path}
    issue.update(details)
    return issue


def _is_fx3u_reserved_m(number):
    return any(start <= number <= end for start, end in _FX3U_RESERVED_M_RANGES)


def _vfd_option_key(option):
    text = str(option or "").strip().casefold()
    if "多段速" in text or any(token in text for token in ("stf", "rh/rm/rl")):
        return "multi_speed"
    if "模拟量" in text or "0-10v" in text or "4-20ma" in text:
        return "analog"
    if "rs485" in text or "modbus" in text:
        return "rs485"
    if "脉冲" in text or "pulse" in text:
        return "pulse"
    if "不确定" in text or "unknown" in text:
        return "unknown"
    return text


def normalize_missing_info(missing_info):
    """Upgrade persisted analysis questions without rewriting project files.

    Older analyses offered only analog/RS485 for a drive.  Add the complete
    command-method choice on read, and mark analog-only details as conditional
    so a user choosing discrete preset-speed terminals is not blocked by an
    irrelevant 4DA question.
    """
    normalized = []
    control_question = ""
    homing_question = ""
    positioning_implementation_question = ""
    for raw_item in missing_info or []:
        if not isinstance(raw_item, dict):
            continue
        item = copy.deepcopy(raw_item)
        question = str(item.get("question", "")).strip()
        if (
            "变频器" in question
            and any(marker in question for marker in ("控制方式", "给定方式", "频率方式"))
        ):
            control_question = question
            options = list(_VFD_CONTROL_OPTIONS)
            seen = {_vfd_option_key(option) for option in options}
            for option in item.get("options", []) or []:
                key = _vfd_option_key(option)
                if key and key not in seen:
                    options.append(str(option))
                    seen.add(key)
            item["options"] = options
        item_id = str(item.get("id", "")).strip()
        if item_id == "homing_required" or any(
            marker in question for marker in ("是否需要回原点", "是否回原点", "需要回原点吗")
        ):
            homing_question = question
        if item_id == "positioning_implementation" or (
            any(marker in question for marker in ("运动控制", "定位", "脉冲输出"))
            and any(marker in question for marker in ("实现方式", "实现方案", "硬件方案"))
        ):
            positioning_implementation_question = question
        normalized.append(item)

    for item in normalized:
        question = str(item.get("question", "")).strip()
        item_id = str(item.get("id", "")).strip()
        if control_question:
            if question == control_question:
                continue
            if "模拟量" in question and any(
                marker in question for marker in ("模块", "通道", "量程", "数字量", "范围")
            ):
                item["required_when"] = {
                    "parameter": control_question,
                    "contains": "模拟量",
                }
        if homing_question and (
            item_id == "homing_method"
            or (
                any(marker in question for marker in ("回原点", "原点回归"))
                and any(marker in question for marker in ("方式", "方法", "指令", "模式"))
            )
        ):
            item["required_when"] = {
                "parameter": homing_question,
                "contains_any": ["是", "需要"],
                "not_contains": ["否", "不需要"],
            }
        if positioning_implementation_question and item_id == "positioning_module_model":
            item["required_when"] = {
                "parameter": positioning_implementation_question,
                "contains_any": [
                    "定位模块",
                    "高速输出适配器",
                    "外接模块",
                    "1PG",
                    "2HSY",
                    "10PG",
                ],
                "not_contains": ["内置高速脉冲", "基本单元内置"],
            }
        if item_id == "positioning_module_quantity":
            item["required_when"] = {
                "all": [
                    {
                        "parameter": "positioning_module_model",
                        "contains_any": ["FX3U-2HSY-ADP", "2HSY-ADP"],
                    },
                    {
                        "parameter": "pulse_output_axis",
                        "contains_any": ["Y2", "Y3", "Y002", "Y003"],
                    },
                ]
            }
    return normalized


def _condition_values(condition, key):
    value = condition.get(key)
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value or "").strip() else []


def _required_when_matches(required_when, parameter_values):
    """Evaluate one declarative dependency against confirmed parameter values."""
    if not isinstance(required_when, dict):
        return False
    all_conditions = required_when.get("all")
    if isinstance(all_conditions, list):
        return bool(all_conditions) and all(
            _required_when_matches(condition, parameter_values)
            for condition in all_conditions
        )
    any_conditions = required_when.get("any")
    if isinstance(any_conditions, list):
        return any(
            _required_when_matches(condition, parameter_values)
            for condition in any_conditions
        )
    controller = str(required_when.get("parameter", "")).strip()
    selected_value = str(parameter_values.get(controller, "") or "").strip()
    if not controller or not selected_value:
        return False
    selected_folded = selected_value.casefold()

    equals = _condition_values(required_when, "equals") or _condition_values(
        required_when, "in"
    )
    contains = _condition_values(required_when, "contains_any") or _condition_values(
        required_when, "contains"
    )
    not_equals = _condition_values(required_when, "not_equals")
    not_contains = _condition_values(required_when, "not_contains")

    if equals:
        positive = any(selected_folded == value.casefold() for value in equals)
    elif contains:
        positive = any(value.casefold() in selected_folded for value in contains)
    else:
        positive = False
    if not positive:
        return False
    if any(selected_folded == value.casefold() for value in not_equals):
        return False
    if any(value.casefold() in selected_folded for value in not_contains):
        return False
    return True


def _validate_device_address(address, plc_model):
    """Return ``(error_message, warning_message, prefix, number)``."""
    match = _EXACT_DEVICE_RE.fullmatch(address)
    if not match:
        return (
            "地址格式无效，应为 X0、Y10、M100、D200 等软元件地址"
            + ("，FX5U 特殊软元件使用 SM/SD 前缀" if plc_model == "FX5U" else ""),
            None,
            None,
            None,
        )

    prefix = match.group(1).upper()
    digits = match.group(2)
    if plc_model == "FX3U" and prefix in {"SM", "SD"}:
        return (
            f"{address} 是 FX5U 的 SM/SD 格式；FX3U 应使用 M8000/D8000 系列",
            None,
            prefix,
            None,
        )

    if prefix in {"X", "Y"} and plc_model == "FX3U":
        if any(char not in "01234567" for char in digits):
            return (
                f"{address} 不是有效的 FX3U 八进制 {prefix} 地址",
                None,
                prefix,
                None,
            )
        number = int(digits, 8)
    else:
        number = int(digits, 10)

    if plc_model == "FX5U" and prefix in {"SM", "SD"}:
        if not (0 <= number <= 2047 or 8000 <= number <= 8999):
            return (
                f"{address} 超出 FX5U {prefix}0-{prefix}2047 及兼容 {prefix}8000 系列范围",
                None,
                prefix,
                number,
            )
        warning = None
        if address not in _KNOWN_READ_ONLY_SPECIAL_INPUTS:
            warning = f"{address} 是系统特殊软元件，使用前需核对读写属性和 CPU/硬件条件"
        return None, warning, prefix, number

    maximum = _DEVICE_LIMITS[plc_model].get(prefix)
    if maximum is None:
        return (
            f"{plc_model} 当前设备模型不支持 {prefix} 地址",
            None,
            prefix,
            number,
        )
    if number > maximum:
        display_number = format(maximum, "o") if plc_model == "FX3U" and prefix in {"X", "Y"} else str(maximum)
        return (
            f"{address} 超出 {plc_model} {prefix}0-{prefix}{display_number} 范围",
            None,
            prefix,
            number,
        )

    if plc_model == "FX3U" and prefix == "M" and _is_fx3u_reserved_m(number):
        return f"{address} 在 FX3U 中为保留或不可用的特殊继电器", None, prefix, number

    warning = None
    if (
        plc_model == "FX3U"
        and prefix in {"M", "D"}
        and number >= 8000
        and address not in _KNOWN_READ_ONLY_SPECIAL_INPUTS
    ):
        warning = f"{address} 是系统特殊软元件，使用前需核对读写属性和 CPU/硬件条件"
    return None, warning, prefix, number


def validate_spec_draft(spec, plc_model=None):
    """Validate an editable confirmed-specification draft without mutating it.

    The function deliberately validates the draft *before* canonicalization so
    duplicate rows and incomplete required values are not silently discarded.
    It returns structured issues that can be rendered by application clients.
    """
    errors = []
    warnings = []
    if not isinstance(spec, dict):
        errors.append(
            _validation_issue("invalid_spec", "规格草稿必须是对象", "$")
        )
        return {"errors": errors, "warnings": warnings}

    model = str(plc_model or spec.get("plc_model") or "FX3U").strip().upper()
    if model not in _DEVICE_LIMITS:
        errors.append(
            _validation_issue(
                "unsupported_plc_model",
                f"暂不支持 PLC 型号：{model or '未填写'}",
                "$.plc_model",
                plc_model=model,
            )
        )
        return {"errors": errors, "warnings": warnings}

    approaches = [
        item for item in (spec.get("approaches") or []) if isinstance(item, dict)
    ]
    # Candidate quality must never block the user's next step. Different
    # approaches may legitimately share the same hard generation contract and
    # differ only in generation_guide / data model / sequencing semantics.
    # Likewise, an unselected candidate with a malformed contract is advisory:
    # only the selected contract can affect generation.
    for index, approach in enumerate(approaches):
        path = f"$.approaches[{index}].generation_contract"
        for message in contract_definition_issues(approach):
            warnings.append(
                _validation_issue(
                    "candidate_approach_contract_warning",
                    message,
                    path,
                    blocking=False,
                )
            )

    selected_approach = spec.get("selected_approach")
    if selected_approach:
        # A self-contradictory selected hard contract is still a real local
        # impossibility. Keep that pre-model-call guard so we do not spend
        # generation tokens on a contract that cannot be satisfied.
        for message in contract_definition_issues(selected_approach):
            errors.append(
                _validation_issue(
                    "invalid_approach_contract",
                    message,
                    "$.selected_approach.generation_contract",
                )
            )
        for message in contract_definition_warnings(selected_approach):
            warnings.append(_validation_issue(
                "unverified_approach_contract", message,
                "$.selected_approach.generation_contract", blocking=False,
            ))
        selected_id = normalize_approach(selected_approach).get("approach_id")
        available_ids = {
            normalize_approach(item).get("approach_id") for item in approaches
        }
        if approaches and selected_id not in available_ids:
            warnings.append(
                _validation_issue(
                    "selected_approach_not_in_candidates",
                    "当前选择方案不在本轮候选列表中；将按当前选择方案继续确认",
                    "$.selected_approach.approach_id",
                    blocking=False,
                )
            )

    seen_parameter_names = set()
    seen_parameter_ids = {}
    parameters = spec.get("parameters", []) or []
    if not isinstance(parameters, list):
        errors.append(
            _validation_issue(
                "invalid_parameters", "关键参数必须是列表", "$.parameters"
            )
        )
        parameters = []
    parameter_values = {}
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        parameter_name = str(parameter.get("name", "")).strip()
        if parameter_name:
            parameter_values.setdefault(
                parameter_name, str(parameter.get("value", "")).strip()
            )
        parameter_id = str(parameter.get("id", "")).strip()
        if parameter_id:
            parameter_values.setdefault(
                parameter_id, str(parameter.get("value", "")).strip()
            )

    for index, parameter in enumerate(parameters):
        path = f"$.parameters[{index}]"
        if not isinstance(parameter, dict):
            errors.append(
                _validation_issue("invalid_parameter", "参数行格式无效", path)
            )
            continue
        name = str(parameter.get("name", "")).strip()
        value = str(parameter.get("value", "")).strip()
        parameter_id = str(parameter.get("id", "")).strip()
        if parameter_id:
            id_key = parameter_id.casefold()
            first = seen_parameter_ids.get(id_key)
            if first is not None:
                first_value = str(parameters[first].get("value", "")).strip()
                conflicting = first_value != value
                issue = _validation_issue(
                    "conflicting_parameter_id" if conflicting else "duplicate_parameter_id",
                    f"参数ID“{parameter_id}”重复（首次位于第 {first + 1} 行）",
                    f"{path}.id",
                    row=index,
                    first_row=first,
                )
                if conflicting:
                    errors.append(issue)
                else:
                    issue["blocking"] = False
                    warnings.append(issue)
            else:
                seen_parameter_ids[id_key] = index
        if not name:
            errors.append(
                _validation_issue(
                    "missing_parameter_name", "参数名称不能为空", f"{path}.name", row=index
                )
            )
        else:
            normalized_name = name.casefold()
            if normalized_name in seen_parameter_names:
                warnings.append(
                    _validation_issue(
                        "duplicate_parameter",
                        f"参数“{name}”重复，后续值可能覆盖前项",
                        f"{path}.name",
                        row=index,
                    )
                )
            seen_parameter_names.add(normalized_name)
        required = bool(parameter.get("required", False))
        required_when = parameter.get("required_when")
        if required and isinstance(required_when, dict):
            required = _required_when_matches(required_when, parameter_values)
        if required and not value:
            errors.append(
                _validation_issue(
                    "required_parameter_missing",
                    f"必填参数“{name or index + 1}”尚未填写",
                    f"{path}.value",
                    row=index,
                )
            )
        if value and _asks_contact_type(name) and _io_parameter_kind(parameter) and not _has_contact_type(value):
            errors.append(
                _validation_issue(
                    "contact_type_missing",
                    f"参数“{name}”还需明确常开或常闭",
                    f"{path}.value",
                    row=index,
                )
            )

    binding_rows = spec.get("io_table")
    if not isinstance(binding_rows, list):
        binding_rows = raw_to_io_table(spec.get("io_allocation_raw", ""))
    binding_history = spec.get("io_bindings", [])

    seen_bindings = {}
    for index, parameter in enumerate(parameters):
        if not isinstance(parameter, dict) or not str(parameter.get("value") or "").strip():
            continue
        hint = binding_hint(parameter)
        if hint is None or parameter.get("id") in QUESTION_IDS:
            continue
        if bound_parameter_is_removed(parameter, binding_rows, binding_history):
            continue
        if not parameter_uses_bound_address(parameter):
            # A question may be associated with D/M/T/etc. without selecting
            # that address. Semantic answers are ordinary parameters, not an
            # invalid I/O answer and therefore create no confirmation gate.
            continue
        address = resolve_parameter_address(parameter, binding_rows, binding_history)
        if address is None:
            errors.append(_validation_issue("invalid_io_answer", "请为该输入/输出选择一个明确的软元件地址",
                                            f"$.parameters[{index}].value", row=index))
            continue
        error, _warning, _prefix, _number = _validate_device_address(address, model)
        if error:
            errors.append(_validation_issue("invalid_io_address", error,
                                            f"$.parameters[{index}].value", row=index))
        identity = hint["binding_id"]
        if identity in seen_bindings and seen_bindings[identity] != address:
            errors.append(_validation_issue("conflicting_io_binding", "同一输入/输出绑定选择了不同地址，请统一选择",
                                            f"$.parameters[{index}].value", row=index))
        seen_bindings[identity] = address

    io_table = spec.get("io_table")
    if io_table is None:
        io_table = []
        for match in ASSIGNMENT_RE.finditer(str(spec.get("io_allocation_raw", "") or "")):
            address = match.group(1).upper()
            io_table.append(
                {
                    "kind": _device_kind(address),
                    "address": address,
                    "label": match.group(2).strip(" ：:，,。"),
                    "source": "raw",
                }
            )
    if not isinstance(io_table, list):
        errors.append(
            _validation_issue("invalid_io_table", "I/O 分配必须是列表", "$.io_table")
        )
        io_table = []

    seen_addresses = {}
    for index, row in enumerate(io_table):
        path = f"$.io_table[{index}]"
        if not isinstance(row, dict):
            errors.append(_validation_issue("invalid_io_row", "I/O 行格式无效", path))
            continue
        address = str(row.get("address", "")).strip().upper()
        label = str(row.get("label", "")).strip()
        kind = str(row.get("kind", "")).strip()
        kind = kind.upper() if kind != "特殊" else kind
        if not address:
            if label or kind:
                errors.append(
                    _validation_issue(
                        "missing_io_address",
                        "I/O 行填写了类别或说明，但地址为空",
                        f"{path}.address",
                        row=index,
                    )
                )
            continue

        first_row = seen_addresses.get(address)
        if first_row is not None:
            errors.append(
                _validation_issue(
                    "duplicate_io_address",
                    f"I/O 地址 {address} 重复（首次位于第 {first_row + 1} 行）",
                    f"{path}.address",
                    row=index,
                    address=address,
                    first_row=first_row,
                )
            )
        else:
            seen_addresses[address] = index

        error_message, warning_message, prefix, number = _validate_device_address(
            address, model
        )
        if error_message:
            errors.append(
                _validation_issue(
                    "invalid_io_address",
                    error_message,
                    f"{path}.address",
                    row=index,
                    address=address,
                    plc_model=model,
                )
            )
        elif warning_message:
            warnings.append(
                _validation_issue(
                    "special_device",
                    warning_message,
                    f"{path}.address",
                    row=index,
                    address=address,
                    plc_model=model,
                )
            )

        if kind and kind not in _VALID_IO_KINDS:
            errors.append(
                _validation_issue(
                    "invalid_io_kind",
                    f"未知 I/O 类别：{kind}",
                    f"{path}.kind",
                    row=index,
                )
            )
        elif not kind:
            errors.append(
                _validation_issue(
                    "missing_io_kind",
                    f"{address} 未选择 I/O 类别",
                    f"{path}.kind",
                    row=index,
                    address=address,
                )
            )
        elif prefix and not error_message:
            special = prefix in {"SM", "SD"} or (
                model == "FX3U" and prefix in {"M", "D"} and number >= 8000
            )
            expected_kind = "特殊" if prefix in {"SM", "SD"} else prefix
            if kind == "特殊" and not special:
                errors.append(
                    _validation_issue(
                        "io_kind_mismatch",
                        f"{address} 不是特殊软元件，类别应为 {prefix}",
                        f"{path}.kind",
                        row=index,
                        address=address,
                    )
                )
            elif kind and kind != "特殊" and kind != expected_kind:
                errors.append(
                    _validation_issue(
                        "io_kind_mismatch",
                        f"地址 {address} 与类别 {kind} 不一致",
                        f"{path}.kind",
                        row=index,
                        address=address,
                    )
                )

        if not label:
            warnings.append(
                _validation_issue(
                    "missing_io_label",
                    f"{address} 未填写用途说明",
                    f"{path}.label",
                    row=index,
                    address=address,
                )
            )

    hardware_validation = validate_hardware_spec(spec, model)
    errors.extend(hardware_validation.get("errors", []))
    warnings.extend(hardware_validation.get("warnings", []))
    return {"errors": errors, "warnings": warnings}


def _question_label(question):
    label = str(question)
    for phrase in (
        "使用哪个轴",
        "接哪个输入点",
        "接哪个输出点",
        "分别接什么输入",
        "接什么输入",
        "接什么输出",
        "是否有",
        "分别",
        "哪个",
        "？",
        "?",
    ):
        label = label.replace(phrase, "")
    return label.strip(" ：:，,。") or "用户确认地址"


def _answer_assignments(question, answer):
    text = str(answer)
    assignments = []
    for device in DEVICE_RE.findall(text):
        device = device.upper()
        before = re.search(
            rf"([^,，;；\n]{{1,20}}?){re.escape(device)}",
            text,
            re.IGNORECASE,
        )
        label = before.group(1).strip(" ：:，,。()（）") if before else ""
        if not label:
            label = _question_label(question)
        assignments.append((device, label))
    return assignments


def _append_io_assignments(io_text, assignments):
    additions = []
    existing = {item.upper() for item in DEVICE_RE.findall(io_text)}
    for device, label in assignments:
        if device not in existing:
            additions.append(f"{device}={label}")
            existing.add(device)
    if not additions:
        return io_text
    prefix = "\n" if io_text else ""
    return io_text + prefix + "确认的 I/O: " + ", ".join(additions)


def _similarity(question, description):
    left = re.sub(r"[\W_]+", "", str(question), flags=re.UNICODE).lower()
    right = re.sub(r"[\W_]+", "", str(description), flags=re.UNICODE).lower()
    if not left or not right:
        return 0.0
    if right in left or left in right:
        return min(len(left), len(right)) / max(len(left), len(right))
    return difflib.SequenceMatcher(None, left, right).ratio()


def _apply_io_answer(io_text, question, answer):
    devices = DEVICE_RE.findall(str(answer))
    if len(devices) != 1 or not io_text:
        return io_text, False
    assignments = _answer_assignments(question, answer)
    label = assignments[0][1] if assignments else ""
    return _apply_device_assignment(io_text, question, devices[0], label)


def _apply_device_assignment(io_text, question, device, label=""):
    if not io_text:
        return io_text, False
    selected = str(device).upper()
    candidates = []
    for match in ASSIGNMENT_RE.finditer(io_text):
        current = match.group(1).upper()
        if current[0] != selected[0]:
            continue
        description = match.group(2)
        candidates.append(
            (
                max(
                    _similarity(question, description),
                    _similarity(label, description),
                ),
                match,
                current,
            )
        )
    if not candidates:
        return io_text, False
    score, match, current = max(candidates, key=lambda item: item[0])
    if score < 0.25:
        return io_text, False
    if current == selected:
        return io_text, True
    start, end = match.span(1)
    return io_text[:start] + selected + io_text[end:], True


def apply_missing_answers_to_io(io_text, answers):
    """Apply address-like confirmation answers into the editable I/O block."""
    io_text = str(io_text or "").strip()
    remaining_answers = {}
    applied_io_answers = {}

    if not isinstance(answers, dict):
        return io_text, remaining_answers, applied_io_answers

    for question, answer in answers.items():
        answer_text = str(answer).strip()
        assignments = _answer_assignments(question, answer_text)
        if assignments:
            updated = io_text
            unmatched = []
            for device, label in assignments:
                updated, matched = _apply_device_assignment(
                    updated,
                    str(question),
                    device,
                    label,
                )
                if not matched:
                    unmatched.append((device, label))
            io_text = _append_io_assignments(updated, unmatched)
            applied_io_answers[str(question)] = answer_text
        else:
            remaining_answers[str(question)] = answer_text
    return io_text, remaining_answers, applied_io_answers


def _device_kind(address, fallback="特殊"):
    text = str(address or "").strip().upper()
    if text.startswith(("SM", "SD")):
        return "特殊"
    if text and text[0] in {"X", "Y", "M", "T", "C", "D", "S"}:
        return text[0]
    return fallback or "特殊"


def raw_to_io_table(io_allocation_raw):
    """Parse the legacy raw I/O text into editable table rows."""
    rows = []
    seen = set()
    text = str(io_allocation_raw or "")
    for match in ASSIGNMENT_RE.finditer(text):
        address = match.group(1).upper()
        label = match.group(2).strip(" ：:，,。")
        key = address.upper()
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "kind": _device_kind(address),
                "address": address,
                "label": label,
                "source": "raw",
            }
        )
    return rows


def io_table_to_raw(io_table):
    """Render canonical I/O table rows into the raw text used by prompts."""
    groups = {kind: [] for kind in IO_KIND_ORDER}
    for row in io_table or []:
        if not isinstance(row, dict):
            continue
        address = str(row.get("address", "")).strip().upper()
        label = str(row.get("label", "")).strip()
        if not address:
            continue
        kind = str(row.get("kind") or _device_kind(address)).strip() or "特殊"
        kind = kind.upper() if kind != "特殊" else kind
        if kind not in groups:
            kind = _device_kind(address)
        groups.setdefault(kind, []).append(f"{address}={label}" if label else address)
    lines = []
    for kind in IO_KIND_ORDER:
        values = groups.get(kind) or []
        if values:
            lines.append(f"{kind}: " + ", ".join(values))
    return "\n".join(lines)


def _suggested_io_to_table(suggested_io):
    """Convert only the documented ``suggested_io`` device shapes to rows.

    Analysis models sometimes add descriptive objects such as
    ``analog_output: {channel, address, note}``.  Those keys are metadata, not
    PLC device addresses.  Accepting arbitrary nested mappings used to turn
    CHANNEL into a C device merely because it starts with the letter C.  Keep
    this adapter deliberately strict; hardware choices belong in
    ``missing_info`` and must be confirmed by the user.
    """
    rows = []
    if not isinstance(suggested_io, dict):
        return rows
    for category, values in suggested_io.items():
        category_text = str(category).strip()
        normal_kind = category_text.upper()
        special_kind = category_text.lower()
        if normal_kind in _SUGGESTED_IO_DEVICE_KINDS:
            allowed_prefixes = {normal_kind}
            row_kind = normal_kind
        elif special_kind in _SUGGESTED_IO_SPECIAL_KINDS:
            allowed_prefixes = _SUGGESTED_IO_SPECIAL_KINDS[special_kind]
            row_kind = "特殊"
        else:
            # Unknown fields (for example analog_output) are not I/O rows.
            continue

        source = "analysis"
        if isinstance(values, dict):
            iterable = values.items()
        elif isinstance(values, list):
            iterable = ((value, "") for value in values)
        else:
            continue
        for address, label in iterable:
            address = str(address).strip().upper()
            match = _EXACT_DEVICE_RE.fullmatch(address)
            if not match:
                continue
            prefix = match.group(1).upper()
            if prefix not in allowed_prefixes:
                continue
            number = int(match.group(2), 10)
            if special_kind == "special_relays" and prefix == "M" and number < 8000:
                continue
            if special_kind == "special_registers" and prefix == "D" and number < 8000:
                continue
            label_text = str(label or "").strip()
            if not label_text:
                label_text = _KNOWN_READ_ONLY_SPECIAL_INPUTS.get(address, "")
            rows.append(
                {
                    "kind": row_kind,
                    "address": address,
                    "label": label_text,
                    "source": source,
                }
            )
    return rows


def _parameter_choice_metadata(item):
    """Keep choices separate from prose and from the user's confirmed value."""
    metadata = parameter_metadata(item)
    if isinstance(item.get("io_binding"), dict):
        hint = binding_hint(item)
        if hint is not None:
            metadata["io_binding"] = hint
    if isinstance(item.get("options"), (list, tuple)):
        metadata["options"] = [str(option) for option in item["options"] if str(option).strip()]
    if "suggested_default" in item:
        suggestion = item["suggested_default"]
        metadata["suggested_default"] = "" if suggestion is None else str(suggestion)
    return metadata


def _missing_info_to_parameters(missing_info):
    parameters = []
    for item in normalize_missing_info(missing_info):
        if not isinstance(item, dict):
            continue
        name = str(item.get("question", "")).strip()
        if not name:
            continue
        options = [str(option) for option in item.get("options", [])]
        default = item.get("default")
        # An AI-proposed default is a suggestion, not user confirmation.  Keep
        # the editable value empty so required choices (especially hardware
        # interfaces) block confirmation until the user actively selects one.
        value = (str(item["confirmed_value"]) if item.get("source") == "confirmed_request_fact"
                 and item.get("id") == "control_method" and "confirmed_value" in item else "")
        notes = list(options)
        if default is not None and str(default).strip():
            notes.append(f"AI建议：{str(default).strip()}（尚未确认）")
        parameter = {
            "id": str(item.get("id", "")).strip(),
            "name": name,
            "value": value,
            "source": str(item.get("source", "")).strip() or "analysis",
            "required": bool(item.get("required", True)),
            "note": " / ".join(notes),
            "note_provenance": text_origin(" / ".join(notes), "review_choices"),
            "options": options,
        }
        parameter.update({k: v for k, v in parameter_metadata(item).items() if k != "note_provenance"})
        if isinstance(item.get("io_binding"), dict):
            hint = binding_hint(item)
            if hint is not None:
                parameter["io_binding"] = hint
        if default is not None:
            parameter["suggested_default"] = str(default)
        if isinstance(item.get("required_when"), dict):
            parameter["required_when"] = copy.deepcopy(item["required_when"])
        parameters.append(parameter)
    return parameters


def _merge_io_rows(base_rows, incoming_rows):
    merged = []
    by_address = {}
    base_count = len(base_rows or [])
    base_labels = []
    for row in base_rows or []:
        if not isinstance(row, dict):
            continue
        address = str(row.get("address", "")).strip().upper()
        if not address:
            continue
        kind = str(row.get("kind") or _device_kind(address)).strip() or _device_kind(address)
        base_labels.append((kind, str(row.get("label", "")).strip().casefold()))
    for position, row in enumerate(list(base_rows or []) + list(incoming_rows or [])):
        if not isinstance(row, dict):
            continue
        address = str(row.get("address", "")).strip().upper()
        if not address:
            continue
        clean = {
            "kind": str(row.get("kind") or _device_kind(address)).strip() or _device_kind(address),
            "address": address,
            "label": str(row.get("label", "")).strip(),
            "source": str(row.get("source", "")).strip() or "analysis",
        }
        for field in ("binding_id", "source_parameter_id", "row_id"):
            if isinstance(row.get(field), str):
                clean[field] = row[field]
        if position >= base_count and not clean.get("binding_id"):
            if address in by_address:
                # Once a row exists in a confirmed specification, a later model
                # suggestion must never overwrite its address-adjacent metadata
                # such as a user-edited purpose label.
                continue
            exact_previous_labels = [item for item in base_labels
                                     if item[0] == clean["kind"] and clean["label"]
                                     and item[1] == clean["label"].casefold()]
            if len(exact_previous_labels) == 1:
                # Reanalysis may repeat the old suggested address after the
                # operator moved the device. Preserve the confirmed row instead
                # of adding a duplicate suggestion for the same unique purpose.
                continue
        if address in by_address:
            previous = merged[by_address[address]]
            for field in ("binding_id", "source_parameter_id", "row_id"):
                if field in previous:
                    clean.setdefault(field, previous[field])
            merged[by_address[address]] = clean
        else:
            by_address[address] = len(merged)
            merged.append(clean)
    return merged


def _parameters_from_missing_answers(missing_answers):
    parameters = []
    if not isinstance(missing_answers, dict):
        return parameters
    for name, value in missing_answers.items():
        parameters.append(
            {
                "name": str(name),
                "value": str(value),
                "source": "previous",
                "required": False,
                "note": "",
            }
        )
    return parameters


def _merge_parameters(base_parameters, incoming_parameters):
    merged = []
    by_id = {}
    by_name = {}
    for item in list(base_parameters or []) + list(incoming_parameters or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        clean = {
            "id": str(item.get("id", "")).strip(),
            "name": name,
            "value": parameter_value(item),
            "source": str(item.get("source", "")).strip() or "analysis",
            "required": bool(item.get("required", False)),
            "note": str(item.get("note", "")).strip(),
        }
        clean.update(_parameter_choice_metadata(item))
        if isinstance(item.get("required_when"), dict):
            clean["required_when"] = copy.deepcopy(item["required_when"])
        stable_id = clean["id"].casefold()
        name_key = name.casefold()
        position = by_id.get(stable_id) if stable_id else None
        if position is None:
            candidate = by_name.get(name_key)
            # Different stable identities can share a display label (e.g.
            # two motors' start inputs). Wording alone must not merge them.
            if candidate is not None and (not stable_id or not merged[candidate].get("id")):
                position = candidate
        if position is not None:
            existing = merged[position]
            for key, value in _parameter_choice_metadata(existing).items():
                clean.setdefault(key, value)
            # A newly generated unanswered question must not erase a value
            # that the user already confirmed in an earlier specification.
            # Stable ids also merge wording variants such as a trailing "？".
            # If two persisted rows already conflict, retain the first
            # confirmed value instead of silently letting list order change
            # the selected implementation.
            answered = existing.get("value") not in (None, "")
            if answered:
                # Reanalysis cannot rebind an answered identity, even when the
                # new prose happens to carry exactly the same scalar answer.
                for key in ("semantic_key", "value_kind", "unit"):
                    if key in existing:
                        clean[key] = existing[key]
            if answered and (
                clean["value"] in (None, "") or clean["value"] != existing["value"]
            ):
                clean["value"] = existing["value"]
                clean["source"] = existing.get("source") or clean["source"]
                clean["name"] = existing.get("name") or clean["name"]
                clean["note"] = existing.get("note") or clean["note"]
                if existing.get("note_provenance"):
                    clean["note_provenance"] = copy.deepcopy(existing["note_provenance"])
                # Stable identity belongs to the existing answered parameter.
                for key in ("semantic_key", "value_kind", "unit"):
                    if key in existing:
                        clean[key] = existing[key]
                clean["required"] = bool(existing.get("required", clean["required"]))
                if isinstance(existing.get("required_when"), dict):
                    clean["required_when"] = copy.deepcopy(
                        existing["required_when"]
                    )
                else:
                    clean.pop("required_when", None)
            if not clean.get("id") and existing.get("id"):
                clean["id"] = existing["id"]
            merged[position] = clean
        else:
            position = len(merged)
            merged.append(clean)
        final = merged[position]
        final_id = str(final.get("id", "")).strip().casefold()
        final_name = str(final.get("name", "")).strip().casefold()
        if final_id:
            by_id[final_id] = position
        if final_name:
            by_name[final_name] = position
        # Keep the old wording as an alias when an incoming row used the same
        # stable id with slightly different punctuation.
        by_name[name_key] = position
    return merged


def build_review_draft(analysis, previous_spec=None):
    """Build the editable single-source review draft from AI analysis."""
    analysis = analysis or {}
    previous = canonicalize_confirmed_spec(previous_spec) if previous_spec else {}
    plc_model = str(
        analysis.get("plc_model") or previous.get("plc_model") or "FX3U"
    ).strip().upper()
    analysis = ensure_hardware_questions(analysis, plc_model, confirmed_spec=previous)
    suggested_rows = _suggested_io_to_table(analysis.get("suggested_io", {}))
    previous_rows = previous.get("io_table") or raw_to_io_table(
        previous.get("io_allocation_raw", "")
    )
    if suggested_rows:
        io_table = _merge_io_rows(previous_rows, suggested_rows)
    else:
        io_table = _merge_io_rows(previous_rows, [])

    previous_parameters = previous.get("parameters") or _parameters_from_missing_answers(
        previous.get("missing_answers", {})
    )
    retained_parameters = []
    for item in previous_parameters:
        if not isinstance(item, dict):
            continue
        if is_automatic_hardware_question(item):
            # Drop only unanswered legacy rows. Preserve facts the user had
            # already entered, but make them optional from now on.
            if not str(item.get("value", "")).strip():
                continue
            item = copy.deepcopy(item)
            item["required"] = False
            item.pop("required_when", None)
        retained_parameters.append(item)
    intent = analysis.get("hardware_intent") or {}
    drop_prior_vfd = intent.get("source") == "user_request" and not intent.get("flags", {}).get("vfd")
    if drop_prior_vfd:
        # A current explicit equipment removal also removes its old question
        # values/dependents from the NEW draft, never from the stored revision.
        filtered = ensure_hardware_questions({
            "hardware_intent": intent,
            "missing_info": [{**item, "question": item["name"]} for item in retained_parameters],
        }, plc_model)["missing_info"]
        retained_parameters = [{k: v for k, v in item.items() if k != "question"} for item in filtered]
    previous_parameters = retained_parameters
    parameters = _merge_parameters(
        previous_parameters,
        restore_bound_choices(
            _missing_info_to_parameters(analysis.get("missing_info", [])),
            previous_rows, previous.get("io_bindings", []),
        ),
    )

    io_table, parameters = bind_known_question_rows(io_table, parameters)

    approaches = [
        normalize_approach(item)
        for item in (analysis.get("approaches") or [])
        if isinstance(item, dict)
    ]
    selected_approach = (
        copy.deepcopy(approaches[0])
        if approaches
        else normalize_approach(previous.get("selected_approach", {}))
    )
    draft = {
        "schema_version": 3,
        "plc_model": plc_model,
        "summary": analysis.get("summary") or previous.get("summary", ""),
        "approaches": approaches,
        "selected_approach": selected_approach,
        "parameters": parameters,
        "io_table": io_table,
        "io_allocation_raw": io_table_to_raw(io_table),
        "user_notes": previous.get("user_notes", ""),
        "missing_answers": {},
        "hardware_requirements": copy.deepcopy(
            analysis.get("hardware_requirements")
            or previous.get("hardware_requirements")
            or {}
        ),
        "hardware_context": copy.deepcopy(
            analysis.get("hardware_config")
            or previous.get("hardware_context")
            or {}
        ),
        "execution_semantics": copy.deepcopy(
            analysis.get("execution_semantics")
            or previous.get("execution_semantics")
            or []
        ),
    }
    if analysis.get("summary"):
        draft["summary_provenance"] = text_origin(str(draft["summary"]), "analysis_overview")
    elif previous.get("summary_provenance"):
        draft["summary_provenance"] = copy.deepcopy(previous["summary_provenance"])
    if drop_prior_vfd:
        cleaned = ensure_hardware_questions({
            "hardware_intent": intent, "hardware_config": draft["hardware_context"],
            "approaches": [draft["selected_approach"]] if draft["selected_approach"] else [],
            "missing_info": [],
        }, plc_model)
        draft["hardware_context"] = cleaned.get("hardware_config", {})
        if not cleaned.get("approaches"):
            draft["selected_approach"] = {}
    if isinstance(analysis.get("hardware_intent"), dict):
        draft["hardware_intent"] = copy.deepcopy(analysis["hardware_intent"])
    from plc.specification.provenance import intent_context, decision_context
    intent_source = analysis if ("intent_context" in analysis or "engineering_context" in analysis) else previous
    intent = intent_context(intent_source)
    if intent:
        draft["intent_context"] = intent
    # No fallback to the previous confirmation's proposals/evidence. A draft
    # may preserve current intent without resurrecting any old candidate audit.
    receipt = decision_context(analysis)
    if receipt:
        receipt["confirmation"] = {"status": "draft"}
        draft["decision_receipt"] = receipt
    if previous.get("io_bindings"):
        draft["io_bindings"] = copy.deepcopy(previous["io_bindings"])
    draft["hardware_profile"] = build_hardware_profile(draft, plc_model)
    return restore_review_choices(draft, analysis)


def _io_parameter_kind(parameter):
    parameter_id = str(parameter.get("id", "")).strip().casefold()
    hint = binding_hint(parameter)
    if hint is not None:
        return hint["kind"]
    explicit_ids = {"start_input": "X", "stop_input": "X", "output_coil": "Y", "output_address": "Y"}
    if parameter_id in explicit_ids:
        return explicit_ids[parameter_id]
    question = str(parameter.get("name", "")).replace(" ", "").upper()
    for kind, phrases in (("X", ("哪个输入", "哪个X", "接什么输入")),
                          ("Y", ("哪个输出", "哪个Y", "接什么输出"))):
        if any(phrase in question for phrase in phrases):
            return kind
    return None


def _asks_contact_type(question):
    text = str(question).casefold()
    return ("常开" in text and ("常闭" in text or "常閉" in text)) or (
        "normally open" in text and "normally closed" in text
    )


def _has_contact_type(value):
    return bool(confirmed_input_levels(value))


def _restore_io_parameter_choices(parameter, io_rows):
    """Offer only addresses already allocated, with no assumed contact type."""
    if parameter.get("options"):
        return
    kind = _io_parameter_kind(parameter)
    if kind is None:
        return
    question = str(parameter.get("name", ""))
    candidates = {}
    for row in io_rows:
        if not isinstance(row, dict):
            continue
        address = str(row.get("address", "")).strip().upper()
        if re.fullmatch(kind + r"\d+", address):
            candidates[address] = max(candidates.get(address, 0), _similarity(question, row.get("label", "")))
    addresses = sorted(candidates, key=lambda address: (-candidates[address], address))
    if not addresses:
        return
    if _asks_contact_type(question):
        parameter["options"] = [f"{address}，{contact}" for address in addresses for contact in ("常开", "常闭")]
    else:
        parameter["options"] = addresses
        if candidates[addresses[0]] >= 0.25:
            parameter.setdefault("suggested_default", addresses[0])


def restore_review_choices(draft, analysis):
    """Enrich an older draft from its own analysis without changing answers.

    Older v3 drafts retained only a prose note. Recover structured choices on
    read, never by splitting that note: legitimate choices can contain slashes.
    """
    restored = copy.deepcopy(draft)
    if not isinstance(restored, dict) or not isinstance(analysis, dict):
        return restored
    questions = _missing_info_to_parameters(analysis.get("missing_info", []))
    by_id = {item["id"].casefold(): item for item in questions if item.get("id")}
    by_name = {item["name"].casefold(): item for item in questions}
    io_rows = list(restored.get("io_table") or [])
    if not io_rows:
        io_rows = _suggested_io_to_table(analysis.get("suggested_io") or analysis.get("io_allocation") or {})
    if not io_rows:
        io_rows = raw_to_io_table(restored.get("io_allocation_raw", ""))
    for parameter in restored.get("parameters", []) or []:
        if not isinstance(parameter, dict):
            continue
        item = by_id.get(str(parameter.get("id", "")).strip().casefold())
        if item is None:
            item = by_name.get(str(parameter.get("name", "")).strip().casefold())
        if item is not None:
            for key, value in _parameter_choice_metadata(item).items():
                parameter.setdefault(key, value)
        _restore_io_parameter_choices(parameter, io_rows)
    return restored


def preserve_io_user_edits(previous_spec, draft):
    """Carry direct specification-editor I/O removals as non-generation provenance.

    The metadata is used only to stop later analysis suggestions from reviving
    addresses the operator explicitly removed or moved. It never blocks saving,
    never reaches Agent B, and adding the address again clears its tombstone.
    """
    result = copy.deepcopy(draft or {})
    if not isinstance(previous_spec, dict):
        return result

    def addresses(value):
        rows = value.get("io_table", []) if isinstance(value, dict) else []
        return {
            canonical_device(str(row.get("address") or "").strip().upper())
            for row in rows or [] if isinstance(row, dict) and row.get("address")
        }

    previous_addresses = addresses(previous_spec)
    current_addresses = addresses(result)
    previous_overrides = previous_spec.get("io_user_overrides")
    removed = set()
    if isinstance(previous_overrides, dict):
        removed.update(
            canonical_device(str(address).strip().upper())
            for address in previous_overrides.get("removed_addresses", []) or []
            if str(address).strip()
        )
    removed.update(previous_addresses - current_addresses)
    removed.difference_update(current_addresses)

    overrides = copy.deepcopy(result.get("io_user_overrides"))
    if not isinstance(overrides, dict):
        overrides = {}
    if removed:
        overrides["removed_addresses"] = sorted(removed)
        result["io_user_overrides"] = overrides
    else:
        overrides.pop("removed_addresses", None)
        if overrides:
            result["io_user_overrides"] = overrides
        else:
            result.pop("io_user_overrides", None)
    return result


def canonicalize_confirmed_spec(spec):
    """Return one conflict-free specification for storage and API injection."""
    canonical = copy.deepcopy(spec or {})
    canonical["approaches"] = [
        normalize_approach(item)
        for item in (canonical.get("approaches") or [])
        if isinstance(item, dict)
    ]
    canonical["selected_approach"] = normalize_approach(
        canonical.get("selected_approach") or {}
    )
    # Structured rows are authoritative. Legacy raw text is imported only when
    # no table exists, never used as a lossy round-trip storage format.
    io_table = canonical.get("io_table")
    if not isinstance(io_table, list):
        io_table = raw_to_io_table(canonical.get("io_allocation_raw", ""))
    parameters = _merge_parameters([], canonical.get("parameters", []) or [])
    for parameter in parameters:
        if not parameter.get("source"):
            parameter["source"] = "user"
    answers = canonical.get("missing_answers")
    legacy = _parameters_from_missing_answers(answers)
    rows, retained, bindings, applied = bind_answers(
        io_table, parameters + legacy, canonical.get("io_bindings", []),
        protected_ids=QUESTION_IDS,
    )
    for row in rows:
        row["address"] = str(row.get("address") or "").strip().upper()
        row["kind"] = str(row.get("kind") or _device_kind(row["address"])).strip()
        row["label"] = str(row.get("label") or "").strip()
        row["source"] = str(row.get("source") or "raw").strip()
    canonical["io_table"] = rows
    canonical["io_allocation_raw"] = io_table_to_raw(rows)
    # Legacy unanswered/non-address facts remain in their original slot, not
    # duplicated as new editable questions. Address provenance is stored apart
    # from parameters so normal review does not keep asking completed questions.
    parameter_names = {p.get("name") for p in parameters}
    canonical["parameters"] = [p for p in retained if p.get("name") in parameter_names]
    canonical["missing_answers"] = {
        str(key): str(value).strip() for key, value in (answers or {}).items() if str(key) not in applied
    } if isinstance(answers, dict) else {}
    if bindings:
        canonical["io_bindings"] = bindings
    else:
        canonical.pop("io_bindings", None)
    applied_io_answers = dict(canonical.get("io_overrides_applied") or {})
    applied_io_answers.update(applied)
    canonical["plc_model"] = str(canonical.get("plc_model") or "FX3U").strip().upper()
    canonical["hardware_profile"] = build_hardware_profile(
        canonical,
        canonical["plc_model"],
    )
    if applied_io_answers:
        canonical["io_overrides_applied"] = applied_io_answers
    else:
        canonical.pop("io_overrides_applied", None)
    canonical["user_notes"] = str(canonical.get("user_notes", "")).strip()
    from plc.semantics import normalize_semantic_requirements

    canonical["execution_semantics"] = normalize_semantic_requirements(
        canonical.get("execution_semantics") or []
    )
    overrides = canonical.get("io_user_overrides")
    if isinstance(overrides, dict):
        removed = {
            canonical_device(str(address).strip().upper())
            for address in overrides.get("removed_addresses", []) or []
            if str(address).strip()
        }
        active_addresses = {
            canonical_device(str(row.get("address") or "").strip().upper())
            for row in canonical.get("io_table", []) or []
            if isinstance(row, dict) and row.get("address")
        }
        removed.difference_update(active_addresses)
        if removed:
            overrides = copy.deepcopy(overrides)
            overrides["removed_addresses"] = sorted(removed)
            canonical["io_user_overrides"] = overrides
        else:
            canonical.pop("io_user_overrides", None)
    elif "io_user_overrides" in canonical:
        canonical.pop("io_user_overrides", None)
    if (spec or {}).get("schema_version") == 4:
        from plc.specification.provenance import confirmed_spec_fields
        return confirmed_spec_fields(canonical)
    canonical["schema_version"] = 3  # Editable legacy/review draft, not a sealed confirmation.
    return canonical


def edit_io_row(row=None, address=None):
    """Prepare a form row; validity is still decided by validate_spec_draft."""
    result = copy.deepcopy(row) if row is not None else {"address": "", "label": "", "kind": "X"}
    if address is not None:
        result["address"] = address.upper()
        result["kind"] = _device_kind(address)
    return result


def editor_io_kind(value):
    """The existing desktop category display normalization, owned by Core."""
    normalized = value.strip().upper()
    return normalized if not normalized or normalized in {"X", "Y", "M", "T", "C", "D", "S"} else "特殊"

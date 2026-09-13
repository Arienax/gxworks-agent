import os
import json
import re
import sys
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from i18n import language_scoped, tr
from response_language import TEXT_RESPONSE, preserved_annotations as source_annotations
from workflow_response_contracts import (
    ANALYSIS_RESPONSE, DEBUG_RESPONSE, DIAGNOSIS_RESPONSE, INSPECTION_RESPONSE,
    LADDER_RESPONSE, PATCH_RESPONSE, ST_RESPONSE, TEST_SUITE_RESPONSE,
)
from approach_contracts import normalize_approach
from draw import AdvancedSVGLadder
from config_manager import get_api_key, get_model_profile, load_full_config
from model_provider import (
    ImageAttachment,
    ModelRequest,
    ResponseRejectedError,
    UserMessage,
    collect_response,
    get_active_provider,
    reload_model_provider,
    reset_model_provider,
    strip_legacy_provider_fields,
)
from resource_paths import resource_path
from plc_generation_context import (
    ST_SYSTEM_PROMPT, LADDER_SYSTEM_PROMPT, _st_system_prompt_for_model,
    _build_knowledge_query, _build_knowledge_context, _select_system_prompt,
    _routing_text_with_selected_approach, _load_plc_models, _build_model_context,
    _confirmed_context_text, _with_confirmed_context, build_generation_instructions,
)
from prompt_context_policy import (
    audit_request, audit_section, context_policy_scope, manual_lookup_decision,
    resolve_context_policy, select_base_prompt,
)
from plc_json_validator import PLCJsonValidationError, parse_device_address
from hardware_profiles import ensure_hardware_questions
from pattern_library import (
    assemble_prompt,
    build_workflow_prompt,
    classify_request,
)


_provider_session = ContextVar("workflow_provider_session", default=None)
_workflow_model = ContextVar("workflow_model_name", default=None)


@contextmanager
def provider_scope(provider=None, *, model_name=None):
    """Keep one provider for an entire workflow, including fallback and repair.

    A caller may supply a provider captured when a job is submitted. Otherwise
    resolution is lazy, so deterministic and injected offline workflows do not
    initialize credentials or SDK clients. Nested workflows reuse the session.
    """
    existing = _provider_session.get()
    session = existing if provider is None and existing is not None else [provider]
    token = _provider_session.set(session)
    model_token = _workflow_model.set(model_name or _workflow_model.get())
    try:
        with context_policy_scope():
            yield
    finally:
        _workflow_model.reset(model_token)
        _provider_session.reset(token)


def _workflow_provider():
    session = _provider_session.get()
    if session is None:
        return get_active_provider()
    if session[0] is None:
        session[0] = get_active_provider()
    return session[0]


def load_config():
    """Compatibility projection for callers that still expect key/base URL."""

    config = load_full_config()
    api_key = get_api_key(config)
    base_url = get_model_profile(config).get("baseUrl", "").strip()

    if not api_key:
        raise ValueError("未配置 API Key，请打开“设置”中的 API 设置完成配置。")

    return api_key, base_url

def reload_api_client():
    """One-release compatibility alias for the provider cache."""

    return reload_model_provider()


def reset_api_client():
    """One-release compatibility alias for the provider cache."""

    reset_model_provider()


def _active_model_name(config=None):
    return _workflow_model.get() or str(get_model_profile(config or load_full_config()).get("model") or "")


@language_scoped
def _request_model(
    messages,
    *,
    model_name=None,
    effort=None,
    stream=False,
    tools=None,
    request_timeout=None,
    max_retries=None,
    on_reasoning_chunk=None,
    on_content_chunk=None,
    on_event=None,
    fallback_to_non_stream=False,
    on_fallback=None,
    options=None,
    response_contract=TEXT_RESPONSE,
    preserved_annotations=(),
):
    """Run one canonical request without exposing provider response shapes."""

    request_options = dict(options or {})
    if effort is not None:
        request_options["reasoning_effort"] = effort
    # Format and transport are independent. Preserve an explicitly supplied
    # native schema on streaming requests as well as non-streaming requests.
    if response_contract.format == "text":
        request_options.setdefault("response_format", None)
    request = ModelRequest.from_messages(
        messages,
        model=model_name or None,
        tools=tuple(tools or ()),
        options=request_options,
        stream=bool(stream),
        timeout=request_timeout,
        max_retries=max_retries,
        response_contract=response_contract,
        preserved_annotations=preserved_annotations,
    )
    audit_request(request.messages)
    return collect_response(
        _workflow_provider(),
        request,
        on_reasoning_chunk=on_reasoning_chunk,
        on_content_chunk=on_content_chunk,
        on_event=on_event,
        fallback_to_non_stream=fallback_to_non_stream,
        on_fallback=on_fallback,
    )


def _user_message_with_images(content, image_attachments=None):
    """Build one canonical user message without exposing provider wire fields."""

    images = tuple(image_attachments or ())
    if any(not isinstance(item, ImageAttachment) for item in images):
        raise TypeError("image_attachments must contain ImageAttachment values")
    return UserMessage(content, images)


# ============================
# 阶段1：需求分析 Prompt + 函数
# ============================
# PLC 型号识别 + 特殊软元件注入
# ============================


def _detect_plc_model(user_input: str) -> str:
    """从用户输入中检测 PLC 型号，找不到则用 config 默认"""
    input_upper = user_input.upper()
    models = _load_plc_models()
    for model in models:
        if model == "default":
            continue
        if model.upper() in input_upper:
            return model
    config = load_full_config()
    return config.get("plc_model", models.get("default", "FX3U"))


def _resolve_plc_model(user_input="", confirmed_context=None, explicit_model=None):
    """Resolve the selected project model before consulting global config."""
    models = _load_plc_models()
    if explicit_model:
        candidate = str(explicit_model).strip().upper()
        if candidate in models:
            return candidate
    if isinstance(confirmed_context, dict):
        candidate = str(confirmed_context.get("plc_model", "")).strip().upper()
        if candidate in models:
            return candidate
    return _detect_plc_model(str(user_input or ""))


_ANALYSIS_IO_KINDS = {"X", "Y", "M", "D", "T", "C", "S", "SM", "SD"}
_ASSUMPTION_MARKERS = ("假设", "暂定", "待确认", "需确认", "unknown", "assume")


def _iter_analysis_text(value):
    if isinstance(value, dict):
        for nested in value.values():
            for item in _iter_analysis_text(nested):
                yield item
    elif isinstance(value, (list, tuple)):
        for nested in value:
            for item in _iter_analysis_text(nested):
                yield item
    elif value is not None:
        yield str(value)


def _normalize_analysis_result(result, plc_model="FX3U", user_text=""):
    """Normalize phase-one AI JSON before the specification editor sees it.

    Only actual PLC device addresses remain in ``suggested_io``.  Hardware
    metadata is preserved separately so strict specification validation never
    mistakes CHANNEL/ADDRESS/NOTE fields for C/D/I/O devices.
    """
    if not isinstance(result, dict):
        raise ValueError("Analysis response must be a JSON object")

    normalized = dict(result)
    normalized["approaches"] = [
        normalize_approach(item)
        for item in (normalized.get("approaches") or [])
        if isinstance(item, dict)
    ]
    raw_io = normalized.get("suggested_io", {})
    if not isinstance(raw_io, dict):
        raw_io = {}

    existing_hardware = normalized.get("hardware_config")
    if isinstance(existing_hardware, dict):
        hardware = dict(existing_hardware)
    elif existing_hardware in (None, "", []):
        hardware = {}
    else:
        hardware = {"reported_value": existing_hardware}

    existing_assumptions = normalized.get("assumptions", [])
    if isinstance(existing_assumptions, list):
        assumptions = [str(item) for item in existing_assumptions if str(item).strip()]
    elif existing_assumptions:
        assumptions = [str(existing_assumptions)]
    else:
        assumptions = []

    existing_diagnostics = normalized.get("format_diagnostics", [])
    diagnostics = list(existing_diagnostics) if isinstance(existing_diagnostics, list) else []
    clean_io = {}
    unmapped = []
    metadata = {}

    def add_diagnostic(code, path, message, value=None):
        item = {"code": code, "path": path, "message": message}
        if value is not None:
            item["value"] = value
        diagnostics.append(item)

    def capture_assumptions(path, value):
        for text in _iter_analysis_text(value):
            lower = text.lower()
            if any(marker in lower for marker in _ASSUMPTION_MARKERS):
                note = "%s: %s" % (path, text)
                if note not in assumptions:
                    assumptions.append(note)

    for raw_category, values in raw_io.items():
        category_text = str(raw_category).strip()
        category_lower = category_text.lower()
        category_upper = category_text.upper()
        special_relays = category_lower == "special_relays"
        special_registers = category_lower == "special_registers"
        is_device_category = category_upper in _ANALYSIS_IO_KINDS

        if not (is_device_category or special_relays or special_registers):
            key = re.sub(r"[^a-z0-9_]+", "_", category_lower).strip("_") or "metadata"
            if key in hardware:
                metadata[category_text] = values
            else:
                hardware[key] = values
            capture_assumptions("suggested_io.%s" % category_text, values)
            add_diagnostic(
                "non_io_metadata_moved",
                "suggested_io.%s" % category_text,
                str(tr("非软元件类别已移入 hardware_config，未作为 I/O 使用。")),
                values,
            )
            continue

        if isinstance(values, dict):
            entries = list(values.items())
        elif isinstance(values, list):
            if is_device_category:
                add_diagnostic(
                    "io_labels_required",
                    "suggested_io.%s" % category_text,
                    str(tr("普通 I/O 类别必须使用地址到非空用途说明的字典；仅地址列表已忽略。")),
                    values,
                )
                continue
            entries = [(value, "") for value in values]
        else:
            metadata[category_text] = values
            add_diagnostic(
                "invalid_io_container",
                "suggested_io.%s" % category_text,
                str(tr("I/O 类别必须是地址字典；特殊软元件类别也可使用地址列表，原值已移入 hardware_config。")),
                values,
            )
            continue

        for raw_address, raw_label in entries:
            address = str(raw_address).strip().upper()
            path = "suggested_io.%s.%s" % (category_text, address or "<empty>")
            try:
                parsed_address = parse_device_address(address, plc_model)
                if parsed_address is None:
                    raise ValueError(
                        str(tr("{address} 不是 {model} 的合法软元件地址", address=address or "<empty>", model=plc_model))
                    )
                actual_kind, _number = parsed_address
            except (PLCJsonValidationError, ValueError, TypeError) as exc:
                item = {
                    "category": category_text,
                    "address": address,
                    "label": raw_label,
                }
                unmapped.append(item)
                capture_assumptions(path, raw_label)
                add_diagnostic("invalid_io_address", path, str(exc), item)
                continue

            allowed_kinds = None
            if special_relays:
                allowed_kinds = {"M", "SM"}
            elif special_registers:
                allowed_kinds = {"D", "SD"}
            elif is_device_category:
                allowed_kinds = {category_upper}

            if actual_kind not in allowed_kinds:
                add_diagnostic(
                    "io_category_corrected",
                    path,
                    str(tr("地址前缀与类别不一致，已按真实前缀归类为 {kind}。", kind=actual_kind)),
                    {"from": category_text, "to": actual_kind},
                )

            label = raw_label
            if isinstance(label, (dict, list)):
                add_diagnostic(
                    "io_label_normalized",
                    path,
                    str(tr("结构化标签不能作为 I/O 说明，已转为简短 JSON 文本。")),
                )
                label = json.dumps(label, ensure_ascii=False, separators=(",", ":"))
            else:
                label = str(label or "").strip()
            if is_device_category and not label:
                add_diagnostic(
                    "missing_io_label",
                    path,
                    str(tr("普通 I/O 地址缺少用途说明，已忽略该项。")),
                )
                continue
            if actual_kind == "SM" or (special_relays and actual_kind == "M"):
                target_category = "special_relays"
            elif actual_kind == "SD" or (special_registers and actual_kind == "D"):
                target_category = "special_registers"
            else:
                target_category = actual_kind
            clean_io.setdefault(target_category, {})[address] = label

    if metadata:
        current_metadata = hardware.get("analysis_metadata")
        if not isinstance(current_metadata, dict):
            if current_metadata not in (None, "", []):
                metadata = {"reported_value": current_metadata, **metadata}
            hardware["analysis_metadata"] = {}
        hardware["analysis_metadata"].update(metadata)
    if unmapped:
        current_unmapped = hardware.get("unmapped_suggested_io")
        if not isinstance(current_unmapped, list):
            current_unmapped = [] if current_unmapped in (None, "", {}) else [current_unmapped]
            hardware["unmapped_suggested_io"] = current_unmapped
        current_unmapped.extend(unmapped)

    normalized["suggested_io"] = clean_io
    if hardware:
        normalized["hardware_config"] = hardware
    else:
        normalized.pop("hardware_config", None)
    normalized["assumptions"] = assumptions
    normalized["format_diagnostics"] = diagnostics
    normalized["plc_model"] = plc_model
    from plc_semantics import (
        infer_semantic_requirements,
        normalize_semantic_requirements,
    )

    # Execution semantics are validation constraints, so hidden model output
    # must not be allowed to invent them.  Only deterministic evidence from the
    # user's own request is authoritative here.  A previously confirmed value
    # is preserved later by ``build_review_draft`` when this list is empty.
    inferred_semantics = infer_semantic_requirements(
        user_text,
        source="current_request",
    )
    normalized["execution_semantics"] = normalize_semantic_requirements(
        inferred_semantics
    )
    return ensure_hardware_questions(normalized, plc_model, user_text)


def _parse_analysis_response(raw, plc_model="FX3U", user_text=""):
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0]
    result = json.loads(text.strip())
    return _normalize_analysis_result(result, plc_model, user_text)


def _request_analysis_response(messages, *, on_format_repair=None, **kwargs):
    """Allow one syntax correction of an unconfirmed analysis draft only.

    Keep the shared collector strict. Language/field rejection, transport
    errors and valid JSON with the wrong root type are not repair signals.
    Neither attempt publishes content until its normal acceptance succeeds.
    """
    with provider_scope():
        try:
            return _request_model(messages, response_contract=ANALYSIS_RESPONSE, **kwargs)
        except ResponseRejectedError as rejected:
            if [(v.path, v.reason) for v in rejected.violations] != [
                ("content", "invalid_json_object")
            ]:
                raise
            raw = rejected.raw_response.message.content.strip()
            if raw.startswith("```") and raw.endswith("```") and "\n" in raw:
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            if not raw.startswith("{"):
                raise
            try:
                json.loads(raw)
            except json.JSONDecodeError as syntax_error:
                location = f"line {syntax_error.lineno}, column {syntax_error.colno}"
            else:
                raise rejected
            if on_format_repair is not None:
                on_format_repair()
            correction = (
                "Your previous analysis draft is not valid JSON (" + location + "). "
                "Return the complete corrected JSON object only, using the analysis schema above. "
                "Correct JSON syntax and missing schema keys only; preserve the requirement, "
                "devices, alternatives and questions. Do not invent confirmed answers or generate PLC code. "
                "Each flowchart_steps item has separate type and label keys, for example "
                '{"type":"transition","label":"X0"}. No markdown or explanations.'
            )
            repair_messages = [*messages, rejected.raw_response.message,
                               UserMessage(correction)]
            try:
                repaired = _request_model(repair_messages, response_contract=ANALYSIS_RESPONSE, **kwargs)
            except ResponseRejectedError as error:
                error.raw_attempts = (*rejected.raw_attempts, *error.raw_attempts)
                raise
            return replace(repaired, raw_attempts=(*rejected.raw_attempts, *repaired.raw_attempts))


ANALYSIS_SYSTEM_PROMPT = """# Role
你是 PLC 需求分析助手。只分析需求，不生成梯形图 JSON 或 ST 代码。

# Priority
Priority during pre-generation analysis is: output JSON shape > explicit changes
in the current user message > the previous confirmed specification used as a
baseline > selected PLC model profile and routed task knowledge > history.
If the user explicitly changes an address, parameter, or option in the current
turn, reflect that change and do not restore older cached values.

# 变频器控制方案确认
- 数字多段速端子、模拟量给定、RS-485/Modbus、高速脉冲/频率给定是四种不同方案，会产生不同的梯形图结构、扩展模块和 I/O 分配，不能当作 PLC 铭牌参数静默删除。
- 用户没有明确给定方式时，必须在 missing_info 中询问“变频器频率给定控制方式”，`id` 为 `control_method`、`required` 为 true。固定少量频率档位（例如 20/50/60Hz）可以把“普通 Y 输出组合控制 STF/RH/RM/RL，由变频器参数保存频率”列为推荐选项，但仍需用户确认。
- 变频器型号与端子/通信映射按实现依赖提问：Modbus 寄存器、站号或型号专用功能依赖具体变频器时询问 `drive_model`；PLC 输出与 STF/RH/RM/RL、模拟量通道或通信寄存器的对应关系不明确时询问 `wiring_mapping`。可以使用 `required_when` 表达条件必填，不得因它们位于 PLC 外部而过滤。
- 连续无级调速才比较模拟量与通信；只有驱动明确支持脉冲频率给定时才比较高速脉冲方案。
- FX3U-4DA 与 FX3U-4DA-ADP 是不同硬件、访问方式不可混用。禁止臆造 D8260、缓冲起始 D 地址或任何未由所选型号资料提供的地址。
- RD3A/WR3A 只用于 FX0N-3A 与对应的 FX2N-2AD/2DA，不适用于 FX3U-4AD-ADP/FX3U-4DA-ADP；后者按连接顺序和通道使用手册分配的 D8260-D8299 专用软元件。
- “变频器点动”是普通端子控制，不等于伺服 JOG/定位。

# 伺服/步进运动控制确认
- “步进电机/步进驱动器”属于运动控制；“步进状态机/步骤/阶段/顺序”属于流程控制。仅当需求同时包含两者时才同时使用两套规则。
- 伺服/步进驱动器控制方式、脉冲轴、方向输出、回原点输入和当前方案实际采用的定位模块会改变程序结构或 I/O，不能按 PLC 铭牌资料删除。
- `required_when.parameter` 可以填写控制参数的稳定 `id` 或完整问题文本；可使用 `equals`、`contains_any`、`not_contains`。从属项必须只在控制选项成立时阻止确认。
- PLSY/DPLSY：仅询问脉冲输出轴、频率、脉冲数或连续输出；不要追加相对/绝对或回原点问题。
- DRVI/DRVA：询问相对/绝对、目标脉冲数/位置、速度、脉冲输出轴和方向输出。方向输出是单独确认的合法 Y 点，不得固定推导为 Y0→Y4、Y1→Y5、Y2→Y6。
- ZRN：仅当 `homing_required` 选择需要时，条件询问 `homing_method`、回零速度、爬行速度、DOG 输入和脉冲输出轴。
- DSZR：仅当选择 DOG 搜索回零时，条件询问 DOG 输入、零相信号输入、脉冲输出轴和方向输出。
- `positioning_module_model` 只在 `positioning_implementation` 选择 FX3U-2HSY-ADP、FX3U-1PG、FX2N-10PG 等外接方案时条件必填；使用基本单元内置脉冲输出时不得询问通用模块清单。
- FX3U-2HSY-ADP 使用 Y2/Y3 高速轴时，`positioning_module_quantity` 条件必填且必须确认 2 块；Y0/Y1 高速轴只需 1 块。
- M8336 是 DVIT 中断输入指定功能有效，不是 ZRN/DSZR 完成标志。M8029 必须与对应指令关联；需要确认机械停止时使用驱动器定位完成输入。

# 方案设计（检索知识后再给用户选）

在输出 `approaches` 前，先结合当前需求、PLC 型号以及 `Retrieved PLC knowledge` 中检索到的设计知识，在内部搜索适用的实现架构，再筛选 1~3 个候选。

- 候选必须在状态/顺序组织方式、核心数据模型或核心指令族上存在本质差异。
- 仅更换软元件编号、定时器编号、梯级顺序、触点排布，或增加一个只复制同一条件的中间继电器，不算新的架构方案。
- 不得为了凑足数量制造重复方案；设计空间很窄时允许只给 1 个。
- 不要因为 system prompt 中出现过某个实现方式就强制采用它；具体架构的适用条件、优缺点和实现事实以当前需求与检索知识为依据。
- 每个实际候选必须包含 `generation_guide` 和可机器校验的 `generation_contract`，不同候选的 contract 应能体现其架构级差异。

# 输出要求
control_type 从 启停、顺序、定位、计数、模拟量、通讯、PID 中选择 1–3 个字符串。
下面 JSON 仅展示顶层字段形状，`approaches` 故意留空以避免把某种实现写成默认答案；实际回复必须根据当前需求与检索知识填写 1~3 个候选。每个 approach 必须包含 `approach_id`、`name`、`description`、`pros`、`cons`、`generation_guide`、`generation_contract`。
返回纯JSON（不要```json包裹），格式：
{
  "summary": "一句话总结",
  "control_type": ["启停"],
  "approaches": [],
  "missing_info": [],
  "suggested_io": {},
  "hardware_config": {},
  "assumptions": [],
  "format_diagnostics": [],
  "execution_semantics": [],
  "flowchart_steps": [
    {"type":"step","label":"初始状态"}
  ]
}
# suggested_io 硬约束
- 只允许普通类别 X、Y、M、D、T、C、S，以及 special_relays、special_registers；FX5U 的 SM 地址归入 special_relays，SD 地址归入 special_registers。
- 普通类别 X/Y/M/D/T/C/S 必须使用 JSON 对象：键为真实软元件地址，值为基于当前需求的简短非空用途说明；不得只返回地址数组。
- 普通类别中的每个地址都必须有非空说明。若用途无法从当前需求确定，就不要把该地址写入 suggested_io，而应在 assumptions 或 missing_info 中表达不确定性。
- special_relays / special_registers 可以使用地址数组或“地址到说明”的对象；系统软元件的固定说明允许由程序补全。
- 类别中的键必须是该 PLC 型号下真实、语法合法且前缀一致的软元件地址。
- CHANNEL、ADDRESS、NOTE、ANALOG_OUTPUT、模块名、通道、量程、接线和频率档位都不是 I/O 类别或地址；必须放入 hardware_config 或 assumptions。
- 不确定的地址不得写入 suggested_io。把不确定性写入 assumptions；不要因此生成硬件必填项。

# generation_guide 编写要求
- generation_guide 写简要的生成要点（如"使用BLOCK_INPUT状态机"、"每个通道独立梯级"）
- 一两句话即可，不需要语气强调

# generation_contract 硬约束
- 每个方案必须包含非空且可机器校验的 generation_contract；它会在用户选择后成为硬校验条件，不是建议。
- 每个方案只能描述一种明确实现，不得在同一方案中写“SET/RST 或 MOV”“PLSY 或 DRVI”等替代选项；替代实现必须拆成不同方案。
- required_opcodes/forbidden_opcodes 填最终程序必须出现/不得出现的真实降级指令名；required_devices/forbidden_devices 只填该方案固定要求的软元件。契约中的 OUT 由生成 JSON 的 COIL/TIMER/COUNTER 满足，绝不能要求生成 APP_INSTR OUT。
- 结构名只允许：direct_logic、register_state_machine、bit_state_machine、state_initialization、state_comparison、state_transition、self_hold、set_reset_latch、hardware_counter、data_register_counter、edge_trigger、pulse_positioning、analog_control、serial_communication、pid_control、vfd_multi_speed。
- required_structures/forbidden_structures 必须体现方案之间的本质差异。例如硬件计数器法要求 hardware_counter，INC 数据计数法要求 data_register_counter，寄存器步进法要求 register_state_machine。
- any_of_opcode_groups/any_of_structure_groups 仅用于同一方法内部真正等价的兼容指令，不得用来合并本应独立展示的不同方案。
- 所有方案的 contract 必须互相可区分；不要只更换名称而给出相同约束。

# execution_semantics 规则
- 仅使用 LEVEL、RISING_EDGE、FALLING_EDGE、FIRST_SCAN、CYCLIC、INTERRUPT。
- 用户说“每次按下一次/触发一次”时记录 RISING_EDGE；“松开/断开瞬间”记录 FALLING_EDGE；“上电/进入RUN后初始化一次”记录 FIRST_SCAN。
- 用户说固定周期执行时记录 CYCLIC，并在明确给出周期时填写 period_ms；明确要求中断任务时记录 INTERRUPT。普通持续条件为 LEVEL。
- 这是已确认的执行语义，不是 PLC 铭牌参数，不得放进 missing_info。

# flowchart_steps 规则
- 从初始状态开始，步骤和转移条件交替（step → transition → step → ...）
- 第一个元素必须为 step，最后一个元素必须为 step
- 简单流程：step 和 transition 交替
- **并行分支**：插入 `{"type":"fork","label":"分两路"}` 开始分支，之后每条分支的块加 `"branch":0`、`"branch":1` 等区分，最后 `{"type":"join","label":"汇合"}` 合并
- 每个节点必须使用独立的 type 和 label 键：`{"type":"transition","label":"X0启动"}`。
- 示例：[{"type":"step","label":"初始化"},{"type":"transition","label":"X0启动"},{"type":"fork","label":"双通道"},{"type":"step","label":"通道0动作","branch":0},{"type":"transition","label":"T0到","branch":0},{"type":"step","label":"通道1动作","branch":1},{"type":"transition","label":"T1到","branch":1},{"type":"join","label":"汇合"},{"type":"step","label":"完成"}]
- label 简洁：动作类"Y0 ON T0延时"，条件类"X0触发"或"T0延时到"

# 缺失信息提问原则
每个 missing_info 项必须提供稳定 id、question、required 和 options 字符串数组。存在可选范围时提供具体候选，同时允许用户自定义；不得只返回问题和空白框。地址问题只推荐已知的可用点位，不擅自填入未知接线事实；若常开/常闭尚未确认，应分别列出候选或单独提问，不设置极性默认。default 仅是建议，不能当作用户已确认的回答；自由文本且确无可枚举候选时才允许 options 为空。
0. 以下 PLC 自身的通用铭牌/配置资料不属于必填项，不得放入 missing_info：PLC CPU 完整型号、基本单元输出类型、固件/硬件版本、当前已安装扩展模块/适配器的完整清单。用户未提供时记录为 assumptions 并继续，不得阻止生成。
0.1 上述删除范围不包括会改变当前实现的外部或方案参数：变频器、伺服/步进驱动器控制方式，端子/通信映射，以及已经选用的定位模块/高速输出适配器型号。按实现依赖使用稳定 `id`、`required`、`required_when` 表达，不得因为它们位于 PLC 外部或名称中含“模块”而过滤。
1. 运动控制按指令族提问：PLSY/DPLSY 只问轴、频率和脉冲数/连续；DRVI/DRVA 才问相对/绝对、目标、速度、脉冲轴和方向输出；ZRN/DSZR 的回原点细节仅在用户选择需要回原点时条件提问。
2. 不得把所有运动参数列为统一必填项；从属问题的 `required_when` 必须引用控制问题的稳定 id 或完整问题文本。
3. 传感器检测必问：传感器接哪个X？
4. 用户明确说"先A后B再C"→必问是否需步进状态机
5. 多泵轮换必问：首次请求从几号泵开始、系统停止是否重置轮换指针、故障恢复是自动重新投入还是独立按钮手动复位、极低压是否立即追加备用泵
6. 用户明确写明常开/常闭时，分析摘要和生成阶段必须保持相同触点类型，不得擅自反转
7. 不要问太琐碎的问题（如"T0还是T1"），软元件编号由后续生成阶段自动分配"""


@language_scoped
def analyze_requirement(
    user_requirement: str,
    conversation_history=None,
    confirmed_context=None,
    confirmed_spec=None,
    task_type=None,
    image_attachments=None,
    on_format_repair=None,
) -> dict:
    """
    Phase 1: fast analysis with effort=low.
    Auto-detect PLC model from user input and inject special soft-element table.
    Returns: dict or None
    """
    confirmed_context = confirmed_spec if confirmed_spec is not None else confirmed_context
    model = _resolve_plc_model(user_requirement, confirmed_context)
    routing_requirement = _routing_text_with_selected_approach(
        user_requirement,
        confirmed_context,
    )
    workflow_prompt, _route = build_workflow_prompt(
        routing_requirement,
        target_mode="ladder",
        forced_task=task_type or "analysis",
    )
    knowledge_ctx = _build_knowledge_context(
        user_requirement,
        plc_model=model,
        task_type="analysis",
        confirmed_context=confirmed_context,
    )
    model_ctx = _build_model_context(
        model,
        confirmed_context,
        compact=bool(knowledge_ctx),
    )
    sys_prompt = _with_confirmed_context(
        ANALYSIS_SYSTEM_PROMPT + model_ctx + workflow_prompt + knowledge_ctx,
        confirmed_context,
    )
    print(f"阶段1: 需求分析中... (effort=low, PLC={model})")

    messages = _build_clean_messages(conversation_history or [], sys_prompt)
    messages.append(
        _user_message_with_images(user_requirement, image_attachments)
    )

    try:
        response = _request_analysis_response(
            messages,
            effort="low",
            stream=False,
            on_format_repair=on_format_repair,
        )
        raw = response.message.content.strip()

        # 清理 Markdown 包裹
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("\n", 1)[0]
        raw = raw.strip()

        result = _parse_analysis_response(raw, model, user_requirement)
        print(f"阶段1 分析完成: {result.get('summary', '')[:80]}...")
        return result

    except ResponseRejectedError:
        raise
    except Exception as e:
        print(f"阶段1 分析失败: {e}")
        return None


@language_scoped
def analyze_requirement_streaming(
    user_requirement: str,
    on_reasoning_chunk=None,
    on_content_chunk=None,
    conversation_history=None,
    confirmed_context=None,
    confirmed_spec=None,
    task_type=None,
    image_attachments=None,
    on_format_repair=None,
):
    """
    阶段1 流式版：分析用户需求，实时显示思考过程。

    返回:
      dict: 分析结果 JSON，失败时返回 None
    """
    confirmed_context = confirmed_spec if confirmed_spec is not None else confirmed_context
    model = _resolve_plc_model(user_requirement, confirmed_context)
    routing_requirement = _routing_text_with_selected_approach(
        user_requirement,
        confirmed_context,
    )
    workflow_prompt, _route = build_workflow_prompt(
        routing_requirement,
        target_mode="ladder",
        forced_task=task_type or "analysis",
    )
    knowledge_ctx = _build_knowledge_context(
        user_requirement,
        plc_model=model,
        task_type="analysis",
        confirmed_context=confirmed_context,
    )
    model_ctx = _build_model_context(
        model,
        confirmed_context,
        compact=bool(knowledge_ctx),
    )
    sys_prompt = _with_confirmed_context(
        ANALYSIS_SYSTEM_PROMPT + model_ctx + workflow_prompt + knowledge_ctx,
        confirmed_context,
    )
    print(f"阶段1(流式): 需求分析中... (effort=low, PLC={model})")

    messages = _build_clean_messages(conversation_history or [], sys_prompt)
    messages.append(
        _user_message_with_images(user_requirement, image_attachments)
    )

    try:
        response = _request_analysis_response(
            messages,
            effort="low",
            stream=True,
            on_format_repair=on_format_repair,
            on_reasoning_chunk=on_reasoning_chunk,
            on_content_chunk=on_content_chunk,
        )
        full_content = response.message.content

        # 解析返回的 JSON
        raw = full_content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("\n", 1)[0]
        raw = raw.strip()

        result = _parse_analysis_response(raw, model, user_requirement)
        print(f"阶段1 分析完成: {result.get('summary', '')[:80]}...")
        return result

    except ResponseRejectedError:
        raise
    except Exception as e:
        print(f"阶段1 流式分析失败: {e}")
        return None


HISTORY_FILE = "chat_history.json"
CONFIRMED_CONTEXT_FILE = "confirmed_requirements.json"


def save_confirmed_context(context):
    """Persist the latest user-confirmed specification for the current session."""
    payload = {"context": str(context or "").strip()}
    with open(CONFIRMED_CONTEXT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _load_confirmed_context():
    if not os.path.exists(CONFIRMED_CONTEXT_FILE):
        return ""
    try:
        with open(CONFIRMED_CONTEXT_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return str(payload.get("context", "")).strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        return ""


def _load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("已重置为全新对话。")
            return []
    return []

def _save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def _build_clean_messages(conversation_history, system_prompt):
    """构建发送给模型的消息列表，仅保留用户可见正文。"""
    messages = [{"role": "system", "content": system_prompt}]
    for msg in conversation_history:
        role = msg.get("role")
        if role not in {"user", "assistant"}:
            continue
        clean = {"role": role, "content": str(msg.get("content", ""))}
        messages.append(clean)
    return messages


DEBUG_REPORT_SYSTEM_PROMPT = """
# PLC Debug Report Mode
You are debugging an existing Mitsubishi ladder JSON program. Follow the
selected PLC model profile supplied in the request; never assume FX3U rules
for an FX5U project.
Do not generate a replacement ladder JSON. Do not enter requirement confirmation.
Use the provided current ladder JSON as read-only evidence.

Return pure JSON only, with this exact shape:
{
  "summary": "short summary in the application response language",
  "possible_causes": ["cause 1", "cause 2"],
  "related_rungs": [1, 2],
  "recommended_changes": ["change 1", "change 2"],
  "needs_fix": true,
  "fix_instruction": "one concise instruction that can be used to generate a partial fix"
}

Rules:
- Priority is: report JSON shape > current confirmed specification and
  canonical I/O > routed task knowledge > current user question >
  conversation history.
- Mention rung_id values when a cause can be tied to a rung.
- If evidence is insufficient, say what to inspect online.
- Pay special attention to output ownership, reset priority, state transitions,
  timer reset order, duplicate writers, M8029 placement, and FX3U 32-bit
  register-pair rules.
- Multiple SET/RST instructions for one address are normal held-bit logic and
  are not duplicate coils. Only report a conflict when COIL is mixed with
  SET/RST or when concrete scan-order evidence proves contradictory ownership.
- A T/C/D/M value may be written by the PLC and consumed only by HMI/SCADA or
  another task. Absence of a ladder read or RST is not a defect unless the
  confirmed requirement explicitly assigns that responsibility to this program.
- Set needs_fix=false when the report is only an explanation or the program
  appears acceptable.
"""


def _clean_json_response(raw):
    raw = str(raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
    if raw.endswith("```"):
        raw = raw.rsplit("\n", 1)[0]
    return raw.strip()

def _prepare_api_call(
    user_requirement,
    model_name,
    effort,
    target_mode,
    is_edit_mode=False,
    conversation_history=None,
    confirmed_context=None,
    confirmed_spec=None,
    persist_history=None,
    task_type=None,
    review_mode=None,
    current_version_json=None,
    plc_model=None,
    image_attachments=None,
):
    """
    共用准备逻辑：加载历史、追加用户消息、保存、选取系统提示词、
    构建清洗后的统一消息列表。
    返回 (messages, conversation_history, persist_history)
    """
    if conversation_history is None:
        conversation_history = []
    else:
        conversation_history = [dict(item) for item in conversation_history]
    if persist_history is None:
        persist_history = False
    if confirmed_spec is not None:
        confirmed_context = confirmed_spec
    conversation_history.append({"role": "user", "content": user_requirement})
    if persist_history:
        _save_history(conversation_history)

    selected_model = _resolve_plc_model(
        user_requirement,
        confirmed_context,
        explicit_model=plc_model,
    )
    system_prompt = build_generation_instructions(
        user_requirement,
        plc_model=selected_model,
        target_mode=target_mode,
        is_edit_mode=is_edit_mode,
        task_type=task_type,
        review_mode=review_mode,
        confirmed_context=confirmed_context,
        current_version_json=current_version_json,
        # Keep API extension/test hooks while sharing the actual assembly.
        prompt_builder=_select_system_prompt,
        knowledge_builder=_build_knowledge_context,
        profile_builder=_build_model_context,
        confirmed_builder=_with_confirmed_context,
    )
    messages_to_send = _build_clean_messages(conversation_history, system_prompt)
    if image_attachments:
        messages_to_send[-1] = _user_message_with_images(
            user_requirement,
            image_attachments,
        )

    return messages_to_send, conversation_history, persist_history


@language_scoped
def debug_ladder(
    user_question,
    current_version_json,
    confirmed_spec=None,
    conversation_history=None,
    local_findings=None,
    model_name=None,
    effort="high",
    request_timeout=120,
    raise_errors=False,
    plc_model="FX3U",
    debug_context=None,
):
    """Analyze the current ladder JSON and return a structured debug report."""
    config = load_full_config()
    model_name = model_name or _active_model_name(config)
    workflow_prompt, _route = build_workflow_prompt(
        user_question,
        target_mode="ladder",
        forced_task="debug",
    )
    knowledge_ctx = _build_knowledge_context(
        user_question,
        plc_model=plc_model,
        task_type="debug",
        confirmed_context=confirmed_spec,
        evidence={
            "debug_context": debug_context or {},
            "local_findings": local_findings or [],
            "current_ladder_json": current_version_json,
        },
    )
    system_prompt = _with_confirmed_context(
        DEBUG_REPORT_SYSTEM_PROMPT
        + f"\nSelected PLC model: {plc_model}\n"
        + _build_model_context(
            plc_model,
            confirmed_spec,
            compact=bool(knowledge_ctx),
        )
        + workflow_prompt
        + knowledge_ctx,
        confirmed_spec,
    )
    local_findings = local_findings or []
    debug_payload = {
        "user_question": user_question,
        "debug_context": debug_context or {},
        "plc_model": plc_model,
        "local_findings": local_findings,
        "current_ladder_json": current_version_json,
    }
    messages = _build_clean_messages(conversation_history or [], system_prompt)
    messages.append(
        {
            "role": "user",
            "content": json.dumps(debug_payload, ensure_ascii=False, indent=2),
        }
    )

    try:
        response = _request_model(
            messages,
            model_name=model_name,
            effort=effort,
            stream=False,
            response_contract=DEBUG_RESPONSE,
            request_timeout=request_timeout,
            max_retries=0 if request_timeout is not None else None,
        )
        raw = _clean_json_response(response.message.content)
        report = json.loads(raw)
    except ResponseRejectedError:
        raise
    except Exception as error:
        print(f"debug api request failed: {error}")
        if raise_errors:
            raise RuntimeError(f"Debug API request failed: {error}") from error
        return None
    return _normalize_debug_report(report)


def _normalize_debug_report(report):
    if not isinstance(report, dict):
        report = {}
    return {
        "summary": str(report.get("summary", "")).strip() or str(tr("调试分析完成")),
        "possible_causes": _string_list(report.get("possible_causes")),
        "related_rungs": _int_list(report.get("related_rungs")),
        "recommended_changes": _string_list(report.get("recommended_changes")),
        "needs_fix": _strict_bool(report.get("needs_fix", False)),
        "fix_instruction": str(report.get("fix_instruction", "")).strip(),
    }


DEBUG_EVIDENCE_DIAGNOSIS_SYSTEM_PROMPT = """
# FX3U simulator-evidence diagnosis
You receive one immutable, version-bound failure evidence object. It contains
only failed assertions/invariants, nearby device traces, reverse dependency
paths, related PLC IR networks, deterministic findings, and retrieved manual
or debugging-case blocks.

Return pure JSON only:
{
  "schema_version": 1,
  "root_cause": "concise root cause in the application response language",
  "confidence": 0.0,
  "affected_networks": ["N0001"],
  "evidence_refs": ["assertion:test:step:Y0", "network:N0001"],
  "recommended_change": "precise local change"
}

Rules:
- Use only related_networks and allowed_evidence_refs from the payload.
- Diagnose the observed failure, not unrelated style or safety improvements.
- Environment unavailable/error is never a program diagnosis; such runs are
  filtered before this prompt.
- Do not output ladder JSON or a patch in this response.
- If evidence is ambiguous, lower confidence but still identify the best
  evidence-bound hypothesis. Never invent a device, network or field value.
"""


DEBUG_EVIDENCE_PATCH_SYSTEM_PROMPT = """
# FX3U simulator-evidence local patch
You receive validated failure evidence and a validated diagnosis. Return one
strictly local network patch. The caller will reject every field outside the
allowed boundary and will run deterministic validation and full regression.

Return pure JSON only:
{
  "schema_version": 1,
  "base_revision": 1,
  "base_ir_sha256": "64 lowercase hex characters copied from evidence.binding",
  "target_revision": 2,
  "operations": [
    {
      "operation": "modify_network",
      "network": "N0001",
      "ladder": {"complete replacement ladder object for that same rung_id": true}
    }
  ],
  "device_comments": {}
}

Rules:
- Only operation=modify_network is allowed. Do not add, delete or renumber a network.
- Only diagnosis.affected_networks may be modified.
- Preserve each replacement ladder.rung_id exactly.
- Do not change unrelated behavior or introduce an address outside
  evidence.allowed_patch_devices.
- Do not add new outputs, timers, counters, state registers or safety features.
- Copy base_revision/base_ir_sha256 exactly; target_revision is base + 1.
"""


@language_scoped
def _call_debug_evidence_json(
    system_prompt,
    payload,
    *,
    model_name=None,
    effort="high",
    request_timeout=120,
    raise_errors=False,
    on_reasoning_chunk=None,
    on_content_chunk=None,
    on_progress=None,
    response_contract=INSPECTION_RESPONSE,
):
    config = load_full_config()
    selected_model = model_name or _active_model_name(config)
    messages = _build_clean_messages([], system_prompt)
    messages.append(
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, indent=2),
        }
    )
    try:
        wants_stream = any(
            callback is not None
            for callback in (on_reasoning_chunk, on_content_chunk, on_progress)
        )
        if wants_stream and on_progress:
            on_progress(str(tr("AI 正在生成仿真测试方案（流式）")))
        response = _request_model(
            messages,
            model_name=selected_model,
            effort=effort,
            stream=wants_stream,
            response_contract=response_contract,
            preserved_annotations=source_annotations(payload),
            request_timeout=request_timeout,
            max_retries=0 if request_timeout is not None else None,
            on_reasoning_chunk=on_reasoning_chunk,
            on_content_chunk=on_content_chunk,
            fallback_to_non_stream=wants_stream,
            on_fallback=(
                (lambda error: on_progress(
                    str(tr("流式显示不可用，正在切换普通模式：{error}", error=error))
                ))
                if on_progress
                else None
            ),
        )
        raw_content = response.message.content
        if not raw_content.strip():
            raise RuntimeError("模型响应没有返回正文")

        if on_progress:
            on_progress(str(tr("正在解析模型输出：清理并校验 JSON 结构")))
        parsed = json.loads(_clean_json_response(raw_content))
        if not isinstance(parsed, dict):
            raise ValueError("response must be a JSON object")
        return parsed
    except ResponseRejectedError:
        raise
    except Exception as error:
        if raise_errors:
            raise RuntimeError(f"Debug evidence API request failed: {error}") from error
        return None


@language_scoped
def debug_evidence_diagnosis(
    evidence,
    *,
    model_name=None,
    effort="high",
    request_timeout=120,
    raise_errors=False,
):
    """Ask the model for a diagnosis; deterministic validation happens later."""

    return _call_debug_evidence_json(
        DEBUG_EVIDENCE_DIAGNOSIS_SYSTEM_PROMPT,
        {"evidence": evidence},
        response_contract=DIAGNOSIS_RESPONSE,
        model_name=model_name,
        effort=effort,
        request_timeout=request_timeout,
        raise_errors=raise_errors,
    )


@language_scoped
def debug_evidence_patch(
    evidence,
    diagnosis,
    *,
    model_name=None,
    effort="high",
    request_timeout=120,
    raise_errors=False,
):
    """Ask the model for a local patch; deterministic validation happens later."""

    return _call_debug_evidence_json(
        DEBUG_EVIDENCE_PATCH_SYSTEM_PROMPT,
        {"evidence": evidence, "diagnosis": diagnosis},
        response_contract=PATCH_RESPONSE,
        model_name=model_name,
        effort=effort,
        request_timeout=request_timeout,
        raise_errors=raise_errors,
    )


SIMULATOR_TEST_SUITE_SYSTEM_PROMPT = """
# FX3U GX Simulator2 test-suite planner
You receive a version-bound PLC IR test context. Propose executable tests; do
not operate GX Works2, GX Simulator2, a mouse, a keyboard, files, or devices.
Return pure JSON only in this shape:
{
  "schema_version": 1,
  "name": "regression suite name",
  "plc_model": "FX3U",
  "tests": [
    {
      "schema_version": 1,
      "name": "unique test name",
      "description": "what this proves",
      "initial": {"X0": 0},
      "steps": [
        {"id": "start", "at_ms": 100, "set": {"X0": 1}},
        {"id": "verify", "at_ms": 150, "expect": {"Y0": 1}}
      ],
      "invariants": [],
      "fault_injections": [],
      "trace_devices": ["X0", "Y0"],
      "sample_ms": 10,
      "timeout_ms": 2000
    }
  ]
}

Rules:
- Keep JSON keys and enum values exactly as shown in English. Use the application
  response language for every natural-language field and any visible planning
  summary; keep PLC addresses and instruction names unchanged.
- Use only addresses present in context.devices or context.io_map.
- Stimulus writes may target only declared X inputs or declared non-special
  M/D test inputs. Never write Y/T/C/S or M8xxx/D8xxx.
- Every stimulus/fault device must have an explicit initial value.
- Every test must contain at least one expect/wait_for or invariant.
- Every step id must be unique within its test, including repeated actions such
  as pressing or releasing the same button more than once.
- ``invariants`` is only for constraints that must hold continuously throughout
  a test. Point-in-time checks belong in ``steps[].expect``. For ordinary
  start/stop assertions, keep ``invariants`` as ``[]`` and do not duplicate a
  final expectation there. Never put ``{"at_ms": ..., "expect": ...}`` in
  ``invariants``. Valid invariant types are ``mutual_exclusion`` (devices),
  ``maximum_on_time``/``minimum_off_time`` (device, duration_ms),
  ``sequence_constraint`` (devices, optional allow_repeat), and
  ``state_constraint`` (device, allowed).
- Cover normal start/stop or sequence behavior actually represented by the IR.
- Add fault cases only when the program contains corresponding timeout/alarm or
  defined recovery behavior. Do not invent safety behavior or requirements.
- Timing must follow the IR semantics; one-shot inputs must include an OFF/ON
  transition, and timers must be allowed enough time to finish. After every
  rising-edge activation, explicitly write the input back to 0 before any
  later activation; after every falling-edge activation, write it back to 1.
  Never represent a repeated edge by writing the same bit value twice.
- Keep the initial proposal compact (normally 2-8 high-value tests).
"""


@language_scoped
def generate_simulator_test_suite(
    test_context,
    *,
    model_name=None,
    effort="high",
    request_timeout=120,
    raise_errors=False,
    on_reasoning_chunk=None,
    on_content_chunk=None,
    on_progress=None,
):
    """Generate Test DSL only; deterministic validation occurs in simulator.planning."""

    return _call_debug_evidence_json(
        SIMULATOR_TEST_SUITE_SYSTEM_PROMPT,
        {"context": test_context},
        response_contract=TEST_SUITE_RESPONSE,
        model_name=model_name,
        effort=effort,
        request_timeout=request_timeout,
        raise_errors=raise_errors,
        on_reasoning_chunk=on_reasoning_chunk,
        on_content_chunk=on_content_chunk,
        on_progress=on_progress,
    )


MULTI_AGENT_SPECIALIST_PROMPTS = {
    "reviewer": """
# PLC program Reviewer specialist
You receive one immutable, version-bound PLC IR review context. Return JSON
advice only. You cannot call tools, modify a program, import GX Works2, run a
simulator, write/force devices, or delegate to another agent.

Return pure JSON only:
{
  "binding": {"project_id":"", "version_id":"", "revision":1,
              "ir_sha256":"copy context.binding exactly"},
  "summary": "review summary in the application response language",
  "findings": [{
    "severity":"warning|info", "category":"stable_snake_case",
    "title":"short title", "message":"evidence-bound observation",
    "evidence":[{"rung_id":1,"json_path":"$.rungs[0]","address":"Y0"}],
    "recommendation":"specific engineering action",
    "fixable":false, "fix_instruction":"", "confidence":"high|medium|low"
  }],
  "online_checks": []
}

Rules:
- Copy context.binding exactly. Cite only existing rungs, paths and devices.
- Deterministic analysis is authoritative; do not turn style preferences into defects.
- Multiple SET/RST sites are normal unless concrete priority behavior contradicts
  a confirmed requirement. A T/C/D/M value may exist only for HMI/external use.
- Do not invent safety, reset, completion, motion or communications requirements.
- A selectable fix requires exact version evidence and a precise instruction;
  otherwise keep fixable=false. Never output replacement ladder/IR/CSV.
""",
    "timing_planner": """
# PLC Timing Planner specialist
Review only scan/event/timer/counter/state/motion timing semantics in one
immutable, version-bound PLC IR context. Return JSON advice only. You cannot
call tools, change code, import, simulate, operate devices, or delegate. You
cannot call tools through another agent either.

Return the same binding/summary/findings/online_checks JSON shape as Reviewer.
Rules:
- Copy context.binding exactly. Use only context.logic, context.timing,
  context.networks, confirmed requirements and deterministic findings.
- Distinguish LEVEL, RISING_EDGE, FALLING_EDGE, FIRST_SCAN, CYCLIC and INTERRUPT.
- Counter C is normally edge/pulse driven; do not apply timer enable rules to it.
- Do not invent scan time facts when timing coverage is unavailable. Express
  uncertain runtime behavior as an online_check, not a confirmed code defect.
- Cite an existing rung/path/device. Never output code, IR, CSV or a patch.
""",
}


@language_scoped
def run_multi_agent_specialist(
    role,
    payload,
    *,
    model_name=None,
    effort="high",
    request_timeout=120,
    raise_errors=False,
):
    """Call one advisory P9 specialist; its JSON is validated by Supervisor."""

    normalized_role = str(role or "").strip().lower()
    prompt = MULTI_AGENT_SPECIALIST_PROMPTS.get(normalized_role)
    if prompt is None:
        raise ValueError(f"Unsupported multi-agent specialist role: {role}")
    context = payload.get("context") if isinstance(payload, dict) else None
    if isinstance(context, dict):
        plc_model = str(
            ((context.get("plc") or {}).get("cpu")) or "FX3U"
        ).upper()
        retrieval_query = _build_knowledge_query(
            normalized_role,
            context.get("request"),
            context.get("networks"),
            context.get("logic"),
            context.get("timing"),
            context.get("deterministic_analysis"),
            context.get("local_report"),
        )
        prompt += _build_knowledge_context(
            retrieval_query,
            plc_model=plc_model,
            task_type="program_review",
            confirmed_context=context.get("confirmed_spec"),
        )
    return _call_debug_evidence_json(
        prompt,
        payload,
        response_contract=INSPECTION_RESPONSE,
        model_name=model_name,
        effort=effort,
        request_timeout=request_timeout,
        raise_errors=raise_errors,
    )


def _strict_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "是"}:
            return True
        if normalized in {"false", "0", "no", "否", ""}:
            return False
    return False


def _string_list(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _int_list(value):
    result = []
    if not isinstance(value, list):
        value = [value] if value is not None else []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


INSPECTION_SYSTEM_PROMPT = """
# PLC Inspection Candidate Mode
Analyze the supplied, read-only Mitsubishi ladder JSON. The selected PLC
model in the payload is authoritative. Do not generate replacement or partial
ladder JSON.

Return pure JSON only:
{
  "summary": "summary in the application response language",
  "findings": [
    {
      "finding_id": "reuse the local finding_id when this is the same issue",
      "severity": "error|warning|info",
      "category": "stable_snake_case_category",
      "title": "short title in the application response language",
      "message": "what is wrong or uncertain",
      "evidence": [
        {"rung_id": 1, "json_path": "$.rungs[0]", "address": "Y0"}
      ],
      "recommendation": "specific engineering action",
      "fixable": true,
      "fix_instruction": "precise code change, or empty when not safely fixable",
      "confidence": "high|medium|low"
    }
  ],
  "online_checks": [
    {
      "address": "Y0",
      "condition": "when to observe",
      "expected": "expected value",
      "reason": "why it discriminates between causes"
    }
  ],
  "followup_questions": ["only questions that materially improve evidence"]
}

Rules:
- Every code finding must cite an existing rung_id or JSON path. Do not invent
  addresses, rungs, online values, or safety requirements.
- Treat the local report as deterministic evidence and add semantic context;
  do not hide or contradict it without explicit evidence.
- Keep runtime/field checks separate from code changes.
- Multiple SET/RST instructions for one held Y/M address are valid and commonly
  distributed across rungs. Do not call them duplicate writers merely because
  there is more than one SET or RST location; require evidence of a COIL mix or
  a concrete scan-order/priority contradiction.
- Counter devices are normally pulse/edge driven. Do not apply the timer
  "enable must remain true" rule to C devices.
- T/C/D/M values may be produced for HMI, SCADA, communications or another task.
  A value that is not read again in this ladder, or a counter without an in-
  ladder reset, is not by itself a defect.
- A motion instruction may use BUSY/DONE, an external in-position sensor, a
  state owned elsewhere or fire-and-forget behavior. Missing M8029/SM8029 alone
  is not a finding; only report a completion strategy that contradicts confirmed
  requirements or is placed inconsistently with the selected model.
- Reuse a local finding's finding_id and category when adding evidence to the
  same issue. Do not restate it as a new finding.
- Set fixable=true only for evidence-bound code changes with a non-empty
  fix_instruction. Emergency stop, safety door, limit, overload, and other
  safety findings must use fixable=false and require engineering review.
- If evidence is insufficient, return online_checks/followup_questions rather
  than pretending that a cause is confirmed.
"""


@language_scoped
def inspect_ladder(
    report_type,
    request,
    current_version_json,
    local_report,
    *,
    plc_model="FX3U",
    confirmed_spec=None,
    conversation_history=None,
    model_name=None,
    effort="high",
    request_timeout=120,
    raise_errors=False,
):
    """Return an AI inspection candidate for later strict local normalization."""
    if report_type == "fault_debug":
        report_type = "debug"
    if report_type not in {"program_review", "debug"}:
        raise ValueError(f"Unsupported inspection report type: {report_type}")
    config = load_full_config()
    model_name = model_name or _active_model_name(config)
    request_text = (
        json.dumps(request, ensure_ascii=False)
        if isinstance(request, (dict, list))
        else str(request or "")
    )
    workflow_prompt, _route = build_workflow_prompt(
        f"{plc_model}\n{request_text}",
        target_mode="ladder",
        forced_task="debug" if report_type == "debug" else "program_review",
    )
    knowledge_ctx = _build_knowledge_context(
        request_text,
        plc_model=plc_model,
        task_type=report_type,
        confirmed_context=confirmed_spec,
        evidence={
            "local_report": local_report,
            "current_ladder_json": current_version_json,
        },
    )
    system_prompt = _with_confirmed_context(
        INSPECTION_SYSTEM_PROMPT
        + f"\nSelected PLC model: {plc_model}\n"
        + _build_model_context(
            plc_model,
            confirmed_spec,
            compact=bool(knowledge_ctx),
        )
        + f"Inspection type: {report_type}\n"
        + workflow_prompt
        + knowledge_ctx,
        confirmed_spec,
    )
    payload = {
        "report_type": report_type,
        "plc_model": plc_model,
        "request": request,
        "local_report": local_report,
        "current_ladder_json": current_version_json,
    }
    messages = _build_clean_messages(conversation_history or [], system_prompt)
    messages.append(
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, indent=2),
        }
    )
    try:
        response = _request_model(
            messages,
            model_name=model_name,
            effort=effort,
            stream=False,
            request_timeout=request_timeout,
            max_retries=0 if request_timeout is not None else None,
            response_contract=INSPECTION_RESPONSE,
        )
        raw = _clean_json_response(response.message.content)
        candidate = json.loads(raw)
        if not isinstance(candidate, dict):
            raise ValueError("inspection response must be a JSON object")
        return candidate
    except ResponseRejectedError:
        raise
    except Exception as error:
        print(f"inspection api request failed: {error}")
        if raise_errors:
            raise RuntimeError(f"Inspection API request failed: {error}") from error
        return None


def review_ladder(
    review_focus,
    current_version_json,
    local_report,
    **kwargs,
):
    return inspect_ladder(
        "program_review",
        {"review_focus": str(review_focus or "").strip()},
        current_version_json,
        local_report,
        **kwargs,
    )


PARTIAL_LADDER_REPAIR_SYSTEM_PROMPT = """# PLC ladder local structural repair
You repair only the rejected ladder locations supplied in the user payload.
Do not re-analyze the requirement, redesign the program, retrieve manuals, or
regenerate unrelated rungs. The backend owns the immutable full baseline and
will merge and validate your patch.

Return one pure JSON object only, exactly in partial-edit form:
{"mode":"partial","device_comments":{},"rungs":[],"delete_rung_ids":[]}

Rules:
- `mode` must be `partial` and `delete_rung_ids` must be empty.
- `rungs` may contain only complete replacement rungs whose rung_id is listed
  in `allowed_rung_ids`; never add, delete, renumber, or repeat another rung.
- Preserve control logic, addresses, operands, parameters and contact polarity
  except for the minimum structural/protocol correction explicitly requested.
- Use the supplied `baseline_subset` as the only program evidence.
- `device_comments` may contain only addresses listed in `allowed_addresses`.
- Do not output markdown, explanation, diagnostics, or a full ladder program.
"""

FORMAT_LADDER_REPAIR_SYSTEM_PROMPT = """# PLC ladder JSON format repair
Repair JSON syntax/protocol only. Do not re-analyze the PLC requirement, retrieve
manuals, redesign logic, or invent missing behavior. The user payload contains
the rejected raw candidate and the parser failure location.

Return one pure, complete top-level ladder JSON object only:
{"device_comments":{},"rungs":[]}

Rules:
- Preserve every recoverable address, opcode, operand, value, rung_id, branch,
  contact polarity, label and comment from the rejected candidate.
- Correct only JSON syntax, delimiters, container closure and protocol shape.
- Never emit `mode:"partial"` on this path.
- If the text ended early, close structures whose existing content is evident;
  do not synthesize unseen rungs or new PLC logic.
- Do not output markdown or explanation.
"""


@language_scoped
def repair_ladder_response(repair_payload, model_name, effort, *, mode,
                           on_reasoning_chunk=None, on_content_chunk=None):
    """One explicit repair request that bypasses normal generation context."""
    if mode not in {"partial", "format"}:
        raise ValueError("Unsupported ladder repair mode")
    if not isinstance(repair_payload, dict):
        raise TypeError("repair_payload must be an object")
    system_prompt = (PARTIAL_LADDER_REPAIR_SYSTEM_PROMPT
                     if mode == "partial" else FORMAT_LADDER_REPAIR_SYSTEM_PROMPT)
    audit_section("repair_system_prompt", system_prompt,
                  reason="explicit_local_repair" if mode == "partial" else "explicit_format_repair",
                  source="api")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(repair_payload, ensure_ascii=False, separators=(",", ":"))},
    ]
    response = _request_model(
        messages,
        model_name=model_name,
        effort=effort,
        stream=True,
        response_contract=LADDER_RESPONSE,
        preserved_annotations=source_annotations(repair_payload),
        on_reasoning_chunk=on_reasoning_chunk,
        on_content_chunk=on_content_chunk,
        fallback_to_non_stream=True,
    )
    return response.message.reasoning, response.message.content


@language_scoped
def stream_model_response(user_requirement, model_name, effort, target_mode,
                             on_reasoning_chunk=None, on_content_chunk=None,
                             is_edit_mode=False, conversation_history=None,
                             confirmed_context=None, persist_history=None,
                             task_type=None, review_mode=None,
                             confirmed_spec=None,
                             current_version_json=None,
                             plc_model=None,
                             image_attachments=None):
    """
    通过当前 ModelProvider 流式调用模型，实时返回工程推理摘要。

    参数:
        on_reasoning_chunk(token: str) — 收到推理内容片段时回调
        on_content_chunk(token: str)   — 收到输出内容片段时回调

    返回:
        (full_reasoning: str, full_content: str)
    """
    print(f"思考中(流式)... (当前模式: {effort}, 目标语言: {target_mode})")

    messages, conversation_history, should_persist = _prepare_api_call(
        user_requirement, model_name, effort, target_mode,
        is_edit_mode=is_edit_mode,
        conversation_history=conversation_history,
        confirmed_context=confirmed_context,
        confirmed_spec=confirmed_spec,
        persist_history=persist_history,
        task_type=task_type,
        review_mode=review_mode,
        current_version_json=current_version_json,
        plc_model=plc_model,
        image_attachments=image_attachments,
    )

    response = _request_model(
        messages,
        model_name=model_name,
        effort=effort,
        stream=True,
        response_contract=LADDER_RESPONSE if target_mode == "ladder" else ST_RESPONSE,
        preserved_annotations=source_annotations(current_version_json, confirmed_spec, confirmed_context),
        on_reasoning_chunk=on_reasoning_chunk,
        on_content_chunk=on_content_chunk,
    )
    full_reasoning = response.message.reasoning
    full_content = response.message.content

    # ---------- 保存历史（含思考过程） ----------
    conversation_history.append({
        "role": "assistant",
        "content": full_content,
        "reasoning": full_reasoning
    })
    if should_persist:
        _save_history(conversation_history)

    return full_reasoning, full_content


@language_scoped
def generate_model_json(user_requirement: str, model_name: str, effort: str,
                                   target_mode: str, is_edit_mode: bool = False,
                                   conversation_history=None,
                                   confirmed_context=None,
                                   confirmed_spec=None,
                                   persist_history=None,
                                   request_timeout=None,
                                   max_retries=None,
                                   raise_errors=False,
                                   task_type=None,
                                   review_mode=None,
                                   current_version_json=None,
                                   plc_model=None,
                                   image_attachments=None) -> str:
    print(f"思考中... (当前模式: {effort}, 目标语言: {target_mode})")

    messages, conversation_history, should_persist = _prepare_api_call(
        user_requirement, model_name, effort, target_mode,
        is_edit_mode=is_edit_mode,
        conversation_history=conversation_history,
        confirmed_context=confirmed_context,
        confirmed_spec=confirmed_spec,
        persist_history=persist_history,
        task_type=task_type,
        review_mode=review_mode,
        current_version_json=current_version_json,
        plc_model=plc_model,
        image_attachments=image_attachments,
    )

    try:
        response = _request_model(
            messages,
            model_name=model_name,
            effort=effort,
            stream=False,
            request_timeout=request_timeout,
            max_retries=max_retries,
            response_contract=LADDER_RESPONSE if target_mode == "ladder" else ST_RESPONSE,
            preserved_annotations=source_annotations(current_version_json, confirmed_spec, confirmed_context),
        )
        assistant_message = response.message

        conversation_history.append({
            "role": "assistant",
            "content": assistant_message.content
        })
        if should_persist:
            _save_history(conversation_history)

        raw_content = assistant_message.content.strip()
        if raw_content.startswith("```"):
            raw_content = raw_content.split("\n", 1)[1]
        if raw_content.endswith("```"):
            raw_content = raw_content.rsplit("\n", 1)[0]
        return raw_content.strip()

    except ResponseRejectedError:
        raise
    except Exception as e:
        print(f"api 接入失败: {e}")
        if raise_errors:
            raise RuntimeError(f"API request failed: {e}") from e
        return ""


def _deprecated_model_alias(name, target):
    """Keep one-release source compatibility without retaining old parsing."""

    def forward(*args, **kwargs):
        warnings.warn(
            f"api.{name} 已废弃；请改用 api.{target.__name__}。",
            DeprecationWarning,
            stacklevel=2,
        )
        return target(*args, **kwargs)

    forward.__name__ = name
    forward.__doc__ = f"Deprecated forwarding alias for {target.__name__}."
    forward.__deprecated__ = True
    return forward


# One-release source compatibility.  New application code uses only the
# vendor-neutral names; these wrappers are removed in the next release.
call_deepseek_analyze_requirement = _deprecated_model_alias(
    "call_deepseek_analyze_requirement", analyze_requirement
)
call_deepseek_analyze_streaming = _deprecated_model_alias(
    "call_deepseek_analyze_streaming", analyze_requirement_streaming
)
call_deepseek_debug_ladder = _deprecated_model_alias(
    "call_deepseek_debug_ladder", debug_ladder
)
call_deepseek_debug_evidence_diagnosis = _deprecated_model_alias(
    "call_deepseek_debug_evidence_diagnosis", debug_evidence_diagnosis
)
call_deepseek_debug_evidence_patch = _deprecated_model_alias(
    "call_deepseek_debug_evidence_patch", debug_evidence_patch
)
call_deepseek_generate_simulator_test_suite = _deprecated_model_alias(
    "call_deepseek_generate_simulator_test_suite", generate_simulator_test_suite
)
call_deepseek_multi_agent_specialist = _deprecated_model_alias(
    "call_deepseek_multi_agent_specialist", run_multi_agent_specialist
)
call_deepseek_inspection = _deprecated_model_alias(
    "call_deepseek_inspection", inspect_ladder
)
call_deepseek_review_ladder = _deprecated_model_alias(
    "call_deepseek_review_ladder", review_ladder
)
call_deepseek_streaming = _deprecated_model_alias(
    "call_deepseek_streaming", stream_model_response
)
call_deepseek_to_generate_json = _deprecated_model_alias(
    "call_deepseek_to_generate_json", generate_model_json
)

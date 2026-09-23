"""
模式库匹配引擎 — 关键词分类 + 动态 Prompt 组装
"""
from dataclasses import asdict, dataclass
import json
import re

from shared.paths import resource_path


# ============================
# 路径与缓存
# ============================

def _get_library_path():
    return str(resource_path("pattern_library.json"))


_library_cache = None


@dataclass
class KnowledgeRouterResult:
    task_type: str
    vendor: str
    confidence: float
    knowledge_bundles: list
    open_points: list
    warnings: list

    def to_dict(self):
        return asdict(self)


KNOWLEDGE_BUNDLES = {
    "task_router": """
## PLC workflow router
- Classify the user request before writing code: analysis, generate, edit, review, debug, explain, sfc, or io_mapping.
- Use the smallest relevant rule set. Do not mix vendor ecosystems unless the user explicitly asks for migration.
- If the request is incomplete, list open points instead of silently inventing safety-critical parameters.
- Never auto-add stop or emergency-stop inputs. Use stop/e-stop only when the
  user explicitly provides or confirms the device/address.
""",
    "knowledge_priority": """
## Knowledge priority
Priority is: hard JSON/schema/report shape > the current-turn explicit change
while preparing a new draft > the prior confirmed specification used as that
draft's baseline > routed task knowledge > conversation history. After the user
confirms the new draft, that confirmed specification and canonical I/O become
the only generation source.
- Current-turn user changes override older cached requirements and historical
  assignments during pre-generation analysis.
- Mark uncertain details as assumptions or open points instead of inventing
  safety-critical parameters.
""",
    "output_ownership": """
## Scan cycle and output ownership review
- Every important Y/M output should have one obvious owner.
- Avoid scattered writers for the same address. Combine ordinary COIL conditions into one parallel_block.
- Multiple SET and RST instructions for the same held bit are a normal way to
  collect independent conditions without duplicate COILs. Do not flag pure
  SET/RST writers merely because they are spread across rungs; inspect scan
  order and reset priority only when both conditions can be true together.
- When debugging a stuck output, trace: target address -> all writers -> owner logic -> inhibit/reset -> transition conditions.
""",
    "scan_semantics": """
## PLC execution semantics
- Distinguish LEVEL, RISING_EDGE, FALLING_EDGE, FIRST_SCAN, CYCLIC and
  INTERRUPT before generating ladder.
- "Each time/once when pressed or detected" is an event and must use an edge
  trigger, not a level contact that repeats every scan.
- Power-on/RUN initialization uses the selected model profile's first-scan
  relay, never its always-ON RUN relay.
- A fixed periodic request needs an explicit clock/timer cycle with an OFF/reset
  path. An interrupt request is not equivalent to an ordinary MAIN cyclic rung.
- For multi-stage sequences, keep state transitions and state outputs as
  separate logical regions even if GX Works2 later imports one full program.
""",
    "mitsubishi_fx3u": """
## Mitsubishi FX3U / GX Works2 rules
- Do not assume Siemens/Omron/IEC feature parity.
- M8029 is a shared completion flag and must be checked immediately with the corresponding application instruction.
- Use M8002 for one-scan power-on/RUN initialization; do not substitute the
  always-ON M8000 relay.
- For PLSY/DPLSY/PLSV/DRVI/DDRVI/DRVA/DDRVA/DVIT/ZRN/DSZR, put the motion instruction and M8029 completion logic in the same rung as sibling branches.
- Shared enable contacts belong in rung.shared_inputs. State comparison belongs in rung.header_element.
- FX3U 32-bit register pairs use low word + next high word. Do not use D8340/D8350/D8360 pairs as 16-bit math or ordinary COMPARE operands.
- For floating-point math from a 32-bit pair, convert to a separate D pair
  first, for example DFLT D8350 D200, then use DEADD/DESUB/DEMUL/DEDIV.
- FX3U SFTL/SFTLP source and destination bit ranges must not overlap; prefer a
  D state pointer for pump rotation.
- An ordinary T timer resets only after its enable path turns OFF. M8000 stays
  ON throughout RUN, so M8000 alone creates a power-on delay, never a periodic
  oscillator. For matching periods use M8011/M8012/M8013/M8014; otherwise use
  an explicit path that turns the timer enable OFF before the next cycle.
- JSON ``TIMER`` outputs use T addresses only. JSON ``COUNTER`` outputs use C
  addresses only; never encode a C counter as ``TIMER``.
- Do not emulate ALT with same-edge sibling branches ``NC Mx -> SET Mx`` and
  ``NO Mx -> RST Mx``. The later branch can observe the SET immediately and
  undo it in the same scan. Use two explicit timing/state phases instead.
""",
    "mitsubishi_fx5u": """
## Mitsubishi FX5U / iQ-F rules
- Use decimal X/Y addressing and the FX5U instruction/device profile supplied
  by the project. Do not silently apply FX3U-only register-pair rules.
- SM/SD are the native special relay/register prefixes. Compatible SM8000
  aliases may be used only when the selected model profile documents them.
- Validate positioning and completion handling against the FX5U profile rather
  than assuming GX Works2/FX3U behavior.
""",
    "vfd_control": """
## Variable-frequency drive command selection
- Apply this section only when the user request or confirmed project actually
  involves a VFD. A retrieved example or proposed question is not hardware evidence.
- Do not treat every request containing speed, frequency, or jog as PLC
  positioning. First identify how the drive accepts its frequency command.
- Discrete preset-speed terminals: PLC Y outputs switch drive terminals such as
  STF/RH/RM/RL. The Y points carry ON/OFF selection states; the drive stores the
  actual preset frequencies. This is normally the first option to discuss for a
  small fixed set such as 20/50/60 Hz.
- Analog reference: when module details are supplied, distinguish FX3U-4DA from
  FX3U-4DA-ADP because their access methods differ. If details are absent, keep
  module-specific addresses abstract until the analog method is selected, then
  ask for the implementation-dependent module/channel details conditionally.
- RD3A/WR3A are dedicated only to FX0N-3A and FX2N-2AD/2DA. They are not the
  access method for FX3U-4AD-ADP/4DA-ADP; those adapters use D8260-D8299
  assignments selected by the complete analog-adapter connection order and
  channel.
- RS-485/Modbus: use supplied protocol, station, baud-rate and register details;
  ask for missing values when the selected implementation depends on them.
- High-speed pulse/frequency output is a fourth, distinct method. Use it only
  when it matches the request. Reject an explicitly confirmed relay-output
  conflict, but do not require CPU/output metadata merely to continue.
- If the command method is not supplied, create a required ``control_method``
  question. Drive model and terminal/register mapping are conditional questions
  only when the selected method or generated I/O depends on them. These are
  external design inputs, not retired PLC nameplate fields.
""",
    "motion_control": """
## Servo / stepper motion requirement selection
- Distinguish a stepper motor/driver (步进电机、步进驱动器, stepper) from a
  sequence step/state machine (步进状态机、步骤、阶段、顺序). Keep both rule
  sets only when the request really contains both motion and sequence behavior.
- Do not delete external motion parameters as PLC nameplate data. A selected
  drive interface, pulse axis, direction output, homing inputs and a positioning
  module model can change the generated program and I/O mapping.
- Ask by instruction family, not with one universal mandatory list:
  * PLSY/DPLSY: pulse output axis, frequency, and pulse count or continuous mode.
  * DRVI/DRVA: relative/absolute mode, target pulses/position, speed, pulse output
    axis, and a separately confirmed direction output.
  * ZRN: zero-return speed, creep speed, DOG input and pulse output. Ask homing
    details only when the user selected that homing is required.
  * DSZR: DOG input, zero-phase input, pulse output and direction output.
- ``homing_method`` must use ``required_when`` controlled by
  ``homing_required``. ``positioning_module_model`` must be conditional on a
  selected external positioning/high-speed-output module implementation; it is
  not required for the base CPU's built-in pulse outputs.
- One FX3U-2HSY-ADP provides the Y0/Y1 high-speed axes. Y2/Y3 at 200 kHz
  require two confirmed adapters, so preserve ``positioning_module_quantity``
  whenever those axes are selected.
- FX3U direction outputs are not a fixed Y0->Y4/Y1->Y5/Y2->Y6 mapping. Confirm a
  valid, non-conflicting Y output; the example may use Y4 but the validator must
  accept other confirmed Y points.
- M8336 configures the DVIT interrupt input and is not ZRN/DSZR completion.
  Associate M8029 directly with its motion instruction. When physical servo
  standstill matters, use the drive's confirmed in-position signal rather than
  treating instruction completion as mechanical completion.
""",
    "motion_control_fx5u": """
## Servo / stepper motion requirement selection for FX5U
- Distinguish a stepper motor/driver from a sequence step/state machine. Keep
  both rule sets only when the request truly contains both kinds of behavior.
- Do not delete external motion parameters as PLC nameplate data. The selected
  drive interface, pulse axis, direction output, homing inputs and positioning
  hardware can change the generated program and I/O mapping.
- Ask only for parameters used by the selected instruction family. Express
  homing and external-module details with ``required_when`` instead of one
  universal mandatory list.
- Validate available axes, direction wiring, instruction operands, special
  devices and completion semantics against the selected FX5U/iQ-F profile.
  Never copy FX3U M8xxx/D83xx addresses into an FX5U project.
""",
    "debugging": """
## PLC debugging mode
- Prefer fault isolation before rewriting logic.
- Ask what bit/value is observed online only when it cannot be inferred.
- Explain likely scan-cycle causes: overwritten output, reset priority, missing edge pulse, timer reset, state transition not reached.
- Do not generate full replacement ladder JSON in debug report mode; return
  possible causes, related rung_id values, and recommended changes.
""",
    "review": """
## PLC review mode
- Review in this order: platform fit, I/O mapping, output ownership, state ownership, alarm/fault handling, reset behavior, online-monitor readability.
- Separate hard errors from advisory warnings.
""",
    "io_mapping": """
## I/O mapping
- Treat the confirmed I/O allocation as the single source of truth.
- If the review card contains both option answers and an editable I/O block, merge them into one canonical spec before generation.
- Current-turn user changes override previous cached or historical assignments.
- If the user edits an option value, update the corresponding canonical I/O
  entry too; do not inject two conflicting versions into the prompt.
""",
    "sfc": """
## SFC / sequence control
- Keep steps and transitions explicit.
- A state register or step relay should have one owner for transitions and one clear reset/init path.
""",
}


def load_library():
    """加载 pattern_library.json（内存缓存）"""
    global _library_cache
    if _library_cache is not None:
        return _library_cache
    path = _get_library_path()
    with open(path, "r", encoding="utf-8") as f:
        _library_cache = json.load(f)
    return _library_cache


def reload_library():
    """强制刷新缓存（模式库文件更新后调用）"""
    global _library_cache
    _library_cache = None
    return load_library()


# ============================
# 关键词匹配分类
# ============================

def classify_request(user_input, target_mode="ladder", is_edit_mode=False):
    """
    对用户需求做关键词匹配，返回匹配的模式/规则/范例/自检项 ID 集合。

    返回:
      dict: {
        "matched_ids": set,
        "scenario": "industrial" | "teaching",
        "is_edit": bool,
        "is_st": bool,
        "selected_instructions": list  # 从 instruction_mapping 匹配到的指令名
      }
    """
    lib = load_library()
    user_lower = user_input.lower()

    # --- 1. 场景检测 ---
    teaching_keywords = ["教学", "练习", "trn", "基础", "考试",
                         "fx-trn", "起保停", "基本逻辑"]
    is_teaching = any(kw in user_lower for kw in teaching_keywords)
    scenario = "teaching" if is_teaching else "industrial"

    # --- 2. 编辑模式 ---
    is_edit = is_edit_mode or user_input.startswith("## 当前梯形图json")

    matched = set()

    # --- 3. 匹配自动补全规则 ---
    for rule in lib.get("auto_completion_rules", []):
        if _match_keywords(user_input, rule.get("keywords_trigger", [])):
            if rule.get("scenario") == "any" or \
               (scenario == "industrial" and rule.get("scenario") == "industrial_only"):
                matched.add(rule["id"])

    # --- 4. 匹配工业模式 ---
    for pattern in lib.get("patterns", []):
        if _match_keywords(user_input, pattern.get("keywords", [])):
            matched.add(pattern["id"])

    # A drive's jog terminal is ordinary discrete drive control, not servo/PLC
    # positioning.  Bare ``步进`` in PLC prose means a sequence step unless a
    # motor/driver/motion term disambiguates it.
    lower = user_input.lower()
    vfd_context = _has_vfd_context(lower)
    if vfd_context:
        matched.add("pattern_vfd")
    motion_specific = _has_motion_context(lower)
    sequence_specific = _has_sequence_context(lower, motion_specific)
    if vfd_context and not motion_specific:
        matched.discard("pattern_h")

    # --- 5. 匹配条件自检项 ---
    for item in lib.get("checklist", []):
        if not item.get("always_include", False):
            if _match_keywords(user_input, item.get("keywords", [])):
                matched.add(item["id"])

    # --- 6. 匹配范例 ---
    for ex in lib.get("examples", []):
        example_keywords = ex.get("keywords")
        if example_keywords is None:
            example_keywords = _example_header_keywords(ex.get("header", ""))
        if _match_keywords(user_input, example_keywords):
            matched.add(ex["id"])

    # --- 7. 匹配指令（instruction_mapping） ---
    selected_instructions = []
    im = lib.get("instruction_mapping", {})
    # Match PLC opcodes as standalone ASCII tokens.  A raw substring check
    # makes short instructions such as TO, RS, SET, MOV and PID fire inside
    # unrelated prose (motor, version, offset, movement, rapid...).  Scan the
    # longest names first so a future RS2/DMOV-style entry takes precedence
    # over its shorter prefix.
    instruction_items = sorted(
        im.items(),
        key=lambda item: (-len(str(item[0])), str(item[0]).upper()),
    )
    for instr_name, instr_info in instruction_items:
        if _match_instruction_token(user_input, instr_name):
            selected_instructions.append(instr_name)
            # 自动拉入该指令关联的范例和模式
            if instr_info.get("example"):
                matched.add(instr_info["example"])
            if instr_info.get("pattern"):
                matched.add(instr_info["pattern"])

    # Phrase-aware disambiguation must run after every matching stage because
    # auto-completion rules and checklists also contain the historical bare
    # keyword ``步进``.
    motion_only_ids = {
        "pattern_h",
        "rule_position_complete",
        "check_position_flag",
    }
    sequence_only_ids = {"pattern_c", "check_step"}
    if not motion_specific:
        matched.difference_update(motion_only_ids)
    if not sequence_specific:
        matched.difference_update(sequence_only_ids)

    route = KnowledgeRouter.route(user_input, target_mode, is_edit_mode)

    return {
        "matched_ids": matched,
        "scenario": scenario,
        "is_edit": is_edit,
        "is_st": target_mode == "st",
        "selected_instructions": selected_instructions,
        "workflow_route": route.to_dict(),
    }


def _match_keywords(user_input, keywords):
    """任一关键词命中即返回 True"""
    if not keywords:
        return False
    user_lower = user_input.lower()
    for kw in keywords:
        if not kw:
            continue
        keyword = kw.lower()
        # Short drive terminal names must match as tokens.  A raw substring
        # check would make e.g. "rl" match unrelated English words.
        if re.fullmatch(r"[a-z]{1,3}", keyword):
            if re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", user_lower):
                return True
        elif re.fullmatch(r"[a-z]+[0-9]+", keyword):
            # A device mentioned in an example header is an exact address, not
            # a prefix family.  In particular Y0 must not match Y000 and pull
            # unrelated DRVI/ZRN examples into a PLSY request.
            if re.search(
                rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])",
                user_lower,
            ):
                return True
        elif keyword in user_lower:
            return True
    return False


def _example_header_keywords(header):
    """Derive semantic header keywords without using example operands.

    Device addresses in a title describe the example body; they are not a
    routing signal.  Treating Y0 as a keyword made every motion example that
    happened to use Y0 match the same request.
    """

    keywords = []
    for token in str(header or "").split():
        normalized = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", token)
        if re.fullmatch(
            r"(?:ER|SM|SD|TS|TC|CS|CC|[XYMSTCDRVZPIN])\d+",
            normalized,
            flags=re.IGNORECASE,
        ):
            continue
        keywords.append(token)
    return keywords


def _match_instruction_token(user_input, instruction_name):
    """Return whether an opcode appears as one standalone ASCII token.

    Chinese text may directly touch an opcode (for example ``使用DRVI定位``),
    while ASCII letters, digits and underscores belong to the same token and
    therefore block a match (``motor`` must not match ``TO`` and ``RS2`` must
    not match ``RS``).
    """
    opcode = str(instruction_name or "").strip()
    if not opcode:
        return False
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(opcode)}(?![A-Za-z0-9_])",
            str(user_input or ""),
            flags=re.IGNORECASE,
        )
    )


def _has_vfd_context(lower):
    strong_terms = (
        "vfd", "inverter", "变频器", "多段速", "段速", "频率给定",
        "模拟调速",
    )
    if any(term in lower for term in strong_terms):
        return True
    if re.search(r"(?<![a-z0-9])(stf|rh|rm|rl)(?![a-z0-9])", lower):
        return True
    if any(signal in lower for signal in ("0-10v", "4-20ma")):
        if any(term in lower for term in ("输出", "给定", "调速", "变频")):
            return True
    return "频率" in lower and any(term in lower for term in ("输出", "调速", "hz", "赫兹"))


def _has_motion_context(lower):
    value = str(lower or "").casefold()
    explicit_terms = (
        "position",
        "motion",
        "pulse",
        "servo",
        "stepper",
        "plsy",
        "plsv",
        "drvi",
        "drva",
        "zrn",
        "dszr",
        "dvit",
        "定位",
        "伺服",
        "脉冲",
        "回原点",
        "原点回归",
    )
    if any(term in value for term in explicit_terms):
        return True
    return bool(re.search(r"步进(?:电机|马达|驱动器?|轴)", value))


def _has_sequence_context(lower, motion_specific=False):
    value = str(lower or "").casefold()
    if any(
        term in value
        for term in (
            "步进状态机",
            "状态机",
            "顺序控制",
            "顺序执行",
            "多阶段",
            "阶段",
            "步骤",
            "依次",
            "先后",
        )
    ):
        return True
    return "步进" in value and not motion_specific


class KnowledgeRouter:
    @staticmethod
    def route(user_input, target_mode="ladder", is_edit_mode=False, forced_task=None):
        text = str(user_input or "")
        lower = text.lower()
        upper = text.upper()
        task_type = forced_task or _detect_task_type(lower, is_edit_mode)
        vendor, confidence, warnings = _detect_vendor(upper, lower)
        bundles = ["task_router", "knowledge_priority"]
        open_points = []

        if target_mode == "st":
            open_points.append("Target output is ST; ladder-only JSON branch rules are advisory.")
        if task_type in {"review", "program_review", "debug"}:
            bundles.append("output_ownership")
            bundles.append("debugging" if task_type == "debug" else "review")
        if task_type in {"analysis", "generate", "edit", "repair", "debug_fix", "contract_repair"}:
            bundles.extend(["io_mapping", "output_ownership", "scan_semantics"])
        if task_type in {"repair", "debug_fix", "contract_repair"}:
            bundles.append("debugging")
        if task_type == "io_mapping":
            bundles.append("io_mapping")
        if task_type == "sfc":
            bundles.extend(["sfc", "output_ownership", "scan_semantics"])
        if vendor in {"FX3U", "GX Works2", "Mitsubishi"}:
            bundles.append("mitsubishi_fx3u")
        if vendor == "FX5U":
            bundles.append("mitsubishi_fx5u")

        vfd_context = _has_vfd_context(lower)
        if vfd_context:
            bundles.append("vfd_control")

        motion_context = _has_motion_context(lower)
        if motion_context:
            bundles.append(
                "motion_control_fx5u" if vendor == "FX5U" else "motion_control"
            )
            vendor_bundle = "mitsubishi_fx5u" if vendor == "FX5U" else "mitsubishi_fx3u"
            if vendor_bundle not in bundles:
                bundles.append(vendor_bundle)
            max_axis = "3" if vendor == "FX5U" else "2"
            if not re.search(rf"(?<![A-Z0-9])Y0*[0-{max_axis}](?!\d)", upper):
                open_points.append(
                    "Motion pulse axis output is not explicit; confirm the exact Y point and hardware limit."
                )
        if any(word in lower for word in (
            "analog", "模拟量", "32位", "浮点", "d8350", "d8340", "d8360",
            "4da", "4da-adp", "0-10v", "4-20ma",
        )):
            vendor_bundle = "mitsubishi_fx5u" if vendor == "FX5U" else "mitsubishi_fx3u"
            if vendor_bundle not in bundles:
                bundles.append(vendor_bundle)
        if any(word in lower for word in ("io", "i/o", "输入", "输出", "软元件", "地址", "分配")):
            if "io_mapping" not in bundles:
                bundles.append("io_mapping")

        if vendor == "mixed":
            warnings.append(
                "Mixed PLC vendor signals detected; keep vendor rules separated."
            )
        if not re.search(r"(?:FX3U|FX3G|FX5U|GX\s*WORKS?2|三菱|MITSUBISHI)", upper):
            open_points.append("PLC family is not explicit; default project PLC may be used.")

        ordered = []
        for item in bundles:
            if item not in ordered:
                ordered.append(item)
        return KnowledgeRouterResult(
            task_type=task_type,
            vendor=vendor,
            confidence=confidence,
            knowledge_bundles=ordered,
            open_points=open_points,
            warnings=warnings,
        )


def _detect_task_type(lower, is_edit_mode=False):
    if is_edit_mode:
        return "edit"
    checks = (
        ("debug", ("为什么", "咋办", "不动", "失败", "报错", "wrong", "bug", "debug", "修复")),
        ("review", ("检查", "评审", "review", "看一下", "校验", "问题")),
        ("explain", ("解释", "说明", "是什么", "怎么用", "manual")),
        ("sfc", ("sfc", "顺序功能图", "流程图")),
        ("io_mapping", ("i/o", "io", "软元件", "输入输出", "分配表")),
    )
    for task, keywords in checks:
        if any(keyword in lower for keyword in keywords):
            return task
    return "generate"


def _detect_vendor(upper, lower):
    signals = {
        "Mitsubishi": ("MITSUBISHI", "三菱", "FX3U", "FX3G", "FX5U", "GX WORKS2", "GXWORKS2"),
        "Siemens": ("SIEMENS", "西门子", "S7-", "TIA"),
        "Omron": ("OMRON", "欧姆龙", "CX-PROGRAMMER"),
    }
    hits = [
        vendor
        for vendor, keywords in signals.items()
        if any(keyword in upper or keyword.lower() in lower for keyword in keywords)
    ]
    if len(hits) > 1:
        return "mixed", 0.5, ["Detected multiple PLC vendor families."]
    if hits:
        if hits[0] == "Mitsubishi":
            if "FX5U" in upper:
                return "FX5U", 0.95, []
            if "FX3U" in upper:
                return "FX3U", 0.95, []
            if "GX WORKS2" in upper or "GXWORKS2" in upper:
                return "GX Works2", 0.9, []
        return hits[0], 0.85, []
    return "unknown", 0.35, []


def build_workflow_prompt(
    user_input="",
    target_mode="ladder",
    is_edit_mode=False,
    forced_task=None,
):
    route = KnowledgeRouter.route(
        user_input,
        target_mode=target_mode,
        is_edit_mode=is_edit_mode,
        forced_task=forced_task,
    )
    sections = [
        "\n---\n# Routed PLC workflow context",
        f"- task_type: {route.task_type}",
        f"- vendor: {route.vendor}",
        f"- confidence: {route.confidence:.2f}",
    ]
    if route.open_points:
        sections.append("- open_points: " + "; ".join(route.open_points))
    if route.warnings:
        sections.append("- warnings: " + "; ".join(route.warnings))
    for bundle_id in route.knowledge_bundles:
        content = KNOWLEDGE_BUNDLES.get(bundle_id, "")
        if content:
            sections.append(content.strip())
    return "\n".join(sections), route


# ============================
# 动态 Prompt 组装
# ============================

MAX_ASSEMBLED_CHARS = 6500


def assemble_prompt(
    classification,
    target_mode="ladder",
    include_core=True,
    plc_model=None,
):
    """
    根据分类结果组装紧凑 System Prompt。

    参数:
      classification: classify_request() 的返回值
      target_mode: "ladder" 或 "st"
      include_core: 是否包含角色、通用铁律和基础 schema。主 API 的基础
        prompt 已经包含这些内容，因此只装配场景知识以避免重复。
      plc_model: 已解析的项目型号；用于避免 ST 提示词跨型号混入软元件。

    返回:
      str: 组装后的 Prompt 文本
    """
    if target_mode == "st":
        route_vendor = (classification.get("workflow_route") or {}).get("vendor")
        return _assemble_st_prompt(plc_model or route_vendor)

    lib = load_library()
    matched_ids = classification["matched_ids"]
    scenario = classification["scenario"]
    sections = []
    skipped = False

    # Output constraints must survive even when optional examples do not fit.
    always = lib.get("always_include", {})
    output_section = always.get("output_constraints", {})
    output_content = (
        output_section.get("content", "")
        if isinstance(output_section, dict)
        else output_section
    )
    reserved = len(output_content) + (1 if output_content else 0)
    available = max(0, MAX_ASSEMBLED_CHARS - reserved)

    def current_size():
        return sum(len(item) for item in sections) + max(0, len(sections) - 1)

    def append_complete(text, *, required=False):
        nonlocal skipped
        text = str(text or "")
        if not text:
            return False
        addition = len(text) + (1 if sections else 0)
        if required or current_size() + addition <= available:
            sections.append(text)
            return True
        skipped = True
        return False

    # ---- 始终包含 ----
    vendor = (classification.get("workflow_route") or {}).get("vendor")
    always_keys = (
        ["role", "special_relays", "iron_laws", "json_schema"]
        if include_core
        else []
    )
    if vendor == "FX5U" and "special_relays" in always_keys:
        # The legacy special-relay table is explicitly FX3U/M8000 based.  The
        # selected FX5U profile is injected separately and must not compete
        # with this more detailed but wrong-device table.
        always_keys.remove("special_relays")
    for key in always_keys:
        sec = always.get(key, {})
        content = sec.get("content", "") if isinstance(sec, dict) else sec
        if content:
            append_complete(content, required=True)

    # ---- 场景标注 ----
    if scenario == "teaching":
        note = "\n**注意：教学/基础场景。保持简洁；停止/急停在任何场景都不自动补全。**\n"
        append_complete(note)

    # ---- 自动补全规则 ----
    matched_rules = _collect(lib, "auto_completion_rules", matched_ids)
    if matched_rules:
        header = "\n---\n# 📋 自动补全规则\n\n| # | 条件 | 动作 |\n|---|------|------|\n"
        text = header + "".join(r["header"] + "\n" for r in matched_rules)
        append_complete(text)

    # ---- 工业模式 ----
    matched_patterns = _collect(lib, "patterns", matched_ids)
    if matched_patterns:
        header = "\n---\n# 🏭 工业模式\n\n"
        pattern_parts = []
        for p in matched_patterns:
            text = f"## {p['name']}\n{p['description']}\n\n"
            candidate = header + "".join(pattern_parts) + text
            if current_size() + len(candidate) + (1 if sections else 0) <= available:
                pattern_parts.append(text)
            else:
                skipped = True
        if pattern_parts:
            append_complete(header + "".join(pattern_parts))

    # ---- 范例 ----
    matched_examples = _collect(lib, "examples", matched_ids)
    if matched_examples:
        header = "\n---\n# 📖 参考范例\n\n"
        example_parts = []
        for ex in matched_examples:
            text = ex["header"] + "\n" + ex["content"] + "\n"
            candidate = header + "".join(example_parts) + text
            if current_size() + len(candidate) + (1 if sections else 0) <= available:
                example_parts.append(text)
            else:
                skipped = True
        if example_parts:
            append_complete(header + "".join(example_parts))

    # ---- 自检清单 ----
    checklist_text = "\n---\n# ✅ 输出前自检\n\n| # | 检查项 | 通过标准 |\n|---|--------|---------|\n"
    checklist_parts = []
    for item in lib.get("checklist", []):
        if item.get("always_include") or item["id"] in matched_ids:
            checklist_parts.append(item["content"] + "\n")
    append_complete(checklist_text + "".join(checklist_parts))

    # ---- 编辑模式 ----
    if classification["is_edit"]:
        edit_note = ("\n---\n## 🔄 多轮增量编辑模式\n"
                     "用户消息以 `## 当前梯形图JSON` 开头时，为对既有程序的增量修改。\n"
                     "只修改/新增用户指定的梯级，不改变不相关的梯级。保持 rung_id 递增。\n")
        append_complete(edit_note)

    # ---- 输出约束 ----
    if output_content:
        sections.append(output_content)

    # Whole sections are either present or absent; never hand the model half a
    # JSON example.  The omission note contains no instructions and is safe to
    # drop if the budget is already exactly full.
    result = "\n".join(sections)
    omission_note = "\n\n(提示：已按整段预算省略部分低优先级范例。)"
    if skipped and len(result) + len(omission_note) <= MAX_ASSEMBLED_CHARS:
        result += omission_note
    return result


def _assemble_st_prompt(plc_model=None):
    """ST 模式的简化 Prompt"""
    normalized_model = str(plc_model or "").strip().upper()
    if normalized_model.startswith("FX5"):
        platform = "FX5U / GX Works3"
        special_devices = """# 特殊软元件（FX5U）
SM8000=常ON SM8002=初始脉冲 SM8013=1s时钟
SM8029=应用指令复用结束标志（须紧邻对应指令）
SM8340/SM8350/SM8360=Y0/Y1/Y2脉冲BUSY
SM8348/SM8358/SM8368=定位驱动中
SM8349/SM8359/SM8369=脉冲停止指令（不是监控位）
SM8562=串行发送请求 SM8563=串行接收完成
禁止使用FX3U无S前缀的特殊软元件地址；以当前FX5U型号资料为准。"""
    else:
        platform = "FX3G / FX3U / FX3UC / GX Works2"
        special_devices = """# 特殊软元件（FX3U）
M8000=常ON M8002=初始脉冲 M8013=1s时钟
M8029=应用指令复用结束标志(须紧邻对应指令) M8336=DVIT中断输入指定功能有效
M8340/M8350/M8360=Y0/Y1/Y2脉冲BUSY M8348/M8358/M8368=定位驱动中
M8349/M8359/M8369=脉冲停止指令(不是监控位)
M8122=RS发送请求 M8123=RS接收完成
普通T定时器的使能变OFF后才复位；M8000单独驱动只能形成上电延时，不能形成周期闪烁。"""
    return f"""# Role
你是三菱 PLC ST 语言专家。将用户需求转为结构化文本。当前平台：{platform}。只能使用当前 PLC 型号的指令集和软元件前缀。

# 语法铁律
- 赋值 `:=`，布尔仅用 TRUE/FALSE，禁止 0/1
- 逻辑 AND/OR/NOT/XOR，禁止 & |
- 每条语句以 `;` 结尾
- 比较 > >= < <= = <>，D 寄存器比较前用 WORD_TO_INT() 转换
- 定时器 OUT_T(条件, TCx, 设定值)，触点 TSx
- 计数器 OUT_C(条件, CCx, 设定值)，触点 CSx
- 普通定时器使能变OFF后才复位；当前型号的运行常ON特殊继电器单独驱动只能形成上电延时，不能形成周期闪烁

{special_devices}

# 输出
{{\"st_code\": \"完整ST代码\"}}"""


def _collect(lib, section_key, matched_ids):
    """从库中收集匹配的条目，按 priority 排序"""
    entries = [
        e for e in lib.get(section_key, [])
        if e.get("id") in matched_ids
    ]
    entries.sort(key=lambda e: e.get("priority", 999))
    return entries

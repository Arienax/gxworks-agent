"""Presentation-only names for internal identifiers.

The application persists stable identifiers such as ``manual_start_test`` and
``sim_20260830...`` because they are useful for binding evidence and patches.
Those values are not suitable as operator-facing labels.  This module keeps
the persisted value untouched and provides one shared conversion at UI
boundaries.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence
from shared.i18n import get_language, language_context, tr, translate


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_PLC_DEVICE_RE = re.compile(
    r"^(?:[XYMDTCS]\d+|[XYMDTCS]\d+[A-Z]?|D\d+\.\d+)$", re.IGNORECASE
)
_OPAQUE_ID_RE = re.compile(
    r"^(?:sim|plan|attempt|agents?|analysis|inspection(?:[-_]retry)?|"
    r"compile|task|debug[-_]?plan)[-_]"
    r"(?:[0-9a-f]{8,}|\d{8}T\d{6}(?:[-_][0-9a-f]+)?)$",
    re.IGNORECASE,
)
_UUID_OR_HASH_RE = re.compile(
    r"^(?:[0-9a-f]{10,}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$",
    re.IGNORECASE,
)
_INLINE_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9_/\\])"
    r"([A-Za-z][A-Za-z0-9]*(?:[_-][A-Za-z0-9]+)+)"
    r"(?![A-Za-z0-9_/\\.])"
)
_JSON_PATH_RE = re.compile(r"\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])+")
_JSON_PATH_TOKEN_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


_EXACT_LABELS = {
    "regression": "回归测试",
    "regression_test": "回归测试",
    "full_regression": "完整回归测试",
    "manual_start_test": "手动启动测试",
    "auto_start_test": "自动启动测试",
    "state_machine_control": "步进状态机控制",
    "step_state_machine": "步进状态机",
    "sequential_state_machine": "顺序状态机",
    "relay_latch_control": "继电器自保持控制",
    "set_reset_control": "SET/RST 置位复位控制",
    "counter_control": "计数器控制",
    "register_state_machine": "数据寄存器状态机",
    "auto_stop_via_mode_cycle": "通过模式切换停止自动运行",
    "auto_low_pressure_add_second_pump": "自动模式低压时追加第二台泵",
    "auto_low_pressure_full_three_pump_sequence": "自动模式低压时依次启动三台泵",
    "switch_auto_no_start": "切换至自动模式且不启动",
    "start_pulse": "按下启动按钮",
    "start_pulse_off": "松开启动按钮",
    "reset_pulse": "按下复位按钮",
    "reset_release": "松开复位按钮",
    "verify_single": "检查单泵运行",
    "still_single": "保持单泵运行",
    "manual_transition": "切换至手动模式",
    "auto_again_no_start": "再次切换至自动模式且不启动",
    "initial_one_pump": "初始保持单泵运行",
    "timer_adds_second": "定时后追加第二台泵",
    "all_three_pumps": "三台泵全部运行",
    "fault1_assert": "触发1号泵故障",
    "fault1_release": "解除1号泵故障",
    "backup_runs": "备用泵运行",
    "check_initial": "检查初始状态",
    "verify_initial": "检查初始状态",
    "start_stop": "启动与停止",
    "start_and_stop": "启动与停止",
    "press_start": "按下启动按钮",
    "release_start": "松开启动按钮",
    "press_stop": "按下停止按钮",
    "release_stop": "松开停止按钮",
    "press_reset": "按下复位按钮",
    "release_reset": "松开复位按钮",
    "start_requires_stop_released": "停止按钮松开后才允许启动",
    "press_start_while_stop": "停止按钮按下时尝试启动",
    "should_not_start": "应保持停止",
    "verify_run": "检查运行状态",
    "verify_stopped": "检查停止状态",
    "still_running": "保持运行",
    "reset_after_full": "满料后复位",
    "press_start_again": "再次按下启动按钮",
    "one_part_on": "第1个工件信号接通",
    "one_part_off": "第1个工件信号断开",
    "start_blocked_by_stop": "停止按钮阻止启动",
    "legacy_debug_finding": "历史调试问题",
    "schema_version": "格式版本",
    "plc_model": "PLC 型号",
    "test_name": "测试项目",
    "suite_name": "测试方案",
    "step_id": "步骤编号",
    "rung_id": "梯级编号",
    "branch_id": "支路编号",
    "network_id": "程序段编号",
    "run_id": "运行记录",
    "plan_id": "方案记录",
    "report_id": "报告记录",
    "finding_id": "问题编号",
    "mutual_exclusion": "互斥约束",
    "maximum_on_time": "最大接通时间",
    "minimum_off_time": "最短断开时间",
    "state_constraint": "状态约束",
    "required_when": "条件必填",
    "fault_injections": "故障场景",
    "trace_devices": "观测元件",
    "wait_for": "等待条件",
    "sample_ms": "采样周期",
    "timeout_ms": "超时时间",
    "unknown": "未知",
    "not_found": "未找到",
    "connection_failed": "连接失败",
    "invalid_argument": "参数无效",
    "permission_denied": "权限不足",
    "timed_out": "超时",
    "not_available": "不可用",
    "pydantic_core": "API 运行组件",
    "allowed_rung_ids": "允许修改的梯级",
    "delete_rung_ids": "待删除梯级",
}

_DISPLAY_FIELD_LABELS = {
    "schema_version": "格式版本",
    "name": "名称",
    "description": "说明",
    "plc_model": "PLC 型号",
    "initial": "初始状态",
    "steps": "测试步骤",
    "id": "步骤名称",
    "at_ms": "执行时间",
    "set": "写入状态",
    "expect": "期望状态",
    "wait_for": "等待条件",
    "invariants": "运行约束",
    "fault_injections": "故障场景",
    "trace_devices": "观测元件",
    "sample_ms": "采样周期",
    "timeout_ms": "超时时间",
    "metadata": "附加信息",
    "tests": "测试",
    "type": "类型",
}

_DISPLAY_COLLECTION_LABELS = {
    "tests": "测试",
    "steps": "步骤",
    "invariants": "运行约束",
    "fault_injections": "故障场景",
}


_TOKEN_LABELS = {
    "function": "功能",
    "feature": "功能",
    "test": "测试",
    "tests": "测试",
    "case": "场景",
    "scenario": "场景",
    "manual": "手动",
    "auto": "自动",
    "automatic": "自动",
    "again": "再次",
    "start": "启动",
    "started": "已启动",
    "stop": "停止",
    "stopped": "停止",
    "restart": "重新启动",
    "reset": "复位",
    "verify": "状态检查",
    "verification": "检查",
    "check": "检查",
    "release": "松开",
    "released": "已松开",
    "latch": "自保持",
    "latched": "自保持检查",
    "unlatch": "解除保持",
    "fault": "故障",
    "faults": "故障",
    "error": "错误",
    "failure": "失败",
    "pump": "泵",
    "pumps": "泵",
    "motor": "电机",
    "motors": "电机",
    "main": "主",
    "primary": "主",
    "backup": "备用",
    "standby": "备用",
    "switch": "切换",
    "switches": "切换",
    "switched": "已切换",
    "mode": "模式",
    "state": "状态",
    "machine": "状态机",
    "step": "步骤",
    "stage": "阶段",
    "sequence": "顺序",
    "sequential": "顺序",
    "pressure": "压力",
    "level": "液位",
    "temperature": "温度",
    "speed": "速度",
    "frequency": "频率",
    "high": "高",
    "low": "低",
    "full": "满料",
    "normal": "正常",
    "restored": "恢复正常",
    "restore": "恢复",
    "recovery": "恢复",
    "run": "运行",
    "running": "运行",
    "runs": "运行",
    "control": "控制",
    "input": "输入",
    "inputs": "输入",
    "output": "输出",
    "outputs": "输出",
    "on": "接通",
    "off": "断开",
    "open": "断开",
    "close": "接通",
    "closed": "接通",
    "pulse": "脉冲",
    "timer": "定时器",
    "timing": "定时",
    "counter": "计数器",
    "count": "计数",
    "increment": "递增",
    "decrement": "递减",
    "delay": "延时",
    "timeout": "超时",
    "interlock": "互锁",
    "mutex": "互斥",
    "alarm": "报警",
    "emergency": "急停",
    "estop": "急停",
    "home": "回零",
    "homing": "回零",
    "position": "定位",
    "positioning": "定位",
    "forward": "正转",
    "reverse": "反转",
    "servo": "伺服",
    "inverter": "变频器",
    "drive": "驱动",
    "regression": "回归测试",
    "workflow": "工作流",
    "program": "程序",
    "network": "程序段",
    "rung": "梯级",
    "branch": "支路",
    "all": "全部",
    "single": "单台",
    "first": "第一",
    "second": "第二",
    "third": "第三",
    "one": "1",
    "two": "2",
    "three": "3",
    "add": "增加",
    "remove": "移除",
    "read": "读取",
    "write": "写入",
    "initial": "初始",
    "initialize": "初始化",
    "initialization": "初始化",
    "init": "初始化",
    "final": "最终",
    "complete": "完成",
    "completed": "已完成",
    "passed": "通过",
    "failed": "失败",
    "pending": "等待处理",
    "and": "并",
    "or": "或",
    "to": "至",
    "after": "后",
    "before": "前",
    "via": "通过",
    "cycle": "循环切换",
    "transition": "切换",
    "still": "仍保持",
    "adds": "追加",
    "assert": "触发",
    "conveyor": "输送带",
    "counting": "计数",
    "press": "按下",
    "pressed": "已按下",
    "requires": "需要",
    "while": "时",
    "should": "应当",
    "not": "未",
    "sensor": "传感器",
    "done": "完成",
    "edge": "边沿",
    "rearm": "重新使能",
    "prevented": "已禁止",
    "ignored": "已忽略",
    "expect": "检查",
    "restarted": "已重新启动",
    "five": "5",
    "attempt": "尝试",
    "controls": "控制操作",
    "rising": "上升",
    "only": "仅一次",
    "hold": "持续保持",
    "part": "工件",
    "blocked": "已阻止",
    "by": "由于",
    "with": "与",
    "without": "无",
    "no": "无",
    "schema": "格式",
    "version": "版本",
    "mutual": "互相",
    "exclusion": "互斥",
    "maximum": "最大",
    "minimum": "最小",
    "time": "时间",
    "constraint": "约束",
    "required": "必填",
    "when": "条件",
}


_TECHNICAL_TOKENS = {
    "AI",
    "CPU",
    "CSV",
    "GX",
    "HMI",
    "IR",
    "JSON",
    "MODBUS",
    "PLC",
    "RS485",
    "ST",
}

_SOURCE_LABELS = {
    "user": "用户填写",
    "ai": "AI 分析",
    "analysis": "需求分析",
    "deterministic": "规则推导",
    "inferred": "自动推导",
    "default": "默认值",
    "confirmed": "用户确认",
    "legacy": "历史数据",
    "previous": "已确认数据",
    "raw": "原有分配",
    "model": "AI 生成",
    "generated": "自动生成",
    "manual": "手动填写",
    "local": "本地检查",
    "merged": "综合结果",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _fallback(kind: str, index: Optional[int]) -> str:
    label = str(tr(_clean(kind) or "项目"))
    return f"{label} {int(index)}" if index is not None else label


def looks_like_internal_identifier(value: Any) -> bool:
    """Return whether ``value`` looks like a machine-facing identifier."""

    text = _clean(value)
    if not text or _PLC_DEVICE_RE.fullmatch(text):
        return False
    if _OPAQUE_ID_RE.fullmatch(text) or _UUID_OR_HASH_RE.fullmatch(text):
        return True
    if ("_" in text or "-" in text) and not re.search(r"\s", text):
        return bool(re.fullmatch(r"[A-Za-z0-9_-]+", text))
    return bool(
        re.fullmatch(r"[a-z]+(?:[A-Z][A-Za-z0-9]*)+", text)
        or re.fullmatch(r"(?:function|case|step|network|rung|branch)\d+[A-Za-z]?", text, re.I)
    )


def _split_identifier(text: str) -> list[str]:
    chunks = re.split(r"[_\-\s]+", text)
    result: list[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        upper = chunk.upper()
        if upper in _TECHNICAL_TOKENS or re.fullmatch(r"FX\d+[A-Z]?", upper):
            result.append(upper)
            continue
        camel = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", chunk)
        camel = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", camel)
        camel = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", camel)
        result.extend(part for part in camel.split() if part)
    return result


def naturalize_identifier(
    value: Any,
    *,
    kind: str = "项目",
    index: Optional[int] = None,
) -> str:
    """Convert one stable identifier into an operator-facing Chinese label.

    The caller must continue storing and binding with the original identifier.
    ``kind`` and ``index`` are used only for opaque values that cannot be
    meaningfully translated.
    """

    text = _clean(value)
    if not text:
        return _fallback(kind, index)
    if _PLC_DEVICE_RE.fullmatch(text):
        return text.upper()
    if get_language() != "zh-CN":
        if _OPAQUE_ID_RE.fullmatch(text) or _UUID_OR_HASH_RE.fullmatch(text):
            return _fallback(kind, index)
        version_match = re.fullmatch(r"v0*(\d+)", text, re.I)
        if version_match:
            return str(tr("版本")) + " " + str(int(version_match.group(1)))
        lowered = text.casefold().replace("-", "_")
        if lowered in _EXACT_LABELS:
            return str(tr(_EXACT_LABELS[lowered]))
        if _CJK_RE.search(text) or re.search(r"[\u3040-\u30ff]", text):
            return text
        if not looks_like_internal_identifier(text):
            return text
        pieces = _split_identifier(text)
        if get_language() == "en":
            return " ".join(pieces).capitalize()
        return " ".join(str(tr(_TOKEN_LABELS.get(piece.casefold(), piece))) for piece in pieces)
    version = re.fullmatch(r"v0*(\d+)", text, re.IGNORECASE)
    if version:
        return f"版本 {int(version.group(1))}"
    function = re.fullmatch(
        r"function[_\-\s]*(\d+)[_\-\s]*([A-Za-z])", text, re.IGNORECASE
    )
    if function:
        return f"功能 {int(function.group(1))}{function.group(2).upper()}"
    location = re.fullmatch(
        r"(network|rung|branch|step|case)[_\-\s]*0*(\d+)",
        text,
        re.IGNORECASE,
    )
    if location:
        labels = {
            "network": "程序段",
            "rung": "梯级",
            "branch": "支路",
            "step": "步骤",
            "case": "测试项目",
        }
        return f"{labels[location.group(1).lower()]} {int(location.group(2))}"
    compact_network = re.fullmatch(r"N0*(\d+)", text, re.IGNORECASE)
    if compact_network:
        return f"程序段 {int(compact_network.group(1))}"
    lowered = text.casefold().replace("-", "_")
    if lowered in _EXACT_LABELS:
        return _EXACT_LABELS[lowered]
    if _OPAQUE_ID_RE.fullmatch(text) or _UUID_OR_HASH_RE.fullmatch(text):
        return _fallback(kind, index)
    # Hardware model names and catalog codes are operator-relevant values, not
    # presentation IDs.  Preserve them verbatim (for example FR-D700,
    # FX3U-48MR and MR-JE-40A).
    if (
        "-" in text
        and "_" not in text
        and re.fullmatch(r"[A-Za-z0-9.-]+", text)
        and re.search(r"[A-Z0-9]", text)
    ):
        return text
    if _CJK_RE.search(text) and not looks_like_internal_identifier(text):
        return text

    pieces: list[tuple[str, str]] = []
    unknown_count = 0
    for token in _split_identifier(text):
        lower = token.casefold()
        upper = token.upper()
        if upper in _TECHNICAL_TOKENS or re.fullmatch(r"FX\d+[A-Z]?", upper):
            pieces.append(("technical", upper))
        elif token.isdigit():
            pieces.append(("number", str(int(token))))
        elif lower in _TOKEN_LABELS:
            pieces.append(("translated", _TOKEN_LABELS[lower]))
        elif len(token) == 1 and token.isalpha():
            pieces.append(("letter", token.upper()))
        else:
            unknown_count += 1
            pieces.append(("unknown", token))

    # An opaque or mostly unknown slug is less useful than a stable ordinal.
    if unknown_count and unknown_count * 2 >= len(pieces):
        return _fallback(kind, index)

    rendered = ""
    previous_kind = ""
    for piece_kind, piece in pieces:
        if not rendered:
            rendered = piece
        elif piece_kind in {"technical", "unknown"} or previous_kind in {
            "technical",
            "unknown",
        }:
            rendered += " " + piece
        else:
            rendered += piece
        previous_kind = piece_kind

    rendered = re.sub(r"泵\s*(\d+)", r"\1号泵", rendered)
    rendered = re.sub(r"(\d+)\s*泵", r"\1号泵", rendered)
    rendered = re.sub(r"电机\s*(\d+)", r"\1号电机", rendered)
    rendered = re.sub(r"(\d+)\s*电机", r"\1号电机", rendered)
    rendered = re.sub(r"传感器\s*(\d+)", r"\1号传感器", rendered)
    rendered = re.sub(r"(\d+)\s*工件", r"\1个工件", rendered)
    rendered = re.sub(r"第([123])泵", r"第\1台泵", rendered)
    rendered = re.sub(r"功能\s*(\d+)\s*([A-Z])", r"功能 \1\2", rendered)
    rendered = re.sub(r"(程序段|梯级|支路|步骤|测试项目)\s*(\d+)", r"\1 \2", rendered)
    rendered = rendered.replace("主泵", "主泵").replace("备用并", "备用泵并")
    rendered = re.sub(r"\s+", " ", rendered).strip()
    return rendered or _fallback(kind, index)


def naturalize_display_text(value: Any) -> str:
    """Replace embedded snake/kebab identifiers in otherwise readable text."""

    raw = str(value or "")
    if not raw:
        return ""
    leading = raw[: len(raw) - len(raw.lstrip())]
    trailing = raw[len(raw.rstrip()) :]
    text = raw.strip()
    if not text:
        return raw
    if looks_like_internal_identifier(text):
        return leading + naturalize_identifier(text) + trailing

    def replace(match: re.Match[str]) -> str:
        return naturalize_identifier(match.group(1))

    rendered = _JSON_PATH_RE.sub(
        lambda match: _naturalize_json_path(match.group(0)),
        text,
    )
    rendered = re.sub(
        r"pydantic_core(?:\.pydantic_core)?",
        str(tr("API 运行组件")),
        rendered,
        flags=re.IGNORECASE,
    )
    for field, label in _DISPLAY_FIELD_LABELS.items():
        rendered = re.sub(
            rf"(?P<quote>[\"'])?{re.escape(field)}(?P=quote)\s*:",
            str(tr(label)) + (": " if get_language() == "en" else "："),
            rendered,
        )
    rendered = _INLINE_IDENTIFIER_RE.sub(replace, rendered)
    colon = ": " if get_language() == "en" else "："
    rendered = rendered.replace("assertion:", str(tr("状态检查")) + colon)
    rendered = rendered.replace("invariant:", str(tr("运行约束")) + colon)
    rendered = rendered.replace("network:", str(tr("程序段")) + colon)
    return leading + rendered + trailing


def _naturalize_json_path(path: str) -> str:
    """Render a validation path without hiding the failing test or field."""

    tokens = [
        (field, index)
        for field, index in _JSON_PATH_TOKEN_RE.findall(path)
    ]
    rendered = []
    cursor = 0
    while cursor < len(tokens):
        field, index = tokens[cursor]
        if field:
            collection_label = _DISPLAY_COLLECTION_LABELS.get(field)
            if (
                collection_label
                and cursor + 1 < len(tokens)
                and tokens[cursor + 1][1]
            ):
                rendered.append(
                    f"{tr(collection_label)} {int(tokens[cursor + 1][1]) + 1}"
                )
                cursor += 2
                continue
            rendered.append(str(tr(_DISPLAY_FIELD_LABELS.get(field, field))))
        elif index:
            rendered.append(str(int(index) + 1))
        cursor += 1
    return " / ".join(rendered) or str(tr("程序内部位置"))


def _trailing_json_string_start(text: str) -> Optional[int]:
    """Return the start of a JSON string that needs a later stream chunk.

    A streamed key can arrive as ``\"at`` / ``_ms\"`` / ``:``.  Emitting the
    middle chunk before the colon makes the presentation converter treat
    ``at_ms`` as a generic identifier.  Hold that final quoted fragment until
    its following punctuation arrives.
    """

    in_string = False
    escaped = False
    string_start: Optional[int] = None
    last_closed_start: Optional[int] = None
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
                last_closed_start = string_start
        elif character == '"':
            in_string = True
            string_start = index

    if in_string:
        return string_start
    stripped = text.rstrip()
    if stripped.endswith('"'):
        return last_closed_start
    return None


def preferred_display_name(
    value: Any,
    *,
    kind: str = "项目",
    index: Optional[int] = None,
    descriptive_keys: Sequence[str] = (
        "display_name",
        "description",
        "title",
        "label",
        "comment",
        "summary",
    ),
) -> str:
    """Prefer a model-provided natural description, then translate its ID."""

    if isinstance(value, Mapping):
        for key in descriptive_keys:
            candidate = _clean(value.get(key))
            if candidate:
                return naturalize_display_text(candidate)
        for key in ("name", "id", "key"):
            candidate = _clean(value.get(key))
            if candidate:
                return naturalize_identifier(candidate, kind=kind, index=index)
        return _fallback(kind, index)
    return naturalize_identifier(value, kind=kind, index=index)


def version_display_name(value: Any) -> str:
    text = _clean(value)
    if not text or text == "-":
        return str(tr("未指定版本"))
    return naturalize_identifier(text, kind="版本")


def source_display_name(value: Any) -> str:
    """Return a readable origin label while callers retain the raw enum."""

    text = _clean(value)
    return str(tr(_SOURCE_LABELS.get(
        text.casefold(),
        naturalize_identifier(text, kind="系统生成") if text else "未标明来源",
    )))


class DisplayTextStream:
    """Naturalize arbitrarily split stream chunks without leaking partial IDs."""

    def __init__(self, language=None) -> None:
        self._pending = ""
        self.language = language or get_language()

    def feed(self, value: Any) -> str:
        combined = self._pending + str(value or "")
        self._pending = ""
        if not combined:
            return ""
        quoted_fragment_start = _trailing_json_string_start(combined)
        if quoted_fragment_start is not None:
            self._pending = combined[quoted_fragment_start:]
            combined = combined[:quoted_fragment_start]
        trailing_identifier = re.search(r"[A-Za-z0-9_-]+$", combined)
        if trailing_identifier:
            self._pending = trailing_identifier.group(0)
            combined = combined[: trailing_identifier.start()]
        with language_context(self.language):
            return naturalize_display_text(combined) if combined else ""

    def flush(self) -> str:
        pending = self._pending
        self._pending = ""
        with language_context(self.language):
            return naturalize_display_text(pending) if pending else ""


__all__ = [
    "DisplayTextStream",
    "looks_like_internal_identifier",
    "naturalize_display_text",
    "naturalize_identifier",
    "preferred_display_name",
    "source_display_name",
    "version_display_name",
]

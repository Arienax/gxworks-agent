"""Agent B: translate a confirmed specification into one ladder candidate.

Agent A owns requirement analysis and confirmation. Agent B deliberately receives only
the confirmed engineering projection. To keep generation latency proportional to the
actual control logic, the model emits a compact ladder plan; deterministic local code
expands that plan into the existing ``ladder_v1`` representation before the ordinary
validators, IR builder and persistence path see it.
"""
from __future__ import annotations

import copy
import json

from model_provider import TextDelta
from plc_generation_context import _build_knowledge_context, public_generation_specification
from prompt_context_policy import audit_section
from response_language import ResponseContract


_GENERATION_REQUEST = (
    "根据已经由用户确认的规格生成完整梯形图。"
    "不得重新分析需求、提出问题或改变已确认 I/O、触点极性、参数和所选方案。"
    "输入条件的 OR 必须在同一个输出分支中表示；"
    "不得把 (A OR B) -> 同一输出 拆成多个 output branch/多个 branches 来表达。"
    "只返回一份最终 JSON；顶层对象闭合后立即结束回复，"
    "不得在同一次 completion 中自检后再重写或追加第二份完整 JSON。"
)

_COMPACT_RESPONSE = ResponseContract("compact_ladder", "json")
_CONTACT_TYPES = frozenset({"NO", "NC", "P", "F", "RISING", "FALLING"})
_COMPARE_PREFIXES = frozenset({"=", "==", "<>", ">=", "<=", ">", "<"})
_TYPED_OUTPUTS = frozenset({"COIL", "PLS", "PLF", "TIMER", "COUNTER"})

_COMPACT_PROTOCOL = """# Agent B compact ladder protocol
你只负责把已确认规格翻译成紧凑梯级计划；不要重新设计需求。

语义规则：
- 用户已确认的 X/Y 地址、触点极性、参数和 selected_approach.generation_contract 必须原样遵守。
- 不得擅自新增 X/Y、停止/急停、硬件、模块寄存器或特殊软元件；内部状态优先使用普通 M/D/T/C 低位地址。
- 同一普通 Y/M 只保留一个 COIL owner；多个触发条件必须合并到该输出的同一个条件结构。
- 输入 OR 使用 `{"or":[[...],[...]]}` 放在同一 branch 的 `i` 中，不得用多个输出 branch 表达同一输出的 OR。
- OR 的每个分支只能包含简单输入，不允许 OR 嵌套。
- 比较输入直接写前缀表达式，例如 `">= D0 K1"`、`"< D0 K4"`；算术先用应用指令写入寄存器，再比较。
- TIMER 只能写 T，COUNTER 只能写 C；普通定时器必须有可变为 FALSE 的使能/复位路径。
- “每次/按下时只执行一次”使用 P/F/RISING/FALLING 边沿，不用持续电平重复触发。

返回形状只有：`{"r":[rung,...]}`。
- rung: `{"h":可选简单输入或null,"s":可选简单输入数组,"b":[branch,...]}`；通常只需要 `b`。
- branch: `{"i":可选输入数组,"o":[输出字符串,...]}`；无条件时可省略 `i`。
- 简单输入：`"NO X0"`、`"NC M1"`、`"P X2"` 或比较 `"> D0 K3"`。
- OR 输入：`{"or":[["NO M20"],["NO M21"],["NO M22"]]}`；串联条件可写成同一子数组中的多个字符串。
- 标准输出：`"COIL Y0"`、`"PLS M0"`、`"PLF M0"`、`"TIMER T0 K10"`、`"COUNTER C0 K9"`。
- 其他输出字符串首 token 直接作为 APP_INSTR opcode，例如 `"MOV K1 D0"`、`"INC D0"`；后续 token 是 operands。
- 不要输出 branch_id、y_offset_level、rung_id、label、debug_note、device_comments；这些由本地代码确定性补齐。
- 不输出 Markdown、解释、第二份 JSON 或未定义字段。
"""


def _compact_response_schema():
    """Small transport schema; engineering validity remains in existing validators."""
    simple = {"type": "string", "minLength": 1, "maxLength": 160}
    parallel = {
        "type": "object",
        "properties": {
            "or": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "array", "minItems": 1, "items": copy.deepcopy(simple)},
            }
        },
        "required": ["or"],
        "additionalProperties": False,
    }
    branch = {
        "type": "object",
        "properties": {
            "i": {"type": "array", "items": {"oneOf": [copy.deepcopy(simple), parallel]}},
            "o": {"type": "array", "minItems": 1, "items": copy.deepcopy(simple)},
        },
        "required": ["o"],
        "additionalProperties": False,
    }
    rung = {
        "type": "object",
        "properties": {
            "h": {"type": ["string", "null"], "maxLength": 160},
            "s": {"type": "array", "items": copy.deepcopy(simple)},
            "b": {"type": "array", "minItems": 1, "items": branch},
        },
        "required": ["b"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"r": {"type": "array", "minItems": 1, "items": rung}},
        "required": ["r"],
        "additionalProperties": False,
    }


class _FirstJSONObjectStream:
    """Cut a streamed response after its first complete top-level JSON object."""

    def __init__(self):
        self.started = False
        self.finished = False
        self.passthrough = False
        self.in_string = False
        self.escaped = False
        self.stack = []

    def feed(self, text):
        value = str(text or "")
        if not value or self.finished:
            return "", self.finished
        if self.passthrough:
            return value, False

        emitted = []
        for index, char in enumerate(value):
            emitted.append(char)
            if not self.started:
                if char.isspace():
                    continue
                if char != "{":
                    self.passthrough = True
                    emitted.extend(value[index + 1 :])
                    return "".join(emitted), False
                self.started = True
                self.stack.append("}")
                continue

            if self.in_string:
                if self.escaped:
                    self.escaped = False
                elif char == "\\":
                    self.escaped = True
                elif char == '"':
                    self.in_string = False
                continue

            if char == '"':
                self.in_string = True
            elif char == "{":
                self.stack.append("}")
            elif char == "[":
                self.stack.append("]")
            elif char in "}]":
                if not self.stack or char != self.stack[-1]:
                    self.passthrough = True
                    emitted.extend(value[index + 1 :])
                    return "".join(emitted), False
                self.stack.pop()
                if not self.stack:
                    self.finished = True
                    return "".join(emitted), True

        return "".join(emitted), False


class _FirstJSONObjectProvider:
    """Streaming facade that prevents a second complete Agent-B JSON copy."""

    def __init__(self, provider):
        self._provider = provider
        self.profile = getattr(provider, "profile", {})

    def stream(self, request):
        if not request.stream:
            yield from self._provider.stream(request)
            return

        scanner = _FirstJSONObjectStream()
        iterator = iter(self._provider.stream(request))
        try:
            for event in iterator:
                if isinstance(event, TextDelta) and not scanner.passthrough:
                    text, complete = scanner.feed(event.text)
                    if text:
                        yield TextDelta(text)
                    if complete:
                        return
                else:
                    yield event
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()


def _response_options(provider):
    """Constrain only the compact transport object, never the verbose ladder_v1."""
    profile = getattr(provider, "profile", None)
    capabilities = profile.get("capabilities", {}) if isinstance(profile, dict) else {}
    if capabilities.get("structured_output"):
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "confirmed_spec_compact_ladder",
                    "strict": True,
                    "schema": _compact_response_schema(),
                },
            }
        }
    return {"response_format": {"type": "json_object"}}


def _json_object(text):
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
    if raw.endswith("```"):
        raw = raw.rsplit("\n", 1)[0]
    value = json.loads(raw.strip())
    if not isinstance(value, dict):
        raise ValueError("confirmed-spec generation must return one JSON object")
    return value


def _strict_generation_projection(confirmed_spec):
    """Expose structured confirmed facts, not Agent-A implementation prose."""
    projected = public_generation_specification(confirmed_spec) or {}
    selected = projected.get("selected_approach")
    if isinstance(selected, dict):
        selected.pop("name", None)
        selected.pop("description", None)
        selected.pop("generation_guide", None)
    return projected


def _simple_input(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: expected compact input string")
    token = value.strip()
    head, *tail = token.split(maxsplit=1)
    kind = head.upper()
    if kind in _CONTACT_TYPES:
        if not tail or not tail[0] or any(char.isspace() for char in tail[0]):
            raise ValueError(f"{path}: contact requires one address")
        return {"type": kind, "address": tail[0]}
    if kind in _COMPARE_PREFIXES:
        if not tail:
            raise ValueError(f"{path}: comparison requires two operands")
        return {"type": "COMPARE", "expression": token}
    raise ValueError(f"{path}: unsupported compact input {head!r}")


def _branch_input(value, path):
    if isinstance(value, str):
        return _simple_input(value, path)
    if not isinstance(value, dict) or set(value) != {"or"}:
        raise ValueError(f"{path}: input must be a string or one OR block")
    branches = value.get("or")
    if not isinstance(branches, list) or not branches:
        raise ValueError(f"{path}.or: requires at least one branch")
    decoded = []
    for branch_index, branch in enumerate(branches):
        if not isinstance(branch, list) or not branch:
            raise ValueError(f"{path}.or[{branch_index}]: requires at least one simple input")
        decoded.append([
            _simple_input(item, f"{path}.or[{branch_index}][{item_index}]")
            for item_index, item in enumerate(branch)
        ])
    return {"type": "parallel_block", "branches": decoded}


def _output(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: expected compact output string")
    parts = value.strip().split()
    kind = parts[0].upper()
    if kind in {"COIL", "PLS", "PLF"}:
        if len(parts) != 2:
            raise ValueError(f"{path}: {kind} requires one address")
        return {"type": kind, "address": parts[1]}
    if kind in {"TIMER", "COUNTER"}:
        if len(parts) != 3:
            raise ValueError(f"{path}: {kind} requires address and preset")
        return {"type": kind, "address": parts[1], "value": parts[2]}
    if kind == "APP":
        if len(parts) < 2:
            raise ValueError(f"{path}: APP requires an opcode")
        kind, operands = parts[1].upper(), parts[2:]
    else:
        operands = parts[1:]
    if kind in _TYPED_OUTPUTS:
        raise ValueError(f"{path}: malformed typed output")
    return {"type": "APP_INSTR", "opcode": kind, "operands": operands}


def _confirmed_comments(projected):
    comments = {}
    for row in projected.get("io_table", []) if isinstance(projected, dict) else []:
        if not isinstance(row, dict):
            continue
        address = str(row.get("address") or "").strip().upper()
        text = str(row.get("label") or row.get("description") or "").strip()
        if address and text:
            comments[address] = text[:64]
    return comments


def _expand_compact_ladder(compact, projected=None):
    """Deterministically expand Agent B's short protocol into ladder_v1."""
    if not isinstance(compact, dict) or set(compact) != {"r"}:
        raise ValueError("compact ladder must contain only top-level field 'r'")
    rows = compact.get("r")
    if not isinstance(rows, list) or not rows:
        raise ValueError("compact ladder requires at least one rung")

    rungs = []
    for rung_index, row in enumerate(rows, start=1):
        path = f"r[{rung_index - 1}]"
        if not isinstance(row, dict) or not set(row).issubset({"h", "s", "b"}):
            raise ValueError(f"{path}: invalid compact rung fields")
        branches = row.get("b")
        if not isinstance(branches, list) or not branches:
            raise ValueError(f"{path}.b: requires at least one branch")
        shared = row.get("s", [])
        if not isinstance(shared, list):
            raise ValueError(f"{path}.s: expected list")
        header = row.get("h")
        if header is not None:
            header = _simple_input(header, f"{path}.h")

        expanded_branches = []
        for branch_index, branch in enumerate(branches, start=1):
            branch_path = f"{path}.b[{branch_index - 1}]"
            if not isinstance(branch, dict) or not set(branch).issubset({"i", "o"}):
                raise ValueError(f"{branch_path}: invalid compact branch fields")
            inputs = branch.get("i", [])
            outputs = branch.get("o")
            if not isinstance(inputs, list):
                raise ValueError(f"{branch_path}.i: expected list")
            if not isinstance(outputs, list) or not outputs:
                raise ValueError(f"{branch_path}.o: requires at least one output")
            expanded_branches.append({
                "branch_id": branch_index,
                "y_offset_level": branch_index - 1,
                "inputs": [
                    _branch_input(item, f"{branch_path}.i[{index}]")
                    for index, item in enumerate(inputs)
                ],
                "outputs": [
                    _output(item, f"{branch_path}.o[{index}]")
                    for index, item in enumerate(outputs)
                ],
            })

        rungs.append({
            "rung_id": rung_index,
            "header_element": header,
            "shared_inputs": [
                _simple_input(item, f"{path}.s[{index}]")
                for index, item in enumerate(shared)
            ],
            "branches": expanded_branches,
        })

    return {"device_comments": _confirmed_comments(projected or {}), "rungs": rungs}


def _build_agent_b_prompt(projected, plc_model):
    model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    evidence = _build_knowledge_context(
        _GENERATION_REQUEST,
        plc_model=model,
        task_type="generate",
        confirmed_context=projected,
        evidence=None,
    )
    confirmed = json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
    prompt = (
        _COMPACT_PROTOCOL
        + f"\n# Selected PLC\n{model}\n"
        + "\n# Confirmed project specification\n"
        + confirmed
        + evidence
    )
    audit_section("system_prompt", prompt, reason="confirmed_spec_compact_generation", source="application")
    return prompt


def generate_confirmed_ladder(
    confirmed_spec,
    plc_model="FX3U",
    *,
    model_name=None,
    effort=None,
    on_stage=None,
):
    """Make one streaming model call, then locally expand the compact plan."""
    import api

    model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    projected = _strict_generation_projection(confirmed_spec)
    if not projected:
        raise ValueError("confirmed generation specification is empty")

    system_prompt = _build_agent_b_prompt(projected, model)
    if on_stage:
        on_stage("confirmed_spec_generation", "正在根据已确认规格生成梯形图")

    base_provider = api._workflow_provider()
    provider = _FirstJSONObjectProvider(base_provider)
    with api.provider_scope(provider, model_name=model_name):
        response = api._request_model(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _GENERATION_REQUEST},
            ],
            model_name=model_name,
            effort=effort,
            stream=True,
            max_retries=0,
            options=_response_options(provider),
            response_contract=_COMPACT_RESPONSE,
        )
    compact = _json_object(response.message.content)
    return {"ladder": _expand_compact_ladder(compact, projected), "model_calls": 1}

"""Compact, auditable generation prompt assembly for API and MCP clients.

The deterministic PLC validators remain the acceptance boundary. This module
keeps model context task-shaped instead of replaying the full historical prompt:
normal generation gets one semantic kernel plus targeted evidence, contract
repair gets only its immutable repair baseline/scope, and format repair gets no
PLC knowledge context at all.
"""
from __future__ import annotations

import copy
import json
import re
import sys

import application.generation_context_support as _legacy
from knowledge.patterns import KNOWLEDGE_BUNDLES, classify_request, load_library
from shared.context_policy import audit_section, manual_lookup_decision, resolve_context_policy

_engineering_hardware_snapshot = _legacy._engineering_hardware_snapshot
_build_knowledge_query = _legacy._build_knowledge_query
_routing_text_with_selected_approach = _legacy._routing_text_with_selected_approach
_load_plc_models = _legacy._load_plc_models
_build_model_context = _legacy._build_model_context
public_generation_value = _legacy.public_generation_value
public_generation_ladder = _legacy.public_generation_ladder
public_generation_specification = _legacy.public_generation_specification
_KNOWLEDGE_TASK_SETTINGS = _legacy._KNOWLEDGE_TASK_SETTINGS

ST_SYSTEM_PROMPT = """# Role
你是三菱 PLC ST 生成器。把当前确认规格转换为所选 PLC 型号可执行的 ST，并只返回协议 JSON。

# Authority
输出协议 > 本轮明确修改 > 当前确认规格/canonical I/O/selected_approach.generation_contract > 当前型号资料与检索证据 > 本轮匹配的专用控制提示 > 历史上下文。

# ST semantic kernel
- 不得擅自新增 I/O、停止/急停、硬件、模块寄存器或未确认别名；用户明确给出的地址、NO/NC 极性和参数必须保持。
- selected_approach.generation_contract 是硬约束，不得以“功能等价”为由换方案。
- `:=` 赋值；BOOL 只用 TRUE/FALSE；逻辑只用 AND/OR/NOT/XOR；语句以 `;` 结束。
- 比较使用 `> >= < <= = <>`；涉及有符号寄存器语义时按当前平台要求转换。
- 定时器使用 `OUT_T(条件, TCx, 设定值)`/TSx，计数器使用 `OUT_C(条件, CCx, 设定值)`/CSx。
- 普通定时器必须有会变 FALSE 的使能/复位路径；RUN 常 ON 继电器不能单独构成周期振荡。
- “每次/按下时只执行一次”属于事件，应使用边沿；持续成立的条件才使用电平语义。
- 运动、模拟量、通信和特殊软元件必须服从本轮型号资料/手册证据，不复制其他 PLC 家族地址。

# Output
只返回 `{\"st_code\":\"完整 ST 代码\"}`，不得输出 Markdown、分析过程或额外正文。"""

LADDER_SYSTEM_PROMPT = """# Role
你是三菱 PLC ladder_v1 JSON 生成器。把当前确认规格转换为所选 PLC 型号的梯形图 JSON。

# Authority
输出 schema > 本轮明确修改 > 当前确认规格/canonical I/O/selected_approach.generation_contract > 当前型号资料与检索证据 > 本轮匹配的专用控制提示 > 历史上下文。

# Ladder semantic kernel
- 不得擅自新增 I/O、停止/急停、硬件、模块寄存器或未确认别名；用户明确给出的地址、NO/NC 极性和参数必须保持。
- selected_approach.generation_contract 是硬约束；required_* 必须满足，forbidden_* 不得出现。
- 同一普通 Y/M 只保留一个 COIL owner；多条件合并到该输出的条件结构中。
- `shared_inputs` 只放公共串联输入；局部 `parallel_block` 只放在 branch.inputs，且不得嵌套。
- COMPARE 不做算术；先用 APP_INSTR 计算到寄存器再比较。
- 普通 T 定时器必须存在会变 OFF 的使能/复位路径；RUN 常 ON 继电器不能单独构成周期振荡。
- TIMER 只能使用 T，COUNTER 只能使用 C。
- “每次/按下时只执行一次”使用 P/F 边沿语义，不用每扫描重复触发的普通电平触点。
- 运动完成处理必须与对应运动指令保持同 rung 归属，并使用当前型号资料中的完成状态。
- 不得用同扫描互补 `NC Mx -> SET Mx` / `NO Mx -> RST Mx` 模拟翻转。
- OUT 的协议表示：普通 Y/M 用 COIL，T/C 分别用 TIMER/COUNTER；禁止生成 `{\"type\":\"APP_INSTR\",\"opcode\":\"OUT\"`。
- device_comments、label、debug_note 单条不得超过 64 字符。
- 只返回 schema 允许的 JSON，不输出 Markdown、推理过程或解释正文。"""

CONTRACT_REPAIR_SYSTEM_PROMPT = """# Local ladder contract repair
你正在修复一个已经生成但未通过结构校验的 ladder_v1 候选。失败候选是不可变 repair baseline；这不是重新生成任务。

规则：
- 只返回一个可解析 JSON 对象，且 `mode` 必须为 `partial`。
- 只返回用户消息列出的 allowed rung_id 对应的完整替换梯级；不得新增/删除梯级，不得返回完整程序。
- `delete_rung_ids` 必须为空；device_comments 只能修改允许范围内的现有地址。
- 只修复给出的 violation/JSON/结构问题；不得改变控制意图、地址、参数、触点极性或无关梯级。
- repair baseline 中未显示/未允许的内容保持不变，由本地合并器负责保留。
- 后端会拒绝任何 scope escape；不要尝试扩大范围。

返回形状：`{\"mode\":\"partial\",\"rungs\":[...],\"device_comments\":{},\"delete_rung_ids\":[]}`。不得输出解释。"""

FORMAT_REPAIR_SYSTEM_PROMPT = """# JSON format repair
把用户提供的失败候选恢复成且仅恢复成一个可解析的完整 ladder JSON 对象。
只修正 JSON 引号、分隔符、括号、闭合或因截断造成的结构缺口；不得重新分析 PLC 需求，不得主动改变已有控制逻辑、地址、参数或触点极性。
返回完整 ladder JSON；禁止 `mode=partial`，禁止 Markdown 和解释正文。"""


def _st_system_prompt_for_model(plc_model):
    model = str(plc_model or "FX3U").strip().upper() or "FX3U"
    return ST_SYSTEM_PROMPT + f"\n\n# Selected PLC\n{model}。特殊软元件、指令和硬件能力以本轮注入的型号资料/手册证据为准。"


def _is_format_repair(task_type, user_requirement):
    if str(task_type or "").strip().casefold() == "format_repair":
        return True
    return str(user_requirement or "").lstrip().startswith("这是用户明确确认的一次 JSON 格式修复")


def _specialist_context(classification, *, target_mode, plc_model, char_budget=4200):
    route = classification.get("workflow_route") or {}
    bundle_names = route.get("knowledge_bundles") or []
    allowed = {"motion_control", "motion_control_fx5u", "vfd_control", "sfc"}
    parts, used = [], 0

    def add(text):
        nonlocal used
        text = str(text or "").strip()
        if not text:
            return False
        cost = len(text) + (2 if parts else 0)
        if used + cost > char_budget:
            return False
        parts.append(text)
        used += cost
        return True

    for name in bundle_names:
        if name in allowed:
            add(KNOWLEDGE_BUNDLES.get(name, ""))

    policy = resolve_context_policy()
    if policy.examples:
        lib = load_library()
        matched = set(classification.get("matched_ids") or ())
        model = str(plc_model or "").upper()
        candidates = []
        for item in lib.get("examples", ()):
            if item.get("id") not in matched or item.get("target_mode", "ladder") != target_mode:
                continue
            models = [str(value).upper() for value in item.get("plc_models", ["FX3U"])]
            if model and model not in models:
                continue
            candidates.append(item)
        candidates.sort(key=lambda item: (item.get("priority", 999), str(item.get("id", ""))))
        if candidates:
            item = candidates[0]
            add("# Matched example\n" + str(item.get("header", "")).strip() + "\n" + str(item.get("content", "")).strip())

    result = "\n\n".join(parts)
    audit_section("dynamic_prompt", result, status="included" if result else "excluded",
                  reason="specialist_delta" if result else "no_specialist_delta", source="pattern_library")
    audit_section("workflow_prompt", status="excluded", reason="generic_bundles_removed", source="workflow_router")
    return result


def _select_system_prompt(target_mode, is_edit_mode=False, user_requirement="", task_type=None,
                          review_mode=None, plc_model=None, confirmed_context=None):
    forced_task = task_type or review_mode
    if str(forced_task or "").strip().casefold() == "contract_repair":
        audit_section("system_prompt", CONTRACT_REPAIR_SYSTEM_PROMPT, reason="contract_repair", source="api")
        return CONTRACT_REPAIR_SYSTEM_PROMPT
    if _is_format_repair(forced_task, user_requirement):
        audit_section("system_prompt", FORMAT_REPAIR_SYSTEM_PROMPT, reason="format_repair", source="api")
        return FORMAT_REPAIR_SYSTEM_PROMPT

    routing_requirement = _routing_text_with_selected_approach(user_requirement, confirmed_context)
    classification = classify_request(routing_requirement, target_mode=target_mode, is_edit_mode=is_edit_mode)
    route = classification.get("workflow_route") or {}
    selected_vendor = str(plc_model or route.get("vendor") or "").strip()
    if target_mode == "ladder":
        from plc.generation_contract import ladder_response_schema
        base = (LADDER_SYSTEM_PROMPT + "\n\n# Machine-readable output schema (authoritative structure)\n" +
                json.dumps(ladder_response_schema(allow_partial=is_edit_mode, plc_model=selected_vendor), ensure_ascii=False,
                           separators=(",", ":")))
    else:
        base = _st_system_prompt_for_model(selected_vendor)
    audit_section("base_prompt", base,
                  reason="legacy" if resolve_context_policy().legacy else "controlled_baseline",
                  source="base_prompt")
    dynamic = _specialist_context(classification, target_mode=target_mode, plc_model=selected_vendor)
    result = "\n\n".join(part for part in (base, dynamic) if part)
    audit_section("system_prompt", result, reason="compact_generation", source="api")
    return result


def _build_knowledge_context(primary_query, *, plc_model="FX3U", task_type="generate",
                             confirmed_context=None, evidence=None):
    normalized_task = str(task_type or "generate").strip().casefold()
    if normalized_task in {"contract_repair", "format_repair"}:
        audit_section("manual_context", status="excluded", reason="repair_scope_only", source="manual_retriever")
        return ""
    top_k, char_budget = _KNOWLEDGE_TASK_SETTINGS.get(normalized_task, _KNOWLEDGE_TASK_SETTINGS["generate"])
    query = _build_knowledge_query(primary_query, confirmed_context, evidence)
    should_lookup, lookup_reason = manual_lookup_decision(query)
    if (not should_lookup and normalized_task == "analysis" and
            resolve_context_policy().manuals == "adaptive" and query.strip()):
        should_lookup, lookup_reason = True, "analysis_design_retrieval"
    if not should_lookup:
        audit_section("manual_context", status="excluded", reason=lookup_reason, source="manual_retriever")
        return ""
    try:
        from knowledge.retriever import build_knowledge_context as retrieve_context
        context = retrieve_context(query, plc_model=plc_model, task_type=normalized_task,
                                   top_k=top_k, char_budget=char_budget)
    except Exception:
        audit_section("manual_context", status="unavailable", reason="retrieval_failed", source="manual_retriever")
        print("PLC knowledge retrieval unavailable", file=sys.stderr)
        return ""
    if not context:
        audit_section("manual_context", status="empty", reason="no_relevant_results", source="manual_retriever")
        return ""
    result = "\n\n# Retrieved PLC evidence\n" + str(context).strip() + "\n"
    audit_section("manual_context", result, reason=lookup_reason, source="manual_retriever")
    return result


def _confirmed_context_text(confirmed_context):
    if confirmed_context is None:
        return ""
    if isinstance(confirmed_context, str):
        return confirmed_context.strip()
    if isinstance(confirmed_context, dict):
        legacy_context = confirmed_context.get("legacy_context")
        if legacy_context:
            return str(legacy_context).strip()
        clean = {key: value for key, value in confirmed_context.items() if not str(key).startswith("_")}
        return json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    return str(confirmed_context).strip()


def _with_confirmed_context(system_prompt, confirmed_context=None):
    context = _confirmed_context_text(confirmed_context)
    return system_prompt if not context else f"{system_prompt}\n\n# Confirmed project specification\n{context}"


_DEVICE_RE = re.compile(r"(?<![A-Za-z0-9_])(?:SM|SD|X|Y|M|D|T|C|S|V|Z)\d+(?:[VZ]\d+)?(?![A-Za-z0-9_])", re.I)
_RUNG_RE = re.compile(r"(?:rung(?:_id)?|梯级|网络|第)\s*[:#：]?\s*(\d+)", re.I)
_GLOBAL_EDIT_RE = re.compile(r"整体|全部|全局|整套|重构整个|优化整个|rewrite\s+all|refactor\s+all|whole\s+program", re.I)
_CJK_RE = re.compile(r"[\u3400-\u9fff]{2,}")
_ASCII_TERM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


def _rung_addresses(rung):
    text = json.dumps(rung, ensure_ascii=False, separators=(",", ":"))
    return sorted({match.group(0).upper() for match in _DEVICE_RE.finditer(text)})


def _rung_opcodes(rung):
    result = []
    if not isinstance(rung, dict):
        return result
    for branch in rung.get("branches", ()) or ():
        if not isinstance(branch, dict):
            continue
        for output in branch.get("outputs", ()) or ():
            if isinstance(output, dict) and output.get("type") == "APP_INSTR":
                opcode = str(output.get("opcode", "")).strip().upper()
                if opcode and opcode not in result:
                    result.append(opcode)
    return result


def _query_terms(text):
    source = str(text or "")
    terms = {item.casefold() for item in _ASCII_TERM_RE.findall(source)}
    for sequence in _CJK_RE.findall(source):
        if len(sequence) <= 6:
            terms.add(sequence)
        else:
            for width in (2, 3, 4):
                terms.update(sequence[index:index + width] for index in range(len(sequence) - width + 1))
    return {item for item in terms if len(item) >= 2}


def _explicit_repair_rung_ids(text):
    match = re.search(r"允许修改的\s*rung_id\s*[：:]\s*([^\n]+)", str(text or ""), flags=re.I)
    return {int(value) for value in re.findall(r"\d+", match.group(1))} if match else set()


def _current_version_context(user_requirement, current_version_json, *, target_mode="ladder", contract_repair=False):
    if current_version_json is None:
        return "", None
    if target_mode != "ladder" or not isinstance(current_version_json, dict):
        compact = json.dumps(current_version_json, ensure_ascii=False, separators=(",", ":"))
        if len(compact) > 6000:
            compact = compact[:6000] + "…"
        return "# Current version excerpt\n" + compact, current_version_json

    rungs = current_version_json.get("rungs")
    if not isinstance(rungs, list) or not rungs:
        return "", None
    comments = current_version_json.get("device_comments", {})
    comments = comments if isinstance(comments, dict) else {}
    request = str(user_requirement or "")
    explicit_ids = (_explicit_repair_rung_ids(request) if contract_repair else
                    {int(value) for value in _RUNG_RE.findall(request)})
    request_addresses = {match.group(0).upper() for match in _DEVICE_RE.finditer(request)}
    for address, label in comments.items():
        label = str(label or "").strip()
        if len(label) >= 2 and label in request:
            request_addresses.add(str(address).upper())

    terms, exact, ranked = _query_terms(request), set(), []
    for index, rung in enumerate(rungs):
        if not isinstance(rung, dict):
            continue
        rung_id = rung.get("rung_id")
        addresses = set(_rung_addresses(rung))
        exact_hit = ((isinstance(rung_id, int) and not isinstance(rung_id, bool) and rung_id in explicit_ids)
                     or bool(addresses & request_addresses))
        if exact_hit:
            exact.add(index)
        text = json.dumps(rung, ensure_ascii=False, separators=(",", ":")).casefold()
        for address in addresses:
            if address in comments:
                text += " " + str(comments[address]).casefold()
        score = 1000 if exact_hit else sum(1 for term in terms if term in text)
        if score:
            ranked.append((score, index))

    if contract_repair:
        selected = {index for index, rung in enumerate(rungs)
                    if isinstance(rung, dict) and rung.get("rung_id") in explicit_ids}
    elif _GLOBAL_EDIT_RE.search(request):
        selected = set(range(len(rungs)))
    else:
        selected = set(exact)
        for index in tuple(exact):
            if index > 0:
                selected.add(index - 1)
            if index + 1 < len(rungs):
                selected.add(index + 1)
        ranked.sort(key=lambda row: (-row[0], row[1]))
        for _score, index in ranked:
            if len(selected) >= 8:
                break
            selected.add(index)
        if not selected:
            selected = set(range(len(rungs)))

    selected_rungs = [copy.deepcopy(rungs[index]) for index in sorted(selected) if isinstance(rungs[index], dict)]
    selected_addresses = {address for rung in selected_rungs for address in _rung_addresses(rung)}
    if contract_repair and not explicit_ids:
        selected_comments = copy.deepcopy(comments)
    else:
        selected_comments = {address: comments[address] for address in comments
                             if str(address).upper() in selected_addresses}

    if contract_repair:
        payload = {"device_comments": selected_comments, "rungs": selected_rungs}
        text = ("# Immutable repair baseline slice\n"
                "Only backend-authorized repair evidence is shown. Unshown program content remains immutable and local.\n" +
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return text, payload

    rung_index = []
    for rung in rungs:
        if not isinstance(rung, dict):
            continue
        item = {"id": rung.get("rung_id"), "devices": _rung_addresses(rung)}
        opcodes = _rung_opcodes(rung)
        if opcodes:
            item["opcodes"] = opcodes
        rung_index.append(item)
    payload = {"rung_index": rung_index, "device_comments": selected_comments, "rungs": selected_rungs}
    text = ("# Current program edit context\n"
            "The complete baseline remains local. Preserve omitted rungs/comments and prefer mode=\"partial\" for local edits.\n" +
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return text, payload


def build_generation_instructions(user_requirement, *, plc_model, target_mode="ladder", is_edit_mode=False,
                                  task_type=None, review_mode=None, confirmed_context=None,
                                  current_version_json=None, prompt_builder=None, knowledge_builder=None,
                                  profile_builder=None, confirmed_builder=None):
    normalized_task = str(task_type or review_mode or ("edit" if is_edit_mode else "generate")).strip().casefold()
    if _is_format_repair(normalized_task, user_requirement):
        audit_section("model_profile", status="excluded", reason="format_repair", source="model_registry")
        audit_section("manual_context", status="excluded", reason="format_repair", source="manual_retriever")
        audit_section("confirmed_context", status="excluded", reason="format_repair", source="application")
        return FORMAT_REPAIR_SYSTEM_PROMPT

    if normalized_task == "contract_repair":
        current_context, _ = _current_version_context(user_requirement, current_version_json,
                                                       target_mode="ladder", contract_repair=True)
        audit_section("model_profile", status="excluded", reason="contract_repair", source="model_registry")
        audit_section("manual_context", status="excluded", reason="contract_repair", source="manual_retriever")
        audit_section("confirmed_context", status="excluded", reason="contract_repair", source="application")
        result = CONTRACT_REPAIR_SYSTEM_PROMPT + (("\n\n" + current_context) if current_context else "")
        audit_section("system_prompt", result, reason="contract_repair_scoped", source="api")
        return result

    prompt_builder = prompt_builder or _select_system_prompt
    knowledge_builder = knowledge_builder or _build_knowledge_context
    profile_builder = profile_builder or _build_model_context
    confirmed_builder = confirmed_builder or _with_confirmed_context
    selected_prompt = prompt_builder(target_mode, is_edit_mode=is_edit_mode, user_requirement=user_requirement,
                                     task_type=task_type, review_mode=review_mode, plc_model=plc_model,
                                     confirmed_context=confirmed_context)
    current_context, retrieval_evidence = _current_version_context(user_requirement, current_version_json,
                                                                    target_mode=target_mode)
    if retrieval_evidence is None and current_version_json is not None:
        retrieval_evidence = current_version_json
    knowledge_ctx = knowledge_builder(user_requirement, plc_model=plc_model, task_type=normalized_task,
                                      confirmed_context=confirmed_context, evidence=retrieval_evidence)
    system_prompt = confirmed_builder(selected_prompt + profile_builder(
        plc_model, confirmed_context, compact=bool(knowledge_ctx)) + knowledge_ctx, confirmed_context)
    if current_context:
        system_prompt += "\n\n" + current_context
    return system_prompt


def generation_user_input(user_input, *, is_edit_mode=False, target_mode="ladder", repair_mode=False):
    text = str(user_input or "")
    if target_mode == "ladder" and is_edit_mode and not repair_mode:
        return ('优先返回 mode="partial"，只列修改/新增的完整梯级；不要重复输出未修改梯级。\n'
                '用户修改要求：\n' + text)
    return text

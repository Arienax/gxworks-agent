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

from knowledge.patterns import KNOWLEDGE_BUNDLES, classify_request
from shared.context_audit import audit_section

from application.generation_support import _engineering_hardware_snapshot
from application.generation_support import _build_knowledge_query
from application.generation_support import _routing_text_with_selected_approach
from application.generation_support import _load_plc_models
from application.generation_support import _build_model_context
from application.generation_support import public_generation_value
from application.generation_support import public_generation_ladder
from application.generation_support import public_generation_specification
from application.generation_support import _KNOWLEDGE_TASK_TOP_K
from plc.generation_contract import CYCLIC_ASSIGNMENT_PROMPT

ST_SYSTEM_PROMPT = """# Role
按当前规格生成所选 PLC 型号的 ST，只返回协议 JSON。

# Authority
输出协议 > 本轮明确修改 > 当前确认规格/canonical I/O/selected_approach.generation_contract > 当前型号资料与检索证据 > 本轮匹配的专用控制提示 > 历史上下文。

# ST semantic kernel
""" + CYCLIC_ASSIGNMENT_PROMPT + """
- 保持已给地址、电平和参数，不新增 I/O、停止/急停、硬件、模块寄存器或未确认别名；遵守 selected_approach.generation_contract。
- 非保持输出每扫描赋值或用完整 IF/ELSE；缺 ELSE 保留旧值。最高优先条件不得被后写覆盖。
- `:=` 赋值，BOOL 用 TRUE/FALSE，逻辑用 AND/OR/NOT/XOR，语句以 `;` 结束；比较用 `> >= < <= = <>`，符号转换遵守平台要求。
- 定时器用 `OUT_T(条件, TCx, 设定值)`/TSx，计数器用 `OUT_C(条件, CCx, 设定值)`/CSx；普通定时器使能必须能变 FALSE，常 ON 不能形成周期。
- 事件用边沿，持续条件用电平。运动、模拟量、通信、特殊软元件以本型号证据为准。
- 参数遵守完整 st_signature；含 EN 时即使在 IF 内也保留首个 BOOL 参数，不能照搬 Ladder 操作数。
- 首次生成就应用事实中的适用条件、输入域和结果范围；call_available=false 的资料不授予调用能力。保持需求的运算含义，不以取绝对值、截断或补零掩盖越界。
- 字软元件参与比较时先明确有符号/无符号类型，使用已绑定的类型标签；新标签通过声明协议创建，不靠裸地址的隐式解释。保留用户要求的数值含义。
- 编译警告必须按同一事实中的类型、输入域、精度和副作用规则处理；编译无错误不等于这些检查已经通过。幂运算采用当前 CPU 的实际计算规则，不能假设与宿主语言的幂运算相同。

# Output
没有 Actual GXW context 时只返回 `{\"st_code\":\"完整 ST 代码\"}`。提供该上下文时按其 ST candidate v2 契约输出，正文和声明分开，任务取自实际工程。不得输出 Markdown、分析过程或额外正文。"""

LADDER_SYSTEM_PROMPT = """# Role
你是三菱 PLC ladder_v1 JSON 生成器。把当前确认规格转换为所选 PLC 型号的梯形图 JSON。

# Authority
输出 schema > 本轮明确修改 > 当前确认规格/canonical I/O/selected_approach.generation_contract > 当前型号资料与检索证据 > 本轮匹配的专用控制提示 > 历史上下文。

# Ladder semantic kernel
""" + CYCLIC_ASSIGNMENT_PROMPT + """
- NO 检查位=1，NC 检查位=0；停止 active_level=0 时运行允许用 NO，反之用 NC。不得把物理常闭直接翻译成 NC。
- 不得擅自新增 I/O、停止/急停、硬件、模块寄存器或未确认别名；用户明确给出的地址、NO/NC 极性和参数必须保持。
- selected_approach.generation_contract 的结构化 required_* 必须满足，forbidden_* 不得出现；unverified_constraints 保留方案语义，但不升级为额外硬约束，也不覆盖当前参数/I/O。
- 普通 COIL 导通写 TRUE、断开写 FALSE；同一普通 Y/M 一个 COIL，不再增加反条件 COIL“清零”。
- AND 用串联，输入 OR 用 parallel_block，NOT 用 NC。一个输出的完整条件放在同一输入路径；输出 branches 不能表达输入 OR。
- `shared_inputs` 只放公共串联输入；局部 `parallel_block` 只放在 branch.inputs，且不得嵌套。
- COMPARE 不做算术；先用 APP_INSTR 计算到寄存器再比较。
- 普通 T 定时器必须存在会变 OFF 的使能/复位路径；RUN 常 ON 继电器不能单独构成周期振荡。
- TIMER 只能使用 T，COUNTER 只能使用 C。
- “每次/按下时只执行一次”使用 P/F 边沿语义，不用每扫描重复触发的普通电平触点。
- 运动完成处理必须与对应运动指令保持同 rung 归属，并使用当前型号资料中的完成状态。
- 不得用同扫描互补 `NC Mx -> SET Mx` / `NO Mx -> RST Mx` 模拟翻转。
- OUT 的协议表示：普通 Y/M 用 COIL，T/C 分别用 TIMER/COUNTER；禁止生成 `{\"type\":\"APP_INSTR\",\"opcode\":\"OUT\"`。
- device_comments 只写软元件用途短名称，最多 16 字符，不写条件、时序或动作过程；label、debug_note 单条不得超过 64 字符。
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


def _specialist_context(classification, *, target_mode, plc_model, char_budget=None):
    route = classification.get("workflow_route") or {}
    bundle_names = route.get("knowledge_bundles") or []
    allowed = {"motion_control", "motion_control_fx5u", "vfd_control", "sfc"}
    parts = []

    def add(text):
        text = str(text or "").strip()
        if not text:
            return False
        parts.append(text)
        return True

    for name in bundle_names:
        scoped_cpu = {"motion_control": "FX3U", "motion_control_fx5u": "FX5U"}.get(name)
        if scoped_cpu and str(plc_model or "").upper() != scoped_cpu:
            continue
        if name in allowed:
            add(KNOWLEDGE_BUNDLES.get(name, ""))

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
        base = LADDER_SYSTEM_PROMPT + '\nUse the structured output_schema as the authoritative output contract.'
    else:
        base = _st_system_prompt_for_model(selected_vendor)
    audit_section("base_prompt", base, reason="canonical_generation_base", source="base_prompt")
    dynamic = _specialist_context(classification, target_mode=target_mode, plc_model=selected_vendor)
    result = "\n\n".join(part for part in (base, dynamic) if part)
    audit_section("system_prompt", result, reason="compact_generation", source="api")
    return result


def _build_knowledge_context(primary_query, *, plc_model="FX3U", task_type="generate",
                             confirmed_context=None, evidence=None,
                             include_design=False, design_query=None):
    from knowledge.evidence import KnowledgeContext, context_manifest, text_sha256
    from plc.specification.provenance import retrieval_projection

    normalized_task = str(task_type or "generate").strip().casefold()
    def finish(context):
        if normalized_task in {"generate", "edit"}:
            from application.capability_context import augment_generation_knowledge
            meta = getattr(primary_query, "metadata", {})
            requirement = (meta.get("capability_requirement") or meta.get("residual_fact_query") or str(primary_query)) if isinstance(meta, dict) else str(primary_query)
            target = meta.get("target_mode", "ladder") if isinstance(meta, dict) else "ladder"
            context = augment_generation_knowledge(requirement, context, plc_model=plc_model,
                target_mode=target, confirmed_spec=confirmed_context,
                repair_scope=meta.get("repair_scope") if isinstance(meta, dict) else None,
                catalog=meta.get("callable_catalog", ()) if isinstance(meta, dict) else ())
        from shared.model_context import technical_context_text
        return KnowledgeContext(technical_context_text(context), getattr(context, 'manifest', {}))
    # Retrieval executes the caller's choice. Omitted/None never means "infer".
    def absent(status, reason, **metadata):
        audit_section("manual_context", status=status, reason=reason, source="manual_retriever")
        return finish(KnowledgeContext("", {"stage": normalized_task, "status": status,
                                     "reason": reason, "records": [], "plc_model": plc_model, **metadata}))
    if normalized_task in {"contract_repair", "format_repair"}:
        return absent("excluded", "repair_scope_only")
    base_top_k = _KNOWLEDGE_TASK_TOP_K.get(
        normalized_task, _KNOWLEDGE_TASK_TOP_K["generate"]
    )
    engineering = retrieval_projection(confirmed_context)
    if getattr(primary_query, "precompiled", False):
        query = primary_query
    else:
        query = (_build_knowledge_query(engineering, primary_query, evidence) if normalized_task == "generate"
                 else _build_knowledge_query(primary_query, engineering, evidence))
    query_text = str(query or "").strip()
    if normalized_task == "analysis":
        should_lookup = bool(query_text)
        lookup_reason = (
            "analysis_design_retrieval" if include_design
            else "analysis_fact_retrieval"
        )
    elif normalized_task in {"generate", "edit"}:
        from knowledge.analysis_router import has_generation_fact_target
        from plc.device_definitions import device_definition_targets
        metadata = getattr(query, 'metadata', {})
        feature_query = metadata.get('capability_requirement') or query if isinstance(metadata, dict) else query
        should_lookup = bool(query_text) and (has_generation_fact_target(query)
            or bool(device_definition_targets(feature_query, plc_model))
            or metadata.get('target_mode') == 'st')
        lookup_reason = (
            "generation_fact_retrieval"
            if should_lookup else "no_specific_fact_target"
        )
    else:
        should_lookup = bool(query_text)
        lookup_reason = (
            f"{normalized_task}_retrieval" if should_lookup else "empty_query"
        )
    if not should_lookup:
        return absent("excluded", lookup_reason)
    try:
        from knowledge.retriever import build_knowledge_context as retrieve_context
        from knowledge.structured_facts import generation_aspects
        language = getattr(query, 'metadata', {}).get('target_mode')
        aspect_options = ({'aspects': generation_aspects(query, language=language)}
                          if normalized_task in {'generate', 'edit'} else {})
        analysis_options = ({"include_design": bool(include_design), "design_query": design_query}
                            if normalized_task == "analysis" else {})
        context = retrieve_context(query, plc_model=plc_model, task_type=normalized_task,
                                   top_k=base_top_k, language=language, **aspect_options, **analysis_options)
    except Exception as error:
        from knowledge.evidence import retrieval_failure
        from shared.diagnostics import emit
        failure = retrieval_failure(error)
        emit("retrieval_failed", stage="model_request", **failure)
        print("PLC knowledge retrieval unavailable: " + json.dumps(failure, ensure_ascii=True), file=sys.stderr)
        return absent("unavailable", "retrieval_failed", failure=failure)
    manifest = context_manifest(context, stage=normalized_task)
    manifest.update(query_truncated=bool(getattr(query, "truncated", False)),
                    query_sha256=text_sha256(query), plc_model=plc_model)
    result = "\n\n# Retrieved PLC technical content\n" + str(context).strip() + "\n" if context else ""
    result = public_generation_value(result)
    manifest = public_generation_value(manifest)
    manifest["context_sha256"] = text_sha256(result)
    audit_section("manual_context", result, status="included" if result else "empty",
                  reason=lookup_reason, source="manual_retriever")
    return finish(KnowledgeContext(result, manifest))


def _confirmed_context_text(confirmed_context):
    if confirmed_context is None:
        return ""
    if isinstance(confirmed_context, str):
        return confirmed_context.strip()
    if isinstance(confirmed_context, dict):
        legacy_context = confirmed_context.get("legacy_context")
        if legacy_context:
            return str(legacy_context).strip()
        from shared.model_context import technical_value
        clean = technical_value(public_generation_specification(confirmed_context))
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
    from shared.model_context import technical_value
    current_version_json = technical_value(current_version_json)
    if target_mode != "ladder" or not isinstance(current_version_json, dict):
        compact = json.dumps(current_version_json, ensure_ascii=False, separators=(",", ":"))
        return "# Current version source\n" + compact, current_version_json

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
                                  profile_builder=None, confirmed_builder=None, on_context=None,
                                  model_profile=None, wire_history=None):
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
    from application.confirmed_generation_context import (
        build_confirmed_generation_context, project_confirmed_specification, generation_execution_prompt,
    )
    shared_confirmed = (target_mode == "ladder" and normalized_task in {"generate", "edit"}
                        and isinstance(confirmed_context, dict) and bool(confirmed_context))
    if shared_confirmed:
        confirmed_context = project_confirmed_specification(confirmed_context)
        current_version_json = public_generation_ladder(current_version_json)
    current_context, retrieval_evidence = _current_version_context(user_requirement, current_version_json,
                                                                    target_mode=target_mode)
    if retrieval_evidence is None and current_version_json is not None:
        retrieval_evidence = current_version_json
    if shared_confirmed and confirmed_context:
        from application.generation_wire import render_wire_messages
        from plc.specification.provenance import SOURCE_PRECEDENCE

        history = copy.deepcopy(wire_history) if isinstance(wire_history, list) else None

        def confirmed_wire_renderer(
            runtime_spec, evidence_text, generation_request, _current_program,
            context_checkpoint, runtime_history,
        ):
            from application.generation_wire import render_context_checkpoint
            selected = prompt_builder(
                target_mode,
                is_edit_mode=is_edit_mode,
                user_requirement=generation_request,
                task_type=task_type,
                review_mode=review_mode,
                plc_model=plc_model,
                confirmed_context=runtime_spec,
            )
            from shared.model_context import technical_context_units, technical_value, technical_knowledge_coverage
            payload = {
                'context_schema_version': 3, 'plc_model': plc_model,
                'confirmed_spec': technical_value(runtime_spec),
                'target_profile': profile_builder(plc_model, runtime_spec, compact=bool(evidence_text)),
                'knowledge': technical_context_units(evidence_text),
                'knowledge_coverage': technical_knowledge_coverage(evidence_text),
                'current_program': retrieval_evidence,
                'historical_context': render_context_checkpoint(context_checkpoint),
            }
            from plc.generation_contract import generation_output_contract
            payload['output_schema'] = generation_output_contract(
                allow_partial=is_edit_mode, plc_model=plc_model, target_mode=target_mode)['schema']
            system = selected + SOURCE_PRECEDENCE + generation_execution_prompt(
                runtime_spec, evidence_text=evidence_text, task_type=normalized_task, plc_model=plc_model)
            message_history = (runtime_history if isinstance(runtime_history, list) and runtime_history
                else history if history is not None else [{'role': 'user', 'content': generation_request}])
            return {'messages': render_wire_messages(system, [
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, separators=(',', ':'))},
                *message_history])}

        context = build_confirmed_generation_context(
            confirmed_context,
            plc_model,
            user_requirement=user_requirement,
            current_program=current_version_json,
            task_type=normalized_task,
            evidence=retrieval_evidence,
            knowledge_builder=knowledge_builder,
            model_profile=model_profile,
            wire_renderer=confirmed_wire_renderer,
            wire_history=history or [],
        )
        confirmed_context = context.confirmed_spec
        user_requirement = context.generation_request
        knowledge_ctx = context.knowledge_context
        if on_context:
            on_context(copy.deepcopy(context.handoff))
        if context.wire_packet:
            system_prompt = context.wire_packet["messages"][0]["content"]
            execution_prompt = generation_execution_prompt(
                confirmed_context,
                evidence_text=knowledge_ctx,
                task_type=normalized_task,
                plc_model=plc_model,
            )
            audit_section(
                "generation_execution_policy",
                execution_prompt,
                reason="settled_facts",
                source="application",
            )
            from application.generation_wire import GenerationWirePrompt
            return GenerationWirePrompt(system_prompt, context.wire_packet)
    else:
        from knowledge.evidence import KnowledgeQuery
        from application.context_compiler import model_budget
        fact_requirement = user_requirement
        if target_mode == "st" and normalized_task == "generate" and isinstance(confirmed_context, dict):
            fact_requirement = '\n'.join(dict.fromkeys(str(value).strip() for value in
                (confirmed_context.get("summary"), user_requirement) if value and str(value).strip()))
        from application.generation_support import repair_retrieval_scope
        scope = repair_retrieval_scope(user_requirement, current_version_json, plc_model=plc_model)
        scope_meta = {}
        if scope:
            from knowledge.analysis_router import generation_fact_query
            from knowledge.structured_facts import structured_fact_targets
            fact_requirement = generation_fact_query(scope['requirement'] + '\n' + '\n'.join(scope['baseline_terms']),
                                                     confirmed_context, plc_model)
            scope_meta = {"structured_fact_mode": "direct",
                          "structured_fact_targets": structured_fact_targets(fact_requirement, confirmed_context, plc_model=plc_model),
                          "repair_scope": {k: v for k, v in scope.items() if k != "requirement"},
                          "residual_fact_query": fact_requirement}
        if retrieval_evidence is not None and not scope:
            fact_requirement = _build_knowledge_query(fact_requirement, retrieval_evidence)
        language_query = KnowledgeQuery(fact_requirement, precompiled=True, metadata={
            **model_budget(model_profile), **scope_meta, "target_mode": target_mode,
            "hardware_context": copy.deepcopy((confirmed_context or {}).get('hardware_profile') or {}),
            "capability_requirement": fact_requirement})
        knowledge_ctx = knowledge_builder(language_query, plc_model=plc_model, task_type=normalized_task,
                                          confirmed_context=confirmed_context, evidence=retrieval_evidence)
        if on_context:
            from plc.specification.provenance import handoff_snapshot
            from knowledge.evidence import context_manifest
            manifest = context_manifest(knowledge_ctx, stage=normalized_task)
            handoff = handoff_snapshot(project_confirmed_specification(confirmed_context), evidence=manifest,
                                       stage=normalized_task)
            if manifest.get("capability_discovery"):
                handoff["capability_discovery"] = copy.deepcopy(manifest["capability_discovery"])
            on_context(handoff)
    selected_prompt = prompt_builder(target_mode, is_edit_mode=is_edit_mode, user_requirement=user_requirement,
                                     task_type=task_type, review_mode=review_mode, plc_model=plc_model,
                                     confirmed_context=confirmed_context)
    from plc.specification.provenance import SOURCE_PRECEDENCE
    from shared.model_context import technical_context_text, technical_value
    from shared.model_context import technical_context_units, technical_knowledge_coverage
    from application.generation_wire import GenerationWirePrompt, render_wire_messages
    system_prompt = selected_prompt + SOURCE_PRECEDENCE
    if (target_mode == 'ladder' and normalized_task in {'generate', 'edit'}
            and isinstance(confirmed_context, dict) and confirmed_context):
        system_prompt += generation_execution_prompt(confirmed_context, evidence_text=knowledge_ctx,
            task_type=normalized_task, plc_model=plc_model)
    payload = {
        'context_schema_version': 3, 'plc_model': plc_model,
        'confirmed_spec': technical_value(confirmed_context),
        'target_profile': profile_builder(plc_model, technical_value(confirmed_context), compact=bool(knowledge_ctx)),
        'knowledge': technical_context_units(knowledge_ctx),
        'knowledge_coverage': technical_knowledge_coverage(knowledge_ctx),
        'current_program': retrieval_evidence,
    }
    from plc.generation_contract import generation_output_contract
    payload['output_schema'] = generation_output_contract(
        allow_partial=is_edit_mode, plc_model=plc_model, target_mode=target_mode)['schema']
    history = wire_history if isinstance(wire_history, list) else [{'role': 'user', 'content': user_requirement}]
    messages = render_wire_messages(system_prompt, [
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}, *history])
    return GenerationWirePrompt(system_prompt, {'messages': messages})



def generation_user_input(user_input, *, is_edit_mode=False, target_mode="ladder", repair_mode=False):
    text = str(user_input or "")
    if target_mode == "ladder" and is_edit_mode and not repair_mode:
        return ('优先返回 mode="partial"，只列修改/新增的完整梯级；不要重复输出未修改梯级。\n'
                '用户修改要求：\n' + text)
    return text

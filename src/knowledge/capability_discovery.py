# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Functional queries over the existing retriever and source-owned catalogue."""
from __future__ import annotations

import re
from collections.abc import Mapping

from knowledge.evidence import evidence_record


# Concepts expand vocabulary, never map a requirement to a mandated opcode.
FUNCTION_TERMS = (
    ("shift", r"移位|位移|bit\s+shift|word\s+shift", "bit shift left right word shift"),
    ("queue", r"队列|先进先出|工件跟踪|跟踪工件|连续发料|连续工件|(?:同时|连续).{0,12}(?:物料|工件)|(?:物料|工件).{0,12}(?:跟踪|在途|并发)|\bmultiple\s+(?:items|workpieces|objects)\b|queue|fifo|tracking", "FIFO queue bit shift word shift"),
    ("bulk_reset", r"批量.{0,12}(?:清|复位)|(?:清空|清零|清除|复位|初始化).{0,20}(?:范围|连续|全部|所有|数组|状态)|(?:全部|所有|范围|连续|数组).{0,16}(?:清空|清零|清除|复位|初始化)|zone reset|(?:bulk|all|range|array).{0,10}(?:reset|clear|initialize)|(?:clear|reset|initialize).{0,10}(?:bulk|all|range|array)", "zone reset clear range fill move"),
    ("block_move", r"块传送|块复制|批量.{0,6}(?:传送|复制)|连续.{0,8}(?:复制|传送)|数组|block move|array|copy", "block move fill move array"),
    ("timer", r"延时|延迟|计时|倒计时|定时|timer|delay|timing", "timer on delay off delay timing"),
    ("compare", r"比较|区间|大小判断|compare|comparison", "compare zone compare equal greater less"),
    ("loop", r"循环|遍历|迭代|\bloop\b|\biterat\w*\b|\btravers\w*\b", "program flow iteration loop repeat traverse array"),
    ("sequence", r"多阶段|交通灯|顺序控制|状态机|multi.?stage|state machine|sequence control", "state sequence step transition sequential control"),
    ("edge", r"上升沿|下降沿|边沿|每次|一次事件|rising|falling|edge", "rising falling edge pulse trigger"),
    ("rtc", r"实时时钟|RTC|校时|分钟校准|real[ -]?time\s+clock|clock\s+(?:data|setting|write)", "real time clock set RTC data minute correction four digit year"),
    ("scan_clock", r"用户.{0,8}(?:扫描)?时钟|通\s*\d+\s*个?扫描|(?:扫描数|扫描次数).{0,8}时钟|user\s+timing\s+clock|timing\s+pulse|scan\s+clock", "user timing clock timing pulse generation number of scans ON OFF"),
    ("socket_receive", r"接收|读取.{0,12}以太网|socket.{0,24}(?:receiv|read)|receiv.{0,24}(?:socket|connection|data)", "socket communication reading out received data during instruction execution"),
    ("counter_read", r"(?:读取|刷新).{0,16}高速计数|高速计数.{0,24}(?:读取|最新|当前值|即时刷新)|计数器.{0,12}(?:最新|当前值)|即时刷新|(?:read|refresh).{0,24}high[ -]speed\s+counter|counter.{0,16}current\s+value", "high speed counter current value read dedicated instruction immediate refresh"),
    ("counter_range_write", r"(?:写入|设置|修改).{0,20}(?:环形计数|计数范围)|(?:环形计数|ring\s+counter).{0,30}(?:上下限|范围|limits?|range)|(?:write|set).{0,24}(?:counter\s+range|lower.{0,8}upper)", "ring counter lower limit upper limit value write"),
)

_PURPOSE_TERMS = {
    "shift": r"shift|移位", "queue": r"fifo|queue|shift|队列",
    "bulk_reset": r"reset|fill|clear|复位", "block_move": r"block|fill|array|块",
    "timer": r"timer|delay|timing|定时", "compare": r"compar|比较",
    "loop": r"\bloop\b|iterat|travers|循环体|遍历|迭代|程序循环|循环程序",
    "sequence": r"sequence|sequential|state transition|step control|顺序|状态转移",
    "edge": r"edge|trigger|\bPLS\b|\bPLF\b|\bLDP\b|\bLDF\b|边沿",
    "rtc": r"RTC|real[ -]?time\s+clock|clock\s+data",
    "scan_clock": r"timing\s+pulse|user\s+timing\s+clock",
    "socket_receive": r"receiv|SOCRCV",
    "counter_read": r"current\s+value\s+read|ICCNTRD",
    "counter_range_write": r"(?:lower|upper).{0,20}limit|ICRNGWR",
}

# The PLC scan and a closed control loop are execution/process facts, not a
# request for program iteration. Cyclic redundancy is another distinct use of
# "cyclic". Match source meaning rather than excluding individual opcodes.
_NON_ITERATION = re.compile(
    r"循环扫描|扫描循环|闭环|(?:control|closed[ -]?loop)\s+loop|closed[ -]?loop"
    r"|cyclic\s+redundancy|(?:cyclic\s+)?scan(?:ning)?\s+(?:cycle|loop)"
    r"|(?:cycle|loop|cyclic)\s+scan(?:ning)?", re.I)


def _matches_purpose(function, text):
    text = str(text or "")
    if function == "loop" and _NON_ITERATION.search(text):
        return False
    if function == "queue" and (re.search(r"\bwith\s+carry\b", text, re.I)
            or re.search(r"\bFILO\b", text, re.I) and not re.search(r"\bFIFO\b", text, re.I)):
        return False
    return bool(re.search(_PURPOSE_TERMS[function], text, re.I))


def functional_queries(requirement):
    from knowledge.analysis_router import positive_requirement_text
    text = positive_requirement_text(requirement)
    # A periodic scan clock is not program iteration or a time-based timer.
    if re.search(_PURPOSE_TERMS["scan_clock"], text, re.I) or re.search(r"扫描.{0,12}(?:时钟|循环)|循环时钟", text):
        text = _NON_ITERATION.sub("", text)
        text = re.sub(r"循环时钟", "时钟", text)
    return [{"function": name, "query": "PLC " + expansion}
            for name, pattern, expansion in FUNCTION_TERMS
            if re.search(pattern, _NON_ITERATION.sub("", text) if name == "loop" else text, re.I)]


def _instruction_card(opcode, plc_model, record):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode, cpu=plc_model)
    if form is None:
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_st_call(opcode, cpu=plc_model)
    if form is None or not form.spec.supports_cpu(plc_model):
        return None
    opcode = form.opcode
    spec = form.spec
    from plc.instructions import GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES
    if spec.category in GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES:
        return None
    coverage = spec.contract_coverage()
    materials = spec.source_materials
    title = (record.get('section') or opcode) if record.get('content_origin') == 'authored_fact' else (
        materials[0]["document_id"].split(":", 2)[-1] if materials else record.get("section") or opcode)
    return {"id": "instruction:" + opcode, "kind": "instruction", "name": opcode,
            "family": form.base_mnemonic, "purpose": title,
            "interface": [{"name": o.name, "role": o.role, "type": o.data_type,
                           **({"allowed_devices": list(o.allowed_devices)} if o.allowed_devices else {}),
                           **({"word_count": o.word_count} if o.word_count != 1 else {})}
                          for o in spec.operands],
            "execution": spec.execution_form,
            **({"st_call": dict(spec.st_call)} if spec.st_call else {}),
            **({"target_call": DEFAULT_INSTRUCTION_REGISTRY.describe_call(opcode, cpu=plc_model)}
               if spec.st_call and spec.contract_sources else {}),
            "unknown": [k for k, status in coverage.items() if status != "source_verified"],
            "sources": [evidence_record(record)] if record.get("id") else [
                {"manual_id": m["manual_id"], "revision": m["revision"], "pdf_pages": m["pdf_pages"]}
                for m in materials[:1]] or [
                {k: s[k] for k in ("manual_id", "revision", "pdf_page", "target_model") if k in s}
                for s in spec.contract_sources[:1]], "functions": []}


def discover(requirement, *, plc_model, target_mode, explicit_targets=(), catalog=(), retrieve,
             relation_records=(), relation_limit=0):
    queries = functional_queries(requirement)
    candidates, seen, gaps = [], {}, []
    source_records = list(relation_records)

    def add(card, function):
        if not card:
            return
        key = card["id"]
        if key not in seen:
            seen[key] = card
            candidates.append(card)
        if function not in seen[key]["functions"]:
            seen[key]["functions"].append(function)

    for target in explicit_targets or ():
        op = str(target.get("opcode") or "")
        card = _instruction_card(op, plc_model, {})
        if target_mode != "fbd":
            add(card, "explicit")
        if card is None:
            gaps.append({"function": "explicit", "target": op, "reason": "not_available_in_registry_scope"})
    for query in queries:
        from knowledge.evidence import KnowledgeQuery
        scoped_query = KnowledgeQuery(query['query'], metadata={'semantic_function': query['function']})
        hits = retrieve(scoped_query, plc_model=plc_model, task_type="generate",
                        top_k=64, char_budget=200000, source_lanes=("fact", "example"))
        source_records.extend(hits)
        before = set(seen)
        for record in hits:
            op = str(record.get("instruction_opcode") or "").upper()
            if (query['function'] not in record.get('semantic_functions', ())
                    and not _matches_purpose(query["function"], record.get("section"))):
                continue
            if op and target_mode != "fbd":
                card = _instruction_card(op, plc_model, record)
                if card and target_mode == "st" and not card.get("st_call"):
                    card["unknown"].append("language_specific_call_signature")
                add(card, query["function"])
            elif record.get("chunk_type") in {"example_st", "example_csv", "design_pattern"}:
                language = "st" if record["chunk_type"] == "example_st" else "ladder"
                if language == target_mode:
                    add({"id": "pattern:" + str(record["id"]), "kind": "pattern",
                         "name": record.get("section"), "purpose": record.get("section"),
                         "interface": [], "execution": None, "unknown": ["process_applicability"],
                         "sources": [evidence_record(record)], "functions": []}, query["function"])
        if set(seen) == before and not any(query["function"] in c["functions"] for c in candidates):
            gaps.append({"function": query["function"], "reason": "no_scoped_manual_match"})
    entries = catalog.get("nodes", catalog.get("templates", catalog.get("catalog", []))) if isinstance(catalog, Mapping) else catalog
    if isinstance(entries, Mapping):
        entries = list(entries.values())
    for index, entry in enumerate(entries or ()):
        kind = str(entry.get("kind") or "")
        if kind not in {"function", "function_block", "FUNCTION", "FUNCTION_BLOCK"}:
            continue
        name = entry.get("type_name") or entry.get("symbol") or entry.get("name")
        identity = entry.get("template") or entry.get("qualified_name") or name
        # Distinct prototypes and colliding names must not collapse to one owner.
        key = "project:" + str(identity) + ":" + str(entry.get("prototype_offset", index))
        add({"id": key, "kind": "project_callable", "name": name,
             "purpose": entry.get("description") or "当前工程目录中的可调用对象",
             "interface": [{k: p[k] for k in ("name", "role", "side", "data_type", "type") if k in p}
                           for p in entry.get("ports", [])],
             "execution": None, "unknown": ["body_behavior_unverified"],
             "sources": [{"kind": "current_project_catalog", "identity": identity}],
             "functions": []}, "project_catalog")
        card = seen[key]
        if entry.get("description_source"):
            card["sources"].append(dict(entry["description_source"]))
        if name and re.search(r"(?<![A-Za-z0-9_])" + re.escape(str(name)) + r"(?![A-Za-z0-9_])", str(requirement), re.I):
            add(card, "explicit")
        for query in queries:
            # A name alone is not proof of a user FB's behavior. Match the
            # supplied description only; exact calls preserve all owners.
            if _matches_purpose(query["function"], entry.get("description")):
                add(card, query["function"])
    if target_mode == "fbd":
        names = {str(c["name"]).upper() for c in candidates if c["kind"] == "project_callable"}
        for target in explicit_targets or ():
            if str(target.get("opcode") or "").upper() not in names:
                gaps.append({"function": "explicit", "target": target.get("opcode"),
                             "reason": "not_in_current_language_catalog"})
    result = {"version": "functional-discovery-v1", "target_mode": target_mode,
            "functions": [q["function"] for q in queries], "queries": queries,
            "candidates": candidates, "gaps": gaps, "model_calls": 0}
    if relation_limit:
        from knowledge.function_relations import annotate_source_records, expand_relations
        expanded = expand_relations(candidates, annotate_source_records(source_records), plc_model=plc_model,
            target_mode=target_mode, retrieve=retrieve, instruction_card=_instruction_card, limit=relation_limit)
        candidates.extend(expanded.pop("additions"))
        result.update(expanded)
    return result

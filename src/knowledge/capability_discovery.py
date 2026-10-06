"""Functional queries over the existing retriever and source-owned catalogue."""
from __future__ import annotations

import re
from collections.abc import Mapping

from knowledge.evidence import evidence_record


# Concepts expand vocabulary, never map a requirement to a mandated opcode.
FUNCTION_TERMS = (
    ("shift", r"移位|位移|bit\s+shift|word\s+shift", "bit shift left right word shift"),
    ("queue", r"队列|先进先出|工件跟踪|跟踪工件|连续发料|连续工件|queue|fifo|tracking", "FIFO queue bit shift word shift"),
    ("bulk_reset", r"初始化|批量.{0,12}(?:清|复位)|(?:清空|清零|清除|复位).{0,20}(?:范围|连续|全部|所有|数组|状态)|(?:全部|所有|范围|连续|数组).{0,16}(?:清空|清零|清除|复位|初始化)|zone reset|(?:bulk|all|range).{0,10}(?:reset|clear)|(?:clear|reset).{0,10}(?:bulk|all|range)|initialize", "zone reset clear range fill move"),
    ("block_move", r"块传送|块复制|批量.{0,6}(?:传送|复制)|连续.{0,8}(?:复制|传送)|数组|block move|array|copy", "block move fill move array"),
    ("timer", r"延时|计时|倒计时|定时|秒|timer|delay|timing", "timer on delay off delay timing"),
    ("compare", r"比较|区间|大小判断|compare|comparison", "compare zone compare equal greater less"),
    ("loop", r"循环|遍历|迭代|\bloop\b|\biterat\w*\b|\btravers\w*\b", "program flow iteration loop repeat traverse array"),
    ("sequence", r"多阶段|交通灯|顺序控制|状态机|multi.?stage|state machine|sequence control", "state sequence step transition sequential control"),
    ("edge", r"上升沿|下降沿|边沿|每次|一次事件|rising|falling|edge", "rising falling edge pulse trigger"),
)

_PURPOSE_TERMS = {
    "shift": r"shift|移位", "queue": r"fifo|queue|shift|队列",
    "bulk_reset": r"reset|fill|clear|复位", "block_move": r"block|fill|array|块",
    "timer": r"timer|delay|timing|定时", "compare": r"compar|比较",
    "loop": r"\bloop\b|iterat|travers|循环体|遍历|迭代|程序循环|循环程序",
    "sequence": r"sequence|sequential|state transition|step control|顺序|状态转移",
    "edge": r"edge|trigger|\bPLS\b|\bPLF\b|\bLDP\b|\bLDF\b|边沿",
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
    return bool(re.search(_PURPOSE_TERMS[function], text, re.I))


def functional_queries(requirement):
    text = str(requirement or "")
    return [{"function": name, "query": "PLC " + expansion}
            for name, pattern, expansion in FUNCTION_TERMS
            if re.search(pattern, _NON_ITERATION.sub("", text) if name == "loop" else text, re.I)]


def _instruction_card(opcode, plc_model, record):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode, cpu=plc_model)
    if form is None or not form.spec.supports_cpu(plc_model):
        return None
    spec = form.spec
    from plc.instructions import GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES
    if spec.category in GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES:
        return None
    coverage = spec.contract_coverage()
    materials = spec.source_materials
    title = (materials[0]["document_id"].split(":", 2)[-1] if materials else record.get("section") or opcode)
    return {"id": "instruction:" + opcode, "kind": "instruction", "name": opcode,
            "family": form.base_mnemonic, "purpose": title,
            "interface": [{"name": o.name, "role": o.role, "type": o.data_type}
                          for o in spec.operands],
            "execution": spec.execution_form,
            "unknown": [k for k, status in coverage.items() if status != "source_verified"],
            "sources": [evidence_record(record)] if record.get("id") else [
                {"manual_id": m["manual_id"], "revision": m["revision"], "pdf_pages": m["pdf_pages"]}
                for m in materials[:1]], "functions": []}


def discover(requirement, *, plc_model, target_mode, explicit_targets=(), catalog=(), retrieve):
    queries = functional_queries(requirement)
    candidates, seen, gaps = [], {}, []

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
        hits = retrieve(query["query"], plc_model=plc_model, task_type="generate",
                        top_k=64, char_budget=200000, source_lanes=("fact", "example"))
        before = set(seen)
        for record in hits:
            op = str(record.get("instruction_opcode") or "").upper()
            if not _matches_purpose(query["function"], record.get("section")):
                continue
            if op and target_mode != "fbd":
                card = _instruction_card(op, plc_model, record)
                if card and target_mode == "st":
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
    return {"version": "functional-discovery-v1", "target_mode": target_mode,
            "functions": [q["function"] for q in queries], "queries": queries,
            "candidates": candidates, "gaps": gaps, "model_calls": 0}

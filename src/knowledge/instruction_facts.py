"""Task-directed, source-preserving instruction evidence; no model calls.

The catalogue resolves spelling/forms, not manual facts. Keyword matches identify
candidate evidence only. They never certify operand semantics or close a fact gap.
The existing index remains read-only and owns model/source applicability.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping

# These are questions shared by instruction families, not implementation recipes.
FACT_QUESTIONS = {
    "operands": "operand meaning, order, unit / 操作数含义、顺序、单位",
    "operation": "operation and data direction / 操作及数据方向",
    "execution": "execution condition, pulse, continuous enable / 执行条件、脉冲、持续使能",
    "limits": "operand range and boundary behavior / 操作数范围及边界行为",
}
_FACT_TERMS = {
    "operands": re.compile(r"operand|source|destination|data.type|bit|word|操作数|源|目标|单位|数据类型", re.I),
    "operation": re.compile(r"operation|shift|transfer|move|copy|rotate|result|direction|运算|操作|移位|传送|复制|方向|结果", re.I),
    "execution": re.compile(r"execut|pulse|rising|falling|scan|enable|\bOFF\b|\bON\b|执行|脉冲|上升沿|下降沿|扫描|使能", re.I),
    "limits": re.compile(r"range|limit|restrict|overflow|overlap|outside|exceed|maximum|minimum|caution|supported|≤|≥|范围|边界|限制|溢出|重叠|最大|最小", re.I),
}
_OFFICIAL = frozenset({"programming", "positioning", "structured_instruction", "structured_function"})
_VERSION = "instruction-facts-v2-direct-structured"


def _sha(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def instruction_fact_targets(query, confirmed_spec=None):
    """Resolve selected/query opcodes, retaining exact confirmed operands."""
    from knowledge.analysis_router import route_analysis_request
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.specification.approach import normalize_instruction_instances

    spec = confirmed_spec if isinstance(confirmed_spec, Mapping) else {}
    selected = spec.get("selected_approach") or {}
    selected = selected if isinstance(selected, Mapping) else {}
    contract = selected.get("generation_contract")
    contract = contract if isinstance(contract, Mapping) else {}
    preferences = selected.get("implementation_preferences")
    preferences = preferences if isinstance(preferences, Mapping) else {}

    targets = []
    instance_opcodes = set()
    seen_instances = set()

    def add_instance(item, source):
        opcode = str(item.get("opcode") or "").strip().upper()
        operands = [str(value) for value in item.get("operands") or []]
        if not opcode:
            return
        marker = (opcode, tuple(operands))
        if marker in seen_instances:
            return
        seen_instances.add(marker)
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode)
        base = str(getattr(form, "base_mnemonic", opcode)).upper()
        targets.append({
            "opcode": opcode,
            "base_opcode": base,
            "operands": operands,
            "instance_source": source,
        })
        instance_opcodes.add(opcode)

    for item in normalize_instruction_instances(contract.get("instruction_instances")):
        add_instance(item, "generation_contract")
    for item in normalize_instruction_instances(preferences.get("instruction_instances")):
        add_instance(item, "implementation_preferences")

    required = contract.get("required_opcodes") or []
    route = route_analysis_request(
        query, confirmed_context=spec,
        resolve_opcode=DEFAULT_INSTRUCTION_REGISTRY.resolve_form,
    )
    requested = list(required) if isinstance(required, (list, tuple)) else []
    routing_text = re.sub(r"\.(?=\s|$)", " ", route.query_text)
    boundary = r"[A-Za-z0-9_.$@+<>!=\-]"
    requested.extend(op for op in route.opcodes if re.search(
        r"(?<!" + boundary + ")" + re.escape(op) + r"(?!" + boundary + ")", routing_text, re.I))
    # Preserve punctuation-bearing catalogue names; MOV is not a match for $MOV.
    for token in re.findall(r"(?<![A-Za-z0-9_.$@+<>!=\-])[$A-Za-z][A-Za-z0-9_.$@+<>!=\-]*", routing_text):
        if re.search(r"[.$@+<>=!\-]", token) and DEFAULT_INSTRUCTION_REGISTRY.resolve_form(token) is not None:
            requested.append(token)

    seen_generic = set()
    for value in requested:
        if not isinstance(value, str):
            continue
        opcode = value.strip().upper()
        if not re.fullmatch(r"[$A-Z][A-Z0-9_.$@+<>!=\-]{0,63}", opcode):
            continue
        if opcode in instance_opcodes or opcode in seen_generic:
            continue
        seen_generic.add(opcode)
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode)
        base = str(getattr(form, "base_mnemonic", opcode)).upper()
        targets.append({"opcode": opcode, "base_opcode": base})
    return targets


def _is_target(result, target):
    if result.get("manual_type") not in _OFFICIAL:
        return False
    names = {target["opcode"], target["base_opcode"]}
    opcode = str(result.get("instruction_opcode") or "").upper()
    if opcode:
        return opcode in names
    # A mention in body prose is insufficient. Use an instruction's own heading.
    section = str(result.get("section") or "")
    return any(re.search(r"(?<![A-Za-z0-9_.$@+\-])" + re.escape(name) + r"(?![A-Za-z0-9_.$@+\-])", section, re.I)
               for name in names)


def _related_units(seed, plc_model, task_type):
    """Recover same-section/explicit-parent units, never adjacent unrelated pages.

    Older indexes need no migration. Without a stable manual+section identity we
    return nothing, rather than guessing a page window or crossing revisions.
    """
    from knowledge import core
    manual_id, section = seed.get("manual_id"), seed.get("section")
    if not manual_id or not section:
        return []
    path = core._index_path()
    identity = core._index_identity(path)
    if identity[0] == "missing":
        return []
    try:
        connection = core._connection(path, identity)
        schema = core._schema(connection)
        table = schema.get("chunks") or {}
        columns = table.get("columns", ())
        section_column = core._first_column(columns, core._SECTION_COLUMNS)
        id_column = core._first_column(columns, core._CHUNK_ID_COLUMNS)
        if "manual_id" not in columns or not section_column or not id_column:
            return []
        quote = core._quote_identifier
        conditions = [f"{quote(section_column)}=?"]
        args = [manual_id, section]
        # Only actual index relationships are followed. There is no implicit
        # 'next page belongs to the same instruction' rule.
        parent_column = core._first_column(columns, ("parent_chunk_id", "parent_id"))
        if parent_column:
            conditions.append(f"{quote(parent_column)}=?")
            args.append(seed.get("id"))
        sql = (f"SELECT rowid AS _chunk_rowid, * FROM {quote(table['name'])} "
               f"WHERE manual_id=? AND ({' OR '.join(conditions)}) ORDER BY rowid LIMIT 24")
        rows = connection.execute(sql, args).fetchall()
        if parent_column:
            original = core._fetch_chunks(connection, schema, [("id", seed.get("id"))])
            row = original.get(("id", str(seed.get("id"))))
            parent = row[parent_column] if row is not None else None
            if parent:
                rows.extend(core._fetch_chunks(connection, schema, [("id", parent)]).values())
        results = []
        for row in rows:
            value = core._chunk_result(row, {}, path, plc_model, task_type)
            if not value or value.get("manual_id") != manual_id or value.get("manual_type") not in _OFFICIAL:
                continue
            if str(value.get("revision") or "") != str(seed.get("revision") or ""):
                continue
            results.append(value)
        return results
    except (OSError, sqlite3.Error, TypeError, ValueError, KeyError, IndexError):
        return []


def _units(text):
    """Offsets into original text; tables and structured records stay indivisible."""
    markers = list(re.finditer(r"(?m)^\[(?:PAGE|TABLE|STRUCTURED INSTRUCTION RECORD)\b[^\n]*", text))
    starts = sorted({0, *(match.start() for match in markers), len(text)})
    for left, right in zip(starts, starts[1:]):
        block = text[left:right]
        # A table may contain blank lines; never split it into disconnected rows.
        if block.startswith("[TABLE") or block.startswith("[STRUCTURED INSTRUCTION RECORD]"):
            # The record's blank-line separator precedes the original page body.
            cut = block.find("\n\n") if block.startswith("[STRUCTURED INSTRUCTION RECORD]") else -1
            if cut >= 0:
                yield left, left + cut
                left += cut + 2
                block = text[left:right]
            else:
                if block.strip():
                    yield left, right
                continue
        cursor = 0
        for separator in re.finditer(r"\n[ \t]*\n", block):
            if block[cursor:separator.start()].strip():
                yield left + cursor, left + separator.start()
            cursor = separator.end()
        if block[cursor:].strip():
            yield left + cursor, right


def _render_units(result, selected):
    """Preserve source offsets and provenance while grouping selected units.

    Structured contract/step-width facts are supplemental to the original
    manual evidence. They are not candidates for prose/table selection, but
    once a manual block is selected the structured prefix is delivered with it.
    """
    text = str(result.get("text") or "")
    selected = sorted(selected, key=lambda unit: unit[0])
    source_text = "\n\n".join(text[start:end].rstrip() for start, end, _ in selected)

    structured_prefix = ""
    if text.startswith("[STRUCTURED INSTRUCTION RECORD]"):
        cut = text.find("\n\n")
        structured_prefix = (text if cut < 0 else text[:cut]).rstrip()
    if structured_prefix:
        source_text = structured_prefix + ("\n\n" + source_text if source_text else "")

    metadata = ("Manual: " + str(result.get("manual_number") or result.get("manual_id") or result.get("source") or "")
                + "; revision: " + str(result.get("revision") or "unspecified")
                + "; section: " + str(result.get("section") or "") + "\n")
    value = copy.deepcopy(result)
    value.update(
        id=str(result["id"]) + "#facts-" + _sha(source_text)[:12],
        original_id=str(result["id"]), source_text_sha256=_sha(text),
        source_spans=[{"start": start, "end": end} for start, end, _ in selected],
        candidate_fact_categories=sorted({cat for _, _, cats in selected for cat in cats}),
        text=metadata + source_text,
    )
    value["content_sha256"] = _sha(value["text"])
    return value


def _completion_sources(seed, plc_model, task_type):
    """Resolve linked completion devices without broad retrieval.

    The instruction table supplies the linked device identity. Device evidence is
    then fetched through the exact structured-device index; BM25/dense ranking is
    never used to decide what a completion flag means.
    """
    from knowledge import core
    from knowledge.structured_facts import resolve_device_records

    path = core._index_path()
    identity = core._index_identity(path)
    if identity[0] == "missing":
        return []
    try:
        connection = core._connection(path, identity)
        table = core._schema(connection).get("instructions", {})
        if not {"chunk_id", "completion_flags_json"} <= set(table.get("columns", ())):
            return []
        record = connection.execute(
            "SELECT completion_flags_json FROM " + core._quote_identifier(table["name"]) + " WHERE chunk_id=? LIMIT 1",
            (seed.get("id"),)).fetchone()
        flags = json.loads(record[0] or "[]") if record else []
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return []
    if not isinstance(flags, list):
        return []

    results, seen = [], set()
    for flag in dict.fromkeys(
        value for value in flags
        if isinstance(value, str) and re.fullmatch(r"(?:M|SM)[0-9]+", value)
    ):
        if len(seen) >= 8:
            break
        hits = resolve_device_records([flag], plc_model=plc_model, task_type=task_type)
        same_revision = [
            row for row in hits
            if row.get("manual_id") == seed.get("manual_id")
            and row.get("revision") == seed.get("revision")
        ]
        same_manual = [row for row in hits if row.get("manual_id") == seed.get("manual_id")]
        selected = same_revision or same_manual or hits
        for row in selected:
            marker = row.get("id")
            if not marker or marker in seen:
                continue
            seen.add(marker)
            focused = copy.deepcopy(row)
            focused["fact_focus_terms"] = [flag]
            results.append(focused)
            if len(seen) >= 8:
                break
    return results

def _pack_target(results, allowance):
    """Pack definition, tables and cautions together before any top-k truncation.

    Prefer the first ranked manual revision. Different programming syntaxes are
    not mixed merely to fill space. LAYOUT/DIAGRAM are fallbacks when a PROSE or
    TABLE representation exists, not duplicate paragraphs of the same page.
    """
    from knowledge import core
    if not results or allowance <= 0:
        return []
    document_key = lambda value: (value.get("manual_id") or value.get("source") or value.get("id"), value.get("revision"))
    primary = document_key(results[0])
    sources = [r for r in results if document_key(r) == primary]
    candidates = []
    for source_index, result in enumerate(sources):
        text = str(result.get("text") or "")
        page_markers = list(re.finditer(r"(?m)^\[PAGE[^\n]*", text))
        for start, end in _units(text):
            raw = text[start:end]
            focus = result.get("fact_focus_terms", ())
            if focus and not any(re.search(r"(?<![A-Z0-9])" + re.escape(term) + r"(?![A-Z0-9])", raw, re.I) for term in focus):
                continue
            if raw.startswith(("SOURCE:", "[STRUCTURED INSTRUCTION RECORD]")):
                # Source identity is in the citation. Structured extraction can
                # omit operand symbols, so original definition/table wins.
                continue
            markers = [match for match in page_markers if match.start() <= start]
            mode = markers[-1].group() if markers else ""
            table = raw.startswith("[TABLE")
            if not table and ("LAYOUT" in mode or "LADDER/DIAGRAM" in mode):
                priority = 0
            elif table and re.search(r"(?:Operand|Oper-\s*and).*Description|操作数.*含义", raw, re.I|re.S):
                priority = 4
            elif "PROSE" in mode or raw.startswith("[PAGE") and "PROSE" in raw.split("\n",1)[0]:
                priority = 3
            elif table:
                priority = 2
            else:
                priority = 1
            if focus and not result.get("instruction_opcode"):
                priority += 5  # The referenced flag definition precedes opcode examples.
            cats = [key for key, pattern in _FACT_TERMS.items() if pattern.search(raw)]
            candidates.append((source_index, (start, end, cats), priority))
    selected, rendered, covered = {}, {}, set()
    while candidates:
        candidates.sort(key=lambda item: (-item[2], -len(set(item[1][2])-covered),
                                          item[0], item[1][0]))
        source_index, unit, priority = candidates.pop(0)
        # Do not refill with duplicate page-layout renditions after the factual
        # source representations have been considered.
        if priority == 0 and rendered:
            continue
        trial_units = [*selected.get(source_index, []), unit]
        trial = _render_units(sources[source_index], trial_units)
        block_cost = lambda value: len(core._format_result_block(value)) + 40
        cost = sum(block_cost(value) for index, value in rendered.items() if index != source_index) + block_cost(trial)
        if cost > allowance:
            continue
        selected[source_index], rendered[source_index] = trial_units, trial
        covered.update(unit[2])
    return [rendered[index] for index in sorted(rendered)]


def retrieve_instruction_facts(
    query, *, plc_model, task_type, char_budget, candidates=(), targets=None,
    retrieve=None, resolver=None,
):
    """Resolve selected instructions directly, then pack their source evidence.

    ``candidates`` and ``retrieve`` remain accepted for compatibility but are
    deliberately unused: an explicit opcode must never be recovered from broad
    BM25/dense candidate ranking.
    """
    from knowledge import core
    from knowledge.structured_facts import resolve_instruction_records

    target_source = "provided_targets" if targets is not None else "query_references"
    targets = copy.deepcopy(targets if targets is not None else instruction_fact_targets(query))
    resolver = resolver or resolve_instruction_records
    report = {
        "version": _VERSION,
        "targets": targets,
        "questions": dict(FACT_QUESTIONS),
        "target_source": target_source,
        "queries": [],
        "lookups": [],
        "records": [],
        "facts": [],
        "verification": "not_performed",
        "retrieval_mode": "structured_direct",
    }
    if task_type not in {"generate", "edit"} or char_budget <= 0:
        return [], report
    if not targets:
        report["reason"] = "no_instruction_target"
        return [], report

    allowance = max(0, int(char_budget) // max(1, len(targets)))
    groups = []
    companion_seen = set()
    for target in targets:
        report["lookups"].append({
            "kind": "instruction",
            "opcode": target["opcode"],
            "base_opcode": target["base_opcode"],
            "source": "instructions.opcode_norm",
        })
        hits = list(resolver([target], plc_model=plc_model, task_type=task_type))
        seeds = [item for item in hits if _is_target(item, target)]
        sources, seen = [], set()
        for seed in seeds:
            if seed.get("id") in seen:
                continue
            for result in (seed, *_related_units(seed, plc_model, task_type)):
                marker = result.get("id")
                if not marker or marker in seen:
                    continue
                opcode = str(result.get("instruction_opcode") or "").upper()
                if opcode and opcode not in {target["opcode"], target["base_opcode"]}:
                    continue
                seen.add(marker)
                sources.append(result)

        companions = _completion_sources(seeds[0], plc_model, task_type) if seeds else []
        companion_pool = [
            row for row in _pack_target(companions, min(allowance * 2 // 3, 2200))
            if row["id"] not in companion_seen
        ]
        companion_seen.update(row["id"] for row in companion_pool)
        companion_cost = sum(len(core._format_result_block(row)) + 40 for row in companion_pool)
        primary_pool = _pack_target(sources, allowance - companion_cost)
        pool = primary_pool[:1] + companion_pool + primary_pool[1:]
        for value in pool:
            value["fact_kind"] = "instruction"
            value["fact_target"] = target["opcode"]
            value["fact_dimensions"] = list(
                value.get("candidate_fact_categories") or ()
            )
        groups.append(pool)

    results = []
    while any(groups):
        for group in groups:
            if group:
                results.append(group.pop(0))
    report["records"] = [
        {
            key: copy.deepcopy(item[key])
            for key in (
                "id", "original_id", "source_text_sha256", "content_sha256",
                "source_spans", "candidate_fact_categories", "fact_kind",
                "fact_target", "fact_dimensions", "instruction_contract",
                "instruction_step_width", "instruction_instance",
            )
            if key in item
        }
        for item in results
    ]
    return results, report

def delivered_fact_report(report, included_ids):
    """Compatibility view backed by the generic fact-coverage owner."""
    from knowledge.fact_coverage import instruction_report_view
    return instruction_report_view(report, included_ids)


def included_knowledge_ids(text, records=()):
    """Compatibility alias for generic complete-block delivery accounting."""
    from knowledge.fact_coverage import included_evidence_ids
    return included_evidence_ids(text, records)

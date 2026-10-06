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
_VERSION = "instruction-facts-v7-compiled-definitions"

_OPERAND_SLOT_FACETS = (
    ("operand_roles", "role_status", "operand_role_status"),
    ("operand_types", "data_type_status", "operand_type_status"),
    ("operand_order", "symbol_status", "operand_order_status"),
    ("device_classes", "device_class_status", "device_class_status"),
    ("purpose", "purpose_status", "operand_purpose_status"),
)


def _best_status(*values):
    normalized = [str(value or "unresolved") for value in values]
    if "source_verified" in normalized:
        return "source_verified"
    for value in normalized:
        if value not in {"", "unresolved"}:
            return value
    return "unresolved"


def _operand_gap_details(record):
    """Return unresolved operand facts at slot × facet granularity.

    Manual evidence is still packed as whole source units (especially tables),
    but one verified operand facet must not close unrelated facets or positions.
    Slot-level status is preferred; aggregate lane status is a compatibility
    fallback for records produced before slot status was materialized.
    """
    record = record if isinstance(record, Mapping) else {}
    from plc.instruction_semantics import bind_operand_slots, operand_usage_status
    operand = record.get("operand_semantics") or {}
    target = record.get("target_applicability") or {}
    slots = record.get("operand_slots")

    if isinstance(slots, list) and slots:
        gaps = []
        for raw_slot in slots:
            if not isinstance(raw_slot, Mapping):
                continue
            position = raw_slot.get("position")
            for facet, slot_key, _aggregate_key in _OPERAND_SLOT_FACETS:
                status = (
                    operand_usage_status(raw_slot.get("usage_facts") or (), "purpose")
                    if facet == "purpose" else str(raw_slot.get(slot_key) or "unresolved")
                )
                if status == "source_verified":
                    continue
                gap = {
                    "position": position,
                    "facet": facet,
                    "status": status,
                }
                for key in ("symbol", "name"):
                    if raw_slot.get(key):
                        gap[key] = str(raw_slot[key])
                if any(item.get("facet") == facet for item in raw_slot.get("usage_conflicts") or ()):
                    gap["reason"] = "candidate_conflict"
                gaps.append(gap)
            for facet in sorted({
                item.get("facet") for item in [*raw_slot.get("usage_facts", []), *raw_slot.get("usage_conflicts", [])]
                if isinstance(item, Mapping) and item.get("facet")
                and item.get("facet") != "purpose"
            }):
                status = operand_usage_status(raw_slot.get("usage_facts") or (), facet)
                if status != "source_verified":
                    gaps.append({
                        "position": position, "facet": facet, "status": status,
                        **{key: str(raw_slot[key]) for key in ("symbol", "name") if raw_slot.get(key)},
                    })
        return gaps

    if (
        target.get("min_operands") == 0
        and target.get("max_operands") == 0
        and target.get("operand_order_status") == "source_verified"
    ):
        return []

    compatibility_slots = bind_operand_slots(operand, target)
    if compatibility_slots:
        return _operand_gap_details({**record, "operand_slots": compatibility_slots})

    definitions = list(operand.get("operands") or ())
    symbols = list(target.get("native_operand_order") or ())
    count = max(len(definitions), len(symbols))
    aggregate_status = {
        "operand_roles": _best_status(
            operand.get("operand_role_status"),
            target.get("operand_role_status"),
        ),
        "operand_types": _best_status(
            operand.get("operand_type_status"),
            target.get("operand_type_status"),
        ),
        "operand_order": str(target.get("operand_order_status") or "unresolved"),
        "device_classes": str(target.get("device_class_status") or "unresolved"),
        # A signature/role promotion carries no operand-purpose evidence.
        "purpose": "unresolved",
    }

    gaps = []
    positions = range(1, count + 1) if count else (None,)
    for position in positions:
        definition = (
            definitions[position - 1]
            if position is not None and position <= len(definitions)
            and isinstance(definitions[position - 1], Mapping)
            else {}
        )
        symbol = (
            symbols[position - 1]
            if position is not None and position <= len(symbols)
            else None
        )
        for facet, _slot_key, _aggregate_key in _OPERAND_SLOT_FACETS:
            status = aggregate_status[facet]
            if status == "source_verified":
                continue
            gap = {"position": position, "facet": facet, "status": status}
            if symbol:
                gap["symbol"] = str(symbol)
            if definition.get("name"):
                gap["name"] = str(definition["name"])
            gaps.append(gap)
    return gaps


def _manual_fact_gaps(record):
    """Return coarse manual lanes derived from fine-grained structured gaps."""
    record = record if isinstance(record, Mapping) else {}
    gaps = set(FACT_QUESTIONS)
    target = record.get("target_applicability") or {}

    if not _operand_gap_details(record):
        gaps.discard("operands")
    if target.get("boundary_status") == "source_verified":
        gaps.discard("limits")

    # Operation/execution remain manual gaps until dedicated structured owners
    # prove those dimensions; a mnemonic or canonical-op label is insufficient.
    return frozenset(gaps)


def _structured_fact_dimensions(record):
    if record.get('definition_fact_groups') or record.get('structured_lanes_delivered') is False:
        # These packets account actual compiled facts independently. Detached
        # legacy lane metadata is not proof that the prompt contains that lane.
        return frozenset()
    return frozenset(FACT_QUESTIONS) - _manual_fact_gaps(record)


def _needs_completion_manual(record):
    runtime = record.get("runtime_semantics") if isinstance(record, Mapping) else None
    completion = runtime.get("completion") if isinstance(runtime, Mapping) else None
    return not (
        isinstance(completion, Mapping)
        and completion.get("status") == "source_verified"
    )


def _model_structured_prefix(text):
    """Keep model-facing structured lanes, never runtime-only step widths."""
    raw = str(text or "")
    if not raw.startswith((
        "[STRUCTURED INSTRUCTION RECORD]",
        "[STRUCTURED LOCAL INSTRUCTION RECORD]",
    )):
        return ""
    cut = raw.find("\n\n")
    prefix = raw if cut < 0 else raw[:cut]
    return "\n".join(
        line
        for line in prefix.splitlines()
        if line.strip()
        and not (raw.startswith("[STRUCTURED INSTRUCTION RECORD]") and line.startswith("OPERANDS:"))
        and not line.startswith((
            "STEP_WIDTH:",
            "STEP_WIDTH_SOURCE:",
            "STEP_WIDTH_REASON:",
        ))
    ).rstrip()


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
    requested.extend(item.get('opcode') for item in spec.get('operation_intents', [])
                     if isinstance(item, Mapping) and item.get('status') == 'confirmed' and item.get('opcode'))
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
            value["manual_text"] = str(core._row_value(row, core._TEXT_COLUMNS, value["text"]))
            results.append(value)
        return results
    except (OSError, sqlite3.Error, TypeError, ValueError, KeyError, IndexError):
        return []


def _units(text):
    """Offsets into original text; tables and structured records stay indivisible."""
    markers = list(re.finditer(
        r"(?m)^\[(?:PAGE|TABLE|STRUCTURED(?: LOCAL)? INSTRUCTION RECORD)\b[^\n]*",
        text,
    ))
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


def _visual_operand_sequence(raw, opcode, expected):
    """Read only diagram-like operand placeholders near this opcode.

    Concrete examples such as TADD D 10 D 20 D 30 are deliberately ignored:
    a placeholder participates only when it is attached to a glyph, is a compact
    n/N count token next to such placeholders, or occupies a short visual row.
    """
    text = str(raw or "")
    opcode = str(opcode or "").strip().upper()
    expected = [str(value or "").strip().upper() for value in expected or ()]
    if not opcode or len(expected) < 2:
        return []

    boundary = r"[A-Za-z0-9_.$@+<>!=\-]"
    opcode_re = re.compile(
        r"(?<!%s)%s(?!%s)" % (boundary, re.escape(opcode), boundary),
        re.I,
    )
    operand_re = re.compile(r"(?i)(?<![A-Z0-9])([SDMN]\d{0,3})(?![A-Z0-9])")
    glyph_operand_re = re.compile(
        r"(?i)([SDM]\d{0,3})\s*(?=\[GLYPH-[0-9A-F]+\])"
    )
    fused_count_re = re.compile(
        r"(?i)(?<![A-Z0-9])(n\d{0,3})(?=[SDM]\d{0,3}\s*\[GLYPH-[0-9A-F]+\])"
    )
    expected_set = set(expected)

    lines = text.splitlines()
    offsets, cursor = [], 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line) + 1

    fallback = []
    for match in opcode_re.finditer(text):
        line_index = max(
            (index for index, offset in enumerate(offsets) if offset <= match.start()),
            default=0,
        )
        line = lines[line_index] if lines else ""
        relative = match.start() - offsets[line_index] if offsets else 0
        # A mnemonic embedded in ordinary prose ("dead band") is not syntax.
        if line[:relative].strip() and not re.search(
            r"(?:FNC\s*\d+|input)\s*$", line[:relative], re.I
        ):
            continue

        region_lines = lines[line_index:line_index + 4]
        region = "\n".join(region_lines)
        if "[GLYPH-" not in region:
            continue

        positions = []
        # Glyph-bound placeholders are the strongest signal.
        for item in glyph_operand_re.finditer(region):
            positions.append((item.start(1), item.group(1).upper()))
        # PDF extraction commonly fuses count placeholders with the next glyph
        # operand: nS, nD1, n1n2D. Recover those counts without treating D10,
        # M3, etc. from concrete examples as placeholders.
        for item in fused_count_re.finditer(region):
            positions.append((item.start(1), item.group(1).upper()))
        # Standalone n/N tokens on the opcode line are also visual operands.
        opcode_line_tail = line[relative + len(opcode):]
        for item in operand_re.finditer(opcode_line_tail):
            token = item.group(1).upper()
            if token.startswith("N"):
                positions.append((item.start(1), token))

        # Very short single-token rows adjacent to glyph syntax can represent
        # a displaced placeholder (for example GBIN's S on the next row).
        base_offset = len(region_lines[0]) + 1 if region_lines else 0
        running = base_offset
        for short_line in region_lines[1:]:
            stripped = short_line.strip()
            if re.fullmatch(r"(?i)[SDMN]\d{0,3}", stripped):
                positions.append((running + short_line.find(stripped), stripped.upper()))
            running += len(short_line) + 1

        sequence = []
        for _position, token in sorted(positions):
            if token in expected_set and token not in sequence:
                sequence.append(token)
        if len(sequence) < 2:
            continue
        indexes = [expected.index(token) for token in sequence]
        if any(left >= right for left, right in zip(indexes, indexes[1:])):
            return sequence
        if not fallback:
            fallback = sequence
    return fallback


def _conflicts_with_verified_operand_order(
    result, raw, *, expected_order=None, opcodes=None,
):
    """True when manual visual residue contradicts a verified native order."""
    result = result if isinstance(result, Mapping) else {}
    target = result.get("target_applicability") or {}
    if expected_order is None:
        if target.get("operand_order_status") != "source_verified":
            return False
        expected = list(target.get("native_operand_order") or ())
    else:
        expected = [str(value or "").strip().upper() for value in expected_order]
    if len(expected) < 2:
        return False

    names = [
        str(value or "").strip().upper()
        for value in (
            opcodes
            if opcodes is not None
            else (
                result.get("instruction_opcode"),
                target.get("opcode"),
                target.get("base_mnemonic"),
            )
        )
        if str(value or "").strip()
    ]
    for opcode in dict.fromkeys(names):
        sequence = _visual_operand_sequence(raw, opcode, expected)
        if len(sequence) < 2:
            continue
        indexes = [expected.index(token) for token in sequence if token in expected]
        if any(left >= right for left, right in zip(indexes, indexes[1:])):
            return True
    return False


def _embedded_instruction_layout_offset(raw):
    """Locate PDF diagram residue accidentally flattened into a PROSE unit.

    High-fidelity pages already carry layout/diagram representations separately.
    A suffix beginning with an FNC label and containing several glyph placeholders
    is visual syntax, not a second prose statement of operand order. Splitting it
    here preserves exact source offsets while letting the normal layout fallback
    policy keep it out when prose/table evidence is available.
    """
    text = str(raw or "")
    first_line = text.split("\n", 1)[0]
    if not first_line.startswith("[PAGE") or "PROSE" not in first_line:
        return None
    matches = list(re.finditer(r"(?m)^FNC\s+\d+\s*$", text))
    for match in reversed(matches):
        tail = text[match.start():]
        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        if (
            tail.count("[GLYPH-") >= 2
            and len(lines) >= 5
            and sum(len(line) <= 48 for line in lines) >= 4
        ):
            return match.start()
    return None


def _operand_dimension(position, facet):
    return f"operand:{position if position is not None else 'unknown'}:{facet}"


def _operand_table_units(text):
    """Recover complete operand tables, including ones flattened into LAYOUT.

    A layout suffix can contain conflicting visual syntax. Only its explicit
    operand-description table and following notes are used as a source unit.
    """
    header = re.compile(
        r"(?im)^[^\n]*(?:Operand|Variable|操作数)[^\n]*\|[^\n]*(?:Description|含义|说明)[^\n]*$"
    )
    for start, end in _units(text):
        raw = text[start:end]
        for match in header.finditer(raw):
            left = start + match.start()
            if raw.startswith("[TABLE"):
                yield start, end
                break
            # Keep footnotes with the table, stop before a new numbered section
            # or a different table header. Never infer a neighboring page.
            tail = text[left:]
            boundary = re.search(
                r"(?im)^(?:\d+\.\s+|(?:Bit Devices|Word Devices)\s*\||\[PAGE|\[TABLE)",
                tail,
            )
            right = left + boundary.start() if boundary else len(text)
            if right > left:
                yield left, right


def _render_units(result, selected, *, bindings=(), conflicts=(), structured_owner=None, relation_groups=()):
    """Preserve source offsets and provenance while grouping selected units.

    Structured instruction facts are supplemental to the original
    manual evidence. They are not candidates for prose/table selection, but
    once a manual block is selected the structured prefix is delivered with it.
    """
    text = str(result.get("text") or "")
    selected = sorted(selected, key=lambda unit: unit[0])
    source_text = "\n\n".join(text[start:end].rstrip() for start, end, _ in selected)
    for group in relation_groups:
        marker = {key: group[key] for key in ("id", "status", "representation")}
        source_text += ("\n\n" if source_text else "") + (
            "[RELATION EVIDENCE " + json.dumps(marker, sort_keys=True) + "]\n" + group["text"] + "\n[/RELATION EVIDENCE]")

    structured_prefix = _model_structured_prefix(text)
    value = copy.deepcopy(result)
    if bindings or conflicts:
        from knowledge.structured_facts import _instruction_lane_prompt_lines
        from plc.instruction_semantics import attach_operand_usage
        owner = structured_owner or result
        lanes = {key: copy.deepcopy(owner.get(key) or {}) for key in (
            "operand_semantics", "target_applicability", "runtime_semantics",
        )}
        lanes["operand_slots"] = attach_operand_usage(
            owner.get("operand_slots") or [], bindings, conflicts=conflicts,
        )
        value.update(lanes)
        rendered_lanes = "\n".join(_instruction_lane_prompt_lines(lanes))
        if structured_prefix:
            structured_prefix = "\n".join(
                line for line in structured_prefix.splitlines()
                if not line.startswith(("OPERAND_SEMANTICS:", "TARGET_APPLICABILITY:", "RUNTIME_SEMANTICS:"))
            )
            structured_prefix += "\n" + rendered_lanes
        else:
            structured_prefix = "[STRUCTURED INSTRUCTION RECORD]\n" + rendered_lanes
    if structured_prefix:
        source_text = structured_prefix + ("\n\n" + source_text if source_text else "")

    metadata = ("Manual: " + str(result.get("manual_number") or result.get("manual_id") or result.get("source") or "")
                + "; revision: " + str(result.get("revision") or "unspecified")
                + "; section: " + str(result.get("section") or "") + "\n")
    value.update(
        id=str(result["id"]) + "#facts-" + _sha(source_text)[:12],
        original_id=str(result["id"]), source_text_sha256=_sha(text),
        source_spans=[{"start": start, "end": end} for start, end, _ in selected],
        candidate_fact_categories=sorted({cat for _, _, cats in selected for cat in cats}),
        text=metadata + source_text,
    )
    value["operand_evidence_bindings"] = copy.deepcopy(list(bindings))
    value["operand_candidate_conflicts"] = copy.deepcopy(list(conflicts))
    value["relation_evidence_groups"] = copy.deepcopy(list(relation_groups))
    value["fact_group_members"] = {dimension: [m["output"] for m in group["members"]]
                                   for group in relation_groups for dimension in group["dimensions"]}
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

def _packing_cost(records):
    from knowledge import core
    from knowledge.evidence import estimate_tokens

    blocks = [core._format_result_block(record) for record in records]
    return (sum(len(block) + 40 for block in blocks),
            sum(estimate_tokens('Reference role: technical_reference\n' + block) + 1 for block in blocks))


def _within_packing_budget(records, allowance, token_allowance=None):
    chars, tokens = _packing_cost(records)
    return chars <= allowance and (token_allowance is None or tokens <= token_allowance)


def _pack_definition_target(sources, allowance, owner, needed, order, opcodes, diagnostics,
                            token_allowance=None, include_uninterpreted=True):
    """Pack compiled dependency closures before unresolved original evidence."""
    from knowledge import core
    from plc.instruction_definition import instruction_task_view
    definition = owner['instruction_definition']
    view = instruction_task_view(definition, questions=needed)
    facts = {g['id']: g for g in view['groups']}
    # A candidate source from another index/revision must not be attached just
    # because its opcode happens to match. Registry declarations stay usable.
    source_scopes = {(str(r.get('original_id') or r['id']), r.get('manual_id'), r.get('revision')) for r in sources}
    packed, packed_ids, relations = [], set(), []
    bundles = sorted(view['bundles'], key=lambda b: (not facts[b['root']]['dimension'].startswith('effects.'), b['root']))
    for bundle in bundles:
        if bundle['root'] in packed_ids:
            continue
        groups = [facts[identity] for identity in bundle['fact_ids']]
        if any(g['status'] == 'candidate_evidence' and not any(
                (str(s.get('id')), s.get('manual_id'), s.get('revision')) in source_scopes for s in g['sources'])
               for g in groups):
            view['gaps'].append({'root': bundle['root'], 'reason': 'source_not_in_current_index'})
            continue
        marker = {'opcode': definition['opcode'], 'target_model': definition['target_model'],
                  'root': bundle['root'], 'source_verification':
                  'source_checked' if all(g['status'] == 'source_verified' for g in groups) else 'not_complete'}
        text = '[INSTRUCTION FACTS ' + json.dumps(marker, ensure_ascii=False) + ']\n'
        text += json.dumps({'groups': [{k: g[k] for k in ('id', 'dimension', 'value', 'status', 'depends_on')}
                                      for g in groups]}, ensure_ascii=False, separators=(',', ':'))
        text += '\n[/INSTRUCTION FACTS]'
        dimensions = ['definition.' + g['id'] for g in groups]
        group_members = {'definition.' + g['id']: list(g['members']) for g in groups}
        root = facts[bundle['root']]
        relation_groups = []
        if root['value'].get('behavior') == 'conditional_results':
            lifecycle = [g['value'] for g in groups if g['dimension'].startswith('execution.')]
            retain = any(v.get('disabled') == 'retain' or v.get('action') == 'retain' for v in lifecycle)
            outputs = root['value']['outputs']
            relation = {'id': root['id'], 'status': root['status'], 'representation': 'compiled_definition',
                        'source': copy.deepcopy(root['sources'][0]),
                        'members': [{**copy.deepcopy(o), 'output': o.get('output') or (o['target']['parameter'] +
                                     ('+' + str(o['target']['offset']) if o['target'].get('offset') else '')),
                                     'target': o['target'], 'expression': o['expression']} for o in outputs],
                        'dimensions': ['operation.result_mapping', *(['execution.disabled_retention'] if retain else [])],
                        'dependencies': copy.deepcopy(groups), 'text': root['value'].get('text') or
                        json.dumps({'outputs': outputs, 'lifecycle': lifecycle}, ensure_ascii=False)}
            relation_groups.append(relation)
            dimensions.extend(relation['dimensions'])
            group_members.update({dim: [m['output'] for m in relation['members']] for dim in relation['dimensions']})
            text += '\n[RELATION EVIDENCE ' + json.dumps({k: relation[k] for k in ('id', 'status', 'representation')}) + \
                    ']\n' + relation['text'] + '\n[/RELATION EVIDENCE]'
        record = copy.deepcopy(sources[0])
        record.update(id=str(sources[0]['id']) + '#definition:' + bundle['root'], text=text,
                      fact_dimensions=dimensions, candidate_fact_categories=[], fact_group_members=group_members,
                      definition_fact_groups=copy.deepcopy(groups), relation_evidence_groups=relation_groups,
                      operand_evidence_bindings=[{'position': g['value']['position'], 'facet': g['value']['facet'],
                          'symbol': g['value']['symbol'], 'value': g['value']['value'], 'status': g['status'],
                          'source': g['sources'][0], 'fact': {'facet': g['value']['facet'], 'value': g['value']['value'],
                          'status': g['status'], 'sources': g['sources']}}
                          for g in groups if g['value'].get('facet') and g['value'].get('position')],
                      content_sha256=_sha(text))
        if not _within_packing_budget([*packed, record], allowance, token_allowance):
            continue
        packed.append(record)
        packed_ids.update(bundle['fact_ids'])
        relations.extend(relation_groups)
    view['receipt']['packed_fact_ids'] = sorted(packed_ids)
    if diagnostics is not None:
        diagnostics.update(candidate_groups=[copy.deepcopy(g) for g in relations],
                           packed_group_ids=[g['id'] for g in relations],
                           packing_status='packed' if relations else 'candidate_conflict' if any(
                               g['status'] == 'conflict' and g['dimension'].startswith('effects.')
                               for g in definition['facts']) else 'budget_omitted' if any(
                               g['value'].get('behavior') == 'conditional_results' for g in view['groups']) else 'no_complete_group',
                           definition_requirements=view['requirements'], definition_task_view=view)
    # The original units are still evidence for unresolved material, with the
    # original order-conflict filter. They do not acquire interpreted meanings.
    raw_sources = []
    for source in sources:
        raw = copy.deepcopy(source)
        raw['text'] = str(source.get('manual_text') or source.get('text') or '')
        if raw['text'].startswith('[STRUCTURED'):
            raw['text'] = raw['text'].split('\n\n', 1)[-1]
        raw['instruction_definition'] = {}
        raw['structured_lanes_delivered'] = False
        raw['definition_conflict_spans'] = [s.get('context_span') or s.get('row_span') for g in definition['facts']
            if g['status'] == 'conflict' for candidate in g['value'].get('candidates', [])
            for s in candidate.get('sources', []) if str(s.get('id')) == str(source.get('original_id') or source['id'])
            and (s.get('context_span') or s.get('row_span'))]
        raw_sources.append(raw)
    cost, token_cost = _packing_cost(packed)
    raw_records = _pack_target(raw_sources, max(0, allowance - cost) if include_uninterpreted else 0, needed_categories=needed,
                              verified_operand_order=order, verified_opcodes=opcodes,
                              token_allowance=max(0, token_allowance - token_cost)
                              if token_allowance is not None else None)
    if not packed:
        return raw_records
    # One instruction packet remains atomic through the final Context Compiler.
    # Its manifest retains original raw-unit locations separately from facts.
    result = packed[0]
    result['text'] = '\n\n'.join(r['text'] for r in packed)
    result['definition_fact_groups'] = list({g['id']: g for r in packed for g in r['definition_fact_groups']}.values())
    result['operand_evidence_bindings'] = [b for r in packed for b in r['operand_evidence_bindings']]
    result['relation_evidence_groups'] = [g for r in packed for g in r['relation_evidence_groups']]
    result['fact_dimensions'] = sorted({d for r in packed for d in r['fact_dimensions']})
    result['fact_group_members'] = {d: members for r in packed for d, members in r['fact_group_members'].items()}
    result['definition_task_view'] = copy.deepcopy(view)
    from plc.instruction_semantics import attach_operand_usage
    conflicts = [{'position': g['value']['position'], 'facet': g['value']['facet'], 'status': 'unresolved',
                  'reason': 'candidate_conflict', 'candidates': copy.deepcopy(g['value']['candidates'])}
                 for g in definition['facts'] if g['status'] == 'conflict' and 'position' in g['value']]
    slots = copy.deepcopy(owner.get('operand_slots', []))
    for slot in slots:
        slot['usage_facts'] = []
        slot['usage_conflicts'] = []
        slot['purpose_status'] = 'unresolved'
    result['operand_slots'] = attach_operand_usage(slots, result['operand_evidence_bindings'], conflicts=conflicts)
    result['operand_candidate_conflicts'] = conflicts
    if conflicts:
        result['text'] += '\n' + json.dumps({'usage_conflicts': [{'position': c['position'], 'facet': c['facet'],
                                                               'reason': c['reason']} for c in conflicts]})
    if not _within_packing_budget([result], allowance, token_allowance):
        view['receipt']['packed_fact_ids'] = []
        view['gaps'].append({'reason': 'packet_budget_omitted'})
        if diagnostics is not None:
            diagnostics.update(packed_group_ids=[], packing_status='budget_omitted', definition_task_view=view)
        return raw_records
    result['raw_evidence_sources'] = []
    delivered_raw = []
    for raw in raw_records:
        trial = copy.copy(result)
        trial['text'] += '\n\n[UNINTERPRETED SOURCE ' + json.dumps({'id': raw.get('original_id'),
                            'manual_id': raw.get('manual_id'), 'source_spans': raw.get('source_spans')}) + ']\n' + raw['text']
        if not _within_packing_budget([trial], allowance, token_allowance):
            view['gaps'].append({'reason': 'uninterpreted_source_budget_omitted', 'source_id': raw.get('original_id')})
            continue
        result['text'] = trial['text']
        result['raw_evidence_sources'].append({k: raw[k] for k in ('original_id', 'manual_id', 'revision', 'source_spans') if k in raw})
        delivered_raw.append(raw)
    result['definition_task_view'] = copy.deepcopy(view)
    result['candidate_fact_categories'] = sorted({c for r in delivered_raw for c in r.get('candidate_fact_categories', [])})
    result['content_sha256'] = _sha(result['text'])
    return [result]


def _pack_target(
    results, allowance, *, needed_categories=None,
    verified_operand_order=None, verified_opcodes=(),
    operand_gap_details=(), structured_owner=None, relation_diagnostics=None,
    token_allowance=None, include_uninterpreted=True,
):
    """Pack definition, tables and cautions together before any top-k truncation.

    Prefer the first ranked manual revision. Different programming syntaxes are
    not mixed merely to fill space. LAYOUT/DIAGRAM are fallbacks when a PROSE or
    TABLE representation exists, not duplicate paragraphs of the same page.
    """
    from knowledge import core
    if not results or allowance <= 0:
        return []
    needed = (
        None
        if needed_categories is None
        else {
            str(item)
            for item in needed_categories
            if str(item) in FACT_QUESTIONS
        }
    )
    document_key = lambda value: (value.get("manual_id") or value.get("source") or value.get("id"), value.get("revision"))
    primary = document_key(results[0])
    sources = [r for r in results if document_key(r) == primary]
    definition = (structured_owner or {}).get('instruction_definition') or {}
    if definition.get('facts'):
        return _pack_definition_target(sources, allowance, structured_owner, needed, verified_operand_order,
                                       verified_opcodes, relation_diagnostics, token_allowance, include_uninterpreted)
    owner_model = ((structured_owner or {}).get("target_applicability") or {}).get("target_model")
    relation_candidates = []
    # Equivalent same-manual renditions may corroborate a group. Different
    # complete mappings remain a gap; representation priority cannot arbitrate.
    meanings = {tuple((m["output"], m["comparison"]) for m in group["members"])
                for _, group in relation_candidates}
    if relation_diagnostics is not None:
        normalized_sources = [(r, re.sub(r"\[GLYPH-[0-9A-F]+\]", "", str(r.get("text") or ""))) for r in sources]
        relation_diagnostics.update(
            source_ids=[str(r["id"]) for r in sources],
            source_hints=[{"id": str(r["id"]),
                           "comparisons": sorted(set(re.findall(r"S1\s*([><=])\s*S2", raw))),
                           "disabled_retention": bool(re.search(r"command input turns OFF.*?latch", raw, re.S))}
                          for r,raw in normalized_sources],
            candidate_groups=[{k: copy.deepcopy(v) for k,v in group.items() if k != "text"}
                              for _, group in relation_candidates],
            conflicting_groups=len(meanings) > 1, packed_group_ids=[],
        )
    if len(meanings) > 1:
        relation_candidates = []
    candidates = []
    for source_index, result in enumerate(sources):
        text = str(result.get("text") or "")
        page_markers = list(re.finditer(r"(?m)^\[PAGE[^\n]*", text))
        table_units = set(_operand_table_units(text)) if operand_gap_details else set()
        unit_ranges = sorted(set(_units(text)) | table_units)
        for unit_start, unit_end in unit_ranges:
            unit_raw = text[unit_start:unit_end]
            operand_table = (unit_start, unit_end) in table_units
            embedded_cut = None if operand_table else _embedded_instruction_layout_offset(unit_raw)
            pieces = (
                [
                    (unit_start, unit_start + embedded_cut, False),
                    (unit_start + embedded_cut, unit_end, True),
                ]
                if embedded_cut not in (None, 0)
                else [(unit_start, unit_end, False)]
            )
            for start, end, embedded_layout in pieces:
                raw = text[start:end]
                if any(span['start'] < end and start < span['end'] for span in result.get('definition_conflict_spans', [])):
                    continue
                if _conflicts_with_verified_operand_order(
                    result,
                    raw,
                    expected_order=verified_operand_order,
                    opcodes=verified_opcodes or None,
                ):
                    # Native order already has source-verified ownership. A PDF
                    # diagram flattened into another sequence is not useful
                    # corroboration for any remaining operand facet and can make
                    # the model reason against two incompatible syntaxes.
                    continue
                focus = result.get("fact_focus_terms", ())
                if focus and not any(re.search(r"(?<![A-Z0-9])" + re.escape(term) + r"(?![A-Z0-9])", raw, re.I) for term in focus):
                    continue
                if raw.startswith(("SOURCE:", "[STRUCTURED INSTRUCTION RECORD]")):
                    # Source identity is in the citation. Structured extraction can
                    # omit operand symbols, so original definition/table wins.
                    continue
                markers = [match for match in page_markers if match.start() <= start]
                mode = markers[-1].group() if markers else ""
                table = operand_table or raw.startswith("[TABLE")
                if embedded_layout or (
                    not table and ("LAYOUT" in mode or "LADDER/DIAGRAM" in mode)
                ):
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
                bindings = []  # Operand interpretation belongs to the build compiler.
                owner_target = (structured_owner or result).get("target_applicability") or {}
                for binding in bindings:
                    binding["source"].update(
                        target_model=owner_target.get("target_model") or result.get("plc_model"),
                        opcode=owner_target.get("opcode") or result.get("instruction_opcode"),
                    )
                    if binding.get("fact"):
                        binding["fact"]["sources"] = [copy.deepcopy(binding["source"])]
                if bindings and "operands" not in cats:
                    cats.append("operands")
                if needed is not None:
                    cats = [key for key in cats if key in needed]
                    if not cats:
                        continue
                candidates.append((source_index, (start, end, cats), priority, bindings))

    # Inspect all source candidates before budget selection. A later unit can
    # contradict an earlier one even if its other positions add new coverage.
    from plc.instruction_semantics import arbitrate_operand_usage
    grouped = {}
    for _, _, _, bindings in candidates:
        for binding in bindings:
            fact = binding.get("fact") or {
                "facet": binding["facet"], "value": binding["value"],
                "status": binding["status"], "sources": [binding["source"]],
            }
            grouped.setdefault((binding["position"], binding["facet"]), []).append(fact)
    conflicts = []
    for (position, facet), facts in sorted(grouped.items()):
        _, rejected = arbitrate_operand_usage(facts)
        conflicts.extend({**conflict, "position": position} for conflict in rejected)
    blocked = {(item["position"], item["facet"]) for item in conflicts}
    # Source units are atomic. Keeping their contradictory prose would still
    # deliver two meanings even if one structured binding were removed.
    quarantined = [(index, unit) for index, unit, _, bindings in candidates if any(
        (binding["position"], binding["facet"]) in blocked for binding in bindings
    )]
    candidates = [item for item in candidates if not any(
        item[0] == index and left < item[1][1] and item[1][0] < right
        for index, (left, right, _) in quarantined
    )]
    selected, selected_bindings, rendered, covered, bound = {}, {}, {}, set(), set()
    while candidates:
        candidates.sort(key=lambda item: (-item[2], -len({
            _operand_dimension(binding["position"], binding["facet"])
            for binding in item[3]
        } - bound), -len(set(item[1][2])-covered),
                                          item[0], item[1][0]))
        source_index, unit, priority, bindings = candidates.pop(0)
        # Do not refill with duplicate page-layout renditions after the factual
        # source representations have been considered.
        if priority == 0 and rendered:
            continue
        binding_dimensions = {
            _operand_dimension(binding["position"], binding["facet"]) for binding in bindings
        }
        if bindings and binding_dimensions <= bound and set(unit[2]) <= covered:
            continue
        if any(left < unit[1] and unit[0] < right for left, right, _ in selected.get(source_index, [])):
            continue
        trial_units = [*selected.get(source_index, []), unit]
        trial_bindings = [*selected_bindings.get(source_index, []), *bindings]
        trial = _render_units(
            sources[source_index], trial_units, bindings=trial_bindings,
            conflicts=conflicts, structured_owner=structured_owner,
        )
        trial_records = [value for index, value in rendered.items() if index != source_index] + [trial]
        if not _within_packing_budget(trial_records, allowance, token_allowance):
            continue
        selected[source_index], rendered[source_index] = trial_units, trial
        selected_bindings[source_index] = trial_bindings
        covered.update(unit[2])
        bound.update(binding_dimensions)
    if not rendered:
        prefix = _model_structured_prefix(sources[0].get("text"))
        if prefix or conflicts:
            trial = _render_units(sources[0], [], conflicts=conflicts, structured_owner=structured_owner)
            if _within_packing_budget([trial], allowance, token_allowance):
                rendered[0] = trial
    # A recovered relation group is a distinct atomic source projection. It
    # is neither a syntax-layout fallback nor evidence for an operand purpose.
    # Never deliver a partial result mapping or an isolated retention clause.
    for source_index, group in relation_candidates:
        trial = _render_units(
            sources[source_index], selected.get(source_index, []),
            bindings=selected_bindings.get(source_index, []), conflicts=conflicts,
            structured_owner=structured_owner, relation_groups=[group],
        )
        trial_records = [value for index, value in rendered.items() if index != source_index] + [trial]
        if not _within_packing_budget(trial_records, allowance, token_allowance):
            continue
        rendered[source_index] = trial
        if relation_diagnostics is not None:
            relation_diagnostics["packed_group_ids"].append(group["id"])
        break  # one equivalent group suffices; preserve its precise source
    if relation_diagnostics is not None:
        relation_diagnostics["packing_status"] = (
            "candidate_conflict" if len(meanings) > 1 else
            "packed" if relation_diagnostics["packed_group_ids"] else
            "budget_omitted" if relation_candidates else "no_complete_group"
        )
    return [rendered[index] for index in sorted(rendered)]


def retrieve_instruction_facts(
    query, *, plc_model, task_type, char_budget, candidates=(), targets=None,
    retrieve=None, resolver=None, token_budget=None, primary_slots=None, compact_sources=False,
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
        "operand_requirements": [],
        "relation_requirements": [], "relation_evidence": [],
        "verification": "not_performed",
        "retrieval_mode": "structured_direct",
    }
    if task_type not in {"generate", "edit"} or char_budget <= 0:
        return [], report
    if not targets:
        report["reason"] = "no_instruction_target"
        return [], report

    allowance = max(0, int(char_budget) // max(1, len(targets)))
    # Only this many primary packets can reach final top-k. Reserving a full
    # share for later targets would starve an earlier dependency closure even
    # when the first round fits. The final compiler still enforces the total.
    primary_count = min(len(targets), max(1, int(primary_slots))) if primary_slots is not None else len(targets)
    token_allowance = max(0, int(token_budget) // primary_count) if token_budget is not None else None
    if token_allowance is not None:
        report.update(token_budget=token_budget, target_token_allowance=token_allowance)
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
            for result in (seed, *([] if seed.get('compiled_source_chapter') else _related_units(seed, plc_model, task_type))):
                marker = result.get("id")
                if not marker or marker in seen:
                    continue
                opcode = str(result.get("instruction_opcode") or "").upper()
                if opcode and opcode not in {target["opcode"], target["base_opcode"], seed.get("manual_instruction_opcode")}:
                    continue
                seen.add(marker)
                sources.append(result)

        structured_owner = seeds[0] if seeds else {}
        relation_diagnostics = {"target": target["opcode"]}
        operand_gap_details = _operand_gap_details(structured_owner)
        manual_gaps = _manual_fact_gaps(structured_owner)
        report["lookups"][-1]["manual_gaps"] = sorted(manual_gaps)
        report["lookups"][-1]["operand_gap_details"] = copy.deepcopy(
            operand_gap_details
        )
        report["lookups"][-1]["structured_dimensions"] = sorted(
            _structured_fact_dimensions(structured_owner)
        )
        for gap in operand_gap_details:
            requirement = {
                "kind": "instruction", "target": target["opcode"],
                "dimension": _operand_dimension(gap["position"], gap["facet"]),
                "position": gap["position"], "facet": gap["facet"],
            }
            if "operands" in target:
                requirement["instruction_instance"] = {
                    "opcode": target["opcode"], "operands": list(target["operands"]),
                }
            report["operand_requirements"].append(requirement)

        companions = (
            _completion_sources(seeds[0], plc_model, task_type)
            if seeds and _needs_completion_manual(structured_owner)
            else []
        )
        companion_pool = [
            row for row in _pack_target(
                companions,
                min(allowance * 2 // 3, 2200),
                # Completion companions exist only to explain runtime completion
                # state. Never let an adjacent instruction operand table re-enter
                # after structured operand semantics have closed that dimension.
                needed_categories={"execution"},
                token_allowance=token_allowance * 2 // 3 if token_allowance is not None else None,
            )
            if row["id"] not in companion_seen
        ]
        companion_seen.update(row["id"] for row in companion_pool)
        companion_cost, companion_tokens = _packing_cost(companion_pool)
        target_overlay = (
            structured_owner.get("target_applicability")
            if isinstance(structured_owner, Mapping)
            else {}
        ) or {}
        verified_operand_order = (
            list(target_overlay.get("native_operand_order") or ())
            if target_overlay.get("operand_order_status") == "source_verified"
            else None
        )
        primary_pool = _pack_target(
            sources,
            allowance - companion_cost,
            needed_categories=manual_gaps,
            verified_operand_order=verified_operand_order,
            verified_opcodes=(
                target.get("opcode"),
                target.get("base_opcode"),
            ),
            operand_gap_details=operand_gap_details,
            structured_owner=structured_owner,
            relation_diagnostics=relation_diagnostics,
            token_allowance=token_allowance - companion_tokens if token_allowance is not None else None,
            include_uninterpreted=not compact_sources,
        )
        if relation_diagnostics.get('definition_task_view'):
            report.setdefault('definition_requirements', []).extend(relation_diagnostics['definition_requirements'])
            report.setdefault('definition_views', []).append(relation_diagnostics['definition_task_view'])
            report["relation_evidence"].append(relation_diagnostics)
            definition = structured_owner.get('instruction_definition') or {}
            for group in definition.get('facts', []):
                if group['value'].get('behavior') == 'conditional_results':
                    report['relation_requirements'].extend(
                        {'kind': 'instruction', 'target': target['opcode'], 'dimension': dim, 'members': group['members']}
                        for dim in ('operation.result_mapping', 'execution.disabled_retention'))
        pool = primary_pool[:1] + companion_pool + primary_pool[1:]
        for value in pool:
            value["fact_kind"] = "instruction"
            value["fact_target"] = target["opcode"]
            value["fact_dimensions"] = sorted(
                set(value.get("fact_dimensions") or ()) | set(value.get("candidate_fact_categories") or ())
                | set(_structured_fact_dimensions(value))
                | {
                    _operand_dimension(binding["position"], binding["facet"])
                    for binding in value.get("operand_evidence_bindings") or ()
                }
                | {dimension for group in value.get("relation_evidence_groups") or ()
                   for dimension in group["dimensions"]}
            )
            if "operands" in target:
                value["instruction_instance"] = {
                    "opcode": target["opcode"], "operands": list(target["operands"]),
                }
            value["manual_fact_gaps"] = sorted(manual_gaps)
            value["operand_gap_details"] = copy.deepcopy(operand_gap_details)
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
                "operand_semantics", "target_applicability", "runtime_semantics",
                "operand_slots", "operand_gap_details",
                "operand_evidence_bindings",
                "operand_candidate_conflicts",
                "instruction_step_width", "instruction_instance",
                "relation_evidence_groups",
                "fact_group_members",
                "definition_fact_groups", "definition_task_view",
                "raw_evidence_sources",
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

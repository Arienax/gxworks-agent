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
_VERSION = "instruction-facts-v6-relation-evidence"
_CMP_RELATION_DIMENSIONS = ("operation.result_mapping", "execution.disabled_retention")

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


def _source_offset_basis(result):
    """Translate resolved text offsets back to the original chunk when possible."""
    text = str(result.get("text") or "")
    original = result.get("manual_text")
    offset_delta, offset_basis = 0, "resolved_record.text"
    if isinstance(original, str):
        original_body = original.partition("\n\n")[2] if original.startswith("[STRUCTURED") else original
        body = original_body.strip()
        rendered = text.rstrip()
        if body and rendered.endswith(body):
            body_start = len(original) - len(original_body) + len(original_body) - len(original_body.lstrip())
            offset_delta = body_start - (len(rendered) - len(body))
            offset_basis = "chunks.text"
    return offset_delta, offset_basis


def _cmp_relation_groups(result, expected_order, *, target_model=None):
    """Recover an atomic 16-bit result diagram from explicit layout cells.

    This is source text projection, not a verified operation definition. A
    complete group needs labelled outputs, one explicit comparison per output,
    command-input context and the same page's disabled-retention sentence.
    Unlabelled/reordered prose glyphs cannot supply missing associations.
    """
    model = target_model or result.get("plc_model") or (result.get("target_applicability") or {}).get("target_model")
    if (model != "FX3U" or str(result.get("instruction_opcode") or "").upper() != "CMP"
            or list(expected_order or ()) != ["S1", "S2", "D"]):
        return []
    text = str(result.get("text") or "")
    delta, basis = _source_offset_basis(result)
    groups = []
    glyphs = re.compile(r"\[GLYPH-[0-9A-F]+\]", re.I)
    clean = lambda value: re.sub(r"\s+", " ", glyphs.sub("", value)).strip()
    label = re.compile(r"D(?:\s*\+\s*([12]))?$")
    comparison = re.compile(r'^"?\s*\[\s*S1\s*([><=])\s*S2\s*\]\s*"?\.?$')
    for left, right in _units(text):
        raw = text[left:right]
        page = re.match(r"\[PAGE (\d+) LAYOUT\]", raw)
        if not page:
            continue
        # First-column projection keeps label/relation association. Other
        # columns contain the waveform and running page headers, not this row.
        lines, cursor = [], left
        for line in raw.splitlines(keepends=True):
            cell = line.split("|", 1)[0].rstrip()
            lines.append((clean(cell), cursor, cursor + len(cell), line))
            cursor += len(line)
        headings = [i for i, row in enumerate(lines) if row[0] == "1. 16-bit operation (CMP and CMPP)"]
        if len(headings) != 1:
            continue
        begin = headings[0]
        body = lines[begin:]
        command = [row for row in body if row[0] in {"Command", "input"}]
        algebraic = [row for row in body if row[0].startswith("• Comparison is executed algebraically.")]
        hold = [i for i, row in enumerate(body) if row[0] == (
            "Even if the command input turns OFF and CMP instruction is not executed, D , D +1 and D +2 latch")]
        if {row[0] for row in command} != {"Command", "input"} or len(algebraic) != 1 or len(hold) != 1:
            continue
        h = hold[0]
        if h + 1 >= len(body):
            continue
        # A separate sidebar cell may intervene; do not infer a missing clause.
        tails = [row for row in body[h+1:h+4] if row[0] == "the status just before the command input turns OFF from ON."]
        if len(tails) != 1:
            continue
        found = {}
        invalid = False
        for i, row in enumerate(body[:h]):
            match = label.fullmatch(row[0])
            if not match:
                continue
            following = []
            for item in body[i+1:h]:
                if label.fullmatch(item[0]):
                    break
                if comparison.fullmatch(item[0]):
                    following.append(item)
            if not following:
                continue  # waveform labels alone carry no relation
            output = "D" + ("+" + match[1] if match[1] else "")
            if output in found or len(following) != 1:
                invalid = True
                break
            found[output] = (comparison.fullmatch(following[0][0])[1], row, following[0])
        if invalid or set(found) != {"D", "D+1", "D+2"} or {v[0] for v in found.values()} != {">", "=", "<"}:
            continue
        first = found["D"][1][1]
        last = found["D+2"][2][2]
        on_cells = []
        for row in body:
            cursor = row[1]
            for cell in row[3].rstrip('\r\n').split('|')[:2]:
                if clean(cell) == "Turns ON in the case of" and first <= cursor < last:
                    on_cells.append((clean(cell), cursor, cursor+len(cell), row[3]))
                cursor += len(cell)+1
        if len(on_cells) != 3:
            continue
        witness_rows = [body[0], algebraic[0], *command, *on_cells, body[h], tails[0]]
        members = []
        for output in ("D", "D+1", "D+2"):
            operator, output_row, relation_row = found[output]
            witness_rows.extend([output_row, relation_row])
            members.append({"output": output, "comparison": f"S1 {operator} S2",
                            "value_spans": [{"start": r[1]+delta, "end": r[2]+delta} for r in (output_row, relation_row)]})
        source = {key: copy.deepcopy(result[key]) for key in (
            "id", "manual_id", "manual_number", "revision", "source", "section", "plc_model",
        ) if key in result}
        source.update(pdf_page=int(page[1]), target_model=model, offset_basis=basis,
                      source_spans=[{"start": left+delta, "end": right+delta}],
                      value_spans=[{"start": r[1]+delta, "end": r[2]+delta} for r in witness_rows])
        prose = "\n".join([
            body[0][0], algebraic[0][0],
            "Command input controls CMP execution. When the instruction executes:",
            *[f"{m['output']}: Turns ON in the case of [{m['comparison']}]." for m in members],
            body[h][0] + " " + tails[0][0],
        ])
        groups.append({"id": f"{result['id']}:cmp16:{page[1]}", "status": "candidate_evidence",
                       "representation": "layout_cell_projection", "dimensions": list(_CMP_RELATION_DIMENSIONS),
                       "members": members, "source": source, "text": prose})
    return groups


def _operand_evidence_bindings(result, start, end, gaps, expected_order):
    """Bind explicit rows by unique native symbol, never their physical order."""
    expected = [re.sub(r"\s+", "", str(item)).upper() for item in expected_order or ()]
    if not expected or len(set(expected)) != len(expected):
        return []
    text = str(result.get("text") or "")
    offset_delta, offset_basis = _source_offset_basis(result)
    raw = text[start:end]
    lines = raw.splitlines(keepends=True)
    description_column, columns, cursor = None, {}, start
    rows = []
    pending = []
    hints = {}
    data_type = re.compile(
        r"(?:Bit|Bool(?:ean)?|Word|Dword|Real|Float|String|ANY\d+|"
        r"(?:16|32|64)(?:-\s*or\s*(?:32|64))?-bit(?:\s+(?:binary|integer|floating point))?)",
        re.I,
    )
    for item in result.get("manual_operand_rows") or ():
        if not isinstance(item, Mapping):
            continue
        symbol = re.sub(r"\s+", "", str(item.get("position") or "")).upper()
        description = " ".join(str(item.get("description") or "").split())
        if symbol in expected and description:
            hints.setdefault(description, []).append(symbol)

    def cell_parts(line, offset):
        result, relative = [], 0
        for raw_cell in line.rstrip("\r\n").split("|"):
            cell = raw_cell.strip()
            left = offset + relative + len(raw_cell) - len(raw_cell.lstrip())
            result.append((cell, left, left + len(cell)))
            relative += len(raw_cell) + 1
        return result

    def native_symbol(cell):
        # Font placeholders decorate the symbol, not its identity. Other
        # content in the same cell still makes that association ambiguous.
        cell = re.sub(r"\[GLYPH-[0-9A-F]+\]", "", cell, flags=re.I)
        return re.sub(r"\s+", "", cell).upper()

    def description_fragment(cell):
        if not cell or cell == "<blank>" or data_type.fullmatch(cell):
            return False
        if re.match(r"^(?:\*\d+[.:]|\[GLYPH-[0-9A-F]+\]\d+:)", cell, re.I):
            return False  # notes stay in the source unit, outside row purpose
        tokens = re.findall(r"[A-Za-z]+", cell)
        return (bool(re.search(r"[\u3400-\u9fff]", cell))
                or any(len(token) >= 4 for token in tokens)
                or len(tokens) == 1 and len(tokens[0]) >= 3
                or cell.startswith(("(", "[")) and bool(re.search(r"\d", cell)))

    def source_page(offset):
        markers = list(re.finditer(r"(?m)^\[(?:PAGE\s+(\d+)|TABLE\s+page=(\d+))\b", text[:offset]))
        return int(next(group for group in markers[-1].groups() if group)) if markers else result.get("pdf_page")

    def supports_split_cell(symbol, description, offset):
        # Reconstruct only if another representation of this source page has
        # the exact complete value on the same explicit native-symbol row. No
        # spelling similarity, preferred length or cross-manual inference.
        if not result.get("manual_id") or not result.get("revision") or not source_page(offset):
            return False
        for left, right in _operand_table_units(text):
            if left == start and right == end or source_page(left) != source_page(offset):
                continue
            column = None
            for other in text[left:right].splitlines():
                other_cells = [cell.strip() for cell in other.split("|")]
                if column is None:
                    column = next((index for index, cell in enumerate(other_cells)
                                   if re.fullmatch(r"Description|含义|说明", cell, re.I)), None)
                    continue
                if len(other_cells) <= column:
                    continue
                anchors = [native_symbol(cell) for cell in other_cells[:column]
                           if native_symbol(cell) in expected]
                if anchors == [symbol] and " ".join(other_cells[column].split()) == " ".join(description.split()):
                    return True
        return False

    for line in lines:
        parts = cell_parts(line, cursor)
        cells = [part[0] for part in parts]
        if description_column is None:
            for index, cell in enumerate(cells):
                if re.fullmatch(r"Description|含义|说明", cell, re.I):
                    description_column = index
            if description_column is not None:
                for index, cell in enumerate(cells):
                    for facet, pattern in (
                        ("operand_types", r"Data\s*Type|数据类型"),
                        ("operand_roles", r"Role|读写角色"),
                        ("unit", r"Unit|单位"),
                        ("encoding", r"Encoding|编码"),
                        ("range", r"Range|范围"),
                        ("condition", r"Condition|条件"),
                    ):
                        if re.fullmatch(pattern, cell, re.I):
                            columns[facet] = index
            cursor += len(line)
            continue
        description = cells[description_column] if description_column < len(cells) else ""
        symbols = [native_symbol(cell) for cell in cells[:description_column]] if "|" in line else []
        symbols = [symbol for symbol in symbols if symbol in expected]
        hinted = hints.get(" ".join(description.split()), [])
        # An indexed description may recover a dropped symbol only if the
        # source description and its indexed association are both unambiguous.
        if (not symbols and len(hinted) == 1
                and all(cell in {"", "<blank>"} for cell in cells[:description_column])):
            symbols = hinted
        if len(symbols) > 1:
            pending = []
            cursor += len(line)
            continue
        if not symbols:
            # Flattened PDF rows place their description before the symbol,
            # with continuations after it. Bind only through that explicit
            # symbol anchor; sidebar fragments and data types are excluded.
            fragment = parts[0]
            closing_range = bool(
                rows and rows[-1]["parts"] and re.fullmatch(r"[\d\s,.:;+-]+[)\]]", fragment[0])
                and any("[" in part[0] or "(" in part[0] for part in rows[-1]["parts"])
            )
            if description_fragment(fragment[0]) or closing_range:
                if (rows and rows[-1]["wrapped"] and rows[-1]["parts"]
                        and (closing_range or fragment[0].startswith(("(", "[")) or fragment[0][0].islower())):
                    rows[-1]["parts"].append(fragment)
                    rows[-1]["end"] = cursor + len(line.rstrip("\r\n"))
                elif rows and fragment[0].startswith(("(", "[")):
                    pass  # inline-row notes remain context, never a prefix of the next row
                else:
                    pending.append(fragment)
            cursor += len(line)
            continue
        valid_description = (description_fragment(description)
                             and native_symbol(description) not in expected)
        wrapped = not valid_description or bool(pending and description.startswith(("(", "[", "-")))
        fragments = list(pending) if wrapped else []
        join_at = set()
        if valid_description:
            prefix = parts[description_column-1] if description_column else None
            if (not wrapped and prefix and re.fullmatch(r"[A-Za-z]", prefix[0])
                    and description and description[0].islower()
                    and supports_split_cell(symbols[0], prefix[0]+description, cursor)):
                fragments.append(prefix)
                join_at.add(len(fragments))
            fragments.append(parts[description_column])
        pending = []
        values = {
            facet: cells[index] for facet, index in columns.items()
            if index < len(cells) and cells[index] not in {"", "<blank>"}
        }
        if wrapped and data_type.fullmatch(description):
            values["operand_types"] = description
        if "operand_types" in values and not data_type.fullmatch(values["operand_types"]):
            del values["operand_types"]
        rows.append({"symbol": symbols[0], "start": min([cursor, *(part[1] for part in fragments)]),
                     "end": cursor + len(line.rstrip("\r\n")), "parts": fragments,
                     "wrapped": wrapped, "join_at": join_at, "values": values})
        cursor += len(line)
    symbol_counts = {symbol: sum(row["symbol"] == symbol for row in rows) for symbol in expected}
    bindings = []
    for row in rows:
        symbol, row_start, row_end = row["symbol"], row["start"], row["end"]
        if symbol_counts[symbol] != 1:
            continue
        values = dict(row["values"])
        if row["parts"]:
            values["purpose"] = "".join(("" if index == 0 or index in row["join_at"] else " ") + part[0]
                                        for index, part in enumerate(row["parts"]))
        position = expected.index(symbol) + 1
        for gap in gaps or ():
            facet = gap.get("facet")
            if gap.get("position") != position or facet not in values:
                continue
            evidence = {
                "id": str(result.get("original_id") or result.get("id") or ""),
                "manual_id": result.get("manual_id"),
                "manual": result.get("manual_number"),
                "revision": result.get("revision"),
                "page": result.get("page"),
                "section": result.get("section"),
                "row_span": {"start": row_start + offset_delta, "end": row_end + offset_delta},
                "context_span": {"start": start + offset_delta, "end": end + offset_delta},
                "offset_basis": offset_basis,
            }
            page_markers = list(re.finditer(r"(?m)^\[(?:PAGE\s+(\d+)|TABLE\s+page=(\d+))\b", text[:row_start]))
            if page_markers:
                evidence["pdf_page"] = int(next(group for group in page_markers[-1].groups() if group))
            if facet == "purpose" and (row["wrapped"] or len(row["parts"]) > 1):
                evidence["value_spans"] = [{"start": left + offset_delta, "end": right + offset_delta,
                                           **({"join_before": ""} if index in row["join_at"] else {})}
                                          for index, (_, left, right) in enumerate(row["parts"])]
            binding = {
                "position": position, "symbol": symbol, "facet": facet,
                "value": values[facet], "status": "candidate_evidence", "source": evidence,
            }
            if facet not in {"operand_types", "operand_roles"}:
                from plc.instructions import OperandUsageFact
                binding["fact"] = OperandUsageFact(
                    facet, values[facet], "candidate_evidence", (evidence,),
                ).as_mapping()
            bindings.append(binding)
    return bindings


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

def _pack_target(
    results, allowance, *, needed_categories=None,
    verified_operand_order=None, verified_opcodes=(),
    operand_gap_details=(), structured_owner=None, relation_diagnostics=None,
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
    owner_model = ((structured_owner or {}).get("target_applicability") or {}).get("target_model")
    relation_candidates = [(index, group) for index, result in enumerate(sources)
                           for group in _cmp_relation_groups(result, verified_operand_order, target_model=owner_model)]
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
                bindings = _operand_evidence_bindings(
                    result, start, end, operand_gap_details, verified_operand_order,
                ) if operand_table else []
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
        block_cost = lambda value: len(core._format_result_block(value)) + 40
        cost = sum(block_cost(value) for index, value in rendered.items() if index != source_index) + block_cost(trial)
        if cost > allowance:
            continue
        selected[source_index], rendered[source_index] = trial_units, trial
        selected_bindings[source_index] = trial_bindings
        covered.update(unit[2])
        bound.update(binding_dimensions)
    if not rendered:
        prefix = _model_structured_prefix(sources[0].get("text"))
        if prefix or conflicts:
            trial = _render_units(sources[0], [], conflicts=conflicts, structured_owner=structured_owner)
            if len(core._format_result_block(trial)) + 40 <= allowance:
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
        cost = sum(len(core._format_result_block(value)) + 40 for index,value in rendered.items()
                   if index != source_index) + len(core._format_result_block(trial)) + 40
        if cost > allowance:
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
                if opcode and opcode not in {target["opcode"], target["base_opcode"], seed.get("manual_instruction_opcode")}:
                    continue
                seen.add(marker)
                sources.append(result)

        structured_owner = seeds[0] if seeds else {}
        relation_diagnostics = {"target": target["opcode"]}
        if plc_model == "FX3U" and target["opcode"] == "CMP":
            report["relation_requirements"].extend(
                {"kind": "instruction", "target": "CMP", "dimension": dimension,
                 "members": ["D", "D+1", "D+2"], "verification": "not_performed"}
                for dimension in _CMP_RELATION_DIMENSIONS
            )
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
            )
            if row["id"] not in companion_seen
        ]
        companion_seen.update(row["id"] for row in companion_pool)
        companion_cost = sum(
            len(core._format_result_block(row)) + 40
            for row in companion_pool
        )
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
        )
        if plc_model == "FX3U" and target["opcode"] == "CMP":
            report["relation_evidence"].append(relation_diagnostics)
        pool = primary_pool[:1] + companion_pool + primary_pool[1:]
        for value in pool:
            value["fact_kind"] = "instruction"
            value["fact_target"] = target["opcode"]
            value["fact_dimensions"] = sorted(
                set(value.get("candidate_fact_categories") or ())
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

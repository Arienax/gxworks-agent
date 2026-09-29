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
_VERSION = "instruction-facts-v3-gap-directed"

_OPERAND_SLOT_FACETS = (
    ("operand_roles", "role_status", "operand_role_status"),
    ("operand_types", "data_type_status", "operand_type_status"),
    ("operand_order", "symbol_status", "operand_order_status"),
    ("device_classes", "device_class_status", "device_class_status"),
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
                status = str(raw_slot.get(slot_key) or "unresolved")
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
                gaps.append(gap)
        return gaps

    if (
        target.get("min_operands") == 0
        and target.get("max_operands") == 0
        and target.get("operand_order_status") == "source_verified"
    ):
        return []

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

    boundary = r"[A-Za-z0-9_.$@+<>!=\\-]"
    opcode_re = re.compile(
        r"(?<!%s)%s(?!%s)" % (boundary, re.escape(opcode), boundary),
        re.I,
    )
    operand_re = re.compile(r"(?i)(?<![A-Z0-9])([SDMN]\\d{0,3})(?![A-Z0-9])")
    glyph_operand_re = re.compile(
        r"(?i)([SDM]\\d{0,3})\\s*(?=\\[GLYPH-[0-9A-F]+\\])"
    )
    fused_count_re = re.compile(
        r"(?i)(?<![A-Z0-9])(n\\d{0,3})(?=[SDM]\\d{0,3}\\s*\\[GLYPH-[0-9A-F]+\\])"
    )
    expected_set = set(expected)

    lines = text.splitlines()
    offsets, cursor = [], 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line) + 1

    for match in opcode_re.finditer(text):
        line_index = max(
            (index for index, offset in enumerate(offsets) if offset <= match.start()),
            default=0,
        )
        line = lines[line_index] if lines else ""
        relative = match.start() - offsets[line_index] if offsets else 0
        # A mnemonic embedded in ordinary prose ("dead band") is not syntax.
        if line[:relative].strip() and not re.search(
            r"(?:FNC\\s*\\d+|input)\\s*$", line[:relative], re.I
        ):
            continue

        region_lines = lines[line_index:line_index + 4]
        region = "\\n".join(region_lines)
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
            if re.fullmatch(r"(?i)[SDMN]\\d{0,3}", stripped):
                positions.append((running + short_line.find(stripped), stripped.upper()))
            running += len(short_line) + 1

        sequence = []
        for _position, token in sorted(positions):
            if token in expected_set and token not in sequence:
                sequence.append(token)
        if len(sequence) >= 2:
            return sequence
    return []


def _conflicts_with_verified_operand_order(result, raw):
    """True when manual visual residue contradicts a verified native order."""
    result = result if isinstance(result, Mapping) else {}
    target = result.get("target_applicability") or {}
    if target.get("operand_order_status") != "source_verified":
        return False
    expected = list(target.get("native_operand_order") or ())
    if len(expected) < 2:
        return False
    opcode = str(result.get("instruction_opcode") or target.get("opcode") or "")
    sequence = _visual_operand_sequence(raw, opcode, expected)
    if len(sequence) < 2:
        return False
    indexes = [expected.index(token) for token in sequence if token in expected]
    return any(left >= right for left, right in zip(indexes, indexes[1:]))


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


def _render_units(result, selected):
    """Preserve source offsets and provenance while grouping selected units.

    Structured contract/step-width facts are supplemental to the original
    manual evidence. They are not candidates for prose/table selection, but
    once a manual block is selected the structured prefix is delivered with it.
    """
    text = str(result.get("text") or "")
    selected = sorted(selected, key=lambda unit: unit[0])
    source_text = "\n\n".join(text[start:end].rstrip() for start, end, _ in selected)

    structured_prefix = _model_structured_prefix(text)
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

def _pack_target(results, allowance, *, needed_categories=None):
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
    candidates = []
    for source_index, result in enumerate(sources):
        text = str(result.get("text") or "")
        page_markers = list(re.finditer(r"(?m)^\[PAGE[^\n]*", text))
        for unit_start, unit_end in _units(text):
            unit_raw = text[unit_start:unit_end]
            embedded_cut = _embedded_instruction_layout_offset(unit_raw)
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
                if _conflicts_with_verified_operand_order(result, raw):
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
                table = raw.startswith("[TABLE")
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
                if needed is not None:
                    cats = [key for key in cats if key in needed]
                    if not cats:
                        continue
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
    if not rendered:
        prefix = _model_structured_prefix(sources[0].get("text"))
        if prefix:
            trial = _render_units(sources[0], [])
            if len(core._format_result_block(trial)) + 40 <= allowance:
                rendered[0] = trial
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

        structured_owner = seeds[0] if seeds else {}
        operand_gap_details = _operand_gap_details(structured_owner)
        manual_gaps = _manual_fact_gaps(structured_owner)
        report["lookups"][-1]["manual_gaps"] = sorted(manual_gaps)
        report["lookups"][-1]["operand_gap_details"] = copy.deepcopy(
            operand_gap_details
        )
        report["lookups"][-1]["structured_dimensions"] = sorted(
            _structured_fact_dimensions(structured_owner)
        )

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
        primary_pool = _pack_target(
            sources,
            allowance - companion_cost,
            needed_categories=manual_gaps,
        )
        pool = primary_pool[:1] + companion_pool + primary_pool[1:]
        for value in pool:
            value["fact_kind"] = "instruction"
            value["fact_target"] = target["opcode"]
            value["fact_dimensions"] = sorted(
                set(value.get("candidate_fact_categories") or ())
                | set(_structured_fact_dimensions(value))
            )
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

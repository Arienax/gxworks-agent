"""Build-time instruction document structure and source-bound table extraction.

Runtime retrieval consumes the resulting instruction facts. This module owns
manual representation parsing; it does not resolve model requests or promote
source verification.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from collections.abc import Mapping


@dataclass(frozen=True)
class DocumentCell:
    value: str
    start: int
    end: int
    row: int
    column: int


@dataclass(frozen=True)
class DocumentUnit:
    id: str
    representation: str
    page: int | None
    start: int
    end: int
    cells: tuple[DocumentCell, ...]


def clean_cell(value):
    return re.sub(r'\s+', ' ', re.sub(r'\[GLYPH-[0-9A-F]+\]', '', value, flags=re.I)).strip()


def document_units(record):
    """Keep every original source unit and cell, including unparsed diagrams."""
    text = str(record.get('text') or '')
    units = []
    for index, (start, end) in enumerate(_units(text)):
        raw = text[start:end]
        # Paragraph splitting must not erase its containing page's visual
        # representation. The marker often precedes several blank lines.
        markers = list(re.finditer(r'(?m)^\[PAGE (\d+) ([A-Z_/]+)\]|^\[TABLE[^\n]*?page=(\d+)', text[:start + len(raw.split('\n', 1)[0])]))
        marker = markers[-1] if markers else None
        page = int(marker[1] or marker[3]) if marker else record.get('pdf_page')
        representation = (marker[2].lower() if marker and marker[2] else
                          'table' if marker and marker[3] or raw.startswith('[TABLE') else 'prose')
        cursor, cells = start, []
        for row, line in enumerate(raw.splitlines(keepends=True)):
            left = cursor
            for column, part in enumerate(line.rstrip('\r\n').split('|')):
                cells.append(DocumentCell(clean_cell(part), left, left + len(part), row, column))
                left += len(part) + 1
            cursor += len(line)
        units.append(DocumentUnit(f"{record['id']}:unit:{index}", representation, page, start, end, tuple(cells)))
    return units


def geometric_page_lines(page):
    """Reconstruct horizontal source rows without the rotated chapter tabs."""
    lines = []
    for word in sorted(json.loads(page['word_geometry_json']), key=lambda w: (w['top'], w['x0'])):
        if word['x0'] >= 538:
            continue
        if lines and abs(word['top'] - lines[-1][0]) < 3.5:
            lines[-1][1].append(word)
        else:
            lines.append((word['top'], [word]))
    return [(y, ' '.join(w['text'] for w in sorted(words, key=lambda w: w['x0'])), words)
            for y, words in lines]


def extract_model_badge_marks(rectangles, layout):
    """Read configured source-page highlights, without inferring CPU support."""
    badges = layout.get('model_badges', [])
    if not badges:
        return None
    tolerance = layout.get('coordinate_tolerance', 0.5)
    marks = []
    for badge in badges:
        matches = [rect for rect in rectangles if all(abs(actual - expected) <= tolerance
            for actual, expected in zip((rect['x0'], rect['top'], rect['x1'], rect['bottom']), badge['bbox']))]
        active = [rect for rect in matches if rect.get('fill') is True and
                  rect.get('non_stroking_color') == layout['highlight_fill_color']]
        if len(active) > 1 or any(rect.get('fill') and rect not in active for rect in matches):
            return None
        marks.append({'model': badge['model'], 'highlighted': bool(active), 'bbox': list(badge['bbox'])})
    return marks if any(mark['highlighted'] for mark in marks) else None


def extract_native_signature(page, tables, *, declared_base=None):
    """Read complete set-data rows and literal mnemonic columns, never an icon.

    This is a build-stage structural extraction, not independent review or
    opcode admission. Unsupported columns, partial tables and competing symbol
    anchors remain unresolved. Existing two-manual review can consume it.
    """
    token = r'[$A-Z][A-Z0-9_.$@+<>=!\-]*'
    symbol = re.compile(r'(?:[SD]|[nm])\d?')
    lines = geometric_page_lines(page)
    text = '\n'.join(line for _, line, _ in lines)
    heading = re.search(r'FNC\s*(\d+)\s*[–—−-]\s*(' + token + r')\s*/', text[:700])
    starts = [y for y, line, _ in lines if re.match(r'2\.\s*(?:Set data|Data setting)\b', line, re.I)]
    ends = [y for y, line, _ in lines if re.match(r'3\.\s*(?:Applicable devices|Devices)\b', line, re.I)]
    formats = re.search(r'1\.\s*Instruction format(.*?)2\.\s*(?:Set data|Data setting)', text, re.S | re.I)
    if not starts or not formats:
        return None
    if not ends and declared_base:
        # Some definition tables end on this page and the device matrix starts
        # on the next one. The current page's explicit table box is sufficient;
        # no neighbor page is searched or assigned by proximity.
        ends = [max((word['bottom'] for _, _, words in lines for word in words), default=0)]
    if not ends:
        return None
    if heading:
        base, fnc = heading[2], heading[1]
    elif declared_base and re.fullmatch(token, declared_base):
        numbers = set(re.findall(r'\bFNC\s*(\d+)\b', formats[1]))
        if len(numbers) != 1:
            return None
        base, fnc = declared_base, next(iter(numbers))
    else:
        return None
    boxes = []
    for table in tables:
        rows = json.loads(table['rows_json'])
        header = ' '.join(rows[0]).lower() if rows else ''
        box = json.loads(table['bbox_json'])
        if 'operand' in header and 'description' in header and 'data' in header and starts[0] < box[1] < ends[0]:
            boxes.append((box, table))
    if len(boxes) > 1:
        return None
    if boxes:
        box, table = boxes[0]
    else:
        body = [(y, line) for y, line, _ in lines if starts[0] < y < ends[0]]
        if not any('operand' in line.lower() and 'description' in line.lower() for _, line in body):
            return None
        notes = [y for y, line in body if re.match(r'\*\d+\.', line)]
        box = [48, starts[0] + 10, 538, min(notes) if notes else ends[0]]
        table = {'table_index': 0, 'table_text': '\n'.join(line for _, line in body),
                 'rows_json': '[]', 'bbox_json': json.dumps(box)}
    symbols = []
    rows = []
    for y, line, words in lines:
        if not box[1] <= y < box[3]:
            continue
        anchors = [w for w in words if box[0] <= w['x0'] < min(140, box[2]) and symbol.fullmatch(w['text'])]
        if len(anchors) > 1:
            return None
        if anchors:
            symbols.append(anchors[0]['text'].upper())
            rows.append({'symbol': symbols[-1], 'bbox': [anchors[0]['x0'], y, box[2], anchors[0]['bottom']],
                         'source_text': line})
    if (not symbols and 'no set data' not in table['table_text'].lower()) or len(set(symbols)) != len(symbols):
        return None
    table_rows = json.loads(table['rows_json'])
    if table_rows and symbols and len(table_rows) - 1 != len(symbols):
        return None
    begin = next(y for y, line, _ in lines if re.match(r'1\.\s*Instruction format', line, re.I))
    words = [w for y, _, line_words in lines if begin < y < starts[0] for w in line_words]
    columns = [w for w in words if w['text'] == 'Mnemonic']
    forms = {}
    for column in columns:
        for word in words:
            if (word['top'] <= column['top'] + 5 or abs(word['x0'] - column['x0']) > 16
                    or not re.fullmatch(token, word['text'])):
                continue
            conditions = [w for w in words if w['text'] in ('Continuous', 'Pulse')
                          and 40 < w['x0'] - column['x0'] < 110 and abs(w['top'] - word['top']) < 10]
            widths = [w['text'] for w in words if w['text'] in ('16-bit', '32-bit')
                      and 25 < column['x0'] - w['x0'] < 85 and abs(w['top'] - column['top']) < 5]
            forms[word['text']] = {'execution_form': conditions[0]['text'].lower() if len(conditions) == 1 else None,
                                  'instruction_width': int(widths[0][:2]) if len(widths) == 1 else None,
                                  'bbox': [word['x0'], word['top'], word['x1'], word['bottom']]}
    if not columns and declared_base:
        # Positioning manuals label these columns "Instruction symbol".
        # A literal form must share a row with its execution condition; the
        # instruction icon elsewhere in the format is never an alias source.
        for word in words:
            if not re.fullmatch(token, word['text']):
                continue
            conditions = [w for w in words if w['text'] in ('Continuous', 'Pulse')
                          and 25 < w['x0'] - word['x0'] < 110 and abs(w['top'] - word['top']) < 5]
            if len(conditions) != 1:
                continue
            widths = [w['text'] for w in words if w['text'] in ('16-bit', '32-bit')
                      and abs(w['x0'] - word['x0']) < 60 and 0 < word['top'] - w['top'] < 80]
            forms[word['text']] = {'execution_form': conditions[0]['text'].lower(),
                                  'instruction_width': int(widths[0][:2]) if len(widths) == 1 else None,
                                  'bbox': [word['x0'], word['top'], word['x1'], word['bottom']]}
    return {'base': base, 'fnc': fnc, 'symbols': symbols, 'forms': forms,
            'page': page['pdf_page'], 'section': page['section'], 'manual_id': page['manual_id'],
            'table_index': table['table_index'], 'table_bbox': box, 'operand_rows': rows,
            'format_text': formats[1],
            'table_source': {'rows_json': table['rows_json'], 'bbox_json': table['bbox_json']}}


def source_location(record, unit, spans):
    delta, basis = _source_offset_basis(record)
    source = {key: copy.deepcopy(record[key]) for key in
              ('id', 'manual_id', 'manual_number', 'revision', 'source', 'section') if key in record}
    source.update(pdf_page=unit.page, offset_basis=basis,
                  source_spans=[{'start': unit.start + delta, 'end': unit.end + delta}],
                  value_spans=[{'start': start + delta, 'end': end + delta} for start, end in spans])
    return source


def extract_binary_result_charts(record, *, symbols, opcode, target_model, width):
    """Project explicitly labelled binary-comparison charts, independent of opcode.

    This structural rule consumes a finite-width heading, parameter symbols,
    labelled output cells and a source-supported retention clause. It does not
    guess unlabelled equations or reconstruct across page boundaries.
    """
    if width not in {16, 32} or len(symbols) < 3:
        return []
    symbol_set = set(symbols)
    label_pattern = re.compile(r'([A-Z][A-Z0-9]*?)(?:\s*\+\s*([0-9]+))?$')
    relation_pattern = re.compile(r'^"?\s*\[\s*([A-Z][A-Z0-9]*)\s*([><=])\s*([A-Z][A-Z0-9]*)\s*\]\s*"?\.?$')
    headings = re.compile(r'^(?:\d+\.\s*)?(\d+)-bit operation(?:\s*\(([^)]+)\))?$')
    hold_pattern = re.compile(r'^Even if the command input turns OFF and ([A-Z][A-Z0-9]*) instruction is not executed, (.+) latch$')
    groups = []
    for unit in document_units(record):
        if unit.representation != 'layout':
            continue
        first_column = [cell for cell in unit.cells if cell.column == 0]
        starts = [(i, headings.fullmatch(cell.value)) for i, cell in enumerate(first_column) if headings.fullmatch(cell.value)]
        selected = [(i, match) for i, match in starts if int(match[1]) == width]
        if len(selected) != 1:
            continue
        begin, heading = selected[0]
        if heading[2] and opcode not in re.findall(r'\b[A-Z][A-Z0-9]*\b', heading[2]):
            continue
        after = min((i for i, _ in starts if i > begin), default=len(first_column))
        body = first_column[begin:after]
        notes = [c for c in body if c.value.startswith('• Comparison is executed algebraically.')]
        command = [c for c in body if c.value in {'Command', 'input'}]
        holds = [(i, hold_pattern.fullmatch(c.value)) for i, c in enumerate(body) if hold_pattern.fullmatch(c.value)]
        if len(notes) != 1 or {c.value for c in command} != {'Command', 'input'} or len(holds) != 1:
            continue
        hold_index, hold_match = holds[0]
        tails = [c for c in body[hold_index + 1:hold_index + 4] if c.value == 'the status just before the command input turns OFF from ON.']
        if len(tails) != 1:
            continue
        found, invalid = {}, False
        for i, cell in enumerate(body[:hold_index]):
            label = label_pattern.fullmatch(cell.value)
            if label is None or label[1] not in symbol_set:
                continue
            relations = []
            for other in body[i + 1:hold_index]:
                next_label = label_pattern.fullmatch(other.value)
                if next_label and next_label[1] in symbol_set:
                    break
                match = relation_pattern.fullmatch(other.value)
                if match:
                    relations.append((other, match))
            if not relations:
                continue
            name, offset = label[1], int(label[2] or 0)
            key = (name, offset)
            if key in found or len(relations) != 1:
                invalid = True
                break
            other, relation = relations[0]
            if relation[1] not in symbol_set or relation[3] not in symbol_set:
                invalid = True
                break
            found[key] = (cell, other, relation)
        # This shape is a three-way partition; other charts remain visible as
        # uninterpreted material instead of being squeezed into this behavior.
        names = {key[0] for key in found}
        if (invalid or len(names) != 1 or {key[1] for key in found} != {0, 1, 2}
                or {value[2][2] for value in found.values()} != {'>', '=', '<'}):
            continue
        output_name = next(iter(names))
        references = [f'{output_name}' + (f'+{offset}' if offset else '') for offset in range(3)]
        held = re.sub(r'\s+', '', hold_match[2]).replace('and', ',').split(',')
        if [s for s in held if s] != references:
            continue
        ordered = [found[(output_name, i)] for i in range(3)]
        first, last = ordered[0][0].start, ordered[-1][1].end
        on_cells = [c for c in unit.cells if c.column <= 1 and first <= c.start < last
                    and c.value == 'Turns ON in the case of']
        if len(on_cells) != 3:
            continue
        value_type = {'kind': 'int', 'bits': width, 'signed': True}
        boolean = {'kind': 'bool'}
        operators = {'>': 'gt', '=': 'eq', '<': 'lt'}
        members = []
        delta, _ = _source_offset_basis(record)
        for offset, (cell, other, relation) in enumerate(ordered):
            members.append({'output': references[offset], 'target': {'parameter': output_name, 'offset': offset, 'unit': 'bit'},
                            'comparison': f'{relation[1]} {relation[2]} {relation[3]}',
                            'expression': {'op': operators[relation[2]], 'type': boolean,
                                           'args': [{'op': 'parameter', 'name': symbol, 'type': value_type} for symbol in (relation[1], relation[3])]},
                            'value_spans': [{'start': c.start + delta, 'end': c.end + delta} for c in (cell, other)]})
        witness = [body[0], notes[0], *command, *on_cells, body[hold_index], tails[0]]
        witness.extend(c for pair in ordered for c in pair[:2])
        source = source_location(record, unit, [(c.start, c.end) for c in witness])
        source.update(target_model=target_model, opcode=opcode)
        # Text is a projection of the explicit source cells, not a case answer.
        prose = '\n'.join([body[0].value, notes[0].value,
                           f'Command input controls {hold_match[1]} execution. When the instruction executes:',
                           *[f"{m['output']}: Turns ON in the case of [{m['comparison']}]." for m in members],
                           body[hold_index].value + ' ' + tails[0].value])
        groups.append({'id': f"{record['id']}:result-chart:{width}:{unit.page}",
                       'representation': 'layout_cell_projection', 'value_type': value_type,
                       'members': members, 'source': source, 'text': prose, 'disabled': 'retain',
                       'source_unit_id': unit.id})
    return groups

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
    in_note = False
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
                or bool(re.fullmatch(r'[A-Z]\d+\s+to\s+[A-Z]\d+', cell))
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
        # A multiline footnote is a separate source annotation. Its lowercase
        # continuation must not extend the final wrapped operand description.
        if re.match(r'^(?:\*\d+[.:]|\[GLYPH-[0-9A-F]+\]\d+:)', cells[0], re.I):
            in_note = True
            pending = []
            cursor += len(line)
            continue
        # A merged extraction may repeat the set-data header above applicable-
        # device rows. Availability checkmarks are not a parameter description
        # and cannot anchor preceding prose as that parameter's purpose.
        if re.fullmatch(r'(?:\[GLYPH-F0(?:50|70)\](?:\d+)?\s*)+', description, re.I):
            pending = []
            cursor += len(line)
            continue
        symbols = ([native_symbol(cell) for cell in cells[:description_column]] if "|" in line else
                   [native_symbol(line.strip())] if pending and native_symbol(line.strip()) in expected else [])
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
            if in_note:
                cursor += len(line)
                continue
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
                        and (closing_range or fragment[0].startswith(("(", "[")) or fragment[0][0].islower()
                             or re.match(re.escape(rows[-1]['symbol']) + r'\s*[+:]', fragment[0]))):
                    rows[-1]["parts"].append(fragment)
                    rows[-1]["end"] = cursor + len(line.rstrip("\r\n"))
                elif rows and fragment[0].startswith(("(", "[")):
                    pass  # inline-row notes remain context, never a prefix of the next row
                else:
                    pending.append(fragment)
            cursor += len(line)
            continue
        in_note = False
        valid_description = (description_fragment(description)
                             and native_symbol(description) not in expected)
        wrapped = not valid_description or bool(pending and (description.startswith(("(", "[", "-")) or
            re.match(re.escape(symbols[0]) + r'\s*[+:]', description)))
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
            # A missing delimiter means this representation is incomplete.
            # Keep its original unit, but do not introduce a conflicting flat
            # purpose from a damaged cell or guess the missing character.
            if any(values['purpose'].count(left) != values['purpose'].count(right)
                   for left, right in [('(', ')'), ('[', ']')]):
                del values['purpose']
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

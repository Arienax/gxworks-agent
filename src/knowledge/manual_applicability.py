"""Source-page applicability, shared by the offline manual build.

These are evidence scopes, not instruction admission or native verification.
Unrecognised layouts remain unknown; a continuation inherits only an explicit
outline ancestor's header. Version conditions remain attached to the source.
"""
from __future__ import annotations

import json
import re

from knowledge.instruction_document import extract_model_badge_marks

SCOPE_EXTRACTOR_VERSION = 2


def numeric_named_glyph_characters(characters, font_encodings):
    """Decode the page's explicit C<ASCII> glyph names, without guessing CIDs.

    Some embedded legacy fonts omit ToUnicode but declare every glyph through
    Encoding/Differences (for example C83 for S). Only that complete, printable
    numeric-name convention is read; other font encodings are left untouched.
    """
    encodings = {}
    for font in font_encodings:
        if font.get('to_unicode') or not font.get('fontname'):
            continue
        mapping, code, valid = {}, None, True
        for item in font.get('differences') or ():
            if type(item) is int:
                code = item
                continue
            name = re.fullmatch(r'C(\d+)', str(item))
            if code is None or not name or not 32 <= int(name[1]) <= 126:
                valid = False
                break
            mapping[code] = chr(int(name[1]))
            code += 1
        if valid and mapping:
            prior = encodings.get(font['fontname'])
            encodings[font['fontname']] = mapping if prior is None or prior == mapping else {}
    output = []
    for character in characters:
        text = character.get('text', '')
        cid = re.fullmatch(r'\(cid:(\d+)\)', text)
        code = int(cid[1]) if cid else ord(text) if len(text) == 1 else None
        decoded = encodings.get(character.get('fontname'), {}).get(code, text)
        output.append({**character, 'text': decoded})
    return output


def reviewed_header_applicability(record, reviews):
    """Bind a checked artwork reading to its exact source heading only.

    This is offline source input for badges drawn as outlines. It does not
    supply runtime rules and cannot borrow a different page or manual edition.
    """
    candidates = []
    pages = record.get('pdf_pages') or ()
    for review in reviews or ():
        if (not pages or type(review.get('pdf_page')) is not int
                or review['pdf_page'] != min(pages)
                or any(review.get(key) != record.get(key)
                       for key in ('manual_id', 'manual_number', 'revision', 'section'))
                or review.get('verification') != 'source_checked'
                or review.get('status') != 'source_verified'):
            continue
        source = review.get('source')
        if (not isinstance(source, dict)
                or any(source.get(key) != record.get(key)
                       for key in ('manual_id', 'manual_number', 'revision'))
                or source.get('pdf_pages') != [review['pdf_page']]
                or source.get('representation') != 'instruction_header_cpu_version_badges'
                or not source.get('review_artifact')):
            continue
        marks = review.get('model_marks')
        if (not isinstance(marks, list) or not marks
                or len({mark.get('model') for mark in marks if isinstance(mark, dict)}) != len(marks)
                or {mark.get('model') for mark in marks if isinstance(mark, dict)}
                   != set(record.get('manual_models') or ())):
            continue
        valid = True
        for mark in marks:
            if not isinstance(mark, dict) or mark.get('state') not in {'supported', 'conditional', 'unsupported'}:
                valid = False
                break
            minimum = mark.get('minimum_version')
            if minimum is not None and (mark['state'] != 'conditional'
                    or not isinstance(minimum, str) or not re.fullmatch(r'\d+(?:\.\d+)+', minimum)):
                valid = False
                break
            if mark['state'] == 'conditional' and minimum is None:
                valid = False
                break
        if valid:
            candidates.append({'basis': 'instruction_header', 'model_marks': marks,
                'supported_models': [mark['model'] for mark in marks if mark['state'] != 'unsupported'],
                'conditional_models': [mark['model'] for mark in marks if mark['state'] == 'conditional'],
                'evidence_pages': [review['pdf_page']], 'reviewed_source': source})
    if not candidates or any(candidate != candidates[0] for candidate in candidates[1:]):
        return None
    return candidates[0]


def _inside(obj, box, tolerance=1):
    return (obj['x0'] >= box[0] - tolerance and obj['x1'] <= box[2] + tolerance
            and obj['top'] >= box[1] - tolerance and obj['bottom'] <= box[3] + tolerance)


def _points(obj):
    return {(round(x, 1), round(y, 1)) for x, y in obj.get('pts', [])}


def _fx_matrix(words, curves, lines, layout):
    cells = layout['cells']
    labels = [w for w in words if 75 < w['top'] < 170 and w['text'] == cells[0]['label']]
    if len(labels) != 1:
        return None
    top = labels[0]['top']
    marks = []
    for cell in cells:
        candidates = [w for w in words if abs(w['top'] - top) < 3 and w['text'] == cell['label']]
        if len(candidates) != 1:
            return None
        word = candidates[0]
        box = [word['x0'] - 3, word['bottom'] + 1, word['x1'] + 3, word['bottom'] + 18]
        shapes = [o for o in curves if _inside(o, box)]
        diagonals = [o for o in lines if _inside(o, box) and
                     3 < o['x1'] - o['x0'] < 9 and 3 < o['bottom'] - o['top'] < 9]
        circle = any(len(_points(o)) >= 8 and 4 < o['x1']-o['x0'] < 9 for o in shapes)
        triangle = any(len(_points(o)) == 3 for o in shapes)
        cross = len({_points_key(o) for o in diagonals}) >= 2
        states = [state for yes, state in ((circle, 'supported'), (triangle, 'conditional'), (cross, 'unsupported')) if yes]
        if len(states) != 1:
            return None
        marks.extend({'model': model, 'state': states[0]} for model in cell['models'])
    return marks


def _points_key(obj):
    return tuple(sorted(_points(obj)))


def ql_header_symbols(words):
    """Literal mnemonic row above the six CPU icons, including '(P)' forms."""
    labels = [w for w in words if w['text'] == 'Basic' and 50 < w['x0'] < 60 and w['top'] < 230]
    if len(labels) != 1:
        return []
    top = labels[0]['top']
    rows = {}
    for word in words:
        if 50 < word['x0'] < 547 and top-40 < word['top'] < top-15:
            rows.setdefault(round(word['top']), []).append(word)
    for _,parts in sorted(rows.items(), reverse=True):
        line = ' '.join(w['text'] for w in sorted(parts,key=lambda w:w['x0'])).strip()
        line = re.sub(r'\s*\.\s*', '.', line)
        if re.fullmatch(r'[A-Z0-9_.$+<>=!*/,()\-\s]+',line) and re.search(r'[A-Z]{2}',line):
            names = []
            for match in re.finditer(r'([A-Z][A-Z0-9_.$+<>=!\-]*(?:\(P\)[A-Z0-9_.$+<>=!\-]*)?)', line):
                name = match[1]
                names.append(name.replace('(P)', ''))
                if '(P)' in name:
                    names.append(name.replace('(P)', 'P'))
            return list(dict.fromkeys(names))
    return []


def source_instruction_symbols(text, outline=""):
    """Literal declaration/heading symbols, never calls found in examples.

    Dedicated hardware manuals do not have the common-instruction CPU icon
    row. Their section heading, named instruction list or EN signature still
    owns a symbol. This establishes identity only, not CPU admission or arity.
    """
    symbol = r"[A-Z][A-Z0-9_.$]*"
    declarations = []
    leaf = str(outline or "").split(" > ")[-1]
    # Structured function chapters declare their callable form before '/'.
    # Preserve the enable form as a separate identity, just as '(P)' forms.
    heading = re.match(r'^\d+(?:\.\d+)+\s+([A-Z][A-Z0-9_]*)(\(_E\))?\s*/', leaf)
    if heading:
        declarations.append(heading[1])
        if heading[2]:
            declarations.append(heading[1] + '_E')
    # A numbered instruction section may name its literal form in parentheses.
    if re.match(r"^\d+(?:\.\d+)+\s", leaf):
        declarations.extend(re.findall(r"\((" + symbol + r"(?:\(P\))?)\)\s*$", leaf))
    for line in str(text or "").splitlines():
        line = line.strip()
        signature = re.fullmatch(
            r"(?:ENO\s*:=\s*)?(" + symbol + r")\s*\(\s*EN(?:\s*[,)]).*", line
        )
        if signature:
            declarations.append(signature[1])
        heading = re.fullmatch(
            r"(?:\(\d+\)\s*)?[^:]*\binstructions?\s*:\s*("
            + symbol + r"(?:\(P\))?(?:\s*,\s*" + symbol + r"(?:\(P\))?)*)", line,
            re.I,
        )
        if heading:
            declarations.extend(part.strip() for part in heading[1].split(",") if part.strip().upper() == part.strip())
    result = []
    for declaration in declarations:
        if declaration.endswith("(P)"):
            result.extend((declaration[:-3], declaration[:-3] + "P"))
        else:
            result.append(declaration)
    return list(dict.fromkeys(result))


def _ql_icons(words, curves, layout):
    # The six rounded icon cells have stable x positions, but their y position
    # moves with the heading. Locate the row from all six labels, never prose.
    labels = [w for w in words if w['text'] == 'Basic' and 50 < w['top'] < 230 and 50 < w['x0'] < 60]
    if len(labels) != 1:
        return None
    word = labels[0]
    cells = layout['cells']
    # Some pages translate the complete icon strip by about one point. Locate
    # its first enclosing icon, then preserve the configured relative cells.
    first_boxes = [o for o in curves if len(o.get('pts', [])) == 9
                   and 24 < o['x1'] - o['x0'] < 28
                   and _inside(word, [o['x0'], o['top'], o['x1'], o['bottom']])]
    offsets = {round(o['x0'] - cells[0]['x0'], 2) for o in first_boxes}
    if len(offsets) != 1 or abs(next(iter(offsets))) > 3:
        return None
    offset = next(iter(offsets))
    cells = [{**cell, 'x0': cell['x0'] + offset, 'x1': cell['x1'] + offset} for cell in cells]
    for cell in cells:
        # High/performance uses two lines and individually positioned glyphs
        # in some revisions. Confirm the complete label within its own cell.
        fragments = [w for w in words if word['bottom']-18 < w['top'] < word['bottom']+2
                     and cell['x0'] <= w['x0'] < cell['x1']]
        label = ''.join(w['text'] for w in sorted(fragments, key=lambda w: (round(w['top']), w['x0'])))
        if re.sub(r'\W', '', cell['label']).casefold() not in re.sub(r'\W', '', label).casefold():
            return None
    marks = []
    for cell in cells:
        candidates = [o for o in curves if len(o.get('pts', [])) == 9
                      and abs(o['x0'] - cell['x0']) < .6 and abs(o['x1'] - cell['x1']) < .6
                      and o['top'] < word['bottom'] < o['bottom']]
        boxes = {(round(o['top'], 1), round(o['bottom'], 1)) for o in candidates}
        if len(boxes) != 1:
            return None
        top, bottom = next(iter(boxes))
        # The published X icon is a closed 13-point outline (two diagonal
        # strokes). Restrict recognition to the icon, outside diagrams.
        cross = any(len(o.get('pts', [])) == 13 and 9 < o['x1']-o['x0'] < 14
                    and 9 < o['bottom']-o['top'] < 14 and
                    _inside(o, [cell['x0']-1, top-15, cell['x1']+1, top+5]) for o in curves)
        conditional = any(w['text'] == 'Ver.' and
                          _inside(w, [cell['x0']-1, top-15, cell['x1']+1, top+3]) for w in words)
        if cross and conditional:
            return None
        state = 'unsupported' if cross else 'conditional' if conditional else 'supported'
        marks.extend({'model': model, 'state': state} for model in cell['models'])
    return marks


def _iqf_icons(words, curves, lines, layout):
    """Read the four CPU availability badges defined by the iQ-F legend."""
    cells = layout['cells']
    anchors = [w for w in words if w['text'] == cells[0]['label']
               and 45 < w['top'] < 240 and 45 < w['x0'] < 85]
    rows = []
    for anchor in anchors:
        labels = [[w for w in words if w['text'] == cell['label']
                   and abs(w['top'] - anchor['top']) < 3
                   and cell['x0'] - 3 < w['x0'] < cell['x1'] + 3] for cell in cells]
        if all(len(matches) == 1 for matches in labels):
            rows.append([matches[0] for matches in labels])
    if len(rows) != 1:
        return None
    marks = []
    for cell, word in zip(cells, rows[0]):
        boxes = {(round(o['x0'], 2), round(o['top'], 2), round(o['x1'], 2), round(o['bottom'], 2))
                 for o in curves if len(o.get('pts', [])) == 9
                 and 40 < o['x1'] - o['x0'] < 45 and 15 < o['bottom'] - o['top'] < 20
                 and _inside(word, [o['x0'], o['top'], o['x1'], o['bottom']])}
        if len(boxes) != 1:
            return None
        box = next(iter(boxes))
        mark_area = [box[0] - 1, box[1] - 15, box[2] + 1, box[3] + 1]
        cross = any(len(o.get('pts', [])) == 13 and 9 < o['x1'] - o['x0'] < 14
                    and 9 < o['bottom'] - o['top'] < 14 and _inside(o, mark_area) for o in curves)
        cross = cross or any(w['text'] in {'×', '\uf0c8', '[GLYPH-F0C8]'}
                             and _inside(w, mark_area) for w in words)
        diagonals = [o for o in lines if 3 < o['x1'] - o['x0'] < 14
                     and 3 < o['bottom'] - o['top'] < 14 and _inside(o, mark_area)]
        cross = cross or len({_points_key(o) for o in diagonals}) >= 2
        conditional = any(w['text'] == 'Ver.' and _inside(w, mark_area) for w in words)
        if cross and conditional:
            return None
        state = 'unsupported' if cross else 'conditional' if conditional else 'supported'
        marks.extend({'model': model, 'state': state} for model in cell['models'])
    return marks


def _legacy_grey_fill(rect):
    color = rect.get('non_stroking_color')
    if isinstance(color, (tuple, list)) and len(color) == 4:
        c, m, y, k = color
        brightness = sum((1-v)*(1-k) for v in (c, m, y))/3
    elif isinstance(color, (tuple, list)):
        brightness = sum(color)/len(color)
    else:
        brightness = float(color or 0)
    return bool(rect.get('fill')) and .5 < brightness < .99


def _legacy_partial_fill(shape):
    """A partially shaded badge is conditional, rather than an empty badge."""
    points = shape.get('pts') or []
    if not _legacy_grey_fill(shape) or len(points) < 3:
        return False
    area = abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2)
                   in zip(points, [*points[1:], points[0]]))) / 2
    bounds = (shape['x1'] - shape['x0']) * (shape['bottom'] - shape['top'])
    return bounds > 0 and .05 < area / bounds < .95


def _legacy_shading(words, rectangles, layout, *, polygons=()):
    labels = []
    for cell in layout['cells']:
        candidates = [w for w in words if 80 < w['top'] < 150 and w['x0'] > 300
                      and w['text'].casefold() == cell['label'].casefold()]
        if len(candidates) != 1:
            return None
        labels.append((cell, candidates[0]))
    if max(w['top'] for _, w in labels) - min(w['top'] for _, w in labels) > 3:
        return None
    marks = []
    for cell, word in labels:
        shades = []
        for rect in rectangles:
            if not rect.get('fill') or not _inside(word, [rect['x0'], rect['top'], rect['x1'], rect['bottom']]):
                continue
            if not 20 < rect['x1'] - rect['x0'] < 45 or not 12 < rect['bottom']-rect['top'] < 22:
                continue
            if _legacy_grey_fill(rect):
                shades.append(rect)
        partial = any(_legacy_partial_fill(shape)
                      and 20 < shape['x1'] - shape['x0'] < 45
                      and 12 < shape['bottom'] - shape['top'] < 22
                      and _inside(word, [shape['x0'], shape['top'], shape['x1'], shape['bottom']])
                      for shape in polygons)
        state = 'conditional' if partial else 'supported' if shades else 'unsupported'
        marks.extend({'model': model, 'state': state} for model in cell['models'])
    return marks


def _legacy_heading_tops(words):
    """Numbered chapter/section titles also end the preceding instruction."""
    return sorted({w['top'] for w in words if w['x0'] < 110
                   and re.fullmatch(r'\d+(?:\.\d+)+', w['text'])})


def legacy_instruction_sections(words, rectangles, tables, *, layout, characters=(), polygons=()):
    """Read each legacy heading's own CPU strip and optional per-form matrix.

    A physical page can contain several independent instructions. Continuation
    strips and neighbouring instructions never supply a heading's CPU scope.
    Table/cell geometry uses the same shape as SourcePageGeometry.
    """
    if layout.get('kind') != 'legacy_shading':
        return []
    headings = []
    for word in words:
        if word['x0'] >= 110 or not re.fullmatch(r'\d+(?:\.\d+){2,}', word['text']):
            continue
        line = ' '.join(w['text'] for w in sorted(words, key=lambda w: w['x0'])
                        if abs(w['top'] - word['top']) < 3)
        match = re.match(r'(\d+(?:\.\d+){2,})\s+([A-Z][A-Z0-9]*)\s+\(FNC\s*(\d+)\)', line)
        if match:
            headings.append({'section': match[0], 'opcode': match[2],
                             'fnc_number': int(match[3]), 'top': word['top']})
    headings.sort(key=lambda h: h['top'])
    compact = lambda text: re.sub(r'[^a-z0-9]', '', text.casefold())
    categories = {'pulsep': 'pulse', '16bitoperation': '16', '32bitoperation': '32'}
    for index, heading in enumerate(headings):
        top = heading['top']
        bottom = min((y for y in _legacy_heading_tops(words) if y > top + 1), default=float('inf'))
        heading['bottom'] = bottom
        anchors = [w for w in words if top <= w['top'] < min(bottom, top + 35)
                   and w['x0'] > 300 and w['text'].casefold() == layout['cells'][0]['label'].casefold()]
        marks = None
        if len(anchors) == 1:
            shift = anchors[0]['top'] - 100
            def normalize(item):
                return {**item, 'top': item['top'] - shift, 'bottom': item['bottom'] - shift}
            marks = _legacy_shading([normalize(w) for w in words if top <= w['top'] < bottom],
                                    [normalize(r) for r in rectangles], layout,
                                    polygons=[normalize(shape) for shape in polygons])
        heading['model_marks'], heading['form_models'] = marks, {}
        form_tables = list(tables)
        # A snapped table can lose an outer header entirely. Recover only an
        # original, labelled header rectangle and its immediately adjacent row;
        # the same CPU-cell checks below still decide every support value.
        for rect in rectangles:
            box = tuple(rect[k] for k in ('x0', 'top', 'x1', 'bottom'))
            if not top < box[1] < bottom:
                continue
            text = ''.join(c['text'] for c in sorted(characters, key=lambda c: c['x0'])
                           if _inside(c, box, tolerance=.6))
            if compact(text) not in categories:
                continue
            adjacent = [r for r in rectangles if abs(r['top'] - box[3]) < 1
                        and box[0] <= (r['x0'] + r['x1']) / 2 <= box[2]
                        and 4 < r['x1'] - r['x0'] < (box[2] - box[0]) / len(layout['cells']) * 1.25]
            bottoms = {round(r['bottom'], 1) for r in adjacent}
            if len(bottoms) == 1:
                form_tables.append({'bbox': [*box[:3], next(iter(bottoms))],
                                    'cells': [{'bbox': box, 'text': text}]})
        for table in form_tables:
            if not top < table['bbox'][1] < bottom:
                continue
            headers = [cell for cell in table['cells'] if compact(cell['text']) in categories]
            if not headers:
                continue
            for header in headers:
                category = categories[compact(header['text'])]
                x0, _, x1, body_top = header['bbox']
                body_bottom = table['bbox'][3]
                if body_bottom - body_top < 2:
                    # A single-row strip shades the form itself. Its CPU
                    # header cannot make a white 32-bit form applicable.
                    # Table extraction snaps edges by up to three points;
                    # match the original cell, never its enclosing strip.
                    frame = [r for r in rectangles if all(abs(r[key] - value) < 3
                        for key, value in zip(('x0', 'top', 'x1', 'bottom'), header['bbox']))]
                    if not frame:
                        states = None
                    else:
                        shaded = any(_legacy_grey_fill(r) for r in frame)
                        partial = any(_legacy_partial_fill(shape) and any(
                            _inside(shape, [r[key] for key in ('x0', 'top', 'x1', 'bottom')], tolerance=1)
                            for r in frame) for shape in polygons)
                        states = {}
                        for mark in marks or ():
                            if (not shaded and not partial) or mark['state'] == 'unsupported':
                                state = False
                            elif shaded and not partial and mark['state'] == 'supported':
                                state = True
                            else:
                                state = None
                            states[mark['model']] = state
                else:
                    states = {}
                    # PDF table snapping can cut a printed label or merge two
                    # adjacent cells. The five drawn badge boxes own the CPU
                    # columns; equal divisions of a snapped envelope do not.
                    boxes = {tuple(round(r[k], 1) for k in ('x0', 'top', 'x1', 'bottom'))
                             for r in rectangles if x0 <= (r['x0'] + r['x1']) / 2 <= x1
                             and abs(r['top'] - body_top) < 1
                             and abs(r['bottom'] - body_bottom) < 1
                             and 4 < r['x1'] - r['x0'] < (x1 - x0) / len(layout['cells']) * 1.25}
                    boxes = sorted(boxes)
                    if len(boxes) != len(layout['cells']):
                        heading['form_models'][category] = None
                        continue
                    for box, configured in zip(boxes, layout['cells']):
                        text = ''.join(c['text'] for c in sorted(characters, key=lambda c: c['x0'])
                                       if _inside(c, box, tolerance=.6))
                        if compact(text) != compact(configured['label']):
                            states = None
                            break
                        shaded = any(_legacy_grey_fill(r) and _inside(r, box, tolerance=1)
                                     and r['bottom'] - r['top'] > (body_bottom - body_top) * .7
                                     for r in rectangles)
                        partial = any(_legacy_partial_fill(shape) and _inside(shape, box, tolerance=1)
                                      and shape['bottom'] - shape['top'] > (body_bottom - body_top) * .7
                                      for shape in polygons)
                        states.update({model: None if partial else shaded for model in configured['models']})
                prior = heading['form_models'].get(category)
                heading['form_models'][category] = states if category not in heading['form_models'] or prior == states else None
    return headings


def extract_header_applicability(words, rectangles, curves, lines, layout):
    """Read a configured and visually reviewed header, or return unknown."""
    kind = layout.get('kind')
    if kind == 'fx_matrix':
        marks = _fx_matrix(words, curves, lines, layout)
    elif kind == 'ql_icons':
        marks = _ql_icons(words, curves, layout)
    elif kind == 'iqf_icons':
        marks = _iqf_icons(words, curves, lines, layout)
    elif kind == 'legacy_shading':
        marks = _legacy_shading(words, rectangles, layout)
    elif layout.get('model_badges'):
        # The existing compiler owns FX3 badge geometry. Preserve that parser.
        raw = extract_model_badge_marks(rectangles, layout)
        marks = [] if raw is not None else None
        for mark in raw or ():
            content = ' '.join(w['text'] for w in words if _inside(w, mark['bbox']))
            # Several FX3 revisions draw the version strip as artwork rather
            # than text. Recognising the active badge must not erase that
            # unresolved firmware condition or pretend it is unconditional.
            state = 'conditional' if mark['highlighted'] else 'unsupported'
            marks.append({'model': mark['model'], 'state': state, 'header_text': content,
                          'version_requirement': 'consult_source_icon' if mark['highlighted'] else None})
    else:
        marks = None
    if marks is None:
        return None
    result = {'basis': 'instruction_header', 'model_marks': marks,
            'supported_models': [m['model'] for m in marks if m['state'] != 'unsupported'],
            'conditional_models': [m['model'] for m in marks if m['state'] == 'conditional'],
            'condition': 'Check the cited section for CPU firmware/serial-number and operand restrictions.'}
    if kind == 'ql_icons':
        result['opcodes'] = ql_header_symbols(words)
    return result


def apply_manual_scope(connection, spec, source, *, pdf_path=None, page_headers=None):
    """Apply one shared extraction to every source chapter, including continuations."""
    columns = {row[1] for row in connection.execute('PRAGMA table_info(chunks)')}
    if 'source_applicability_json' not in columns:
        connection.execute("ALTER TABLE chunks ADD COLUMN source_applicability_json TEXT NOT NULL DEFAULT ''")
    scope = ','.join(source['plc_models'])
    connection.execute('UPDATE manuals SET plc_models=? WHERE manual_id=?', (scope, spec.id))
    connection.execute('UPDATE chunks SET plc_models=? WHERE manual_id=?', (scope, spec.id))
    layout = source.get('applicability_layout') or source.get('instruction_layout')
    anchors = {}
    def add_header(outline, header, page_number):
        prior = anchors.get(outline)
        if prior and (prior.get('basis') == 'conflicting_headers' or
                      prior['model_marks'] != header['model_marks']):
            anchors[outline] = {'basis':'conflicting_headers', 'supported_models':[], 'model_marks':[],
                                'instruction_support':'unknown',
                                'evidence_pages': sorted(set(prior['evidence_pages'] + [page_number]))}
        elif prior:
            prior['evidence_pages'] = sorted(set(prior['evidence_pages'] + [page_number]))
            if prior.get('opcodes') or header.get('opcodes'):
                prior['opcodes'] = list(dict.fromkeys([*prior.get('opcodes', []), *header.get('opcodes', [])]))
        else:
            anchors[outline] = {**header, 'evidence_pages':[page_number]}
    if page_headers is not None:
        for page_number, outline, header in page_headers:
            if header is not None and outline:
                add_header(outline, header, page_number)
    elif layout:
        import pdfplumber
        artifact_columns = {row[1] for row in connection.execute('PRAGMA table_info(page_artifacts)')}
        with pdfplumber.open(str(pdf_path or spec.path)) as pdf:
            for row in connection.execute('SELECT pdf_page,outline_path FROM page_artifacts WHERE manual_id=? ORDER BY pdf_page', (spec.id,)):
                page_number, outline = row
                if not outline or re.search(r'how to read|instruction list|table of contents', outline, re.I):
                    continue
                page = pdf.pages[page_number-1]
                words = page.extract_words(x_tolerance=2, y_tolerance=3)
                header = extract_header_applicability(words, page.rects, page.curves, page.lines, layout)
                if 'visual_json' in artifact_columns:
                    stored = connection.execute('SELECT visual_json FROM page_artifacts WHERE manual_id=? AND pdf_page=?',
                                                (spec.id, page_number)).fetchone()
                    visual = json.loads(stored[0] or '{}')
                    visual.pop('source_applicability', None)
                    if header is not None:
                        visual['source_applicability'] = header
                    connection.execute('UPDATE page_artifacts SET visual_json=? WHERE manual_id=? AND pdf_page=?',
                                       (json.dumps(visual, ensure_ascii=False), spec.id, page_number))
                if header is not None:
                    add_header(outline, header, page_number)
                page.flush_cache()
    stats = {'headers': len(anchors), 'scoped_chunks': 0, 'conditional_chunks': 0, 'unknown_chunks': 0,
             'applicability_layout': layout, 'extractor_version': SCOPE_EXTRACTOR_VERSION}
    for row in connection.execute('SELECT id,outline_path FROM chunks WHERE manual_id=?', (spec.id,)).fetchall():
        chunk_id, outline = row
        parents = [key for key in anchors if outline == key or outline.startswith(key + ' > ')]
        if not parents:
            evidence = {'basis': 'manual_scope', 'supported_models': source['plc_models'], 'instruction_support': 'unknown'}
            stats['unknown_chunks'] += 1
            models = scope
        else:
            evidence = anchors[max(parents, key=len)]
            models = ','.join(m for m in evidence['supported_models'] if m in source['plc_models']) or '__NO_APPLICABLE_CPU__'
            stats['scoped_chunks'] += 1
            stats['conditional_chunks'] += bool(evidence.get('conditional_models'))
        connection.execute('UPDATE chunks SET plc_models=?,source_applicability_json=? WHERE id=?',
                           (models, json.dumps(evidence, ensure_ascii=False, separators=(',', ':')), chunk_id))
    connection.execute('UPDATE entity_index SET plc_models=(SELECT plc_models FROM chunks WHERE id=entity_index.chunk_id) WHERE manual_id=?', (spec.id,))
    return stats

"""Source-page applicability, shared by the offline manual build.

These are evidence scopes, not instruction admission or native verification.
Unrecognised layouts remain unknown; a continuation inherits only an explicit
outline ancestor's header. Version conditions remain attached to the source.
"""
from __future__ import annotations

import json
import re

from knowledge.instruction_document import extract_model_badge_marks


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
        if re.fullmatch(r'[A-Z0-9_.$+<>=!*/,()\-\s]+',line) and re.search(r'[A-Z]{2}',line):
            names = []
            for match in re.finditer(r'([A-Z][A-Z0-9_.$+<>=!\-]*)(\(P\))?',line):
                names.append(match[1])
                if match[2]:
                    names.append(match[1]+'P')
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


def _legacy_shading(words, rectangles, layout):
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
            color = rect.get('non_stroking_color')
            if isinstance(color, (tuple, list)) and len(color) == 4:
                c, m, y, k = color
                brightness = sum((1-v)*(1-k) for v in (c,m,y))/3
            elif isinstance(color, (tuple, list)):
                brightness = sum(color)/len(color)
            else:
                brightness = float(color or 0)
            if .5 < brightness < .99:
                shades.append(rect)
        marks.extend({'model': model, 'state': 'supported' if shades else 'unsupported'} for model in cell['models'])
    return marks


def extract_header_applicability(words, rectangles, curves, lines, layout):
    """Read a configured and visually reviewed header, or return unknown."""
    kind = layout.get('kind')
    if kind == 'fx_matrix':
        marks = _fx_matrix(words, curves, lines, layout)
    elif kind == 'ql_icons':
        marks = _ql_icons(words, curves, layout)
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
        else:
            anchors[outline] = {**header, 'evidence_pages':[page_number]}
    if page_headers is not None:
        for page_number, outline, header in page_headers:
            if header is not None and outline:
                add_header(outline, header, page_number)
    elif layout:
        import pdfplumber
        with pdfplumber.open(str(pdf_path or spec.path)) as pdf:
            for row in connection.execute('SELECT pdf_page,outline_path FROM page_artifacts WHERE manual_id=? ORDER BY pdf_page', (spec.id,)):
                page_number, outline = row
                if not outline or re.search(r'how to read|instruction list|table of contents', outline, re.I):
                    continue
                page = pdf.pages[page_number-1]
                words = page.extract_words(x_tolerance=2, y_tolerance=3)
                header = extract_header_applicability(words, page.rects, page.curves, page.lines, layout)
                if header is not None:
                    add_header(outline, header, page_number)
                page.flush_cache()
    stats = {'headers': len(anchors), 'scoped_chunks': 0, 'conditional_chunks': 0, 'unknown_chunks': 0,
             'applicability_layout': layout}
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

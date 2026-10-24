"""Canonical source-language facts for JY997D55801 AB.

The caller owns facts, references, CPU applicability and dependency closure. This
helper reads the already indexed language tables and diagrams, never the runtime
instruction registry. It returns fact payloads only; it grants no Core capability.
"""
from __future__ import annotations

import json
import re

from knowledge.instruction_document import instruction_declaration_names


MANUAL_ID = 'fx5_programming_ab'
DESIGN_MANUAL_ID = 'fx5_program_design_m'
_TYPE = r'(?:ANY[A-Z0-9]*(?:_[A-Z0-9]+)*|BOOL|WORD|DWORD|LWORD|INT|DINT|LINT|UINT|UDINT|ULINT|REAL|LREAL|W?STRING(?:\([0-9]+\))?|TIME|DATE|TIME_OF_DAY|DATE_AND_TIME|POINTER)'
_TYPE_END = re.compile(r'\b(' + _TYPE + r'(?:\s*/\s*' + _TYPE + r')*)(?:\s+(array element))?(?:\s*\*\d+|\s*[()])*\s*$')
_CALL = re.compile(r'\b(?P<result>ENO|[a-z][a-z0-9]*)\s*:=\s*(?P<form>[A-Z][A-Z0-9_.$]*(?:[ \t]+_?[A-Z][A-Z0-9_]*)?)\s*\((?P<args>[^;)]*)\)\s*;?')
_ARG = re.compile(r'(?:EN|ENO|[a-z][a-z0-9]*|Un)\Z')
_NAME = re.compile(r'\(([a-z][a-z0-9]*|U/H|U|N|I|(?<![A-Z0-9_.$+*/<>=!\-\]\)]\()P|Set value)\)|\b(EN|ENO)\b')
_OPTIONAL = re.compile(r'\((P|_U|_E)\)')


def read_language_page_words(source):
    """Optional loader; callers already loading page geometry should reuse it."""
    return {int(page): json.loads(raw or '[]') for page, raw in source.execute(
        'SELECT pdf_page, word_geometry_json FROM page_artifacts WHERE manual_id=?',
        (MANUAL_ID,))}


def _variants(value):
    values = [value]
    while any(_OPTIONAL.search(item) for item in values):
        expanded = []
        for item in values:
            match = _OPTIONAL.search(item)
            expanded.extend((item[:match.start()] + item[match.end():],
                             item[:match.start()] + match[1] + item[match.end():])
                            if match else (item,))
        values = expanded
    return values


def _type_value(text):
    match = _TYPE_END.search(text.strip())
    if not match:
        return None
    values = [part.strip() for part in match[1].split('/')]
    value = values[0] if len(values) == 1 else values
    return {'kind': 'array_element', 'element_type': value} if match[2] else value


def _table_body(layout, standard=False):
    pattern = (r'Argument\s+Description\s+Type\s+Data type' if standard else
               r'Operand\s*(?:\|\s*)?(?:Description|Remarks)[^\n]*Data type(?:[^\n]*)')
    match = re.search(pattern, layout)
    if not match:
        return ''
    rest = layout[match.end():]
    stop = re.search(r'(?m)^(?:\[GLYPH-[^\]]+\])?(?:Applicable devices|Processing details|'
                     r'Operation error|Execution condition Operation result|General function|'
                     r'FX5UJ|FX5S\s*\|)', rest)
    return rest[:stop.start()] if stop else rest


def _layout_operands(layout):
    """Recover named rows and label types omitted from the ruled-table extraction."""
    result, names = [], []
    for raw in _table_body(layout).splitlines():
        line = raw.strip()
        prefix = line.split('|', 1)[0]
        named = list(_NAME.finditer(prefix))
        if named and named[0].start() == 0:
            names = [match[1] or match[2] for match in named]
        form = re.sub(r'\*\d+', '', _NAME.sub('', prefix)).strip()
        if len(named) > 1 and form == '/':
            form = ''
        form = form if re.fullmatch(r'[A-Z$+*/<>=!\-][A-Z0-9_.$+*/<>=!()\-]*', form) else ''
        value = _type_value(line.replace('[GLYPH-F0C9]', ''))
        if value is None and form and re.search(r'\b64-bit unsigned binary\b', line):
            shared = [row for row in result if row['name'] in names and row['type'] == 'ANY32_ARRAY']
            if shared:
                value = shared[-1]['type']
        if not names or value is None:
            continue
        for name in names:
            if (not named and not form and '[GLYPH-' not in prefix
                    and any(row['name'] == name and not row['form'] for row in result)):
                continue
            result.append({'name': name, 'type': value, 'form': form,
                           'source_form_annotation': prefix,
                           'type_basis': 'named_label_type_row'})
    return result


def _geometry_operands(words):
    """Bind the original type column to its original operand row by coordinates."""
    rows = []
    for header in (word for word in words if word['text'] == 'Operand'):
        same = [word for word in words if abs(word['top'] - header['top']) < 2]
        descriptions = [word for word in same if word['text'] in {'Description', 'Remarks'}]
        data = sorted(word['x0'] for word in same if word['text'] == 'Data')
        if not descriptions or not data:
            continue
        desc_x, type_x = descriptions[0]['x0'], data[-1]
        stops = [word['top'] for word in words if word['top'] > header['bottom'] and
                 word['text'] in {'Applicable', 'Processing', 'Precautions', 'Operation', 'Operand'}]
        bottom = min(stops) if stops else header['top'] + 350
        body = sorted((word for word in words if header['bottom'] < word['top'] < bottom),
                      key=lambda word: (word['top'], word['x0']))
        lines = []
        for word in body:
            line = next((line for line in lines if abs(line[0]['top'] - word['top']) < 2), None)
            if line is None:
                lines.append([word])
            else:
                line.append(word)
        names = []
        for line in lines:
            line.sort(key=lambda word: word['x0'])
            left = ' '.join(word['text'] for word in line if word['x0'] < desc_x - 1)
            named = list(_NAME.finditer(left))
            if named and named[0].start() == 0:
                names = [match[1] or match[2] for match in named]
            value = _type_value(' '.join(word['text'] for word in line if word['x0'] >= type_x - 1))
            if value is None or not names:
                continue
            form = re.sub(r'\*\d+', '', _NAME.sub('', left)).strip()
            if len(named) > 1 and form == '/':
                form = ''
            form = form if re.fullmatch(r'[A-Z$+*/<>=!\-][A-Z0-9_.$+*/<>=!()\-]*', form) else ''
            rows.extend({'name': name, 'type': value, 'form': form,
                         'source_form_annotation': left,
                         'type_basis': 'operand_row_label_type_column_geometry'} for name in names
                        if named or form or '[GLYPH-' in left or
                        not any(row['name'] == name and not row['form'] for row in rows))
    return rows


def _standard_operands(layout):
    """PART 5/6 declare directions explicitly in the Argument table."""
    layout = re.sub(r'[ \t]*\|[ \t]*', ' ', layout)
    result, chunks, current = [], [], []
    for raw in _table_body(layout, standard=True).splitlines():
        line = raw.strip()
        if re.match(r'^(?:EN|ENO|[a-z][0-9]*)(?:\(|\s)', line):
            if current:
                chunks.append(' '.join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        chunks.append(' '.join(current))
    for chunk in chunks:
        direction = re.search(r'\b(Input/output|Input/Output|Input|Output) variable\s+', chunk)
        if not direction:
            # A standalone next argument occupies the same merged type and
            # direction cell, e.g. SEL's s2(IN0), followed by s3(IN1).
            if result and re.fullmatch(r'[a-z][0-9]*(?:\([A-Z0-9]+\))?', chunk):
                result.append({**result[-1], 'name': re.match(r'[a-z][0-9]*', chunk)[0],
                               'type_basis': 'argument_table_merged_type_cell'})
            continue
        name = re.match(r'^(ENO|EN|[a-z][0-9]*)', chunk)[1]
        # A footnote may follow the table. Read the type immediately after the
        # explicit direction rather than treating the footnote as part of a type.
        kind = re.match('(' + _TYPE + r'(?:\s*/\s*' + _TYPE + r')*)(\s+array element)?',
                        chunk[direction.end():])
        if not kind:
            continue
        value = _type_value(kind[0])
        row = {'name': name, 'type': value, 'form': '',
               'direction': {'Input': 'input', 'Output': 'output',
                             'Input/output': 'in_out', 'Input/Output': 'in_out'}[direction[1]],
               'type_basis': 'argument_table_data_type',
               'direction_basis': 'argument_table_variable_direction'}
        interval = re.match(r'([a-z])(\d+)(?:\([^)]*\))?\s+to\s+\1(\d+)', chunk)
        if interval:
            row['range'] = (interval[1], int(interval[2]), int(interval[3]))
        # Several functions share one row; parallel slash lists bind return type
        # to function name without guessing from an opcode's spelling.
        if name == 'd' and isinstance(value, list):
            form_names = re.findall(r'\b[A-Z][A-Z0-9_]+(?:\(_E\))?', chunk[:direction.start()])
            if len(form_names) == len(value):
                row['form_types'] = {form: item for form, item in zip(form_names, value)}
        result.append(row)
        grouped = re.match(r'^(?:[a-z][0-9]*(?:\([A-Z0-9]+\))?)(?:,\s*[a-z][0-9]*(?:\([A-Z0-9]+\))?)+', chunk)
        if grouped:
            extra_names = re.findall(r'(?:^|,\s*)([a-z][0-9]*)', grouped[0])[1:]
            result.extend({**row, 'name': extra} for extra in extra_names)
    return result


def _fbd_pins(words, layout, names):
    """Only actual left/right labels of a documented FBD block establish I/O."""
    headers = [word for word in words if word['text'].startswith('FBD/LD')]
    roles = {}
    for header in headers:
        stops = [word['top'] for word in words if word['top'] > header['bottom'] and
                 word['text'] in {'Setting', 'Operand', 'Argument', 'Processing'}]
        bottom = min(stops) if stops else header['top'] + 210
        body = [word for word in words if header['bottom'] < word['top'] < bottom]
        for en in (word for word in body if word['text'] == 'EN'):
            matches = [word for word in body if word['text'] == 'ENO'
                       and abs(word['top'] - en['top']) < 2 and word['x0'] > en['x1']]
            if not matches:
                continue
            eno = min(matches, key=lambda word: word['x0'])
            for word in body:
                if word['text'] not in names or word['top'] < en['top'] - 2:
                    continue
                if abs(word['x0'] - en['x0']) < 3:
                    roles.setdefault(word['text'], set()).add('input')
                if abs(word['x1'] - eno['x1']) < 7 and word['x0'] > en['x1']:
                    roles.setdefault(word['text'], set()).add('output')
    return {name: ('in_out' if len(directions) > 1 else next(iter(directions)))
            for name, directions in roles.items()}


def _call_rows(zone):
    calls, seen = [], set()
    for match in _CALL.finditer(zone):
        arguments = [re.sub(r'\s+', '', part) for part in match['args'].split(',') if part.strip()]
        form = re.sub(r'\s+', '', match['form'])
        identity = (form, tuple(arguments), match['result'])
        if identity in seen:
            continue
        seen.add(identity)
        calls.append({'form': form, 'arguments': arguments, 'result': match['result']})
    return calls


def _statement_rows(zone, declared):
    rows = []
    for match in re.finditer(r'(?<![:=A-Z0-9_])\b([A-Z][A-Z0-9_]*)\(([^;()]*)\)\s*;', zone):
        if match[1] not in declared:
            continue
        arguments = [name.strip() for name in match[2].split(',') if name.strip()]
        if all(_ARG.fullmatch(name) for name in arguments):
            rows.append({'form': match[1], 'arguments': arguments, 'result': None,
                         'kind': 'statement'})
    return rows


def _table_language_negatives(words):
    result = set()
    for word in words:
        if word['text'] != 'Not':
            continue
        if not any(item['text'].rstrip('.') == 'supported' and abs(item['top'] - word['top']) < 3
                   and 0 < item['x0'] - word['x0'] < 30 for item in words):
            continue
        for title, language in (('Structured', 'st'), ('FBD/LD', 'fbd')):
            if any(item['text'].startswith(title) and abs(item['x0'] - word['x0']) < 10
                   and 0 < word['top'] - item['bottom'] < 120 for item in words):
                result.add(language)
    return result


def _fbd_only_calls(words, zone, declared, names):
    """A documented FBD block need not have a corresponding ST call."""
    calls = []
    for header in (word for word in words if word['text'].startswith('FBD/LD')):
        stops = [word['top'] for word in words if word['top'] > header['bottom'] and
                 word['text'] in {'Setting', 'Operand', 'Argument', 'Processing'}]
        bottom = min(stops) if stops else header['top'] + 160
        body = [word for word in words if header['bottom'] < word['top'] < bottom]
        groups = []
        for en in (word for word in body if word['text'] == 'EN'):
            candidates = [word for word in body if word['text'] == 'ENO'
                          and abs(word['top'] - en['top']) < 2 and word['x0'] > en['x1']]
            if not candidates:
                continue
            eno = min(candidates, key=lambda word: word['x0'])
            pins = [word for word in body if word['text'] in names and word['top'] >= en['top'] - 2
                    and (abs(word['x0'] - en['x0']) < 3 or
                         abs(word['x1'] - eno['x1']) < 7 and word['x0'] > en['x1'])]
            pins.sort(key=lambda word: (word['top'], word['x0']))
            captions = [word['text'] for word in body if word['text'] in declared and
                        0 < en['top'] - word['bottom'] < 40 and abs(word['x0'] - en['x0']) < 30]
            groups.append((pins, captions))
        quoted = [name for content in re.findall(r'"([^"]+)"', zone)
                  for name in re.split(r'\s*,\s*', content) if re.fullmatch(r'[A-Z][A-Z0-9_]+', name)]
        for pins, captions in groups:
            forms = captions or (quoted if len(groups) == 1 else [])
            if not forms and len(groups) == 1:
                forms = sorted(declared)
            for form in forms:
                calls.append({'form': form,
                              'arguments': list(dict.fromkeys(word['text'] for word in pins if word['text'] != 'ENO')),
                              'result': 'ENO', 'kind': 'fbd_block', 'languages': ['fbd']})
    return calls


def _fb_call_rows(text, declared):
    """Read instance calls and named pins, including a wrapped final argument.

    A few source calls omit a comma after ENO. The individual named bindings
    remain readable, but the exact sample is not promoted to valid ST syntax.
    """
    calls = []
    for match in re.finditer(r'\b([A-Z][A-Z0-9_]+)_1\s*\(([^;]+)\)\s*;', text):
        if match[1] not in declared:
            continue
        bindings = re.findall(r'([A-Za-z][A-Za-z0-9]*)\s*(:=|=>)\s*(ENO|EN|[a-z][a-z0-9]*)\b', match[2])
        calls.append({'form': match[1], 'arguments': [name for _, _, name in bindings],
                      'result': None, 'bindings': {name: pin for pin, _, name in bindings},
                      'binding_operators': {name: operator for _, operator, name in bindings},
                      'instance': match[1] + '_1',
                      'source_syntax_warning': bool(re.search(
                          r':=\s*(?:EN|ENO|[a-z][a-z0-9]*)\s+[A-Za-z][A-Za-z0-9]*\s*:=', match[2]))})
    return calls


def _language_pages(section, page_words):
    function_block = section['outline'].startswith('PART 6 FUNCTION BLOCKS')
    standard = section['outline'].startswith('PART 5 STANDARD FUNCTIONS') or function_block
    texts = dict(section.get('page_texts') or [])
    pages = []
    for page, layout in section.get('page_layouts') or []:
        header = re.search(r'Ladder(?: diagram)?[^\n]*(?:Structured text|\bST\b)[^\n]*', layout)
        if not header:
            continue
        body = layout[header.start():]
        end = re.search(r'(?m)^(?:Setting data|Operand[^\n]*Description|Argument Description)', body)
        zone = body[:end.start()] if end else body
        declared = instruction_declaration_names(texts.get(page, layout), section['outline'],
                                                   'programming', source_text=section['text'])
        rows = (_standard_operands(layout) if standard else _geometry_operands(page_words.get(page, [])))
        if not standard:
            seen = {(row['name'], row['form'], json.dumps(row['type'], sort_keys=True)) for row in rows}
            rows += [row for row in _layout_operands(layout)
                     if (row['name'], row['form'], json.dumps(row['type'], sort_keys=True)) not in seen]
        comparison_aliases = dict(re.findall(r'\b(EQ|NE|GT|LE|LT|GE) is ([<>=]+)', layout))
        expanded_zone = zone
        if comparison_aliases:
            # The same source explicitly defines EQ/NE/... for this placeholder.
            # A replacement is limited to its printed call, not arbitrary glyphs.
            expanded_zone = re.sub(r'[^\n;]*:=.*?\[GLYPH-F06F\][^;\n]*;',
                lambda match: '\n'.join(match[0].replace('[GLYPH-F06F]', alias)
                                         for alias in comparison_aliases), zone)
        calls = (_fb_call_rows(texts.get(page, layout), declared) if function_block else
                 _call_rows(expanded_zone))
        if not standard:
            calls.extend(_statement_rows(expanded_zone, declared))
        if not declared and calls:
            declared = instruction_declaration_names('\n'.join(texts.values()), section['outline'],
                                                     'programming', source_text=section['text'])
        # A language header in a worked example does not replace its declaration.
        if not declared:
            continue
        if comparison_aliases:
            for row in rows:
                annotation = row.get('source_form_annotation', '')
                if '[GLYPH-F06F]' in annotation and isinstance(row['type'], str):
                    unsigned = '_U' in annotation
                    row['forms'] = [form for form in declared if form.endswith('_U') == unsigned]
        aliases = dict(re.findall(r'language,\s*(U/H|U|N|I|P|d|Set value) displays as ([A-Za-z][A-Za-z0-9]*)',
                                  re.sub(r'\s+', ' ', layout)))
        for row in list(rows):
            if row['name'] in aliases:
                rows.append({**row, 'name': aliases[row['name']]})
        # The dummy string is explicitly printed as a literal rather than as a
        # label type. Its representation and call spelling are both source bound.
        if re.search(r'Displayed as U0 in the ST language and FBD/LD language', layout) and re.search(r'Dummy.*?character string', layout):
            rows.append({'name': 'U0', 'type': 'STRING', 'form': '',
                         'type_basis': 'documented_dummy_string_constant', 'required_literal': 'U0'})
        for row in rows:
            role = 'Execution condition' if row['name'] == 'EN' else 'Execution result' if row['name'] == 'ENO' else ''
            if not standard and role and re.search(r'(?m)^' + row['name'] + r'\b[^\n]*' + role, _table_body(layout)):
                row['direction'] = 'input' if row['name'] == 'EN' else 'output'
                row['direction_basis'] = 'operand_table_execution_role'
        # The source explicitly prints a joined operand label and the language
        # diagram/call uses its concatenated name, e.g. (s5)/(d1) -> s5d1.
        for first, second in re.findall(r'\(([a-z]\d+)\)/\(([a-z]\d+)\)', _table_body(layout)):
            joined = first + second
            if any(joined in call['arguments'] for call in calls):
                for row in list(rows):
                    if row['name'] == first:
                        rows.append({**row, 'name': joined})
        names = ({row['name'] for row in rows} | set(aliases) | {'EN', 'ENO'}
                 | {name for call in calls for name in call['arguments']})
        directions = (_fbd_pins(page_words.get(page, []), layout, names) if not standard else
                      {row['name']: row['direction'] for row in rows})
        directions.update({aliases[name]: value for name, value in list(directions.items()) if name in aliases})
        if not calls and not standard:
            calls = _fbd_only_calls(page_words.get(page, []), zone, declared,
                                    {row['name'] for row in rows} | {'EN', 'ENO'})
        if not standard and 'STL' in declared and re.search(r'FBD/LD[^\n]*\nSTL', zone):
            directions['d'] = 'output'
        if not standard and 'TRAN' in declared and re.search(r'FBD/LD', zone):
            directions['s'] = 'input'
            # Program Design M, PDF 97, prints TRAN(Boolean expression) and
            # the same Boolean transition network ending in the FBD TRAN pin.
            # This type is from that independent manual, not the opcode name.
            rows.append({'name': 's', 'type': 'BOOL', 'form': 'TRAN',
                         'direction': 'input', 'type_basis': 'program_design_transition_boolean_expression',
                         'direction_basis': 'program_design_transition_input_argument'})
        for name, direction in (('EN', 'input'), ('ENO', 'output')):
            if (name in directions and not any(row['name'] == name for row in rows)
                    and any(name in call['arguments'] or name == call['result'] for call in calls)):
                rows.append({'name': name, 'form': '', 'type': 'BOOL', 'direction': direction,
                             'type_basis': 'boolean_execution_condition_result_pin',
                             'direction_basis': 'fbd_pin_side_geometry'})
        if standard and not function_block and 'The function is described as an operator.' in zone:
            for form in declared:
                if form.endswith('_E') or any(call['form'] == form for call in calls):
                    continue
                calls.append({'form': form, 'arguments': [row['name'] for row in rows
                              if row['direction'] == 'input' and row['name'] != 'EN'],
                              'result': 'd', 'kind': 'operator'})
        version = {'st': [], 'fbd': []}
        form_versions = {'st': {}, 'fbd': {}}
        for marker, minimum in re.findall(r'\*(\d+)\s+Supported by engineering tool version\s+"([0-9.]+[A-Z])" and later', layout):
            for language, title in (('st', r'(?:Structured text|\bST)'), ('fbd', 'FBD/LD')):
                if re.search(title + r'\*' + marker + r'\b', zone):
                    version[language].append(minimum)
            # Comparison functions mark only the no-enable ST example. Bind
            # that marker to the actual ST column, not to every table form.
            words = page_words.get(page, [])
            structured = [word for word in words if word['text'] == 'Structured']
            for word in words:
                if word['text'] != 'EN/ENO]*' + marker:
                    continue
                if not any(word['x0'] >= header['x0'] and
                           header['bottom'] < word['top'] < header['bottom'] + 100
                           for header in structured):
                    continue
                prefix = [item for item in words if item['text'] in {'[Without', '[With'}
                          and abs(item['top'] - word['top']) < 3 and 0 < word['x0'] - item['x0'] < 50]
                if len(prefix) != 1:
                    continue
                enabled = prefix[0]['text'] == '[With'
                for form in declared:
                    if form.endswith('_E') == enabled:
                        form_versions['st'].setdefault(form, []).append(minimum)
        variable = re.findall(r'input variable "([a-z])" can be changed in the range of (\d+) to (\d+)', layout)
        pages.append({'page': page, 'declared': declared, 'rows': rows, 'calls': calls,
                      'directions': directions, 'zone': zone,
                      'standard': standard, 'function_block': function_block,
                      'comparison_aliases': comparison_aliases,
                      'negative_languages': _table_language_negatives(page_words.get(page, [])),
                      'has_geometry': bool(page_words.get(page)),
                      'contact_expression': ('This becomes a assignment statement, operator, control syntax, etc.' in zone),
                      'optional_pulse': '(P)' in layout[:header.start()],
                      'versions': version, 'form_versions': form_versions, 'variadic': variable,
                      'layout': layout})
    return pages


def _row_for(rows, name, form, call_form):
    candidates = [row for row in rows if row['name'] == name or
                  row.get('range') and re.fullmatch(row['range'][0] + r'\d+', name)
                  and row['range'][1] <= int(name[1:]) <= row['range'][2]]
    exact = [row for row in candidates if form in row.get('forms', []) or
             row['form'] and form in _variants(row['form'])]
    candidates = exact or [row for row in candidates if not row['form'] and not row.get('forms')]
    if not candidates:
        return None
    distinct = {json.dumps(row['type'], sort_keys=True) for row in candidates}
    if len(distinct) != 1:
        return None
    row = dict(candidates[0])
    if call_form in row.get('form_types', {}):
        row['type'] = row['form_types'][call_form]
    return row


def _calls_for(page, form):
    direct = [call for call in page['calls'] if call['form'] == form]
    if direct:
        return direct
    relation = re.search(r'[<>=]+', form)
    if relation:
        aliases = [name for name, symbol in page['comparison_aliases'].items() if symbol == relation[0]]
        if len(aliases) == 1:
            spelling = form[:relation.start()] + '_' + aliases[0] + form[relation.end():]
            direct = [call for call in page['calls'] if call['form'] == spelling]
            if not direct and '$' in spelling:
                direct = [call for call in page['calls'] if call['form'] == spelling.replace('$', 'STRING')]
            if direct:
                return direct
    if page['standard'] or form not in page['declared']:
        return []
    # Symbolic instructions name the FBD/ST block in the language table. The
    # source's optional P/_U markers align variants; no mnemonic alias is invented.
    if '.' in form:
        # Dot-to-underscore is accepted only when that exact spelling is printed
        # in the same language table, with the original form declared on the page.
        return [call for call in page['calls'] if call['form'] == form.replace('.', '_')]
    for extension in ('_1', '_2'):
        special = [call for call in page['calls'] if call['form'] == form + extension]
        published = [name.strip() for quoted in re.findall(r'"([^"]+)"', page['zone'])
                     for name in quoted.split(',')]
        if special and form + extension in published:
            return special
    if form == 'OUT':
        published = [name.strip() for quoted in re.findall(r'"([^"]+)"', page['zone'])
                     for name in quoted.split(',')]
        special = [call for call in page['calls'] if call['form'] in {'OUT_T', 'OUT_C'}
                   and call['form'] in published]
        if special:
            return special
    # Slash-separated mnemonic declarations name the same published language
    # table, e.g. ESTR(P)/DESTR(P); match their corresponding optional form.
    for declaration in re.findall(r'(?m)^([A-Z][A-Z0-9_]*(?:\(P\))?/[A-Z][A-Z0-9_]*(?:\(P\))?)\s*$', page['layout']):
        groups = [_variants(item) for item in declaration.split('/')]
        for group in groups:
            if form in group:
                index = group.index(form)
                aliases = [candidate[index] for candidate in groups if len(candidate) == len(group)]
                matches = [call for call in page['calls'] if call['form'] in aliases]
                if len(matches) == 1:
                    return matches
    if not re.search(r'[+$*/<>=!\-]', form):
        return []
    suffix = (re.search(r'(_(?!U$)[A-Z0-9]+)$', form) or [''])[0]
    if suffix and not all(call['form'].endswith(suffix) for call in page['calls']):
        suffix = ''
    trim = lambda name: name[:-len(suffix)] if suffix else name
    form = trim(form)
    unsigned = form.endswith('_U')
    base = form[:-2] if unsigned else form
    pulse = page['optional_pulse'] and base.endswith('P')
    matches = [call for call in page['calls'] if trim(call['form']).endswith('_U') == unsigned
               and (not page['optional_pulse'] or
                    (trim(call['form'])[:-2] if unsigned else trim(call['form'])).endswith('P') == pulse)]
    return matches if len(matches) == 1 else []


def _negative(page, form, language):
    name = 'ST' if language == 'st' else 'FBD/LD'
    text = re.sub(r'\s+', ' ', page['layout'])
    aliases = {form}
    for declaration in re.findall(r'(?m)^([A-Z][A-Z0-9_]*(?:\(P\))?/[A-Z][A-Z0-9_]*(?:\(P\))?)\s*$', page['layout']):
        groups = [_variants(item) for item in declaration.split('/')]
        for group in groups:
            if form in group and re.search(r'can also be used as', text):
                index = group.index(form)
                aliases.update(candidate[index] for candidate in groups if len(candidate) == len(group))
    for match in re.finditer(r'The ([A-Z][A-Z0-9_]*(?:(?:,| and) [A-Z][A-Z0-9_]*)*) instructions? '
                            r'(?:is|are) not supported by the ([^.]+)', text):
        if aliases.intersection(re.findall(r'[A-Z][A-Z0-9_]*', match[1])) and name + ' language' in match[2]:
            replacement = re.search(r'Use ([A-Z][A-Z0-9_]*) of the standard function', text[match.end():])
            return {'basis': 'explicit_language_restriction',
                    **({'replacement_standard_function': replacement[1]} if replacement else {})}
    if language in page['negative_languages']:
        return {'basis': 'language_table_explicit_not_supported_column_geometry'}
    if page['has_geometry']:
        return None
    zone = page['zone']
    if language == 'st':
        match = re.search(r'Structured text[^\n]*\n(.*?)(?:FBD/LD|$)', zone, re.S)
    else:
        match = re.search(r'FBD/LD[^\n]*\n(.*)', zone, re.S)
    if match and re.search(r'\bNot supported\b', match[1]):
        return {'basis': 'language_table_explicit_not_supported'}
    return None


def _parameter(page, name, form, call_form):
    row = _row_for(page['rows'], name, form, call_form)
    direction = row.get('direction') if row else None
    if not direction:
        direction = page['directions'].get(name)
    parameter = {'name': name, 'type': row['type'] if row else 'unknown',
                 'direction': direction or 'unknown'}
    if row:
        parameter['type_basis'] = row['type_basis']
        if row.get('required_literal'):
            parameter['required_literal'] = row['required_literal']
    if direction:
        parameter['direction_basis'] = (row.get('direction_basis') if row else None) or 'fbd_pin_side_geometry'
    return parameter


def _complete(parameters):
    return all(parameter['type'] != 'unknown' and parameter['direction'] in {'input', 'output', 'in_out'}
               for parameter in parameters)


def _appendix_model_conditions(form):
    """Appendix 3, PDF 1460-1462, retaining each actual CPU heading."""
    if form in {'STL', 'RETSTL'}:
        return {
            'FX5S': [{'engineering_tool_version_at_least': '1.080J',
                      'cpu_firmware_support': 'from_first_production'}],
            'FX5UJ': [{'engineering_tool_version_at_least': '1.060N',
                       'cpu_firmware_support': 'from_first_production'}],
            'FX5U': [{'engineering_tool_version_at_least': '1.045X'}],
            'FX5UC': [{'engineering_tool_version_at_least': '1.045X'}],
        }, [1460, 1461]
    if form == 'TRAN':
        return {
            'FX5S': [{'cpu_firmware_version_at_least': '1.010',
                      'engineering_tool_version_at_least': '1.095Z'}],
            'FX5UJ': [{'cpu_firmware_version_at_least': '1.050',
                       'engineering_tool_version_at_least': '1.095Z'}],
            'FX5U': [{'cpu_firmware_version_at_least': '1.220',
                      'engineering_tool_version_at_least': '1.070Y'}],
            'FX5UC': [{'cpu_firmware_version_at_least': '1.220',
                       'engineering_tool_version_at_least': '1.070Y'}],
        }, [1460, 1462]
    return {}, []


def _design_source(*pages):
    return {'manual_id': DESIGN_MANUAL_ID, 'manual_number': 'JY997D55701',
            'revision': 'M', 'pdf_pages': list(pages)}


def _instruction_execution_specs(page, form, signature, language):
    """Bind Program Design rules only to the documented language call/pins.

    A function's EN gate does not establish an instruction's internal edge/state
    behavior. The FB-container rules below retain their separate EN and option.
    """
    if page['standard']:
        return []
    if form == 'TRAN':
        restrictions = ({'statement_representations': [
                            {'kind': 'call_statement', 'function': 'TRAN', 'input_type': 'BOOL'},
                            {'kind': 'assignment_statement', 'target': 'TRAN', 'expression_type': 'BOOL'},
                            {'kind': 'assignment_statement', 'target': 'transition_name', 'expression_type': 'BOOL'}]}
                        if language == 'st' else
                        {'network_count': 1, 'ends_with': 'TRAN', 'TRAN_count': 1,
                         'inline_st_allowed': False, 'device_label_assignment_allowed': False,
                         'disallowed_elements': ['coil', 'function_block', 'jump', 'jump_label', 'return'],
                         'other_function_instructions': 'only_transition_program_whitelist'})
        return [
            ('execution.trigger', {'evaluation': 'sfc_transition_boolean_condition',
                'input': 's', 'input_type': 'BOOL', 'transition_true_when': 'input_expression_true'}, [97]),
            ('constraints.call_context', restrictions, [97]),
        ]
    parameters = signature['parameters']
    enabled = any(item['name'] == 'EN' and item['type'] == 'BOOL' and item['direction'] == 'input'
                  for item in parameters)
    result = signature['returns']
    eno = (result.get('name') == 'ENO' and result.get('type') == 'BOOL'
           and result.get('direction') == 'output') or any(
        item['name'] == 'ENO' and item['type'] == 'BOOL' and item['direction'] == 'output'
        for item in parameters)
    if not enabled or not eno:
        return []
    specs = [('execution.call_enable', {
        'evaluation': 'documented_instruction_function_enable_contract',
        'enable_gate': {'parameter': 'EN', 'type': 'BOOL', 'required_value': True,
                        'scope': 'normal_function_operation_only'},
        'EN_false': {'normal_function_operation': 'not_enabled', 'ENO': False,
                     'instruction_specific_off_processing': 'requires_instruction_specific_fact'},
        'instruction_internal_trigger': 'governed_by_instruction_specific_operation',
        'operand_state_when_disabled': 'not_established_by_general_enable_contract',
        'gate_establishes_executor_support': False,
    }, [15, 54] if language == 'st' else [15, 65])]
    if language == 'fbd':
        specs.append(('execution.same_scan', {
            'order_basis': ['unit_position', 'wire_connections'],
            'authoritative_program_order': 'execution_number_shown_on_each_unit',
            'drawing_directions': ['left_to_right', 'top_to_bottom'],
            'scope': 'FBD_LD_program_units',
            'ladder_source_instruction_order_is_not_substituted': True,
        }, [72]))
        specs.append(('constraints.fbd_connection', {
            'input_points_all_connected': True,
            'connection_types': 'same_type_after_documented_automatic_conversion',
            'instruction_data_output_to_other_function_or_fb_input': 'through_variable_element',
            'direct_output_connection_exception': 'ENO',
            'enabled_function_chain': {'downstream_function_requires_EN': True,
                                       'connection': 'ENO_to_EN', 'purpose': 'avoid_undefined_value'},
            'ENO_output_label_required_when_not_chained': False,
        }, [15, 64, 65]))
        if form in {'BREAK', 'BREAKP'}:
            specs.append(('constraints.fbd_branch_scope', {
                'target_scope': 'same_program_block',
                'cross_program_block_branch': False,
                'native_CJ_replacement': 'FBD_jump_element',
                'subroutine_call_through_jump': False,
            }, [67]))
    pulse = (page['optional_pulse'] and form.removesuffix('_U').endswith('P')) or form in {'PLS', 'PLF'}
    if pulse and language == 'st':
        # Design M p.59 is specifically about instruction EN inside IF/CASE.
        # It is not an operand-edge history rule for LDP/ANDP/ANDF contacts.
        specs.append(('execution.selection_edge', {
            'control_statements': ['IF', 'CASE'],
            'trigger_object': 'instruction_EN_with_selection_condition',
            'state_owner': 'instruction_ON_OFF_judgment',
            'comparison_basis': 'instruction_judgment_at_previous_scan',
            'current_judgment': 'selection_condition_AND_EN',
            'rising_execution': 'selection_true_AND_previous_judgment_OFF_AND_EN_true',
            'falling_execution': 'selection_true_AND_previous_judgment_ON_AND_EN_false',
            'selection_false': {'current_judgment': False, 'instruction_executed': False,
                                'falling_transition_alone_does_not_execute_instruction': True},
            'first_execution_history': 'not_stated_by_this_rule',
            'multiple_calls_in_one_scan_history_update': 'not_stated_by_this_rule',
            'executor_capability': 'not_established',
        }, [59]))
        if form == 'PLS':
            specs.append(('effects.selection_pulse_output', {
                'target': 'd', 'trigger': 'rising_instruction_judgment_when_IF_or_CASE_selected',
                'on_duration_scans': 1,
                'scope': 'documented_ST_PLS_example_with_selection_condition',
                'general_skipped_call_output_rule': 'not_established_by_example',
            }, [59]))
    if form in {'MC', 'MCR'} and language == 'st':
        specs.append(('effects.master_control', {
            'operation': 'start_master_control' if form == 'MC' else 'end_master_control',
            'when_master_control_off': {
                'selection_statements': ['IF', 'CASE'],
                'iteration_statements': ['FOR', 'WHILE', 'REPEAT'],
                'statements_inside_selection_or_iteration': 'not_processed',
                'outside_assignment': 'not_processed_destination_retained',
                'outside_non_assignment': 'not_executed',
            },
            'scope': 'ST_statement_processing_not_native_ladder_coil_reset',
        }, [60]))
    if pulse:
        specs.append(('execution.fb_enable_control', {
            'container': 'function_block', 'use_EN_ENO': True,
            'property': 'use_mc_mcr_to_control_en', 'instruction_position': 'coil_side',
            'yes': {'EN_control_instructions': 'MC_MCR',
                    'next_container_EN_true_and_condition_true': 'instruction_executed'},
            'no': {'EN_control_instruction': 'CJ',
                   'next_container_EN_true_and_condition_true': 'instruction_may_not_execute'},
            'edge_history_rule_outside_this_container': 'not_established_by_this_source',
        }, [27, 123, 124]))
    categories = [category for rule in signature.get('operand_conditions', [])
                  if rule.get('parameter') == 'Coil' for category in rule.get('label_categories', [])]
    if signature['call_form'] == 'OUT':
        categories.append('OUT_bit_device')
    for category in dict.fromkeys(categories):
        if category == 'timer':
            on_disable = {'current_count': 0, 'coil': False, 'contact': False}
            example_page = 125
        elif category in {'retentive_timer', 'counter', 'long_counter'}:
            on_disable = {'current_count': 'unchanged', 'coil': False, 'contact': 'unchanged'}
            example_page = 126 if category == 'retentive_timer' else 123
        elif category == 'OUT_bit_device':
            on_disable = {'specified_device': False}
            example_page = 127
        else:
            continue
        specs.append(('effects.fb_disabled_state', {
            'container': 'function_block', 'container_EN': False, 'operand_label_category': category,
            'property': 'use_mc_mcr_to_control_en',
            'yes': {'EN_control_instructions': 'MC_MCR', 'when_disabled': on_disable},
            'no': {'EN_control_instruction': 'CJ', 'when_disabled': {'device_state': 'unchanged'}},
            'scope': 'FB_container_disable_not_instruction_EN_false',
        }, sorted({27, 123, example_page})))
    return specs


def canonical_language_facts(section, parsed, record, *, page_words=None):
    """Return dimension/value/language/status/form/page payloads for caller binding.

    Replace (do not append beside) legacy call_forms-only st_signature facts.
    The caller must retain page sources, CPU scope and existing dependencies, and
    attach the returned conditions when present. Results contain no manual prose.
    """
    page_words = page_words or {}
    cache_key = '_canonical_language_pages'
    if cache_key not in parsed:
        parsed[cache_key] = _language_pages(section, page_words)
    result = []
    for page in parsed[cache_key]:
        for form in record['forms']:
            if form not in page['declared']:
                continue
            calls = _calls_for(page, form)
            kind = ('standard_function_block' if page['function_block'] else
                    'standard_function' if page['standard'] else 'instruction')
            common = {'form': form, 'declaration_kind': kind, 'documented_page': page['page'],
                      'executor_capability': 'not_established', 'admission_effect': 'none'}

            def emit(dimension, value, language, status='source_verified', *, design_pages=(), extra_conditions=()):
                fact = {'dimension': dimension, 'value': {**common, **value},
                        'language': language, 'status': status, 'form': form, 'page': page['page']}
                versions = page['versions'].get(language, []) + page['form_versions'].get(language, {}).get(form, [])
                if versions:
                    fact['conditions'] = [{'engineering_tool_version_at_least': version}
                                          for version in sorted(set(versions))]
                if language == 'ladder':
                    fact.setdefault('conditions', []).append(
                        {'context': 'project_mode', 'equals': 'structured_ladder_block'})
                    fact['value']['representation'] = 'structured_ladder_block'
                model_conditions, additional_pages = _appendix_model_conditions(form)
                if model_conditions:
                    fact['model_conditions'] = model_conditions
                    fact['additional_source_pages'] = additional_pages
                    fact['value']['cpu_version_requirements'] = model_conditions
                if form == 'TRAN':
                    fact.setdefault('conditions', []).append({'program_context': 'sfc_transition'})
                    design_pages = sorted(set(design_pages) | {97})
                if extra_conditions:
                    fact.setdefault('conditions', []).extend(extra_conditions)
                if design_pages:
                    fact['additional_sources'] = [_design_source(*design_pages)]
                result.append(fact)
                return fact

            for language in (('st', 'fbd', 'ladder') if page['standard'] else ('st', 'fbd')):
                negative = _negative(page, form, language) if language != 'ladder' else None
                if negative:
                    emit('identity.language_availability', {'language': language, 'available': False,
                                                           **negative}, language)
                    continue
                if page['contact_expression']:
                    emit('identity.language_availability',
                         {'language': language, 'available': True,
                          'basis': 'explicit_language_representation', 'callable_as_named_function': False}, language)
                    emit('parameters.' + language + '_signature',
                         {'available': True, 'call_kind': 'logical_expression' if language == 'st' else 'contact',
                          'callable_as_named_function': False, 'enable': False,
                          'parameters': [{'name': 's', 'type': 'ANY_BOOL', 'direction': 'input',
                                          'type_basis': 'operand_table_label_type',
                                          'direction_basis': 'contact_input_operand'}],
                          'returns': {'name': 'operation_result', 'type': 'BOOL', 'direction': 'output'},
                          'argument_order': ['s']}, language)
                    emit('execution.expression_evaluation', {
                        'evaluation': 'when_enclosing_expression_or_contact_is_evaluated',
                        'operand': 's', 'requires_edge_history': False,
                        'named_instruction_function_call': False,
                    }, language)
                    emit('effects.contact_expression', {
                        'operand': 's', 'negate_operand': form in {'LDI', 'ANI', 'ORI'},
                        'operation': 'start' if form.startswith('LD') else
                                     'and' if form in {'AND', 'ANI'} else 'or',
                        'representation': 'boolean_expression' if language == 'st' else 'contact',
                        'connection_order': 'explicit_expression_tree' if language == 'st' else
                                            'documented_series_or_parallel_contact_connection',
                        'not_a_named_function': True,
                    }, language)
                    continue
                if not calls:
                    continue
                for call in calls:
                    if language not in call.get('languages', ['st', 'fbd', 'ladder']):
                        continue
                    if language == 'fbd' and (not re.search(r'FBD/LD', page['zone']) or
                            not page['directions'] and not (call.get('kind') == 'statement' and
                            re.search(r'FBD/LD[^\n]*\n[^\n]*\b' + re.escape(form) + r'\b', page['zone']))):
                        continue
                    parameters = [_parameter(page, name, form, call['form']) for name in call['arguments']]
                    returns = ({'role': 'outputs_via_named_arguments'} if page['function_block'] else
                               {'role': 'statement_no_return'} if call.get('kind') == 'statement' else
                               _parameter(page, call['result'], form, call['form']))
                    if language in {'fbd', 'ladder'}:
                        pin_names = list(dict.fromkeys(
                            [name for name in call['arguments'] if page['standard'] or name in page['directions']
                             or call.get('kind') == 'statement']
                            + ([call['result']] if call['result'] else [])))
                        parameters = [_parameter(page, name, form, call['form']) for name in pin_names]
                        returns = {'role': 'outputs_via_pins'}
                    if page['function_block']:
                        for parameter in parameters:
                            label = parameter['name']
                            parameter['documented_argument'] = label
                            parameter['name'] = call['bindings'][label]
                    available = {'language': language, 'available': True,
                                 'basis': 'documented_call_and_argument_table' if page['standard'] else
                                          'documented_st_call' if language == 'st' else 'documented_fbd_pin_geometry',
                                 'call_form': call['form']}
                    if call.get('kind') == 'operator' and language == 'st':
                        available['basis'] = 'explicit_operator_representation'
                        available['call_kind'] = 'operator'
                    emit('identity.language_availability', available, language)
                    signature = {'parameters': parameters, 'returns': returns,
                                 'enable': 'EN' in call['arguments'], 'available': True,
                                 'call_form': call['form'],
                                 'argument_order': list(call['arguments']) if language == 'st' else
                                                   [parameter['name'] for parameter in parameters]}
                    if call.get('kind') == 'operator' and language == 'st':
                        signature.update(call_kind='operator', operator_symbol=form,
                                         basis='explicit_operator_representation')
                    if call.get('kind') == 'statement':
                        signature['call_kind'] = 'statement'
                    if call.get('kind') == 'fbd_block':
                        signature['call_kind'] = 'instruction_block'
                    if call['form'] in {'OUT_T', 'OUT_C', 'OUTH', 'OUTHS'}:
                        label_kinds = ('timer', 'retentive_timer') if 'Timer Number' in page['layout'] else (
                            ('long_counter',) if 'Long counter number' in page['layout'] else ('counter',))
                        signature['operand_conditions'] = [{'parameter': 'Coil', 'label_categories': list(label_kinds)}]
                        if call['form'] == 'OUT_T':
                            for parameter in parameters:
                                if parameter['name'] == 'Value':
                                    parameter['type'] = 'ANY_INT'
                                    parameter['type_basis'] = 'explicit_st_fbd_out_t_type_footnote'
                    if page['function_block']:
                        signature.update(call_kind='function_block', requires_instance=True,
                                         documented_instance=call['instance'],
                                         argument_order=[parameter['name'] for parameter in parameters],
                                         source_named_argument_operators={call['bindings'][name]:
                                             'assignment(:=)' if operator == ':=' else 'output_association(=>)'
                                             for name, operator in call['binding_operators'].items()})
                        if language == 'st' and call['source_syntax_warning']:
                            signature['source_sample_syntax'] = 'missing_argument_separator'
                    if page['variadic']:
                        signature['variadic_parameters'] = [
                            {'name_prefix': prefix, 'minimum': int(minimum), 'maximum': int(maximum)}
                            for prefix, minimum, maximum in page['variadic']]
                    complete = _complete(parameters + ([returns] if language == 'st' and not page['function_block'] and
                                                       call.get('kind') != 'statement' else []))
                    emit('parameters.' + language + '_signature', signature, language,
                         'source_verified' if complete else 'declared_unverified')
                    if page['function_block'] and language == 'st':
                        output_bindings = [parameter['name'] for parameter in parameters
                            if parameter['direction'] == 'output' and
                            signature['source_named_argument_operators'].get(parameter['name']) == 'assignment(:=)']
                        if output_bindings:
                            emit('constraints.source_consistency', {
                                'conflict': 'standard_fb_output_binding_operator',
                                'source_claims': [
                                    {'manual_id': MANUAL_ID, 'pdf_page': page['page'],
                                     'output_arguments': output_bindings, 'operator': 'assignment(:=)'},
                                    {'manual_id': DESIGN_MANUAL_ID, 'pdf_page': 53,
                                     'VAR_OUTPUT_operator': 'output_association(=>)',
                                     'VAR_INPUT_VAR_IN_OUT_operator': 'assignment(:=)'}],
                                'resolution': 'not_established', 'automatic_admission': False,
                            }, language, 'declared_unverified', design_pages=[53])
                    for dimension, execution, design_pages in _instruction_execution_specs(page, form, signature, language):
                        fb_control = dimension in {'execution.fb_enable_control', 'effects.fb_disabled_state'}
                        extra_conditions = ([{'program_context': 'inside_function_block',
                                              'function_block_use_en_eno': True,
                                              'function_block_en_control_option_supported': True,
                                              'instruction_position': 'coil_side',
                                              **({'operand_label_category': execution['operand_label_category']}
                                                 if dimension == 'effects.fb_disabled_state' else {})}]
                                            if fb_control else [])
                        fact = emit(dimension, {**execution, 'call_form': call['form']}, language,
                                    design_pages=design_pages, extra_conditions=extra_conditions)
                        if fb_control:
                            # This is a version rule for a particular option and
                            # FB implementation, not a limit on the CJ alternative.
                            fact['value']['conditional_version_requirements'] = [{
                                'models': ['FX5U', 'FX5UC'],
                                'when': {'function_block_type': 'subroutine',
                                         'use_mc_mcr_to_control_en': True},
                                'cpu_firmware_version_at_least': '1.060',
                                'engineering_tool_version_at_least': '1.040S'}]
                            fact.setdefault('additional_sources', []).append({
                                'manual_id': 'fx5_application_ag', 'manual_number': 'JY997D55401',
                                'revision': 'AG', 'pdf_pages': [1035, 1036]})
    return result

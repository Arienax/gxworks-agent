"""Native operand order from the FX5 manual's Ladder column, not ST calls."""
import re

from knowledge.instruction_document import instruction_declaration_names
from tools.fact_fx5_signatures import (
    _geometry_operands, _layout_operands, _row_for, _fbd_pins, _variants, _language_pages,
)


_OPERAND = re.compile(r'^\((s\d*|d\d*|n\d*|U/H|U|N|I|P)\)(?:/\((d\d*)\))?$|^(Value)$')


def _printed_forms(layout, declared):
    """Bind each diagram to its own form line, not the chapter introduction."""
    found = set()
    for line in re.split(r'Ladder diagram', layout, maxsplit=1)[0].splitlines():
        line = re.sub(r'\s*\[(?:using|For) [^\]]+\]\s*$', '', line.strip())
        line = re.sub(r'(?<=\bSP)\s+\.', '.', line)
        for token in re.split(r',\s*', line):
            token = re.sub(r'\s+(?:T|ST|C|LC|F)$', '', token)
            if not re.fullmatch(r'[A-Z0-9_.$+*/<>=!()\-]+(?:\[GLYPH-F06F\][A-Z_()]*|)', token):
                continue
            for variant in _variants(token):
                pattern = re.escape(variant).replace(re.escape('[GLYPH-F06F]'), r'(?:<>|<=|>=|=|<|>)')
                found.update(form for form in declared if re.fullmatch(pattern, form))
    return found


def _diagrams(words, opcode, declared, *, allow_zero=False):
    diagrams = []
    for header in [word for word in words if word['text'] == 'Ladder']:
        right = [word['x0'] for word in words if abs(word['top']-header['top']) < 3
                 and word['text'] in {'Structured', 'ST'} and word['x0'] > header['x1']]
        if not right:
            continue
        x_right = min(right)
        stops = [word['top'] for word in words if word['top'] > header['bottom'] and
                 (word['text'].startswith('FBD/LD') or
                  word['text'] in {'Setting', 'Processing', 'Operand', 'Argument'})]
        if not stops:
            continue
        bottom = min(stops)
        content = [word for word in words if header['bottom'] < word['top'] < bottom and word['x0'] < x_right-3]
        lines = []
        for word in sorted(content, key=lambda word: (word['top'], word['x0'])):
            line = next((line for line in lines if abs(line[0]['top'] - word['top']) < 2), None)
            if line is None:
                lines.append([word])
            else:
                line.append(word)
        candidates = []
        for line in lines:
            line.sort(key=lambda word: word['x0'])
            labels = [label for word in line for match in _OPERAND.finditer(word['text'])
                      for label in [(match[1] + '_' + match[2]) if match[2] else match[1] or 'Set_value']]
            explicit = {word['text'] for word in line if word['text'] in declared}
            if explicit and opcode not in explicit:
                continue
            if (labels or opcode in explicit) and labels not in candidates:
                candidates.append(labels)
        if not candidates and not any('Not' in word['text'] for word in content):
            # INV and several no-operand blocks use a vector symbol in this
            # column. The bounded column plus absence of a Setting-data table
            # is reviewed separately from a page with missing geometry.
            if allow_zero or any(word['text'] == opcode for word in content):
                candidates.append([])
        diagrams.extend(candidates)
    return [list(names) for names in dict.fromkeys(tuple(names) for names in diagrams)]


def canonical_ladder_facts(section, record, *, page_words):
    if not section['outline'].startswith(('PART 3 ', 'PART 4 ')):
        return []
    result = []
    prepared = {page['page']: page for page in _language_pages(section, page_words)}
    texts = dict(section.get('page_texts', ()))
    for page, layout in section.get('page_layouts', ()):
        declared = instruction_declaration_names(texts.get(page, layout), section['outline'],
                                                 'programming', source_text=section['text'])
        if record['opcode'] not in declared:
            continue
        printed = _printed_forms(layout, declared)
        if printed and record['opcode'] not in printed:
            continue
        words = page_words.get(page, [])
        rows = _geometry_operands(words) + _layout_operands(layout)
        orders = _diagrams(words, record['opcode'], declared,
                           allow_zero=bool(words) and not rows and 'Setting data' not in layout)
        if not orders:
            continue
        rows = prepared.get(page, {}).get('rows', rows)
        directions = _fbd_pins(words, layout, {row['name'] for row in rows} | {'Coil', 'Value'})
        title = section['section_token']
        if 'd displays as Coil' in layout:
            directions['d'] = 'in_out'
        if 'Set value displays as Value' in layout and 'Value' in directions:
            directions['Set_value'] = directions['Value']
        contact = title in {'operation_start_series_connection_parallel_connection',
                            'pulse_operation_start_pulse_series_connection_pulse_parallel_connection',
                            'pulse_not_operation_start_pulse_not_series_connection_pulse_not_parallel_connection',
                            'checking_the_status_of_a_step', 'checking_the_status_of_a_block'}
        if contact:
            directions['s'] = 'input'
        for index, names in enumerate(orders):
            native_directions = dict(directions)
            intro = re.split(r'Ladder diagram', layout, maxsplit=1)[0]
            # Two-operand arithmetic/logic reads d before writing the result to
            # the same location. Its three-operand sibling is a different form.
            if (names == ['s', 'd'] and re.search(r'\(d\).+store.+\(d\)', intro, re.S)
                    and '(s)' in intro):
                native_directions.update(s='input', d='in_out')
            if title == '9_1_starts_ends_step_ladder':
                native_directions['d'] = 'input'
            operands = []
            for name in names:
                row = _row_for(rows, name.replace('_', ' ') if name == 'Set_value' else name,
                               record['opcode'], record['opcode'])
                aliases = name.split('_') if re.fullmatch(r's\d+_d\d+', name) else []
                if aliases:
                    alternatives = [_row_for(rows, alias, record['opcode'], record['opcode']) for alias in aliases]
                    if all(alternatives) and alternatives[0]['type'] == alternatives[1]['type']:
                        row = alternatives[0]
                        native_directions[name] = 'in_out'
                item = {'name': name, 'type': row['type'] if row else 'unknown'}
                if aliases:
                    item.update(aliases=aliases, direction_selector='MODBUS_function_code')
                if name == 'U' and "Dummy (Input the character string ['U0'].)" in layout:
                    item.update(type='STRING', fixed_literal='U0', label_allowed=False)
                if name in native_directions:
                    item['direction'] = native_directions[name]
                    item['direction_basis'] = ('own_operand_read_write_description' if
                        contact or native_directions[name] == 'in_out' else
                        'same_operand_in_the_documented_language_diagram')
                # Native pointer/nesting identifiers are explicit input operands.
                if name in {'P', 'I', 'N', 'U', 'U/H', 'Set_value'}:
                    item['direction'] = 'input'
                if name == 'Set_value' and row is None:
                    label = next((row for row in rows if row['name'] == 'Value'), None)
                    if label:
                        item['type'] = label['type']
                operands.append(item)
            common = {'declaration_kind': 'instruction', 'documented_page': page,
                      'basis': 'left_to_right_operands_in_the_Ladder_column',
                      'diagram_variant': index}
            result.extend([
                {'dimension': 'parameters.order', 'value': {**common, 'symbols': names}, 'page': page,
                 'status': 'source_verified'},
                {'dimension': 'parameters.arity', 'value': {**common, 'minimum': len(names), 'maximum': len(names)},
                 'page': page, 'status': 'source_verified'},
                {'dimension': 'parameters.typed_operands',
                 'value': {**common, 'operands': operands, 'operand_count': len(names)}, 'page': page,
                 'status': 'source_verified' if all(item['type'] != 'unknown' and item.get('direction') for item in operands)
                           else 'declared_unverified'},
            ])
    return result

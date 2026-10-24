"""Source-bound FX5 operation errors and numbered technical qualifications.

The input is one manual section, including its physical page layouts and tables.
No program example, bare hexadecimal constant, or neighbouring section is an
error witness. Repeated codes retain every cause and wrapped line. Public values
contain technical predicates, operands, units and numbers; original paragraphs
remain in the private source index.

This module authors knowledge only. A numerical rule without a literal operand
binding remains ``constraints.source_qualifications`` / ``declared_unverified``.
It does not become an executable instruction constraint or simulator behaviour.
"""
from __future__ import annotations

import json
import re
import copy

from tools.fact_fx5_processing_review import review_processing_clause
from tools.fact_fx5_execution_review import review_execution_clause
from tools.fact_fx5_precaution_review import review_precaution_clause


METHOD = 'authored_fx5_condition_review_v1'
ERROR_ROW = re.compile(r'^\s*(?P<codes>[0-9A-F]{4}H(?:\s*[,/|]\s*[0-9A-F]{4}H)*)\s*\|\s*(?P<body>.*)$', re.I)
NOTE_ROW = re.compile(r'^\s*\*(?P<number>\d+)\s+(?P<body>\S.*)$')
GLYPH = re.compile(r'\[GLYPH-(?P<code>[0-9A-F]+)\]')
OPERAND = re.compile(r'(?<![A-Za-z0-9_])\((?P<name>[sdnmptijk][0-9]{0,2}|U/H|U|H|Coil|Value|Set value|IN[0-9]*|PT|PV|CV)\)'
                     r'(?P<offset>\s*\+\s*\d+)?(?P<bit>\s*\.b\d+)?', re.I)
NUMBER = r'[+\-\u2212]?(?:[0-9A-F]+H|K?\d[\d,]*(?:\.\d+)?)'
RANGE = re.compile(r'(?<![\w.])(?P<min>' + NUMBER + r')\s+(?:to|through)\s+'
                   r'(?P<max>' + NUMBER + r')(?!\w)', re.I)
DECIMAL = re.compile(r'(?<![A-Za-z0-9])(?P<value>[+\-\u2212]?\d[\d,]*(?:\.\d+)?)'
                     r'(?P<unit>H|ms|msec|us|Hz|kHz|MHz|%)?(?![A-Za-z0-9])', re.I)
HEX = re.compile(r'(?<![A-Za-z0-9])(?P<value>[0-9A-F]{1,8})H(?![A-Za-z0-9])')
HEX_MASK = re.compile(r'(?<![A-Za-z0-9])(?P<value>[0-9A-F*]{1,8}\*+[0-9A-F*]*)H(?![A-Za-z0-9])')
DEVICE = re.compile(r'\b(?:SD|SM|SB|SW|LC|ST|FD|[DXYMSTRCIBUWZV])\d+(?:\.b\d+)?\b')
CPU = re.compile(r'\bFX5(?:UC|UJ|U|S)\b')
INSTRUCTION = re.compile(r'\b[A-Z][A-Z0-9_.]*(?:\([Pp]\))?\s+instructions?\b')
FOOTER = re.compile(r'^\s*(?:\d{1,4}|\d+\s+[A-Z][A-Z /\-]+|\d+(?:\.\d+)+\s+[A-Z].+|MELSEC .+|FX5.+Manual.*)\s*$')
HEADINGS = (
    ('operation_error', re.compile(r'^Operation errors?(?:\s*\|\s*\d{1,2})?\s*$', re.I)),
    ('setting_data', re.compile(r'^Setting data\b', re.I)),
    ('applicable_devices', re.compile(r'^Applicable devices\b', re.I)),
    ('processing_details', re.compile(r'^Processing details\b', re.I)),
    ('precautions', re.compile(r'^Precautions\b|^Restrictions\b', re.I)),
    ('execution_timing', re.compile(r'^(?:Execution|Operation|Instruction execution) timing\b|^Timing chart\b', re.I)),
    ('program_example', re.compile(r'^Program examples?\b|^Ex\.$|^\[Example(?:\s|:)|^(?:•\s*)?(?:(?:Setting|Execution|Program) )?Examples?\b', re.I)),
    ('language_form', re.compile(r'^(?:Ladder diagram|Structured text|FBD/LD)\b', re.I)),
)
LANGUAGES = ('ladder', 'st', 'fbd')
TABLE_FORM = r'[A-Z+*/<>=\-][A-Z0-9_+*/<>=.$\-\[\]]*(?:\(P\))?(?:_U)?'
CONDITION_SCOPES = ('error', 'footnote', 'operand_ranges', 'precautions', 'control_data',
                    'execution_timing', 'processing_qualifications')
QUALIFYING_PROSE = re.compile(r'\b(?:when|if|until|while|after|before|during|unless|except|'
                              r'cannot|must|only|should|do not|does not|not supported|ignored|'
                              r'undefined|scan|interrupt|firmware|version|overflow|underflow|'
                              r'carry|borrow|completion|error|range|reserved|can be specified)\b', re.I)
ERROR_REPORTING = re.compile(r'^If .+completes with an error, an error code is stored|^For the error code stored', re.I)
NORMATIVE_INPUT = re.compile(r'^(?:•\s*)?(?:A|The) value input to \([sdnm][0-9]?\).*'
                             r'(?:data (?:type|value)|within the range|is \d)', re.I)
CONTEXT_BOUND_RULES = frozenset({'invalid_execution_order', 'program_placement', 'invalid_program_context',
    'conditional_result', 'completion_device_timing', 'completion_error_reporting', 'conditional_completion',
    'instruction_execution_permission', 'cpu_mode_transition', 'counter_count_condition',
    'timer_repeated_execution', 'set_latch_retention', 'pulse_device_state', 'pulse_skipped_execution',
    'multiple_write_precedence', 'device_state_event', 'master_control_identifier_order',
    'control_completion_status', 'control_data_ownership', 'function_result_matrix',
    'output_condition_matrix', 'sd_memory_card_presence', 'output_mode_axis_restriction',
    'conditional_data_storage', 'flag_pulse_timing', 'invalid_binary32_input_domain'})


def _flat(value):
    return ' '.join(str(value or '').split())


def _number(value):
    value = value.replace(',', '').replace('\u2212', '-')
    if value.upper().startswith('K'):
        value = value[1:]
    if value.upper().endswith('H'):
        return {'value': int(value[:-1], 16), 'encoding': 'hexadecimal'}
    return {'value': float(value) if '.' in value else int(value), 'encoding': 'decimal'}


def _heading(line):
    text = GLYPH.sub('', line).strip()
    return next((kind for kind, pattern in HEADINGS if pattern.match(text)), None)


def _chart_start(line):
    return bool(re.match(r'^(?:Application setting area|Instruction execution (?:complete|error completion) flag|'
                         r'File being accessed|Scan(?:\s|$)|Sequence scan|'
                         r'File name(?:/folder name)?\s*\([sdnm][0-9]?\)|'
                         r'Below is shown the timing chart|\(1\)\s+No processing during scanning)', line))


def _operand_type_heading(text):
    text = text.removeprefix('•').strip()
    text = re.sub(r'^When ', '', text)
    types = r'BOOL|INT|DINT|LINT|WORD|DWORD|REAL|LREAL|STRING|TIME'
    atom = r'\([sdnm][0-9]?\) is (?:the )?(?:' + types + r')(?: type)?'
    if re.fullmatch(atom + r'(?: and ' + atom + r')*', text):
        return [{'operand': name, 'data_type': data_type} for name, data_type in re.findall(
            r'\(([sdnm][0-9]?)\) is (?:the )?(' + types + r')', text)]
    same = re.fullmatch(r'(?P<names>\([sdnm][0-9]?\)(?:(?:,? and |, )\([sdnm][0-9]?\))*) are '
                        r'(?:the )?(?P<type>' + types + r')(?: type)?', text)
    if same:
        return [{'operand': subject['operand'], 'data_type': same['type']} for subject in _subjects(same['names'])]
    alternatives = re.fullmatch(r'(?P<names>\([sdnm][0-9]?\)(?:(?:,? and |, )\([sdnm][0-9]?\))*) are '
                                r'(?P<types>(?:' + types + r')(?:(?:,? or |, )(?:' + types + r'))+)', text)
    if alternatives:
        choices = re.split(r',? or |, ', alternatives['types'])
        return [{'operand': subject['operand'], 'data_type_any_of': choices} for subject in _subjects(alternatives['names'])]
    return None


def _blocks(section):
    """Physical error-table rows and footnotes, retaining continuation lines."""
    errors, notes = [], []
    domain, current_error, current_note = None, None, None
    registers, diagnostic_operands = [], []
    error_domain, has_action = 'not_established', False
    operand_types = []
    cause_cells = {}
    _file_tables, file_table_lines = _file_transfer_combination_blocks(section)
    for table in section.get('tables') or ():
        try:
            rows = json.loads(table.get('rows_json') or '[]')
        except (TypeError, ValueError):
            continue
        if rows and any('Action' == str(cell).strip() for cell in rows[0]):
            column = next((i for i, cell in enumerate(rows[0]) if str(cell).strip().casefold() == 'description'
                           or re.search(r'\bcause\b', str(cell), re.I)), None)
            if column is not None:
                cause_cells.setdefault(int(table.get('pdf_page') or 0), []).extend(
                    _flat(row[column]) for row in rows[1:] if len(row) > column and row[column])

    def finish_error():
        nonlocal current_error
        if current_error:
            source_lines = current_error.pop('lines')
            text = _flat(' '.join(source_lines))
            if current_error['has_action_column']:
                # PDF table cells retain the complete Cause even when Action
                # cells are lost. Bind by its literal first fragment, never by
                # row ordinal. Corrective actions cannot become error triggers.
                current_error.pop('first_cause_fragment')
                matches, ambiguous = [], False
                cells = list(dict.fromkeys(cell for page in current_error['pages'] for cell in cause_cells.get(page, [])))
                for line in source_lines:
                    prefix = _flat(re.split(r'[|•]', line.lstrip('• '), maxsplit=1)[0])
                    if not prefix:
                        continue
                    found = [cell for cell in cells if cell.lstrip('• ').startswith(prefix) or
                             (len(prefix) >= 48 and len(cell.lstrip('• ')) >= 48 and
                              prefix[:48] == cell.lstrip('• ')[:48])]
                    if len(found) > 1:
                        ambiguous = True
                    elif len(found) == 1 and found[0] not in matches:
                        matches.append(found[0])
                if matches and not ambiguous:
                    # A vertically merged error-code cell can own several
                    # complete cause cells. Every one has its own literal
                    # layout witness; corrective-action cells own none.
                    text = ' • '.join(matches)
                    current_error['cause_cell_bound'] = True
                    current_error['bound_cause_cells'] = len(matches)
                else:
                    current_error['cause_cell_bound'] = False
            else:
                current_error.pop('first_cause_fragment')
            chapter = str(section.get('chapter_number') or '')
            if chapter.isdigit():
                text = re.sub(r'(?<=[.)])\s*\|\s*' + re.escape(chapter) + r'\s+(?=[A-Z])', ' • ', text)
            current_error['text'] = text
            errors.append(current_error)
            current_error = None

    def finish_note():
        nonlocal current_note
        if current_note:
            current_note['text'] = _flat(' '.join(current_note.pop('lines')))
            notes.append(current_note)
            current_note = None

    for page, layout in section.get('page_layouts') or ():
        for line_number, line in enumerate((layout or '').splitlines(), 1):
            stripped = line.strip()
            if line_number in file_table_lines.get(int(page), set()):
                finish_error()
                finish_note()
                domain = 'control_data'
                continue
            heading = _heading(stripped)
            if heading:
                if heading == 'execution_timing' and domain == 'program_example':
                    continue
                finish_error()
                finish_note()
                domain = heading
                if heading == 'operation_error':
                    error_domain, has_action, registers, diagnostic_operands = 'operation_error', False, [], []
                    operand_types = []
                continue
            typed = _operand_type_heading(stripped)
            if typed and domain == 'operation_error':
                finish_error()
                finish_note()
                operand_types = typed
                continue
            if stripped.startswith('[GLYPH-F0FB]'):
                # A new titled subsection (for example "Control data (d1)")
                # terminates a preceding note; its tables are not note prose.
                finish_error()
                finish_note()
                plain = GLYPH.sub('', stripped).strip()
                if domain == 'program_example':
                    if plain == 'Operation result' and re.search(r'Function (?:with|without) EN/ENO', layout):
                        domain = 'processing_details'
                    else:
                        continue
                domain = 'setting_data' if re.match(r'^Descriptions?, ranges?, (?:and )?data types?', plain, re.I) else (
                    'setting_data' if domain == 'setting_data' and re.match(r'^FX5(?:S|UJ|U|UC)(?:/FX5(?:S|UJ|U|UC))* CPU module', plain)
                    else
                    'processing_details' if plain == 'Operation result' and re.search(r'Function (?:with|without) EN/ENO', layout)
                    else 'control_data' if re.fullmatch(r'Control data(?:\s*\([sdnm][0-9]?\))?', plain, re.I) or domain == 'control_data'
                    else 'technical_subsection')
                continue
            if current_note and (re.match(r'^Operand\s*:', stripped) or re.fullmatch(r'\[[^]]+\]', stripped)):
                finish_note()
                continue
            if (current_error and has_action and stripped.isdigit() and re.search(
                    r'> ' + re.escape(stripped) + r' [A-Z]', section.get('outline', ''))):
                continue
            if (not stripped or FOOTER.match(stripped)):
                finish_error()
                finish_note()
                continue
            # Both header layouts occur in the manual: a single table line and
            # a two-line "Error code" / "(SD0/SD8067) | Description" header.
            if 'Error code' in stripped and ('Description' in stripped or 'SD0' in stripped or 'cause' in stripped.lower()):
                finish_error()
                finish_note()
                domain = 'operation_error'
                has_action = bool(re.search(r'\|\s*Action\b', stripped))
                registers, diagnostic_operands = [], []
                if has_action:
                    error_domain = 'completion_status_error'
                registers = list(dict.fromkeys(re.findall(r'\bSD\d+\b', stripped)))
                if registers:
                    error_domain = 'operation_error'
                continue
            if 'SD0/SD8067' in stripped and 'Description' in stripped:
                domain = 'operation_error'
                registers = ['SD0', 'SD8067']
                error_domain = 'operation_error'
                continue
            if domain == 'operation_error' and re.fullmatch(r'\(SD0?/SD8067\)', stripped):
                registers = re.findall(r'SD\d+', stripped)
                error_domain = 'operation_error'
                continue
            if domain == 'operation_error' and re.fullmatch(r'\(\([sdnm][0-9]?\)\+\d+\)', stripped):
                diagnostic_operands = _subjects(stripped)
                error_domain = 'completion_status_error'
                continue
            if domain == 'operation_error' and ERROR_REPORTING.match(stripped):
                finish_error()
                finish_note()
                domain = 'error_reporting'
                continue
            error = ERROR_ROW.match(stripped)
            if error and domain == 'operation_error':
                finish_error()
                finish_note()
                current_error = {'codes': [code.upper() for code in re.split(r'\s*[,/|]\s*', error['codes'])],
                                 'page': int(page), 'pages': [int(page)], 'line': line_number,
                                 'domain': domain, 'diagnostic_registers': list(registers),
                                 'diagnostic_operands': list(diagnostic_operands),
                                 'operand_type_conditions': list(operand_types),
                                 'error_domain': error_domain, 'has_action_column': has_action,
                                 'first_cause_fragment': _flat(re.split(r'[|•]', error['body'], maxsplit=1)[0]),
                                 'lines': [error['body']]}
                continue
            note = NOTE_ROW.match(stripped)
            if note and domain != 'program_example':
                finish_error()
                finish_note()
                current_note = {'number': int(note['number']), 'page': int(page), 'pages': [int(page)],
                                'line': line_number, 'domain': domain or 'section_qualification',
                                'lines': [note['body']]}
                continue
            if current_note:
                if stripped == 'The following table shows the values which can be specified in (s2) and (s3) for each function number.':
                    finish_note()
                    domain = 'control_data'
                    continue
                if _chart_start(stripped) and current_note['lines'][-1].rstrip().endswith('.'):
                    finish_note()
                    domain = 'execution_timing'
                    continue
                if ('|' in stripped and not re.search(r'following|shown below|table',
                        ' '.join(current_note['lines']), re.I)):
                    finish_note()
                    continue
                current_note['lines'].append(stripped)
                if int(page) not in current_note['pages']:
                    current_note['pages'].append(int(page))
            elif current_error:
                current_error['lines'].append(stripped.removeprefix('|').strip())
                if int(page) not in current_error['pages']:
                    current_error['pages'].append(int(page))
    finish_error()
    finish_note()
    return errors, notes


def _geometry_result_columns(section, page):
    """Recover a bottom-of-page result table by its three real x columns."""
    geometry = section.get('page_words') or {}
    words = geometry.get(int(page), geometry.get(str(page), [])) if isinstance(geometry, dict) else []
    if not words or not all(isinstance(word, dict) and {'text', 'x0', 'x1', 'top', 'bottom'} <= word.keys() for word in words):
        return None
    lines = []
    for word in sorted(words, key=lambda item: (item['top'], item['x0'])):
        if not lines or abs(lines[-1]['top'] - word['top']) > 0.7:
            lines.append({'top': word['top'], 'words': [word]})
        else:
            lines[-1]['words'].append(word)
    for line in lines:
        line['words'].sort(key=lambda item: item['x0'])
        line['text'] = ' '.join(word['text'] for word in line['words'])
    headers = [line for line in lines if line['text'] == 'Data type is INT Data type is DINT Data type is REAL']
    if len(headers) != 1:
        return None
    header = headers[0]
    anchors = [header['words'][index]['x0'] for index in (0, 4, 8)]
    footer = next((line['top'] for line in lines if line['top'] > header['top'] and
                   re.fullmatch(r'\d+ ARITHMETIC OPERATION FUNCTIONS', line['text'])), None)
    if footer is None:
        return None
    columns = []
    for index, data_type in enumerate(('INT', 'DINT', 'REAL')):
        cell_lines = []
        for line in lines:
            if not (header['top'] + 2 < line['top'] < footer):
                continue
            selected = [word for word in line['words'] if word['x0'] >= anchors[index] - 0.7 and
                        (index == 2 or word['x0'] < anchors[index + 1] - 0.7)]
            if selected:
                text = ' '.join(word['text'] for word in selected)
                if re.match(r'^\[Example\b', text):
                    break
                # A word crossing a neighbouring column is ambiguous. Keep
                # the original unresolved table rather than assign it by order.
                if index < 2 and any(word['x1'] > anchors[index + 1] for word in selected):
                    return None
                cell_lines.append(text)
        text = _flat(' '.join(cell_lines))
        if not text.endswith('.'):
            return None
        columns.append((data_type, text))
    return columns


def _high_speed_control_blocks(section):
    """The real HIOEN/DHIOEN selector and bit tables, including after examples."""
    source = _flat(' '.join(layout for _page, layout in section.get('page_layouts') or ()))
    roles = re.search(r'Specify the number of the function to be started or stopped in (?P<selector>\(s1\)), '
                      r'the bit of the channel to be started in (?P<start>\(s2\)), and the bit of the '
                      r'channel to be stopped in (?P<stop>\(s3\))\.', source)
    if not roles:
        return [], {}
    selector, start, stop = (_subjects(roles[name])[0] for name in ('selector', 'start', 'stop'))
    blocks, skipped, current = [], {}, None

    def finish():
        nonlocal current
        if current:
            current['text'] = _flat(' '.join(current.pop('lines')))
            blocks.append(current)
            current = None

    for page, layout in section.get('page_layouts') or ():
        lines = (layout or '').splitlines()
        enum_rows = [(number, line.strip()) for number, line in enumerate(lines, 1)
                     if re.match(r'^K\d+(?:\*\d+)*\s*\|', line.strip())]
        if (len(enum_rows) == 10 and any(line.strip() == '[GLYPH-F0FB]Function number' for line in lines) and
                not any('Parameter setting' in line for line in lines)):
            blocks.append({'kind': 'control_data', 'page': int(page), 'pages': [int(page)],
                           'line': enum_rows[0][0], 'domain': 'technical_subsection',
                           'bound_subjects': [selector], 'binding_basis': 'literal_function_selector_definition',
                           'control_table_kind': 'high_speed_function_numbers',
                           'text': ' • '.join(line for _number, line in enum_rows)})
            skipped.setdefault(int(page), set()).update(number for number, _line in enum_rows)
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            heading = re.fullmatch(r'• Function numbers? (?P<values>K\d+(?: to K\d+| and K\d+)?)', stripped)
            if heading:
                finish()
                values = [int(value) for value in re.findall(r'K(\d+)', heading['values'])]
                if ' to ' in heading['values']:
                    values = list(range(values[0], values[-1] + 1))
                current = {'kind': 'control_data', 'page': int(page), 'pages': [int(page)], 'line': number,
                           'domain': 'technical_subsection', 'bound_subjects': [selector, start, stop],
                           'binding_basis': 'literal_function_selector_and_start_stop_definitions',
                           'control_table_kind': 'high_speed_function_mask', 'function_values': values,
                           'selector_subject': selector, 'start_subject': start, 'stop_subject': stop, 'lines': []}
                skipped.setdefault(int(page), set()).add(number)
                continue
            if current:
                if _heading(stripped) in {'program_example', 'precautions', 'operation_error'}:
                    finish()
                    continue
                if not stripped or FOOTER.match(stripped):
                    continue
                current['lines'].append(stripped)
                if int(page) not in current['pages']:
                    current['pages'].append(int(page))
                skipped.setdefault(int(page), set()).add(number)
    finish()
    return blocks, skipped


def _file_transfer_combination_blocks(section):
    """The printed selector/quantity/position/result columns form one table."""
    blocks, skipped = [], {}
    for page, layout in section.get('page_layouts') or ():
        flat = _flat(layout)
        header = re.search(r'Execution/completion type \((?P<mode>d1)\)\+0 Data type specification '
                           r'\((?P=mode)\)\+7 Processing unit and setting range Number of '
                           r'(?:(?P<read>read- target data) \((?P<count>d1)\)\+2|'
                           r'request write data \((?P<write_count>s3)\)\+0) File position '
                           r'\((?P=mode)\)\+4, \((?P=mode)\)\+5 Number of data actually '
                           r'(?:(?:read \((?P<actual_read>d2)\)\+0)|(?:written \((?P<actual_write>d1)\)\+2))', flat)
        if not header:
            continue
        lines = (layout or '').splitlines()
        start = next((index for index, line in enumerate(lines)
                      if re.match(r'^(?:0000H: 16-bit binary|0100H: Decimal)', line.strip())), None)
        if start is None:
            continue
        end = next((index for index in range(start, len(lines))
                    if lines[index].strip() == 'Execution/completion type' or FOOTER.match(lines[index].strip())), len(lines))
        body = _flat(' '.join(lines[start:end]))
        if not re.search(r'\b0001H:|\b0140H:', body):
            continue
        first_line = start
        while first_line and not NOTE_ROW.match(lines[first_line - 1].strip()):
            preceding = lines[first_line - 1].strip()
            if (preceding == 'Processing details' or FOOTER.match(preceding) or
                    (preceding.startswith('•') and not preceding.startswith('• Specify data in '))):
                break
            first_line -= 1
            if 'Combination of processing units' in preceding or preceding.startswith('• Specify data in '):
                break
        read = bool(header['read'])
        count = {'operand': header['count'], 'offset': 2} if read else {'operand': header['write_count'], 'offset': 0}
        actual = {'operand': header['actual_read'], 'offset': 0} if read else {'operand': header['actual_write'], 'offset': 2}
        blocks.append({'kind': 'control_data', 'page': int(page), 'pages': [int(page)], 'line': start + 1,
                       'domain': 'control_data', 'text': body, 'file_combination_table': True,
                       'operation': 'read' if read else 'write',
                       'mode_selector': {'operand': header['mode'], 'offset': 0},
                       'unit_selector': {'operand': header['mode'], 'offset': 7},
                       'count_subject': count, 'actual_count_subject': actual,
                       'position_subject': {'operand': header['mode'], 'offset': 4},
                       'position_storage_offsets': [4, 5],
                       'bound_subjects': list(dict((item['operand'], item) for item in [
                           {'operand': header['mode']}, {'operand': count['operand']}, {'operand': actual['operand']}]).values()),
                       'binding_basis': 'literal_file_combination_column_headers'})
        # Repeated column headers at the foot of a continued page are table
        # content too. A new heading/bullet would have closed the table above.
        skipped[int(page)] = set(range(first_line + 1, len(lines) + 1))
    for block in blocks:
        continuation = next((other for other in blocks if other['page'] == block['page'] + 1 and
                             other['operation'] == block['operation']), None)
        if continuation:
            block['pages'].append(continuation['page'])
    return blocks, skipped


def _body_blocks(section):
    """Prose and control rows with physical boundaries, excluding examples.

    A chart without prose remains a diagram qualification, never an invented
    temporal relation. A control row is bound only by a literal ``Operand:``
    declaration and its printed offset, not by its position in a PDF table.
    """
    blocks, special_lines = _high_speed_control_blocks(section)
    file_blocks, file_lines = _file_transfer_combination_blocks(section)
    blocks.extend(file_blocks)
    for page, numbers in file_lines.items():
        special_lines.setdefault(page, set()).update(numbers)
    current = None
    note_active = False
    domain, table_domain, base, form = None, None, None, None
    inline_example_domain, inline_diagram_domain = None, None
    context_lists = {}
    context_list_lines = {}
    result_columns = {}
    result_column_lines = {}
    result_column_basis = {}
    for page, layout in section.get('page_layouts') or ():
        lines = (layout or '').splitlines()
        # These labelled clauses are normative continuations of "following
        # block", although PDF reading order places them below the examples.
        if re.search(r'the following block is read\.', _flat(layout)):
            items = [(number, line.strip()) for number, line in enumerate(lines, 1)
                     if re.match(r'^- (?:Sequence program|SFC program \(within the action\)):', line.strip())]
            if len(items) == 2:
                context_lists[int(page)] = items
                context_list_lines[int(page)] = {number for number, _line in items}
        for number, line in enumerate(lines, 1):
            headers = [part.strip() for part in line.strip().split('|')]
            if line.strip() == 'Data type is INT Data type is DINT Data type is REAL':
                columns = _geometry_result_columns(section, page)
                after = lines[number:]
                next_heading = next((offset for offset, tail in enumerate(after)
                                     if _heading(tail.strip()) not in {None, 'program_example'} or
                                     tail.strip().startswith('[GLYPH-F0FB]')), None)
                # These physical tables finish at the page footer. A following
                # subsection needs its own geometric bottom, not this shortcut.
                if columns and next_heading is None:
                    result_columns[(int(page), number)] = columns
                    result_column_lines[(int(page), number)] = set(range(number, len(lines) + 1))
                    result_column_basis[(int(page), number)] = 'word_geometry_column_bounds'
                continue
            if len(headers) < 2 or not all(re.fullmatch(r'Data type is (?:INT|DINT|REAL)', part) for part in headers):
                continue
            cells = [[] for _ in headers]
            consumed = {number}
            for continuation_number, continuation in enumerate(lines[number:], number + 1):
                if re.match(r'^\s*\[Example\b', continuation, re.I) or '|' not in continuation:
                    break
                fragments = [part.strip() for part in continuation.split('|')]
                if len(fragments) > len(headers):
                    break
                for column, fragment in enumerate(fragments):
                    if fragment:
                        cells[column].append(fragment)
                consumed.add(continuation_number)
            if all(cell and _flat(' '.join(cell)).endswith('.') for cell in cells):
                result_columns[(int(page), number)] = [(header.removeprefix('Data type is '), _flat(' '.join(cell)))
                                                       for header, cell in zip(headers, cells)]
                result_column_lines[(int(page), number)] = consumed
                result_column_basis[(int(page), number)] = 'literal_pipe_columns_and_headers'

    def finish():
        nonlocal current
        if current:
            current['text'] = _flat(' '.join(current.pop('lines')))
            legend = re.search(r'\(\d+\): (?:Up to|Use ["/\\]|Can be omitted|The folder path|The delimiter)', current['text'])
            if current['kind'] == 'control_data' and not current.get('bound_subjects') and legend:
                owner = next((block for block in reversed(blocks) if block['page'] == current['page'] and
                              block.get('bound_subjects') and re.search(r'(?:File|Folder) name character', block['text'])), None)
                if owner:
                    owner['text'] += ' ' + current['text'][legend.start():]
                    owner.setdefault('related_source_lines', []).append(current['line'])
                    current = None
                    return
            if re.search(r'the following block is read\.$', current['text']):
                items = context_lists.get(current['page'], [])
                if items:
                    current['text'] += ' ' + ' '.join(text for _number, text in items)
                    current['related_source_lines'] = [number for number, _text in items]
                    current['list_binding_basis'] = 'explicit_following_block_program_context'
            if current['text']:
                if current['kind'] != 'processing_qualifications' or QUALIFYING_PROSE.search(current['text']):
                    if current['kind'] == 'processing_qualifications' and re.search(
                            r'\b(?:scan|after|before|until|while|during|drive contact|execution command|'
                            r'completion flag|completion device)\b', current['text'], re.I):
                        current['kind'] = 'execution_timing'
                    blocks.append(current)
            current = None

    def begin(kind, page, line_number, text, **extra):
        nonlocal current
        current = {'kind': kind, 'page': int(page), 'pages': [int(page)], 'line': line_number,
                   'domain': domain, 'lines': [text], **extra}

    for page, layout in section.get('page_layouts') or ():
        active_result_lines = set()
        layout_lines = (layout or '').splitlines()
        for line_number, line in enumerate(layout_lines, 1):
            stripped = line.strip()
            if line_number in special_lines.get(int(page), set()):
                continue
            if inline_diagram_domain:
                if not stripped:
                    domain, inline_diagram_domain = inline_diagram_domain, None
                continue
            columns = result_columns.get((int(page), line_number))
            if columns:
                intro = _flat(' '.join(current['lines'])) if current else ''
                antecedent = re.match(r'^•?\s*(If .+?), the result will be output as follows from \(d\)\.$', intro)
                if not antecedent:
                    antecedent = re.search(r'(If an underflow(?: and an overflow)? occurs? in the operation result), '
                                           r'the result will be output as follows from \(d\)\.', _flat(layout))
                    if antecedent:
                        intro = antecedent[0]
                if antecedent:
                    # The real table columns, not row position or type order,
                    # identify each overload. Preserve the shared antecedent
                    # on a column whose cell does not repeat it.
                    current = None
                    domain, table_domain, base = 'processing_details', None, None
                    active_result_lines.update(result_column_lines[(int(page), line_number)])
                    for data_type, text in columns:
                        text = text.lstrip('• ')
                        if not re.match(r'^(?:Even if|If|When)\b', text, re.I):
                            text = antecedent[1] + ', ' + text[0].lower() + text[1:]
                        begin('processing_qualifications', page, line_number, text,
                              operand_type_conditions=[{'operand': 'd', 'data_type': data_type}],
                              source_table_column=data_type, table_antecedent=intro,
                              table_binding_basis=result_column_basis[(int(page), line_number)])
                        finish()
                    continue
            if (line_number in context_list_lines.get(int(page), set()) or
                    line_number in active_result_lines):
                continue
            if (not stripped or FOOTER.match(stripped)) and current and re.search(r'[,;]$|\b(?:is|are|was|were|the|a|an|of|by|and|or|to|from|with|in|for|increment|decrement)$',
                                                     current['lines'][-1], re.I):
                # A blank PDF layout line can bisect an unfinished sentence.
                # Preserve its actual continuation; the next heading/bullet
                # still closes the block normally.
                continue
            if not stripped or FOOTER.match(stripped):
                finish()
                note_active = False
                continue
            plain = GLYPH.sub('', stripped).strip()
            heading = _heading(stripped)
            if (heading == 'program_example' and current and
                    sum(part.count('(') - part.count(')') for part in current['lines']) > 0):
                # A wrapped parenthetical "(for / example, ...)" is part of
                # the same normative sentence, not an Example heading.
                heading = None
            if heading:
                note_active = False
                if heading == 'execution_timing' and domain == 'program_example':
                    continue
                finish()
                if heading == 'program_example' and stripped.startswith('[Example'):
                    if domain != 'program_example':
                        inline_example_domain = domain
                else:
                    inline_example_domain = None
                domain = heading
                if heading == 'processing_details':
                    table_domain, base = None, None
                elif heading == 'setting_data':
                    table_domain, base, form = 'operand_ranges', None, None
                elif heading in {'precautions', 'execution_timing'}:
                    table_domain, base, form = None, None, None
                elif heading in {'applicable_devices', 'operation_error', 'program_example', 'language_form'}:
                    table_domain, base, form = None, None, None
                continue
            if NORMATIVE_INPUT.match(stripped):
                finish()
                domain, table_domain, base = 'processing_details', None, None
            if domain == 'control_data' and re.match(
                    r'^•\s*(?:Whether (?:the )?execution|The normal or abnormal completion|'
                    r'Normal completion or abnormal completion)', stripped, re.I):
                finish()
                domain, table_domain, base = 'processing_details', None, None
            if domain == 'program_example':
                if inline_example_domain and stripped.startswith('•') and QUALIFYING_PROSE.search(stripped):
                    domain, inline_example_domain = inline_example_domain, None
                elif plain == 'Operation result' and re.search(r'Function (?:with|without) EN/ENO', layout):
                    finish()
                    domain, table_domain, base = 'processing_details', None, None
                else:
                    continue
            if (domain in {'processing_details', 'execution_timing'} and stripped == 'Command' and
                    line_number < len(layout_lines) and layout_lines[line_number].strip() == 'input'):
                finish()
                inline_diagram_domain, domain = domain, 'inline_diagram'
                continue
            if (domain in {'processing_details', 'execution_timing'} and current and
                    re.search(r'as shown below\.$', current['lines'][-1], re.I) and
                    re.fullmatch(r'(?:SM|M|X|Y|D|T|C)\d+', stripped)):
                finish()
                domain = 'program_example'
                continue
            if stripped.startswith('Error code') and re.search(r'Description|cause', stripped, re.I):
                finish()
                domain, table_domain, base = 'operation_error', None, None
                continue
            declaration = re.match(r'^Operand\s*:\s*(\([sdnm][0-9]?\))', plain, re.I)
            if declaration:
                finish()
                note_active = False
                base = _subjects(declaration[1])[0]
                domain, table_domain = 'control_data', 'control_data'
                continue
            if re.fullmatch(r'Control data(?:\s*\([sdnm][0-9]?\))?', plain, re.I):
                finish()
                note_active = False
                declared = _subjects(plain)
                base = declared[0] if len(declared) == 1 else None
                domain, table_domain = 'control_data', 'control_data'
                continue
            if stripped.startswith('[GLYPH-F0FB]'):
                note_active = False
                # In control tables these glyphs also introduce branch values;
                # retaining them with their row is essential to avoid promoting
                # a branch-specific interval into an unconditional range.
                next_base = _subjects(plain)
                if (domain == 'setting_data' and table_domain == 'operand_ranges' and current and
                        re.match(r'^FX5(?:S|UJ|U|UC)(?:/FX5(?:S|UJ|U|UC))* CPU module', plain)):
                    current['lines'].append(stripped)
                    continue
                if domain == 'control_data' and len(next_base) == 1 and not re.match(r'^When\b', plain, re.I):
                    finish()
                    # A field's title can precede continuation rows of the
                    # previous table in PDF reading order. Only an explicit
                    # "Operand:" declaration changes their base operand.
                    continue
                if domain == 'control_data' and current:
                    current['lines'].append(stripped)
                    continue
                finish()
                if re.match(r'^Descriptions?, ranges?, (?:and )?data types?', plain, re.I):
                    table_domain = 'operand_ranges'
                elif re.match(r'^Applicable devices', plain, re.I):
                    domain, table_domain, base = 'applicable_devices', None, None
                elif domain not in {'precautions', 'execution_timing'}:
                    domain, table_domain = 'processing_details', None
                continue
            if NOTE_ROW.match(stripped):
                finish()
                note_active = True
                continue
            if note_active and _chart_start(stripped):
                note_active = False
                domain, table_domain, base = 'execution_timing', None, None
            if note_active:
                continue
            if domain == 'operation_error' and ERROR_REPORTING.match(stripped):
                finish()
                domain, table_domain = 'error_reporting', None
            if domain in {'operation_error', 'applicable_devices', 'language_form'}:
                continue
            if 'Operand' in plain and ('Description' in plain or 'Data type' in plain):
                table_domain = 'operand_ranges'
                continue
            if table_domain == 'operand_ranges':
                row = re.match(r'^(?P<operand>\((?:[sdnmptijk][0-9]?|U/H|U|H|Coil|Value|Set value)\)|ENO\b|EN\b)\s*(?P<body>.*)', stripped, re.I)
                if row:
                    if (current and current['kind'] == 'operand_ranges' and
                            re.match(r'^\+\d+\s*:', row['body']) and
                            _subjects(row['operand']) == current.get('bound_subjects')):
                        # Named array fields belong to the immediately preceding
                        # literal argument row, not independent scalar operands.
                        current['lines'].append(stripped)
                        continue
                    finish()
                    subject = _subjects(row['operand']) or [{'operand': row['operand']}]
                    form_body = re.sub(r'^(?:\*\d+\s*)+', '', row['body'].lstrip())
                    form_match = re.match(r'\s*(' + TABLE_FORM + r')(?=\s*(?:\||$)|\s+Comparison data\b)', form_body)
                    form = form_match[1] if form_match else None
                    begin('operand_ranges', page, line_number, row['body'], bound_subjects=subject,
                          binding_basis='literal_operand_table_row', form=form)
                elif current and current['kind'] == 'operand_ranges':
                    # Unsigned/signed continuation rows must retain their form.
                    form_match = re.match(r'^(' + TABLE_FORM + r')\s*\|', stripped)
                    if form_match:
                        inherited = current['bound_subjects']
                        finish()
                        begin('operand_ranges', page, line_number, stripped, bound_subjects=inherited,
                              binding_basis='literal_operand_table_form_continuation', form=form_match[1])
                    else:
                        current['lines'].append(stripped)
                continue
            if table_domain == 'control_data' or domain == 'control_data':
                row = re.match(r'^(?P<operand>\([sdnm][0-9]?\))?\+(?P<offset>\d+)'
                               r'(?:\s+to\s+\+(?P<end>\d+))?\s*\|?\s*(?P<body>.*)', stripped)
                if row:
                    if row['operand'] and re.fullmatch(r'[.)]+', row['body'].strip()):
                        if current:
                            current['lines'].append(stripped)
                        continue
                    if row['operand']:
                        base = _subjects(row['operand'])[0]
                    # An empty second offset denotes the second word of one
                    # control item; no register order is inferred from a table.
                    tail = row['body'].lstrip('| ')
                    continuation = current and (not tail or tail.startswith('•') or
                                                  re.fullmatch(r'[0-9A-F]+H', tail))
                    if continuation:
                        current.setdefault('printed_offsets', []).append(int(row['offset']))
                        current['lines'].append(row['body'])
                    else:
                        finish()
                        subject = [{**base, 'offset': int(row['offset'])}] if base else []
                        begin('control_data', page, line_number, row['body'], bound_subjects=subject,
                              binding_basis='literal_control_operand_and_offset' if base else 'not_established',
                              printed_offsets=list(range(int(row['offset']), int(row['end'] or row['offset']) + 1)))
                        owner = re.search(r'\b(User|System)\s*$', row['body'])
                        if owner:
                            current['set_by'] = owner[1].lower()
                    continue
                if re.match(r'^(?:Device\s+Item|Device\s*\||b\d+\s*\|)', plain):
                    continue
            if stripped.startswith('•'):
                # Control-table bullets are branches of the same row. Their
                # selector and bounds have to stay in one qualification.
                if current and current['kind'] in {'control_data', 'operand_ranges'}:
                    current['lines'].append(stripped)
                    continue
                finish()
            if current:
                current['lines'].append(stripped)
                if int(page) not in current['pages']:
                    current['pages'].append(int(page))
            elif domain in {'precautions', 'execution_timing', 'control_data', 'processing_details', 'error_reporting'}:
                kind = 'processing_qualifications' if domain in {'processing_details', 'error_reporting'} else domain
                begin(kind, page, line_number, stripped)
    finish()
    return sorted(blocks, key=lambda block: (block['page'], block['line']))


def _subjects(text):
    found = []
    for match in OPERAND.finditer(text):
        if match['name'] == 'P' and re.search(r'(?:[+*/<>=\-]|\[GLYPH-[0-9A-F]+\])$', text[:match.start()]):
            # Optional pulse suffix in +(P), BKCMP□(P), etc.
            continue
        if match['name'].upper() in {'K', 'H'} and re.search(r'constant\s*$', text[:match.start()], re.I):
            continue
        subject = {'operand': match['name'].replace(' ', '_')}
        if match['offset']:
            subject['offset'] = int(re.sub(r'\D', '', match['offset']))
        if match['bit']:
            subject['bit'] = int(re.sub(r'\D', '', match['bit']))
        if subject not in found:
            found.append(subject)
    return found


def _formula_qualifications(text):
    """Ordered technical notation, including unresolved relation glyphs.

    The tokens are not evaluated. In particular a lost superscript in ``2-126``
    is not silently interpreted as subtraction or promoted to a numeric domain.
    """
    for phrase, role in ((r'specified value \(stored value\)', 'specified_value'),
                         (r'Total number of characters', 'total_character_count'),
                         (r'Number of characters in the decimal part', 'decimal_character_count')):
        text = re.sub(phrase, '$' + role, text, flags=re.I)
    atom = r'(?:(?<![A-Za-z0-9_])\([sdnm][0-9]?\)|\b[sdnm][0-9]?\b|\$[a-z_]+|[+\-]?\d[\d,]*(?:\.\d+)?)'
    operator = r'(?:\[GLYPH-[0-9A-F]+\]|<=|>=|<>|!=|[+*/<>=\-])'
    formula = re.compile(atom + r'(?:\s*' + operator + r'\s*' + atom + r')+', re.I)
    token = re.compile(r'\[GLYPH-(?P<glyph>[0-9A-F]+)\]|\(?(?P<operand>[sdnm][0-9]?)\)?|'
                       r'\$(?P<role>[a-z_]+)|(?P<number>\d[\d,]*(?:\.\d+)?)|(?P<operator><=|>=|<>|!=|[+*/<>=\-])', re.I)
    found = []
    for match in formula.finditer(text):
        tokens = []
        for item in token.finditer(match[0]):
            if item['glyph']:
                tokens.append({'unresolved_operator_glyph': item['glyph']})
            elif item['operand']:
                tokens.append({'operand': item['operand']})
            elif item['role']:
                tokens.append({'role': item['role']})
            elif item['number']:
                number = item['number'].replace(',', '')
                tokens.append({'numeric_literal': float(number) if '.' in number else int(number)})
            else:
                tokens.append({'operator': item['operator']})
        if len(tokens) > 2:
            found.append({'tokens': tokens, 'interpretation': 'source_notation_only'})
    return found


def _symbols(cell):
    text = re.sub(r'\*\d+', '', _flat(cell)).strip().strip('()')
    return text.replace(' ', '_') if re.fullmatch(r'[sdnmptijk][0-9]?|U/H|U|H|Coil|Value|Set value|IN[0-9]*|PT|PV|CV', text, re.I) else None


def _operand_bindings(section, record):
    declared, notes = set(), {}
    for item in record.get('operands') or record.get('parameters') or ():
        name = item.get('name') if isinstance(item, dict) else item
        if isinstance(name, str):
            declared.add(name.replace(' ', '_').casefold())
    for table in section.get('tables') or ():
        try:
            rows = json.loads(table.get('rows_json') or '[]')
        except (TypeError, ValueError):
            continue
        if not rows or not any('Operand' in str(cell) for cell in rows[0]):
            continue
        page = int(table.get('pdf_page') or section.get('first_page') or 0)
        header = ' '.join(str(cell or '') for row in rows[:3] for cell in row)
        domain = 'applicable_devices' if re.search(r'Bit devices?|Word devices?|Constants?', header, re.I) else 'setting_data'
        for row in rows[1:]:
            if not row:
                continue
            symbol = _symbols(row[0])
            if not symbol:
                continue
            declared.add(symbol.casefold())
            for cell in row:
                for marker in re.findall(r'\*(\d+)', str(cell or '')):
                    notes.setdefault((page, domain, int(marker)), set()).add(symbol)
    # The extractor deliberately leaves some symbol-only PDF cells blank.
    # Recover only literal layout row labels, never their ordinal positions.
    domain, symbol = None, None
    for page, layout in section.get('page_layouts') or ():
        for line in (layout or '').splitlines():
            heading = _heading(line.strip())
            if heading:
                domain, symbol = heading, None
                continue
            if domain not in {'setting_data', 'applicable_devices'}:
                continue
            row = re.match(r'^\s*(\([A-Za-z][A-Za-z0-9 /]*\)|ENO\b|EN\b)\s*(.*)', line)
            if row:
                name = _symbols(row[1])
                if name:
                    symbol = name
                    declared.add(symbol.casefold())
            elif NOTE_ROW.match(line.strip()) or not line.strip():
                symbol = None
            if symbol:
                for marker in re.findall(r'\*(\d+)', line):
                    notes.setdefault((int(page), domain, int(marker)), set()).add(symbol)
    for block in _body_blocks(section):
        for subject in block.get('bound_subjects') or ():
            declared.add(subject['operand'].casefold())
    declared.update(_argument_aliases(section))
    for fact in record.get('signature_facts') or ():
        for item in fact.get('value', {}).get('operands') or fact.get('value', {}).get('parameters') or ():
            if item.get('name'):
                declared.add(str(item['name']).replace(' ', '_').casefold())
    return declared, notes


def _argument_aliases(section):
    """Literal PART5 source-variable / formal-parameter declarations."""
    aliases, in_table = {}, False
    merged_from = None
    arity = re.search(r'The input variable "(?P<base>[sdnm])" can be changed in the range of '
                      r'(?P<min>\d+) to (?P<max>\d+)\.', section.get('text', ''))
    atom = re.compile(r'(?P<source>[sdnm][0-9]{0,2}|ENO|EN)(?=\s|\(|,|$)'
                      r'(?:\((?P<formal>[A-Z][A-Z0-9_]*)\))?')
    for _page, layout in section.get('page_layouts') or ():
        merged_from = None
        layout_lines = (layout or '').splitlines()
        for line_index, line in enumerate(layout_lines):
            stripped = line.strip()
            if re.match(r'^Argument\b.*Description.*Data type', stripped):
                in_table = True
                merged_from = None
                continue
            if not in_table:
                continue
            row = atom.match(stripped)
            if row:
                if not re.search(r'(?:Input|Output) variable', stripped) and row['formal'] and stripped[row.end():].strip():
                    # A description and its Type cell can wrap to separate PDF
                    # lines. Only the same literal Argument row may supply it;
                    # the next source name or heading closes the search.
                    continuation = []
                    for tail in layout_lines[line_index + 1:]:
                        tail = tail.strip()
                        if not tail or atom.match(tail) or _heading(tail) or re.match(r'^\(\d+\):', tail):
                            break
                        continuation.append(tail)
                        if re.search(r'(?:Input|Output) variable', tail):
                            stripped = _flat(stripped + ' ' + ' '.join(continuation))
                            break
                members = [(row['source'], row['formal'])]
                position = row.end()
                while True:
                    separator = re.match(r'\s*(?P<kind>,|and\b|to\b)\s*', stripped[position:])
                    if not separator:
                        break
                    following = atom.match(stripped, position + separator.end())
                    if not following:
                        break
                    if separator['kind'] == 'to':
                        first = re.fullmatch(r'([sdnm])(\d+)', members[-1][0])
                        last = re.fullmatch(r'([sdnm])(\d+)', following['source'])
                        if not first or not last or first[1] != last[1] or not (int(first[2]) < int(last[2]) <= 64):
                            break
                        formal_start = re.fullmatch(r'([A-Z]+)(\d+)', members[-1][1] or '')
                        formal_end = re.fullmatch(r'([A-Z]+)(\d+)', following['formal'] or '')
                        can_expand_formal = (formal_start and formal_end and formal_start[1] == formal_end[1] and
                                             int(formal_end[2]) - int(formal_start[2]) == int(last[2]) - int(first[2]))
                        members.extend((first[1] + str(index),
                                        formal_start[1] + str(int(formal_start[2]) + index - int(first[2])) if can_expand_formal else None)
                                       for index in range(int(first[2]) + 1, int(last[2]) + 1))
                    else:
                        members.append((following['source'], following['formal']))
                    position = following.end()
                explicit_direction = ('input' if 'Input variable' in stripped else
                                      'output' if 'Output variable' in stripped else None)
                # A vertically merged Argument cell can print the next source
                # and formal name on its own line (SEL: s3(IN1)). Its immediate
                # predecessor supplies the merged description and direction.
                # Bare diagram port labels or nonadjacent rows supply neither.
                merged = (explicit_direction is None and merged_from is not None and
                          not stripped[position:].strip() and all(formal for _source, formal in members))
                if explicit_direction is None and not merged:
                    merged_from = None
                    continue
                direction = explicit_direction or aliases[merged_from]['direction']
                for source, formal in members:
                    entry = {'source_name': source,
                             'direction': direction}
                    if formal:
                        entry['formal_name'] = formal
                    if merged:
                        entry['declaration_binding_basis'] = 'adjacent_argument_row_merged_description'
                        entry['merged_description_source'] = merged_from
                    indexed = re.fullmatch(r'([sdnm])(\d+)', source)
                    if arity and indexed and indexed[1] == arity['base']:
                        entry['variadic_group'] = arity['base']
                        entry['minimum_inputs'] = int(arity['min'])
                        entry['maximum_inputs'] = int(arity['max'])
                        entry['required_in_every_call'] = int(indexed[2]) <= int(arity['min'])
                    aliases[source.casefold()] = entry
                merged_from = members[-1][0].casefold()
            elif _heading(stripped) or re.match(r'^\(\d+\):', stripped):
                in_table = False
                merged_from = None
            else:
                merged_from = None
    return aliases


def _nearest_subject(text, start, end):
    matches = list(OPERAND.finditer(text))
    if not matches:
        return None
    # A later sentence's operand is not the subject of this interval, even
    # when the PDF flattens both sentences into the same table cell. Prefer
    # the preceding literal operand within the current sentence.
    boundaries = list(re.finditer(r'[.!?]\s+|\u2022', text[:start]))
    sentence_start = boundaries[-1].end() if boundaries else 0
    preceding = [item for item in matches if sentence_start <= item.start() and item.end() <= start]
    match = preceding[-1] if preceding else min(
        matches, key=lambda item: min(abs(item.start() - end), abs(item.end() - start)))
    return _subjects(match[0])[0]


def _unit(text):
    tests = ((r'\b(?:milliseconds?|msec|ms)\b', 'ms'), (r'\b(?:microseconds?|us)\b', 'us'),
             (r'\bkHz\b', 'kHz'), (r'\bHz\b', 'Hz'), (r'%', 'percent'),
             (r'\bbytes?\b', 'byte'), (r'\bwords?\b', 'word'), (r'\bbits?\b', 'bit'),
             (r'\bcharacters?\b', 'character'), (r'\bpoints?\b', 'point'))
    return next((unit for pattern, unit in tests if re.search(pattern, text, re.I)), None)


def _following_unit(text):
    match = re.match(r'^\s*(?:\)\s*)?(milliseconds?|msec|ms|microseconds?|us|kHz|MHz|Hz|%|'
                     r'bytes?|words?|bits?|characters?|points?)\b', text, re.I)
    return _unit(match[1]) if match else None


def _quantity_role(text):
    """A declared quantity component, distinct from its storage operand."""
    tests = ((r'folder path hierarch', 'folder_path_depth'),
             (r'folder path and file name|folder path.*file name.*includ', 'combined_path_character_count'),
             (r'folder path', 'folder_path_character_count'),
             (r'file name', 'file_name_character_count'),
             (r'number of characters.*decimal part', 'decimal_character_count'),
             (r'total number of characters', 'total_character_count'),
             (r'number of columns', 'column_count'), (r'number of (?:read|write|data)', 'data_count'),
             (r'number of points', 'point_count'), (r'nesting', 'nesting_depth'))
    sentence = re.split(r'[.;•]', text)[-1]
    return next((role for pattern, role in tests if re.search(pattern, sentence, re.I)), None)


def _numeric_qualifications(text):
    ranges, comparisons, constants = [], [], []
    for match in RANGE.finditer(text):
        try:
            lower, upper = _number(match['min']), _number(match['max'])
        except ValueError:
            continue
        value = {'minimum': lower['value'], 'maximum': upper['value'], 'inclusive': True,
                 'lower_encoding': lower['encoding'], 'upper_encoding': upper['encoding']}
        subject = _nearest_subject(text, match.start(), match.end())
        if subject:
            value['subject'] = subject
        unit = _following_unit(text[match.end():match.end() + 24])
        if unit:
            value['unit'] = unit
        ranges.append(value)
    for match in re.finditer(r'\((?P<left>[sdnm][0-9]?)\)\s*(?P<operator><=|>=|<>|!=|=|<|>)\s*'
                             r'\((?P<right>[sdnm][0-9]?)\)', text, re.I):
        comparisons.append({'left': {'operand': match['left']}, 'operator': match['operator'],
                            'right': {'operand': match['right']}})
    verbal = re.compile(r'(?P<operator>exceeds?|larger than|greater than|more than|less than|at least|at most|within|up to)\s+'
                         r'(?P<number>' + NUMBER + r')(?!\w)', re.I)
    operators = {'exceed': '>', 'exceeds': '>', 'larger than': '>', 'greater than': '>', 'more than': '>',
                 'less than': '<', 'at least': '>=', 'at most': '<=', 'within': '<=', 'up to': '<='}
    for match in verbal.finditer(text):
        try:
            number = _number(match['number'])
        except ValueError:
            continue
        comparison = {'subject': _nearest_subject(text, match.start(), match.end()) or
                             {'role': 'unbound_source_value'}, 'operator': operators[match['operator'].lower()],
                      'value': number['value'], 'encoding': number['encoding']}
        unit = _following_unit(text[match.end():match.end() + 24])
        if unit:
            comparison['unit'] = unit
        role = _quantity_role(text[:match.start()])
        if role:
            comparison['quantity_role'] = role
        comparisons.append(comparison)
    for match in re.finditer(r'(?P<number>' + NUMBER + r')\s+(?:(?P<unit>characters?|bytes?|words?|bits?|points?|levels?)\s+)?'
                             r'or\s+(?P<operator>more|less)\b', text, re.I):
        try:
            number = _number(match['number'])
        except ValueError:
            continue
        comparison = {'subject': _nearest_subject(text, match.start(), match.end()) or
                             {'role': 'unbound_source_value'}, 'operator': '>=' if match['operator'].lower() == 'more' else '<=',
                      'value': number['value'], 'encoding': number['encoding']}
        if match['unit']:
            comparison['unit'] = 'level' if match['unit'].lower().startswith('level') else _unit(match['unit'])
        role = _quantity_role(text[:match.start()])
        if role:
            comparison['quantity_role'] = role
        comparisons.append(comparison)
    for match in HEX.finditer(text):
        value = {'value': int(match['value'], 16), 'encoding': 'hexadecimal'}
        if value not in constants:
            constants.append(value)
    for match in DECIMAL.finditer(text):
        # A decimal regex must not re-read the prefix of a hexadecimal value.
        if any(hit.start() <= match.start() < hit.end() for hit in HEX.finditer(text)):
            continue
        value = {'value': _number(match['value'])['value'], 'encoding': 'decimal'}
        if match['unit']:
            value['unit'] = match['unit']
        if value not in constants:
            constants.append(value)
    return {'ranges': ranges, 'comparisons': comparisons, 'numeric_values': constants,
            'hex_masks': [match['value'].upper() for match in HEX_MASK.finditer(text)]}


PREDICATES = (
    (r'exceed.*(?:device range|corresponding device|said device)|outside.*device range', 'device_span_out_of_range'),
    (r'overlap', 'device_ranges_overlap'),
    (r'outside.*(?:valid range|range)|out of.*range|other than.*(?:\d|range)|not.*within.*range', 'value_outside_allowed_domain'),
    (r'negative value', 'negative_value'),
    (r'0 is specified|value.*is 0\b|\bis zero\b', 'zero_value'),
    (r'BCD|binary-coded decimal', 'bcd_domain'),
    (r'ASCII|character code', 'character_code_domain'),
    (r'data type.*signed|signed operation|signed data is specified', 'signed_data'),
    (r'unsigned', 'unsigned_data'),
    (r'number of characters|character string length', 'character_count'),
    (r'decimal part', 'decimal_part'),
    (r'type.*differs|different.*type|type.*different', 'device_types_differ'),
    (r'module number.*differ', 'module_numbers_differ'),
    (r'module access device', 'module_access_device'),
    (r'bit device', 'bit_device'),
    (r'word device', 'word_device'),
    (r'digit specif', 'digit_specification'),
    (r'bit-specified|bit specified|\.b[" ]', 'word_bit_specification'),
    (r'number of nibbles', 'nibble_count'),
    (r'(?:is|are) executed before|instruction.*before.*instruction', 'instruction_execution_order'),
    (r'programmed between', 'instruction_placement_between_constructs'),
    (r'standby type program', 'standby_program'),
    (r'FB file|function block', 'function_block_context'),
    (r'not used as a label|label.*not.*exist', 'label_not_defined_in_program_file'),
    (r'cannot be used|can not be used|not allowed|not supported|cannot.*specif', 'not_permitted'),
    (r'only .*can be (?:used|specified|set)|only .*type.*label', 'exclusive_allowed_class'),
    (r'global label', 'global_label_required'),
    (r'define an array|define the array|array label', 'array_storage_required'),
    (r'Do not specify a label|Specification with a label is not allowed', 'label_not_permitted'),
    (r'(?:firmware|engineering tool).*version|supported version', 'version_dependency'),
    (r'(?:intelligent|function|CPU|analog|positioning).*module|FX5-', 'hardware_dependency'),
    (r'file.*not.*exist|folder.*not.*exist', 'file_or_folder_missing'),
    (r'(?:file|folder).*exist', 'file_system_existence'),
    (r'file.*(?:open|closed)|(?:not open|connection.*open)', 'open_state'),
    (r'file name|folder name|file path', 'file_system_name_or_path'),
    (r'read.only|write.protect|write protect', 'write_protection'),
    (r'simultaneous|at the same time|channel in use', 'resource_conflict'),
    (r'communication|connection|receive|send|SLMP|FTP|TCP|UDP', 'communication_state'),
    (r'port number', 'port_number'),
    (r'IP address|octet', 'ip_address'),
    (r'not turned ON when an error|complete flag.*not.*ON', 'completion_flag_suppressed_on_error'),
    (r'one scan|one-scan', 'single_scan'),
    (r'undefined', 'undefined_result'),
    (r'round(?:ed)? up', 'round_up'),
    (r'multiple(?:s)? of', 'alignment_multiple'),
    (r'odd.*byte|even.*byte', 'byte_parity'),
    (r'unit(?:s)? of words|word units', 'word_count_unit'),
    (r'add(?:ing)? (?:one|1) byte', 'odd_byte_padding'),
    (r'Unicode', 'unicode_string'),
    (r'shift JIS', 'shift_jis_encoding'),
    (r'NaN|not a number', 'nan_value'),
    (r'denormalized|denormal', 'subnormal_value'),
    (r'(?<!\w)-0(?!\w)', 'negative_zero'),
    (r'infinity|infinite', 'infinite_value'),
    (r'interrupt pointer', 'interrupt_pointer'),
    (r'high.speed counter', 'high_speed_counter'),
    (r'output mode|CW/CCW|PULSE/SIGN', 'pulse_output_mode'),
    (r'parameter setting|set by parameter|set with a parameter', 'parameter_configuration'),
    (r'step relay|state relay', 'step_relay'),
    (r'table number|coordinate point', 'table_or_coordinate_selection'),
    (r'\bENO\b', 'enable_output'),
    (r'\bFALSE\b', 'false_value'),
    (r'\bTRUE\b', 'true_value'),
    (r'refer to|Use .+ of the standard function', 'source_cross_reference'),
    (r'data type is|data type.*specified by a device|16.bit.*data|32.bit.*data', 'data_type_qualification'),
    (r'same device number can be specified|same device.*specified', 'same_device_qualification'),
    (r'least significant digit', 'device_address_alignment'),
    (r'nesting|nested', 'nesting_depth'),
    (r'main program|other program file', 'program_file_context'),
    (r'pointer.*does not exist|subroutine.*(?:do|does) not exist', 'program_target_missing'),
    (r'\bnot set\b', 'required_value_missing'),
    (r'not.*K4|not.*K8', 'digit_count_not_permitted'),
    (r'combination.*(?:invalid|specified)|invalid combination', 'invalid_configuration_combination'),
    (r'is ignored|are ignored', 'ignored_value'),
    (r'\bEQ is =|\bNE is <>', 'comparison_aliases'),
    (r'indexing is not available|index modification.*(?:not|cannot)|cannot.*index modif', 'indexing_not_permitted'),
    (r'completion device|complete device|completion status', 'completion_state'),
    (r'drive contact|execution command|execution condition', 'execution_contact'),
    (r'turn(?:s|ed)? (?:on|off)|switched (?:on|off)|turns from', 'state_transition'),
    (r'divis(?:or|ion).*0|division by zero', 'division_by_zero'),
    (r'change.*(?:during|while)|(?:during|while).*chang', 'change_during_execution'),
    (r'hold|retain|maintain|remain unchanged|remains unchanged', 'state_retention'),
    (r'reserved|do not use|must not use', 'reserved_storage'),
    (r'one instruction|same instruction|multiple.*instruction', 'instruction_resource_sharing'),
    (r'following scan|next scan|subsequent scan|scan after', 'scan_boundary'),
    (r'initial value|initialization|initial setting', 'initialization'),
    (r'watchdog|watch dog', 'watchdog_timer'),
    (r'bit inversion|inverted|invert', 'bit_inversion'),
    (r'completion.*(?:abnormal|normal)|completed.*(?:successfully|error)', 'completion_classification'),
    (r'\bCPU module\b|\bSD memory card\b|\bCPU parameter\b', 'execution_resource'),
)


def _literal_rules(text, value):
    """Relations whose direction, bound and subject are all explicit in source."""
    rules = []
    subjects = value.get('subjects', [])
    names = [subject['operand'] for subject in subjects]
    predicates = set(value.get('predicates', []))
    outside = re.search(r'other than|outside|out of|not (?:set )?within', text, re.I)
    if outside:
        for interval in value.get('ranges', []):
            rules.append({'kind': 'value_outside_interval', **interval})
    elif value.get('ranges') and re.search(r'set value is from|setting range|can be (?:set|specified)|'
                                           r'within the range|value is.*range', text, re.I):
        rules.extend({'kind': 'allowed_interval', **interval} for interval in value['ranges'])
    for comparison in value.get('comparisons', []):
        rules.append({'kind': 'source_comparison', **comparison})
    if 'negative_value' in predicates:
        rules.append({'kind': 'value_comparison', 'operator': '<', 'value': 0,
                      'subjects': subjects or [{'role': 'unbound_source_value'}]})
    zero = re.search(r'(?<!\d)0 is specified for (?P<tail>.+)|(?P<head>.+?)\bis 0\b|'
                     r'When 0 is set in (?P<tail2>.+)', text, re.I)
    if zero:
        rules.append({'kind': 'value_comparison', 'operator': '=', 'value': 0,
                      'subjects': subjects or [{'role': 'unbound_source_value'}]})
    if 'device_ranges_overlap' in predicates:
        rules.append({'kind': 'device_ranges_overlap', 'subjects': subjects,
                      'overlap_kind': 'partial' if 'partially' in text.lower() else 'not_qualified',
                      'same_head_exception': value.get('same_head_exception', False)})
    if 'device_span_out_of_range' in predicates:
        rule = {'kind': 'device_span_out_of_range', 'subjects': subjects}
        points = re.search(r'(?:range of )?(?:(?P<count>\d+)|\((?P<operand>n[12]?)\))\s*point', text, re.I)
        if points:
            rule['span'] = {'value': int(points['count'])} if points['count'] else {'operand': points['operand']}
        rules.append(rule)
    for predicate in ('device_types_differ', 'module_numbers_differ'):
        if predicate in predicates:
            rules.append({'kind': predicate, 'subjects': subjects})
    device_list = r'(?P<devices>(?:SD|SM|SB|SW|ST|LC|FD|[XYMSTCDRBF])(?:\s*[,/]?\s*(?:and\s+)?(?:SD|SM|SB|SW|ST|LC|FD|[XYMSTCDRBF]))*)'
    allow = re.search(r'Only\s+' + device_list + r'\s+can be (?:used|specified)', text)
    deny = re.search(device_list + r'\s+cannot be used', text)
    for match, relation in ((allow, 'allowed_device_classes'), (deny, 'disallowed_device_classes')):
        if match:
            classes = re.findall(r'\b(?:SD|SM|SB|SW|ST|LC|FD|[XYMSTCDRBF])\b', match['devices'])
            rules.append({'kind': relation, 'device_classes': classes, 'subjects': subjects})
    literal = r'["\u201c]?(?:[0-9A-F]+H|K-?\d+|\d+)["\u201d]?'
    allowed_values = re.search(r'(?:Only|other than)\s+(?P<values>' + literal +
                               r'(?:\s*(?:,|or|and)\s*' + literal + r')*)', text, re.I)
    if allowed_values and re.search(r'is (?:specified|set)|can be (?:used|specified|set)', text, re.I):
        numbers = [_number(number.removeprefix('K')) for number in re.findall(r'[0-9A-F]+H|K-?\d+|\d+', allowed_values['values'], re.I)]
        rules.append({'kind': 'allowed_value_set', 'values': numbers, 'subjects': subjects,
                      'source_describes': 'violation' if 'other than' in allowed_values[0].lower() else 'requirement'})
    multiple = re.search(r'(?:only )?multiples? of\s+(\d+)', text, re.I)
    if multiple:
        rules.append({'kind': 'alignment_multiple', 'multiple': int(multiple[1]), 'subjects': subjects})
    if 'digit specified bit type label cannot be used' in text.lower():
        rules.append({'kind': 'label_type_disallowed', 'label_type': 'digit_specified_bit', 'subjects': subjects})
    if re.search(r'Do not specify a label|Specification with a label is not allowed', text, re.I):
        rules.append({'kind': 'label_not_permitted', 'subjects': subjects})
    if 'global label assigned to the device' in text.lower():
        rules.append({'kind': 'label_requirement', 'label_scope': 'global', 'device_assignment_required': True,
                      'subjects': subjects})
    if 'array_storage_required' in predicates:
        rules.append({'kind': 'label_requirement', 'array_required': True, 'storage': 'complete_operation_area',
                      'reference': 'array_element', 'subjects': subjects})
    if re.search(r'Only decimal constant \(K\) can be used', text, re.I):
        rules.append({'kind': 'constant_class', 'allowed': ['K'], 'subjects': subjects})
    label = re.search(r'Only (?P<type>timer type/retentive timer type|long counter type|counter type) label can be used', text, re.I)
    if label:
        rules.append({'kind': 'label_type', 'allowed': [item.replace(' ', '_') for item in label['type'].lower().replace(' type', '').split('/')], 'subjects': subjects})
    if re.search(r'Only the FX5 intelligent function module can be (?:specified|used)', text):
        rules.append({'kind': 'module_class', 'allowed': 'FX5_intelligent_function_module', 'subjects': subjects})
    data_type = re.search(r'\bdata type is\s+(ANY_[A-Z]+|ANY\d+|BOOL|INT|DINT|WORD|DWORD)\b', text)
    if data_type:
        rules.append({'kind': 'data_type', 'data_type': data_type[1], 'subjects': subjects})
    if 'completion_flag_suppressed_on_error' in predicates:
        rules.append({'kind': 'completion_flag', 'when': 'instruction_error_detected', 'turn_on': False})
    if value.get('language_display_alias'):
        rules.append({'kind': 'language_display_alias', **value['language_display_alias']})
    version = re.search(r'engineering tool version\s*["\u201c]?(\d+\.\d+[A-Z]?)', text, re.I)
    if version and re.search(r'and later|or later', text, re.I):
        rules.append({'kind': 'engineering_tool_version', 'minimum': version[1]})
    if re.search(r'FX5S does not support this function', text):
        rules.append({'kind': 'cpu_support', 'models': ['FX5S'], 'supported': False})
    supported = re.search(r'Compatible only with\s+(FX5-[A-Z0-9\-]+)', text)
    if supported:
        rules.append({'kind': 'hardware_requirement', 'module': supported[1]})
    for phrase, kind in (('standby type program', 'standby_program'), ('FB file', 'function_block_file'),
                         ('main program', 'main_program'), ('interrupt program', 'interrupt_program')):
        if phrase in text and re.search(r'executed in|programmed in|written in', text):
            rules.append({'kind': 'invalid_program_context', 'context': kind,
                          'instruction_names': value.get('instruction_names', [])})
    before = re.search(r'(?P<executed>[A-Z][A-Z0-9]*(?:\(P\))?) instruction is executed before '
                        r'the (?P<required>[A-Z][A-Z0-9]*(?:\(P\))?) instruction', text)
    if before:
        after = re.search(r'after the ([A-Z][A-Z0-9]*(?:\(P\))?) instruction', text)
        rules.append({'kind': 'invalid_execution_order', 'executed': before['executed'],
                      'required_before': before['required'], **({'entered_via': after[1]} if after else {})})
    if 'label_not_defined_in_program_file' in predicates or 'program_target_missing' in predicates:
        rules.append({'kind': 'program_target_missing', 'subjects': subjects,
                      'scope': 'same_program_file' if 'same program file' in text else 'specified_target'})
    if value.get('device_state_conditions'):
        rules.append({'kind': 'device_state_qualification', 'states': value['device_state_conditions']})
    if value.get('referenced_pages'):
        rules.append({'kind': 'manual_cross_reference', 'pages': value['referenced_pages']})
    unsupported = re.search(r'The (?P<form>[A-Z][A-Z0-9_]*(?: and [A-Z][A-Z0-9_]*)?) instructions? '
                            r'(?:is|are) not supported by the (?P<languages>.+)', text)
    if unsupported:
        rules.append({'kind': 'language_availability', 'forms': unsupported['form'].split(' and '),
                      'languages': _languages(unsupported['languages']), 'supported': False})
    replacement = re.search(r'Use ([A-Z][A-Z0-9_]*) of the standard function', text)
    if replacement:
        rules.append({'kind': 'standard_function_reference', 'name': replacement[1],
                      'equivalence_to_instruction': 'not_established'})
    if value.get('same_head_exception'):
        rules.append({'kind': 'same_device_permitted', 'subjects': subjects})
    if re.search(r'\bEQ is =, NE is <>, GT is >, LE is <=, LT is <, and GE is >=', text):
        rules.append({'kind': 'comparison_aliases', 'operators': {'EQ': '=', 'NE': '<>', 'GT': '>', 'LE': '<=', 'LT': '<', 'GE': '>='}})
    between = re.search(r'(?:programmed|written|used) (?:and written )?(?:other than )?between '
                         r'(?:the )?([A-Z]+)(?: instructions?)?(?:-| and (?:the )?)([A-Z]+)', text)
    if between:
        rules.append({'kind': 'program_placement', 'construct_start': between[1], 'construct_end': between[2],
                      'relation': 'outside_required_region' if 'other than between' in text else 'inside_prohibited_region',
                      'instruction_names': value.get('instruction_names', [])})
    executed = re.search(r'(\d+)(?:st|nd|rd|th) level.*executed', text)
    if executed and 'nesting_depth' in predicates:
        rules.append({'kind': 'nesting_depth', 'invalid_depth': int(executed[1])})
    # The following retain the declared literals even where the subject is not
    # an operand. Such qualifications are never source-verified numeric rules.
    if 'device_address_alignment' in predicates and re.search(r'least significant digit.*["\u201c]0', text, re.I):
        rules.append({'kind': 'device_address_alignment', 'least_significant_digit': 0, 'subjects': subjects})
    if 'indexing_not_permitted' in predicates:
        rules.append({'kind': 'indexing', 'permitted': False, 'subjects': subjects})
    if re.search(r'(?:Do not|cannot|must not) (?:change|modify)|must not be changed', text, re.I):
        phases = []
        if re.search(r'while.*(?:execut|process)|during.*(?:execut|process)|until.*complet', text, re.I):
            phases.append('instruction_in_progress')
        rules.append({'kind': 'modification_restriction', 'subjects': subjects,
                      'phases': phases or ['source_context_requires_binding']})
    reserved = re.search(r'(?:set|fixed to)\s+(?P<value>[0-9A-F]+H|0)\b', text, re.I)
    if reserved:
        rules.append({'kind': 'required_value', 'subjects': subjects, **_number(reserved['value'])})
    if 'division_by_zero' in predicates:
        rules.append({'kind': 'division_by_zero', 'subjects': subjects})
    if re.search(r'(?:cannot|can not) be (?:used|executed).*interrupt|'
                 r'(?:instruction|SFC control instruction) is used in the interrupt routine program|'
                 r'which cannot be used in interruption routine program', text, re.I):
        rules.append({'kind': 'invalid_program_context', 'context': 'interrupt_program',
                      'instruction_names': value.get('instruction_names', [])})
    if re.search(r'(?:channel|axis|unit).*used by (?:an)?other instruction|already used by other instructions', text, re.I):
        resource = 'axis' if re.search(r'axis', text, re.I) else 'channel' if re.search(r'channel', text, re.I) else 'unit'
        rules.append({'kind': 'resource_in_use', 'resource': resource, 'subjects': subjects})
    if re.search(r'(?:not set by|not used by) parameters', text, re.I):
        rules.append({'kind': 'parameter_configuration_required', 'subjects': subjects})
    if re.search(r'(?:unit number|module|channel number of a module).*does not exist', text, re.I):
        rules.append({'kind': 'hardware_target_missing', 'subjects': subjects})
    if re.search(r'Timeout occurred while communicating', text, re.I):
        rules.append({'kind': 'communication_timeout'})
    if re.search(r'Signal error (?:is|was) detected while accessing', text, re.I):
        rules.append({'kind': 'communication_signal_error'})
    if re.search(r'Updating procedure.*not properly completed', text, re.I):
        rules.append({'kind': 'module_update_not_completed'})
    occupied = re.search(r'(?P<count>One|Two|Three|Four|\d+) devices are occupied from the device specified (?:by|in) '
                         r'(?P<subject>\([sdnm][0-9]?\))', text, re.I)
    if occupied:
        words = {'one': 1, 'two': 2, 'three': 3, 'four': 4}
        count = words.get(occupied['count'].lower())
        rules.append({'kind': 'occupied_device_span', 'subject': _subjects(occupied['subject'])[0],
                      'subjects': subjects, 'points_per_head': count if count is not None else int(occupied['count'])})
    if re.search(r'these devices are not used (?:in|by) other (?:machine )?controls', text, re.I):
        rules.append({'kind': 'exclusive_storage_use', 'subjects': subjects,
                      'reference': 'preceding_declared_device_span'})
    if re.search(r'CPU module.*(?:RUN to STOP|RUN.*STOP).*continues the processing', text, re.I):
        rules.append({'kind': 'cpu_mode_transition', 'from': 'run', 'to': 'stop',
                      'instruction_processing': 'continues'})
    if re.search(r'Only bit specification of word device is applicable', text, re.I):
        rules.append({'kind': 'allowed_device_form', 'device_form': 'word_device_bit', 'subjects': subjects})
    drive = re.search(r'Only drive (\d+) \(for the SD memory card\) can be set', text)
    if drive:
        rules.append({'kind': 'allowed_value_set', 'values': [{'value': int(drive[1]), 'encoding': 'decimal'}],
                      'subjects': subjects, 'drive_kind': 'sd_memory_card'})
    if re.search(r'Regardless of the program language.*data type is specified by a device', text, re.I):
        rules.append({'kind': 'data_type_resolution', 'basis': 'operand_device', 'language_independent': True})
    if re.search(r'System:.*stores the execution result', text, re.I):
        rules.append({'kind': 'control_data_ownership', 'set_by': 'system', 'subjects': subjects,
                      'stored_value': 'instruction_execution_result'})
    if re.search(r'User: Data to be set before the execution', text, re.I):
        rules.append({'kind': 'control_data_ownership', 'set_by': 'user', 'subjects': subjects,
                      'required_before': 'instruction_execution'})
    variable = re.search(r'input variable ["\u201c]([sdnm][0-9]?)["\u201d] can be changed in the range of', text)
    if variable:
        rules.extend({'kind': 'allowed_interval', 'subject': {'operand': variable[1]}, **interval}
                     for interval in value.get('ranges') or ())
    if re.search(r'for (?:other )?precautions.*refer to|for details on.*refer to|refer to.*Manual', text, re.I):
        manuals = []
        for name, role in (('Communication', 'communication'), ('Application', 'application'),
                           ('Positioning', 'positioning'), ('inverter manual', 'inverter')):
            if name in text:
                manuals.append(role)
        rules.append({'kind': 'manual_dependency', 'manual_topics': manuals or ['source_named_manual']})
    if re.search(r'When FALSE is output from ENO, data output from \(d\) is undefined', text):
        rules.append({'kind': 'conditional_result', 'when': {'operand': 'ENO', 'equals': False},
                      'result': {'operand': 'd', 'state': 'undefined', 'must_not_consume': True}})
    if re.search(r'Operation result\s*\|\s*\(d\).*'
                 r'No operation error occurred\s*\|\s*Operation output value.*'
                 r'An operation error occurred\s*\|\s*Indefinite value', text):
        rules.append({'kind': 'function_result_matrix', 'enable_form': 'without_EN_ENO',
                      'rows': [{'operation_error': False, 'result': 'operation_value'},
                               {'operation_error': True, 'result': 'undefined'}], 'output': 'd'})
    if re.search(r'Execution condition\s*\|\s*Operation result.*EN\s*\|\s*ENO\s*\|\s*\(d\).*'
                 r'TRUE \(Executes operation\)\s*\|\s*TRUE.*Operation output value.*'
                 r'FALSE \(Stops operation\)\s*\|\s*FALSE.*Indefinite value', text):
        rows = [{'EN': True, 'operation_error': False, 'ENO': True, 'output_state': 'operation_value'},
                {'EN': False, 'ENO': False, 'output_state': 'undefined'}]
        if 'FALSE (Operation error occurred)' in text:
            rows.insert(1, {'EN': True, 'operation_error': True, 'ENO': False, 'output_state': 'undefined'})
        rules.append({'kind': 'function_result_matrix', 'enable_form': 'with_EN_ENO', 'rows': rows, 'output': 'd'})
    if re.search(r'most significant bit is ["\u201c]1["\u201d].*negative binary value', text, re.I):
        rules.append({'kind': 'signed_comparison_interpretation', 'subjects': subjects,
                      'when': {'bit': 'most_significant', 'equals': True}, 'interpretation': 'negative',
                      'excluded_operation': 'unsigned' if re.search(r'Excluding unsigned', text, re.I) else 'source_not_declared'})
    if re.search(r'SET and RST instructions.*same output relay.*nearer the END instruction.*is output', text, re.I):
        rules.append({'kind': 'multiple_write_precedence', 'instruction_names': ['SET', 'RST'],
                      'target_class': 'Y', 'both_instructions_executed': True, 'winner': 'last_executed_before_END'})
    if re.search(r'written value is reflected at the first time.*instruction is executed after.*overwritten', text, re.I):
        rules.append({'kind': 'operand_update_visibility', 'visible_at': 'first_instruction_execution_after_write'})
    if re.search(r'counter does not count while the operation result remains on', text, re.I):
        rules.append({'kind': 'counter_count_condition', 'continuous_on': 'no_increment', 'increment_event': 'rising_edge'})
    if re.search(r'same OUT T and OUT ST instructions.*same scan twice or more.*same number of times', text, re.I):
        rules.append({'kind': 'timer_repeated_execution', 'time_updates_per_scan': 'number_of_executions',
                      'timer_forms': ['T', 'ST']})
    if re.search(r'do not update the current value or turn ON/OFF the contacts', text, re.I):
        rules.append({'kind': 'timer_skipped_execution', 'current_value': 'unchanged', 'contacts': 'unchanged',
                      'source_antecedent': 'skipped_by_CJ_while_timer_on' if re.search(r'skipped using the CJ', text) else 'requires_preceding_clause'})
    if re.search(r'device.*turned ON is held on even if the execution command turns OFF', text, re.I):
        rules.append({'kind': 'set_latch_retention', 'when': 'execution_command_off', 'device_state': True})
    pulse = re.search(r'turns ON the specified device for one scan when the (PLS|PLF) command '
                      r'(turns from OFF to ON|turns OFF from ON).*turns OFF in other cases', text, re.I)
    if pulse:
        rules.append({'kind': 'pulse_device_state', 'trigger': 'rising' if pulse[1] == 'PLS' else 'falling',
                      'on_duration_scans': 1, 'otherwise': False})
    if re.search(r'device specified by \(d\) sometimes turns ON for one scan or more.*'
                 r'jump by the CJ instruction.*subroutine program was not called', text, re.I):
        rules.append({'kind': 'pulse_skipped_execution', 'subject': {'operand': 'd'}, 'on_scans': {'minimum': 1},
                      'when_any': ['instruction_skipped_by_CJ', 'subroutine_not_called'],
                      'upper_bound': 'not_declared'})
    if re.search(r'CPU module.*ALT instruction.*reversal operation.*every operation cycle', text, re.I):
        rules.append({'kind': 'toggle_repetition', 'instruction': 'ALT', 'period': 'each_execution_cycle'})
    if re.search(r'number of parallel circuits.*not limited', text, re.I):
        rules.append({'kind': 'parallel_block_count', 'maximum': 'not_limited',
                      'when': 'recommended_program' if 'recommended program' in text else 'source_not_declared'})
    if re.search(r'LD instruction and the LDI instruction.*8 times or less', text, re.I):
        rules.append({'kind': 'logic_instruction_repetition_limit', 'instruction_names': ['LD', 'LDI'],
                      'maximum': 8, 'when': 'recommended_program_not_used'})
    if re.search(r'Nesting up to 15 levels \(N0 to N14\) is possible', text):
        rules.append({'kind': 'master_control_nesting', 'maximum_levels': 15,
                      'minimum_identifier': 'N0', 'maximum_identifier': 'N14'})
    if re.search(r'MC instruction.*smallest.*MCR instruction.*biggest', text):
        rules.append({'kind': 'master_control_identifier_order', 'MC': 'ascending', 'MCR': 'descending'})
    if re.search(r'cannot be used in FOR to NEXT, STL to RETSTL, P to RET.*I to IRET', text):
        rules.append({'kind': 'program_placement_prohibited',
                      'regions': [['FOR', 'NEXT'], ['STL', 'RETSTL'], ['P', 'RET'], ['I', 'IRET']]})
    if re.search(r'Addition by write during RUN mode results in an error', text):
        rules.append({'kind': 'online_change', 'operation': 'add_instruction', 'permitted': False})
    if re.search(r'number of characters is handled in byte units \(8 bits\)', text):
        rules.append({'kind': 'character_count_unit', 'unit': 'byte', 'bits_per_unit': 8})
    if re.search(r'two-byte character.*shift JIS.*counted 2', text, re.I):
        rules.append({'kind': 'character_count_unit', 'encoding': 'shift_jis',
                      'bytes_per_character': 2, 'count_per_character': 2})
    if re.search(r'file name(?:/folder name)? string.*cannot be read', text, re.I):
        rules.append({'kind': 'file_name_read_failure', 'subjects': subjects})
    if re.search(r'file name string contains no character', text, re.I):
        rules.append({'kind': 'file_name_empty', 'subjects': subjects})
    if re.search(r'(?:file name string|a file is specified.*file name string).*ends with a delimiter', text, re.I):
        rules.append({'kind': 'file_name_syntax', 'subjects': subjects, 'invalid': 'trailing_delimiter'})
    if re.search(r'(?:period|half-width period).*(?:at (?:its|the) end|at the end).*before each delimiter', text, re.I):
        rules.append({'kind': 'file_name_syntax', 'subjects': subjects,
                      'invalid': 'period_at_end_or_before_delimiter'})
    if re.search(r'half-width space.*(?:end|before each delimiter)', text, re.I):
        rules.append({'kind': 'file_name_syntax', 'subjects': subjects,
                      'invalid': 'space_at_end_or_before_delimiter'})
    if '$MELPRJ$' in text and re.search(r'(?:system folder.*is specified|specifying.*system folder.*cannot|do not specify.*system folder)', text, re.I):
        rules.append({'kind': 'system_folder_access_prohibited', 'folder': '$MELPRJ$', 'subjects': subjects,
                      'scope': 'folder_and_descendants'})
    if re.search(r'drive specified.*not the one for the SD memory card', text, re.I):
        rules.append({'kind': 'drive_kind_mismatch', 'required': 'sd_memory_card', 'subjects': subjects})
    if re.search(r'SD memory card is not inserted', text, re.I):
        rules.append({'kind': 'sd_memory_card_presence', 'state': 'absent'})
    if re.search(r'SD memory card is removed or unmounted during', text, re.I):
        rules.append({'kind': 'sd_memory_card_presence', 'state': 'removed_or_unmounted',
                      'when': 'instruction_in_progress'})
    if re.search(r'block does not exist|control target block does not exist', text, re.I):
        rules.append({'kind': 'sfc_target_missing', 'target': 'block', 'subjects': subjects})
    if re.search(r'No specified step exists in the specified block', text, re.I):
        rules.append({'kind': 'sfc_target_missing', 'target': 'step_in_block', 'subjects': subjects})
    if re.search(r'current step is specified within the action', text, re.I):
        rules.append({'kind': 'sfc_target_restriction', 'invalid_target': 'current_step', 'context': 'inside_action'})
    null = re.search(r'(?:No NULL code \((?P<code>[0-9A-F]+H)\) exists|["\u201c]?(?P<other>[0-9A-F]+H)["\u201d]? '
                     r'(?:is not set|does not exist between setting areas))', text)
    if null:
        rules.append({'kind': 'terminator_missing', 'subjects': subjects,
                      **_number(null['code'] or null['other']), 'search_scope': 'declared_device_area'})
    bit_digits = re.search(r'(?:number of digits is not|number of digits is not) K(?P<digits>\d+)', text, re.I)
    if bit_digits:
        rules.append({'kind': 'digit_specification', 'required_digits': int(bit_digits['digits']), 'subjects': subjects})
    if re.search(r'Xn data is not set in the ascending order', text):
        rules.append({'kind': 'scaling_table_order', 'coordinate': 'X', 'required': 'ascending'})
    if re.search(r'instructions before the occurrence of an error are executed', text, re.I):
        rules.append({'kind': 'partial_execution_on_error', 'completed_before_error': 'retained'})
    if re.search(r'whole (?:concatenated|deleted|specified) character string cannot be stored', text, re.I):
        rules.append({'kind': 'string_storage_span_out_of_range', 'subjects': subjects})
    if re.search(r'completes with an error, an error code is stored in the device specified', text, re.I):
        rules.append({'kind': 'completion_error_reporting', 'subjects': subjects,
                      'stored_when': 'completion_error', 'operation_error_is_separate': True,
                      'not_stored_when': 'operation_error' if re.search(r'not stored if.*operation error', text, re.I) else 'source_not_declared'})
    if not value.get('ranges') and re.search(r'(?:outside|out of) (?:the )?(?:valid|allowable|set)?\s*range|'
                                            r'(?:exceeds?|exceed in).*device|exceeds the end of the device', text, re.I):
        if re.search(r'device (?:area|range|points)|corresponding device|device.*end|device number', text, re.I):
            domain = 'cpu_device_area'
        elif re.search(r'file|storage file', text, re.I):
            domain = 'device_data_storage_file'
        elif re.search(r'scaling conversion data', text, re.I):
            domain = 'scaling_conversion_table_domain'
        else:
            domain = 'declared_parameter_domain'
        rules.append({'kind': 'value_outside_domain', 'subjects': subjects, 'domain_reference': domain,
                      'bounds': 'defined_by_referenced_domain'})
    count_relation = re.search(r'(?:value (?:stored in a device )?specified (?:in|by) )?'
                               r'(?P<left>\([sdnm][0-9]?\)) (?:exceeds|is greater than) '
                               r'(?:the )?number of characters of (?:a character string specified by )?'
                               r'(?P<right>\([sdnm][0-9]?\))', text, re.I)
    if count_relation:
        rules.append({'kind': 'operand_comparison', 'left': _subjects(count_relation['left'])[0], 'operator': '>',
                      'right': {'operation': 'character_count', 'subject': _subjects(count_relation['right'])[0]}})
    if re.search(r'The size of the read data exceeds the read device size', text, re.I):
        rules.append({'kind': 'data_storage_capacity', 'invalid_when': 'read_size_greater_than_destination_capacity',
                      'capacity_source': 'declared_read_device_span'})
    if re.search(r'module access device is specified for both', text, re.I):
        rules.append({'kind': 'device_form_combination', 'subjects': subjects,
                      'invalid_when': 'all_operands_module_access_device'})
    if re.search(r'An unavailable device is set|cannot be specified.*specified', text, re.I):
        rules.append({'kind': 'device_class_not_permitted', 'subjects': subjects,
                      'allowed_classes': 'declared_operand_device_form'})
    if re.search(r'data.*(?:is negative|negative value)', text, re.I) and not any(rule['kind'] == 'value_comparison' for rule in rules):
        rules.append({'kind': 'value_comparison', 'subjects': subjects, 'operator': '<', 'value': 0})
    if re.search(r'channel.*specified incorrectly|channel.*incorrect', text, re.I):
        rules.append({'kind': 'communication_configuration', 'field': 'channel', 'validity': 'invalid'})
    if re.search(r'arrival monitoring time.*specified incorrectly', text, re.I):
        rules.append({'kind': 'communication_configuration', 'field': 'arrival_monitoring_time', 'validity': 'invalid'})
    if re.search(r'number of resends.*specified incorrectly', text, re.I):
        rules.append({'kind': 'communication_configuration', 'field': 'resend_count', 'validity': 'invalid'})
    if re.search(r'(?:execution/error completion type|request data size|IP address or port number).*incorrect', text, re.I):
        field = 'execution_completion_type' if 'completion type' in text else 'request_data_size' if 'request data size' in text else 'target_ip_address_or_port'
        rules.append({'kind': 'communication_configuration', 'field': field, 'validity': 'invalid'})
    if re.search(r'station number/IP address.*not set', text, re.I):
        rules.append({'kind': 'communication_configuration', 'field': 'local_station_number_or_ip_address', 'validity': 'missing'})
    if re.search(r'response timeout occurred', text, re.I):
        rules.append({'kind': 'communication_timeout', 'phase': 'response'})
    if re.search(r'UDP/IP transmission failed', text, re.I):
        rules.append({'kind': 'communication_transport_error', 'transport': 'udp_ip'})
    if re.search(r'response with a data length exceeding the allowable range', text, re.I):
        rules.append({'kind': 'communication_response_length', 'validity': 'outside_declared_range'})
    if re.search(r'Do not execute .+ in an interrupt program', text, re.I):
        rules.append({'kind': 'invalid_program_context', 'context': 'interrupt_program'})
    flag = re.search(r'cannot be executed while (?P<flag>SM\d+)(?:\s*\([^)]*\))? is ON', text, re.I)
    if flag:
        rules.append({'kind': 'instruction_execution_permission', 'permitted': False,
                      'when': {'device': flag['flag'], 'equals': True}})
    flag = re.search(r'When (?P<flag>SM\d+) is turned ON during execution.*terminate abnormally', text, re.I)
    if flag:
        rules.append({'kind': 'conditional_completion', 'when': {'device': flag['flag'], 'transition': 'on'},
                      'phase': 'instruction_in_progress', 'completion': 'abnormal'})
    simultaneous = re.search(r'cannot be executed simultaneously with (?P<instructions>.+)', text, re.I)
    if simultaneous:
        names = re.findall(r'\bSP\.[A-Z]+\b', simultaneous['instructions'])
        if names:
            rules.append({'kind': 'instruction_mutual_exclusion', 'other_instructions': list(dict.fromkeys(names))})
    if re.search(r'Do not disconnect the power or remove the SD memory card during execution', text, re.I):
        rules.append({'kind': 'resource_retention_required', 'phase': 'instruction_in_progress',
                      'resources': ['power', 'sd_memory_card']})
    off = re.search(r'bit device (?P<subject>\([sdnm][0-9]?\)(?:\+\d+)?) turns off at the execution of the END instruction in the next scan', text, re.I)
    if off:
        rules.append({'kind': 'device_state_event', 'subject': _subjects(off['subject'])[0], 'state': False,
                      'event': 'END', 'scan': 'next_after_completion'})
    if re.search(r'device turns on during the END processing for the scan.*instruction is completed.*'
                 r'turns off during the next END processing', text, re.I):
        rules.append({'kind': 'completion_device_timing', 'subjects': subjects,
                      'on': {'event': 'END', 'scan': 'instruction_completion'},
                      'off': {'event': 'END', 'scan': 'next_after_completion'}})
    if re.search(r'When the output mode is CW/CCW, specify the CCW axis', text):
        rules.append({'kind': 'output_mode_axis_restriction', 'mode': 'cw_ccw', 'allowed_axis_role': ['ccw'],
                      'subjects': subjects})
    if re.search(r'output mode is PULSE/SIGN and using Y, only the SIGN output or general-purpose output of the self-axis', text):
        rules.append({'kind': 'output_mode_axis_restriction', 'mode': 'pulse_sign', 'device_class': 'Y',
                      'allowed_axis_role': ['sign', 'self_axis_general_output'], 'subjects': subjects})
    if re.fullmatch(r'Other than S can be used\.?', text):
        rules.append({'kind': 'disallowed_device_classes', 'device_classes': ['S'], 'subjects': subjects})
    for phrase, form in ((r'Digit specified bit device cannot be used', 'digit_specified_bit'),
                          (r'Bit-specified word device cannot be used', 'word_device_bit')):
        if re.search(phrase, text, re.I):
            rules.append({'kind': 'disallowed_device_form', 'device_form': form, 'subjects': subjects})
    if re.search(r'Values after the decimal point are rounded up', text, re.I):
        rules.append({'kind': 'rounding_mode', 'mode': 'source_round_up', 'subjects': subjects,
                      'negative_value_direction': 'not_stated'})
    coordinate = re.search(r'numbers? of coordinate points.*?(16|32)-bit unsigned binary data', text, re.I)
    if coordinate:
        rules.append({'kind': 'data_representation', 'subjects': subjects, 'width_bits': int(coordinate[1]),
                      'encoding': 'binary', 'signed': False})
    channel_status = re.search(r'If completion status.*Channel in use.*error codes: (?P<lower>[0-9A-F]+H) '
                               r'to (?P<upper>[0-9A-F]+H).*data is not stored', text, re.I)
    if channel_status:
        rules.append({'kind': 'conditional_data_storage', 'stored': False, 'subjects': subjects,
                      'when_completion_status': {'minimum': _number(channel_status['lower'])['value'],
                                                 'maximum': _number(channel_status['upper'])['value'], 'inclusive': True}})
    if re.search(r'flag turns on only during one scan time when the activation contact.*turns off and on', text, re.I):
        rules.append({'kind': 'flag_pulse_timing', 'subjects': subjects, 'duration_scans': 1,
                      'event': 'activation_contact_off_then_on'})
    if re.search(r'Parameter setting values are set to an SD device at STOP to RUN', text):
        rules.append({'kind': 'parameter_shadow_update', 'source': 'configured_parameters',
                      'storage': 'SD', 'event': 'stop_to_run'})
    serial = re.search(r'F/W of the serial number (?P<serial>\d[0-9A-Z]*X\*+) or earlier', text)
    if serial:
        rules.append({'kind': 'hardware_revision_support', 'serial_pattern': serial['serial'],
                      'comparison': 'earlier_or_equal', 'supported': False, 'models': value.get('cpu_models', [])})
    terminator = re.search(r'["\u201c](?P<value>[0-9A-F]+H)["\u201d] does not exist', text)
    if terminator and re.search(r'device|setting area|range', text, re.I):
        rules.append({'kind': 'terminator_missing', 'subjects': subjects, **_number(terminator['value']),
                      'search_scope': 'declared_device_area'})
    relation = re.search(r'(?:lower limit value|value).*?specified by (?P<left>\([sdnm][0-9]?\)) '
                         r'is greater than (?:the upper limit value|the value) specified by (?P<right>\([sdnm][0-9]?\))', text, re.I)
    if relation:
        rules.append({'kind': 'operand_comparison', 'left': _subjects(relation['left'])[0],
                      'operator': '>', 'right': _subjects(relation['right'])[0]})
    for phrase, kind in ((r'The specified device value is .*denormalized|specified device value is .*NaN', 'real_number_class_not_supported'),
                         (r'whole specified character string cannot be stored', 'string_storage_span_out_of_range'),
                         (r'range of points used.*exceeds the range of the target', 'device_span_out_of_range'),
                         (r'buffer memory number.*exceeds the buffer memory area', 'buffer_memory_span_out_of_range'),
                         (r'Device that cannot be specified is specified|device other than the target device is specified', 'device_class_not_permitted')):
        if re.search(phrase, text, re.I):
            rules.append({'kind': kind, 'subjects': subjects})
    if 'ignored_value' in predicates:
        rules.append({'kind': 'ignored_value', 'subjects': subjects,
                      'source_condition': 'not_fully_resolved'})
    if 'undefined_result' in predicates:
        rules.append({'kind': 'undefined_result', 'subjects': subjects,
                      'source_condition': 'not_fully_resolved'})
    transition = re.search(r'(?:drive contact|execution command|execution condition).*?'
                           r'(?:turns?|turned|changes?) (?:from )?(off to on|on to off)', text, re.I)
    if transition:
        rules.append({'kind': 'execution_contact_transition',
                      'transition': 'rising' if transition[1].lower() == 'off to on' else 'falling'})
    contact = re.search(r'(?:When|While|If) the (?:drive contact|execution command|execution condition) is (on|off)', text, re.I)
    if contact:
        rules.append({'kind': 'execution_contact_state', 'state': contact[1].lower(),
                      'consequent': 'requires_effect_binding'})
    scan = re.search(r'(?:one|1) scan(?: only)? (?:after|following) (?:the )?(?:execution|completion)', text, re.I)
    if scan:
        rules.append({'kind': 'scan_event_offset', 'offset': 1,
                      'event': 'completion' if re.search(r'completion', scan[0], re.I) else 'execution'})
    if re.search(r'Coil/specified bit.*When bit device is used\s*\|\s*OFF\s*\|\s*OFF\s+'
                 r'ON\s*\|\s*ON.*When bit of word device is specified\s*\|\s*OFF\s*\|\s*0\s+ON\s*\|\s*1', text):
        rules.append({'kind': 'output_condition_matrix', 'condition_source': 'preceding_operation_result',
                      'rows': [{'device_form': device_form, 'operation_result': state, 'written_value': state}
                               for device_form in ('bit_device', 'word_device_bit') for state in (False, True)]})
    typed_input = re.search(r'^(?:A|The) value input to (?P<subject>\([sdnm][0-9]?\)) is '
                            r'(?:the )?(?P<type>BOOL|INT|DINT|LINT|UINT|UDINT|ULINT|WORD|DWORD|LWORD|REAL|LREAL|STRING|TIME|ANY_[A-Z_]+) '
                            r'type data value', text)
    if typed_input and value.get('ranges'):
        subject = _subjects(typed_input['subject'])[0]
        # STRING's byte range limits its length, not numerical string values.
        rules = [rule for rule in rules if rule['kind'] not in {'allowed_interval', 'value_outside_interval'}]
        rules.append({'kind': 'input_data_type', 'subject': subject, 'data_type': typed_input['type']})
        for interval in value['ranges']:
            interval['subject'] = subject
            rules.append({'kind': 'string_byte_length_interval' if typed_input['type'] == 'STRING' else 'allowed_interval',
                          **interval, 'subject': subject})
        length = re.search(r'within the number of characters of the character string to be input to '
                           r'(?P<right>\([sdnm][0-9]?\))', text)
        if length:
            rules.append({'kind': 'operand_comparison', 'left': subject, 'operator': '<=',
                          'right': {'operation': 'character_count', 'subject': _subjects(length['right'])[0]}})
        if re.search(r'However|provided|must|when|if|except|unless', text, re.I) and not length:
            value['unparsed_input_qualification'] = True
    return rules


def _technical_value(text):
    subjects = _subjects(text)
    formulas = _formula_qualifications(text)
    for formula in formulas:
        for token in formula['tokens']:
            if token.get('operand') and token not in subjects:
                subjects.append(token)
    references = [int(page) for page in re.findall(r'\bPage\s+(\d+)\b', text)]
    numeric_text = re.sub(r'\bPage\s+\d+\b|\*\d+', ' ', text)
    value = {'subjects': subjects,
             'predicates': [name for pattern, name in PREDICATES if re.search(pattern, text, re.I)],
             **_numeric_qualifications(numeric_text)}
    if references:
        value['referenced_pages'] = list(dict.fromkeys(references))
    if formulas:
        value['source_formulas'] = formulas
    value['device_addresses'] = list(dict.fromkeys(DEVICE.findall(text)))
    device_ranges = []
    for match in re.finditer(r'\b(?P<family>SD|SM|SB|SW|ST|LC|[XYMSTCDRBF])(?P<start>\d+)\s+to\s+'
                             r'(?P<other>SD|SM|SB|SW|ST|LC|[XYMSTCDRBF])?(?P<end>\d+)\b', text):
        if not match['other'] or match['other'] == match['family']:
            device_ranges.append({'first': match['family'] + match['start'],
                                  'last': match['family'] + match['end'], 'numbering': 'not_inferred'})
    if device_ranges:
        value['device_ranges'] = device_ranges
    enum_entries = []
    for match in re.finditer(r'(?<![A-Za-z0-9])(?P<literal>[0-9A-F]+H|[01])\s*:\s*'
                             r'(?P<body>[^•\[|]+)', text):
        body = match['body']
        entry = {'literal': _number(match['literal'])}
        width = re.search(r'(16|32|64)-bit', body)
        if width:
            entry['bit_width'] = int(width[1])
        for phrase, key, semantic in ((r'CSV', 'format', 'csv'), (r'binary', 'format', 'binary'),
                                       (r'Decimal', 'radix', 10), (r'Hexadecimal', 'radix', 16),
                                       (r'ASCII', 'encoding', 'ascii'), (r'Unicode', 'encoding', 'unicode'),
                                       (r'unsigned', 'signedness', 'unsigned'),
                                       (r'(?<!un)signed', 'signedness', 'signed'),
                                       (r'single-precision', 'representation', 'single_precision_real'),
                                       (r'Completed successfully', 'completion', 'normal'),
                                       (r'Folder', 'target', 'folder'), (r'File\b', 'target', 'file'),
                                       (r'end of the file', 'position', 'file_end'),
                                       (r'beginning of the file', 'position', 'file_beginning')):
            if re.search(phrase, body, re.I):
                entry[key] = semantic
        if len(entry) > 1:
            enum_entries.append(entry)
    if enum_entries:
        value['declared_value_meanings'] = enum_entries
    selectors = []
    for match in re.finditer(r'When ["\u201c](?P<literal>[0-9A-F]+H):[^"\u201d]+["\u201d] '
                             r'is specified by (?P<subject>\([sdnm][0-9]?\)(?:\+\d+)?)', text):
        selectors.append({'subject': _subjects(match['subject'])[0], 'operator': '=', **_number(match['literal'])})
    if selectors:
        value['source_selector_conditions'] = selectors
    value['cpu_models'] = list(dict.fromkeys(CPU.findall(text)))
    value['hardware_modules'] = list(dict.fromkeys(re.findall(r'\bFX5-[A-Z0-9][A-Z0-9/\-]*', text)))
    serials = re.findall(r'\b\d[0-9A-Z]*X\*{2,}\b|\b\d[0-9A-Z]*X\*{2,}', text)
    if serials:
        value['hardware_serial_patterns'] = list(dict.fromkeys(serials))
    value['instruction_names'] = list(dict.fromkeys(match[0].rsplit(' ', 1)[0]
                                                   for match in INSTRUCTION.finditer(text)))
    value['unresolved_glyphs'] = list(dict.fromkeys(match['code'] for match in GLYPH.finditer(text)
                                                 if match['code'] not in {'F0FB', 'F0C0', 'F0C1'}))
    states = []
    for match in re.finditer(r'\b(?P<device>(?:SM|SD|M|D)\d+)\s+is\s+(?P<state>ON|OFF|on|off)\b', text):
        states.append({'device': match['device'], 'state': match['state'].lower()})
    if states:
        value['device_state_conditions'] = states
    device_forms = []
    for match in re.finditer(r'\((?P<operand>[sdnm][0-9]?)\) is specified as a (?P<form>bit|word) device', text, re.I):
        device_forms.append({'operand': match['operand'], 'device_class': match['form'].lower()})
    if device_forms:
        value['operand_device_conditions'] = device_forms
    forms = [match[1] for match in re.finditer(r'When the ([A-Z][A-Z0-9_.]*(?:\(P\))?(?:_U)?) instructions? (?:is|are) used', text)]
    if forms:
        value['form_conditions'] = forms
    if re.search(r'when (?:a )?signed data is specified|Signed operation:', text, re.I):
        value['signedness_condition'] = 'signed'
    elif re.search(r'when (?:an? )?unsigned data is specified|Unsigned operation:', text, re.I):
        value['signedness_condition'] = 'unsigned'
    if re.search(r'Does not apply when same device|same device number can be specified', text, re.I):
        value['same_head_exception'] = True
    versions = re.findall(r'(?:version|Ver\.)\s*["\u201c]?(\d+\.\d+[A-Z]?)', text, re.I)
    if versions:
        value['versions'] = list(dict.fromkeys(versions))
        value['version_relation'] = 'minimum' if re.search(r'and later|or later', text, re.I) else 'not_resolved'
    aliases = re.search(r'\b(?P<from>Set value|U/H|[A-Za-z][A-Za-z0-9]*) displays as (?P<to>[A-Za-z][A-Za-z0-9]*)', text)
    if aliases:
        value['language_display_alias'] = {'source': aliases['from'].strip().replace(' ', '_'), 'displayed': aliases['to']}
    quantifier = re.search(r'\b(any one|either|both|each|all)\b', text, re.I)
    if quantifier:
        value['subject_quantifier'] = {'any one': 'any', 'either': 'any', 'both': 'all'}.get(
            quantifier[1].lower(), quantifier[1].lower())
    value['rules'] = _literal_rules(text, value)
    value['technical_relation_resolved'] = bool(value['rules'])
    return {key: item for key, item in value.items() if item not in ([], None)}


def _clauses(text):
    # Parenthesized exceptions qualify the preceding cause. They cannot become
    # a separately asserted error trigger.
    for abbreviation in ('e.g.', 'i.e.', 'etc.'):
        text = text.replace(abbreviation, abbreviation.replace('.', '<dot>'))
    text = re.sub(r'(?<=[.!?])(\*\d+)\s+(?=[A-Z])', r'\1 • ', text)
    text = re.sub(r'\b\d+\.\s+(?=Function (?:with|without) EN/ENO\b)', '', text)
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z]|\([sdnm][0-9]?\)|\(SM\d|b\d+\b)|'
                     r'(?<=\.\))\s+(?=[A-Z])|\s*\u2022\s*', text)
    output = []
    for part in parts:
        part = part.strip().replace('<dot>', '.')
        if not part:
            continue
        if output and (part.startswith('(Does not apply') or part.startswith('except ') or
                       (re.search(r'following range\.?$', output[-1]) and re.match(r'^0,\s*2-126', part))):
            output[-1] += ' ' + part
        else:
            output.append(part)
    return output


def _languages(text):
    named = []
    if re.search(r'ST language|structured text', text, re.I):
        named.append('st')
    if re.search(r'FBD/LD language', text, re.I):
        named.append('fbd')
    if re.search(r'ladder (?:diagram|language)', text, re.I):
        named.append('ladder')
    return named or list(LANGUAGES)


def _source_languages(section, page, clause):
    selected = _languages(clause)
    if section.get('outline', '').startswith('PART 5 '):
        available = {'st', 'fbd'}
        if any('Ladder diagram' in layout for _page, layout in section.get('page_layouts') or ()):
            available.add('ladder')
        # The canonical signature review binds "Not supported" to the FBD
        # column on these three original pages. Their Ladder function blocks
        # and ST calls are printed. Text reading order alone cannot bind it.
        if set(section.get('pages') or ()).intersection({1287, 1288, 1289}):
            available.discard('fbd')
        return [language for language in selected if language in available]
    if selected != list(LANGUAGES):
        return selected
    for source_page, layout in reversed(section.get('page_layouts') or []):
        if source_page > page or not re.search(r'Structured text|Ladder\s*\|\s*ST', layout):
            continue
        if re.search(r'(?:Structured text(?:\*\d+)?|\|\s*ST(?:\*\d+)?)\s*\n\s*Not supported', layout):
            selected.remove('st')
        if re.search(r'FBD/LD(?:\*\d+)?\s*\n\s*Not supported', layout):
            selected.remove('fbd')
        break
    return selected


def _literal_forms(form, section=None):
    templates = [form]
    if '[GLYPH-F06F]' in form and section and re.search(
            r'EQ is =, NE is <>, GT is >, LE is <=, LT is <, and GE is >=', section.get('text', '')):
        # The literal comparison alias declaration defines this placeholder;
        # other square glyphs remain uninterpreted.
        suffixes = ('=', '<>', '>', '<=', '<', '>=', '_EQ', '_NE', '_GT', '_LE', '_LT', '_GE')
        templates = [form.replace('[GLYPH-F06F]', suffix) for suffix in suffixes]
    return list(dict.fromkeys(variant for template in templates for variant in (
        [template.replace('(P)', ''), template.replace('(P)', 'P')] if '(P)' in template else [template])))


def _content_role(text):
    """Distinguish technical predicates from captions and worked examples."""
    if re.search(r'^(?:For example\b|In this example\b|The following figure shows an example\b)', text, re.I):
        return 'worked_example'
    if re.fullmatch(r'(?:\(\d+\)\s*)*"/[^"\n]+"(?:\s*\(\d+\))*', text):
        return 'worked_example'
    if text in {'range', 'Device Description | Setting range Set by'}:
        return 'source_navigation'
    if re.fullmatch(r'(?:Function (?:with|without) EN/ENO\s*)?(?:The following table lists (?:the )?(?:operation results|'
                    r'execution conditions and operation results)\.?|The table below shows the related devices\.?|'
                    r'The following figure shows the operation at completion of .+\.?|'
                    r'Root folder|Source file|Destination folder|Copy destination folder|'
                    r'File name of the copy source|Folder name of the copy source)', text, re.I):
        return 'caption_or_diagram_label'
    if re.fullmatch(r'Function (?:with|without) EN/ENO\.?', text):
        return 'caption_or_diagram_label'
    if re.fullmatch(r'Below is shown the timing chart \(flag updating timing\) from the execution of the '
                    r'SP\s*\.\s*[A-Z]+ instruction to the completion\.', text):
        return 'caption_or_diagram_label'
    if text == 'Operation is as follows when the operation result up to the OUT instruction changes from ON to OFF.':
        return 'table_context_caption'
    if re.fullmatch(r'(?:[A-Z][A-Z0-9_]*(?: instruction)?|\s|\|)+', text) and ' instruction' in text:
        return 'timing_diagram_label'
    if re.fullmatch(r'(?:OR|ORI|LD|LDI|AND|ANI) instruction \((?:parallel|serial) connection of (?:NC|NO) contacts\)', text):
        return 'timing_diagram_label'
    diagram_tokens = r'(?:[A-Z][A-Z0-9_]*\b|\([sdnm][0-9]?\)|\(\d+\)|\d+\b|[\s|.,·∙…])+'
    if re.fullmatch(diagram_tokens, text):
        return 'diagram_label'
    if re.fullmatch(r'When \([sdnm][0-9]?\)\s*=\s*\d+\s+' + diagram_tokens, text):
        return 'worked_example'
    illustrated = re.fullmatch(r'When \([sdnm][0-9]?\)\s*=\s*K?\d+(?P<grid>.*)', text)
    diagram = illustrated['grid'].strip() if illustrated else text
    if not re.search(r'\b(?:is|are|was|were|turns?|turned|becomes?|set|sets|must|only|changes?|'
                     r'retains?|remains?|shifts?|rotates?|counts?|stores?|exceeds?)\b|<=|>=', diagram, re.I):
        positions = re.findall(r'\b(?:SM|SD|[DXYMFSTCR])\d+\b|\bb\d+\b|\([sdnm][0-9]?(?:[+\-][sdnm0-9]+)*\)', diagram)
        if len(positions) >= 3 or illustrated:
            rest = re.sub(r'\b(?:Decimal bit|Carry flag(?: value)?|Zero flag|Borrow flag|Value of b\d+|'
                          r'Rotating (?:n|\d+)-bit data to the (?:left|right)|BIN|HEX|DEC|'
                          r'Single-precision real number)\b', '', diagram, flags=re.I)
            rest = re.sub(r'\b(?:SM|SD|[DXYMFSTCR])\d+\b|\bb\d+\b|\b[sdnm][0-9]?\b|'
                          r'\b[A-Z][A-Z0-9_]*\b|(?<![A-Za-z])\d+[A-F0-9]*H?\b', '', rest)
            if not re.sub(r'[\s|(),.:+\-*/·∙…<>]+', '', rest):
                return 'worked_example' if illustrated else 'diagram_label'
    if re.fullmatch(r'EN\s*\|\s*Execution condition\s*\|.*BOOL\s+ENO\s*\|\s*Execution result\s*\|.*BOOL', text):
        return 'parameter_table_label'
    return 'technical_condition_candidate'


def _control_rules(block, value):
    """Fully bind simple control fields; retain conditional columns separately."""
    subjects = block.get('bound_subjects') or []
    if len(subjects) != 1:
        return False
    text = block['text']
    subject = subjects[0]
    if value.get('unresolved_glyphs') == ['F0C9'] and not value.get('source_formulas'):
        # This registered manual's non-value table cell, scoped to this
        # control-data row. It is not an unidentified comparison operator.
        value.pop('unresolved_glyphs')
        value['non_value_cells'] = ['source_dash_marker']
    if block.get('set_by'):
        value['set_by'] = block['set_by']
    if re.search(r'Completion\s*(?:\|\s*)?status.*(?:completion status|status at the completion|instruction completion status)'
                 r'.*stored.*0+H: Completed successfully', text, re.I):
        value['rules'] = [{'kind': 'control_completion_status', 'subject': subject, 'normal': 0,
                           'abnormal': 'nonzero_error_code', 'update_event': 'instruction_completion', 'set_by': 'system'}]
        value['technical_relation_resolved'] = True
        return not value.get('unresolved_glyphs') or set(value['unresolved_glyphs']) <= {'F0C9'}
    if re.search(r'Completion status.*0: Normal.*Other than 0: Error', text, re.I):
        value['rules'] = [{'kind': 'control_completion_status', 'subject': subject, 'normal': 0,
                           'abnormal': 'nonzero_error_code', 'update_event': 'instruction_completion', 'set_by': 'system'}]
        value['technical_relation_resolved'] = True
        return not value.get('unresolved_glyphs') or set(value['unresolved_glyphs']) <= {'F0C9'}
    if re.search(r'Not used|Use prohibited|System area', text, re.I) and not re.search(r'When|If|except|depending', text, re.I):
        value['rules'] = [{'kind': 'reserved_control_field', 'subject': subject,
                           'set_by': block.get('set_by') or 'source_not_declared'}]
        value['technical_relation_resolved'] = True
        return not value.get('unresolved_glyphs') or set(value['unresolved_glyphs']) <= {'F0C9'}
    conditional = re.search(r'\b(?:When|If|depending|except|var(?:y|ies)|Other than)\b', text, re.I)
    if conditional or value.get('source_formulas') or value.get('unresolved_glyphs'):
        return False
    if value.get('ranges'):
        # Each literal row range is qualified by that row's storage operand.
        value['rules'] = [{'kind': 'allowed_interval', **interval, 'subject': subject}
                          for interval in value['ranges']]
        value['technical_relation_resolved'] = True
        return True
    fixed = re.search(r'(?P<number>[0-9A-F]+H|\d+)\s*\(fixed\)', text, re.I)
    if fixed:
        value['rules'] = [{'kind': 'required_value', 'subject': subject, **_number(fixed['number'])}]
        value['technical_relation_resolved'] = True
        return True
    return False


def _reviewed_math(section, block, kind, clause, value, declared):
    """Interpret the exact mathematical witnesses checked on original pages.

    F0A3 is not globally replaced: applicability tables and unrelated fonts have
    different glyph semantics. The operand comparisons and binary32 domain here
    were visually read on AB PDF175/177/179/181/408 and PDF331 respectively.
    """
    page = block['page']
    if kind == 'footnote' and page in {175, 177, 179, 181, 408} and clause.startswith('Set so that '):
        comparisons = re.findall(r'\b(n[123])\[GLYPH-F0A3\](n[123])\b', clause)
        if comparisons and not re.search(r'\[GLYPH-(?!F0A3)[0-9A-F]+\]', clause):
            value['subjects'] = list(dict.fromkeys(name for pair in comparisons for name in pair))
            value['subjects'] = [{'operand': name} for name in value['subjects']]
            value['rules'] = [{'kind': 'operand_comparison', 'left': {'operand': left}, 'operator': '<=',
                               'right': {'operand': right}} for left, right in comparisons]
            value['technical_relation_resolved'] = True
            value.pop('unresolved_glyphs', None)
            value['notation_review_basis'] = 'original_PDF_visual_review'
            value['notation_review_page'] = page
    if kind == 'error' and page == 331 and block['codes'] == ['3402H'] and 's' in declared and re.search(
            r'0, 2-126\[GLYPH-F0A3\]\|specified value \(stored value\)\|<2128', clause) and re.search(
            r'single-precision real number.*?specified by \(s\)', section.get('text', ''), re.I):
        normal = {'absolute_minimum': {'base': 2, 'exponent': -126, 'inclusive': True},
                  'absolute_maximum': {'base': 2, 'exponent': 128, 'inclusive': False}}
        domain = {'positive_zero': True, 'finite_normal': normal}
        value['subjects'] = [{'operand': 's'}]
        value['binding_basis'] = 'printed_real_source_operand_in_conversion_definition'
        value['rules'] = [{'kind': 'invalid_binary32_input_domain', 'subject': {'operand': 's'},
                           'valid_domain': domain,
                           'rejected_classes': ['negative_zero', 'subnormal', 'nan', 'positive_infinity', 'negative_infinity']}]
        value['numeric_values'] = [{'value': 0, 'encoding': 'decimal'}]
        value.pop('ranges', None)
        value.pop('comparisons', None)
        value.pop('unresolved_glyphs', None)
        value['technical_relation_resolved'] = True
        value['notation_review_basis'] = 'original_PDF_visual_review'
        value['notation_review_page'] = page


def _high_speed_function_members(text):
    names = {'High-speed counter': 'high_speed_counter',
             'Pulse density/rotation speed measurement': 'pulse_density_rotation_speed_measurement',
             'Multi-output high-speed comparison table': 'multi_output_high_speed_comparison_table',
             'Pulse width measurement': 'pulse_width_measurement', 'PWM': 'pwm'}
    members = []
    for row in text.split('•'):
        match = re.fullmatch(r'\s*K(?P<value>\d+)(?P<notes>(?:\*\d+)*)\s*\|\s*(?P<name>.+?)\s*', row)
        if not match:
            return None
        entry = {'value': int(match['value']),
                 'footnotes': [int(number) for number in re.findall(r'\*(\d+)', match['notes'])]}
        name = match['name']
        if name in names:
            entry['function'] = names[name]
        elif name == 'High-speed comparison table (CPU module)' and entry['value'] == 20:
            entry.update({'function': 'high_speed_comparison_table', 'hardware': 'cpu_module'})
        else:
            module = re.fullmatch(r'High-speed comparison table \((first|second|third|fourth) high-speed pulse input/output module\)', name)
            if not module:
                return None
            ordinal = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4}[module[1]]
            if entry['value'] != 20 + ordinal:
                return None
            entry.update({'function': 'high_speed_comparison_table', 'hardware': 'high_speed_pulse_input_output_module',
                          'module_ordinal': ordinal})
        members.append(entry)
    return members if {entry['value'] for entry in members} == {0, 10, 20, 21, 22, 23, 24, 30, 40, 50} else None


def _review_high_speed_control(kind, clause, *, section, block, value):
    tag = block.get('control_table_kind')

    def complete(rules, dimension, **updates):
        return {'rules': rules, 'dimension': dimension, 'full_clause_review': True,
                'value_updates': {'control_field_binding_complete': True, 'source_formulas': [],
                                  'unresolved_glyphs': [], **updates}}

    if tag == 'high_speed_function_numbers':
        members = _high_speed_function_members(clause)
        if members:
            subject = block['bound_subjects'][0]
            return complete([{'kind': 'function_selector_domain', 'subject': subject, 'members': members}],
                            'constraints.documented_control_data', subjects=[subject],
                            control_table_kind=tag)
    if kind == 'footnote':
        tables, _skipped = _high_speed_control_blocks(section)
        enums = [item for item in tables if item.get('control_table_kind') == 'high_speed_function_numbers'
                 and item['page'] == block['page']]
        masks = [item for item in tables if item.get('control_table_kind') == 'high_speed_function_mask']
        if len(enums) == 1 and masks:
            members = _high_speed_function_members(enums[0]['text'])
            marked = [entry['value'] for entry in members or () if block['number'] in entry['footnotes']]
            selector, stop = masks[0]['selector_subject'], masks[0]['stop_subject']
            if marked and clause == 'When high-speed counter (function number: K0) is stopped during function operation, the function continues to operate, but nothing will be processed.':
                return complete([{'kind': 'dependent_function_counter_stop', 'selector': selector, 'stop_mask': stop,
                                  'stopped_counter_function_value': 0, 'affected_function_values': marked,
                                  'when': 'during_affected_function_operation',
                                  'affected_function_activity': 'continues', 'affected_function_processing': 'none'}],
                                'effects.documented_function_control', subjects=[selector, stop])
            if marked == [30] and clause == 'When multi-output high-speed comparison table (function number: K30) is stopped, high-speed counter of the same channel is also stopped.':
                return complete([{'kind': 'coupled_function_stop', 'selector': selector, 'stop_mask': stop,
                                  'when_selector_value': 30, 'also_stopped_function_value': 0,
                                  'channel_relation': 'same_channel_as_multi_output_comparison_table'}],
                                'effects.documented_function_control', subjects=[selector, stop])
    if tag != 'high_speed_function_mask':
        return None
    text = clause.replace('high- speed', 'high-speed')
    split = re.split(r'(?:Low-order bit position|Bit position) ', text, maxsplit=1)
    if len(split) != 2:
        return None
    prose, grid = split
    if not re.fullmatch(r'(?:b\d+\b|CH\d+\b|\d+\b|\[GLYPH-F0C9\]|High-order bit position|Valid|[\s|])+', grid):
        return None
    bits = [int(number) for number in re.findall(r'\bb(\d+)\b', grid)]
    if len(set(bits)) != len(bits):
        return None
    selector, start, stop = (block[name] for name in ('selector_subject', 'start_subject', 'stop_subject'))
    values = block['function_values']
    shared = {'selector': selector, 'when_function_values': values, 'start_mask': start, 'stop_mask': stop,
              'source_table_bit_indices': bits, 'unmapped_bits_disposition': 'source_not_stated'}
    rules = []
    channels = [int(number) for number in re.findall(r'\bCH(\d+)\b', grid)]
    channel_case = None
    if values == [0] and prose == ('The counting start and stop of a high-speed counter can be controlled for each channel of high-speed counter. '
                                  'CH1 to CH8 are for the CPU module, and CH9 to CH16 are for the high-speed pulse input/output module. '):
        channel_case = (16, 8)
    elif values == [10] and prose == ('The measuring start and stop of the pulse density (rotation speed measurement) can be controlled for each '
                                     'channel of the high-speed counter. '):
        channel_case = (8, 8)
    elif values == [40, 50] and prose == ('The measuring start and stop of pulse width measurement and PWM can be controlled for each channel. '
                                         'CH1 to CH4 are for the CPU module, and CH5 to CH12 are for the high-speed pulse input/output module. '):
        channel_case = (12, 4)
    if channel_case and bits == list(range(15, -1, -1)) and channels == list(range(channel_case[0], 0, -1)):
        maximum, cpu_maximum = channel_case
        rules = [{'kind': 'function_channel_bitmap', **shared,
                  'channels': [{'bit_index': channel - 1, 'channel': channel,
                                'hardware': 'cpu_module' if channel <= cpu_maximum else 'high_speed_pulse_input_output_module'}
                               for channel in range(1, maximum + 1)]}]
    if values == [20, 21, 22, 23, 24]:
        header = re.fullmatch(r'Set the value to turn on the bit of the high-speed comparison table number which is to be started or stopped\. '
                              r'In the case of the CPU module \(K20\), b0 to b(?P<cpu>15|31) can be used\. '
                              r'In the case of the high-speed pulse input/output module \(K21 to K24\), b0 to b14 can be used\. ', prose)
        if header:
            maximum = int(header['cpu'])
            numbers = [int(number) for number in re.findall(r'(?<![A-Za-z0-9])\d+\b', grid)]
            if set(bits) == set(range(maximum + 1)) and len(numbers) == len(bits) and all(number == bit + 1 for bit, number in zip(bits, numbers)):
                rules = [{'kind': 'function_comparison_table_bitmap', **shared,
                          'branches': [{'when_function_values': [20], 'hardware': 'cpu_module',
                                        'minimum_bit': 0, 'maximum_bit': maximum, 'table_number': {'bit_index_plus': 1}},
                                       {'when_function_values': [21, 22, 23, 24], 'hardware': 'high_speed_pulse_input_output_module',
                                        'minimum_bit': 0, 'maximum_bit': 14, 'table_number': {'bit_index_plus': 1}}]}]
    if values == [30]:
        command = re.fullmatch(r'For the multi-output high-speed comparison table, specification of a channel is not required\. '
                               r'To start the multi-output high-speed comparison table, set 01H in \(s2\)\. '
                               r'To stop it, set 01H in \(s3\)\. ', prose)
        if command and bits == list(range(15, -1, -1)) and 'Valid' in grid:
            rules = [{'kind': 'function_start_stop_trigger', **shared, 'channel_specification_required': False,
                      'start_trigger_value': 1, 'stop_trigger_value': 1}]
    if rules:
        return complete(rules, 'parameters.control_function_bitmap', subjects=[selector, start, stop],
                        control_table_kind=tag, control_function_selector_reference=True)
    return None


def _review_completion_parameter(clause, *, block, value):
    """The parameter cell describes notification storage, not an input gate."""
    subjects = block.get('bound_subjects') or []
    if len(subjects) != 1 or subjects[0].get('offset') is not None:
        return None
    primary = subjects[0]
    text = re.sub(r'\|\s*Bit\s*\|', '|', clause)
    text = re.sub(r'\[GLYPH-F0C9\]|\bANYBIT_ARRAY\b|\(Number of elements: 2\)|\|', ' ', text)
    text = _flat(text)
    name = re.escape(primary['operand'])
    bare = re.fullmatch(r'\(' + name + r'\): Completion device, \(' + name + r'\)\+1: Error completion device', text)
    prefixes = (r'Head device number which turns on when the execution of the instruction is completed '
                r'and remains on for 1 scan\.|'
                r'Head device number which turns on when the instruction is completed\.?|'
                r'Device that turns on for one scan upon completion of the instruction\.?|'
                r'Own station device to be turned on for one scan when the instruction completes\.|'
                r'Device of the own station, which turns on for one scan upon completion of the instruction\.|'
                r'Device which turns on when the execution of the instruction is completed and remains on for 1 scan\.|'
                r'Bit device that turns on upon completion of the processing')
    tail = (r'(?:When the instruction completes with an error, \(' + name + r'\)\+1 also turns on\.|'
            r'If the instruction is completed with an error, \(' + name + r'\)\+1 is also turned on\.|'
            r'When the reading is completed abnormally, \(' + name + r'\)\+1 turns on\.|'
            r'\(In the case of an error completion, the device specified by \(' + name + r'\)\+1 also turns on\.\))')
    described = re.fullmatch(r'(?:' + prefixes + r')\s+' + tail, text, re.I)
    if not (bare or described):
        return None
    complete_flag = {'subject': primary, 'role': 'instruction_completion_notification'}
    error_flag = {'subject': {'operand': primary['operand'], 'offset': 1}, 'role': 'error_completion_notification'}
    if described:
        complete_flag['turn_on_event'] = 'instruction_completion'
        error_flag['turn_on_event'] = 'instruction_error_completion'
        if re.search(r'(?:one|1) scan', text, re.I):
            complete_flag['on_duration_scans'] = 1
        # The row does not give a separate OFF event or duration for +1.
        # The execution-timing paragraph, when present, supplies that fact.
    return {'rules': [{'kind': 'completion_notification_storage', 'elements': 2,
                       'completion': complete_flag, 'error_completion': error_flag}],
            'dimension': 'parameters.completion_notification', 'full_clause_review': True,
            'value_updates': {'subjects': [primary], 'source_formulas': [], 'numeric_values': [],
                              'ranges': [], 'comparisons': [],
                              'unresolved_glyphs': [glyph for glyph in value.get('unresolved_glyphs', []) if glyph != 'F0C9']}}


def _review_operand_parameter(clause, *, section, block, value, declared):
    """Literal parameter-row domains and named storage fields."""
    primary = block.get('bound_subjects') or []
    if len(primary) != 1 or primary[0]['operand'].casefold() not in declared:
        return None
    subject = primary[0]
    text = _flat(clause.replace('|', ' ').replace('[GLYPH-F0C9]', ' '))
    rules, subjects = None, [subject]
    pointer = re.fullmatch(r'Start (?:number of the word device storing the data|word device number storing and shifting data) '
                           r'16-bit signed binary ANY16 \(The start is a pointer\. The data is stored starting from '
                           r'\((?P<name>[sd])\)\+1\.\)', text)
    if pointer and pointer['name'].casefold() == subject['operand'].casefold():
        rules = [{'kind': 'table_pointer_storage', 'pointer': subject,
                  'data_head': {'operand': subject['operand'], 'offset': 1},
                  'element_type': 'signed_binary_16'}]
    string = re.fullmatch(
        r'(?P<kind>Character string to be transferred \(up to (?P<single>\d+) characters\)|'
        r'Unicode string \(up to (?P<unicode>\d+) characters\) to be converted|'
        r'Shift JIS string \(up to (?P<jis>\d+) characters\) to be converted) or '
        r'(?:Character string ANYSTRING_SINGLE head device number storing a character string|'
        r'Unicode string ANYSTRING_DOUBLE start device containing the Unicode string|'
        r'String ANYSTRING_SINGLE start device containing the shift JIS string)', text)
    if string:
        width = 'unicode' if string['unicode'] else 'shift_jis' if string['jis'] else 'single_byte_character_string'
        rules = [{'kind': 'string_operand_extent', 'subject': subject,
                  'maximum_characters': int(string['single'] or string['unicode'] or string['jis']),
                  'representation': width, 'argument_forms': ['string_value', 'head_device_containing_string']}]
    real_domain = re.search(r'0,\s*2-126(?P<lower><|\[GLYPH-F0A3\])\|\((?P<name>[sd][0-9]?)\)\|'
                            r'(?P<upper><|\[GLYPH-F0A3\])2128', clause)
    if (real_domain and 'ANYREAL_32' in clause and 'Single-precision real' in clause and
            real_domain['name'].casefold() == subject['operand'].casefold() and
            not re.search(r'\b(?:when|if|unless|except|only)\b', clause, re.I)):
        rules = [{'kind': 'documented_real_operand_domain', 'subject': subject, 'representation': 'binary32',
                  'zero': {'value': 0, 'sign_disposition': 'not_stated_in_parameter_row'},
                  'nonzero_absolute_value': {
                      'minimum': {'base': 2, 'exponent': -126, 'inclusive': real_domain['lower'] != '<'},
                      'maximum': {'base': 2, 'exponent': 128, 'inclusive': real_domain['upper'] != '<'}},
                  'boundary_interpretation': 'literal_parameter_row_not_an_IEEE_correction'}]
    start_count = re.fullmatch(
        r'(?:Head device number for storing the number of characters 16-bit signed binary ANY16_ARRAY '
        r'and position of the start character \(Number of elements: 2\) )?'
        r'(?:\((?P<first>s2)\))?\+0: Position of the start character, \((?P<second>s2)\)\+1: Number of characters', text)
    if start_count and subject == {'operand': 's2'}:
        rules = [{'kind': 'named_parameter_storage', 'base': subject, 'number_of_elements': 2,
                  'fields': [{'offset': 0, 'role': 'start_character_position'},
                             {'offset': 1, 'role': 'character_count'}]}]
    relay_order = re.fullmatch(
        r'(?P<end>Smallest|Largest) state relay number of practical state relays in the '
        r'Bit ANY_BOOL automatic mode \(\(d1\) < \(d2\)\)', text)
    if relay_order:
        subjects = [{'operand': 'd1'}, {'operand': 'd2'}]
        rules = [{'kind': 'state_relay_interval_endpoint', 'subject': subject,
                  'endpoint': 'minimum' if relay_order['end'] == 'Smallest' else 'maximum',
                  'program_mode': 'automatic'},
                 {'kind': 'operand_comparison', 'left': {'operand': 'd1'}, 'operator': '<', 'right': {'operand': 'd2'}}]
    if (subject == {'operand': 's2'} and text ==
            'Device number storing the measured value (PV) -32768 to +32767 16-bit signed binary ANY16*1'):
        # PV labels the measured quantity in this row; the callable argument is
        # explicitly s2. A genuine PV formal in another section remains intact.
        rules = [{'kind': 'allowed_interval', 'subject': subject, 'minimum': -32768, 'maximum': 32767,
                  'inclusive': True, 'quantity_role': 'measured_process_value'}]
    ramp = re.fullmatch(r'\(d\)\+0: Current value 16-bit signed binary ANY16_ARRAY '
                        r'\(Number of elements: 2\) \(d\)\+1: Number of scans', text)
    if ramp and subject == {'operand': 'd'}:
        rules = [{'kind': 'named_parameter_storage', 'base': subject, 'number_of_elements': 2,
                  'fields': [{'offset': 0, 'role': 'current_value'}, {'offset': 1, 'role': 'scan_count'}]}]
    if subject == {'operand': 's'} and ('Registers specifying the calling' in text and
            '(Number of elements: 3)' in text and
            all(item in text for item in ['(s)+0: Works as a register for counting.',
                '(s)+1: Sets the station No. to be called.', '(s)+2: Sets the product No. to be called.',
                'Set them in advance', 'using the transfer instruction.'])):
        rules = [{'kind': 'named_parameter_storage', 'base': subject, 'number_of_elements': 3,
                  'element_type': 'signed_binary_16',
                  'fields': [{'offset': 0, 'role': 'counting_register'},
                             {'offset': 1, 'role': 'requested_station_number'},
                             {'offset': 2, 'role': 'requested_product_number'}],
                  'initialization': {'method': 'transfer_instruction', 'phase': 'before_call'}}]
    if subject == {'operand': 'd'} and ('Registers (bit devices) specifying' in text and
            '(Number of elements: 8)' in text and 'Construct' in text and
            'an internal contact circuit in' in text and 'input signal (X).' in text):
        labels = ['A phase signal', 'B phase signal', 'Zero point detection signal',
                  'Forward rotation at high-speed', 'Forward rotation at low-speed', 'Stop',
                  'Backward rotation at low-speed', 'Backward rotation at high-speed']
        if all(('(d)' + ('+' + str(index) if index else '') + ': ' + label) in text
               for index, label in enumerate(labels)):
            roles = ['a_phase', 'b_phase', 'zero_point_detection', 'high_speed_forward_rotation',
                     'low_speed_forward_rotation', 'stop', 'low_speed_reverse_rotation', 'high_speed_reverse_rotation']
            rules = [{'kind': 'named_parameter_storage', 'base': subject, 'number_of_elements': 8,
                      'element_type': 'bit', 'fields': [{'offset': index, 'role': role} for index, role in enumerate(roles)],
                      'initialization': {'method': 'internal_contact_circuit', 'driven_by': 'input_signal_X',
                                         'phase': 'before_call'}}]
    if rules is None:
        return None
    if any(item['operand'].casefold() not in declared for item in subjects):
        return None
    return {'rules': rules, 'dimension': ('parameters.documented_domain' if real_domain else
                                         'parameters.documented_parameter_structure'), 'full_clause_review': True,
            'value_updates': {'subjects': subjects, 'source_formulas': [], 'numeric_values': [],
                              'ranges': [], 'comparisons': [], 'unresolved_glyphs': [],
                              'operand_binding_required': True}}


def _review_basic_error(clause, *, section, block, declared):
    rules, subjects, phase = None, [], 'call_or_execution_condition'
    def refs(text):
        return _subjects(text)
    outside = re.fullmatch(r'The value set in (?P<name>\([sdnm][0-9]?\)) is other than the following\. '
                           r'(?P<low>\d+)\[GLYPH-F0A3\](?P=name)\[GLYPH-F0A3\](?P<high>\d+)'
                           r'(?: In (?P<negative>\([sdnm][0-9]?\)), a negative value is specified\.)?', clause)
    if outside:
        subject = refs(outside['name'])[0]
        alternatives = [{'kind': 'value_outside_interval', 'subject': subject,
                         'minimum': int(outside['low']), 'maximum': int(outside['high']), 'inclusive': True}]
        subjects = [subject]
        if outside['negative']:
            negative = refs(outside['negative'])[0]
            subjects.append(negative)
            alternatives.append({'kind': 'value_comparison', 'subject': negative, 'operator': '<', 'value': 0})
        rules = [{'kind': 'any_error_condition', 'alternatives': alternatives}]
    negative = re.fullmatch(r'The value of (?P<subject>\([sdnm][0-9]?\)) is negative\.(?:\s*\|\s*\d+)?', clause)
    if negative:
        subjects = refs(negative['subject'])
        rules = [{'kind': 'value_comparison', 'subject': subjects[0], 'operator': '<', 'value': 0}]
    compare = re.fullmatch(r'(?P<left>\([sdnm][0-9]?\)) is (?P<op>larger|smaller) than '
                           r'(?P<right>\([sdnm][0-9]?\)|0)(?P<minus>-1)?\.', clause)
    if compare:
        subjects = refs(compare['left'] + compare['right'])
        right = refs(compare['right'])[0] if compare['right'] != '0' else 0
        if compare['minus']:
            right = {'operation': 'subtract', 'left': right, 'right': 1}
        rules = [{'kind': 'value_comparison', 'subject': subjects[0],
                  'operator': '>' if compare['op'] == 'larger' else '<', 'right': right}]
    if clause == '(n) is other than 1 or 2.':
        subjects = [{'operand': 'n'}]
        rules = [{'kind': 'value_outside_set', 'subject': subjects[0], 'allowed_values': [1, 2]}]
    if clause == 'The value in (s) is other than the interrupt disabled (0) or interrupt enabled (1).':
        subjects = [{'operand': 's'}]
        rules = [{'kind': 'value_outside_set', 'subject': subjects[0],
                  'allowed_values': [{'value': 0, 'meaning': 'interrupt_disabled'}, {'value': 1, 'meaning': 'interrupt_enabled'}]}]
    if clause == 'Either (s) or (d) is other than 0 to 9999 when SM8168 is OFF.':
        subjects = [{'operand': 's'}, {'operand': 'd'}]
        rules = [{'kind': 'any_operand_outside_interval', 'subjects': subjects, 'minimum': 0, 'maximum': 9999,
                  'when': {'device': 'SM8168', 'state': False}}]
    if clause == '(n2) is larger than (n1) or (n3).':
        subjects = [{'operand': name} for name in ('n2', 'n1', 'n3')]
        rules = [{'kind': 'any_error_condition', 'alternatives': [
            {'kind': 'operand_comparison', 'left': {'operand': 'n2'}, 'operator': '>', 'right': {'operand': name}}
            for name in ('n1', 'n3')]}]
    if clause == 'The number of nibbles of the digit specification of bit device of (s) and (d) is different.':
        subjects = [{'operand': 's'}, {'operand': 'd'}]
        rules = [{'kind': 'operand_component_mismatch', 'left': {'operand': 's', 'component': 'nibble_count'},
                  'right': {'operand': 'd', 'component': 'nibble_count'}, 'operand_form': 'digit_specified_bit'}]
    concurrent = re.fullmatch(r'The number of the (?P<form>SEGL|DSW) instructions which are used simultaneously exceeds four\.', clause)
    if concurrent:
        rules = [{'kind': 'concurrent_instruction_count', 'form': concurrent['form'], 'invalid_operator': '>', 'value': 4}]
    nesting = re.fullmatch(r'Nesting of the DI instruction exceeds (?P<max>\d+) levels\.', clause)
    if nesting:
        rules = [{'kind': 'instruction_nesting_depth', 'family': 'DI', 'maximum_levels': int(nesting['max']), 'invalid_operator': '>'}]
    if clause in {'When the FOR instruction is nested, the 17th level is executed.',
                  'A program is written where the nesting of the FOR to NEXT instructions exceeds 16 levels.'}:
        rules = [{'kind': 'instruction_nesting_depth', 'family': 'FOR_NEXT', 'maximum_levels': 16,
                  'invalid_operator': '>', 'invalid_execution_depth_minimum': 17}]
    if clause == 'The branch pointer number outside two nesting levels or more is specified.':
        rules = [{'kind': 'branch_nesting_boundary', 'invalid_outward_levels_minimum': 2,
                  'pointer_role': 'branch_target'}]
    other_program = re.fullmatch(r'A pointer in other program file is specified in (?P<subject>\(P\))\.', clause)
    if other_program:
        subjects = refs(other_program['subject'])
        rules = [{'kind': 'pointer_program_scope', 'subject': subjects[0], 'invalid_scope': 'different_program_file'}]
    interrupt = re.fullmatch(r'The interrupt pointer number specified by \(I\) exceeds the range of the interrupt pointer number '
                             r'\((?P<low>I\d+) to (?P<high>I\d+)\)\.', clause)
    if interrupt:
        subjects = [{'operand': 'I'}]
        rules = [{'kind': 'pointer_outside_identifier_domain', 'subject': subjects[0],
                  'minimum_identifier': interrupt['low'], 'maximum_identifier': interrupt['high'],
                  'index_encoding': 'source_identifier_notation'}]
    if clause == 'Communication procedure with module is not completed correctly when this instruction is executed.':
        rules = [{'kind': 'module_communication_procedure', 'required_state_at_execution': 'completed_correctly',
                  'invalid_state': 'not_completed_correctly'}]
    if clause == 'Write protect of the SD memory card is enabled.':
        rules = [{'kind': 'resource_state_error', 'resource': 'SD_memory_card', 'state': 'write_protect_enabled'}]
    if clause == 'Initialization to the SD memory card did not complete normally.':
        rules = [{'kind': 'resource_state_error', 'resource': 'SD_memory_card', 'state': 'initialization_not_completed_normally'}]
    if clause == 'A storage capacity in the SD memory card for the extended file register "EXFILER.ERD" is insufficient.':
        rules = [{'kind': 'resource_capacity_error', 'resource': 'SD_memory_card',
                  'allocation': 'extended_file_register', 'file': 'EXFILER.ERD', 'capacity': 'insufficient'}]
    codec = re.fullmatch(r'The range of data in the device specified by (?P<subject>\(s\)) includes a character code that cannot be converted\.', clause)
    if codec:
        subjects = refs(codec['subject'])
        rules = [{'kind': 'string_encoding_conversion_error', 'subject': subjects[0],
                  'invalid_condition': 'contains_code_not_convertible_to_declared_destination_encoding'}]
    if clause == 'Byte order mark FEFFH (big endian) is added to the character string in the device specified by (s).':
        subjects = [{'operand': 's'}]
        rules = [{'kind': 'string_byte_order_mark_error', 'subject': subjects[0], 'source_code': 0xFEFF,
                  'source_documented_byte_order': 'big_endian'}]
    if clause == 'The result is outside the following range. 0 to 32767':
        rules = [{'kind': 'operation_result_outside_interval', 'subject': {'role': 'operation_result'}, 'minimum': 0, 'maximum': 32767}]
        phase = 'result'
    aliases = _argument_aliases(section)
    limit_order = re.fullmatch(r'The lower limit value specified by (?P<low>\(s1\)) is greater than '
                               r'the upper limit value specified by (?P<high>\(s[23]\))\.', clause)
    if limit_order:
        subjects = refs(limit_order['low'] + limit_order['high'])
        upper = [item for item in aliases.values() if item.get('formal_name') in {'MX', 'MAX'}]
        if len(upper) == 1 and upper[0]['source_name'] != subjects[1]['operand']:
            actual = {'operand': upper[0]['source_name']}
            return {'rules': [{'kind': 'source_conflict', 'conflict': 'upper_limit_operand_disagrees_with_formal_parameter',
                               'source_claims': [{'source_region': 'error_table', 'lower': subjects[0], 'upper': subjects[1], 'operator': '>'},
                                                {'source_region': 'argument_table', 'upper': actual,
                                                 'formal_name': upper[0]['formal_name']}], 'resolution': 'not_established'}],
                    'full_clause_review': True, 'status': 'conflict', 'dimension': 'constraints.documented_restriction',
                    'value_updates': {'subjects': [*subjects, actual], 'operand_binding_required': True,
                                      'gap_kind': 'source_conflict', 'source_disposition': 'source_conflict',
                                      'source_formulas': [], 'numeric_values': [], 'unresolved_glyphs': []}}
        rules = [{'kind': 'operand_comparison', 'left': subjects[0], 'operator': '>', 'right': subjects[1]}]
    input_names = [name for name, entry in aliases.items() if entry['direction'] == 'input' and name != 'en']
    if len(input_names) == 1:
        input_subject = {'operand': aliases[input_names[0]]['source_name']}
        if clause in {'"0" is specified.', '-0 is input.'}:
            subjects = [input_subject]
            rules = [{'kind': 'invalid_literal_input', 'subject': input_subject,
                      'value': 0, 'sign': 'negative_zero' if clause.startswith('-0') else 'source_not_stated'}]
        math_domain = re.fullmatch(r'A value input by these functions is other than (?P<low>-?\d+\.\d+) to (?P<high>-?\d+\.\d+)\.', clause)
        if math_domain:
            subjects = [input_subject]
            rules = [{'kind': 'value_outside_interval', 'subject': input_subject,
                      'minimum': float(math_domain['low']), 'maximum': float(math_domain['high']), 'inclusive': True}]
    if rules is None or any(subject['operand'].casefold() not in declared for subject in subjects):
        return None
    return {'rules': rules, 'full_clause_review': True,
            'dimension': 'effects.operation_error' if phase == 'result' else None,
            'value_updates': {'subjects': subjects, 'operand_binding_required': bool(subjects), 'phase': phase,
                              'source_formulas': [], 'numeric_values': [], 'comparisons': [], 'ranges': [], 'unresolved_glyphs': []}}


def _review_string_error(clause, *, section, declared):
    """String area, index and character-count conditions retain their operands."""
    names = r'\([sd][0-9]{0,2}\)(?:(?:,? and |, | to )\([sd][0-9]{0,2}\))*'
    missing = re.fullmatch(r'In the corresponding device range of the device specified by (?P<names>' + names + r') '
                           r'and later, "(?P<code>00H)" does not exist\.', clause)
    missing2 = re.fullmatch(r'"(?P<code>00H)" is not set to (?:a label specified by (?P<labels>' + names + r') or '
                            r'devices from specified device number to the end device number of corresponding device|'
                            r'devices from the device number specified by (?P<names>' + names + r') '
                            r'to the end device number of corresponding device)\.', clause)
    missing3 = re.fullmatch(r'There is no (?P<code>0000H) in the setting area specified by (?P<names>' + names + r') '
                            r'and later in the device/label memory\.', clause)
    rules, subjects, phase = None, [], 'input_or_storage_condition'
    match = missing or missing2 or missing3
    if match:
        source_names = (match.groupdict().get('names') or match.groupdict().get('labels'))
        subjects = _subjects(source_names)
        rule = {'kind': 'string_terminator_missing', 'subjects': subjects, 'terminator_code': 0,
                'code_unit_bits': 16 if match['code'] == '0000H' else 8,
                'search_extent': 'corresponding_device_or_label_setting_area'}
        span = re.fullmatch(r'\(s(?P<first>\d+)\) to \(s(?P<last>\d+)\)', source_names)
        aliases = _argument_aliases(section)
        if span:
            endpoint = aliases.get('s' + span['last']) or {}
            group = {'base_name': 's', 'first_source_index': int(span['first']), 'last_source_index': int(span['last'])}
            if endpoint.get('variadic_group') == 's':
                rule['source_operand_group'] = {**group, 'applies_to': 'supplied_call_arguments'}
            elif aliases and any(subject['operand'].casefold() not in declared for subject in subjects):
                declared_inputs = [{'source_name': entry['source_name'], 'formal_name': entry.get('formal_name')}
                                   for entry in aliases.values() if entry['direction'] == 'input' and entry['source_name'] != 'EN']
                return {'rules': [{'kind': 'source_conflict', 'conflict': 'error_operand_range_exceeds_declared_call_inputs',
                                   'source_claims': [{'source_region': 'error_table', 'terminator_missing': rule,
                                                     'source_operand_range': group},
                                                    {'source_region': 'argument_table', 'inputs': declared_inputs}],
                                   'resolution': 'not_established'}],
                        'status': 'conflict', 'full_clause_review': True, 'dimension': 'constraints.documented_restriction',
                        'value_updates': {'subjects': [{'operand': item['source_name']} for item in declared_inputs],
                                          'operand_binding_required': True, 'source_formulas': [], 'numeric_values': [],
                                          'ranges': [], 'comparisons': [], 'unresolved_glyphs': [],
                                          'gap_kind': 'source_conflict', 'source_disposition': 'source_conflict'}}
        rules = [rule]
    length = re.fullmatch(r'The character strings? specified by (?P<names>' + names + r') '
                          r'(?:has|have) more than (?P<max>\d+) characters\.', clause)
    if length:
        subjects = _subjects(length['names'])
        rules = [{'kind': 'any_string_length_exceeds', 'subjects': subjects, 'maximum_characters': int(length['max'])}]
    index = re.fullmatch(r'The value stored in a device specified in (?P<index>\([sn][0-9]?\)(?:\+\d+)?) '
                         r'exceeds the number of characters of (?P<string>\([sd][0-9]?\))\.', clause)
    if index:
        index_ref, string = _subjects(index['index'])[0], _subjects(index['string'])[0]
        subjects = [index_ref, string]
        rules = [{'kind': 'string_index_exceeds_length', 'index': index_ref, 'string': string}]
    total = re.fullmatch(r'The total of the values stored in devices specified in (?P<first>\([sn][0-9]?\)) '
                         r'and (?P<second>\([sn][0-9]?\)(?:\+\d+)?) exceeds the number of characters of '
                         r'(?P<string>\([sd][0-9]?\))\.', clause)
    if total:
        first, second, string = (_subjects(total[key])[0] for key in ('first', 'second', 'string'))
        subjects = [first, second, string]
        rules = [{'kind': 'string_range_exceeds_length', 'index_or_count_sum': [first, second], 'string': string}]
    negative = re.fullmatch(r'The value stored in a device specified in (?P<subject>\([sn][0-9]?\)(?:\+\d+)?) is '
                            r'(?P<max>-\d+) or lower\.', clause)
    if negative:
        subjects = _subjects(negative['subject'])
        rules = [{'kind': 'value_comparison', 'subject': subjects[0], 'operator': '<=', 'value': int(negative['max'])}]
    unavailable = re.fullmatch(r'(?:The (?:entire Unicode|whole converted character|specified character) string|'
                               r'All converted shift (?:JIS \(ASCII\)|Unicode) strings) cannot be stored '
                               r'(?:in (?:the setting area specified by (?P<unicode>\(d\)) in the device/label memory\. '
                               r'\(The number of required points is insufficient\.\)|'
                               r'a label specified by (?P<label>\(d\)) or devices from (?:the device number|specified device number) '
                               r'to the end device number of corresponding device\.|'
                               r'the devices from the device specified by (?P<device>\(d\)) to the last device of the target device\.)|'
                               r'to the number of points to the last No\. in each setting area of the relevant device/\s*label memory '
                               r'after the device No\. specified in (?P<converted>\(d\))\.)', clause)
    if unavailable:
        destination = next(value for value in unavailable.groupdict().values() if value)
        subjects = _subjects(destination)
        rules = [{'kind': 'string_result_storage_insufficient', 'destination': subjects[0],
                  'required_extent': 'whole_result_string', 'available_extent': 'corresponding_device_or_label_area'}]
        phase = 'result'
    if rules is None or any(subject['operand'].casefold() not in declared for subject in subjects):
        return None
    return {'rules': rules, 'full_clause_review': True,
            'dimension': 'effects.operation_error' if phase == 'result' else None,
            'value_updates': {'subjects': subjects, 'operand_binding_required': bool(subjects), 'phase': phase,
                              'source_formulas': [], 'numeric_values': [], 'comparisons': [], 'ranges': [], 'unresolved_glyphs': []}}


_PWM_HARDWARE_TABLE = 'Output number (s1) ON time (s2) Period CPU module Y0 to Y2 2 [GLYPH-F06D]s more 5 [GLYPH-F06D]s more Y3 to Y7 200 [GLYPH-F06D]s more 400 [GLYPH-F06D]s more Output number (s1) ON time (s2) Period CPU module High-speed pulse input/ output module Y0 to Y3 Y0 to Y2 2 [GLYPH-F06D]s more 5 [GLYPH-F06D]s more Y4 to Y7 [GLYPH-F0C9] 200 [GLYPH-F06D]s more 400 [GLYPH-F06D]s more'

_PWM_DIAGRAM_LABELS = {
    (817, 'ON time setting is 5 μs or less ON time setting is 5 μs or more ON time setting is 5 μs or more ON time setting is 5 μs or more ON time setting is 5 μs or more ON time setting is 5 μs or less 5 μs or less Output stop command is issued within 5 μs of the ON width time. 5 μs or more Output stop command is issued in 5 μs or more of the ON width time.'): 'ON',
    (817, 'Period Period'): 'ON',
    (818, 'OFF time setting OFF time setting is 5 μs or less | is 5 μs or less'): 'OFF',
    (818, 'OFF time setting | OFF time setting is 5 μs or more | is 5 μs or more'): 'OFF',
    (818, 'Output stop command is issued in 5 μs or more of the OFF width time. 5 μs or more Period'): 'OFF',
    (818, 'OFF time setting | OFF time setting is 5 μs or more | is 5 μs or more'): 'OFF',
    (824, 'ON time setting is 5 μs or less ON time setting is 5 μs or more ON time setting is 5 μs or more ON time setting is 5 μs or more ON time setting is 5 μs or more ON time setting is 5 μs or less 5 μs or less Output stop command is issued within 5 μs of the ON width time. 5 μs or more Output stop command is issued in 5 μs or more of the ON width time.'): 'ON',
    (824, 'Period Period'): 'ON',
    (825, 'Operations when the DPWM output is stopped (while the output pulse is off)'): 'OFF',
    (825, 'OFF time setting is 5 μs or more OFF time setting is 5 μs or more OFF time setting is 5 μs or more OFF time setting is 5 μs or more OFF time setting is 5 μs or less OFF time setting is 5 μs or less Output stop command is issued within 5 μs of the OFF width time. 5 μs or less 5 μs or more Output stop command is issued in 5 μ s or more of the OFF width time.'): 'OFF',
    (825, 'Output stop command Period Period Period'): 'OFF',
}


_PWM_MERGED_ERROR_ROWS = {'PWM': 'A channel number that is not selected in the parameter setting are specified for the output destination specified by (d). (The PWM output is not executed.) 3611H(CH1) | The ON time specified by (s1) is larger than the period specified by (s2). 3612H(CH2) | (The PWM output stops.) 3613H(CH3) Values of an SD device for setting pulse width and period of this instruction are incorrect. 3614H(CH4) (The PWM output stops.) 3615H(CH5) 3616H(CH6) | The ON time or period is less than "1". 3617H(CH7) | The SD device specified for the number of output pulses stores a value outside the available range (0 to 2147483647). 3618H(CH8) 3619H(CH9) 361AH(CH10) 361BH(CH11) 361CH(CH12)', 'DPWM': 'A channel number or a Y device that is not selected in the parameter setting are specified for the output destination specified by (d). (The PWM output is not executed.) 3611H(CH1) | The ON time specified by (s1) is larger than the period specified by (s2). 3612H(CH2) | (The PWM output stops.) 3613H(CH3) In (s1) and (s2), a negative value is specified. 3614H(CH4) (The PWM output stops.) 3615H(CH5) 3616H(CH6) | Values of an SD device for setting pulse width and period of this instruction are incorrect. 3617H(CH7) | (The PWM output stops.) 3618H(CH8) | The ON time or period is less than "1". 3619H(CH9) The SD device specified for the number of output pulses stores a value outside the available range (0 to 2147483647). 361AH(CH10) 361BH(CH11) 361CH(CH12)'}

_PWM_STOP_QUALIFICATION_CLAUSES = {'condition_precautions_p817_r4_c0': 'Operations when the PWM output is stopped (while the output pulse is on) Period Output stop command If an output stop command is issued while the ON time setting is 5 μs or less, outputting pulses stops after the set ON time elapses.', 'condition_precautions_p817_r4_c1': 'When the ON time setting is 5μs or more and 5 μs or less time has elapsed when an output stop command is issued, outputting pulses stops in 5 μs from the stop command.', 'condition_precautions_p817_r4_c3': 'When the ON time setting is 5 μs or more and 5 μs or more time has elapsed when an output stop command is issued, outputting pulses stops with the stop command.', 'condition_precautions_p818_r10_c0': 'When the OFF time setting is 5 μs or more and 5 μs or more time has elapsed when an output stop command is issued, outputting pulses stops with the stop command.', 'condition_precautions_p818_r5_c0': 'Operations when the PWM output is stopped (while the output pulse is off) Output stop command Period If an output stop command is issued while the OFF time setting is 5 μs or less, outputting pulses stops after the set OFF time elapses.', 'condition_precautions_p818_r7_c0': 'Output stop command is issued within 5 μs of the OFF width time. 5 μs or less Period When the OFF time setting is 5 μs or more and 5 μs or less time has elapsed when an output stop command is issued, outputting pulses stops in 5 μs from the stop command.', 'condition_precautions_p824_r5_c0': 'Operations when the DPWM output is stopped (while the output pulse is on) Output number (s1) ON time (s2) Period CPU module Y0 to Y2 2 [GLYPH-F06D]s more 5 [GLYPH-F06D]s more Y3 to Y7 200 [GLYPH-F06D]s more 400 [GLYPH-F06D]s more Output number (s1) ON time (s2) Period CPU module High-speed pulse input/ output module Y0 to Y3 Y0 to Y2 2 [GLYPH-F06D]s more 5 [GLYPH-F06D]s more Y4 to Y7 [GLYPH-F0C9] 200 [GLYPH-F06D]s more 400 [GLYPH-F06D]s more Period Output stop command If an output stop command is issued while the ON time setting is 5 μs or less, outputting pulses stops after the set ON time elapses.', 'condition_precautions_p824_r5_c1': 'When the ON time setting is 5μs or more and 5 μs or less time has elapsed when an output stop command is issued, outputting pulses stops in 5 μs from the stop command.', 'condition_precautions_p824_r5_c3': 'When the ON time setting is 5 μs or more and 5 μs or more time has elapsed when an output stop command is issued, outputting pulses stops with the stop command.', 'condition_precautions_p825_r10_c3': 'When the OFF time setting is 5 μs or more and 5 μs or more time has elapsed when an output stop command is issued, outputting pulses stops with the stop command.', 'condition_precautions_p825_r10_c4': 'When the OFF time setting is 5 μs or more and 5 μs or less time has elapsed when an output stop command is issued, outputting pulses stops in 5 μs from the stop command.', 'condition_precautions_p825_r10_c5': 'If an output stop command is issued while the OFF time setting is 5 μs or less, outputting pulses stops after the set OFF time elapses.'}

def _review_pwm_clause(kind, clause, *, section, block, declared):
    """Bind PWM qualifications to the original section, tables and antecedents.

    This is manual knowledge, not a simulator implementation. Stop diagrams
    with overlapping inclusive 5 us boundaries deliberately remain unresolved.
    """
    forms = {'16 bit binary pulse width modulation': ('PWM', 813, 819, 816, 818),
             '32 bit binary pulse width modulation': ('DPWM', 820, 826, 824, 825)}
    scope = forms.get(section.get('section'))
    if scope is None or not section.get('outline', '').startswith('PART 3 CPU MODULE INSTRUCTIONS >'):
        return None
    form, first, error_page, precaution_page, final_page = scope
    if not {'s1', 's2', 'd'}.issubset(declared) or first != section.get('first_page'):
        return None
    page = block['page']
    text = _flat(section.get('text', ''))
    source = _flat(block.get('text', ''))
    models = ['FX5S', 'FX5UJ', 'FX5U', 'FX5UC']
    if not all(model in text for model in models) or not re.search(r'ENO\s*:=\s*' + form + r'\(EN,\s*s1,\s*s2,\s*d\)', text):
        return None

    def reviewed(rules, operands=(), dimension=None, **extra):
        return {'rules': rules, 'full_clause_review': True, 'forms': [form],
                'languages': list(LANGUAGES), 'dimension': dimension,
                'additional_pages': [first],
                'value_updates': {'subjects': [{'operand': name} for name in operands],
                    'operand_binding_required': bool(operands), 'context_binding': 'resolved',
                    'numeric_values': [], 'ranges': [], 'comparisons': [], 'source_formulas': [],
                    'unresolved_glyphs': [], 'unbound_condition_markers': [],
                    'unparsed_input_qualification': False, 'device_state_conditions': [],
                    'operand_device_conditions': [], 'cpu_models': models,
                    'knowledge_kind': 'manual_behavior', 'core_execution_capability': 'not_established',
                    'binding_basis': 'PWM_section_operands_and_local_source_context'}, **extra}

    if kind == 'error' and page == error_page and block.get('domain') == 'operation_error':
        if block.get('codes') == ['3600H'] and clause == _PWM_MERGED_ERROR_ROWS[form]:
            # The source row includes a merged CH1..CH12 error-code column.
            # Never bind its pulse-count interval to s2, negative tests to d,
            # or the <1 clause only to the channel printed beside that line.
            result = reviewed([
                {'kind': 'PWM_channel_configuration', 'when': 'channel_not_selected_for_PWM_in_parameters', 'operation_error': True},
                {'kind': 'operand_relation', 'left': {'operand': 's1'}, 'operator': '>', 'right': {'operand': 's2'}, 'error': True},
                {'kind': 'any_value_less_than', 'subjects': [{'operand': 's1'}, {'operand': 's2'}], 'value': 1, 'error': True,
                 'logical_connective': 'OR', 'zero_example_qualification': 'unresolved'},
                {'kind': 'PWM_runtime_register_validation', 'fields': ['ON_time', 'period'], 'validity': 'invalid', 'error': True},
                {'kind': 'value_outside_interval', 'subject': {'context': 'pwm.pulse_count'}, 'minimum': 0, 'maximum': 2147483647,
                 'inclusive': True, 'error': True}], ['s1', 's2', 'd'])
            result['value_updates'].update(error_codes=['3600H'] + [format(0x3610 + ch, '04X') + 'H' for ch in range(1, 13)],
                error_code_binding='3600H_configuration_and_3611H_to_361CH_correspond_to_selected_channel',
                merged_source_row=True, pulse_count_is_not_operand_s2=True)
            return result
        if clause == ('The output destination specified by (d) is already used by another instruction '
                      '(positioning instruction). (The PWM output is not executed.)') and block.get('codes') == ['1810H']:
            return reviewed([{'kind': 'resource_in_use', 'resource': 'pulse_output_destination',
                'subject': {'operand': 'd'}, 'owner': 'positioning_instruction',
                'effects': {'PWM_output': 'not_executed'}}], ['d'])
        if clause.rstrip('.') == ('A Y device is specified as the output destination specified by (d), '
                                  'and there is no unused channel number in the parameter setting') and block.get('codes') == ['1810H']:
            return reviewed([{'kind': 'conditional_result', 'when': {'all': [
                {'operand': 'd', 'device_class': 'Y'}, {'parameter_unused_channel_available': False}]},
                'result': {'operation_error': True, 'output_effect': 'not_established'}}], ['d'])
        if clause == 'Y10 or later is specified as the output destination specified by (d). (The PWM output stops.)' and block.get('codes') == ['3405H']:
            return reviewed([{'kind': 'conditional_result', 'when': {'operand': 'd', 'device_class': 'Y',
                'minimum_address': 'Y10', 'address_notation': 'octal', 'inclusive': True},
                'result': {'PWM_output': 'stops'}}], ['d'])

    if kind == 'footnote' and page == first and block.get('domain') == 'applicable_devices':
        bit_note = ('Only Y can be used for a bit device. When a bit device is specified, specify one of Y0 to Y7. '
            'The high-speed pulse input/output module bit device (Y) cannot be specified. '
            'If Y is specified, outputs are enabled when there is an unused channel number in the parameter setting '
            'and the specified Y number is not used. The nibble of a bit device cannot be specified.')
        if block.get('number') == 1 and source == bit_note:
            return reviewed([
                {'kind': 'allowed_device_classes', 'subject': {'operand': 'd'}, 'when': {'representation': 'bit_device'},
                 'allowed': ['Y'], 'allowed_addresses': ['Y' + str(n) for n in range(8)], 'hardware': 'CPU_module'},
                {'kind': 'disallowed_device_form', 'subject': {'operand': 'd'}, 'forms': ['digit_specified_bit', 'high_speed_pulse_IO_module_bit_device']},
                {'kind': 'conditional_result', 'when': {'all': [{'operand': 'd', 'device_class': 'Y'},
                 {'parameter_unused_channel_available': True}, {'specified_Y_number_in_use': False}]},
                 'result': {'outputs_enabled': True}}], ['d'])
        channel_note = ('When a word device or constant is specified, specify one of the ' +
            ('channel' if form == 'PWM' else 'CH') + ' numbers. Specify K1 to K4 (axis 1 to 4) for the CPU module, '
            'and K5 to K12 (axis 5 to 12) for the high-speed pulse input/output module.')
        if block.get('number') == 2 and source == channel_note:
            return reviewed([{'kind': 'allowed_interval', 'subject': {'operand': 'd'},
                'when': {'representation_any_of': ['word_device', 'constant'], 'hardware': hardware},
                'quantity': 'channel_number', 'minimum': low, 'maximum': high, 'inclusive': True}
                for hardware, low, high in [('CPU_module', 1, 4), ('high_speed_pulse_IO_module', 5, 12)]], ['d'])

    if (kind == 'footnote' and page == first + 2 and block.get('number') == 3
        and block.get('domain') == 'processing_details' and clause ==
        'When this instruction is executed, the pulse width and period specified (s1) and (s2) are set to an SD device. '
        '• After the pulse output is started from each channel, the pulse output monitor turns on.'):
        # Check both tables, not the following monitor-register table or a guessed stride.
        bindings = [{'channel': ch,
                     'ON_time': {'operand': 's1', 'register_pair': [f'SD{5303+16*(ch-1)}', f'SD{5302+16*(ch-1)}']},
                     'period': {'operand': 's2', 'register_pair': [f'SD{5305+16*(ch-1)}', f'SD{5304+16*(ch-1)}']}}
                    for ch in range(1, 13)]
        layouts = {int(p): _flat(t.replace('|', ' ')) for p, t in section.get('page_layouts', [])}
        on_page, period_page = layouts.get(first + 1, ''), layouts.get(first + 2, '')
        on_table = on_page.partition('ON time R/W')[2].split('Pulse output')[0]
        period_table = period_page.partition('Period R/W')[2].split('*1 When')[0]
        monitor_table = period_page.partition('ON timing OFF timing')[2].split('• This instruction stores')[0]
        def pairs(table):
            return [(int(ch), [upper, lower]) for ch, upper, lower in
                    re.findall(r'CH(\d+) (SD\d+), (SD\d+)', table)]
        table_bound = (pairs(on_table) == [(b['channel'], b['ON_time']['register_pair']) for b in bindings]
                       and pairs(period_table) == [(b['channel'], b['period']['register_pair']) for b in bindings]
                       and [(int(ch), device) for ch, device in re.findall(r'CH(\d+) (SM\d+)', monitor_table)]
                           == [(ch, f'SM{5299+ch}') for ch in range(1, 13)]
                       and all(('When ' + ('this' if form == 'PWM' else 'the DPWM') + ' instruction is') in table
                               and 'executed*3' in table for table in [on_table, period_table])
                       and 'Time with a unit selected on the parameter setting screen' in on_page)
        if table_bound:
            result = reviewed([{'kind': 'conditional_data_storage', 'when': {'instruction_executed': form},
                'destination_channel': {'operand': 'd'}, 'bindings': bindings,
                'unit': 'parameter_selected_us_or_ms', 'register_pair_order': 'source_printed_order'},
                {'kind': 'conditional_result', 'when': {'event': 'channel_pulse_output_started'},
                 'result': {'pulse_output_monitor_state': 'ON', 'channel_registers': [
                     {'channel': ch, 'device': f'SM{5299+ch}'} for ch in range(1, 13)]}}], ['s1', 's2', 'd'],
                'parameters.control_status_storage')
            result['additional_pages'].append(first + 1)
            return result

    if (kind == 'footnote' and page == first + 2
            and block.get('number') == 2 and block.get('domain') == 'processing_details'
            and clause == 'Parameter setting values are set to an SD device at STOP to RUN.'):
        return reviewed([{'kind': 'parameter_shadow_update', 'source': 'configured_parameters',
                          'storage': 'SD', 'event': 'stop_to_run'}], dimension='parameters.control_status_storage', additional_pages=[])

    if kind != 'precautions':
        return None
    normalize_stop = lambda t: re.sub(r'\s+', '', t.replace('[GLYPH-F06D]', 'μ').replace('µ', 'μ'))
    stop_keys = [key for key, source_clause in _PWM_STOP_QUALIFICATION_CLAUSES.items()
                 if ('_p' + str(page) + '_') in key and normalize_stop(source_clause) == normalize_stop(clause)]
    if stop_keys:
        result = reviewed([], dimension='execution.source_qualifications')
        result['full_clause_review'] = False
        result['preserve_unverified_dimension'] = True
        result['additional_pages'] = []
        result['value_updates'].update(unparsed_input_qualification=True,
            context_binding='not_fully_resolved', semantic_role='stop_delay_execution_qualification',
            exact_timing='not_established', conservative_stop_delay_upper_bound_us=5)
        if (form == 'DPWM' and page == 824 and _PWM_HARDWARE_TABLE in clause
                and '[FX5S/FX5UJ CPU module]' in text
                and '[FX5U/FX5UC CPU module and high-speed pulse input/output module]' in text):
            # A source layout merged the timing table into the first stop
            # paragraph. Keep a distinct input constraint for that table.
            result['split_pwm_hardware_table'] = True
        return result

    if (form == 'PWM' and page == precaution_page and clause == _PWM_HARDWARE_TABLE
        and '[FX5S/FX5UJ CPU module]' in text
        and '[FX5U/FX5UC CPU module and high-speed pulse input/output module]' in text):
        from plc.pwm_contract import PWM_AB_MINIMUM_TIMES
        rows = [dict(row) for row in PWM_AB_MINIMUM_TIMES]
        return reviewed([{'kind': 'PWM_hardware_minimum_times', 'ON_time': {'operand': 's1'},
            'period': {'operand': 's2'}, 'rows': rows, 'dash_cell': 'no_module_output_entry',
            'unit_conversion': 'parameter_selected_time_to_us'}], ['s1', 's2', 'd'])

    # The old rule silently translated "and" to "either". Neither connective
    # interpretation has enough evidence; prevent a verified derivative too.
    if page == 824 and form == 'DPWM' and clause == (
        'When a negative value is specified for the ON time by (s1) and the period by (s2), '
        'an operation error occurs. (In 16-bit instruction PWM, no error occurs.)'):
        result = reviewed([], ['s1', 's2'])
        result['full_clause_review'] = False
        # Neither operand negative makes either reading of the authored AND
        # antecedent false. Keep the uncertain clause applicable whenever
        # either operand is negative; independent p826 supplies the OR error.
        scalar = {'kind': 'int', 'bits': 64, 'signed': True}
        result['conditions'] = [{'predicate': {'op': 'or', 'type': {'kind': 'bool'}, 'args': [
            {'op': 'lt', 'type': {'kind': 'bool'}, 'args': [
                {'op': 'parameter', 'name': name, 'type': scalar},
                {'op': 'constant', 'value': 0, 'type': scalar}]} for name in ['S1', 'S2']]}}]
        result['value_updates'].update(unparsed_input_qualification=True,
            context_binding='not_fully_resolved', logical_connective='not_established',
            review_uncertainty='negative_operand_conjunction_requires_clarification')
        return result

    if (page == final_page and clause == 'The PWM output stops when SM8034 is on, and starts when SM8034 is off.'):
        return reviewed([{'kind': 'conditional_result', 'when': {'device': 'SM8034', 'state': state},
            'result': {'PWM_output': effect}} for state, effect in [('ON', 'stops'), ('OFF', 'starts')]],
            ['d'], 'effects.documented_result')
    if page == final_page and clause == 'PWM output does not stop, even if a pulse stop command for positioning is driven.':
        return reviewed([{'kind': 'conditional_result', 'when': {'event': 'positioning_pulse_stop_command_driven'},
            'result': {'PWM_output_stop_caused_by_this_command': False}}], ['d'], 'effects.documented_result')
    if page == final_page and clause == (
        'When specifying the number of output pulses, executing the ' + form + ' instruction, and then outputting pulses again '
        'after the pulse output stops due to the completion of output of the specified number of pulses, turn OFF the contact '
        'which drove the ' + form + ' instruction.'):
        return reviewed([{'kind': 'conditional_result', 'when': {'all': [
            {'pulse_count_mode': 'finite'}, {'instruction_executed': form},
            {'output_stopped_because': 'specified_pulse_count_completed'}, {'intent': 'output_pulses_again'}]},
            'result': {'required_action': {'drive_contact_of': form, 'state': 'OFF'}}}], ['d'])
    driver = 'HIOEN' if form == 'PWM' else 'DHIOEN'
    if page == final_page and clause == ('When driving PWM output by the ' + driver + ' instruction, use the ' + driver + ' instruction to turn PWM output off.'):
        return reviewed([{'kind': 'conditional_result', 'when': {'PWM_output_driver': driver},
            'result': {'required_stop_instruction': driver}}], ['d'])
    equality = 'When the period setting is equivalent to the ON time setting, the output always remains ON.'
    continued = 'The output ON state continues even after "Period ' + ('x' if form == 'PWM' else '[GLYPH-F0B4]') + ' Number of output pulses" is finished in this condition.'
    if page == final_page and clause in {equality, continued} and equality in source and continued in source:
        return reviewed([{'kind': 'conditional_result', 'when': {'left': {'operand': 's2'}, 'operator': '=',
            'right': {'operand': 's1'}}, 'result': {'output_state': 'ON', 'continuous': True,
            'persists_after': {'operation': 'multiply', 'arguments': [{'operand': 's2'}, {'quantity': 'number_of_output_pulses'}]}}}],
            ['s1', 's2', 'd'], 'effects.documented_result')

    navigation = _PWM_DIAGRAM_LABELS.get((page, clause))
    if navigation and 'Operations when the ' + form + ' output is stopped (while the output pulse is ' + navigation.lower() + ')' in text:
        return reviewed([{'kind': 'source_navigation', 'target': 'PWM_output_stop_timing_diagram',
            'output_phase': navigation}], dimension='resources.section_structure', content_role='source_navigation')
    return None


def _review_auxiliary_clause(kind, clause, *, section, block, value, declared):
    """Complete technical relations in the non-primary-effect source scopes.

    Every accepted branch consumes the full clause or binds its explicit
    antecedent in the same source block. Numeric roles such as a tool version,
    operation result and interrupt priority are not user input operands.
    """
    if kind == 'processing_qualifications':
        return None

    def reviewed(rules, dimension=None, **updates):
        return {'rules': rules, 'dimension': dimension, 'value_updates': updates, 'full_clause_review': True}

    high_speed_review = _review_high_speed_control(kind, clause, section=section, block=block, value=value)
    if high_speed_review:
        return high_speed_review
    file_review = _review_file_control(kind, clause, section=section, block=block, value=value)
    if file_review:
        return file_review
    if kind == 'operand_ranges':
        parameter_review = _review_operand_parameter(clause, section=section, block=block, value=value, declared=declared)
        if parameter_review:
            return parameter_review
        completion = _review_completion_parameter(clause, block=block, value=value)
        if completion:
            return completion
    if kind == 'error':
        basic_error = _review_basic_error(clause, section=section, block=block, declared=declared)
        if basic_error:
            return basic_error
        string_error = _review_string_error(clause, section=section, declared=declared)
        if string_error:
            return string_error
        if 'SFC' in section.get('outline', ''):
            target_operands = declared - {'en', 'eno'}
            target = next(iter(target_operands)) if len(target_operands) == 1 else None
            if target is None:
                # The batch-read source is explicitly the S-only operand;
                # destination and count operands are not SFC target addresses.
                _names, note_names = _operand_bindings(section, {})
                s_only = {name for note in _blocks(section)[1] if note['text'] == 'Only S can be used.'
                          for name in note_names.get((note['page'], note['domain'], note['number']), ())}
                if len(s_only & declared) == 1:
                    target = next(iter(s_only & declared))
            component = re.fullmatch(r'When (?:(?:a block No\. is (?P<specified>not )?specified, the(?: specified)?)|'
                                     r'the(?: specified)?) (?P<part>block No\.|step No\.|step relay No\. assigned the specified step|'
                                     r'step relay No\. where the specified step is assigned) '
                                     r'is out of (?:the )?range of (?P<low>\d+) to (?P<high>\d+)\.', clause)
            if target in {'s', 'd'} and component:
                part = {'block No.': 'block_number', 'step No.': 'step_number',
                        'step relay No. assigned the specified step': 'assigned_step_relay_number',
                        'step relay No. where the specified step is assigned': 'assigned_step_relay_number'}[component['part']]
                when = {'block_number_explicit': component['specified'] is None} if 'a block No.' in clause else {}
                return reviewed([{'kind': 'sfc_target_component_outside_interval',
                                  'subject': {'operand': target, 'component': part}, 'when': when,
                                  'minimum': int(component['low']), 'maximum': int(component['high'])}],
                                None, subjects=[{'operand': target}], source_formulas=[],
                                ranges=[{'minimum': int(component['low']), 'maximum': int(component['high']),
                                         'subject': {'operand': target, 'component': part}}])
            step_destination = re.fullmatch(r'When a step relay \(s\) is specified to (?P<dest>\(d\)(?: and \(n\))?)\.?', clause)
            if step_destination:
                subjects = _subjects(step_destination['dest'])
                return reviewed([{'kind': 'operand_device_class_disallowed', 'subjects': subjects,
                                  'device_class': 'S', 'device_role': 'step_relay'}], subjects=subjects,
                                source_formulas=[])
            nibble_mismatch = re.fullmatch(r'The number of nibbles of the nibble specification of bit device '
                                          r'\(s\) and \(d\) is different\.', clause)
            if nibble_mismatch:
                return reviewed([{'kind': 'operand_component_mismatch', 'left': {'operand': 's', 'component': 'nibble_count'},
                                  'right': {'operand': 'd', 'component': 'nibble_count'}}],
                                subjects=[{'operand': 's'}, {'operand': 'd'}], source_formulas=[])
            head_range = re.fullmatch(r'When the first device of (?P<subject>\([sdnm][0-9]?\)) is out of the range of the device No\.', clause)
            if head_range:
                subject = _subjects(head_range['subject'])[0]
                return reviewed([{'kind': 'operand_head_outside_device_domain', 'subject': subject,
                                  'domain': 'selected_device_class_number_range'}], subjects=[subject])
        real_review = _review_binary32_error(clause, section=section, block=block, value=value, declared=declared)
        if real_review:
            return real_review
        character_review = _review_character_error(clause, section=section, block=block, declared=declared)
        if character_review:
            return character_review
    if kind == 'footnote':
        if clause == 'When the DHCMOV instruction is used, the latest value can be read. A writable device can be updated immediately.':
            return reviewed([{'kind': 'special_device_access_timing', 'access_instruction': 'DHCMOV',
                              'read_visibility': 'latest_value',
                              'write_visibility': {'when_device_writable': True, 'visibility': 'immediate'}}],
                            'execution.documented_device_access', subjects=[], source_formulas=[],
                            numeric_values=[], operand_binding_required=False)
        array_label = re.fullmatch(r'When specifying setting data by using a label, define an array to secure enough '
                                  r'operation area and specify an element of the array label\.'
                                  r'(?P<digit> Digit specified bit type label cannot be used\.)?', clause)
        if array_label:
            subjects = value.get('subjects') or []
            rules = [{'kind': 'setting_data_label_storage', 'subjects': subjects,
                      'when_operand_category': 'label', 'requires_array_storage': True,
                      'requires_sufficient_operation_area': True, 'argument_form': 'array_element'}]
            if array_label['digit']:
                rules.append({'kind': 'label_form_disallowed', 'subjects': subjects, 'form': 'digit_specified_bit_type_label'})
            return reviewed(rules, 'parameters.label_storage_requirement', operand_binding_required=True)
        variable_arity = re.fullmatch(r'The input variable "s" can be changed in the range of (\d+) to (\d+)\.', clause)
        if variable_arity and re.search(r'\(s1\) to \(s28\)', section.get('text', '')):
            return reviewed([{'kind': 'variadic_input_arity', 'formal_group': 's',
                              'minimum_inputs': int(variable_arity[1]), 'maximum_inputs': int(variable_arity[2])}],
                            'parameters.documented_call_arity', subjects=[], source_formulas=[],
                            operand_binding_required=False)
        if clause == 'This error occurs when SM705 is on. When SM705 is off, sign data other than "2DH (-)" is ignored.':
            return reviewed([{'kind': 'ascii_sign_error_flag_condition', 'device': 'SM705',
                              'error_applies_when': True},
                             {'kind': 'ascii_sign_ignored', 'when': {'device': 'SM705', 'state': False},
                              'ignored_sign_codes': {'except': 0x2D}, 'minus_code': 0x2D}],
                            'effects.documented_conversion_condition', subjects=[], source_formulas=[],
                            numeric_values=[], operand_binding_required=False)
        digit_address = re.fullmatch(r'(?:When|If) the bit device digit is specified in (?P<subject>\([sd][0-9]?\)), '
                                    r'only multiples of 16 \(0, 16, 32, 64…\) can be specified as the device number\. '
                                    r'Only K4 can be specified as the number of digits\.', clause)
        if digit_address:
            subject = _subjects(digit_address['subject'])[0]
            return reviewed([{'kind': 'digit_specified_bit_address_domain', 'subject': subject,
                              'when_operand_form': 'digit_specified_bit', 'device_number_multiple': 16,
                              'required_nibble_count': 4}], 'constraints.documented_device_form',
                            subjects=[subject], source_formulas=[])
        if clause == 'The complete flag is not turned ON when an error is detected during instruction execution.':
            return reviewed([{'kind': 'completion_flag_suppressed', 'when': 'error_during_instruction_execution',
                              'completion_flag_turn_on': False}], 'execution.documented_timing',
                            operand_binding_required=False)
        if clause == 'A two-byte character such as a kanji character represented in shift JIS code should be counted 2.':
            return reviewed([{'kind': 'encoded_character_size', 'encoding': 'shift_jis',
                              'character_class': 'double_byte_character', 'counted_size_bytes': 2}],
                            'parameters.character_count_unit', numeric_values=[], operand_binding_required=False)
        if clause == 'For the firmware version of the CPU module supporting SM705, refer to the following. [GLYPH-F0C0] MELSEC iQ-F FX5 User\'s Manual (Application) A CPU module which does not support SM705 operates in the same way as SM705 is off even if it is turned on.':
            return reviewed([{'kind': 'firmware_support_dependency', 'feature': 'SM705',
                              'manual_id': 'fx5_application_ag'},
                             {'kind': 'unsupported_flag_effective_state', 'device': 'SM705',
                              'when': {'firmware_supports_SM705': False}, 'effective_state': False,
                              'regardless_of_written_state': True}], 'resources.firmware_dependency',
                            numeric_values=[], operand_binding_required=False)
        version = re.fullmatch(r'Supported by engineering tool version ["\u201c](\d+\.\d+[A-Z]?)["\u201d] and later\.', clause)
        if version:
            return reviewed([{'kind': 'engineering_tool_version', 'minimum': version[1]}],
                            'identity.engineering_tool_requirement', numeric_values=[],
                            operand_binding_required=False)
        reference = re.compile(r'When using (?P<classes>(?:LC|ST|T|C|F)(?:,\s*(?:LC|ST|T|C|F))*), '
                               r'refer to (?:\[GLYPH-F0C1\])?Page (?P<page>\d+)(?P<title>[^.]*?)\.')
        references = list(reference.finditer(clause))
        remainder = reference.sub('', clause).strip()
        if references and remainder in {'', 'Digit specified bit device cannot be used.'}:
            rules = [{'kind': 'conditional_manual_reference', 'when_device_classes': item['classes'].split(', '),
                      'manual_id': 'fx5_programming_ab', 'pdf_page': int(item['page'])} for item in references]
            dimension = 'resources.manual_references'
            if remainder:
                rules.append({'kind': 'disallowed_device_form', 'device_form': 'digit_specified_bit',
                              'subjects': value.get('subjects', [])})
                dimension = 'constraints.documented_restriction'
            return reviewed(rules, dimension)
        if clause == 'When bits of word devices are specified, devices turn ON/OFF by the 1/0 status of the specified bit.':
            return reviewed([{'kind': 'word_bit_boolean_interpretation', 'device_form': 'word_device_bit',
                              'quantifier': 'each_specified_bit',
                              'mapping': [{'bit_value': 1, 'state': True}, {'bit_value': 0, 'state': False}]}],
                            'parameters.word_bit_interpretation', source_formulas=[], operand_binding_required=False)
        if clause == 'Only the FX5 intelligent function module can use digit specification of bit devices.':
            return reviewed([{'kind': 'device_form_hardware_requirement', 'device_form': 'digit_specified_bit',
                              'required_module_class': 'FX5_intelligent_function_module',
                              'subjects': value.get('subjects', [])}], 'constraints.documented_restriction')

    if kind == 'error':
        scalar_negative = re.fullmatch(r'The value stored in a device specified in (?P<subject>\([sdnm][0-9]?\)) is negative\.', clause)
        if scalar_negative:
            subject = _subjects(scalar_negative['subject'])[0]
            return reviewed([{'kind': 'invalid_value_comparison', 'subject': subject, 'operator': '<', 'value': 0}],
                            subjects=[subject], source_formulas=[])
        terminator = re.fullmatch(r'In the corresponding device range after the device specified by '
                                 r'(?P<subject>\([sdnm][0-9]?\)), "(?P<code>00H)" does not exist\.', clause)
        wide_terminator = re.fullmatch(r'(?P<code>0000H) does not exist between setting areas in the device/label memory '
                                      r'after the device No\. specified in (?P<subject>\([sdnm][0-9]?\))\.', clause)
        if terminator or wide_terminator:
            match = terminator or wide_terminator
            subject = _subjects(match['subject'])[0]
            return reviewed([{'kind': 'string_terminator_missing', 'subject': subject, 'terminator_code': 0,
                              'code_unit_bits': 16 if wide_terminator else 8,
                              'search_extent': 'device_or_label_setting_area' if wide_terminator else 'corresponding_device_range'}],
                            subjects=[subject], source_formulas=[])
        reset_count = re.fullmatch(r'The number of devices to be reset is (?P<bound>\d+) or more when module access device '
                                  r'has been specified for (?P<first>\(d1\)) and/or (?P<last>\(d2\))\.', clause)
        if reset_count:
            return reviewed([{'kind': 'reset_range_size_restriction',
                              'subject': {'role': 'reset_device_count', 'first': {'operand': 'd1'}, 'last': {'operand': 'd2'}},
                              'when_any_operand_form': {'operands': ['d1', 'd2'], 'form': 'module_access_device'},
                              'invalid_operator': '>=', 'invalid_bound': int(reset_count['bound'])}],
                            subjects=[{'operand': 'd1'}, {'operand': 'd2'}], source_formulas=[])
        module_mismatch = re.fullmatch(r'Module number for (?P<first>\([sd][0-9]?\)) and (?P<last>\([sd][0-9]?\)) '
                                      r'differ when module access device is specified\.', clause)
        if module_mismatch:
            first, last = _subjects(module_mismatch['first'])[0], _subjects(module_mismatch['last'])[0]
            return reviewed([{'kind': 'module_number_mismatch', 'left': first, 'right': last,
                              'when_operand_form': 'module_access_device'}], subjects=[first, last])
        partial_overlap = re.fullmatch(r'Device range of \((?P<count>[nm][0-9]?)\) point\(s\) from '
                                      r'\((?P<src>[sd][0-9]?)\) partially overlaps with device range of '
                                      r'\((?P=count)\) point\(s\) from \((?P<dst>[sd][0-9]?)\)\. '
                                      r'\(Does not apply when same device has been specified for '
                                      r'\((?P=src)\) and \((?P=dst)\)\.\)', clause)
        if partial_overlap:
            src, dst, count = (partial_overlap[name] for name in ('src', 'dst', 'count'))
            return reviewed([{'kind': 'device_ranges_overlap',
                              'left': {'head': {'operand': src}, 'points': {'operand': count}},
                              'right': {'head': {'operand': dst}, 'points': {'operand': count}},
                              'exception': {'same_device': [src, dst]}}],
                            subjects=[{'operand': src}, {'operand': dst}, {'operand': count}], source_formulas=[])
        ascii_sign = re.fullmatch(r'The sign data is other than 20H, 2DH\.(?:\*\d+)?', clause)
        if ascii_sign and 's' in declared and 'ASCII' in section.get('section', ''):
            return reviewed([{'kind': 'ascii_sign_code_outside_set', 'subject': {'operand': 's', 'byte_offset': 0},
                              'allowed_codes': [0x20, 0x2D], 'when': {'device': 'SM705', 'state': True}}],
                            subjects=[{'operand': 's'}], source_formulas=[], numeric_values=[])
        ascii_digits = re.fullmatch(r'A value specified by \(s\) to \(s\)\+(?P<end>\d+) for each place of the ASCII code '
                                    r'is other than "30H" to "39H", "20H", and "00H"\.', clause)
        if ascii_digits:
            return reviewed([{'kind': 'ascii_digit_code_outside_domain', 'subject': {'operand': 's'},
                              'storage_word_offsets': {'minimum': 0, 'maximum': int(ascii_digits['end'])},
                              'scope': 'digit_places', 'allowed_code_interval': {'minimum': 0x30, 'maximum': 0x39},
                              'additional_allowed_codes': [0x20, 0]}], subjects=[{'operand': 's'}], source_formulas=[])
        ascii_value = re.fullmatch(r'The ASCII data in the device specified by \(s\) to \(s\)\+(?P<end>\d+) '
                                   r'is out of the valid range \((?P<low>[+\-]?\d+) to (?P<high>[+\-]?\d+)\) '
                                   r'\(when (?:a )?(?P<sign>signed|unsigned) data is specified\)\.', clause)
        if ascii_value:
            return reviewed([{'kind': 'ascii_numeric_value_outside_domain', 'subject': {'operand': 's'},
                              'storage_word_offsets': {'minimum': 0, 'maximum': int(ascii_value['end'])},
                              'when_numeric_representation': ascii_value['sign'],
                              'valid_minimum': int(ascii_value['low']), 'valid_maximum': int(ascii_value['high'])}],
                            subjects=[{'operand': 's'}], source_formulas=[])
        if clause == 'However, the instructions before the occurrence of an error are executed.':
            return reviewed([{'kind': 'error_partial_execution', 'when': 'instruction_sequence_error',
                              'instructions_before_error': 'executed'}], 'effects.documented_error_state',
                            operand_binding_required=False)
        if clause == 'The value in the middle of operation exceeds the 32-bit data range.':
            return reviewed([{'kind': 'intermediate_result_overflow', 'subject': {'role': 'intermediate_operation_result'},
                              'representation': {'width_bits': 32, 'signedness': 'source_not_declared'}}],
                            None, phase='result', operand_binding_required=False)
        communication = re.fullmatch(r'(?P<failure>Timeout occurred while communicating with|'
                                     r'Signal error (?:is|was) detected while accessing) '
                                     r'(?:the )?(?P<target>connected units|target modules) '
                                     r'during (?:the )?execution of the instruction\.', clause)
        if communication:
            return reviewed([{'kind': 'communication_timeout' if communication['failure'].startswith('Timeout') else
                              'communication_signal_error', 'phase': 'instruction_in_progress',
                              'target_role': communication['target'].replace(' ', '_')}],
                            None, operand_binding_required=False)
        if clause == 'Updating procedure with the unit was not properly completed during the execution of the instruction.':
            return reviewed([{'kind': 'module_update_not_completed', 'phase': 'instruction_in_progress'}],
                            None, operand_binding_required=False)
        if clause == 'A function which is set to be not used by parameters (such as interrupt input signal 1 and zero return relations) is used.':
            return reviewed([{'kind': 'parameter_disabled_function_used',
                              'invalid_when': {'parameter_function_enabled': False}}],
                            None, numeric_values=[], operand_binding_required=False)
        stop = re.fullmatch(r'The operation decelerates and stops by the (?P<reason>forward limit or reverse limit|'
                            r'pulse output stop command or special relay whose all outputs are disabled) '
                            r'during the pulse output or at the activating of the positioning\.', clause)
        if stop:
            return reviewed([{'kind': 'positioning_error_stop',
                              'when_any': ['forward_limit', 'reverse_limit'] if stop['reason'].startswith('forward') else
                                          ['pulse_output_stop_command', 'all_outputs_disabled_special_relay'],
                              'phases': ['pulse_output_in_progress', 'positioning_activation'], 'decelerates': True}],
                            'effects.documented_error_state', operand_binding_required=False)
        overflow = re.fullmatch(r'(?:The data type of the data setting is signed data and|Signed operation is performed and) '
                                r'the operation result exceeds (\d+)\.', clause)
        if overflow:
            return reviewed([{'kind': 'operation_result_outside_domain', 'when': {'signed_operation': True},
                              'subject': {'role': 'operation_result'}, 'operator': '>', 'value': int(overflow[1])}],
                            None, phase='result', subjects=[], operand_binding_required=False,
                            comparisons=[{'subject': {'role': 'operation_result'}, 'operator': '>', 'value': int(overflow[1])}])
        constant = re.fullmatch(r'A constant other than (?P<a>K\d+) or (?P<b>K\d+) is specified '
                                r'when the constant (?P<subject>\([sdnm][0-9]?\)) is specified\.', clause)
        if constant:
            return reviewed([{'kind': 'constant_value_restriction', 'subject': _subjects(constant['subject'])[0],
                              'when_operand_category': 'constant',
                              'allowed_values': [_number(constant['a'])['value'], _number(constant['b'])['value']]}])
        if clause == 'The specified file name string has a period (one-byte) at its end or directly before each delimiter.':
            return reviewed([{'kind': 'invalid_file_name_character_position', 'subjects': value.get('subjects', []),
                              'character': {'name': 'period', 'code_point': 46}, 'encoding_width': 'single_byte',
                              'invalid_positions': ['end', 'immediately_before_each_path_delimiter']}])
        if clause == 'When a file is specified, the file name string ends with a delimiter.':
            return reviewed([{'kind': 'invalid_file_path_suffix', 'subjects': value.get('subjects', []),
                              'when_target_type': 'file', 'invalid_suffix': 'path_delimiter'}])
        overlap = re.fullmatch(r'The device range for \((?P<count>[nm][0-9]?)\) point\(s\) beginning from '
                               r'\((?P<src>[sd][0-9]?)\) overlaps with that of \((?P<other>[nm][0-9]?)\) point\(s\) '
                               r'starting from \((?P<dst>[sd][0-9]?)\)\. '
                               r'\(Does not apply when same device has been specified for '
                               r'\((?P<src2>[sd][0-9]?)\) and \((?P<dst2>[sd][0-9]?)\)\.\)', clause)
        if overlap and overlap['src'] == overlap['src2'] and overlap['dst'] == overlap['dst2']:
            return reviewed([{'kind': 'device_ranges_overlap',
                              'left': {'head': {'operand': overlap['src']}, 'points': {'operand': overlap['count']}},
                              'right': {'head': {'operand': overlap['dst']}, 'points': {'operand': overlap['other']}},
                              'exception': {'same_head': True}}])
        if clause == 'When the high speed counter is assigned to the specification long counter.':
            return reviewed([{'kind': 'counter_assignment_restriction', 'specified_counter': 'long_counter',
                              'disallowed_assignment': 'high_speed_counter', 'subjects': value.get('subjects', [])}])

    if kind in {'execution_timing', 'control_data'}:
        toggle = re.fullmatch(r'This instruction reverses the state of the device specified by \(d\) '
                              r'when the execution command changes from OFF to ON\.', clause)
        if toggle:
            return reviewed([{'kind': 'device_toggle', 'subject': {'operand': 'd'},
                              'when': {'execution_command': {'from': False, 'to': True}}}],
                            'effects.documented_state')
        if re.fullmatch(r'This instruction shifts the ON/OFF status of the device before the device specified by \(d\) '
                        r'to the device specified by \(d\)\.', clause):
            return reviewed([{'kind': 'adjacent_bit_state_transfer', 'source': {'previous_device_of': {'operand': 'd'}},
                              'destination': {'operand': 'd'}}], 'effects.documented_state')
        if clause == 'The device before the device specified by (d) turns OFF.':
            return reviewed([{'kind': 'adjacent_bit_state_clear', 'subject': {'previous_device_of': {'operand': 'd'}},
                              'state': False}], 'effects.documented_state', subjects=[{'operand': 'd'}])
        shift = re.fullmatch(r'(?:These instructions|This instruction) shift(?:s)? \(n1\) (?P<unit>bits|words) of data '
                             r'to the (?P<direction>right|left) by \(n2\) (?:bit|word)\(s\) '
                             r'from the device specified by \(d\)\.', clause)
        if shift:
            return reviewed([{'kind': 'block_shift', 'head': {'operand': 'd'}, 'block_count': {'operand': 'n1'},
                              'shift_count': {'operand': 'n2'}, 'direction': shift['direction'],
                              'unit': 'bit' if shift['unit'] == 'bits' else 'word'}], 'effects.documented_state')
        insertion = re.fullmatch(r'After the shift, \(n2\) points from \(s\) are set into \(n2\) points from '
                                 r'\(d\)(?P<offset>\+\(n1-n2\))?\.', clause)
        if insertion:
            unit_source = next((text for page, text in section.get('page_layouts') or () if page == block['page']), '')
            unit = re.search(r'shift \(n1\) (bits|words) of data', unit_source)
            if unit:
                destination = {'head': {'operand': 'd'}}
                if insertion['offset']:
                    destination['offset'] = {'operation': 'subtract', 'left': {'operand': 'n1'}, 'right': {'operand': 'n2'}}
                return reviewed([{'kind': 'post_shift_insert', 'source': {'head': {'operand': 's'}},
                                  'destination': destination, 'count': {'operand': 'n2'},
                                  'unit': 'bit' if unit[1] == 'bits' else 'word'}], 'effects.documented_state')
        fill = re.fullmatch(r'When K(?P<constant>[01]) is specified for \(s\), set (?P<value>[01])s for \(n2\) '
                            r'points of bits from \(d\)(?P<offset>\+\(n1-n2\))? after the shift\.', clause)
        if fill and fill['constant'] == fill['value']:
            destination = {'head': {'operand': 'd'}}
            if fill['offset']:
                destination['offset'] = {'operation': 'subtract', 'left': {'operand': 'n1'}, 'right': {'operand': 'n2'}}
            return reviewed([{'kind': 'post_shift_constant_fill', 'when': {'operand': 's', 'constant': int(fill['constant'])},
                              'destination': destination, 'count': {'operand': 'n2'}, 'unit': 'bit',
                              'bit_value': bool(int(fill['value']))}], 'effects.documented_state')
        table_antecedent = 'operation result up to the OUT instruction changes from ON to OFF' in block['text']
        if table_antecedent and clause.startswith('Timer type | Timer coil | Current timer | Before time-out | After time-out '):
            if ('Timer | off | 0 | Non-Conductive | Conductive state | Non-Conductive | Conductive state' in clause
                    and 'Retentive timer | off | Holds current value Non-Conductive | Conductive state | Conductive state | Non-Conductive' in clause
                    and 'd' in declared):
                return reviewed([{'kind': 'timer_output_off_state', 'subject': {'operand': 'd'},
                                  'when': {'operation_result': {'from': True, 'to': False}},
                                  'rows': [{'timer_class': 'timer', 'coil': False, 'current_value': 0,
                                            'NO_contact': False, 'NC_contact': True},
                                           {'timer_class': 'retentive_timer', 'coil': False,
                                            'current_value': 'retained', 'contacts': 'retained'}]}],
                                'effects.documented_state', subjects=[{'operand': 'd'}])
        if clause == 'After the timer times up, clear the current value of the retentive timer and turn the contact off by the RST instruction.':
            return reviewed([{'kind': 'retentive_timer_reset', 'when': 'after_timer_time_out', 'instruction': 'RST',
                              'current_value': 0, 'NO_contact': False}], 'effects.documented_state', operand_binding_required=False)
        if clause == 'After a count up, the count value and contact status do not change until the RST instruction is executed.':
            return reviewed([{'kind': 'counter_value_retention', 'start_event': 'count_up',
                              'until': 'RST_execution', 'current_value': 'retained', 'contacts': 'retained'}],
                            'effects.documented_state', operand_binding_required=False)
        no_on = re.fullmatch(r'If an operation error is detected during the execution of the instruction, '
                             r'(?P<left>\([sdnm][0-9]?\)) and (?P<right>\([sdnm][0-9]?\)\+1) do not turn on\.', clause, re.I)
        if no_on:
            if (block['page'] == 578 and no_on['left'] == '(d2)' and no_on['right'] == '(d2)+1' and
                    any(re.search(r'\(d3\)\s*\|?\s*Bit device that turns on upon completion of the processing', layout)
                        for _page, layout in section.get('page_layouts') or ())):
                return {**reviewed([{'kind': 'source_conflict', 'conflict': 'completion_flag_operand_disagrees_with_status_storage',
                    'source_claims': [{'source_region': 'operation_error_suppression_clause',
                                      'subjects': [{'operand': 'd2'}, {'operand': 'd2', 'offset': 1}], 'turn_on': False},
                                     {'source_region': 'parameter_table_and_completion_timing',
                                      'completion': {'operand': 'd3'}, 'error_completion': {'operand': 'd3', 'offset': 1},
                                      'status_data_storage': {'operand': 'd2'}}], 'resolution': 'not_established'}],
                    'execution.documented_timing', subjects=[{'operand': 'd2'}, {'operand': 'd3'}],
                    source_formulas=[], gap_kind='source_conflict', source_disposition='source_conflict'),
                    'status': 'conflict', 'additional_pages': [575]}
            return reviewed([{'kind': 'completion_devices_suppressed', 'when': 'operation_error_during_execution',
                              'subjects': _subjects(no_on['left'] + ' ' + no_on['right']), 'turn_on': False}],
                            'execution.documented_timing')
        indication = re.findall(r'(?:Completion status indication device|Completion device)\s*(\([sdnm][0-9]?\)\+1)',
                                block['text'], re.I)
        if indication:
            subject = _subjects(indication[-1])[0]
            normal = re.fullmatch(r'(?:Status \| Description )?When completed normally\s*(?:\||:)\s*'
                                  r'(?:The device (?:does not change \(remains (?:OFF|off)\)|remains off)|Unchanged from off)\.', clause)
            if normal:
                return reviewed([{'kind': 'completion_status_device', 'subject': subject,
                                  'when': 'normal_completion', 'state': False, 'state_update': 'unchanged'}],
                                'execution.documented_timing', subjects=[subject])
            timing = re.fullmatch(r'When completed (?:with an error|abnormally)\s*(?:\||:)\s*'
                r'(?:(?:The|This) device )?(?:is )?turn(?:s|ed)? on (?:during|by) (?:the )?END processing '
                r'(?:for|of) the scan (?:in which|where) (?:the )?'
                r'[A-Z0-9_.\[\]\\\-]+ instruction (?:is |has been )?completed,? and '
                r'(?:(?:the device |it )?(?:is )?)turn(?:s|ed)? off (?:during|by) (?:the )?next END processing\.', clause, re.I)
            if timing:
                return reviewed([{'kind': 'completion_status_device', 'subject': subject,
                                  'when': 'abnormal_completion',
                                  'on': {'event': 'END', 'scan': 'instruction_completion'},
                                  'off': {'event': 'END', 'scan': 'next_after_completion'}}],
                                'execution.documented_timing', subjects=[subject],
                                unresolved_glyphs=[glyph for glyph in value.get('unresolved_glyphs', []) if glyph != 'F06F'])
        if (kind == 'control_data' and not block.get('bound_subjects') and
                re.fullmatch(r'For the combination of processing units, refer to (?:\[GLYPH-F0C1\])?Page \d+ '
                             r'Combination of processing units\s*\.', clause)):
            return reviewed([{'kind': 'manual_cross_reference', 'manual_id': 'fx5_programming_ab',
                              'pages': value.get('referenced_pages', [])}], 'resources.manual_references',
                            operand_binding_required=False, control_field_binding_complete=True)

    if kind == 'precautions':
        binary = re.fullmatch(r'Binary data is used in all operations in CPU module including arithmetic operations '
                              r'\(\+-\[GLYPH-F0B4\]\[GLYPH-F0B8\]\), increment and decrement instructions\.', clause)
        if binary:
            return reviewed([{'kind': 'cpu_default_number_representation', 'representation': 'binary',
                              'operation_classes': ['arithmetic', 'increment', 'decrement']}],
                            'parameters.number_representation', source_formulas=[], unresolved_glyphs=[],
                            operand_binding_required=False)
        bcd_input = re.fullmatch(r'When receiving digital switch information in binary-coded decimal \(BCD\) format '
                                 r'into a CPU module, use the BIN(?:\(P\))? instructions? '
                                 r'\(for converting BCD data into binary data\)\.', clause)
        if bcd_input:
            return reviewed([{'kind': 'number_representation_conversion', 'source': 'bcd', 'destination': 'binary',
                              'instruction_family': 'BIN', 'use_context': 'external_digital_switch_data'}],
                            'parameters.number_representation', operand_binding_required=False)
        bcd_output = re.fullmatch(r'Furthermore, to output data to seven-segment display unit handling '
                                  r'binary-coded decimal \(BCD\) data, use the BCD(?:\(P\))? instructions? '
                                  r'\(for converting binary data into BCD data\)\.', clause)
        if bcd_output:
            return reviewed([{'kind': 'number_representation_conversion', 'source': 'binary', 'destination': 'bcd',
                              'instruction_family': 'BCD', 'use_context': 'external_seven_segment_display'}],
                            'parameters.number_representation', operand_binding_required=False)
        if clause == 'The data conversion speed depends on the scan time of the CPU module.':
            return reviewed([{'kind': 'conversion_rate_dependency', 'depends_on': 'cpu_scan_time'}],
                            'execution.documented_timing', operand_binding_required=False)
        if clause == 'When an input relay (X) is specified as (s), the response delay will be "Scan time of CPU module + Input filter constant".':
            return reviewed([{'kind': 'input_response_delay', 'subject': {'operand': 's'}, 'when_device_class': 'X',
                              'delay': {'operation': 'add', 'left': {'role': 'cpu_scan_time'},
                                        'right': {'role': 'input_filter_constant'}}}], 'execution.documented_timing')
        repeat = re.fullmatch(r'Note that data is inverted in every operation cycle in a continuous operation type '
                              r'\((?P<form>NEG|DNEG)\) instruction\.', clause)
        if repeat:
            return {**reviewed([{'kind': 'negation_repetition', 'form': repeat['form'], 'period': 'each_operation_cycle'}],
                              'execution.documented_timing', operand_binding_required=False), 'forms': [repeat['form']]}
        byte_sign = re.fullmatch(r'Store sign(?:ed)? data, "space \(20H\)" or "- \(2DH\)"(?: only)? '
                                 r'in the 1st byte \(low-order 8 bits of the head device set in \(s\)\)\.', clause)
        if byte_sign:
            return reviewed([{'kind': 'string_sign_byte_domain', 'subject': {'operand': 's'}, 'byte_offset': 0,
                              'storage_part': 'low_8_bits_of_head_device', 'allowed_codes': [0x20, 0x2D]}],
                            'constraints.documented_restriction', source_formulas=[])
        if clause == 'Only the ASCII data "0 (30H)" to "9 (39H)", "space (20H)" and "decimal point (2EH)" can be stored from the 2nd byte to the "00H" at the end of the character string in (s).':
            return reviewed([{'kind': 'string_payload_byte_domain', 'subject': {'operand': 's'}, 'start_byte_offset': 1,
                              'end': {'before_terminator_code': 0}, 'allowed_code_interval': {'minimum': 0x30, 'maximum': 0x39},
                              'additional_allowed_codes': [0x20, 0x2E], 'encoding': 'ascii'}],
                            'constraints.documented_restriction', source_formulas=[])
        if clause == 'If "- (2DH)" is stored in the 2nd byte or later, an operation error occurs.':
            page_source = next((text for page, text in section.get('page_layouts') or () if page == block['page']), '')
            if 'head device set in (s)' in page_source and 's' in declared:
                return reviewed([{'kind': 'string_sign_byte_position', 'subject': {'operand': 's'}, 'code': 0x2D,
                                  'invalid_minimum_byte_offset': 1, 'consequence': 'operation_error'}],
                                'constraints.documented_restriction', subjects=[{'operand': 's'}], source_formulas=[])
        negative = re.fullmatch(r'Do not set a negative value to the number of bits to be rotated \(n\)\.', clause)
        if negative:
            return reviewed([{'kind': 'value_comparison', 'subject': {'operand': 'n'}, 'operator': '>=', 'value': 0}],
                            'constraints.documented_restriction')
        if re.fullmatch(r'In the case of continuous operation type instructions \((?P<forms>[A-Z]+(?: and [A-Z]+)*)\), '
                        r'note that shift and rotation are executed in every scan time \(operation cycle\)\.', clause):
            forms = re.search(r'\(([A-Z]+(?: and [A-Z]+)*)\)', clause)[1].split(' and ')
            return reviewed([{'kind': 'continuous_shift_execution', 'forms': forms, 'repeat_event': 'each_operation_cycle'}],
                            'execution.documented_timing', numeric_values=[], operand_binding_required=False,
                            form_conditions=forms)
        if clause == 'Nesting up to 15 levels (N0 to N14) is possible.':
            return {**reviewed([{'kind': 'master_control_nesting', 'maximum_levels': 15,
                                'minimum_identifier': 'N0', 'maximum_identifier': 'N14'}],
                              'execution.documented_context', operand_binding_required=False), 'languages': ['ladder']}
        if clause == 'When the MCR instruction is nested at a single location, all master controls can be ended by just one nesting (N) number, the smallest number.':
            return {**reviewed([{'kind': 'master_control_end_scope', 'when': 'MCR_at_single_location',
                                'nesting_identifier': 'smallest', 'ended_scopes': 'all_master_controls'}],
                              'execution.documented_context', subjects=[], operand_binding_required=False), 'languages': ['ladder']}
        if clause == 'When the CPU module is programmed with the ALT instruction, reversal operation is performed at every operation cycle.':
            return reviewed([{'kind': 'toggle_repetition', 'form': 'ALT', 'period': 'each_operation_cycle'}],
                            'execution.documented_timing', operand_binding_required=False)
        if clause == 'To perform reversal operation by command ON/OFF, either use the ALTP instruction (pulse execution type) or set a command contact as LDP etc. (pulse execution type).':
            return reviewed([{'kind': 'toggle_trigger_selection', 'desired_trigger': 'command_ON_OFF',
                              'alternatives': [{'instruction': 'ALTP'}, {'instruction': 'ALT', 'command_contact': 'rising_edge',
                                                                                  'contact_example': 'LDP'}]}],
                            'execution.documented_timing', operand_binding_required=False)
        signed = re.fullmatch(r'When the most significant bit is "1" in the data stored in \(s1\) or \(s2\), '
                              r'it is regarded as a negative binary value for comparison\. \(Excluding unsigned operation\)', clause)
        if signed:
            return reviewed([{'kind': 'signed_comparison_interpretation', 'subjects': [{'operand': 's1'}, {'operand': 's2'}],
                              'when': {'most_significant_bit': True, 'unsigned_operation': False},
                              'interpretation': 'negative_binary_value'}], 'parameters.documented_interpretation')
        less = re.fullmatch(r'Set \((?P<left>s[0-9]?)\) to a value less than \((?P<right>s[0-9]?)\)\.', clause)
        if less:
            return reviewed([{'kind': 'operand_comparison', 'left': {'operand': less['left']}, 'operator': '<',
                              'right': {'operand': less['right']}}], 'constraints.documented_restriction')
        priority = re.fullmatch(r'If this instruction is executed in an interrupt program with the priority (\d+), '
                                r'operation error \(([0-9A-F]+H)\) occurs\.', clause)
        if priority:
            return reviewed([{'kind': 'interrupt_priority_restriction', 'context': 'interrupt_program',
                              'disallowed_priorities': [int(priority[1])], 'error_code': priority[2]}],
                            'constraints.documented_restriction', operand_binding_required=False)
        priority = re.fullmatch(r'This instruction operates in an interrupt program with the priority (\d+) or (\d+)\.', clause)
        if priority:
            return reviewed([{'kind': 'interrupt_priority_support', 'context': 'interrupt_program',
                              'allowed_priorities': [int(priority[1]), int(priority[2])]}],
                            'execution.documented_context', operand_binding_required=False)
        if clause == 'The written value is reflected at the first time that the instruction is executed after the device is overwritten.':
            role = re.search(r'Overwrite the (positioning address|command speed) during the pulse output', block['text'])
            if role:
                return reviewed([{'kind': 'operand_update_visibility', 'subject_role': role[1].replace(' ', '_'),
                                  'write_phase': 'pulse_output_in_progress',
                                  'visible_at': 'first_instruction_execution_after_write'}],
                                'execution.documented_timing', operand_binding_required=False)
        modification = re.fullmatch(r'Do not change (?P<subject>\([sdnm][0-9]?\)) while the instruction is executing\.', clause)
        if modification:
            return reviewed([{'kind': 'modification_restriction', 'subject': _subjects(modification['subject'])[0],
                              'phases': ['instruction_in_progress']}], 'constraints.documented_restriction')
    return None


def _binary32_parameter_subjects(section, page):
    """Read literal input/data rows from this call form's nearest Setting data."""
    layouts = [(int(number), text) for number, text in section.get('page_layouts') or () if int(number) <= int(page)]
    setting_page = next((number for number, text in reversed(layouts)
                         if 'Setting data' in text and 'ANYREAL_32' in text), None)
    if setting_page is None:
        return [], None
    subjects = []
    for row in _body_blocks(section):
        if row['kind'] != 'operand_ranges' or row['page'] != setting_page or 'ANYREAL_32' not in row['text']:
            continue
        if re.search(r'(?:for storing (?:the )?(?:operation|converted|conversion) result|'
                     r'(?:stores?|storing) the (?:operation|converted|conversion) result)', row['text'], re.I):
            continue
        if re.search(r'\b(?:data|number)\b', row['text'], re.I):
            subjects.extend(subject for subject in row.get('bound_subjects', []) if subject not in subjects)
    return subjects, setting_page


def _review_binary32_error(clause, *, section, block, value, declared):
    """The exact binary32 error templates, with powers checked on the PDF.

    Original visual witnesses: AB PDF331/333, 670/672, 1280 and 1319.  No glyph
    replacement is applied to another formula, a processing-row upper bound or
    a different precision.  The source error's operands remain mandatory.
    """
    if value.get('notation_review_basis') == 'original_PDF_visual_review' and block['page'] == 331:
        return None
    compact = re.sub(r'\s+', '', clause)
    if block['page'] == 1319:
        # The original page places chapter number 24 in the right margin at
        # this exact source-row boundary, outside the error description.
        compact = compact.replace('range.|240,2-126', 'range.0,2-126')
    subjects = list(value.get('subjects') or [])
    binding_page = None
    if not subjects:
        subjects, binding_page = _binary32_parameter_subjects(section, block['page'])
    if not subjects:
        page_source = next((text for page, text in section.get('page_layouts') or () if page == block['page']), '')
        named = re.search(r'Values in the devices specified \(stored\) by (?P<names>.+?) should be 0 or ',
                          _flat(page_source))
        if named:
            subjects = _subjects(named['names'])
        else:
            conversion = re.search(r'(?:convert|converts) the single-precision real number.*?'
                                   r'(?:specified (?:by|with|in)) (?P<subject>\(s\)) to ', section.get('text', ''), re.I)
            if conversion:
                subjects = _subjects(conversion['subject'])
    if any(subject['operand'].casefold() not in declared for subject in subjects):
        return None
    nonfinite = re.fullmatch(r'(?:The (?:specified device value|set device or label value|'
                             r'value specified in \([sdnm][0-9]?\)|data specified by \([sdnm][0-9]?\)'
                             r'(?: to \([sdnm][0-9]{0,2}\))?)|\([sdnm][0-9]?\)) is '
                             r'(?P<negative_zero>-0, )?denormalized number, NaN \(not a number\), or '
                             r'\[GLYPH-F0B1\]\[GLYPH-F0A5\]\.', clause)
    formula = re.search(r'0,2-126\[GLYPH-F0A3\]\|?(?:specifiedvalue\(storedvalue\)|specifieddevicevalue|'
                        r'\([sdnm][0-9]?\))\|?<2128', compact, re.I)
    if subjects and (nonfinite or formula):
        rejected = ['subnormal', 'nan', 'positive_infinity', 'negative_infinity']
        if '-0,' in compact:
            rejected.insert(0, 'negative_zero')
        if formula:
            residue = compact[:formula.start()] + compact[formula.end():]
            residue = re.sub(r'^(?:Whenthecontentsofthespecifieddeviceareoutsidethefollowingrange:|'
                             r'Thevaluestoredinspecifieddeviceisoutsidethefollowingrange|'
                             r'Thevalueof\([sdnm][0-9]?\)isoutsidethefollowingrange\.|'
                             r'Thesetsingle-precisionrealnumberisnotlocatedwithinthefollowingrange\.|'
                             r'\([sdnm][0-9]?\)isnotlocatedwithinthefollowingrange)', '', residue)
            residue = re.sub(r'The(?:specifieddevicevalue|dataspecifiedby\([sdnm][0-9]?\))is'
                             r'(?:-0,)?denormalizednumber,NaN\(notanumber\),or'
                             r'\[GLYPH-F0B1\]\[GLYPH-F0A5\]\.', '', residue)
            if residue.strip('.'):
                return None
        rules = [{'kind': 'invalid_binary32_input_domain', 'subjects': subjects,
                  'rejected_classes': rejected}]
        group = re.search(r'\((?P<first>s\d+)\) to \((?P<last>s\d+)\)', clause)
        if group:
            aliases = _argument_aliases(section)
            tail = aliases.get(group['last']) or {}
            if tail.get('variadic_group') == 's':
                rules[0]['source_operand_group'] = {'base_name': 's',
                    'first_source_index': int(group['first'][1:]), 'last_source_index': int(group['last'][1:]),
                    'applies_to': 'supplied_call_arguments'}
        if formula:
            rules[0]['valid_domain'] = {'positive_zero': True,
                'finite_normal': {'absolute_minimum': {'base': 2, 'exponent': -126, 'inclusive': True},
                                  'absolute_maximum': {'base': 2, 'exponent': 128, 'inclusive': False}}}
        return {'rules': rules, 'full_clause_review': True,
                'additional_pages': [binding_page] if binding_page else [],
                'value_updates': {'subjects': subjects, 'operand_binding_required': True, 'ranges': [],
                                  'comparisons': [], 'source_formulas': [], 'unresolved_glyphs': [],
                                  'numeric_values': [{'value': 0, 'encoding': 'decimal'}] if formula else [],
                                  'notation_review_basis': 'reviewed_exact_binary32_error_template',
                                  'notation_visual_witness_pages': [331, 333, 670, 672, 1319]}}
    result = re.fullmatch(r'(?:The value of )?\(d\) exceeds the following range\. \(An overflow has occurred\.\) '
                          r'(?:\|\s*24\s*)?\(d\)\|<2128', clause)
    if result and 'd' in declared and block['codes'] == ['3403H']:
        return {'rules': [{'kind': 'operation_result_outside_domain', 'subject': {'operand': 'd'},
                           'valid_absolute_maximum': {'base': 2, 'exponent': 128, 'inclusive': False}}],
                'full_clause_review': True, 'dimension': 'effects.operation_error',
                'value_updates': {'phase': 'result', 'subjects': [{'operand': 'd'}],
                    'source_formulas': [], 'unresolved_glyphs': [], 'comparisons': [], 'numeric_values': [],
                    'notation_review_basis': 'original_PDF_visual_review', 'notation_review_page': 1280}}
    if (block['page'] == 1319 and block['codes'] == ['3403H'] and
            compact == 'Theoperationresultiswithinthefollowingrange.2128[GLYPH-F0A3]|operationresult|'):
        return {'rules': [{'kind': 'operation_result_overflow', 'subject': {'role': 'operation_result'},
                           'invalid_absolute_minimum': {'base': 2, 'exponent': 128, 'inclusive': True}}],
                'full_clause_review': True, 'dimension': 'effects.operation_error',
                'value_updates': {'phase': 'result', 'subjects': [],
                    'operand_binding_required': False, 'source_formulas': [], 'unresolved_glyphs': [],
                    'comparisons': [], 'numeric_values': [], 'notation_review_basis': 'original_PDF_visual_review',
                    'notation_review_page': 1319}}
    return None


def _review_character_error(clause, *, section, block, declared):
    """Literal numeric-string syntax qualifications, with their own input."""
    if 's' not in declared or not re.search(r'Converting STRING to (?:BOOL|INT|DINT|LINT|UINT|UDINT|ULINT|REAL|LREAL)',
                                          section.get('section', ''), re.I):
        return None
    rules = None
    subject = {'operand': 's'}
    if clause == '00H does not exist in the corresponding device range starting from (s)':
        rules = [{'kind': 'terminator_missing', 'subject': subject, 'value': 0,
                  'encoding': 'hexadecimal', 'search_scope': 'declared_device_area'}]
    elif clause == 'Invalid data which cannot be converted to (s) are set.':
        rules = [{'kind': 'numeric_string_not_convertible', 'subject': subject}]
    elif clause == 'Any character other than "30(0)" to "39(9)" exists in the integer part or decimal part.':
        rules = [{'kind': 'numeric_string_invalid_characters', 'subject': subject,
                  'components': ['integer_part', 'fraction_part'], 'allowed_characters': '0123456789'}]
    elif clause == '2EH (.) exists in two or more positions in the specified character string.':
        rules = [{'kind': 'numeric_string_character_count', 'subject': subject,
                  'character': {'name': 'period', 'source_code': 0x2E},
                  'invalid_minimum_count': 2, 'component': 'entire_string'}]
    elif re.fullmatch(r'Any character other than 45H \(E\), 65\(e\), 2B\(\+\)\s*, or 2D\(-\) '
                      r'exists in the specified exponent part\.', clause):
        # The adjacent clause also discusses decimal digits in this component.
        # Do not silently invent whether "other character" excludes digits.
        return {'rules': [{'kind': 'numeric_string_source_character_qualification', 'subject': subject,
                           'source_named_component': 'exponent_part', 'listed_characters': ['E', 'e', '+', '-'],
                           'decimal_digit_treatment': 'source_scope_ambiguous'}],
                'full_clause_review': False, 'value_updates': {'subjects': [subject], 'operand_binding_required': True,
                    'numeric_values': [], 'source_formulas': [], 'unresolved_glyphs': [],
                    'unparsed_input_qualification': True,
                    'unresolved_semantic_components': ['exponent_part_character_scope_including_decimal_digits']}}
    elif clause == 'Two or more exponent parts of 45H (E) or 65(e) exist in the specified character string.':
        rules = [{'kind': 'numeric_string_character_count', 'subject': subject, 'characters': ['E', 'e'],
                  'invalid_minimum_count': 2, 'component': 'entire_string'}]
    elif clause == 'Three or more digits of numerical values in the exponent parts are described in the specified character string.':
        rules = [{'kind': 'numeric_string_exponent_digits', 'subject': subject, 'invalid_minimum_count': 3}]
    elif clause == 'Two or more signs of exponent parts of 2B(+) or 2D(-) exist in the specified character string.':
        rules = [{'kind': 'numeric_string_sign_count', 'subject': subject, 'sign_characters': ['+', '-'],
                  'invalid_minimum_count': 2, 'component': 'exponent_part'}]
    elif re.fullmatch(r'Two or more signs of 2B\(\+\) or 2D\(-\) exist in the (?:integer|integral) part '
                      r'for the decimal point format,? and exist in the mantissa part for the exponent format '
                      r'in the specified character string\.', clause):
        rules = [{'kind': 'numeric_string_sign_count', 'subject': subject, 'sign_characters': ['+', '-'],
                  'invalid_minimum_count': 2,
                  'components_by_format': {'decimal_point': 'integer_part', 'exponent': 'mantissa_part'}}]
    elif clause == 'The number of characters after (s) is 0 or more than 24':
        rules = [{'kind': 'numeric_string_character_length', 'subject': subject,
                  'valid_minimum': 1, 'valid_maximum': 24}]
    if rules is None:
        return None
    return {'rules': rules, 'full_clause_review': True,
            'value_updates': {'subjects': [subject], 'numeric_values': [], 'ranges': [], 'comparisons': [],
                              'source_formulas': [], 'unresolved_glyphs': [], 'operand_binding_required': True}}


def _review_file_path_parameter(clause, *, section, block, value):
    """File-name cells are reviewed separately from numeric control fields."""
    subjects = block.get('bound_subjects') or []
    if len(subjects) != 1 or not re.match(r'^(?:to \+\[GLYPH-F06F\] )?(?:(?:File|Folder) name character|Folder path|File name/folder name)', clause):
        return None
    primary = subjects[0]
    text = re.sub(r'^to \+\[GLYPH-F06F\]\s*', '', clause)
    path_only = text.startswith('Folder path ')
    new_name = text.startswith('File name/folder name ')
    target_type = 'folder' if re.match(r'^(?:to \+\[GLYPH-F06F\] )?Folder name', clause) else None
    text = re.sub(r'^(?:(?:File|Folder) name character(?: string)?|Folder path|File name/folder name)\s*', '', text)
    text = re.sub(r'(Unicode string User)\s+Application setting area .+$', r'\1', text)
    text = text.replace('Unicode string User', '')
    text = re.sub(r'\bstring\s*\|\s*and the file name\.', 'and the file name.', text)
    text = text.replace('|', ' ')
    # Numbered arrows and a literal sample path are diagram rendering. Their
    # numbered technical qualifications below are retained and bound to this
    # physical file-name cell (original PDF518/540 reviewed visually).
    text = re.sub(r'(?:\(\d+\)\s*)+"/[^"\n]+"(?:\s*\(\d+\)(?!:))*', ' ', text)
    text = re.sub(r'(?<=\.)\s*"/[^"\n]+"(?:\s*\(\d+\)(?!:))*', ' ', text)
    text = re.sub(r'\s*(?=\(\d+\):)', ' • ', text)
    text = _flat(text)
    rules, missing = [], []
    for index, part in enumerate(_clauses(text)):
        before = len(rules)
        if part.startswith('Folder name character string '):
            part = part.removeprefix('Folder name character string ')
            target_type = 'folder'
        if part.startswith('When specifying a file,'):
            target_type = 'file'
        elif part.startswith('When specifying a folder,'):
            target_type = 'folder'
        legend = re.match(r'^\((\d+)\):\s*(.*)', part)
        line = legend[2] if legend else part
        if re.fullmatch(r'Specify the folder path where the files are stored and the file name\.', line):
            rules.append({'kind': 'file_path_storage', 'subject': primary, 'encoding': 'unicode',
                          'components': ['folder_path', 'file_name']})
        elif re.fullmatch(r'When specifying a file, specify the folder path where (?:the file to be '
                          r'(?:deleted|copied|moved|read|written|renamed)|the file whose status is to be acquired|'
                          r'the copy source file|the file to move) '
                          r'is stored and the file name\.', line):
            rules.append({'kind': 'file_path_storage', 'subject': primary, 'encoding': 'unicode',
                          'when_target_type': 'file', 'components': ['folder_path', 'file_name']})
        elif re.fullmatch(r'When specifying a folder, specify (?:the copy source folder path|'
                          r'the folder path of the folder (?:to move|to be deleted|to be renamed|whose status is to be acquired))\.', line):
            rules.append({'kind': 'file_path_storage', 'subject': primary, 'encoding': 'unicode',
                          'components': ['folder_path'], 'role': 'source_folder'})
        elif path_only and re.fullmatch(r'Specify the (?:copy )?destination folder path\.', line):
            rules.append({'kind': 'file_path_storage', 'subject': primary, 'encoding': 'unicode',
                          'components': ['folder_path'], 'role': 'destination_folder'})
        elif new_name and line == 'Specify the string of the file name/folder name after the change.':
            rules.append({'kind': 'file_path_storage', 'subject': primary, 'encoding': 'unicode',
                          'components': ['file_or_folder_name'], 'role': 'new_name'})
        elif new_name and line == 'Do not specify a folder path.':
            rules.append({'kind': 'file_path_component_restriction', 'subject': primary, 'prohibited_component': 'folder_path'})
        elif new_name and re.fullmatch(r'The (?P<part>file|folder) name(?: \(including an extension\))? must be within (?P<max>\d+) characters\.', line):
            match = re.fullmatch(r'The (?P<part>file|folder) name(?P<extension> \(including an extension\))? must be within (?P<max>\d+) characters\.', line)
            rules.append({'kind': 'file_path_character_limit', 'subject': primary,
                          'component': match['part'] + '_name', 'when_target_type': match['part'],
                          'maximum_characters': int(match['max']), 'includes_extension': bool(match['extension'])})
        elif new_name and line == 'Specify one character or more for the file name or folder name.':
            rules.append({'kind': 'file_path_minimum_content', 'subject': primary, 'minimum_non_delimiter_characters': 1})
        elif new_name and line == 'Do not specify a delimiter.':
            rules.append({'kind': 'file_path_character_restriction', 'subject': primary, 'prohibited_characters': 'path_delimiters'})
        elif re.fullmatch(r'The folder path and file name \(including an extension\) must be within (\d+) characters in total\.', line):
            number = int(re.search(r'within (\d+)', line)[1])
            rules.append({'kind': 'file_path_character_limit', 'subject': primary, 'component': 'complete_path',
                          'maximum_characters': number, 'includes_extension': True})
        elif re.fullmatch(r'The folder path must be within (\d+) characters\. \(Delimiters are not included\.\)', line):
            number = int(re.search(r'within (\d+)', line)[1])
            rules.append({'kind': 'file_path_character_limit', 'subject': primary, 'component': 'folder_path',
                          'maximum_characters': number, 'counted_delimiters': False})
        elif re.fullmatch(r'The folder path must be within (\d+) characters\. '
                          r'\(Delimiters at the end of the folder path are not included\.\)', line):
            rules.append({'kind': 'file_path_character_limit', 'subject': primary, 'component': 'folder_path',
                          'maximum_characters': int(re.search(r'within (\d+)', line)[1]),
                          'excludes_trailing_folder_delimiter': True})
        elif re.fullmatch(r'The number of folder path hierarchies must be within (\d+) levels\.', line):
            rules.append({'kind': 'file_path_hierarchy_limit', 'subject': primary,
                          'maximum_levels': int(re.search(r'within (\d+)', line)[1])})
        elif line == 'When omitting an extension in the file name, omit the ". (period)" as well.':
            rules.append({'kind': 'file_extension_omission', 'subject': primary,
                          'when_extension_omitted': True, 'extension_separator_code_point': 46,
                          'separator_must_also_be_omitted': True})
        elif re.fullmatch(r'The file name must be within (\d+) characters \(a period and extension excluded\)\.', line):
            rules.append({'kind': 'file_path_character_limit', 'subject': primary, 'component': 'file_base_name',
                          'maximum_characters': int(re.search(r'within (\d+)', line)[1]),
                          'includes_extension': False, 'includes_extension_separator': False})
        elif re.fullmatch(r'If (\d+) or more characters are used, the extension is ignored and replaced with "\.BIN" or "\.CSV"\.', line):
            rules.append({'kind': 'file_extension_replacement', 'subject': primary,
                          'when_base_name_characters_at_least': int(re.search(r'If (\d+)', line)[1]),
                          'specified_extension': 'ignored', 'replacement_extension_names': ['BIN', 'CSV'],
                          'extension_separator_code_point': 46})
        elif line == 'Do not specify a half-width space at the end of the character string or just before each delimiter.':
            rules.append({'kind': 'file_path_character_position', 'subject': primary, 'code_point': 32,
                          'prohibited_positions': ['end', 'immediately_before_each_delimiter']})
        elif line == 'When the file name has an extension, specify the name without omitting the extension.':
            rules.append({'kind': 'file_extension_requirement', 'subject': primary,
                          'when_extension_present': True, 'must_include_complete_extension': True})
        elif line == 'Specify one character or more for the file name or folder path in addition to a delimiter.':
            rules.append({'kind': 'file_path_minimum_content', 'subject': primary,
                          'minimum_non_delimiter_characters': 1})
        elif line == 'Specify one character or more for the folder path in addition to a delimiter.':
            rules.append({'kind': 'file_path_minimum_content', 'subject': primary,
                          'minimum_non_delimiter_characters': 1})
        elif line == 'Do not add a delimiter at the end of a string.':
            rules.append({'kind': 'file_path_suffix_restriction', 'subject': primary,
                          'prohibited_suffix': 'path_delimiter'})
        elif line == 'Do not put a period (one-byte) at the end of a string or directly before each delimiter.':
            rules.append({'kind': 'file_path_character_position', 'subject': primary, 'code_point': 46,
                          'character_width': 'single_byte',
                          'prohibited_positions': ['end', 'immediately_before_each_delimiter']})
        elif new_name and line == 'Do not put a period (one-byte) at the end of a string.':
            rules.append({'kind': 'file_path_character_position', 'subject': primary, 'code_point': 46,
                          'character_width': 'single_byte', 'prohibited_positions': ['end']})
        elif line == 'Do not specify any file in the system folder ($MELPRJ$).':
            rules.append({'kind': 'file_path_system_folder_restriction', 'subject': primary,
                          'folder': {'name': 'MELPRJ', 'boundary_code_point': 36}, 'files_disallowed': True})
        elif line in {'Do not specify the system folder ($MELPRJ$) or any file in the system folder.',
                      'Do not specify the system folder ($MELPRJ$) or any file in it.'}:
            rules.append({'kind': 'file_path_system_folder_restriction', 'subject': primary,
                          'folder': {'name': 'MELPRJ', 'boundary_code_point': 36},
                          'folder_itself_disallowed': True, 'files_disallowed': True})
        elif legend and re.fullmatch(r'Up to (\d+) characters', line):
            rules.append({'kind': 'file_path_character_limit', 'subject': primary,
                          'component': 'folder_path' if target_type == 'folder' or path_only else 'complete_path',
                          'maximum_characters': int(re.search(r'Up to (\d+)', line)[1]), 'diagram_reference': int(legend[1])})
        elif legend and line in {'Use "/" or "\\" as delimiters for the folder path and file.',
                                'Use "/" or "\\" as delimiters for the folder path.'}:
            rules.append({'kind': 'file_path_delimiters', 'subject': primary, 'allowed_code_points': [47, 92]})
        elif legend and line == 'Can be omitted.':
            component = 'trailing_folder_delimiter' if (target_type == 'folder' or path_only) and legend[1] == '4' else 'leading_delimiter'
            rules.append({'kind': 'file_path_delimiter_requirement', 'subject': primary,
                          'component': component, 'required': False})
        elif line == 'When it is omitted, (1) is up to 252 characters.':
            rules.append({'kind': 'file_path_character_limit', 'subject': primary, 'component': 'complete_path',
                          'when_leading_delimiter_omitted': True, 'maximum_characters': 252})
        elif line == 'When it is omitted, (1) is up to 243 characters.' and (target_type == 'folder' or path_only):
            rules.append({'kind': 'file_path_character_limit', 'subject': primary, 'component': 'folder_path',
                          'when_leading_delimiter_omitted': True, 'maximum_characters': 243})
        elif legend and line == 'If the extension is omitted, ".BIN" or ".CSV" will be automatically added by the system.':
            rules.append({'kind': 'file_extension_default', 'subject': primary, 'when_extension_omitted': True,
                          'automatic_extension_names': ['BIN', 'CSV'], 'extension_separator_code_point': 46})
        elif legend and line == 'The folder path is up to 244 characters long (243 characters long when (3) is omitted).':
            rules.append({'kind': 'file_path_character_limit', 'subject': primary, 'component': 'folder_path',
                          'maximum_characters': 244,
                          'when_leading_delimiter_omitted': {'maximum_characters': 243}})
        elif legend and line == 'The delimiter between the folder path and the file name is not included in the number of characters of the folder path.':
            rules.append({'kind': 'file_path_length_counting', 'subject': primary, 'component': 'folder_path',
                          'excludes_trailing_file_name_delimiter': True})
        else:
            missing.append('file_path_subclause_%d' % index)
        if target_type:
            for rule in rules[before:]:
                rule.setdefault('when_target_type', target_type)
    if not rules:
        return None
    context_pages = []
    bound_subjects = [primary]
    if any(rule.get('when_target_type') for rule in rules):
        selectors = [item for item in _body_blocks(section) if item['kind'] == 'control_data' and
                     item.get('bound_subjects') and re.search(r'b0: Specify the type of data \(file or folder\)|'
                     r'b0: Target type setting Specify the type of data \(file or folder\)', item['text']) and
                     '0: File' in item['text'] and '1: Folder' in item['text']]
        identities = {tuple(sorted(item['bound_subjects'][0].items())) for item in selectors}
        if len(identities) == 1:
            selector = {**selectors[0]['bound_subjects'][0], 'bit': 0}
            bound_subjects.append(selector)
            context_pages = sorted({item['page'] for item in selectors})
            for rule in rules:
                if rule.get('when_target_type'):
                    rule['target_type_selector'] = selector
                    rule['when_selector_value'] = {'file': 0, 'folder': 1}[rule['when_target_type']]
        else:
            missing.append('file_path_target_type_selector_binding')
    remaining_glyphs = [glyph for glyph in value.get('unresolved_glyphs', [])
                        if glyph != 'F0C9' and not (glyph == 'F06F' and clause.startswith('to +[GLYPH-F06F]'))]
    return {'rules': rules, 'dimension': 'constraints.documented_control_data', 'full_clause_review': not missing,
            'additional_pages': context_pages,
            'value_updates': {'subjects': bound_subjects, 'control_field_binding_complete': not missing,
                              'source_formulas': [], 'unresolved_glyphs': remaining_glyphs,
                              'unparsed_input_qualification': bool(missing),
                              'unresolved_semantic_components': missing,
                              'notation_review_basis': 'original_PDF518_540_file_path_diagram_labels'}}


def _review_file_transfer_combinations(clause, *, block):
    if not block.get('file_combination_table'):
        return None
    mode_pattern = re.compile(r'\b(?P<mode>0000|0001|0100|0101|0110|0111|0120|0121|0130|0140)H:')
    modes = list(mode_pattern.finditer(clause))
    rows, missing, definitions = [], [], []
    selector_label = {'0': 'Word', '1': 'Even number of bytes',
                      '2': 'Unit of the data type specified by the execution/completion type', '3': 'Odd number of bytes'}
    quantity = r'(?P<count_unit>Double word|Word|Number of elements) \((?P<zero>0\s*\*1,\s*)?1 to (?P<high>\d+)\)\s*(?P<notes>(?:\*\d+\s*)*)'
    position = (r'(?P<position>(?:Double word|Word|Byte) \((?:00000000H to )?'
                r'(?:7FFFFFFFH|3FFFFFFFH|FFFFFFFEH|FFFFFFFFH)(?:,\s*FFFFFFFFH)?\)|Number of lines|Head/end\s*\*3)')
    payload = re.compile(quantity + r'\s*' + position + r'\s*(?P<actual_unit>Double word|Word|Byte|Number of elements)')
    mode_labels = {'0000': '16-bit binary data', '0001': '32-bit binary data',
                   '0100': 'Decimal (16-bit data)' if block['operation'] == 'read' else 'Decimal (signed 16-bit data)',
                   '0101': 'Decimal (unsigned 16-bit data)',
                   '0110': 'Decimal (signed 32-bit data)', '0111': 'Decimal (unsigned 32-bit data)',
                   '0120': 'Hexadecimal (16-bit data)', '0121': 'Hexadecimal (32-bit data)',
                   '0130': 'String (ASCII data)', '0140': 'Floating point real number (single-precision real number)'}
    for index, mode in enumerate(modes):
        fragment = _flat(clause[mode.end():modes[index + 1].start() if index + 1 < len(modes) else len(clause)])
        selectors = list(re.finditer(r'\b(?P<selector>[0-3]): ', fragment))
        if [item['selector'] for item in selectors] != ['0', '1', '2', '3']:
            missing.append('mode_%s_four_explicit_unit_selectors' % mode['mode'])
            continue
        mode_label = fragment[:selectors[0].start()].strip()
        for split_word, joined_word in [('16- bit', '16-bit'), ('32- bit', '32-bit'), ('single- precision', 'single-precision')]:
            mode_label = mode_label.replace(split_word, joined_word)
        if mode_label != mode_labels[mode['mode']]:
            missing.append('mode_%s_data_type_label' % mode['mode'])
        definitions.append({'value': int(mode['mode'], 16),
                            'format': 'binary' if mode['mode'].startswith('00') else 'csv',
                            'data_kind': 'binary32' if mode['mode'] == '0140' else
                                         'ascii_string' if mode['mode'] == '0130' else 'integer',
                            **({'width_bits': 16 if mode['mode'] in {'0000', '0100', '0101', '0120'} else 32}
                               if mode['mode'] not in {'0130', '0140'} else {}),
                            **({'radix': 16 if mode['mode'] in {'0120', '0121'} else 10}
                               if mode['mode'].startswith('01') and mode['mode'] not in {'0130', '0140'} else {}),
                            **({'signed': 'unsigned' not in mode_label} if 'signed' in mode_label else {})})
        items = []
        for number, selector in enumerate(selectors):
            data = _flat(fragment[selector.end():selectors[number + 1].start() if number + 1 < len(selectors) else len(fragment)])
            label = selector_label[selector['selector']]
            if not data.startswith(label):
                missing.append('mode_%s_unit_%s_label' % (mode['mode'], selector['selector']))
                continue
            data = data[len(label):].strip()
            items.append((int(selector['selector']), data))
        for unit, data in items:
            base = {'mode_value': int(mode['mode'], 16), 'unit_value': unit}
            if unit == 0 and not data and (1, '(Cannot be specified)') in items:
                rows.append({**base, 'permitted': False, 'binding_basis': 'shared_cannot_be_specified_cell'})
                continue
            if data == '(Cannot be specified)':
                rows.append({**base, 'permitted': False})
                continue
            parsed = payload.fullmatch(data)
            if not parsed:
                missing.append('mode_%s_unit_%s_transfer_columns' % (mode['mode'], unit))
                continue
            units = {'Word': 'word', 'Double word': 'double_word', 'Byte': 'byte', 'Number of elements': 'element'}
            requested = {'minimum': 1, 'maximum': int(parsed['high']), 'unit': units[parsed['count_unit']],
                         'footnotes': [int(n) for n in re.findall(r'\*(\d+)', parsed['notes'])]}
            if parsed['zero']:
                requested['additional_allowed_values'] = [0]
                requested['zero_count_footnote'] = 1
            source_position = parsed['position']
            if source_position == 'Number of lines':
                location = {'unit': 'line'}
            elif source_position.startswith('Head/end'):
                location = {'meaning': 'beginning_or_end', 'footnote': 3}
            else:
                extent = re.fullmatch(r'(?P<unit>Double word|Word|Byte) \(00000000H to '
                                      r'(?P<high>7FFFFFFFH|3FFFFFFFH|FFFFFFFEH|FFFFFFFFH)'
                                      r'(?P<append>,\s*FFFFFFFFH)?\)', source_position)
                if not extent:
                    missing.append('mode_%s_unit_%s_position_range' % (mode['mode'], unit))
                    continue
                location = {'unit': units[extent['unit']], 'minimum': 0, 'maximum': int(extent['high'][:-1], 16)}
                if extent['append']:
                    location['additional_allowed_values'] = [0xFFFFFFFF]
            rows.append({**base, 'permitted': True, 'requested_count': requested, 'file_position': location,
                         'actual_count_unit': units[parsed['actual_unit']]})
    expected_modes = {0, 1} if modes and modes[0]['mode'] == '0000' else (
        {0x100, 0x110, 0x120, 0x121, 0x130, 0x140} if block['operation'] == 'read' else
        {0x100, 0x101, 0x110, 0x111, 0x120, 0x121, 0x130, 0x140})
    if {int(item['mode'], 16) for item in modes} != expected_modes:
        missing.append('all_printed_format_modes')
    if len(rows) != 4 * len(expected_modes):
        missing.append('complete_format_and_unit_matrix')
    return {'rules': [{'kind': 'file_transfer_format_matrix', 'operation': block['operation'],
                       'mode_selector': block['mode_selector'], 'unit_selector': block['unit_selector'],
                       'requested_count': block['count_subject'], 'file_position': block['position_subject'],
                       'file_position_storage_offsets': block['position_storage_offsets'],
                       'actual_count': block['actual_count_subject'], 'mode_definitions': definitions, 'rows': rows}],
            'dimension': 'parameters.documented_transfer_contract', 'full_clause_review': not missing,
            'value_updates': {'subjects': block['bound_subjects'], 'control_field_binding_complete': not missing,
                              'source_formulas': [], 'comparisons': [], 'ranges': [], 'numeric_values': [],
                              'unresolved_glyphs': [], 'unparsed_input_qualification': bool(missing),
                              'unresolved_semantic_components': missing}}


def _review_file_control(kind, clause, *, section, block, value):
    """The file-control tables explicitly name every selector and storage row."""
    if kind not in {'control_data', 'footnote'}:
        return None
    subjects = block.get('bound_subjects') or value.get('subjects') or []
    if kind == 'control_data':
        combination = _review_file_transfer_combinations(clause, block=block)
        if combination:
            return combination
        path_review = _review_file_path_parameter(clause, section=section, block=block, value=value)
        if path_review:
            return path_review

    def complete(rules, **updates):
        return {'rules': rules, 'dimension': 'constraints.documented_control_data',
                'value_updates': {'control_field_binding_complete': True, **updates}, 'full_clause_review': True}

    if kind == 'footnote':
        matrices, _skipped = _file_transfer_combination_blocks(section)
        count_refs = {json.dumps(item['count_subject'], sort_keys=True) for item in matrices}
        count = json.loads(next(iter(count_refs))) if len(count_refs) == 1 else None
        clean_note = _flat(clause.replace('word uni ts', 'word units').replace('write dat a', 'write data')
                           .replace('c reated', 'created').replace('execution/completi on', 'execution/completion')
                           .replace('s pecified', 'specified').replace('setting i s disabled', 'setting is disabled'))
        if count and clean_note == 'Up to 32767 points (when the file register (R) is used) when only devices are used.':
            return complete([{'kind': 'file_transfer_storage_count_limit', 'subject': count, 'maximum': 32767,
                              'unit': 'point', 'when': {'storage_form': 'devices_only', 'limiting_device_class': 'R'}}],
                            subjects=[count], source_formulas=[], ranges=[], numeric_values=[])
        odd_read = clean_note == 'Specify the number of read-target data in word units adding 1 to the number of odd byte data to be read.'
        odd_write = clean_note == ('To specify the number of request write data, add 1 byte to the number of odd byte data '
                                   'to be written, and specify the sum in word units.')
        if count and (odd_read or odd_write):
            return complete([{'kind': 'odd_byte_transfer_requested_count', 'subject': count,
                              'operation': 'read' if odd_read else 'write', 'source_quantity': 'odd_byte_data_count',
                              'add_padding_bytes': 1, 'specified_unit': 'word',
                              'specified_count': {'operation': 'divide', 'left': {'operation': 'add',
                                  'left': {'role': 'odd_byte_data_count'}, 'right': 1}, 'right': 2}}],
                            subjects=[count], source_formulas=[], ranges=[], numeric_values=[])
        zero_missing_pattern = (r'If the specified file does not exist and the number of write target data is 0, '
                                   r'a folder to up to the specified path will be created, and the '
                                   r'program will terminate normally without creating a file\.')
        zero_existing_pattern = (r'If the specified file exists and the number of request write data is 0, '
                                 r'the program will terminate normally with the number of actually written data to 0 point\.?')
        zero_parts = _clauses(clean_note)
        zero_conditions = [('absent' if re.fullmatch(zero_missing_pattern, part) else
                            'present' if re.fullmatch(zero_existing_pattern, part) else None) for part in zero_parts]
        if count and zero_parts and all(zero_conditions) and all(item['operation'] == 'write' for item in matrices):
            actual = matrices[0]['actual_count_subject']
            rules = []
            for state in zero_conditions:
                rule = {'kind': 'zero_count_file_write_outcome', 'requested_count': count,
                        'when': {'requested_count_equals': 0, 'file_exists': state == 'present'}, 'completion': 'normal'}
                if state == 'absent':
                    rule.update({'folder_path_created': True, 'file_created': False})
                else:
                    rule['actual_written_count'] = {'subject': actual, 'value': 0}
                rules.append(rule)
            result = complete(rules, subjects=[count, actual], source_formulas=[], ranges=[], numeric_values=[])
            result['dimension'] = 'effects.documented_file_operation'
            return result
        csv_append = re.fullmatch(r'The setting is enabled only when "01\*\*H: Writing data after converted to CSV format" '
                                  r'has been selected for execution/completion type \(d1\)\+0 and '
                                  r'"FFFFFFFFH: Added to the end of the file" has been specified for file position '
                                  r'\(d1\)\+4 and \(d1\)\+5\. When the setting is disabled, specify "0"\.', clean_note)
        write_settings = [item for item in _body_blocks(section) if item.get('bound_subjects') and
                          'b0: Write start position setting' in item['text']]
        if csv_append and len(write_settings) == 1:
            field = {**write_settings[0]['bound_subjects'][0], 'bit': 0}
            return complete([{'kind': 'control_field_activation', 'subject': field,
                              'enabled_only_when': {'all': [{'subject': {'operand': 'd1', 'offset': 0},
                                  'mask': 0xFF00, 'equals': 0x0100}, {'subject': {'operand': 'd1', 'offset': 4},
                                  'storage_word_offsets': [4, 5], 'equals': 0xFFFFFFFF}]},
                              'required_value_when_disabled': 0}], subjects=[field, {'operand': 'd1'}],
                            source_formulas=[], ranges=[], numeric_values=[], unresolved_glyphs=[])
        csv_position = (clean_note == 'The set value is from 00000000H to FFFFFFFEH (from the beginning of the file) '
                        'or FFFFFFFFH (added to the end of the file).')
        if matrices and csv_position and all(item['operation'] == 'write' for item in matrices):
            position = matrices[0]['position_subject']
            return complete([{'kind': 'csv_write_file_position', 'subject': position, 'storage_word_offsets': [4, 5],
                              'branches': [{'minimum': 0, 'maximum': 0xFFFFFFFE, 'position': 'beginning_of_file'},
                                           {'value': 0xFFFFFFFF, 'position': 'end_of_file'}]}],
                            subjects=[position], source_formulas=[], ranges=[], numeric_values=[])
        if clean_note == ('In the case of files/folders accessed in an environment (OS) other than the CPU module, '
                          'the range of values to be acquired depends on the environment (OS).'):
            return {'rules': [{'kind': 'file_status_value_domain_dependency',
                               'when': {'file_or_folder_access_environment': 'outside_CPU_module'},
                               'depends_on': 'external_operating_system'}],
                    'dimension': 'resources.environment_dependency', 'full_clause_review': True,
                    'value_updates': {'subjects': [], 'operand_binding_required': False}}

    meanings = {
        '16-bit binary data': {'format': 'binary', 'width_bits': 16},
        '32-bit binary data': {'format': 'binary', 'width_bits': 32},
        'Decimal (16-bit data)': {'format': 'csv', 'radix': 10, 'width_bits': 16},
        'Decimal (32-bit data)': {'format': 'csv', 'radix': 10, 'width_bits': 32},
        'Decimal (signed 16-bit data)': {'format': 'csv', 'radix': 10, 'width_bits': 16, 'signed': True},
        'Decimal (unsigned 16-bit data)': {'format': 'csv', 'radix': 10, 'width_bits': 16, 'signed': False},
        'Decimal (signed 32-bit data)': {'format': 'csv', 'radix': 10, 'width_bits': 32, 'signed': True},
        'Decimal (unsigned 32-bit data)': {'format': 'csv', 'radix': 10, 'width_bits': 32, 'signed': False},
        'Hexadecimal (16-bit data)': {'format': 'csv', 'radix': 16, 'width_bits': 16},
        'Hexadecimal (32-bit data)': {'format': 'csv', 'radix': 16, 'width_bits': 32},
        'String (ASCII data)': {'format': 'csv', 'encoding': 'ascii', 'data_type': 'string'},
        'Floating point real number (single-precision real number)': {'format': 'csv', 'data_type': 'binary32'},
    }
    if kind == 'control_data' and len(subjects) >= 1:
        primary = subjects[0]
        clean = _flat(clause.replace('|', ' ').replace('[GLYPH-F0C9]', ''))
        read_count = re.fullmatch(r'Number of data actually read The number of data actually read is set\. '
                                 r'The data unit is determined by (?P<unit>\([sdnm][0-9]?\)\+7) '
                                 r'\(Data type specification\)\. System', clean)
        written_count = re.fullmatch(r'Number of data actually The number of actually written data for the data '
                                    r'specified in (?P<data>\([sdnm][0-9]?\)) is stored\. System written '
                                    r'The unit of the value conforms to the data type specification in '
                                    r'(?P<unit>\([sdnm][0-9]?\)\+7)\.', clean)
        if read_count or written_count:
            matched = read_count or written_count
            unit = _subjects(matched['unit'])[0]
            rule = {'kind': 'system_control_result_quantity', 'subject': primary,
                    'quantity': 'actual_written_data_count' if written_count else 'actual_read_data_count',
                    'unit_selector': unit, 'set_by': 'system'}
            referenced = [primary, unit]
            if written_count:
                source = _subjects(matched['data'])[0]
                rule['source_data'] = source
                referenced.append(source)
            result = complete([rule], subjects=referenced, source_formulas=[], unresolved_glyphs=[])
            result['dimension'] = 'parameters.control_status_storage'
            return result
        if clause.startswith('File position '):
            parts = re.split(r'\[GLYPH-F0FB\]When ', clause)
            if len(parts) == 3 and _flat(parts[0]).strip('| ') == 'File position':
                binary = re.match(r'^"(?P<action>Reading|Writing) binary data" is specified by '
                                  r'(?P<mode>\([sdnm][0-9]?\)\+0)(?P<body>.*)$', parts[1])
                csv = re.match(r'^"(?:Reading data after converted to CSV format|Converting and writing data in CSV format)" '
                               r'is specified by (?P<mode>\([sdnm][0-9]?\)\+0)(?P<body>.*)$', parts[2])
                if binary and csv and binary['mode'] == csv['mode']:
                    binary_cells = [_flat(cell.replace('|', ' ')) for cell in binary['body'].split('•')]
                    csv_cells = [_flat(cell.replace('|', ' ')) for cell in csv['body'].split('•')]
                    binary_cells[0] = re.sub(r'\s+', ' ', binary_cells[0]).strip()
                    header_valid = binary_cells[0] == '00000000H to User' and not csv_cells[0]
                    binary_cells, csv_cells = binary_cells[1:], csv_cells[1:]
                    if binary_cells:
                        binary_cells[0] = re.sub(r'\s+FFFFFFFFH$', '', binary_cells[0])
                    middle = re.fullmatch(r'00000001H to FFFFFFFEH: From the specified position '
                                          r'\(The (?:data unit is determined by|unit conforms to the data type specification in) '
                                          r'(?P<unit>\([sdnm][0-9]?\)\+7)\.\)', binary_cells[1] if len(binary_cells) == 3 else '')
                    reading = binary['action'] == 'Reading'
                    expected_last = 'FFFFFFFFH: Cannot be specified.' if reading else 'FFFFFFFFH: Added to the end of the file.'
                    expected_csv = ['00000000H: From the beginning of the file',
                                    '00000001H to FFFFFFFEH: From the specified row',
                                    'FFFFFFFFH: From the position where the previous reading ends'] if reading else [
                                    '00000000H to FFFFFFFEH: From the beginning of the file',
                                    'FFFFFFFFH: Added to the end of the file.']
                    if (header_valid and len(binary_cells) == 3 and middle and
                            binary_cells[0] == '00000000H: From the beginning of the file' and
                            binary_cells[2] == expected_last and csv_cells == expected_csv):
                        mode, unit = _subjects(binary['mode'])[0], _subjects(middle['unit'])[0]
                        binary_branches = [{'value': 0, 'position': 'beginning_of_file'},
                                           {'minimum': 1, 'maximum': 0xFFFFFFFE, 'position': 'specified_offset', 'unit_selector': unit},
                                           {'value': 0xFFFFFFFF, 'position': 'invalid' if reading else 'end_of_file'}]
                        csv_branches = ([{'value': 0, 'position': 'beginning_of_file'},
                                         {'minimum': 1, 'maximum': 0xFFFFFFFE, 'position': 'specified_row'},
                                         {'value': 0xFFFFFFFF, 'position': 'previous_read_end'}] if reading else [
                                        {'minimum': 0, 'maximum': 0xFFFFFFFE, 'position': 'beginning_of_file'},
                                        {'value': 0xFFFFFFFF, 'position': 'end_of_file'}])
                        return complete([{'kind': 'conditional_file_position', 'subject': primary,
                                          'storage_word_offsets': list(dict.fromkeys(block.get('printed_offsets') or [])),
                                          'mode_selector': mode, 'binary': binary_branches, 'csv': csv_branches,
                                          'operation': 'read' if reading else 'write'}],
                                        subjects=[primary, mode, unit], source_formulas=[])
        if clause.startswith('Execution/completion Specify the execution type.'):
            entries = []
            for match in re.finditer(r'\b([0-9A-F]{4})H:\s*(' + '|'.join(
                    re.escape(label) for label in sorted(meanings, key=len, reverse=True)) + r')', clause):
                entries.append({'value': int(match[1], 16), **meanings[match[2]]})
            literal_codes = {int(item, 16) for item in re.findall(r'\b([0-9A-F]{4})H\b', clause)}
            if len(entries) >= 8 and literal_codes == {item['value'] for item in entries} and all(
                    token in clause for token in ('00**H:', '01**H:', 'User')):
                return complete([{'kind': 'control_value_enumeration', 'subject': primary,
                                  'allowed_values': entries, 'set_by': 'user',
                                  'format_masks': [{'mask': '00**H', 'format': 'binary'},
                                                   {'mask': '01**H', 'format': 'csv'}]}],
                                subjects=[primary], source_formulas=[],
                                numeric_values=[{'value': item['value'], 'encoding': 'hexadecimal'} for item in entries])
        if clause.startswith('Number of read-target Specify the number of data to be read.'):
            selector = re.search(r'execution/completion type in (?P<mode>\([sdnm][0-9]?\)\+0) '
                                 r'and data type specification in (?P<unit>\([sdnm][0-9]?\)\+7)', clause)
            source_types = [('0000H', '16-bit binary data'), ('0001H', '32-bit binary data'),
                            ('0100H', 'decimal (16-bit data)'), ('0110H', 'decimal (32-bit data)'),
                            ('0120H', 'hexadecimal (16-bit data)'), ('0121H', 'hexadecimal (32-bit data)'),
                            ('0130H', 'a string (ASCII data)'),
                            ('0140H', 'a floating point real number (single-precision real number)')]
            branches = []
            for code, label in source_types:
                match = re.search(r'When ' + re.escape(label) + r' is specified:\s*'
                                  r'(?P<unit>In units of words|In units of double words|Number of elements) '
                                  r'\((?P<low>\d+) to (?P<high>\d+)\)(?P<notes>(?:\*\d+)*)', clause)
                if match:
                    branches.append({'when_mode': int(code[:-1], 16), 'minimum': int(match['low']),
                                     'maximum': int(match['high']), 'unit': {'In units of words': 'word',
                                        'In units of double words': 'double_word', 'Number of elements': 'element'}[match['unit']],
                                     'footnotes': [int(number) for number in re.findall(r'\*(\d+)', match['notes'])]})
            if selector and len(branches) == 8:
                mode, unit = _subjects(selector['mode'])[0], _subjects(selector['unit'])[0]
                return complete([{'kind': 'conditional_control_interval', 'subject': primary,
                                  'mode_selector': mode, 'data_unit_selector': unit,
                                  'branches': branches, 'set_by': 'user',
                                  'branch_footnotes_required': True}],
                                subjects=[primary, mode, unit], source_formulas=[])
        if clause.startswith('Number of request write data Specify the number of data to be requested to write.'):
            text = _flat(clause.replace('|', ' ').replace('[GLYPH-F0FB]', ''))
            intro = re.match(r'^Number of request write data Specify the number of data to be requested to write\. '
                             r'The processing unit and setting range vary depending on Execution/completion type set in '
                             r'(?P<mode>\([sdnm][0-9]?\)\+0) and data type specification in '
                             r'(?P<unit>\([sdnm][0-9]?\)\+7)\.', text)
            source_types = [('0000H', '16-bit binary data'), ('0001H', '32-bit binary data'),
                            ('0100H', 'decimal (signed 16-bit data)'), ('0101H', 'decimal (unsigned 16-bit data)'),
                            ('0110H', 'decimal (signed 32-bit data)'), ('0111H', 'decimal (unsigned 32-bit data)'),
                            ('0120H', 'hexadecimal (16-bit data)'), ('0121H', 'hexadecimal (32-bit data)'),
                            ('0130H', 'a string (ASCII data)'),
                            ('0140H', 'a floating point real number (single-precision real number)')]
            branches, remainder = [], text[intro.end():] if intro else text
            for code, label in source_types:
                pattern = re.compile(r'When ' + re.escape(label) + r' is specified:\s*'
                                     r'(?P<unit>In units of words|In units of double words|Number of elements) '
                                     r'\((?P<low>\d+) to (?P<high>\d+)\)\s*(?P<notes>(?:\*\d+)*)')
                matches = list(pattern.finditer(remainder))
                if len(matches) != 1:
                    break
                match = matches[0]
                branches.append({'when_mode': int(code[:-1], 16), 'minimum': int(match['low']),
                                 'maximum': int(match['high']), 'unit': {'In units of words': 'word',
                                    'In units of double words': 'double_word', 'Number of elements': 'element'}[match['unit']],
                                 'footnotes': [int(number) for number in re.findall(r'\*(\d+)', match['notes'])]})
                remainder = pattern.sub('', remainder)
            zero = ('When 0 is specified • If the specified file exists, the data will be written to 0 point, '
                    'and the program will terminate normally. • If the specified file does not exist, only a folder '
                    'will be created, and the program will terminate without creating a file.')
            if intro and len(branches) == 10 and zero in remainder:
                remainder = remainder.replace(zero, '')
                for header in ('When "Writing binary data" is specified by ' + intro['mode'],
                               'When "Converting and writing data in CSV format" is specified by ' + intro['mode'] + ': Number of elements'):
                    remainder = remainder.replace(header, '')
                remainder = re.sub(r'1 to 65535, 0 User$', '', remainder.strip())
                if not remainder.replace('•', '').strip():
                    mode, unit = _subjects(intro['mode'])[0], _subjects(intro['unit'])[0]
                    return complete([{'kind': 'conditional_control_interval', 'subject': primary,
                                      'mode_selector': mode, 'data_unit_selector': unit,
                                      'branches': branches, 'additional_allowed_values': [0], 'set_by': 'user',
                                      'branch_footnotes_required': True,
                                      'zero_count_behaviour': [{'when_file_exists': True, 'data_written_points': 0,
                                                                'completion': 'normal'},
                                                               {'when_file_exists': False, 'folder_created': True,
                                                                'file_created': False, 'terminates': True}]}],
                                    subjects=[primary, mode, unit], source_formulas=[])
        if clause.startswith('Maximum number of') and all(phrase in clause for phrase in (
                '0130H: String (ASCII data)', 'Total size of the characters in the element (in units of words)',
                'When data other than', 'Fixed to 0')):
            selector = re.search(r'is specified by (\([sdnm][0-9]?\)\+0)', clause)
            if selector and len(value.get('ranges') or []) == 1:
                mode = _subjects(selector[1])[0]
                interval = value['ranges'][0]
                return complete([{'kind': 'conditional_control_quantity', 'subject': primary, 'selector': mode,
                                  'branches': [{'when': {'equals': 0x0130},
                                                'quantity': 'total_character_storage_words_per_element',
                                                'allowed_values': [0],
                                                'interval': {'minimum': interval['minimum'], 'maximum': interval['maximum']}},
                                               {'when': {'not_equals': 0x0130}, 'required_value': 0}], 'set_by': 'user'}],
                                subjects=[primary, mode], source_formulas=[])
        if clause.startswith('Data type specification') and all(phrase in clause for phrase in (
                '0: Word', '1: Even number of bytes', '2: Unit of the data type specified by',
                '3: Odd number of bytes', 'can be specified only when')):
            selector = re.search(r'by (\([sdnm][0-9]?\)\+0)\.', clause)
            if selector and re.search(r'0000H: 16-bit binary data.*0100H: Decimal \((?:signed )?16-bit data\)', clause):
                mode = _subjects(selector[1])[0]
                return complete([{'kind': 'control_unit_enumeration', 'subject': primary,
                                  'allowed_values': [{'value': 0, 'unit': 'word'},
                                                     {'value': 1, 'unit': 'byte', 'byte_parity': 'even'},
                                                     {'value': 2, 'unit': {'from_mode_selector': mode}},
                                                     {'value': 3, 'unit': 'byte', 'byte_parity': 'odd'}],
                                  'conditional_availability': {'values': [0, 1, 3], 'mode_selector': mode,
                                                               'allowed_modes': [0x0000, 0x0100]},
                                  'set_by': 'user'}], subjects=[primary, mode], source_formulas=[])
        if clause.startswith('Number of columns') and re.search(
                r'When "(?:Reading|Writing) binary data" is specified by \([sdnm][0-9]?\)\+0, set 0\.', clause):
            selector = re.search(r'is specified by (\([sdnm][0-9]?\)\+0)', clause)
            if selector and 'Other than 0: Number of specified columns' in clause and '65535' in clause:
                mode = _subjects(selector[1])[0]
                reading = 'Reading binary data' in clause
                if reading and not all(phrase in clause for phrase in ('ignoring line separators', 'read as one row')):
                    return None
                if not reading and 'Only one row is written.' not in clause:
                    return None
                return complete([{'kind': 'control_column_count', 'subject': primary, 'mode_selector': mode,
                                  'binary': {'required_value': 0},
                                  'csv': {'minimum': 0, 'maximum': 65535,
                                          'zero': {'column_selection': 'all', 'rows': 1,
                                                   'line_separators': 'ignored' if reading else 'one_output_row'},
                                          'positive': 'number_of_columns'}, 'set_by': 'user'}],
                                subjects=[primary, mode], source_formulas=[])
    if kind == 'footnote':
        text = clause.replace('O dd', 'Odd').replace('s pecified', 'specified')
        limit = re.fullmatch(r'When "1: Even number of bytes" or "3: Odd number of bytes" is specified by '
                            r'(?P<unit>\([sdnm][0-9]?\)\+7) \(Data type specification\), '
                            r'the setting range of (?P<count>\([sdnm][0-9]?\)\+\d+) '
                            r'\(Number of (?:read-target|request write) data\) is (?P<low>\d+) to (?P<high>\d+)\.', text)
        if limit:
            unit, count = _subjects(limit['unit'])[0], _subjects(limit['count'])[0]
            return complete([{'kind': 'conditional_control_interval', 'subject': count,
                              'unit_selector': unit, 'when_values': [1, 3],
                              'minimum': int(limit['low']), 'maximum': int(limit['high'])}],
                            subjects=[count, unit], source_formulas=[])
        padding = re.fullmatch(r'Even when "3: Odd number of bytes" is specified by '
                              r'(?P<unit>\([sdnm][0-9]?\)\+7) \(Data type specification\), '
                              r'specify the number in units of words\. Add one byte to the odd number of bytes to be '
                              r'(?:read|written) and set the number in (?P<count>\([sdnm][0-9]?\)\+\d+) '
                              r'\(Number of (?:read-target|request write) data\)\.', text)
        if padding:
            unit, count = _subjects(padding['unit'])[0], _subjects(padding['count'])[0]
            return complete([{'kind': 'odd_byte_word_count', 'subject': count, 'unit_selector': unit,
                              'when_value': 3, 'byte_padding': 1, 'stored_unit': 'word',
                              'quantity': {'operation': 'divide', 'left': {'operation': 'add',
                                           'left': {'role': 'odd_byte_count'}, 'right': 1}, 'right': 2}}],
                            subjects=[count, unit], source_formulas=[])
    return None


def _signature_bindings(section, record):
    aliases = {name: item['formal_name'] for name, item in _argument_aliases(section).items() if item.get('formal_name')}
    for _page, layout in section.get('page_layouts') or ():
        for source, formal in re.findall(r'\b(Set value|d|U/H) displays as (Value|Coil|U)', layout):
            aliases[source.replace(' ', '_').casefold()] = formal
    bindings = []
    for fact in record.get('signature_facts') or ():
        if fact.get('status') != 'source_verified' or fact.get('verification') not in {None, 'source_checked'}:
            continue
        parameters = fact.get('value', {}).get('operands') or fact.get('value', {}).get('parameters') or []
        parameters = [item for item in parameters if item.get('name') not in {'EN', 'ENO'}]
        if not parameters:
            continue
        languages = fact.get('scope', {}).get('languages') or ([fact['language']] if fact.get('language') else [])
        pages = [page for ref in fact.get('sources') or () for page in ref.get('pdf_pages', [])]
        documented = fact.get('value', {}).get('documented_page')
        if documented is not None:
            pages = [documented]
        for language in languages:
            bindings.append({'language': language, 'parameters': parameters, 'pages': pages,
                             'conditions': copy.deepcopy(fact.get('conditions') or []), 'fact_id': fact.get('id'),
                             'aliases': aliases if language in {'st', 'fbd'} else {}})
    return bindings


def _zero_transfer_witnesses(section, record, specs, name, language):
    """A table's productive count range does not exclude a reviewed zero no-op.

    Join only a complete, unconditional, same-section source relation. This is
    not an opcode allowlist or an executable implementation of the no-op.
    """
    witnesses = []
    owner = str(record.get('opcode') or '').upper()
    section_pages = set(section.get('pages') or ())
    for candidate in specs:
        value = candidate.get('value') or {}
        if (candidate.get('status') != 'source_verified' or
                language not in candidate.get('languages', ()) or
                not value.get('full_source_relation_complete') or
                value.get('condition_extraction_basis') != 'complete_authored_rule' or
                value.get('operand_binding') != 'resolved' or
                value.get('context_binding') != 'resolved' or candidate.get('depends_on_keys') or
                value.get('unresolved_items') or value.get('unresolved_glyphs') or
                not set(candidate.get('pages') or ()).issubset(section_pages) or
                not candidate.get('pages')):
            continue
        forms = candidate.get('scope_forms') or value.get('source_forms') or value.get('form_conditions')
        if forms and owner not in {str(form).upper() for form in forms}:
            continue
        eligible = True
        for condition in candidate.get('conditions') or ():
            if condition.get('kind') == 'operand_form':
                eligible &= set(condition.get('required_operands') or ()) == {name}
            elif condition.get('kind') == 'declared_form':
                eligible &= owner in {str(form).upper() for form in condition.get('forms') or ()}
            else:
                eligible = False
        rules = value.get('rules') or []
        if not eligible or len(rules) != 1:
            continue
        rule = rules[0]
        if (set(rule) == {'kind', 'subject', 'value', 'operation_error', 'processing'} and
                rule.get('kind') == 'zero_transfer_count' and
                rule.get('subject') == {'operand': name} and
                type(rule.get('value')) is int and rule['value'] == 0 and
                rule.get('operation_error') is False and rule.get('processing') is False):
            witnesses.append(candidate)
    return witnesses


def _formal_numeric_specs(section, record, specs):
    """Reuse the reviewed call order; never substitute ST order for Ladder.

    Whole-word ANY16/ANY32 domains describe storage representation. Independent
    scalar-input intervals use the existing numeric-ranges evaluator. Block
    fields, strings, BCD and incomplete antecedents remain explicit knowledge.
    """
    bindings = _signature_bindings(section, record)
    if not bindings:
        return specs
    scalar_types = {'INT', 'DINT', 'LINT', 'UINT', 'UDINT', 'ULINT', 'REAL', 'LREAL', 'ANY_NUM'}
    storage_types = {'ANY16', 'ANY16_S', 'ANY16_U', 'ANY32', 'ANY32_S', 'ANY32_U', 'ANY64',
                     'ANY64_S', 'ANY64_U', 'ANY_BIT', 'WORD', 'DWORD', 'LWORD', 'ANY_SIMPLE'}
    count_names = {'n', 'n1', 'n2', 'n3', 'n4', 'm', 'm1', 'm2', 'm3', 'set_value', 'value'}
    result, replacements = [], {}
    for spec in specs:
        value = spec['value']
        intervals = [rule for rule in value.get('rules') or () if rule['kind'] == 'allowed_interval']
        if (not intervals or value['source_kind'] not in {'operand_ranges', 'processing_qualifications'} or
                not value['full_source_relation_complete'] or
                any(rule['kind'] not in {'allowed_interval', 'input_data_type'} for rule in value.get('rules') or ())):
            result.append(spec)
            continue
        subjects = {interval.get('subject', {}).get('operand') for interval in intervals}
        if len(subjects) != 1 or None in subjects or any(interval.get('subject', {}).get('offset') is not None for interval in intervals):
            result.append(spec)
            continue
        name = next(iter(subjects))
        forms = {form.upper() for form in value.get('form_conditions') or ()}
        record_forms = {str(record['opcode']).upper()} if record.get('opcode') else set()
        if forms and not record_forms.intersection(forms):
            result.append(spec)
            continue
        candidates = []
        for binding in bindings:
            target = binding['aliases'].get(name.casefold(), name)
            selected = [(index, item) for index, item in enumerate(binding['parameters'])
                        if str(item.get('name', '')).replace(' ', '_').casefold() == target.casefold()]
            if len(selected) == 1 and binding['language'] in spec['languages']:
                candidates.append((binding, *selected[0]))
        # A source-page match identifies the witness, but is not a runtime
        # overload selector. Different argument orders need a formal selector
        # before either variant can become a numeric gate for this language.
        ambiguous_languages = {item[0]['language'] for item in candidates if len({
            tuple(p.get('name') for p in other[0]['parameters']) for other in candidates
            if other[0]['language'] == item[0]['language']}) > 1}
        matched_page = [item for item in candidates if spec['page'] in item[0]['pages']]
        if matched_page:
            candidates = matched_page
        clones = []
        for language in dict.fromkeys(item[0]['language'] for item in candidates):
            choices = [item for item in candidates if item[0]['language'] == language]
            identities = {(item[1], str(item[2].get('type')), item[2].get('direction'),
                           tuple(p.get('name') for p in item[0]['parameters'])) for item in choices}
            if len(identities) != 1:
                continue
            binding, index, parameter = choices[0]
            ptype = str(parameter.get('type', '')).upper()
            direction = parameter.get('direction')
            scalar = direction in {'input', 'in_out'} and (ptype in scalar_types or
                     (ptype in storage_types and name.casefold() in count_names))
            if scalar and language in ambiguous_languages:
                continue
            storage = ptype in storage_types and not scalar
            if not (scalar or storage or direction == 'output') or 'bcd_domain' in value.get('predicates', []):
                continue
            clone = copy.deepcopy(spec)
            clone['key'] += '_' + ('numeric_' if scalar else 'storage_') + language
            clone['languages'] = [language]
            clone['conditions'] = [condition for condition in clone.get('conditions') or ()
                                   if condition.get('kind') not in {'operand_form', 'declared_form'}]
            for condition in binding['conditions']:
                if condition not in clone['conditions']:
                    clone['conditions'].append(condition)
            clone['value']['source_ranges'] = copy.deepcopy(value.get('ranges') or [])
            clone['value']['parameter_binding'] = {'source_name': name, 'parameter_name': parameter['name'],
                                                 'operand_index': index, 'language': language, 'type': ptype,
                                                 'direction': direction,
                                                 'basis': 'reviewed_language_signature',
                                                 'signature_fact': binding['fact_id']}
            if scalar:
                clone['dimension'] = 'constraints.numeric_ranges'
                clone['value']['ranges'] = [{'operand_index': index, 'minimum': item['minimum'], 'maximum': item['maximum']}
                                             for item in intervals]
                clone['value']['rule'] = 'fx5_documented_scalar_input_range'
                witnesses = _zero_transfer_witnesses(section, record, specs, name, language)
                if (witnesses and all(type(item['minimum']) is int and item['minimum'] == 1
                                     and type(item['maximum']) is int and item['maximum'] >= 1
                                     for item in intervals)):
                    # Preserve source_ranges and the productive source rule;
                    # only the admissible runtime domain includes the zero case.
                    for boundary in clone['value']['ranges']:
                        boundary['minimum'] = 0
                    clone['value']['numeric_domain_basis'] = 'productive_range_union_source_verified_zero_noop'
                    clone['value']['zero_noop_sources'] = [
                        {'key': item['key'], 'pages': list(item['pages']), 'operand': name,
                         'value': 0, 'operation_error': False, 'processing': False}
                        for item in witnesses]
                    clone['pages'] = sorted(set(clone['pages']) | {
                        page for item in witnesses for page in item['pages']})
            else:
                clone['dimension'] = 'parameters.storage_domain'
                clone['value']['domain_role'] = 'output_value' if direction == 'output' else 'stored_bit_pattern'
                clone['value']['input_numeric_gate'] = False
            clones.append(clone)
        if clones:
            remaining = [language for language in spec['languages']
                         if language not in {clone['languages'][0] for clone in clones}]
            if remaining:
                retained = copy.deepcopy(spec)
                retained['key'] += '_unbound_language_forms'
                retained['languages'] = remaining
                clones.append(retained)
            result.extend(clones)
            replacements[spec['key']] = [clone['key'] for clone in clones]
        else:
            result.append(spec)
    for spec in result:
        if spec.get('depends_on_keys'):
            dependencies = []
            for key in spec['depends_on_keys']:
                dependencies.extend(replacements.get(key, [key]))
            spec['depends_on_keys'] = list(dict.fromkeys(dependencies))
    return result


def condition_specs(section, record, *, include_other_forms=False):
    """Return typed, page-bound conditions for one declared instruction form."""
    errors, notes = _blocks(section)
    declared, note_bindings = _operand_bindings(section, record)
    aliases = _argument_aliases(section)
    specs = []
    body = _body_blocks(section)
    groups = [('error', errors), ('footnote', notes)]
    groups.extend((kind, [block for block in body if block['kind'] == kind]) for kind in CONDITION_SCOPES[2:])
    for kind, blocks in groups:
        for ordinal, block in enumerate(blocks):
            context_subjects, context_key = [], None
            clauses = _clauses(block['text']) if kind in {'error', 'precautions', 'execution_timing',
                                                         'processing_qualifications'} else [block['text']]
            in_example = False
            for clause_index, clause in enumerate(clauses):
                role = _content_role(clause)
                if kind == 'control_data' and (role in {'source_navigation', 'worked_example'} or
                        (not block.get('bound_subjects') and role != 'technical_condition_candidate')):
                    continue
                if kind not in {'error', 'operand_ranges', 'control_data'}:
                    if role == 'worked_example':
                        in_example = True
                    if in_example or role != 'technical_condition_candidate':
                        continue
                value = _technical_value(re.sub(r'\*\d+', '', clause) if kind == 'operand_ranges' else clause)
                _reviewed_math(section, block, kind, clause, value, declared)
                if any(rule['kind'] == 'conditional_result' for rule in value.get('rules') or ()):
                    value['rules'] = [rule for rule in value['rules'] if rule['kind'] != 'undefined_result']
                if kind != 'error' and not re.search(r'\b(?:not|cannot|invalid|prohibited|error)\b', clause, re.I):
                    mentioned = [rule for rule in value.get('rules') or ()
                                 if rule['kind'] in {'invalid_program_context', 'program_placement'}]
                    if mentioned:
                        value['program_context_mentions'] = [rule.get('context') or
                            {'start': rule.get('construct_start'), 'end': rule.get('construct_end')} for rule in mentioned]
                        value['rules'] = [rule for rule in value.get('rules') or () if rule not in mentioned]
                        value['technical_relation_resolved'] = bool(value['rules'])
                subjects = value.get('subjects') or []
                context_reference = re.search(r'^(?:The specified (?:file name|folder path)|When a file is specified|'
                                               r'The system folder|Make sure that these devices|these instructions|'
                                               r'In (?:this|that) case|FX5(?:S|UJ|U|UC)(?:/FX5(?:S|UJ|U|UC))* CPU module)', clause, re.I)
                if not subjects and context_subjects and context_reference:
                    subjects = list(context_subjects)
                    value['subjects'] = subjects
                    value['binding_basis'] = 'explicit_antecedent_in_same_source_row'
                    value['antecedent_key'] = context_key
                    for interval in value.get('ranges') or ():
                        if len(subjects) == 1:
                            interval['subject'] = subjects[0]
                    for comparison in value.get('comparisons') or ():
                        if comparison.get('subject') == {'role': 'unbound_source_value'} and len(subjects) == 1:
                            comparison['subject'] = subjects[0]
                    if kind == 'error' and re.match(r'^FX5(?:S|UJ|U|UC)(?:/FX5(?:S|UJ|U|UC))* CPU module\s+K?\d+ to \d+$', clause):
                        value['rules'] = [{'kind': 'value_outside_interval', **interval} for interval in value.get('ranges') or ()]
                        value['technical_relation_resolved'] = bool(value['rules'])
                primary_subjects = block.get('bound_subjects') or []
                if primary_subjects:
                    subjects = primary_subjects + [subject for subject in subjects if subject not in primary_subjects]
                    value['subjects'] = subjects
                    value['binding_basis'] = block['binding_basis']
                    value['primary_subjects'] = primary_subjects
                if (record.get('opcode') == 'OUT' and 'd' in declared and any(
                        rule['kind'] == 'output_condition_matrix' for rule in value.get('rules') or ())):
                    subjects = [{'operand': 'd'}]
                    value['subjects'] = subjects
                    value['binding_basis'] = 'declared_OUT_destination_and_condition_matrix'
                if kind == 'control_data' and primary_subjects:
                    for interval in value.get('ranges') or ():
                        interval['subject'] = primary_subjects[0]
                        interval['conditional_branch_binding'] = 'not_established'
                    # The column's row operand is the storage subject. Values
                    # mentioned by branch selectors are not substituted for it.
                    # Keep branch-dependent limits as qualifications until the
                    # complete selection expression has been bound.
                    value['rules'] = [{'kind': 'control_data_qualification', 'subjects': primary_subjects,
                                       'bounds': value.get('ranges', []),
                                       'selector_conditions': value.get('source_selector_conditions', []),
                                       'declared_value_meanings': value.get('declared_value_meanings', []),
                                       'branch_binding': 'not_established'}]
                    value['technical_relation_resolved'] = bool(value.get('ranges') or value.get('comparisons') or
                                                                 value.get('declared_value_meanings'))
                    value['control_field_binding_complete'] = _control_rules(block, value)
                    if value['control_field_binding_complete'] and value.get('unresolved_glyphs') == ['F0C9']:
                        # The printed dash is a non-value column in these
                        # explicitly authored reserved/status rows.
                        value.pop('unresolved_glyphs')
                if kind == 'operand_ranges':
                    if not (value.get('ranges') or value.get('comparisons') or value.get('source_formulas')):
                        continue
                    for interval in value.get('ranges') or ():
                        # The literal row label supplies the subject even when
                        # the PDF's table-cell extractor dropped that label.
                        if len(primary_subjects) == 1:
                            interval['subject'] = primary_subjects[0]
                    if not value.get('source_formulas') and value.get('ranges'):
                        value['rules'] = [{'kind': 'allowed_interval', **interval} for interval in value['ranges']]
                        value['technical_relation_resolved'] = True
                if block.get('form'):
                    value['form_conditions'] = _literal_forms(block['form'], section)
                    if '[GLYPH-F06F]' in block['form'] and not any('[GLYPH-' in form for form in value['form_conditions']):
                        value['unresolved_glyphs'] = [glyph for glyph in value.get('unresolved_glyphs', []) if glyph != 'F06F']
                if block.get('printed_offsets'):
                    value['printed_control_offsets'] = block['printed_offsets']
                if kind == 'footnote' and not subjects:
                    subjects = [{'operand': name} for name in sorted(
                        note_bindings.get((block['page'], block['domain'], block['number']), set()))]
                    if subjects:
                        value['subjects'] = subjects
                        value['binding_basis'] = 'operand_table_footnote_reference'
                        for rule in value.get('rules') or ():
                            if not rule.get('subjects') and 'subjects' in rule:
                                rule['subjects'] = subjects
                        if len(subjects) == 1:
                            for interval in value.get('ranges') or ():
                                interval.setdefault('subject', subjects[0])
                            for comparison in value.get('comparisons') or ():
                                if comparison.get('subject') == {'role': 'unbound_source_value'}:
                                    comparison['subject'] = subjects[0]
                            # Rules are independent dictionaries; keep the
                            # binding on the relation as well as its inventory.
                            for rule in value.get('rules') or ():
                                if rule.get('subject') == {'role': 'unbound_source_value'} or (
                                        rule['kind'] in {'allowed_interval', 'value_outside_interval'} and not rule.get('subject')):
                                    rule['subject'] = subjects[0]
                data_operands = declared - {'en', 'eno'}
                if not subjects and len(data_operands) == 1 and re.search(r'\b(?:the )?specified device\b', clause, re.I):
                    # A literal one-operand declaration makes this reference
                    # unique. A multi-operand instruction remains unbound.
                    subjects = [{'operand': next(iter(data_operands))}]
                    value['subjects'] = subjects
                    value['binding_basis'] = 'specified_device_and_unique_declared_operand'
                    for rule in value.get('rules') or ():
                        if 'subjects' in rule and not rule['subjects']:
                            rule['subjects'] = subjects
                        if rule.get('subject') == {'role': 'unbound_source_value'}:
                            rule['subject'] = subjects[0]
                if primary_subjects:
                    for rule in value.get('rules') or ():
                        if 'subjects' in rule and not rule['subjects']:
                            rule['subjects'] = primary_subjects
                if value.get('binding_basis') == 'explicit_antecedent_in_same_source_row':
                    for rule in value.get('rules') or ():
                        if 'subjects' in rule and not rule['subjects']:
                            rule['subjects'] = subjects
                        if len(subjects) == 1 and (rule.get('subject') == {'role': 'unbound_source_value'} or
                                                  (rule['kind'] in {'allowed_interval', 'value_outside_interval'} and not rule.get('subject'))):
                            rule['subject'] = subjects[0]
                review = (review_processing_clause(clause, section=section, block=block, value=value, declared=declared)
                          if kind == 'processing_qualifications' else
                          _review_auxiliary_clause(kind, clause, section=section, block=block, value=value, declared=declared))
                if kind == 'precautions':
                    precaution_review = review_precaution_clause(clause, section=section, block=block, value=value, declared=declared)
                    if precaution_review and (precaution_review.get('full_clause_review') or review is None):
                        review = precaution_review
                if kind == 'execution_timing' and (review is None or not review.get('full_clause_review')):
                    execution_review = review_execution_clause(clause, section=section, block=block, value=value, declared=declared)
                    if execution_review:
                        review = execution_review
                if review is None and kind in {'execution_timing', 'footnote', 'precautions'}:
                    # Physical timing paragraphs can also contain the primary
                    # shift/transfer relation. Reuse the same exact authored
                    # clause review instead of duplicating its semantics.
                    review = review_processing_clause(clause, section=section, block=block, value=value, declared=declared)
                pwm_review = _review_pwm_clause(kind, clause, section=section, block=block, declared=declared)
                if pwm_review is not None:
                    review = pwm_review
                if review:
                    value.update(review.get('value_updates') or {})
                    value['rules'] = review['rules']
                    value['technical_relation_resolved'] = bool(review['rules'])
                    value['complete_clause_review'] = bool(review.get('full_clause_review'))
                    role = review.get('content_role') or role
                explicit_form_scope = bool(block.get('form') or (review and (
                    review.get('forms') or 'form_conditions' in (review.get('value_updates') or {}))))
                if value.get('form_conditions') and not explicit_form_scope:
                    # "When DHCMOV is used" can qualify access to a PWM
                    # field. A referenced instruction is not this fact's owner.
                    value['referenced_instruction_forms'] = value.pop('form_conditions')
                subjects = value.get('subjects') or []
                all_bound = bool(subjects) and all(subject['operand'].casefold() in declared for subject in subjects)
                used_aliases = [aliases[subject['operand'].casefold()] for subject in subjects
                                if subject['operand'].casefold() in aliases]
                if used_aliases:
                    value['source_operand_aliases'] = used_aliases
                variadic_groups = []
                optional_range_endpoints = set()
                for span in re.finditer(r'\((?P<base>[sdnm])(?P<first>\d+)\)\s+to\s+'
                                        r'\((?P=base)(?P<last>\d+)\)', clause):
                    endpoint = aliases.get(span['base'] + span['last']) or {}
                    if endpoint.get('variadic_group') != span['base']:
                        continue
                    variadic_groups.append({'base_name': span['base'], 'first_source_index': int(span['first']),
                                            'last_source_index': int(span['last']),
                                            'minimum_call_inputs': endpoint['minimum_inputs'],
                                            'maximum_call_inputs': endpoint['maximum_inputs'],
                                            'applies_to': 'supplied_call_arguments'})
                    optional_range_endpoints.update(span['base'] + str(index)
                                                    for index in range(int(span['first']), int(span['last']) + 1)
                                                    if index > endpoint['minimum_inputs'])
                if variadic_groups:
                    value['variadic_source_groups'] = variadic_groups
                numeric = bool(value.get('numeric_values') or value.get('ranges') or value.get('comparisons'))
                resolved = value.get('technical_relation_resolved', False)
                scoped_rules = {'allowed_device_classes', 'disallowed_device_classes', 'label_type_disallowed',
                                'label_not_permitted', 'label_requirement', 'label_type', 'constant_class',
                                'module_class', 'alignment_multiple', 'data_type', 'indexing'}
                scoped_rules.update({'output_mode_axis_restriction', 'disallowed_device_form', 'rounding_mode',
                                     'data_representation', 'flag_pulse_timing'})
                needs_operand = bool(subjects) or numeric or any(rule['kind'] in scoped_rules for rule in value.get('rules', []))
                if review and 'operand_binding_required' in value:
                    needs_operand = value['operand_binding_required']
                unresolved_context = (kind == 'control_data' and not value.get('control_field_binding_complete')) or value.get('unparsed_input_qualification', False) or any(
                    rule.get('source_condition') == 'not_fully_resolved' or
                    rule.get('consequent') == 'requires_effect_binding' or
                    'source_context_requires_binding' in rule.get('phases', [])
                    for rule in value.get('rules') or ())
                conditional_words = re.findall(r'\b(?:when|if|unless|until|while|during|after|before)\b', clause, re.I)
                if kind == 'operand_ranges':
                    # These are names of table fields, not conditional clauses.
                    # A literal row interval still supplies its complete bound.
                    label_text = re.sub(r'\b(?:Data before conversion|Positioning address after an interrupt input)\b', '', clause)
                    conditional_words = re.findall(r'\b(?:when|if|unless|until|while|during|after|before)\b', label_text, re.I)
                rule_kinds = {rule['kind'] for rule in value.get('rules') or ()}
                contextual = rule_kinds - {'manual_cross_reference', 'manual_dependency', 'device_state_qualification'}
                context_is_bound = bool(contextual) and contextual <= CONTEXT_BOUND_RULES
                if review and review.get('full_clause_review'):
                    context_is_bound = True
                if conditional_words and not context_is_bound:
                    # Recognising one comparison or noun does not account for
                    # an entire conditional statement. Keep its unresolved
                    # antecedent visible instead of asserting an unconditional
                    # error trigger or effect from a matched fragment.
                    uncomplicated_range = (kind == 'error' and len(conditional_words) == 1 and
                                           conditional_words[0].lower() == 'when' and
                                           any(rule['kind'] == 'value_outside_interval' for rule in value.get('rules') or ()) and
                                           not re.search(r'\band\b|\bor\b|\bexcept\b', clause, re.I))
                    if not uncomplicated_range:
                        unresolved_context = True
                        value['unbound_condition_markers'] = list(dict.fromkeys(word.lower() for word in conditional_words))
                if kind == 'error' and block.get('has_action_column') and not block.get('cause_cell_bound'):
                    unresolved_context = True
                verified = resolved and (not needs_operand or all_bound) and not value.get('unresolved_glyphs') and not unresolved_context
                unresolved_items = []
                if not resolved:
                    unresolved_items.append('predicate_relation')
                if needs_operand and not all_bound:
                    unresolved_items.append('operand_binding')
                if value.get('unresolved_glyphs'):
                    unresolved_items.append('source_notation_interpretation')
                if unresolved_context:
                    unresolved_items.append('antecedent_or_consequent')
                value.update({'knowledge_kind': value.get('knowledge_kind', 'manual_condition'), 'source_kind': kind,
                              'source_page': block['page'], 'source_line': block['line'], 'clause_index': clause_index,
                              'source_role': role,
                              'source_representation': 'mathematical_notation' if value.get('source_formulas') else
                                                       'table_or_diagram' if '|' in clause else
                                                       'diagram_label' if not re.search(r'[a-z]{3,}', clause) else 'prose',
                              'operand_binding': 'resolved' if all_bound else 'not_established',
                              'operand_binding_required': needs_operand,
                              'relation_coverage': 'complete_source_relation' if verified else
                                                   'parsed_technical_components' if resolved else 'source_present_not_extracted',
                              'full_source_relation_complete': bool(verified),
                              'unresolved_items': unresolved_items,
                              'core_execution_capability': 'not_established'})
                if unresolved_context:
                    value['context_binding'] = 'not_fully_resolved'
                if kind == 'error':
                    # Reviewed merged rows can recover codes absent from the raw parser.
                    # Do not overwrite that independently checked result.
                    value['error_codes'] = list(((review or {}).get('value_updates') or {}).get('error_codes', block['codes']))
                    value['error_domain'] = block['error_domain']
                    value['diagnostic_registers'] = block['diagnostic_registers']
                    if block.get('operand_type_conditions'):
                        value['overload_operand_types'] = block['operand_type_conditions']
                    if block.get('diagnostic_operands'):
                        value['diagnostic_operands'] = block['diagnostic_operands']
                    if block.get('has_action_column'):
                        value['source_table_column'] = 'cause'
                        value['cause_cell_binding'] = 'resolved' if block.get('cause_cell_bound') else 'not_established'
                        value['corrective_action_column'] = 'separate_source_content_not_a_trigger'
                    markers = [int(number) for number in re.findall(r'\*(\d+)', clause)]
                    if markers:
                        value['referenced_footnotes'] = list(dict.fromkeys(markers))
                elif kind == 'footnote':
                    value['footnote'] = block['number']
                    value['source_block'] = block['domain']
                else:
                    value['source_block'] = block['domain']
                    value['source_line'] = block['line']
                    if block.get('source_table_column'):
                        value['source_table_column'] = block['source_table_column']
                        value['source_table_binding_basis'] = block.get('table_binding_basis')
                    if block.get('operand_type_conditions'):
                        value['overload_operand_types'] = block['operand_type_conditions']
                    markers = [int(number) for number in re.findall(r'\*(\d+)', clause)]
                    if markers:
                        value['referenced_footnotes'] = list(dict.fromkeys(markers))
                spec = {'key': 'condition_%s_p%d_r%d_c%d' % (kind, block['page'], ordinal, clause_index),
                        'dimension': ('constraints.operation_error' if value.get('error_domain') == 'operation_error'
                                      else 'constraints.completion_error') if kind == 'error' and verified else
                                     'constraints.documented_restriction' if verified else 'constraints.source_qualifications',
                        'value': value, 'status': 'source_verified' if verified else 'declared_unverified',
                        'languages': _source_languages(section, block['page'], clause), 'page': block['page'], 'pages': block['pages']}
                if review and review.get('additional_pages'):
                    spec['pages'] = sorted(set(spec['pages']) | set(review['additional_pages']))
                if review and review.get('languages'):
                    spec['languages'] = [language for language in spec['languages'] if language in review['languages']]
                if not spec['languages']:
                    raise ValueError('The authored clause language contradicts its manual section')
                if review and review.get('dimension') and (verified or review.get('preserve_unverified_dimension')):
                    spec['dimension'] = review['dimension']
                elif verified and kind != 'error':
                    relation_kinds = {rule['kind'] for rule in value.get('rules', [])}
                    if relation_kinds and relation_kinds <= {'conditional_result', 'function_result_matrix', 'output_condition_matrix'}:
                        spec['dimension'] = 'effects.documented_result'
                    elif relation_kinds == {'language_display_alias'}:
                        spec['dimension'] = 'parameters.display_alias'
                    elif relation_kinds and relation_kinds <= {'control_completion_status', 'control_data_ownership'}:
                        spec['dimension'] = 'parameters.control_status_storage'
                elif kind == 'execution_timing' and not verified:
                    spec['dimension'] = 'execution.source_qualifications'
                if review and review.get('status'):
                    spec['status'] = review['status']
                scope_forms = (review.get('forms') if review else None)
                if not scope_forms and explicit_form_scope:
                    scope_forms = value.get('form_conditions')
                if scope_forms:
                    spec['scope_forms'] = list(dict.fromkeys(scope_forms))
                    value['source_forms'] = list(spec['scope_forms'])
                    record_forms = {str(record['opcode']).upper()} if record.get('opcode') else set()
                    if record_forms and not record_forms.intersection(str(form).upper() for form in scope_forms) and not include_other_forms:
                        continue
                    if record_forms and not include_other_forms:
                        spec['scope_forms'] = [record['opcode']]
                conditions = []
                if subjects:
                    required_operands = list(dict.fromkeys(subject['operand'] for subject in subjects
                                                          if subject['operand'].casefold() not in optional_range_endpoints))
                    if required_operands:
                        conditions.append({'kind': 'operand_form', 'required_operands': required_operands})
                authored_conditions = bool(review and review.get('full_clause_review'))
                value['condition_extraction_basis'] = 'complete_authored_rule' if authored_conditions else 'partial_technical_heuristics'
                if value.get('device_state_conditions') and not authored_conditions:
                    conditions.extend(value['device_state_conditions'])
                if not authored_conditions:
                    conditions.extend(value.get('operand_device_conditions') or [])
                if value.get('form_conditions') and explicit_form_scope:
                    conditions.append({'kind': 'declared_form', 'forms': value['form_conditions']})
                if value.get('signedness_condition') and not authored_conditions:
                    conditions.append({'kind': 'data_signedness', 'value': value['signedness_condition']})
                if value.get('cpu_models') and not authored_conditions:
                    conditions.append({'kind': 'cpu_model', 'models': value['cpu_models']})
                if value.get('hardware_modules') and not authored_conditions:
                    conditions.append({'kind': 'hardware_dependency', 'modules': value['hardware_modules']})
                if value.get('versions') and not authored_conditions:
                    conditions.append({'kind': 'version_dependency', 'versions': value['versions'],
                                       'relation': value['version_relation']})
                if block.get('operand_type_conditions'):
                    conditions.append({'kind': 'operand_data_types', 'operands': block['operand_type_conditions']})
                if review and review.get('conditions'):
                    conditions.extend(condition for condition in review['conditions'] if condition not in conditions)
                if conditions:
                    spec['conditions'] = conditions
                specs.append(spec)
                if review and review.get('split_pwm_hardware_table'):
                    from plc.pwm_contract import PWM_AB_MINIMUM_TIMES
                    table = copy.deepcopy(spec)
                    table['key'] += '_hardware_table'
                    table['dimension'] = 'constraints.documented_restriction'
                    table['status'] = 'source_verified'
                    table['conditions'] = [{'kind': 'operand_form', 'required_operands': ['s1', 's2', 'd']}]
                    table['value'] = {'knowledge_kind': 'manual_behavior', 'condition_extraction_basis': 'complete_authored_rule',
                        'technical_relation_resolved': True, 'full_source_relation_complete': True,
                        'context_binding': 'resolved', 'operand_binding': 'resolved', 'operand_binding_required': True,
                        'subjects': [{'operand': n} for n in ['s1', 's2', 'd']], 'unresolved_items': [],
                        'source_page': block['page'], 'source_kind': 'precautions', 'source_line': block.get('line', 0),
                        'clause_index': clause_index, 'source_block': 'precautions', 'split_from': spec['key'],
                        'rules': [{'kind': 'PWM_hardware_minimum_times', 'ON_time': {'operand': 's1'},
                                   'period': {'operand': 's2'}, 'rows': [dict(row) for row in PWM_AB_MINIMUM_TIMES],
                                   'unit_conversion': 'parameter_selected_time_to_us'}]}
                    specs.append(table)
                if subjects and value.get('binding_basis') != 'explicit_antecedent_in_same_source_row':
                    context_subjects, context_key = list(subjects), spec['key']
                if value.get('antecedent_key'):
                    spec['depends_on_keys'] = [value['antecedent_key']]
    for spec in specs:
        dependencies = []
        if spec['value'].get('control_function_selector_reference'):
            domains = [item['key'] for item in specs if item['value'].get('control_table_kind') == 'high_speed_function_numbers']
            dependencies.extend(domains)
            if not domains:
                spec['status'] = 'declared_unverified'
                spec['value']['full_source_relation_complete'] = False
                spec['value']['context_binding'] = 'not_fully_resolved'
                spec['value']['unresolved_items'].append('function_selector_domain_dependency')
        for number in spec['value'].get('referenced_footnotes') or ():
            source_block = 'operation_error' if spec['value']['source_kind'] == 'error' else spec['value'].get('source_block')
            matches = [note for note in specs if note['value']['source_kind'] == 'footnote'
                       and note['value']['footnote'] == number
                       and note['value']['source_block'] == source_block
                       and note['page'] in spec['pages']]
            dependencies.extend(note['key'] for note in matches)
        if dependencies:
            spec['depends_on_keys'] = list(dict.fromkeys([*spec.get('depends_on_keys', []), *dependencies]))
    specs = _formal_numeric_specs(section, record, specs)
    return _structured_ladder_condition_specs(section, specs)


def _structured_ladder_condition_specs(section, specs):
    """Keep PART5 structured-block conditions separate from native Ladder."""
    if not section.get('outline', '').startswith('PART 5 '):
        return specs
    result = []
    for source in specs:
        if 'ladder' not in source['languages']:
            result.append(source)
            continue
        other_languages = [language for language in source['languages'] if language != 'ladder']
        if other_languages:
            retained = copy.deepcopy(source)
            retained['languages'] = other_languages
            result.append(retained)
        clone = copy.deepcopy(source)
        if other_languages:
            clone['key'] += '_structured_ladder'
        clone['languages'] = ['ladder']
        mode = {'context': 'project_mode', 'equals': 'structured_ladder_block'}
        if mode not in clone.setdefault('conditions', []):
            clone['conditions'].append(mode)
        clone['value']['representation'] = 'structured_ladder_block'
        result.append(clone)

    def identity(spec):
        value = spec['value']
        return (value['source_kind'], value['source_page'], value['source_line'], value['clause_index'],
                value.get('source_table_column'))

    by_key = {spec['key']: spec for spec in specs}
    by_identity = {}
    for spec in result:
        by_identity.setdefault(identity(spec), []).append(spec)
    for spec in result:
        dependencies, missing = [], []
        for key in spec.get('depends_on_keys') or ():
            source = by_key.get(key)
            variants = by_identity.get(identity(source), []) if source else []
            compatible = [target['key'] for target in variants
                          if set(target['languages']).intersection(spec['languages'])]
            if compatible:
                dependencies.extend(compatible)
            else:
                missing.append(key)
        if dependencies or spec.get('depends_on_keys'):
            spec['depends_on_keys'] = list(dict.fromkeys(dependencies))
        if missing:
            # No ST/FBD dependency is silently attached to a Ladder fact.
            # Retain the missing language-specific relation as unknown.
            spec['value']['unresolved_dependency_language'] = list(dict.fromkeys(missing))
            spec['value']['full_source_relation_complete'] = False
            spec['value']['context_binding'] = 'not_fully_resolved'
            spec['value']['unresolved_items'] = list(dict.fromkeys([
                *spec['value'].get('unresolved_items', []), 'dependency_language_scope']))
            if spec['status'] != 'conflict':
                spec['status'] = 'declared_unverified'
    return result


def condition_inventory(section, record=None):
    """Progress metadata; original paragraphs are deliberately not returned."""
    errors, notes = _blocks(section)
    specs = condition_specs(section, record or {}, include_other_forms=True)
    # Language-specific formal parameter bindings may produce several facts
    # from one source clause. Progress counts source clauses, not those facts.
    source_specs = {}
    for spec in specs:
        value = spec['value']
        identity = (value['source_kind'], value['source_page'], value['source_line'], value['clause_index'],
                    value.get('source_table_column'))
        previous = source_specs.get(identity)
        # The same source clause can have several language bindings. A known
        # ST variant cannot hide a still-unbound Ladder dependency.
        rank = lambda item: (not item['value']['full_source_relation_complete'], item['status'] == 'conflict')
        if previous is None or rank(spec) > rank(previous):
            source_specs[identity] = spec
    specs = list(source_specs.values())
    non_conditions = []
    for block in [*notes, *_body_blocks(section)]:
        for clause in _clauses(block['text']):
            role = _content_role(clause)
            if role != 'technical_condition_candidate':
                non_conditions.append({'page': block['page'], 'line': block['line'], 'role': role,
                                       'source_kind': block.get('kind') or 'footnote'})
    scopes = {}
    for kind in CONDITION_SCOPES:
        selected = [spec for spec in specs if spec['value']['source_kind'] == kind and
                    spec['value']['source_role'] not in {'source_navigation', 'worked_example'}]
        unresolved = [spec for spec in selected if not spec['value']['technical_relation_resolved']
                      or spec['value'].get('context_binding') == 'not_fully_resolved'
                      or spec['value'].get('unresolved_glyphs')
                      or (spec['value']['operand_binding_required'] and spec['value']['operand_binding'] != 'resolved')]
        scopes[kind] = {'source_items': len(selected),
                        'source_verified': sum(spec['status'] == 'source_verified' for spec in selected),
                        'retained_qualifications': sum(spec['status'] == 'declared_unverified' for spec in selected),
                        'source_conflicts': sum(spec['status'] == 'conflict' for spec in selected),
                        'source_present_not_extracted': len(unresolved),
                        'pages': sorted({page for spec in selected for page in spec['pages']}),
                        'status': 'source_present_not_extracted' if unresolved else
                                  'source_conflict' if any(spec['status'] == 'conflict' for spec in selected) else
                                  'technical_components_extracted' if selected else 'source_not_observed',
                        'full_scope_complete': False}
    unresolved, conflicts = [], []
    for spec in specs:
        value = spec['value']
        if spec['status'] == 'conflict':
            conflicts.append({'key': spec['key'], 'page': spec['page'], 'source_line': value['source_line'],
                              'source_kind': value['source_kind'], 'dimension': spec['dimension'],
                              'rules': value.get('rules', []), 'source_disposition': 'source_conflict'})
        if value['source_role'] in {'source_navigation', 'worked_example'}:
            non_conditions.append({'page': spec['page'], 'line': value['source_line'],
                                   'role': value['source_role'], 'source_kind': value['source_kind']})
            continue
        reasons = []
        if not value['technical_relation_resolved']:
            reasons.append('technical_relation_not_parsed')
        if value.get('context_binding') == 'not_fully_resolved':
            reasons.append('conditional_branches_or_consequent_not_fully_bound')
        if value.get('unresolved_glyphs'):
            reasons.append('source_notation_requires_interpretation')
        if value.get('cause_cell_binding') == 'not_established':
            reasons.append('cause_and_action_columns_not_disentangled')
        if value['operand_binding_required'] and value['operand_binding'] != 'resolved' and (
                value.get('ranges') or value.get('comparisons') or value.get('numeric_values')):
            reasons.append('numeric_operand_binding_not_established')
        elif value['operand_binding_required'] and value['operand_binding'] != 'resolved':
            reasons.append('operand_subject_not_bound')
        if reasons:
            unresolved.append({'page': spec['page'], 'pages': spec['pages'], 'key': spec['key'],
                               'source_kind': value['source_kind'], 'source_line': value.get('source_line'),
                               'source_representation': value['source_representation'],
                               'subjects': value.get('subjects', []), 'detected_predicates': value.get('predicates', []),
                               'unresolved_glyphs': value.get('unresolved_glyphs', []),
                               'unbound_condition_markers': value.get('unbound_condition_markers', []),
                               'missing_components': (['predicate_relation'] if not value['technical_relation_resolved'] else []) +
                                   (['antecedent_or_consequent'] if value.get('context_binding') == 'not_fully_resolved' else []) +
                                   (['operand_subject'] if value['operand_binding_required'] and value['operand_binding'] != 'resolved' else []) +
                                   (['source_notation_interpretation'] if value.get('unresolved_glyphs') else []) +
                                   list(value.get('unresolved_semantic_components') or []),
                               'reasons': reasons, 'numeric_values': value.get('numeric_values', []),
                               'ranges': value.get('ranges', []), 'comparisons': value.get('comparisons', []),
                               'source_formulas': value.get('source_formulas', []),
                               'error_codes': value.get('error_codes', []), 'footnote': value.get('footnote')})
    return {'method': METHOD, 'error_rows': len(errors), 'footnotes': len(notes),
            'causes': len(specs), 'source_verified': sum(spec['status'] == 'source_verified' for spec in specs),
            'source_qualifications': sum(spec['status'] == 'declared_unverified' for spec in specs),
            'source_conflicts': conflicts, 'scopes': scopes, 'unresolved_predicates': unresolved,
            'non_condition_source_items': non_conditions,
            'error_table_domains': {'operation_error_rows': sum(row['error_domain'] == 'operation_error' for row in errors),
                                    'completion_status_error_rows': sum(row['error_domain'] == 'completion_status_error' for row in errors),
                                    'unknown_domain_rows': sum(row['error_domain'] == 'not_established' for row in errors),
                                    'separate_corrective_action_columns': [{'page': row['page'], 'codes': row['codes'],
                                                                           'cause_cell_bound': row.get('cause_cell_bound', False),
                                                                           'action_semantics': 'not_extracted_as_error_trigger'}
                                                                          for row in errors if row.get('has_action_column')]},
            'completeness_scope': {'full_section_conditions': 'not_established',
                                   'full_instruction_semantics': 'not_established',
                                   'primary_effect_coverage_implies_condition_completeness': False,
                                   'timing_diagram_interpretation': 'not_established',
                                   'core_execution_capability': 'not_established'}}

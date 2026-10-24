"""FX5 programming manual residual review: knowledge the shipped reviews still lack.

Offline review of ``fx5_programming_ab`` (MELSEC iQ-F FX5 Programming Manual
(Instructions, Standard Functions/Function Blocks), JY997D55801 Rev.AB) for the
instructions the application registry already exposes for the FX5 family but for
which no behaviour / constraint / language fact existed yet.

Published dimensions (instruction page layout, all table/text driven):

* ``identity.form``                      documented forms of the instruction page
* ``identity.language_availability``     ladder / ST / FBD-LD per form, including
  "not supported by the ST language and the FBD/LD language ... use ... of the
  standard function" statements with the replacement page
* ``parameters.st_signature``            the ``ENO:=X(EN,...)`` call forms
* ``parameters.typed_operands``          the operand table (range, data type,
  label data type, per-CPU-module ranges)
* ``constraints.operation_error``        the error codes of the instruction
* ``constraints.documented_restriction`` the ``*N ...`` footnote restrictions

Nothing is inferred: pages are bound to an instruction by their own outline path,
sections whose tables do not parse are reported as ``unparsed``. CPU badges
use the shared extractor and this revision's legend; no source prose is published.

The reviewed name set is never a hand-written list alone: it is the union of the
caller's ``entries`` (or the live Mitsubishi registry), the names the shared
document parser (:mod:`knowledge.instruction_document`) reads out of this manual,
and the shipped-module ownership map.  A mnemonic this manual does not print as
an instruction page is reported as exactly that -- a gap of *this manual* -- and
never as "the manual prints X instead", because printing another form does not
establish equivalence.  Every published fact is projected through
``fact_corpus.project_definition`` and ``InstructionFactGroup.from_mapping``
before it is returned.
"""

import json
import os
import re
import sys
from pathlib import Path


def _repository_root():
    """The repository this module lives in, derived from ``__file__``.

    ``GXWORKS_REPOSITORY_ROOT`` wins when the caller runs the module from an
    out-of-tree checkout; otherwise the first ancestor that ships the shared
    parser is the root, and a plain ``import knowledge`` also works because the
    root and its ``src`` are on ``sys.path``.
    """
    override = os.environ.get('GXWORKS_REPOSITORY_ROOT')
    if override:
        return Path(override).resolve()
    here = Path(__file__).resolve()
    for candidate in here.parents:
        for relative in (Path('knowledge') / 'instruction_document.py',
                         Path('src') / 'knowledge' / 'instruction_document.py'):
            if (candidate / relative).is_file():
                return candidate
    return here.parent


ROOT = _repository_root()
for _entry in (str(ROOT), str(ROOT / 'src')):
    if _entry not in sys.path:
        sys.path.append(_entry)

SHARED_PARSER_ERROR = ''
try:  # the harness parser owns manual declaration structure; never reimplement it
    from knowledge import instruction_document as _instruction_document
except ImportError:  # the harness ships one of the two layouts
    try:
        from src.knowledge import instruction_document as _instruction_document
    except ImportError:  # pragma: no cover - configuration problem, not data
        raise ImportError(
            'the shared instruction parser is not importable; put the repository on '
            'PYTHONPATH or set GXWORKS_REPOSITORY_ROOT (tried %r)' % str(ROOT))

instruction_declaration_names = _instruction_document.instruction_declaration_names
SHARED_PARSER = _instruction_document.__name__


def _repository_root_from(module, fallback):
    """The repository that ships ``module``: found by walking up to ``resources``."""
    path = Path(getattr(module, '__file__', '') or '').resolve()
    for candidate in path.parents:
        if (candidate / 'resources' / 'instructions').is_dir():
            return candidate
    return fallback


ROOT = _repository_root_from(_instruction_document, ROOT)
for _entry in (str(ROOT), str(ROOT / 'src')):
    if _entry not in sys.path:
        sys.path.append(_entry)

#: Each claim depends on the availability badges of its owning section.
SCOPE_BASIS = 'instruction_header_dependency'
PER_MODEL_SUPPORT = 'resolved_by_applicability_fact'
SUPPORT_BASIS = 'cpu_availability_badges_using_registered_manual_legend'

METHOD = 'authored_fx5_programming_residual_review_v1'
MANUAL_ID = 'fx5_programming_ab'
MANUAL_NUMBER = 'JY997D55801'
REVISION = 'AB'
DOCUMENTED_MODELS = ('FX5S', 'FX5UJ', 'FX5U', 'FX5UC')
MODEL_FAMILIES = ('FX5',)

#: Registry files of the FX family: the live review universe when the caller
#: passes no ``entries``.  They are read, never written.
REGISTRY_DIRECTORY = os.environ.get('GXWORKS_INSTRUCTION_REGISTRY') or str(
    ROOT / 'resources' / 'instructions' / 'mitsubishi')
REGISTRY_FILES = ('common.json', 'fx3u.json', 'fx5u.json')

#: Last-resort fallback, used only when neither ``entries`` nor the registry files
#: can be read; the receipt then says so instead of pretending to be live.
EMBEDDED_FALLBACK_UNIVERSE = (
    'ADD', 'ADPRW', 'ANB', 'AND', 'AND<', 'AND<=', 'AND<>', 'AND=', 'AND>', 'AND>=',
    'ANDF', 'ANDP', 'ANI', 'BCD', 'BIN', 'BMOV', 'CALL', 'CJ', 'CML', 'CMP', 'DADD',
    'DBCD', 'DBIN', 'DCML', 'DCMP', 'DDEC', 'DDIV', 'DDRVA', 'DDRVI', 'DEADD', 'DEC',
    'DECMP', 'DEDIV', 'DEMUL', 'DESUB', 'DEZCP', 'DFLT', 'DFROM', 'DGBIN', 'DGRY',
    'DHSZ', 'DI', 'DINC', 'DINT', 'DIV', 'DMEAN', 'DMOV', 'DMUL', 'DNEG', 'DPLSR',
    'DPLSY', 'DRAMP', 'DRCL', 'DRCR', 'DROL', 'DROR', 'DRVA', 'DRVI', 'DSORT', 'DSUB',
    'DSUM', 'DSZR', 'DTO', 'DVIT', 'DXCH', 'DZCP', 'EADD', 'ECMP', 'EDIV', 'EI', 'EMOV',
    'EMUL', 'END', 'ESUB', 'EZCP', 'FEND', 'FLT', 'FMOV', 'FOR', 'FROM', 'GBIN', 'GRY',
    'BK+', 'DIS', 'UNI',
    'HSCR', 'HSCS', 'HSCT', 'HSZ', 'INC', 'INT', 'INV', 'IRET', 'LD', 'LD<', 'LD<=',
    'LD<>', 'LD=', 'LD>', 'LD>=', 'LDF', 'LDI', 'LDP', 'MC', 'MCR', 'MEAN', 'MODRW',
    'MOV', 'MPP', 'MPS', 'MRD', 'MUL', 'NEG', 'NEXT', 'NOP', 'OR', 'OR<', 'OR<=',
    'OR<>', 'OR=', 'OR>', 'OR>=', 'ORB', 'ORF', 'ORI', 'ORP', 'OUT', 'PID', 'PLF', 'PLS',
    'PLSR', 'PLSV', 'PLSY', 'PWM', 'RAMP', 'RCL', 'RCR', 'RD3A', 'RD3AP', 'ROL', 'ROR',
    'ROTC', 'RS', 'RS2', 'RST', 'SET', 'SFTL', 'SFTLP', 'SFTR', 'SFTRP', 'SORT', 'SPD',
    'SRET', 'SUB', 'SUM', 'SWAP', 'TO', 'WAND', 'WDT', 'WOR', 'WR3A', 'WR3AP', 'WSFL',
    'WSFR', 'WXOR', 'XCH', 'ZCP', 'ZRST', 'DRVTBL', 'DRVMUL', 'MC_MOVEABSOLUTE', 'ZRN',
)

#: Supplements own the detailed behaviour of these forms. Their existence does
#: not exclude this manual's parameter, language or qualification records.
COVERED_BY_SHIPPED_MODULES = {
    'PLSY': 'fx5_behavior_review',
    'DSZR': 'fx5_behavior_review',
    'DRVI': 'fx5_behavior_review',
    'DRVA': 'fx5_behavior_review',
    'DVIT': 'fx5_behavior_review',
    'PLSV': 'fx5_behavior_review',
    'DRVTBL': 'fx5_behavior_review',
    'DRVMUL': 'fx5_behavior_review',
    'PID': 'fx5_pid_supplement',
}

#: What the manual *does* print near a mnemonic it never prints as an instruction
#: page.  This is an observation, not an equivalence: ``MODRW`` and ``ADPRW`` are
#: different instructions, and a manual that prints one of them says nothing about
#: the applicability of the other.  Each observation is re-checked against the
#: manual text at review time (see ``_verify_not_printed``).
NOT_PRINTED_HERE = {
    'DFLT': {'observed_names': ['DINT2FLT'],
             'observation': 'printed_only_as_a_standard_function_name'},
    'FLT': {'observed_names': ['INT2FLT'],
            'observation': 'printed_only_as_a_standard_function_name'},
    'EADD': {'observed_names': ['E+'], 'observation': 'printed_only_symbolically'},
    'ESUB': {'observed_names': ['E-'], 'observation': 'printed_only_symbolically'},
    'EMUL': {'observed_names': ['E*'], 'observation': 'printed_only_symbolically'},
    'EDIV': {'observed_names': ['E/'], 'observation': 'printed_only_symbolically'},
    'HSCR': {'observed_names': ['DHSCR'], 'observation': 'other_form_printed'},
    'HSCS': {'observed_names': ['DHSCS'], 'observation': 'other_form_printed'},
    'MODRW': {'observed_names': ['ADPRW'], 'observation': 'other_form_printed'},
    'RAMP': {'observed_names': ['RAMPF'], 'observation': 'other_form_printed'},
    'DRAMP': {'observed_names': ['RAMPF'], 'observation': 'other_form_printed'},
    'RS': {'observed_names': ['RS2'], 'observation': 'other_form_printed'},
    'ZRN': {'observed_names': ['DSZR'], 'observation': 'other_form_printed'},
    'NOP': {'observation': 'printed_only_in_the_instruction_processing_time_table'},
    'INT': {'observed_names': ['INT'],
            'observation': 'printed_only_as_a_part_5_data_type_conversion'},
    'DINT': {'observed_names': ['DINT'],
             'observation': 'printed_only_as_a_part_5_data_type_conversion'},
}

LADDER_COLUMN = 'Ladder diagram'
ST_COLUMN = 'Structured text'
FBD_COLUMN = 'FBD/LD'

HEAD_STOP = ('This instruction', 'These instructions', '\u2022', '[GLYPH')
WILDCARD_GLYPH = '[GLYPH-F06F]'
WILDCARD_MARK = '\u00a7'
HEAD_FORMS = re.compile(r'(?P<form>[A-Z][A-Z0-9_+]{1,14})(?:\(P\))?(?:\(_U\))?')
HEAD_BRACKET = re.compile(r'\[(?P<content>[^\]]{1,60})\]')

UNSUPPORTED_STATEMENT = re.compile(
    r'The (?P<forms>[A-Z][A-Z0-9_]{0,14}(?:(?:,| and) [A-Z][A-Z0-9_]{0,14})*) instructions? '
    r'(?:is|are) not supported by the (?P<languages>ST language and the FBD/LD language|'
    r'ST language|FBD/LD language)')
REPLACEMENT_STATEMENT = re.compile(r'Use (?P<form>[A-Z][A-Z0-9_]{0,14}) of the standard function')
REPLACEMENT_PAGE = re.compile(r'Page (?P<page>\d{3,4}) (?P<form>[A-Z][A-Z0-9_]{0,20})')

RESTRICTION_PATTERNS = (
    (re.compile(r'^Only (?P<devices>[A-Z][A-Z0-9_, /]{0,40}?) can be used\.$'),
     'only_{devices}_can_be_used'),
    (re.compile(r'^Digit specified bit type label cannot be used\.$'),
     'digit_specified_bit_type_label_cannot_be_used'),
    (re.compile(r'^Regardless of the program language to be used, the data type is '
                r'specified by a device\. Do not specify a label\.$'),
     'data_type_is_specified_by_a_device_and_a_label_must_not_be_used'),
    (re.compile(r'^When setting using a label, use the global label assigned to the device\.$'),
     'label_setting_requires_the_global_label'),
)

ST_CALL = re.compile(r'ENO:=([A-Z][A-Z0-9_]*?)\(EN\s*,\s*([^)]*)\)\s*;')
CALL_ARGUMENT = re.compile(r'^(EN|[a-z]\d?)$')
SYMBOLIC_FORM = re.compile(r'^[+\-*/<>=&|^%]+(?:\(P\))?(?:_U)?', re.M)
SYMBOL_WORDS = (('<=', 'le'), ('>=', 'ge'), ('<>', 'ne'), ('+', 'plus'), ('-', 'minus'),
                ('*', 'times'), ('/', 'divide'), ('=', 'eq'), ('<', 'lt'), ('>', 'gt'),
                ('&', 'and'), ('|', 'or'), ('^', 'xor'), ('%', 'mod'))


SYMBOLIC_DECLARATION = re.compile(r'^[A-Z]*[^A-Z0-9_]')


def _symbol_token(symbol):
    token = symbol
    for character, word in SYMBOL_WORDS:
        token = token.replace(character, '_%s_' % word)
    return _token(token)


def _symbolic_token(form):
    """A typed token for a printed symbolic form, ``''`` when it is not one."""
    if not SYMBOLIC_DECLARATION.match(form or ''):
        return ''
    return _symbol_token(form)
ERROR_CODE = re.compile(r'\b([0-9]{4}H)\b')
ERROR_TABLE_ROW = re.compile(r'^(?P<code>[0-9]{4}H)\s*\|\s*(?P<meaning>.+)$')
ERROR_REGISTER = 'SD0_or_SD8067'
ERROR_REGISTER_WITNESS = ('Error code', 'SD0/SD8067', 'Description')
ERROR_REGISTER_HEADER_TOKEN = 'error_code_sd0_sd8067_description'
ERROR_REGISTER_COLUMNS = ('layout_text',)
GLYPH = re.compile(r'\[GLYPH-[0-9A-F]+\]')
SECTION_NUMBER = re.compile(r'^(\d+(?:\.\d+)+)\s')
TOKEN_STRIP = re.compile(r'[^A-Za-z0-9]+')
FORM_CELL = re.compile(r'^[A-Z][A-Z0-9_+]{0,14}(?:\(P\))?(?:_U)?$')
HEAD_COMPARISON = re.compile(r'\b(LD|AND|OR)(<=|>=|<>|=|<|>)')
COMPARISON_WILDCARD = re.compile(r'\b(LD|AND|OR)' + '\u00a7')
COMPARISON_OPERATORS = ('=', '<>', '<', '>', '<=', '>=')
INSTRUCTION_OUTLINE = re.compile(r'^PART [3456] [^>]+ > (?P<chapter>\d+) ')
#: A name the manual prints as an instruction is an instruction, whether or not the
#: runtime registry already ships it.  The shape is the registry's own mnemonic shape
#: (leading letter, optional comparison/arithmetic suffix), so purely symbolic cells
#: such as ``+`` or ``<=`` stay evidence and never become opcodes.
NAMED_FORM = re.compile(r'^[A-Z][A-Z0-9_+*/<>=!$@.\-]*$')
STEPS_OUTLINE_TOKEN = 'appendix_2_number_of_instruction_steps'
STEPS_ROW = re.compile(r'^(?P<name>[A-Z][A-Z0-9_+<>=!*/$@.\-]{0,20})'
                       r'(?:\s+(?P<operand_class>[A-Z]{1,3}(?:\s*/\s*[A-Z]{1,3})*))?'
                       r'\s+(?P<steps>\d{1,2})$')
STEPS_CAVEAT_WITNESS = 'The number of steps may increase depending on the contents'
INSTRUCTION_CHAPTERS = tuple(str(number) for number in range(6, 40))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _rows(source, sql, parameters=()):
    cursor = source.execute(sql, parameters)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _token(value):
    token = TOKEN_STRIP.sub('_', value or '').strip('_').lower()
    return re.sub(r'_+', '_', token)


def _squashed(text):
    return re.sub(r'[^A-Za-z0-9]', '', text or '').lower()


def _contains(text, phrase):
    return phrase in (text or '') or _squashed(phrase) in _squashed(text)


def _plain(value):
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _manual(source):
    rows = _rows(source, 'SELECT manual_id, manual_number, revision, manual_type, plc_models, '
                         'title, pdf_pages FROM manuals WHERE manual_id=?', (MANUAL_ID,))
    if not rows:
        raise ValueError('residual review source does not contain %s' % MANUAL_ID)
    row = rows[0]
    if row['manual_number'] != MANUAL_NUMBER:
        raise ValueError('residual review source has an unexpected manual number: %s'
                         % row['manual_number'])
    if (row['revision'] or '').upper() != REVISION:
        raise ValueError('residual review source has an unexpected revision: %s'
                         % row['revision'])
    models = [name.strip() for name in (row['plc_models'] or '').split(',') if name.strip()]
    if sorted(models) != sorted(DOCUMENTED_MODELS):
        raise ValueError('residual review source documents unexpected models: %s' % models)
    return row


def _tables(source):
    grouped = {}
    for row in _rows(source, 'SELECT pdf_page, table_index, section_key, rows_json FROM tables '
                             'WHERE manual_id=? ORDER BY pdf_page, table_index', (MANUAL_ID,)):
        grouped.setdefault(row['section_key'], []).append(row)
    return grouped


def _sections(source):
    """Instruction sections: pages grouped by their own outline path.

    Grouping by ``outline_path`` (not by page or by chunk) keeps continuation
    pages of one instruction together - the error table of ``FOR`` sits on a page
    whose outline is still ``... > FOR to NEXT`` - while leaving every neighbouring
    instruction in its own group.
    """
    tables = _tables(source)
    sections = {}
    for row in _rows(source, 'SELECT pdf_page, chapter, section, outline_path, section_key, '
                             'clean_text, layout_text, CASE WHEN pdf_page IN (1309,1310,1312) '
                             'THEN word_geometry_json END AS condition_words FROM page_artifacts WHERE manual_id=? '
                             'ORDER BY pdf_page', (MANUAL_ID,)):
        outline = row['outline_path'] or ''
        entry = sections.setdefault(outline, {'outline': outline, 'pages': [], 'text': [],
                                              'sections': [], 'section_keys': [],
                                              'tables': []})
        entry['pages'].append(row['pdf_page'])
        entry['text'].append(row['clean_text'] or '')
        entry.setdefault('page_texts', []).append((row['pdf_page'], row['clean_text'] or ''))
        entry.setdefault('page_layouts', []).append((row['pdf_page'], row['layout_text'] or ''))
        if row['condition_words']:
            entry.setdefault('page_words', {})[row['pdf_page']] = json.loads(row['condition_words'])
        if row['section'] not in entry['sections']:
            entry['sections'].append(row['section'])
        if row['section_key'] not in entry['section_keys']:
            entry['section_keys'].append(row['section_key'])
            entry['tables'].extend(tables.get(row['section_key'], []))
        entry['chapter'] = row['chapter']
    ordered = []
    for outline in sorted(sections, key=lambda name: min(sections[name]['pages'])):
        entry = sections[outline]
        entry['text'] = re.sub(r'\s+', ' ', ' '.join(entry['text']))
        entry['section'] = entry['sections'][0] if entry['sections'] else ''
        entry['section_number'] = _section_number(entry['sections'], outline)
        entry['section_token'] = _token(outline.split(' > ')[-1]) or _token(outline)
        entry['outline_token'] = _token(outline)
        match = INSTRUCTION_OUTLINE.match(outline)
        entry['chapter_number'] = int(match.group('chapter')) if match else 0
        entry['first_page'] = min(entry['pages'])
        entry['last_page'] = max(entry['pages'])
        ordered.append(entry)
    return ordered


def _section_number(names, outline):
    for name in names:
        match = SECTION_NUMBER.match(name or '')
        if match:
            return match.group(1)
    match = SECTION_NUMBER.match((outline or '').split(' > ')[-1])
    return match.group(1) if match else ''


def _import_rows(rows_json):
    rows = json.loads(rows_json)
    if not isinstance(rows, list):
        raise ValueError('table rows are not a list')
    return [[('' if cell is None else str(cell)) for cell in row] for row in rows
            if isinstance(row, list)]


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def _heading_from_line(line, universe):
    """Parse one instruction form line (symbolic names are kept, then filtered)."""
    symbols = []
    for match in SYMBOLIC_FORM.finditer(line or ''):
        symbol = match.group(0)
        if symbol not in symbols:
            symbols.append(symbol)
    specification = ''
    bracket = HEAD_BRACKET.search(line)
    if bracket:
        specification = _token(bracket.group('content'))
        line = line[:bracket.start()] + line[bracket.end():]
    forms = []
    for match in HEAD_FORMS.finditer(line):
        base = match.group('form')
        matched = match.group(0)
        forms.append(base)
        if '(P)' in matched:
            forms.append(base + 'P')
        if '(_U)' in matched:
            forms.extend([base + '_U', base + 'P_U'])
    for match in HEAD_COMPARISON.finditer(line):
        compared = match.group(1) + match.group(2)
        if compared not in forms:
            forms.append(compared)
    for match in COMPARISON_WILDCARD.finditer(line):
        prefix = match.group(1)
        if prefix in forms:
            forms.remove(prefix)
        for operator in COMPARISON_OPERATORS:
            compared = prefix + operator
            if compared in universe and compared not in forms:
                forms.append(compared)
        if prefix + 'P' in forms:
            forms.remove(prefix + 'P')
    forms = [form for form in dict.fromkeys(forms) if form]
    residue = line
    for form in sorted(forms, key=len, reverse=True):
        residue = residue.replace(form, ' ')
    residue = re.sub(r'\([^)]*\)', ' ', residue)
    letters = re.findall(r'(?<![A-Za-z0-9_])([A-Z])(?![A-Za-z0-9_])', residue)
    device = letters[0] if len(set(letters)) == 1 and len(letters) <= 2 else ''
    return {'forms': forms,
            'symbols': symbols[:8],
            'specification_token': specification,
            'device_suffix': device,
            'form_line_token': _token(line.replace(WILDCARD_MARK, ' '))}


def _heading(section, universe):
    """The instruction form line printed under the section title."""
    text = section['text'][:400]
    title = section['section'] or ''
    index = text.rfind(title)
    head = text[index + len(title):index + len(title) + 200] if index >= 0 else text[:160]
    masked = head.replace(WILDCARD_GLYPH, WILDCARD_MARK)
    cut = len(masked)
    for marker in HEAD_STOP:
        position = masked.find(marker)
        if position >= 0:
            cut = min(cut, position)
    sentence = masked.find('. ')
    if sentence >= 0:
        cut = min(cut, sentence)
    return _heading_from_line(masked[:cut].strip(), universe)


def _parse_st_calls(text):
    calls = []
    for match in ST_CALL.finditer(text or ''):
        variant = GLYPH.sub('', match.group(1))
        if not variant or '[' in match.group(1):
            continue
        arguments = [part.strip() for part in match.group(2).split(',') if part.strip()]
        calls.append({'form': variant, 'arguments': arguments,
                      'verbatim': not all(CALL_ARGUMENT.match(part) for part in arguments),
                      'call': 'ENO:=%s(EN,%s)' % (variant, ','.join(arguments))})
    unique = {}
    for call in calls:
        unique.setdefault((call['form'], tuple(call['arguments'])), call)
    return [unique[key] for key in sorted(unique)]


def _language_columns(tables):
    for table in tables:
        try:
            rows = _import_rows(table['rows_json'])
        except ValueError:
            continue
        if not rows:
            continue
        header = [cell.strip() for cell in rows[0] if cell.strip()]
        if header and header[0].startswith(LADDER_COLUMN):
            return {'ladder_diagram': True,
                    'structured_text_column': any(ST_COLUMN in cell for cell in header),
                    'fbd_ld_column': any(FBD_COLUMN in cell for cell in header),
                    'header_cells': [_token(cell) for cell in header]}
    return None


def _parse_operands(tables):
    for table in tables:
        try:
            rows = _import_rows(table['rows_json'])
        except ValueError:
            continue
        if not rows:
            continue
        header = [cell.strip() for cell in rows[0]]
        if 'Operand' not in header or 'Data type' not in header:
            continue
        index = {name: position for position, name in enumerate(header)}
        operands = []
        current_form = '' 
        for row in rows[1:]:
            if len(row) < len(header):
                row = row + [''] * (len(header) - len(row))
            first = row[index['Operand']].strip()
            second = row[index['Operand'] + 1].strip() if len(row) > index['Operand'] + 1 else ''
            form = ''
            if FORM_CELL.match(second):
                form = second
                name = first
            elif FORM_CELL.match(first):
                form = first
                name = first
            else:
                name = first
            description = row[index['Description']].strip() if 'Description' in index else ''
            ranges = row[index['Range']].strip() if 'Range' in index else ''
            data_type = row[index['Data type']].strip() if 'Data type' in index else ''
            label_type = (row[index['Data type (label)']].strip()
                          if 'Data type (label)' in index else '')
            remarks = row[index['Remarks']].strip() if 'Remarks' in index else ''
            if not (name or description or form):
                continue
            if form:
                current_form = form
            operands.append({'name': name or form, 'form': form,
                             'form_group': form or current_form,
                             'description_token': _token(description),
                             'range_text': GLYPH.sub(' ', ranges).strip(),
                             'data_type_token': _token(data_type),
                             'label_data_type_token': _token(label_type),
                             'remarks_token': _token(remarks)})
        if operands:
            return operands
    return []


def _range_value(text):
    cleaned = GLYPH.sub('', text or '').strip()
    numbers = re.findall(r'-?\d+', cleaned)
    if ' to ' in cleaned and len(numbers) >= 2:
        return {'minimum': int(numbers[0]), 'maximum': int(numbers[1])}
    if len(numbers) == 1 and re.match(r'^-?\d+$', cleaned):
        return {'value': int(numbers[0])}
    return None


def _model_ranges(text):
    found = []
    for piece in re.split(r'\u2022', GLYPH.sub(' ', text or '')):
        match = re.match(r'\s*(?P<models>FX5[A-Z]{0,3}(?:/FX5[A-Z]{0,3})*) CPU module '
                         r'(?P<detail>.+)$', piece.strip())
        if match:
            found.append({'models': [name.strip() for name in match.group('models').split('/')]
                          if '/' in match.group('models') else [match.group('models')],
                          'detail_token': _token(match.group('detail'))})
    return found


def _parse_language_statements(text):
    statements = []
    for match in UNSUPPORTED_STATEMENT.finditer(text or ''):
        forms = [part.strip() for part in re.split(r',| and ', match.group('forms'))
                 if part.strip()]
        statements.append({'forms': forms, 'languages': match.group('languages'),
                           'witness': match.group(0)})
    replacements = []
    for match in REPLACEMENT_STATEMENT.finditer(text or ''):
        replacements.append({'form': match.group('form'), 'witness': match.group(0)})
    pages = [{'page': int(match.group('page')), 'form': match.group('form')}
             for match in REPLACEMENT_PAGE.finditer(text or '')]
    if len(statements) == len(replacements):
        page_of = {}
        for entry in pages:
            page_of.setdefault(entry['form'], entry['page'])
        for statement, replacement in zip(statements, replacements):
            statement['replacement'] = replacement['form']
            if replacement['form'] in page_of:
                statement['replacement_page'] = page_of[replacement['form']]
    return statements, replacements, pages


def _meaning_token(meaning):
    cleaned = GLYPH.sub(' ', meaning or '').strip()
    if not cleaned:
        return ''
    first = re.split(r'\.\s|  ', cleaned)[0]
    return _token(' '.join(first.split()[:10]))


def _error_register_of_section(section):
    """The register row printed above the error-code column of this instruction.

    ``clean_text`` drops the repeated ``(SD0/SD8067)`` sub-header, so the row is
    bound from the raw layout column; when it is absent nothing is claimed.
    """
    for page, layout in section.get('page_layouts') or []:
        raw = layout or ''
        index = raw.find('Error code')
        while index >= 0:
            block = raw[index:index + 200]
            if all(phrase in block for phrase in ERROR_REGISTER_WITNESS):
                return {'error_register': ERROR_REGISTER,
                        'error_register_documented': True,
                        'error_register_header_token': ERROR_REGISTER_HEADER_TOKEN,
                        'error_register_bound_from': 'layout_text',
                        'error_register_page': page}
            index = raw.find('Error code', index + 1)
    return {'error_register': 'not_documented_in_the_cited_section',
            'error_register_documented': False}


def _error_codes_from_layout(section):
    """A layout row ``2820H | meaning`` is an error table row, header or not."""
    codes = {}
    for _page, layout in section.get('page_layouts') or []:
        for line in (layout or '').split('\n'):
            match = ERROR_TABLE_ROW.match(line.strip())
            if not match:
                continue
            codes.setdefault(match.group('code'),
                             {'code': match.group('code'),
                              'meaning_token': _meaning_token(match.group('meaning'))})
    return [codes[code] for code in sorted(codes)]


def _parse_error_codes(text, section=None):
    """Only the printed error table rows count.

    A bare ``NNNNH`` in the prose is not an error code: the program examples and
    the date/time ranges print values such as ``0001H`` or ``0063H`` that the
    text scan would turn into invented codes (they appeared in sections whose own
    table says "There is no operation error.").  The layout table is therefore the
    only accepted witness.
    """
    return _error_codes_from_layout(section or {})


def _parse_restrictions(text):
    found = {}
    for match in re.finditer(r'\*(\d+)\s+([^*]+?)(?=\s\*\d+\s|\s\[GLYPH|\sThis instruction|'
                             r'\sThese instructions|\s\u2022|$)', text or ''):
        sentence = re.sub(r'\s+', ' ', GLYPH.sub('', match.group(2))).strip()
        for pattern, template in RESTRICTION_PATTERNS:
            hit = pattern.match(sentence)
            if hit:
                values = {name: _token(value) for name, value in (hit.groupdict() or {}).items()}
                found.setdefault(template.format(**values),
                                 {'token': template.format(**values),
                                  'footnote': match.group(1),
                                  'witness_token': _token(sentence)[:120]})
                break
    return [found[token] for token in sorted(found)]


def _parse_section(section, universe):
    calls = _parse_st_calls(section['text'])
    operands = _parse_operands(section['tables'])
    language = _language_columns(section['tables'])
    statements, replacements, replacement_pages = _parse_language_statements(section['text'])
    errors = _parse_error_codes(section['text'], section)
    restrictions = _parse_restrictions(section['text'])
    heading = _heading(section, universe)
    return {'calls': calls, 'operands': operands, 'language': language,
            'statements': statements, 'replacements': replacements,
            'replacement_pages': replacement_pages, 'errors': errors,
            'restrictions': restrictions, 'heading': heading}


def _universe_opcode(form, universe):
    """The reviewed name a printed form belongs to, or ``''`` when unknown."""
    if form in universe:
        return form
    candidate = form
    for suffix in ('_U', '_I', '_2'):
        if candidate.endswith(suffix):
            candidate = candidate[:-len(suffix)]
            if candidate in universe:
                return candidate
    if candidate.endswith('P') and candidate[:-1] in universe:
        return candidate[:-1]
    return ''


def _declarations(section):
    """Literal declarations the shared parser reads out of this instruction page.

    The parser owns the manual's declaration structure, so the review never has to
    guess which line of a page is the instruction name.  ``source_text`` carries the
    whole section so optional suffixes and comparison placeholders expand only when
    the same source spells them out.
    """
    page_texts = section.get('page_texts') or [(section['first_page'], section['text'])]
    text = '\n'.join(page_text or '' for _page, page_text in page_texts)
    try:
        names = [name for _page, page_text in page_texts
                 for name in instruction_declaration_names(page_text, section['outline'], 'programming',
                                                           source_text=text)]
    except Exception as error:  # pragma: no cover - defensive, the API is stable
        raise ValueError('shared declaration parser failed on %s: %s'
                         % (section.get('outline'), error))
    return list(dict.fromkeys(names))


def _registry_names(directory=None):
    """Mnemonics of the live registry files, or ``()`` when they are unreadable."""
    directory = directory or REGISTRY_DIRECTORY
    names = set()
    for filename in REGISTRY_FILES:
        path = os.path.join(directory, filename)
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return set()
        names.update(_registry_payload_names(payload))
    return names


def _registry_payload_names(payload):
    """The mnemonics a registry document declares in its ``instructions`` list."""
    names = set()
    for entry in payload.get('instructions') or ():
        if isinstance(entry, dict):
            name = str(entry.get('mnemonic') or '').strip().upper()
            if name:
                names.add(name)
    return names


def _base_name(name, universe=(), declared=()):
    """The mnemonic a printed form belongs to, reduced only against ``universe``.

    ``ADDP`` is reduced to ``ADD``, but a name that is known in its own right is
    kept: ``NOP`` must not become ``NO`` and ``RAMP`` must not become ``RAM``.
    Symbolic forms
    (``+``, ``*``, ``E+``) are not mnemonics of their own, so they are dropped
    instead of being turned into invented opcodes.
    """
    if not re.fullmatch(r'[A-Z][A-Z0-9_]*', name or ''):
        return ''
    known = {value for value in universe if value}
    documented = {value for value in declared if value}
    if name in known:
        return name
    candidate = name
    for suffix in ('_U', '_E', '_I', '_2'):
        if candidate.endswith(suffix) and len(candidate) > len(suffix):
            stripped = candidate[:-len(suffix)]
            if stripped in known or stripped in documented:
                return stripped
            candidate = stripped
    if candidate.endswith('P') and len(candidate) > 1:
        stripped = candidate[:-1]
        if stripped in known or stripped in documented:
            return stripped
    # the page printed this name and nothing shorter exists: keep it verbatim
    # instead of inventing a shortened mnemonic (``TCMP`` must not become ``TCM``)
    return candidate


def _review_universe(source, entries=None, directory=None):
    """Names to review: caller entries, else the live registry, plus declarations.

    ``entries`` wins because the caller knows the exact coverage list it is about to
    merge; the registry files are the fallback; the embedded tuple is only used when
    neither can be read, and the receipt always states which one was used.
    """
    register = {'registry': set(), 'entries': set(), 'declared': set()}
    for entry in entries or ():
        if isinstance(entry, str):
            name = entry
        elif isinstance(entry, dict):
            name = (entry.get('opcode') or entry.get('mnemonic') or entry.get('name') or '')
        else:  # pragma: no cover - defensive
            name = ''
        name = str(name).strip().upper()
        if name:
            register['entries'].add(name)
    if register['entries']:
        universe_source = 'entries'
    else:
        register['registry'] = _registry_names(directory)
        universe_source = 'registry_files' if register['registry'] else 'embedded_fallback'
        if not register['registry']:
            register['registry'] = set(EMBEDDED_FALLBACK_UNIVERSE)
    return register, universe_source


def _universe_report(register, universe_source, documented_names):
    """Which names came from where, and what the review can and cannot say."""
    registry = set(register['registry']) | set(register['entries'])
    declared = set(register['declared'])
    registry_bases = {_base_name(name, registry) for name in registry} - {''}
    unregistered = set(register.get('unregistered') or ())
    declared_bases = {_base_name(name, registry_bases, declared) for name in declared} - {''}
    unregistered_bases = sorted(declared_bases - registry_bases)
    return {'universe_source': universe_source,
            'registry_mnemonics': len(registry),
            'entries_mnemonics': len(register['entries']),
            'declared_names_by_the_shared_parser': len(declared),
            'declared_bases_by_the_shared_parser': len(declared_bases),
            'declared_bases_not_in_the_registry': unregistered_bases,
            'declared_forms_without_a_registry_entry': sorted(unregistered),
            'declared_bases_not_in_the_registry_count': len(unregistered_bases),
            'registry_not_declared_here': sorted(registry_bases - declared_bases),
            'documented_here': sorted(documented_names),
            'documented_here_count': len(documented_names),
            'registry_documented': sorted(set(documented_names) & registry),
            'source_derived_identity_only': sorted(set(documented_names) - registry),
            'source_derived_identity_only_count': len(set(documented_names) - registry)}


def _steps_pages(source):
    """Appendix 2 pages, in order: the printed ``minimum steps`` table."""
    return _rows(source, 'SELECT pdf_page, text FROM (SELECT pdf_page, clean_text AS text, '
                         'outline_path FROM page_artifacts WHERE manual_id=?) WHERE '
                         'LOWER(REPLACE(outline_path, " ", "_")) LIKE ? ORDER BY pdf_page',
                 (MANUAL_ID, '%' + STEPS_OUTLINE_TOKEN + '%'))


def _join_steps_lines(text):
    """Undo the two-line wraps of a mnemonic (``AND<>_`` + ``U 4``)."""
    lines = [line.strip() for line in (text or '').splitlines()]
    joined = []
    for line in lines:
        if (joined and re.fullmatch(r'[A-Z_]{1,3}\s+\d{1,2}', line)
                and re.fullmatch(r'[A-Z][A-Z0-9_+<>=!*/$@.\-]*[_<>=]', joined[-1])):
            joined[-1] = joined[-1] + line
        else:
            joined.append(line)
    return joined


def _parse_instruction_steps(source):
    """Read the appendix table into ``{form: {'minimum': steps, 'operand_class': token}}``.

    The table prints one line per instruction (``LD 2``, ``OUT T/ST 5``); steps that
    depend on the operand class are printed as separate lines, so the class is kept
    as its own token instead of being folded into the mnemonic.
    """
    forms = {}
    pages = []
    for page in _steps_pages(source):
        page_number = page['pdf_page']
        pages.append(page_number)
        for line in _join_steps_lines(page['text']):
            match = STEPS_ROW.match(line)
            if not match:
                continue
            name = match.group('name').strip()
            operand_class = _token((match.group('operand_class') or '').replace(' ', ''))
            steps = int(match.group('steps'))
            entry = forms.setdefault(name, {'minimum': steps, 'operand_classes': {}})
            if operand_class:
                entry['operand_classes'][operand_class] = steps
                entry.setdefault('minimum', steps)
            else:
                entry['minimum'] = min(entry.get('minimum', steps), steps)
    return forms, pages


def _steps_value(forms, pages):
    """The published ``resources.instruction_steps`` value of one instruction."""
    minimum = min(value['minimum'] for value in forms.values())
    classes = {}
    for value in forms.values():
        classes.update(value['operand_classes'])
    return {'steps_minimum': minimum,
            'per_printed_form': {name: value['minimum'] for name, value in sorted(forms.items())},
            'per_operand_class': dict(sorted(classes.items())),
            'documented_as': 'appendix_2_number_of_instruction_steps',
            'documented_pages': list(pages),
            'caveat_token': ('steps_may_increase_with_operand_specification_bit_or_digit_'
                             'specification_and_indexing')}


def _instruction_chapter(section):
    """Instruction/function chapters; overviews and appendices are excluded."""
    match = INSTRUCTION_OUTLINE.match(section['outline'] or '')
    return match.group('chapter') if match else ''


def _table_forms(section, universe):
    """Instruction names printed in the ``Operand`` column of the setting table.

    The manual prints the symbolic name on one page (``+(P)``) and the mnemonic
    on the next (``ADD(P)``); the setting table carries the form that actually
    applies to that block, so it is read as evidence and validated against the
    registry instead of being trusted blindly.
    """
    forms = {}
    for table in section['tables']:
        try:
            rows = _import_rows(table['rows_json'])
        except ValueError:
            continue
        if not rows or 'Operand' not in [cell.strip() for cell in rows[0]]:
            continue
        for row in rows[1:]:
            for cell in row[:2]:
                value = cell.strip()
                if not FORM_CELL.match(value):
                    continue
                if _universe_opcode(value, universe):
                    forms.setdefault(value, table['pdf_page'])
    return forms


def _page_headings(section):
    headings = []
    for page, page_text in section.get('page_texts') or [(section['first_page'],
                                                          section['text'])]:
        text = page_text[:400]
        title = section['section'] or ''
        index = text.rfind(title)
        head = text[index + len(title):index + len(title) + 200] if index >= 0 else text[:160]
        masked = head.replace(WILDCARD_GLYPH, WILDCARD_MARK)
        cut = len(masked)
        for marker in HEAD_STOP:
            position = masked.find(marker)
            if position >= 0:
                cut = min(cut, position)
        sentence = masked.find('. ')
        if sentence >= 0:
            cut = min(cut, sentence)
        headings.append((page, masked[:cut].strip()))
    return headings


def _candidates(section, parsed, universe):
    """Reviewed names documented by this section, with the evidence basis."""
    if _instruction_chapter(section) not in INSTRUCTION_CHAPTERS:
        return []
    documented = {}

    symbolic = []

    def record(opcode, form, basis):
        if declarations and opcode not in declarations:
            # A call in a worked example does not own this instruction chapter.
            return
        entry = documented.setdefault(opcode, {'opcode': opcode, 'forms': [],
                                               'bases': [], 'symbolic': []})
        if form and form not in entry['forms']:
            entry['forms'].append(form)
        if basis not in entry['bases']:
            entry['bases'].append(basis)

    declarations = _declarations(section)
    page_bases = set()
    for _page, line in _page_headings(section):
        probe = _heading_from_line(line, universe)
        for form in probe['forms']:
            base = _base_name(form, universe)
            if base:
                page_bases.add(base)
    unregistered = []
    stray = []
    for form in declarations:
        opcode = _universe_opcode(form, universe)
        if opcode:
            record(opcode, form, 'shared_declaration_parser')
            continue
        token = _symbolic_token(form)
        if token:
            if token not in symbolic:
                symbolic.append(token)
        elif form not in unregistered:
            unregistered.append(form)
    section['unregistered_declarations'] = sorted(unregistered)
    section['stray_declarations'] = sorted(stray)
    for _page, line in _page_headings(section):
        probe = _heading_from_line(line, universe)
        for form in probe['forms']:
            opcode = _universe_opcode(form, universe)
            if opcode:
                record(opcode, form, 'documented_form_line')
            else:
                token = _symbolic_token(form)
                if token and token not in symbolic:
                    symbolic.append(token)
        for symbol in probe.get('symbols') or []:
            token = _symbol_token(symbol)
            if token and token not in symbolic:
                symbolic.append(token)
    for form in _table_forms(section, universe):
        opcode = _universe_opcode(form, universe)
        if opcode:
            record(opcode, form, 'operand_table_form_cell')
    for call in parsed['calls']:
        opcode = _universe_opcode(call['form'], universe)
        if opcode:
            record(opcode, call['form'], 'structured_text_call_line')
    for entry in documented.values():
        entry['forms'] = sorted(entry['forms'])
        entry['basis'] = ('shared_declaration_parser' if 'shared_declaration_parser'
                          in entry['bases'] else entry['bases'][0])
        entry['bases'] = sorted(entry['bases'])
        entry['symbolic'] = sorted(symbolic)[:8]
        entry['declared_names'] = [name for name in declarations
                                   if _universe_opcode(name, universe) == entry['opcode']]
    return [documented[opcode] for opcode in sorted(documented)]


# --------------------------------------------------------------------------- #
# references
# --------------------------------------------------------------------------- #
def _chunk_reference(row):
    return {'id': row['chunk_uid'], 'source_kind': 'chunk',
            'manual_id': row.get('manual_id', MANUAL_ID),
            'manual_number': row.get('manual_number', MANUAL_NUMBER),
            'revision': row.get('revision', REVISION),
            'pdf_pages': list(range(row['pdf_page'],
                                    (row['pdf_page_end'] or row['pdf_page']) + 1))}


def _covering_chunks(source, page_start, page_end, *, manual_id=MANUAL_ID):
    return _rows(source, 'SELECT chunk_uid, pdf_page, pdf_page_end, section, outline_path, '
                         'chunk_type, manual_id, manual_number, revision FROM chunks WHERE manual_id=? AND pdf_page<=? AND '
                         'COALESCE(pdf_page_end, pdf_page)>=? ORDER BY pdf_page',
                 (manual_id, page_end, page_start))


def _additional_source_references(source, claim, cache):
    references = []
    for declared in claim.get('additional_sources', ()):
        manual_id = declared['manual_id']
        for page in declared['pdf_pages']:
            key = (manual_id, page)
            if key not in cache:
                cache[key] = [_chunk_reference(chunk) for chunk in
                              _covering_chunks(source, page, page, manual_id=manual_id)]
            matches = cache[key]
            if not matches or any(reference.get(field) != declared.get(field)
                                  for reference in matches for field in ('manual_number', 'revision')):
                raise ValueError('Additional instruction source is missing or has changed: %s page %s'
                                 % (manual_id, page))
            references.extend(reference for reference in matches if reference not in references)
    return references


def _page_references(source, section):
    """Chunks that belong to this instruction, plus any page that belongs elsewhere.

    A chunk may already be anchored to the next instruction while the page artifact
    still carries the previous outline (page 469 of the CALL section is the
    ``Returning from the subroutine program`` chunk).  Such a page is dropped from
    the fact and reported, never silently merged into the instruction.
    """
    cover = _covering_chunks(source, section['first_page'], section['last_page'])
    matched = []
    pages = []
    foreign = []
    for page in range(section['first_page'], section['last_page'] + 1):
        owners = [row for row in cover
                  if row['pdf_page'] <= page <= (row['pdf_page_end'] or row['pdf_page'])]
        own = [row for row in owners if (row['outline_path'] or '') == section['outline']]
        if own:
            pages.append(page)
            for row in own:
                if row['chunk_uid'] not in {item['chunk_uid'] for item in matched}:
                    matched.append(row)
        elif owners:
            foreign.append({'page': page,
                            'outline_token': _token(owners[0]['outline_path'] or '')[:100]})
        else:
            foreign.append({'page': page, 'outline_token': 'no_covering_chunk'})
    if not matched:
        raise ValueError('residual review section has no chunk inside its own outline: %s'
                         % section['outline'])
    references = [_chunk_reference(row) for row in matched]
    return references, pages, foreign



def _heading_regions(source):
    return _rows(source, 'SELECT pdf_page, chapter, section, outline_path, clean_text '
                         'FROM page_artifacts WHERE manual_id=? ORDER BY pdf_page', (MANUAL_ID,))


def _declaration_index(sections):
    """Where the shared parser declares each name in this manual."""
    index = {}
    for section in sections:
        for name in _declarations(section):
            index.setdefault(name, []).append({'section_token': section['section_token'],
                                               'pages': [section['first_page'],
                                                         section['last_page']]})
    return index


def _named_pattern(name):
    return re.compile(r'(?<![A-Z0-9_+*/<>=!-])' + re.escape(name) + r'(?![A-Z0-9_+*/<>=!-])')


def _verify_not_printed(source, sections, universe, documented_names):
    """Check every "this manual does not print it as an instruction page" claim.

    A manual that does not print a mnemonic only proves a gap *of that manual*: it
    says nothing about which other instruction applies instead.  So the claim is one
    sentence -- the name is not declared by the shared parser anywhere in the
    instruction chapters -- and the neighbouring names the manual does print are
    recorded as observations with ``equivalence_claimed=False``.
    """
    index = _declaration_index(sections)
    regions = _heading_regions(source)
    whole = '\n'.join(row['clean_text'] or '' for row in regions)
    part5 = '\n'.join('%s %s' % (row['section'] or '', row['outline_path'] or '')
                       for row in regions if (row['outline_path'] or '').startswith('PART 5'))
    covered = set(COVERED_BY_SHIPPED_MODULES) | set(documented_names)
    claims = []
    for name in sorted(set(universe) - covered):
        if name in index:
            raise ValueError('%s is declared by the shared parser (%s) but was not published'
                             % (name, index[name][0]['section_token']))
        note = NOT_PRINTED_HERE.get(name) or {}
        observation = note.get('observation') or 'mnemonic_not_printed_in_this_manual'
        observed = [value for value in note.get('observed_names') or () if value != name]
        for value in observed:
            if value not in index and not _named_pattern(value).search(whole):
                raise ValueError('%s is recorded as observing %s, which this manual does '
                                 'not print' % (name, value))
        if observation == 'printed_only_in_the_instruction_processing_time_table':
            pattern = _named_pattern(name)
            pages = [row for row in regions if pattern.search(row['clean_text'] or '')]
            if not pages:
                raise ValueError('%s is not printed anywhere in this manual' % name)
            if any('Instruction processing time' not in (row['outline_path'] or '')
                   for row in pages):
                raise ValueError('%s is printed outside the processing time table' % name)
        elif observation == 'printed_only_as_a_part_5_data_type_conversion':
            if _named_pattern(name).search(whole) and not part5:
                raise ValueError('%s has no PART 5 data type conversion section' % name)
            if not _named_pattern(name).search(part5):
                raise ValueError('%s has no PART 5 data type conversion section' % name)
        claims.append({'mnemonic': name,
                       'reason': 'not_printed_as_an_instruction_page_in_this_manual',
                       'observation_token': observation,
                       'observed_names': observed,
                       'equivalence_claimed': False})
    return {'checked': len(claims),
            'rule': ('a_missing_instruction_page_is_a_gap_of_this_manual_and_never_an_'
                     'equivalence_with_another_printed_form'),
            'claims': claims}


# --------------------------------------------------------------------------- #
# compiler projection
# --------------------------------------------------------------------------- #
def instruction_definitions(facts, universe=None):
    """Project the published facts into the per-owner entries the compiler reads."""
    entries = []
    for key in sorted(facts):
        model, opcode = key
        entries.append({'vendor': 'mitsubishi', 'opcode': opcode, 'target_model': model,
                        'registry_member': bool(universe is None or opcode in universe),
                        'facts': [dict(row) for row in facts[key]],
                        'gaps': [], 'conflicts': [], 'uninterpreted_content': []})
    return entries


def _project_definition():
    """The harness projection, resolved the same way as the shared parser."""
    try:
        from knowledge.fact_corpus import project_definition
    except ImportError:  # pragma: no cover - repository laid out as <root>/src
        from src.knowledge.fact_corpus import project_definition
    return project_definition


def _instruction_fact_group():
    """The harness fact-group validator, resolved like the projection."""
    try:
        from plc.instruction_definition import InstructionFactGroup
    except ImportError:  # pragma: no cover - repository laid out as <root>/src
        from src.plc.instruction_definition import InstructionFactGroup
    return InstructionFactGroup


def validate_definitions(facts, universe=None):
    """Every published fact must survive ``project_definition`` and the fact groups.

    This is the harness contract, not a copy of it: the projection drops anything it
    cannot type, and the fact group rejects a dimension, status, scope or source it
    does not accept.  A withheld fact is a defect of this review, so it raises.
    """
    project_definition = _project_definition()
    InstructionFactGroup = _instruction_fact_group()
    entries = instruction_definitions(facts, universe)
    projected = 0
    groups = 0
    withheld = []
    accepted = set()
    for entry in entries:
        result, dropped = project_definition(entry)
        withheld.extend(dropped)
        if len(result['facts']) != len(entry['facts']):
            raise ValueError('the projection dropped a fact of %r: %r'
                             % (entry.get('opcode'), dropped[:3]))
        for fact in result['facts']:
            # the public contract, per fact: dimension, status, scope and sources
            InstructionFactGroup.from_mapping(fact)
            accepted.add(fact['id'])
            groups += 1
        projected += len(result['facts'])
    if withheld:
        raise ValueError('published facts do not pass the harness projection: %s'
                         % withheld[:3])
    published = {fact['id'] for rows in facts.values() for fact in rows}
    unaudited = sorted(published - accepted)
    if unaudited:
        raise ValueError('facts never reached the fact-group contract: %s' % unaudited[:3])
    if len(entries) != len({key for key in facts}):
        raise ValueError('every (model, opcode) needs its own definition entry')
    return {'entries': len(entries),
            'facts_published': len(published),
            'facts_projected': projected,
            'fact_groups_accepted': groups,
            'withheld': 0,
            'contract': 'project_definition + InstructionFactGroup.from_mapping, per fact'}


# --------------------------------------------------------------------------- #
# facts
# --------------------------------------------------------------------------- #
def _fact(identity, dimension, value, references, models, *, status='source_verified',
          languages=None):
    scope = {'models': list(models), 'forms': []}
    if languages:
        scope['languages'] = list(languages)
    if dimension == 'identity.applicability' and any(
            state not in {'supported', 'unsupported'} for state in value.get('model_states', {}).values()):
        status = 'unknown'
    return {'id': identity, 'dimension': dimension, 'value': _plain(value),
            'status': status,
            'verification': 'source_checked' if status == 'source_verified' else 'not_performed',
            'sources': [dict(reference) for reference in references],
            'scope': scope}


def _form_value(section, record, manual, heading, pages, registry_member=True):
    return {'manual_number': manual['manual_number'], 'revision': manual['revision'],
            'scope_basis': SCOPE_BASIS,
            'per_model_support': PER_MODEL_SUPPORT,
            'support_basis': SUPPORT_BASIS,
            'registry_member': registry_member,
            'knowledge_identity_only': not registry_member,
            'core_executable': False,
            'chapter_token': _token(section['chapter'] or ''),
            'chapter_number': section.get('chapter_number') or 0,
            'section_number': section['section_number'] or '',
            'section_token': section['section_token'],
            'outline_token': section['outline_token'],
            'documented_pages': list(pages),
            'documented_forms': list(record['forms']),
            'documented_symbolic_forms': list(record.get('symbolic') or []),
            'form_line_token': heading['form_line_token'] or '',
            'documented_device_suffix': heading['device_suffix'] or '',
            'documented_specification_token': heading['specification_token'] or '',
            'binding_basis': 'page_outline_path_equals_the_instruction_section'}


def _language_value(parsed, record):
    statements = parsed['statements']
    replacements = {entry['form']: entry for entry in parsed['replacements']}
    pages = {}
    for entry in parsed['replacement_pages']:
        pages.setdefault(entry['form'], entry['page'])
    calls = {call['form']: call for call in parsed['calls']}
    columns = parsed['language'] or {}
    forms = {}
    for form in record['forms']:
        entry = {'ladder_diagram': bool(columns.get('ladder_diagram')),
                 'structured_text': 'not_documented', 'fbd_ld': 'not_documented',
                 'basis': 'documented_form_line_only'}
        if form in calls:
            entry['structured_text'] = True
            entry['st_call'] = calls[form]['call']
            entry['basis'] = 'structured_text_column_of_the_instruction_table'
        negative = [statement for statement in statements if form in statement['forms']]
        if negative:
            languages = negative[0]['languages']
            if 'ST' in languages:
                entry['structured_text'] = False
            if 'FBD/LD' in languages:
                entry['fbd_ld'] = False
            entry['basis'] = 'documented_unsupported_language_statement'
            entry['statement_token'] = _token(negative[0]['witness'])[:120]
            if negative[0].get('replacement'):
                entry['replacement_standard_function'] = negative[0]['replacement']
                if negative[0].get('replacement_page'):
                    entry['replacement_page'] = negative[0]['replacement_page']
        if columns.get('fbd_ld_column'):
            entry['fbd_ld'] = True
        forms[form] = entry
    return {'forms': forms,
            'language_table_columns': columns.get('header_cells') or [],
            'documented_unsupported_statements': len(statements),
            'documented_as': 'instruction_page_language_table_and_language_statements',
            'per_cpu_availability_marks': 'glyph_table_not_interpreted',
            'scope_basis': SCOPE_BASIS,
            'per_model_support': PER_MODEL_SUPPORT,
            'support_basis': SUPPORT_BASIS}


def _signature_value(parsed, record):
    forms = set(record['forms'])
    calls = [call for call in parsed['calls'] if call['form'] in forms]
    rows = []
    for call in calls:
        entry = {'form': call['form'], 'arguments': call['arguments'], 'call': call['call']}
        if call['verbatim']:
            entry['arguments_verbatim'] = True
            entry['note_token'] = 'call_line_printed_verbatim_including_a_joined_operand'
        rows.append(entry)
    return {'call_forms': rows,
            'call_count': len(calls),
            'enable_input': 'EN', 'enable_output': 'ENO',
            'documented_as': 'eno_structured_text_call_line'}


def _bare_form(form):
    """``+(P)`` -> ``+``, ``MOV(P)(_U)`` -> ``MOV``: the part that names the form."""
    return (form or '').split('(')[0].strip().upper()


def _arity_value(parsed, record):
    """The operand rows printed for each form, next to the printed call arguments.

    The table repeats a row per sign/width variant, so the row count is published as
    exactly that (rows), never as the arity; ``min``/``max`` appear only where the row
    count and the literal structured-text call agree.
    """
    groups = {}
    for operand in parsed['operands']:
        printed = _bare_form(operand.get('form_group'))
        if printed:
            groups[printed] = groups.get(printed, 0) + 1
    calls = {_bare_form(call['form']): call['arguments'] for call in parsed['calls']}
    per_form = {}
    for form in record['forms']:
        bare = _bare_form(form)
        count = groups.get(bare)
        if not count:
            continue
        arguments = calls.get(bare)
        entry = {'printed_operand_rows_of_the_form': count}
        if arguments is None:
            entry['structured_text_arguments'] = 'not_documented'
            entry['agreement'] = 'no_structured_text_call_printed_for_this_form'
        else:
            entry['structured_text_arguments'] = len(arguments)
            if len(arguments) == count:
                entry['min'] = count
                entry['max'] = count
                entry['agreement'] = 'operand_rows_equal_structured_text_arguments'
            else:
                # the table repeats a row per sign/width variant; the call states the
                # real arity, so the two numbers are published as printed, not merged
                entry['agreement'] = 'operand_rows_exceed_structured_text_arguments'
        per_form[form] = entry
    if not per_form:
        return None
    return {'per_form': per_form,
            'forms_counted': len(per_form),
            'documented_as': 'instruction_page_operand_table',
            'basis': 'one_printed_operand_row_per_operand_of_the_form',
            'cross_checked_with': 'eno_structured_text_call_arguments_where_printed'}


def _operand_value(parsed, record):
    forms = set(record['forms'])
    operands = []
    for operand in parsed['operands']:
        if operand['form'] and operand['form'] not in forms:
            continue
        entry = {'name_token': _token(operand['name']) or _token(operand['form']),
                 'form': operand['form'] or '',
                 'description_token': operand['description_token'] or '',
                 'data_type_token': operand['data_type_token'] or '',
                 'label_data_type_token': operand['label_data_type_token'] or ''}
        if operand['remarks_token']:
            entry['remarks_token'] = operand['remarks_token']
        value = _range_value(operand['range_text'])
        if value is not None:
            entry['range'] = value
        elif operand['range_text']:
            entry['range_token'] = _token(operand['range_text'])[:80]
        model_ranges = _model_ranges(operand['range_text'])
        if model_ranges:
            entry['per_model_ranges'] = model_ranges
        operands.append(entry)
    return {'operands': operands, 'operand_count': len(operands),
            'documented_as': 'operand_table_with_range_and_data_type'}


def _error_value(parsed, section):
    if parsed['errors']:
        value = {'codes': parsed['errors'], 'code_count': len(parsed['errors']),
                 'documented_as': 'operation_error_table',
                 'bound_from': 'layout_text_table_rows'}
        value.update(_error_register_of_section(section))
        return value
    if _contains(section['text'], 'There is no operation error'):
        return {'codes': [], 'code_count': 0,
                'documented_as': 'no_operation_error_statement'}
    return None


#: Behaviour blocks: every instruction page prints a "Processing details" block whose
#: matrix says what the instruction does to the operands.  The matrix is read cell by
#: cell; cells that only carry prose are not published, so nothing is paraphrased.
PROCESSING_TITLE = 'processing details'
PROCESSING_STOP = ('precautions', 'program example', 'operation error', 'ladder diagram',
                   'setting data', 'operand', 'processing details')
CELL_TOKEN_LIMIT = 64


def _processing_matrices(tables):
    """The printed processing-details matrices of one section, in page order."""
    matrices = []
    armed = False
    for table in tables:
        try:
            rows = _import_rows(table['rows_json'])
        except ValueError:
            continue
        if not rows:
            continue
        header = [(cell or '').strip() for cell in rows[0]]
        title = (header[0] if header else '').lower()
        if title == PROCESSING_TITLE:
            armed = True
            continue
        if not armed:
            continue
        if title in PROCESSING_STOP:
            armed = False
            continue
        matrices.append((table.get('pdf_page'), header, rows[1:]))
    return matrices


def _cell_token(cell):
    """One printed cell as a token, or ``''`` when the cell is prose."""
    text = GLYPH.sub(' ', (cell or '')).strip()
    if not text:
        return ''
    token = _token(text)
    if not token or len(token) > CELL_TOKEN_LIMIT:
        return ''
    return token


def _processing_value(section, page_tables=None):
    """``effects.processing_result`` for this page, or ``None`` when it prints none."""
    tables = list(section['tables'])
    if page_tables:
        seen = {(table.get('pdf_page'), table.get('rows_json')) for table in tables}
        for page in range(section['first_page'], section['last_page'] + 1):
            for table in page_tables.get(page) or ():
                if (table.get('pdf_page'), table.get('rows_json')) not in seen:
                    seen.add((table.get('pdf_page'), table.get('rows_json')))
                    tables.append(table)
    blocks = []
    skipped = 0
    for page, header, rows in _processing_matrices(tables):
        columns = [_cell_token(cell) for cell in header]
        body = []
        for row in rows:
            cells = [_cell_token(cell) for cell in row]
            if not any(cells):
                continue
            if not all(cells):
                skipped += 1
                continue
            body.append(cells)
        if body:
            blocks.append({'pdf_page': page,
                           'columns': [column for column in columns if column],
                           'rows': body})
    if not blocks:
        return None
    return {'documented_as': 'processing_details_matrix',
            'blocks': blocks,
            'block_count': len(blocks),
            'row_count': sum(len(block['rows']) for block in blocks),
            'prose_rows_not_published': skipped,
            'column_order': 'as_printed_left_to_right'}

def _restriction_value(parsed):
    if not parsed['restrictions']:
        return None
    return {'restrictions': parsed['restrictions'],
            'count': len(parsed['restrictions']),
            'documented_as': 'footnote_restrictions_of_the_instruction_page'}


MODEL_NAMES = ('FX5S', 'FX5UJ', 'FX5U', 'FX5UC')
def _page_geometry(source):
    """Keep the existing shared header extraction alongside page geometry."""
    geometry = {'headers': {}}
    for row in _rows(source, 'SELECT pdf_page, word_geometry_json, visual_json FROM page_artifacts '
                             'WHERE manual_id=? ORDER BY pdf_page', (MANUAL_ID,)):
        try:
            geometry[row['pdf_page']] = json.loads(row['word_geometry_json'])
            header = json.loads(row['visual_json'] or '{}').get('source_applicability')
            if header:
                geometry['headers'][row['pdf_page']] = header
        except (TypeError, ValueError):
            continue
    return geometry


def _page_form_value(manual, page, outline, section_title, name, opcode, registry_names):
    """``identity.form`` for a page that declares a name without a bindable heading."""
    return {'manual_number': manual['manual_number'], 'revision': manual['revision'],
            'scope_basis': SCOPE_BASIS,
            'per_model_support': PER_MODEL_SUPPORT,
            'support_basis': SUPPORT_BASIS,
            'registry_member': opcode in registry_names,
            'knowledge_identity_only': opcode not in registry_names,
            'core_executable': False,
            'chapter_token': _token(section_title or ''),
            'chapter_number': 0,
            'section_number': '',
            'section_token': _token(section_title or ''),
            'outline_token': _token(outline or ''),
            'documented_pages': [page],
            'documented_forms': [name],
            'documented_symbolic_forms': [],
            'form_line_token': _token(name),
            'documented_device_suffix': '',
            'documented_specification_token': '',
            'binding_basis':
                'page_declaration_of_an_instruction_page_without_a_bindable_heading'}


def _applicability_value(manual, pages, geometry):
    """What the manual actually states about which CPUs run this instruction."""
    states = {model: set() for model in MODEL_NAMES}
    matched = []
    for page in pages:
        header = geometry.get('headers', {}).get(page)
        if not header or header.get('basis') != 'instruction_header':
            continue
        matched.append(page)
        for mark in header.get('model_marks', []):
            if mark.get('model') in states:
                states[mark['model']].add(mark['state'])
    return {
        'documented_models': list(MODEL_NAMES),
        'model_states': {model: next(iter(values)) if len(values) == 1 else 'unknown'
                         for model, values in states.items()},
        'models_basis': SCOPE_BASIS,
        'support_basis': SUPPORT_BASIS,
        'legend_pdf_page': 20,
        'header_pages': matched,
        'other_restrictions': 'retain_instruction_footnotes_and_hardware_conditions',
        'documented_pages': list(pages),
    }


def _page_tables(source):
    """``{pdf_page: [table, ...]}`` for the reviewed manual, read once."""
    tables = {}
    for row in _rows(source, 'SELECT pdf_page, table_index, rows_json FROM tables '
                             'WHERE manual_id=? ORDER BY pdf_page, table_index', (MANUAL_ID,)):
        tables.setdefault(row['pdf_page'], []).append(
            {'pdf_page': row['pdf_page'], 'table_index': row['table_index'],
             'rows_json': row['rows_json']})
    return tables


def _execution_rules(source):
    """The manual's explicit exception table, not a mnemonic suffix guess."""
    rows = _rows(source, 'SELECT layout_text FROM page_artifacts WHERE manual_id=? AND pdf_page=37',
                 (MANUAL_ID,))
    text = rows[0]['layout_text'] if rows else ''
    required = ('On All instructions except for the following', 'Falling edge PLF',
                'Instruction followed by symbol (P), (GP .), and (SP .)',
                'Always LD, LDI, AND, ANI')
    if not all(witness in text for witness in required):
        raise ValueError('FX5 execution-condition table does not match the reviewed revision')
    tail = text.split('Always LD, LDI, AND, ANI', 1)[1]
    always = {'LD', 'LDI', 'AND', 'ANI'}
    always.update(re.findall(r'(?<![A-Z0-9_])([A-Z][A-Z0-9_]*)(?=,|\s*$)', tail))
    always.update(name for name in ('OUTH', 'OUTHS') if name + ' T' in tail)
    # OUT's timer/counter operands are conditions of OUT, not extra mnemonics.
    always.difference_update({'T', 'ST', 'C', 'LC'})
    refs = [_chunk_reference(row) for row in _covering_chunks(source, 37, 37)]
    return always, refs


def _execution_value(record, always, declared_forms):
    opcode = record['opcode']
    if opcode in always or re.fullmatch(r'(?:LD|AND|OR)(?:D|\$|E|DT|TM)?(?:<>|<=|>=|=|<|>)(?:_U)?', opcode):
        return {'trigger': 'instruction_reached', 'precondition': 'any',
                'when_disabled': 'instruction_specific_off_processing',
                'basis': 'execution_condition_table_always'}
    if opcode == 'PLF':
        return {'trigger': 'precondition_falling_edge', 'executions_per_edge': 1,
                'when_disabled': 'no_execution_except_falling_transition',
                'basis': 'execution_condition_table_falling_edge'}
    unpulsed = re.sub(r'P(_U)?$', lambda match: match[1] or '', opcode)
    # STOP, CMP and other names ending in P are not '(P)' variants.
    pulse_variant = unpulsed != opcode and unpulsed in declared_forms
    if opcode == 'PLS' or pulse_variant or opcode.startswith(('GP.', 'SP.', 'GP_', 'SP_')):
        return {'trigger': 'precondition_rising_edge', 'executions_per_edge': 1,
                'when_disabled': 'not_executed', 'basis': 'execution_condition_table_rising_edge'}
    return {'trigger': 'precondition_on', 'when_disabled': 'not_executed',
            'basis': 'execution_condition_table_default_on'}


def _behavior_specs(section, record):
    """Authored transformations bound to the owning introductory statement.

    The introduction defines the operation; worked examples cannot introduce
    another instruction's effect. Full section details still supply limits and
    state qualifiers, and the original bodies stay in the private index.
    """
    title = section['section_token']
    raw = '\n'.join(layout for _, layout in section.get('page_layouts', ()))
    intro_match = re.search(r'\b(?:These|This) instructions?\s+(.+?)(?=Ladder diagram|Setting data|\n\s*FBD/LD)', raw, re.S)
    intro = re.sub(r'\s+', ' ', intro_match[1]) if intro_match else ''
    body = re.sub(r'\s+', ' ', raw)
    specs = []
    def add(key, value, conditions=None):
        specs.append({'key': key, 'dimension': 'effects.' + key, 'value': value,
                      **({'conditions': conditions} if conditions else {})})
    width = re.search(r'(16|32|64)_bit', title)
    width = int(width[1]) if width else None
    opcode = record['opcode']
    # Optional P/_U forms are already declared by the shared parser. A page
    # with two- and three-operand forms retains both formulas explicitly.
    arithmetic = {'adding': 'add', 'subtracting': 'subtract',
                  'multiplying': 'multiply', 'dividing': 'divide'}
    verb = title.split('_', 1)[0]
    if verb in {'incrementing', 'decrementing'} and re.search(r'\b(?:add|subtract) 1\b', intro):
        add('arithmetic', {'operation': 'increment' if verb == 'incrementing' else 'decrement',
                          'target': 'd', 'amount': 1, 'width_bits': width,
                          'signed': not opcode.endswith('_U')})
    conversion = re.match(r'converting_(.+?)_to_(.+)', title)
    if conversion and re.search(r'\bconvert\b', intro) and '(s)' in intro and '(d)' in intro:
        # The type labels below are authored categories, never arbitrary prose.
        types = {'binary_data': 'binary', 'bcd_4_digit_data': 'bcd4',
                 'bcd_8_digit_data': 'bcd8', 'single_precision_real_number': 'real32',
                 'double_precision_real_number': 'real64',
                 '16_bit_signed_binary_data': 'int16', '16_bit_unsigned_binary_data': 'uint16',
                 '32_bit_signed_binary_data': 'int32', '32_bit_unsigned_binary_data': 'uint32',
                 '16_bit_binary_data': 'binary16', '32_bit_binary_data': 'binary32'}
        before, after = types.get(conversion[1]), types.get(conversion[2])
        if before and after:
            add('conversion', {'operation': 'convert', 'source': 's', 'target': 'd',
                               'source_encoding': before, 'target_encoding': after})
    if re.search(r'\btransfer\b', intro) and '(s)' in intro and '(d)' in intro:
        if title.startswith(('transferring_', 'inverting_and_transferring_')):
            add('transfer', {'operation': 'invert_then_copy' if 'invert' in intro else 'copy',
                             'source': 's', 'target': 'd',
                             **({'width_bits': width} if width else {'width_bits': 1} if '1_bit' in title else {}),
                             'count': 'n' if '(n)' in intro else 1,
                             'repeat_source': 'identical' in intro})
    shift = re.search(r'shift.+?\((d|s)\).+?to the (right|left) by (\(n[12]?\)|1) (bit|word)', intro)
    if shift:
        add('shift', {'operation': 'shift', 'target': shift[1], 'direction': shift[2],
                      'distance': shift[3].strip('()'), 'unit': shift[4],
                      **({'width_bits': width} if width else {})})
    if title == 'batch_resetting_bit_devices' and 'reset (n) point(s) bit devices' in intro:
        add('reset', {'operation': 'clear', 'start': 'd', 'count': 'n', 'device_class': 'bit',
                      'timer_counter': {'current_value': 0, 'coil': False, 'contact': False},
                      'annunciator': {'remove_off_numbers': 'SD64..SD79', 'shift_remaining': 'forward',
                                      'active_count': 'SD63'}})
    if title == 'batch_resetting_devices' and 'devices of same type specified by (d1) and (d2)' in intro:
        add('reset', {'operation': 'clear', 'first': 'd1', 'last': 'd2',
                      'inclusive': True, 'same_device_type_required': True})
    from tools.fact_fx5_operation_review import operation_specs
    specs.extend(operation_specs(section, record))
    if specs and 'Flags (zero, carry and borrow) are not activated at this time.' in body:
        add('status_flags', {'flags': ['zero', 'carry', 'borrow'], 'updated': False})
    if specs and 'carry flag (SM700, SM8022) does not turn ON' in body:
        add('status_flags', {'flags': ['SM700', 'SM8022'], 'turn_on': False},
            [{'kind': 'operation_result', 'value': 'underflow_or_overflow'}])
    return specs


def _page_declarations(source):
    """Name declarations of every PART 3 page, read by the shared parser.

    Some instruction pages carry no section heading the review can bind to; the
    page still declares its names, so those pages publish an identity fact with the
    page itself as the binding, never a fabricated section.
    """
    rows = _rows(source, 'SELECT pdf_page, section_key, clean_text, outline_path, section FROM '
                         'page_artifacts WHERE manual_id=? AND outline_path GLOB '
                         "'PART [3456]*' ORDER BY pdf_page", (MANUAL_ID,))
    chapter_text = {}
    for row in rows:
        chapter_text.setdefault(row['section_key'], []).append(row['clean_text'] or '')
    found = []
    for row in rows:
        # The page supplies its own clean_text and its complete outline; the whole
        # section supplies the explicit comparison list, which is the only thing the
        # shared parser accepts as a reason to expand a placeholder.
        section_text = '\n'.join(chapter_text.get(row['section_key']) or ())
        try:
            names = instruction_declaration_names(row['clean_text'], row['outline_path'],
                                                  'programming', source_text=section_text)
        except Exception:  # pragma: no cover - parser rejects malformed pages by design
            continue
        if names:
            found.append((row['pdf_page'], row['outline_path'], row['section'], list(names)))
    return found

def _partition_condition_owners(specifications, opcode, section_owners):
    """Route only explicitly authored forms outside their containing section.

    A paragraph about EI inside DI's priority section is EI knowledge. Mere
    mentions, examples and unscoped conditions never create another owner.
    """
    own, related = [], {}
    for spec in specifications:
        forms = set(spec.get('scope_forms') or ())
        if not forms or opcode in forms:
            own.append({**spec, **({'scope_forms': [opcode]} if forms else {})})
        # Table-derived names include ST display aliases. They remain attached
        # to the containing declaration and do not create new identities.
        if (spec.get('value') or {}).get('complete_clause_review'):
            for target in forms - set(section_owners):
                related.setdefault(target, []).append({**spec, 'scope_forms': [target]})
    for target, specs in related.items():
        keys = {spec['key'] for spec in specs}
        for spec in specs:
            if not set(spec.get('depends_on_keys', ())) <= keys:
                raise ValueError('cross_owner_condition_dependency_not_bound:' + target + ':' + spec['key'])
    return own, related


def reviewed_fx5_programming_residual(source, entries=None):
    """Return ``(facts, receipt)`` for the residual FX5 programming knowledge."""
    from tools.fact_fx5_signatures import canonical_language_facts
    from tools.fact_fx5_ladder_signatures import canonical_ladder_facts
    from tools.fact_fx5_operation_review import section_conditions
    from tools.fact_fx5_standard_behavior import canonical_standard_behavior_facts
    from tools.fact_fx5_condition_review import condition_specs, condition_inventory
    manual = _manual(source)
    models = list(DOCUMENTED_MODELS)
    sections = _sections(source)
    register, universe_source = _review_universe(source, entries)
    declared = set()
    for section in sections:
        declared.update(_declarations(section))
    register['declared'] = declared
    register['unregistered'] = set()
    registry_names = set(register['registry']) | set(register['entries'])
    # Everything the manual prints is in scope: a name the runtime registry does not
    # ship yet is still published, but only as a knowledge identity -- it is never
    # promoted to a callable/executable instruction by this module.
    universe = registry_names | declared
    steps_forms, steps_pages = _parse_instruction_steps(source)
    steps_references = []
    for page in steps_pages:
        for chunk in _covering_chunks(source, page, page):
            reference = _chunk_reference(chunk)
            if reference not in steps_references:
                steps_references.append(reference)
    steps_by_opcode = {}
    for name, value in steps_forms.items():
        opcode = _universe_opcode(name, universe)
        if opcode:
            steps_by_opcode.setdefault(opcode, {})[name] = value
    geometry = _page_geometry(source)
    page_tables = _page_tables(source)
    execution_always, execution_references = _execution_rules(source)
    published = []
    model_conditions_by_fact = {}
    extra_page_references = {}
    steps_done = set()
    documented_pages = {}
    unparsed = []
    skipped_part = []
    split_pages = []
    already_covered = []
    condition_reviews = []
    seen_opcodes = set()
    for section in sections:
        parsed = _parse_section(section, universe)
        chapter = _instruction_chapter(section)
        candidates = _candidates(section, parsed, universe)
        register['unregistered'].update(section.get('unregistered_declarations') or [])
        if not candidates:
            if chapter:
                skipped_part.append({'pages': [section['first_page'], section['last_page']],
                                     'section_token': section['section_token'],
                                     'reason': 'no_registry_opcode_in_the_form_line'})
            continue
        references, pages, foreign = _page_references(source, section)
        if foreign:
            split_pages.append({'pages': [section['first_page'], section['last_page']],
                                'section_token': section['section_token'],
                                'dropped': foreign})
        section_owners = {record['opcode'] for record in candidates}
        related_conditions = {}
        for record in candidates:
            owner = COVERED_BY_SHIPPED_MODULES.get(record['opcode'])
            if owner:
                already_covered.append({'opcode': record['opcode'], 'owner': owner,
                                        'pages': list(pages),
                                        'section_token': section['section_token']})
            seen_opcodes.add(record['opcode'])
            for page in pages:
                documented_pages.setdefault(page, set()).add(record['opcode'])
            facts = []
            heading = parsed['heading']
            anchor = '%s_p%d' % (section['section_token'], pages[0])
            facts.append(_fact('fx5prog:form:%s:%s' % (record['opcode'], anchor),
                               'identity.form',
                               _form_value(section, record, manual, heading, pages,
                                           registry_member=record['opcode'] in registry_names),
                               references, models))
            facts.append(_fact('fx5prog:applicability:%s:%s' % (record['opcode'], anchor),
                               'identity.applicability',
                               _applicability_value(manual, pages, geometry),
                               references, models))
            if section['outline'].startswith(('PART 3 ', 'PART 4 ')):
                facts.append(_fact('fx5prog:execution:%s:%s' % (record['opcode'], anchor),
                                   'execution.trigger', _execution_value(record, execution_always, _declarations(section)),
                                   execution_references + references, models, languages=['ladder']))
            for spec in _behavior_specs(section, record):
                fact = _fact('fx5prog:behavior:%s:%s:%s' % (record['opcode'], anchor, spec['key']),
                             spec['dimension'], spec['value'], references, models,
                             languages=spec.get('languages'), status=spec.get('status', 'source_verified'))
                if spec.get('conditions'):
                    fact['conditions'] = spec['conditions']
                facts.append(fact)
            for index, spec in enumerate(canonical_standard_behavior_facts(section, parsed, record)):
                fact = _fact('fx5prog:standard:%s:%s:%d' % (record['opcode'], anchor, index),
                             spec['dimension'], spec['value'], references, models,
                             languages=[spec['language']], status=spec['status'])
                if spec.get('conditions'):
                    fact['conditions'] = spec['conditions']
                facts.append(fact)
            for index, language_fact in enumerate(canonical_language_facts(
                    section, parsed, record, page_words=geometry)):
                language_sources = [reference for reference in references
                                    if language_fact['page'] in reference.get('pdf_pages', ())] or references
                language_sources = list(language_sources)
                for page in language_fact.get('additional_source_pages', ()):
                    if page not in extra_page_references:
                        extra_page_references[page] = [_chunk_reference(chunk)
                            for chunk in _covering_chunks(source, page, page)]
                    language_sources.extend(reference for reference in extra_page_references[page]
                                            if reference not in language_sources)
                language_sources.extend(reference for reference in
                                        _additional_source_references(source, language_fact, extra_page_references)
                                        if reference not in language_sources)
                fact = _fact('fx5prog:language:%s:%s:%d' % (record['opcode'], anchor, index),
                             language_fact['dimension'], language_fact['value'], language_sources, models,
                             languages=[language_fact['language']], status=language_fact['status'])
                if language_fact.get('conditions'):
                    fact['conditions'] = language_fact['conditions']
                if language_fact.get('model_conditions'):
                    model_conditions_by_fact[fact['id']] = language_fact['model_conditions']
                facts.append(fact)
                if language_fact['dimension'] in {'parameters.st_signature', 'parameters.fbd_signature',
                                                   'parameters.ladder_signature'}:
                    operands = [dict(item) for item in language_fact['value']['parameters']
                                if item['name'] not in {'EN', 'ENO'}]
                    typed = _fact(fact['id'] + ':operands', 'parameters.typed_operands',
                                  {'operands': operands, 'operand_count': len(operands),
                                   'declaration_kind': language_fact['value']['declaration_kind'],
                                   **({'call_form': language_fact['value']['call_form']}
                                      if language_fact['value'].get('call_form') else {})},
                                  language_sources, models, languages=[language_fact['language']],
                                  status=language_fact['status'])
                    if fact.get('conditions'):
                        typed['conditions'] = fact['conditions']
                    if fact['id'] in model_conditions_by_fact:
                        model_conditions_by_fact[typed['id']] = model_conditions_by_fact[fact['id']]
                    facts.append(typed)
            for index, native_fact in enumerate(canonical_ladder_facts(section, record, page_words=geometry)):
                facts.append(_fact('fx5prog:ladder:%s:%s:%d' % (record['opcode'], anchor, index),
                                   native_fact['dimension'], native_fact['value'], references, models,
                                   languages=['ladder'], status=native_fact['status']))
            condition_record = {**record, 'signature_facts': [fact for fact in facts if fact['dimension'] in {
                'parameters.typed_operands', 'parameters.ladder_signature',
                'parameters.st_signature', 'parameters.fbd_signature'}]}
            reviewed_conditions, related = _partition_condition_owners(
                condition_specs(section, condition_record, include_other_forms=True), record['opcode'], section_owners)
            for target, specifications in related.items():
                if target not in universe:
                    raise ValueError('explicit_condition_owner_not_declared:' + target)
                bucket = related_conditions.setdefault(target, {})
                for spec in specifications:
                    previous = bucket.get(spec['key'])
                    if previous is not None and previous != spec:
                        raise ValueError('cross_owner_condition_binding_conflict:' + target + ':' + spec['key'])
                    bucket[spec['key']] = spec
            condition_ids = {spec['key']: 'fx5prog:condition:%s:%s:%s' %
                             (record['opcode'], anchor, spec['key']) for spec in reviewed_conditions}
            for spec in reviewed_conditions:
                condition_sources = []
                for page in spec.get('pages', [spec['page']]):
                    if page not in extra_page_references:
                        extra_page_references[page] = [_chunk_reference(chunk)
                            for chunk in _covering_chunks(source, page, page)]
                    condition_sources.extend(reference for reference in extra_page_references[page]
                                             if reference not in condition_sources)
                fact = _fact(condition_ids[spec['key']], spec['dimension'], spec['value'],
                             condition_sources, models, languages=spec.get('languages'),
                             status=spec['status'])
                if spec.get('conditions'):
                    fact['conditions'] = spec['conditions']
                if spec.get('depends_on_keys'):
                    fact['depends_on'] = [condition_ids[key] for key in spec['depends_on_keys']]
                facts.append(fact)
            condition_reviews.append({'opcode': record['opcode'], 'section': section['section_token'],
                                      **condition_inventory(section, condition_record)})
            # A literal absence-of-error statement is itself a useful condition;
            # error causes above retain complete rows instead of a ten-word key.
            if not any(spec['value'].get('source_kind') == 'error' for spec in reviewed_conditions) \
                    and _contains(section['text'], 'There is no operation error'):
                facts.append(_fact('fx5prog:error:%s:%s' % (record['opcode'], anchor),
                                   'effects.operation_error',
                                   {'codes': [], 'code_count': 0,
                                    'documented_as': 'no_operation_error_statement'},
                                   references, models))
            if (record['opcode'] in steps_by_opcode
                    and record['opcode'] not in steps_done):
                steps_done.add(record['opcode'])
                facts.append(_fact('fx5prog:steps:%s:%s'
                                   % (record['opcode'], 'appendix2_p%d' % steps_pages[0]),
                                   'resources.instruction_steps',
                                   _steps_value(steps_by_opcode[record['opcode']], steps_pages),
                                   steps_references, models))
            processing = _processing_value(section, page_tables)
            if processing:
                facts.append(_fact('fx5prog:processing:%s:%s' % (record['opcode'], anchor),
                                   'effects.processing_result', processing,
                                   references, models))
            if not facts:
                unparsed.append({'pages': [section['first_page'], section['last_page']],
                                 'opcode': record['opcode'],
                                 'reason': 'no_parseable_instruction_block'})
                continue
            for fact in facts:
                if 'SFC' in section['outline'] or 'STEP LADDER' in section['outline']:
                    fact['value'].setdefault('instruction_variant', section['section_token'])
                # A runtime definition owns one literal instruction. Language
                # call aliases remain in the value, not in its owner scope.
                fact['scope']['forms'] = [record['opcode']]
                if not fact['dimension'].startswith('identity.'):
                    fact['conditions'] = [*section_conditions(section), *fact.get('conditions', [])]
                    if not fact['conditions']:
                        fact.pop('conditions')
            published.append((record['opcode'], facts))

        for target, by_key in related_conditions.items():
            anchor = '%s_p%d' % (section['section_token'], pages[0])
            facts = [_fact('fx5prog:related_applicability:%s:%s' % (target, anchor),
                           'identity.applicability', _applicability_value(manual, pages, geometry), references, models)]
            identities = {key: 'fx5prog:related_condition:%s:%s:%s' % (target, anchor, key) for key in by_key}
            for key, spec in by_key.items():
                condition_sources = []
                for page in spec.get('pages', [spec['page']]):
                    if page not in extra_page_references:
                        extra_page_references[page] = [_chunk_reference(chunk)
                            for chunk in _covering_chunks(source, page, page)]
                    condition_sources.extend(reference for reference in extra_page_references[page]
                                             if reference not in condition_sources)
                fact = _fact(identities[key], spec['dimension'], spec['value'], condition_sources, models,
                             languages=spec.get('languages'), status=spec['status'])
                fact['scope']['forms'] = [target]
                conditions = [*section_conditions(section), *spec.get('conditions', [])]
                if conditions:
                    fact['conditions'] = conditions
                if spec.get('depends_on_keys'):
                    fact['depends_on'] = [identities[dependency] for dependency in spec['depends_on_keys']]
                facts.append(fact)
            published.append((target, facts))

    page_identity = []
    for page, outline, section_title, names in _page_declarations(source):
        if page in documented_pages:
            continue
        references = []
        for chunk in _covering_chunks(source, page, page):
            reference = _chunk_reference(chunk)
            if reference not in references:
                references.append(reference)
        if not references:
            continue
        for name in names:
            base = _base_name(name, registry_names, declared)
            opcode = _universe_opcode(name, universe) or (base if base in universe else '')
            if not opcode or (page, opcode) in page_identity:
                continue
            if opcode in COVERED_BY_SHIPPED_MODULES:
                continue
            page_identity.append((page, opcode))
            anchor = '%s_p%d' % (_token(section_title or 'page'), page)
            published.append((opcode, [
                _fact('fx5prog:form:%s:%s' % (opcode, anchor), 'identity.form',
                      _page_form_value(manual, page, outline, section_title, name, opcode,
                                       registry_names), references, models),
                _fact('fx5prog:applicability:%s:%s' % (opcode, anchor),
                      'identity.applicability',
                      _applicability_value(manual, [page], geometry), references, models)]))
            seen_opcodes.add(opcode)

    facts = {}
    for opcode, rows in published:
        for model in models:
            bucket = facts.setdefault((model, opcode), [])
            prerequisites = ['%s:%s' % (row['id'], model) for row in rows
                             if row['dimension'] == 'identity.applicability']
            for fact in rows:
                clone = dict(fact)
                clone['id'] = '%s:%s' % (fact['id'], model)
                clone['scope'] = dict(fact['scope'], models=[model], forms=[opcode])
                model_conditions = model_conditions_by_fact.get(fact['id'], {}).get(model, [])
                if model_conditions:
                    clone['conditions'] = [*fact.get('conditions', []), *model_conditions]
                if clone['dimension'] != 'identity.applicability':
                    clone['depends_on'] = [*prerequisites,
                                          *[dependency + ':' + model for dependency in fact.get('depends_on', ())]]
                bucket.append(clone)

    coverage = {}
    for name in sorted(universe):
        if name in seen_opcodes or name in COVERED_BY_SHIPPED_MODULES:
            continue
        note = NOT_PRINTED_HERE.get(name) or {}
        coverage[name] = note.get('observation') or 'mnemonic_not_printed_in_this_manual'
    not_printed = _verify_not_printed(source, sections, universe, seen_opcodes)
    definitions = validate_definitions(facts, universe)

    steps_published = sorted({key[1] for key, rows in facts.items()
                              if any(row['dimension'] == 'resources.instruction_steps'
                                     for row in rows)})
    by_dimension = {}
    by_status = {}
    for rows in facts.values():
        for fact in rows:
            by_dimension[fact['dimension']] = by_dimension.get(fact['dimension'], 0) + 1
            by_status[fact['status']] = by_status.get(fact['status'], 0) + 1
    receipt = {
        'method': METHOD,
        'manual': {'manual_id': MANUAL_ID, 'manual_number': manual['manual_number'],
                   'revision': manual['revision'], 'manual_type': manual['manual_type'],
                   'pdf_pages': manual['pdf_pages'],
                   'title_token': _token(manual['title'] or '')},
        'models': models,
        'counts': {
            'facts': sum(len(rows) for rows in facts.values()),
            'owner_keys': len(facts),
            'sections_published': len(published),
            'reviewed_names_documented': len(seen_opcodes),
            'by_dimension': dict(sorted(by_dimension.items())),
            'by_status': dict(sorted(by_status.items())),
        },
        'shared_parser': {'module': SHARED_PARSER,
                          'entry_point': 'instruction_declaration_names',
                          'binding_basis': 'shared_declaration_parser'},
        'condition_review': condition_reviews,
        'universe': _universe_report(register, universe_source, seen_opcodes),
        'scope_policy': {
            'models': list(DOCUMENTED_MODELS),
            'models_basis': SCOPE_BASIS,
            'per_model_support': PER_MODEL_SUPPORT,
            'support_basis': SUPPORT_BASIS,
            'per_model_support_facts': by_dimension.get('identity.applicability', 0),
            'rule': 'cpu_header_marks_with_the_manual_legend_and_instruction_conditions',
        },
        'coverage': {
            'reviewed_names': len(universe),
            'documented_here': sorted(seen_opcodes),
            'documented_here_count': len(seen_opcodes),
            # a name the manual prints but the runtime registry does not ship is
            # published as a knowledge identity only: no call_available, no Core
            # executable, no automatic registration
            'registry_documented': sorted(set(seen_opcodes) & registry_names),
            'registry_documented_count': len(set(seen_opcodes) & registry_names),
            'source_derived_identity_only': sorted(set(seen_opcodes) - registry_names),
            'source_derived_identity_only_count': len(set(seen_opcodes) - registry_names),
            'not_documented_here': dict(sorted(coverage.items())),
            'not_documented_here_count': len(coverage),
            'owned_by_shipped_modules': sorted(set(universe) & set(COVERED_BY_SHIPPED_MODULES)),
        },
        'skipped_sections': {
            'count': len(skipped_part),
            'reason': 'instruction_or_function_page_without_a_parsed_declaration',
            'sample': skipped_part[:10],
        },
        'unparsed': {'count': len(unparsed), 'entries': unparsed[:10]},
        'not_printed_verification': not_printed,
        'definition_validation': definitions,
        'instruction_steps': {
            'table': 'appendix_2_number_of_instruction_steps',
            'pages': [steps_pages[0], steps_pages[-1]] if steps_pages else [],
            'printed_rows': len(steps_forms),
            'published_opcodes': len(steps_published),
            'registry_without_a_row': sorted(set(universe) - set(steps_by_opcode)),
            'row_rule': 'one_printed_line_per_instruction_name_including_operand_classes',
        },
        'already_covered': {
            'count': len(already_covered),
            'reason': 'supplement_owns_behavior_this_review_keeps_parameters_and_conditions',
            'entries': already_covered,
        },
        'page_binding': {
            'rule': 'a_page_fact_page_must_be_covered_by_a_chunk_of_the_same_outline',
            'sections_with_dropped_pages': len(split_pages),
            'dropped': split_pages[:10],
        },
        'excluded_parts': [
            {'part': 'PART 2 INSTRUCTION/FUNCTION LIST',
             'reason': 'index_part_without_instruction_pages'},
        ],
        'binding': {
            'rule': 'section_pages_are_grouped_by_their_own_outline_path',
            'chunk_rule': 'a_chunk_must_carry_the_same_outline_path',
            'per_cpu_availability': 'header_glyphs_bound_to_manual_legend',
        },
        'guarantees': {
            'database_mutated': False,
            'pdf_read_at_runtime': False,
            'network_at_runtime': False,
            'manual_prose_published': False,
            'executable_behavior_keys': 0,
            'call_available_promotions': 0,
            'cpu_widening': 0,
            'native_experiment': 'not_performed',
        },
    }
    return facts, receipt

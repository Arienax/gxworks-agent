
from tools.offline_inputs import private_path
import json
import re
import sqlite3
from pathlib import Path

import pytest
pytestmark = pytest.mark.usefixtures('private_manual_evidence')

from knowledge.retriever import retrieve_fact_aware_knowledge
from knowledge.structured_facts import resolve_instruction_records
from shared.paths import resource_path


@pytest.mark.parametrize('form', ['*', '**', '&', '/', '+', '-', '=', '<>', '<=', '>=', '*P', '/P', '*P_U', '/_U'])
def test_symbolic_instruction_forms_survive_the_fact_projection(form):
    from knowledge.fact_corpus import typed_value, UnresolvedExpression
    assert typed_value({'form': form}) == {'form': form}
    with pytest.raises(UnresolvedExpression):
        typed_value({'condition': 'previous scan value is true'})






















def test_incomplete_conflicting_signature_is_a_source_conflict_not_an_absent_fact():
    from knowledge.structured_facts import _aspect_state, instruction_aspect_gap_reasons
    conflict = {'id': 'signature', 'dimension': 'parameters.st_signature', 'status': 'conflict',
                'scope': {'models': ['Q03UDV'], 'languages': ['st']},
                'value': {'parameters': [{'name': 'd', 'type': 'unknown', 'direction': 'output'}],
                          'returns': 'BOOL', 'enable': True, 'source_disposition': 'source_conflict'}}
    packet = {'target': 'WXOR', 'value': {'facts': [conflict]}}
    state = _aspect_state(packet, 'st_call', model='Q03UDV', language='st', query='')
    assert state == 'unresolved'
    assert instruction_aspect_gap_reasons(packet, 'st_call', state=state, model='Q03UDV', language='st') == ['source_conflict']
    # A second CPU cannot inherit the first CPU's conflicting manual panel.
    good = {'id': 'other_cpu_signature', 'dimension': 'parameters.st_signature', 'status': 'source_verified',
            'scope': {'models': ['L02'], 'languages': ['st']},
            'value': {'name': 'WXOR', 'parameters': [], 'returns': 'BOOL', 'enable': False},
            'sources': [{'manual_id': 'fixture', 'pdf_page': 1}], 'verification': 'source_checked'}
    packet['value']['facts'].append(good)
    assert _aspect_state(packet, 'st_call', model='L02', language='st', query='') == 'answered'


@pytest.mark.parametrize('language', ['st', 'fbd'])
@pytest.mark.parametrize('aspect', ['arity', 'order', 'roles', 'types'])
def test_source_declined_call_parameters_are_not_missing_knowledge(language, aspect):
    from knowledge.structured_facts import _aspect_state
    fact = {'id': 'unavailable', 'dimension': 'parameters.' + language + '_signature',
            'value': {'available': False, 'reason': 'source_states_call_not_supported'},
            'scope': {'models': ['Q03UDV'], 'languages': [language]},
            'status': 'source_verified', 'verification': 'source_checked',
            'sources': [{'manual_id': 'fixture', 'pdf_page': 1}]}
    packet = {'target': 'LD=', 'value': {'facts': [fact]}}
    assert _aspect_state(packet, aspect, model='Q03UDV', language=language, query='') == 'not_applicable'
    fact['depends_on'] = ['unresolved_cpu_applicability']
    assert _aspect_state(packet, aspect, model='Q03UDV', language=language, query='') == 'unresolved'


def test_literal_st_expression_keeps_its_operands_when_function_call_is_unavailable():
    from knowledge.structured_facts import _aspect_state
    common = {'scope': {'models': ['Q03UDV'], 'languages': ['st']},
              'status': 'source_verified', 'verification': 'source_checked',
              'sources': [{'manual_id': 'fixture', 'pdf_page': 1}]}
    facts = [{**common, 'id': 'no_function', 'dimension': 'parameters.st_signature',
              'value': {'available': False, 'reason': 'literal_ST_operator_is_an_expression'}},
             {**common, 'id': 'expression', 'dimension': 'parameters.language_expression',
              'value': {'available': True, 'operator': '**'}},
             {**common, 'id': 'operands', 'dimension': 'parameters.typed_operands',
              'value': {'operands': [{'name': 's1', 'role': 'input', 'data_type': 'ANY_REAL'},
                                     {'name': 's2', 'role': 'input', 'data_type': 'ANY_NUM'}]}}]
    packet = {'target': '**', 'value': {'facts': facts}}
    for aspect in ['arity', 'order', 'roles', 'types']:
        assert _aspect_state(packet, aspect, model='Q03UDV', language='st', query='') == 'answered'
    assert _aspect_state(packet, 'st_call', model='Q03UDV', language='st', query='') == 'not_applicable'








def test_compiler_keeps_sibling_titles_and_all_reference_dependencies():
    from knowledge.instruction_compiler import _document_catalog
    manual = {'manual_number': 'fixture', 'manual_type': 'programming', 'plc_models': ['FX5U']}
    def chunk(identity, section, text, outline=None):
        return {'id': identity, 'manual_id': 'fixture', 'revision': 'A',
                'section': section, 'outline_path': outline or section,
                'text': text, 'pdf_page': identity, 'pdf_page_end': identity,
                'plc_models': 'FX5U'}
    chunks = [chunk(1, '1.1 LD', '\n'.join('See section 1.' + str(i) for i in range(2, 19)))]
    chunks.extend(chunk(i, '1.' + str(i) + ' reference',
                        'See section 1.19' if i == 18 else '') for i in range(2, 19))
    chunks.append(chunk(19, '1.19 reference', 'See section 1.1'))
    chunks.extend([
        chunk(20, 'Outline of operation', 'MOV(P)\nTransfers the source value.',
              'PART 3 CPU MODULE INSTRUCTIONS > Move > Outline of operation'),
        chunk(21, 'Outline of operation', 'INC(P)\nThis instruction increments the value.',
              'PART 3 CPU MODULE INSTRUCTIONS > Increment > Outline of operation')])
    documents, forms, _ = _document_catalog(chunks, {'fixture': manual})
    root = next(d for d in documents if d['section'] == '1.1 LD')
    assert len(root['explicit_references']) == 17
    assert len(root['reference_dependency_ids']) == 18
    assert root['id'] not in root['reference_dependency_ids']
    assert {tuple(d['source_ids']) for d, _ in forms['MOV']} == {('20',)}
    assert {tuple(d['source_ids']) for d, _ in forms['INC']} == {('21',)}










def test_iqf_instruction_heading_requires_its_own_declaration():
    from tools.build_fx3u_knowledge_v3 import detect_instruction, compile_instruction_re
    vocabulary = compile_instruction_re({'PID', 'MOV'})
    heading = 'PART 3 CPU MODULE INSTRUCTIONS > 10 PID CONTROL INSTRUCTION > 10.1 PID Control Loop'
    assert detect_instruction('10.1 PID Control Loop\nPID\nThis instruction executes PID control.\nSetting data',
                              heading, 'programming', vocabulary)[0] == 'PID'
    assert detect_instruction('Precautions\nUse MOV in an example.\nPID D0 D1 D2 D3',
                              heading, 'programming', vocabulary)[0] == ''
    assert detect_instruction('PID\nThis instruction executes PID control.',
                              '1.1 Reading instruction examples', 'programming', vocabulary)[0] == ''


@pytest.mark.parametrize('text', ['Setting data', 'FBD/LD', 'Structured text',
                                   'Variable\nDescription\nData type', 'Ladder diagram',
                                   'Input/output argument\nName\nDescription\nData type'])
def test_language_parameter_pages_are_kept_even_before_instruction_identity_is_known(text):
    from tools.build_fx3u_knowledge_v3 import HIGH_FIDELITY_RE
    assert HIGH_FIDELITY_RE.search(text)


def test_parameter_cell_geometry_preserves_a_merged_type_and_every_word():
    from types import SimpleNamespace
    from tools.build_fx3u_knowledge_v3 import extract_tables_and_geometry
    found = SimpleNamespace(bbox=(0, 0, 20, 30),
        extract=lambda: [['Operand', 'Data type'], ['s', 'ANY16'], ['d1', None]],
        rows=[SimpleNamespace(cells=[(0, 0, 10, 10), (10, 0, 20, 10)]),
              SimpleNamespace(cells=[(0, 10, 10, 20), (10, 10, 20, 30)]),
              SimpleNamespace(cells=[(0, 20, 10, 30), None])])
    word = dict(text='D0', x0=0, x1=10, top=0, bottom=10)
    page = SimpleNamespace(find_tables=lambda: [found], extract_words=lambda **kw: [word] * 6001,
                           width=20, height=30, images=[], lines=[], rects=[], curves=[])
    _, words, visual = extract_tables_and_geometry(page, include_tables=True)
    assert len(words) == 6001
    assert visual['parameter_tables_version'] == 1
    cells = visual['parameter_tables'][0]['cells']
    assert {'bbox': [10, 10, 20, 30], 'text': 'ANY16'} in cells
    assert sum(cell['text'] == 'ANY16' for cell in cells) == 1


def test_open_parameter_border_uses_printed_header_endpoints_and_keeps_internal_cells():
    from types import SimpleNamespace
    from tools.build_fx3u_knowledge_v3 import close_parameter_table_borders
    original = SimpleNamespace(bbox=(10, 20, 90, 100), cells=[1, 2])
    closed = SimpleNamespace(bbox=(0, 20, 100, 100), cells=[1, 2, 3])
    calls = []
    def find(settings):
        calls.append(settings)
        return [closed]
    page = SimpleNamespace(width=120, height=150,
        edges=[dict(orientation='h', top=20, width=100, x0=0, x1=100)],
        find_tables=lambda: [original],
        crop=lambda bounds: SimpleNamespace(find_tables=find))
    assert close_parameter_table_borders(page, original) is closed
    assert calls[0]['explicit_vertical_lines'] == [0, 100]
    page.edges = []
    assert close_parameter_table_borders(page, original) is original


@pytest.mark.parametrize('interior_columns,next_table_top,expected_bottom',
                         [([30, 70], 106, 105), ([30], 106, 94), ([30, 70], 96, 94)])
def test_parameter_table_bottom_requires_printed_column_ends_and_respects_next_table(
        interior_columns, next_table_top, expected_bottom):
    from types import SimpleNamespace
    from tools.build_fx3u_knowledge_v3 import close_parameter_table_borders
    table = SimpleNamespace(bbox=(0, 20, 100, 90), cells=[1, 2])
    next_table = SimpleNamespace(bbox=(0, next_table_top, 100, 140))
    edges = [dict(orientation='h', top=20, bottom=20, width=100, x0=0, x1=100),
             dict(orientation='h', top=101, bottom=101, width=100, x0=0, x1=100)]
    edges.extend(dict(orientation='v', top=20, bottom=101, x0=x, x1=x) for x in interior_columns)
    crops = []
    def crop(bounds):
        crops.append(bounds)
        return SimpleNamespace(find_tables=lambda settings: [])
    page = SimpleNamespace(width=120, height=160, edges=edges, find_tables=lambda: [table, next_table], crop=crop)
    assert close_parameter_table_borders(page, table) is table
    assert crops[0][3] == expected_bottom


def test_existing_index_backfills_late_instruction_geometry_without_replacing_source_scope():
    from types import SimpleNamespace
    from tools.build_fx3u_knowledge_v3 import backfill_instruction_geometry
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE instructions(manual_id, source_pages_json)')
    db.execute('INSERT INTO instructions VALUES(?,?)', ('manual', '[3]'))
    db.execute('CREATE TABLE page_artifacts(id,manual_id,pdf_page,plain_text,clean_text,outline_path,'
               'instruction_opcode,chunk_type,word_geometry_json,visual_json,fidelity_flags,layout_text,compact_layout)')
    original = {'geometry_extracted': False, 'source_applicability': {'model': 'FX3U'}, 'table_count': 1}
    db.execute('INSERT INTO page_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (1, 'manual', 3, 'Variable\nData type', 'Variable\nData type', 'BK+', '', 'section', '[]',
         json.dumps(original), 'plain_text', 'old layout', 'old compact'))
    words = [{'text': 'BK+', 'x0': 1, 'x1': 10, 'top': 1, 'bottom': 10}]
    overlay = {'manual_id': 'manual', 'manual_number': 'number', 'revision': 'M',
               'pages': {'3': {'words': words, 'visual': {'parameter_tables_version': 1,
                   'parameter_tables': [], 'source_applicability': {'wrong_model': 'FX5U'}, 'table_count': 0}}}}
    manual = SimpleNamespace(id='manual', manual_number='number', revision='M', path=Path('must-not-open.pdf'))
    result = backfill_instruction_geometry(db, manual, overlay=overlay)
    assert result['recovered_pages'] == result['parameter_table_geometry_pages'] == [3]
    row = db.execute('SELECT clean_text,word_geometry_json,visual_json FROM page_artifacts').fetchone()
    assert row[0] == 'Variable\nData type' and json.loads(row[1]) == words
    assert json.loads(row[2])['source_applicability'] == {'model': 'FX3U'}
    assert json.loads(row[2])['table_count'] == 1
    assert backfill_instruction_geometry(db, manual)['requested_pages'] == 0
    # A later source-derived cell overlay replaces incomplete cell geometry
    # even when the page already has words. It does not reopen the PDF.
    overlay['pages']['3']['visual']['parameter_tables'] = [{'bbox': [0, 0, 10, 30], 'cells': []}]
    assert backfill_instruction_geometry(db, manual, overlay=overlay)['recovered_pages'] == [3]
    assert json.loads(db.execute('SELECT visual_json FROM page_artifacts').fetchone()[0])[
        'parameter_tables'] == overlay['pages']['3']['visual']['parameter_tables']
    with pytest.raises(ValueError, match='different manual'):
        backfill_instruction_geometry(db, manual, overlay={**overlay, 'revision': 'N'})


@pytest.mark.parametrize('text,expected', [
    ('LDP, LDF, ANDP, ANDF, ORP, ORF\n• LDP: Rising edge pulse operation start instruction',
     ['LDP', 'LDF', 'ANDP', 'ANDF', 'ORP', 'ORF']),
    ('MUL(P)(_U)\nThese instructions multiply binary data.', ['MUL', 'MUL_U', 'MULP', 'MULP_U']),
    ('S(P).DEVLD\nReads data from the data memory.', ['S.DEVLD', 'SP.DEVLD']),
    ('SP .DEVST\nThis instruction writes data.', ['SP.DEVST']),
    ('EVAL(P)/DEVAL(P)\nThese instructions convert strings.', ['EVAL', 'EVALP', 'DEVAL', 'DEVALP']),
    ('D/(P)(_U)\nThese instructions divide binary data.', ['D/', 'D/_U', 'D/P', 'D/P_U']),
    ('$MOV(P)\nThese instructions transfer strings.', ['$MOV', '$MOVP']),
    ('WAND(P) [using two operands]\nThese instructions AND each bit.', ['WAND', 'WANDP']),
    ('OUT T, OUTH T, OUTHS T, OUT ST\nThe timer counts up to the set value.', ['OUT', 'OUTH', 'OUTHS']),
    ('Operation error\nMOV D0 D1\nThis instruction is only an example.', []),
    ('Data\nD0\nThese instructions write a result.', []),
    ('-123\nThese instructions use a negative example value.', []),
    ('-(P)\nThese instructions subtract binary data.', ['-', '-P']),
])
def test_iqf_declarations_keep_each_literal_form(text, expected):
    from knowledge.instruction_document import instruction_declaration_names
    assert instruction_declaration_names(text,
        'PART 3 CPU MODULE INSTRUCTIONS > 8.12 Index Register Operation Instruction > Saving data',
        'programming') == expected


def test_iqf_comparison_forms_require_same_section_placeholder_definition():
    from knowledge.instruction_document import instruction_declaration_names
    text = 'LD[GLYPH-F06F](_U)\nThese instructions compare binary data.'
    outline = 'PART 3 CPU MODULE INSTRUCTIONS > Comparing data'
    assert instruction_declaration_names(text, outline, 'programming') == []
    source = text + '\n("=(_U)", "<>(_U)", ">(_U)" enters [GLYPH-F06F].)'
    assert instruction_declaration_names(text, outline, 'programming', source_text=source) == [
        'LD=', 'LD=_U', 'LD<>', 'LD<>_U', 'LD>', 'LD>_U']
    block = 'DBKCMP[GLYPH-F06F](P)(_U)\nThese instructions compare binary data.'
    source = block + '\n("DBKCMP=(P)(_U)", "DBKCMP<>(P)(_U)" enters [GLYPH-F06F].)'
    assert instruction_declaration_names(block, outline, 'programming', source_text=source) == [
        'DBKCMP=', 'DBKCMP=_U', 'DBKCMP=P', 'DBKCMP=P_U',
        'DBKCMP<>', 'DBKCMP<>_U', 'DBKCMP<>P', 'DBKCMP<>P_U']


@pytest.mark.parametrize('text,outline,expected', [
    ('BOOL_TO_WORD(_E)\nThese functions convert BOOL type data.',
     'PART 5 STANDARD FUNCTIONS > 22 TYPE CONVERSION FUNCTIONS > 22.1 Converting BOOL to WORD',
     ['BOOL_TO_WORD', 'BOOL_TO_WORD_E']),
    ('AND(_E), OR(_E), XOR(_E)\n• AND(_E): Outputs the logical product.',
     'PART 5 STANDARD FUNCTIONS > 26 STANDARD BITWISE BOOLEAN FUNCTIONS > 26.1 Operations',
     ['AND', 'AND_E', 'OR', 'OR_E', 'XOR', 'XOR_E']),
    ('GET_BOOL_ADDR, GET_INT_ADDR, GET_WORD_ADDR\nThese functions output the input variable.',
     'PART 5 STANDARD FUNCTIONS > 22 TYPE CONVERSION FUNCTIONS > 22.57 Type conversion',
     ['GET_BOOL_ADDR', 'GET_INT_ADDR', 'GET_WORD_ADDR']),
    ('G(P).UINI\nThese instructions specify the station number.',
     'PART 4 MODULE DEDICATED INSTRUCTION > 19 CC-Link IE TSN INSTRUCTION > 19.1 Setting',
     ['G.UINI', 'GP.UINI']),
    ('G(P).CCPASET\nSet the parameters to the module.',
     'PART 4 MODULE DEDICATED INSTRUCTION > 20 CC-Link IE Field Network INSTRUCTION > 20.1 Setting parameters',
     ['G.CCPASET', 'GP.CCPASET']),
    ('ADD(_E)\nThese functions output the sum.',
     'PART 5 STANDARD FUNCTIONS > 24 ARITHMETIC OPERATION FUNCTIONS > Program examples', []),
    ('Operation example\nADD D0 D1 D2\nThese functions output the sum.',
     'PART 5 STANDARD FUNCTIONS > 24 ARITHMETIC OPERATION FUNCTIONS > 24.1 Addition', []),
    ('PART 5 STANDARD FUNCTIONS\nThis part consists of the following chapters.',
     'PART 5 STANDARD FUNCTIONS', []),
])
def test_iqf_standard_and_module_declarations_are_source_scoped(text, outline, expected):
    from knowledge.instruction_document import instruction_declaration_names
    assert instruction_declaration_names(text, outline, 'programming') == expected


def test_iqf_part5_declared_inventory_covers_every_source_function():
    """The explicit manual inventory, not the parser's own output, is expected."""
    from knowledge.instruction_document import instruction_declaration_names
    conversions = {
        'BOOL': 'WORD DWORD INT DINT TIME STRING',
        'WORD': 'BOOL DWORD INT DINT TIME',
        'DWORD': 'BOOL WORD INT DINT TIME',
        'INT': 'BOOL WORD DWORD DINT BCD REAL TIME STRING',
        'DINT': 'BOOL WORD DWORD INT BCD REAL TIME STRING',
        'BCD': 'INT DINT', 'REAL': 'INT DINT STRING',
        'TIME': 'BOOL WORD DWORD INT DINT STRING', 'STRING': 'BOOL INT DINT REAL TIME',
    }
    bases = {source + '_TO_' + target for source, targets in conversions.items()
             for target in targets.split()}
    bases.update(('BITARR_TO_INT BITARR_TO_DINT INT_TO_BITARR DINT_TO_BITARR CPY_BITARR '
                  'GET_BIT_OF_INT SET_BIT_OF_INT CPY_BIT_OF_INT ABS SQRT LN LOG EXP SIN COS TAN '
                  'ASIN ACOS ATAN ADD MUL SUB DIV MOD EXPT MOVE SHL SHR ROL ROR AND OR XOR NOT '
                  'SEL MAX MIN LIMIT MUX GT GE EQ LE LT NE LEN LEFT RIGHT MID CONCAT INSERT '
                  'DELETE REPLACE FIND ADD_TIME SUB_TIME MUL_TIME DIV_TIME').split())
    expected = bases | {name + '_E' for name in bases} | {
        'GET_BOOL_ADDR', 'GET_INT_ADDR', 'GET_WORD_ADDR'}
    path = private_path('resources/knowledge/fx3u_knowledge_scoped.sqlite')
    source = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = source.execute("SELECT outline_path, clean_text FROM page_artifacts "
                              "WHERE manual_id='fx5_programming_ab' AND outline_path LIKE 'PART 5%' "
                              'ORDER BY pdf_page').fetchall()
    finally:
        source.close()
    actual = set()
    for outline, text in rows:
        actual.update(instruction_declaration_names(text, outline, 'programming'))
    assert len(expected) == 215
    assert actual == expected


@pytest.mark.parametrize('text,expected', [
    ('SR(_E)\nThese function blocks judge two input values.', ['SR', 'SR_E']),
    ('R_TRIG(_E)\nThese functions detect the rising edge of a signal.', ['R_TRIG', 'R_TRIG_E']),
    ('COUNTER_FB_M\nWhen the execution condition is established, this function block starts counting.',
     ['COUNTER_FB_M']),
    ('TIMER_[GLYPH-F06F]_M\nWhen the execution condition is established, these function blocks start counting.\n'
     '([GLYPH-F06F] indicates TIMER_1_FB_M, TIMER_10_FB_M, TIMER_100_FB_M,\n'
     'TIMER_CONT_FB_M, TIMER_CONTHS_FB_M.)',
     ['TIMER_1_FB_M', 'TIMER_10_FB_M', 'TIMER_100_FB_M', 'TIMER_CONT_FB_M', 'TIMER_CONTHS_FB_M']),
    ('TIMER_[GLYPH-F06F]_M\nWhen the execution condition is established, these function blocks start counting.', []),
])
def test_iqf_function_block_declarations_require_literal_source_names(text, expected):
    from knowledge.instruction_document import instruction_declaration_names
    assert instruction_declaration_names(text,
        'PART 6 FUNCTION BLOCKS > 34 TIMER FUNCTION BLOCKS > 34.4 Timer Function Blocks',
        'programming') == expected


def test_iqf_part6_declared_inventory_covers_every_source_function_block():
    from knowledge.instruction_document import instruction_declaration_names
    base = set('SR RS R_TRIG F_TRIG CTU CTD CTUD TP TP_10 TON TON_10 TOF TOF_10'.split())
    expected = base | {name + '_E' for name in base} | set(
        'COUNTER_FB_M TIMER_1_FB_M TIMER_10_FB_M TIMER_100_FB_M TIMER_CONT_FB_M TIMER_CONTHS_FB_M'.split())
    source = sqlite3.connect(Path(private_path('resources/knowledge/fx3u_knowledge_scoped.sqlite'))
                             .resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = source.execute("SELECT outline_path, clean_text FROM page_artifacts "
                              "WHERE manual_id='fx5_programming_ab' AND outline_path LIKE 'PART 6%' "
                              'ORDER BY pdf_page').fetchall()
    finally:
        source.close()
    actual = set()
    for outline, text in rows:
        actual.update(instruction_declaration_names(text, outline, 'programming'))
    assert len(expected) == 32
    assert actual == expected




_HEADER_WITNESS = json.loads((private_path('tests/fixtures/mitsubishi_manual_headers.json')).read_text('utf8'))['pages']


@pytest.mark.parametrize('manual_id,page_number,expected', [
    ('fx5_programming_ab', 236, {'FX5S': 'supported', 'FX5UJ': 'supported',
                               'FX5U': 'supported', 'FX5UC': 'supported'}),
    ('sh080783engp', 632, {'Q00': 'unsupported', 'Q02H': 'supported', 'L02': 'supported'}),
    ('sh080809engx', 857, {'Q00': 'conditional', 'Q03UDV': 'supported', 'L02': 'unsupported'}),
])
def test_shifted_and_iqf_cpu_icons_match_the_local_manual(manual_id, page_number, expected):
    import pdfplumber
    from knowledge.manual_applicability import extract_header_applicability
    catalog_path = private_path('resources/knowledge/sources.json')
    catalog = json.loads(catalog_path.read_text(encoding='utf8'))
    source = next(m for m in catalog['manuals'] if m['id'] == manual_id)
    with pdfplumber.open(catalog_path.parent / source['file']) as pdf:
        page = pdf.pages[page_number - 1]
        header = extract_header_applicability(page.extract_words(x_tolerance=2, y_tolerance=3),
            page.rects, page.curves, page.lines, source['applicability_layout'])
        states = {row['model']: row['state'] for row in header['model_marks']}
        assert {model: states[model] for model in expected} == expected
        if manual_id == 'sh080809engx':
            assert header['opcodes'] == ['S.TO', 'SP.TO']


def test_device_index_rebuild_preserves_each_source_scope_and_conditions():
    import importlib.util
    spec = importlib.util.spec_from_file_location('device_index_builder',
        Path(__file__).parents[1] / 'tools/build_fx3u_knowledge_v3.py')
    import sys
    builder = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = builder
    spec.loader.exec_module(builder)
    connection = sqlite3.connect(':memory:')
    connection.executescript('''
        CREATE TABLE chunks(id INTEGER PRIMARY KEY, section TEXT, plc_models TEXT, source_applicability_json TEXT);
        CREATE TABLE entity_index(entity_norm TEXT, entity TEXT, entity_type TEXT, occurrences INTEGER, chunk_id INTEGER, manual_id TEXT);
        CREATE TABLE device_records(device_norm TEXT, record_type TEXT, UNIQUE(device_norm,record_type));
    ''')
    condition = json.dumps({'conditional_models':['FX2N'], 'note':'V3.00 and above'})
    connection.executemany('INSERT INTO chunks VALUES(?,?,?,?)', [
        (1,'Selectable timers','FX1S','{}'), (2,'FROM/TO interrupt','FX2N',condition),
        (3,'Special relays (SM)','Q03UDV','{}'), (4,'Hexadecimal devices','L02','')])
    connection.executemany('INSERT INTO entity_index VALUES(?,?,?,?,?,?)', [
        ('m8028','M8028','device',2,1,'fx'), ('m8028','M8028','device',1,2,'fx'),
        ('sm605','SM605','device',1,3,'q'), ('sb4a','SB4A','device',1,4,'l')])
    for _ in range(2):
        builder.insert_device_records(connection)
        rows = connection.execute('SELECT device,chunk_id,plc_models,evidence_kind,source_applicability_json '
                                  'FROM device_records WHERE chunk_id IS NOT NULL ORDER BY chunk_id').fetchall()
        assert rows == [('M8028',1,'FX1S','occurrence','{}'),
                        ('M8028',2,'FX2N','occurrence',condition),
                        ('SM605',3,'Q03UDV','occurrence','{}'),
                        ('SB4A',4,'L02','occurrence','')]
        assert connection.execute("SELECT prefix FROM device_records WHERE device='SB4A'").fetchone()[0] == 'SB'
        assert connection.execute("SELECT COUNT(*) FROM device_records WHERE plc_models LIKE '%FX3U%'").fetchone()[0] == 0
    connection.close()


@pytest.mark.parametrize('text,outline,expected', [
    ('S_SOCRCVS (EN, Un, s, d);ENO:=', '7.2 SOCRCV Instruction', ['S_SOCRCVS']),
    ('Errors and program example', '7.4.4 Reading out received data (S.SOCRCVS)', ['S.SOCRCVS']),
    ('(1) Current value read instructions: ICCNTRD1(P), ICCNTRD2(P)', 'Details of dedicated instructions',
     ['ICCNTRD1','ICCNTRD1P','ICCNTRD2','ICCNTRD2P']),
    ('IF X0 THEN S_SOCRCVS(TRUE,"U0",1,D0); END_IF;', 'Example', []),
    ('DUTY is mentioned in this error discussion.', 'Errors', []),
    ('Error lamp states', '13.1.5 ERROR (ERR) indicator lamp (Statuses: ON, flashing, OFF)', []),
    ('Battery indicator', '4.2.10 Battery [BATT (BAT)] LED OFF command [M8030]', []),
])
def test_dedicated_instruction_identity_comes_from_declarations_not_examples(text, outline, expected):
    from knowledge.manual_applicability import source_instruction_symbols
    assert source_instruction_symbols(text, outline) == expected


@pytest.mark.parametrize('text,expected', [
    ('L02使用内置高速计数CH1，M0允许读取', ('M0',)),
    ('CPU L02，读取SD1880和SD1881', ('SD1880','SD1881')),
    ('L02接点与M0串联', ('L02','M0')),
    ('查询L02软元件', ('L02',)),
    ('L02', ('L02',)),
])
def test_cpu_mentions_do_not_hide_literal_latch_device_references(text, expected):
    from knowledge.analysis_router import route_analysis_request
    assert route_analysis_request(text).devices == expected


@pytest.mark.parametrize('witness', _HEADER_WITNESS, ids=lambda w: f"{w['manual_id']}:{w['pdf_page']}")
def test_manual_header_models_match_independent_native_pdf_witness(witness):
    from knowledge.manual_applicability import extract_header_applicability
    sources = json.loads(Path(private_path('resources/knowledge/sources.json')).read_text('utf8'))['manuals']
    source = next(row for row in sources if row['id'] == witness['manual_id'])
    header = extract_header_applicability(witness['words'], witness['rects'], witness['curves'], witness['lines'],
                                         source.get('applicability_layout') or source['instruction_layout'])
    expected = witness['expected']
    if expected is None:
        assert header is None  # A continuation has no independent support badge.
        return
    assert header is not None
    if 'supported' in expected:
        assert set(header['supported_models']) == set(expected['supported'])
        assert set(header['conditional_models']) == set(expected['conditional'])
    else:
        assert not set(header['supported_models']) & set(expected['rejected'])
        assert set(expected['accepted']) <= set(header['supported_models'])
        assert set(expected['conditional']) <= set(header['conditional_models'])
        assert header['opcodes'] == ['LDPI', 'LDFI', 'ANDPI', 'ANDFI', 'ORPI', 'ORFI']


def test_scope_inheritance_uses_only_outline_ancestors_and_keeps_unknown(tmp_path):
    from types import SimpleNamespace
    from knowledge.manual_applicability import apply_manual_scope
    connection = sqlite3.connect(':memory:')
    connection.executescript('CREATE TABLE manuals(manual_id TEXT,plc_models TEXT); '
                            'CREATE TABLE chunks(id INTEGER,manual_id TEXT,outline_path TEXT,plc_models TEXT); '
                            'CREATE TABLE entity_index(manual_id TEXT,chunk_id INTEGER,plc_models TEXT);')
    connection.execute("INSERT INTO manuals VALUES('m','FX3U,FX3G')")
    connection.executemany("INSERT INTO chunks VALUES(?,'m',?,'FX3U,FX3G')",
                           [(1,'A > FIFO'),(2,'A > FIFO > Example'),(3,'A > Unrelated')])
    header = {'basis':'instruction_header','supported_models':['FX3U'],'conditional_models':['FX3U'],
              'model_marks':[{'model':'FX3U','state':'conditional'},{'model':'FX3G','state':'unsupported'}]}
    apply_manual_scope(connection,SimpleNamespace(id='m'),{'plc_models':['FX3U','FX3G']},
                       page_headers=[(10,'A > FIFO',header)])
    rows = connection.execute('SELECT plc_models,source_applicability_json FROM chunks ORDER BY id').fetchall()
    assert [row[0] for row in rows] == ['FX3U','FX3U','FX3U,FX3G']
    assert json.loads(rows[1][1])['evidence_pages'] == [10]
    assert json.loads(rows[2][1])['instruction_support'] == 'unknown'


def test_reference_devices_preserve_hexadecimal_spelling_and_do_not_admit_runtime_addresses():
    from plc.device_identity import canonical_reference_device, reference_device_tokens, DEVICE_TOKEN_RE
    assert reference_device_tokens('X00A YFF B1A W20 L10 SM400 SD520 ZR100 ST1') == (
        'X00A','YFF','B1A','W20','L10','SM400','SD520','ZR100','ST1')
    assert canonical_reference_device('x00a') == 'XA'
    assert not DEVICE_TOKEN_RE.fullmatch('X00A')
    from knowledge.analysis_router import route_analysis_request
    assert route_analysis_request('Q03UDV X00A B1A ZR100 device meaning').devices == ('X00A','B1A','ZR100')


_CPU_DEVICE_QUERIES = (
    '{cpu} 的 {device} 软元件是什么？', '{cpu}中 {device} 的用途是什么？',
    '在 {cpu} 上，{device} 是什么？', 'CPU 型号 {cpu}，{device} 是什么？',
    'PLC {cpu} 的 {device} 是什么？', '{cpu}: {device} 是什么？',
    '{cpu}：{device} 是什么？', 'What is {device} on {cpu}?',
    '{cpu} CPU {device} device definition',
    '{cpu} auxiliary relay definition; {device}',
    '{cpu} R device definition; {device}',
)


@pytest.mark.parametrize('template', _CPU_DEVICE_QUERIES)
def test_every_supported_cpu_is_masked_only_in_model_context(template):
    from plc.target_support import target_cpu_names
    from knowledge.analysis_router import route_analysis_request
    for cpu in target_cpu_names():
        query=template.format(cpu=cpu,device='S0')
        assert route_analysis_request(query).devices == ('S0',), query


def test_cpu_disambiguation_preserves_literal_devices_across_address_domain():
    from hypothesis import given, settings, strategies as st
    from plc.target_support import target_cpu_names
    from knowledge.analysis_router import route_analysis_request

    @settings(max_examples=300,deadline=None,database=None,derandomize=True)
    @given(st.sampled_from(target_cpu_names()),st.sampled_from(_CPU_DEVICE_QUERIES),
           st.sampled_from(['S','M','D','T','C','L','SB','ZR']),st.integers(0,9999))
    def check(cpu,template,prefix,number):
        device=f'{prefix}{number}'
        assert route_analysis_request(template.format(cpu=cpu,device=device)).devices == (device,)
        assert device in route_analysis_request(f'请解释 {device} 软元件。').devices
    check()


@pytest.mark.parametrize('query,expected',[
    ('L02','L02'),('L02 触点是什么？','L02'),('请解释 L06 软元件','L06'),
    ('PLC L26 地址','L26'),('L02 relay definition','L02'),
])
def test_ambiguous_cpu_spelling_remains_an_explicit_device(query,expected):
    from knowledge.analysis_router import route_analysis_request
    assert route_analysis_request(query).devices == (expected,)


def test_conflicting_cached_headers_never_keep_first_supported_scope():
    from types import SimpleNamespace
    from knowledge.manual_applicability import apply_manual_scope
    connection = sqlite3.connect(':memory:')
    connection.executescript('CREATE TABLE manuals(manual_id TEXT,plc_models TEXT); '
                            'CREATE TABLE chunks(id INTEGER,manual_id TEXT,outline_path TEXT,plc_models TEXT); '
                            'CREATE TABLE entity_index(manual_id TEXT,chunk_id INTEGER,plc_models TEXT);')
    connection.execute("INSERT INTO manuals VALUES('m','FX3U')")
    connection.execute("INSERT INTO chunks VALUES(1,'m','A','FX3U')")
    yes = {'basis':'instruction_header','supported_models':['FX3U'],
           'model_marks':[{'model':'FX3U','state':'supported'}]}
    no = {'basis':'instruction_header','supported_models':[],
          'model_marks':[{'model':'FX3U','state':'unsupported'}]}
    apply_manual_scope(connection,SimpleNamespace(id='m'),{'plc_models':['FX3U']},
                       page_headers=[(10,'A',yes),(11,'A',no),(12,'A',yes)])
    models,raw = connection.execute('SELECT plc_models,source_applicability_json FROM chunks').fetchone()
    assert models == '__NO_APPLICABLE_CPU__'
    assert json.loads(raw)['instruction_support'] == 'unknown'
    assert json.loads(raw)['evidence_pages'] == [10,11,12]


def test_ql_optional_pulse_marker_is_a_form_suffix_not_an_instruction():
    from knowledge.manual_applicability import ql_header_symbols
    words = [{'text':'Basic','x0':52,'top':100},
             {'text':'CALL(P)','x0':52,'top':75}, {'text':'DCALL(P)','x0':160,'top':75}]
    assert ql_header_symbols(words) == ['CALL','CALLP','DCALL','DCALLP']


def _database() -> Path:
    path = Path(private_path('resources/knowledge/fx3u_knowledge.sqlite'))
    with path.open("rb") as stream:
        assert stream.read(16) == b"SQLite format 3\0"
    return path


def _operands(opcode: str, manual_id: str = "fx3_programming_r"):
    with sqlite3.connect(_database()) as connection:
        row = connection.execute(
            "SELECT operands_json FROM instructions WHERE opcode_norm=? AND manual_id=?",
            (opcode.casefold(), manual_id),
        ).fetchone()
    assert row is not None
    return json.loads(row[0])


@pytest.mark.parametrize('model,opcode,expected_manuals,forbidden', [
    ('FX1','MOV',{'jy992d48301j'},set()),
    ('FX2N','SFWR',{'jy992d88101e','jy992d48301j'},set()),
    ('FX0N','SFWR',set(),{'jy992d48301j','fxcpu_basic_applied_m'}),
    ('Q00J','LDPI',set(),{'sh080809engx','sh080783engp'}),
    ('Q03UDV','LDPI',{'sh080809engx','sh080783engp'},set()),
])
def test_exact_fact_lookup_obeys_independent_source_header_examples(model,opcode,expected_manuals,forbidden):
    rows = resolve_instruction_records([opcode],plc_model=model,task_type='analysis')
    manuals = {r['manual_id'] for r in rows}
    assert not manuals & forbidden
    if expected_manuals:
        assert manuals & expected_manuals
        assert all(r['instruction_opcode'] == opcode for r in rows)
    if model == 'Q03UDV':
        assert any(model in r.get('source_applicability',{}).get('conditional_models',[]) for r in rows)


def test_feature_manual_scopes_keep_specific_cpu_differences():
    with sqlite3.connect(_database()) as connection:
        scopes = {mid:set(models.split(',')) for mid,models in connection.execute('SELECT manual_id,plc_models FROM manuals')}
    assert scopes['sh080895engg'] == {'L26-BT','L26-PBT'}
    assert scopes['sh080486engaa'] == {'Q12PRH','Q25PRH'}
    assert 'L02S' not in scopes['sh080891engs'] and 'L02' in scopes['sh080891engs']
    assert 'Q03UDV' in scopes['sh080893engu'] and 'Q03UDE' not in scopes['sh080893engu']
    assert 'Q03UDV' in scopes['sh080811engy'] and 'Q03UD' not in scopes['sh080811engy']


def test_structured_operand_applicability_is_extracted_from_official_tables():
    fmov = _operands("FMOV")
    destination = next(item for item in fmov if item.get("position") == "D")
    assert "KnM" in destination.get("applicable_devices", [])
    assert "M" not in destination.get("applicable_devices", [])

    zrst = _operands("ZRST")
    first = next(item for item in zrst if item.get("position") == "D1")
    assert "M" in first.get("applicable_devices", [])


def test_structured_operand_rows_are_globally_well_formed():
    """Manual-table headers and PDF extraction debris must not become operands."""
    with sqlite3.connect(_database()) as connection:
        rows = connection.execute(
            "SELECT manual_id,opcode,operands_json FROM instructions WHERE operands_json <> '[]'"
        ).fetchall()

    bad = []
    for manual_id, opcode, payload in rows:
        for item in json.loads(payload or "[]"):
            if not isinstance(item, dict):
                bad.append((manual_id, opcode, "operand_not_object", repr(item)))
                continue
            position = str(item.get("position") or "").strip().upper()
            description = str(item.get("description") or "").strip()
            data_type = str(item.get("data_type") or "").strip()
            devices = item.get("applicable_devices") or []

            if position == "X":
                bad.append((manual_id, opcode, "device_header_as_operand", position))
            if description.casefold() in {"<blank>", "blank"}:
                bad.append((manual_id, opcode, "blank_description", description))
            if description and re.fullmatch(
                r"(?:(?:\[GLYPH-[0-9A-F]+\]|\(cid:\d+\))\d*\s*)+",
                description,
                flags=re.I,
            ):
                bad.append((manual_id, opcode, "glyph_only_description", description))
            if description and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", description) and len(description) <= 3:
                bad.append((manual_id, opcode, "table_token_description", description))
            if description and len(description) < 24:
                words = re.findall(r"[A-Za-z]+", description)
                if words and not any(len(word) >= 4 for word in words) and not re.search(r"\d{2,}", description):
                    bad.append((manual_id, opcode, "low_quality_description", description))
            if not description and not data_type and not devices:
                bad.append((manual_id, opcode, "operand_has_no_semantics", position))

    assert bad == []


def test_structured_instruction_retrieval_surfaces_applicability_without_prompt_patch():
    results = resolve_instruction_records(
        [{"opcode": "FMOV", "base_opcode": "FMOV"}],
        plc_model="FX3U",
        task_type="generate",
    )
    assert results
    text = "\n".join(item["text"] for item in results[:3])
    assert "[STRUCTURED INSTRUCTION RECORD]" in text
    assert "applicable=" in text
    assert "KnM" in text

    reset_results = retrieve_fact_aware_knowledge(
        "FX3U 批量清零连续 M 软元件区间应该使用什么指令？",
        plc_model="FX3U",
        task_type="generate",
        top_k=5,
        char_budget=9000,
    )
    assert any(
        str(item.get("instruction_opcode", "")).upper() == "ZRST"
        for item in reset_results[:5]
    )

import copy
import csv
import hashlib
import json
from pathlib import Path

import pytest
from hypothesis import example, given, settings, strategies as st
from plc.target_identity import Q_CPUS, PARAMETER_ONLY


@pytest.mark.parametrize('cpu', [cpu for cpu in Q_CPUS if cpu not in PARAMETER_ONLY])
@pytest.mark.parametrize('operand', [r'U0\GA', r'U1\G1F', r'U0Z1\G2A', r'U0\GFF.F'])
def test_all_q_buffer_numbers_remain_decimal_in_source_validation_and_encoding(cpu, operand):
    from plc.ordered_source import buffer_memory_reference, validate_ordered_rung
    from gxw.token_encoder import encode_instruction
    from gxw.token_profiles import source_token_profile
    assert buffer_memory_reference(operand) is None
    with pytest.raises(ValueError, match='invalid operand token'):
        validate_ordered_rung({'rung_id': 1, 'ordered_instructions': [
            {'op': 'LD', 'args': ['M0']}, {'op': 'OUT', 'args': [operand]}]},
            plc_model=cpu, require_catalogued=False)
    with pytest.raises(ValueError):
        encode_instruction('OUT', [operand], profile=source_token_profile(cpu), plc_model=cpu, text_encoding='cp936')


@pytest.mark.parametrize('cpu', [cpu for cpu in Q_CPUS if cpu not in PARAMETER_ONLY])
@pytest.mark.parametrize('operand', ['U9\\G36320.1', 'U0B\\G2000.8', 'U0B\\G8000.F'])
def test_all_q_ordered_buffer_bits_keep_qualified_access_and_remain_source_unverified(cpu, operand):
    from plc.ordered_source import build_instruction_ir, validate_ordered_rung
    from plc.ir import analyze_instruction_access, validate_plc_ir
    rows = [{'op': 'LD', 'args': ['M0']}, {'op': 'OUT', 'args': [operand]}]
    ir = build_instruction_ir([{'instructions': rows}], plc_model=cpu)
    validate_plc_ir(ir, require_catalogued_instructions=False)
    assert analyze_instruction_access('OUT', [operand], plc_model=cpu) == ([], [operand])
    assert ir['networks'][0]['writes'] == [operand]
    assert ir['devices'][operand]['kind'] == 'U_G'
    assert any(row['code'] == 'SOURCE_BUFFER_MEMORY_SEMANTICS_UNVERIFIED'
               and row['severity'] == 'unverified' for row in ir['analysis']['findings'])
    with pytest.raises(ValueError, match='invalid operand token'):
        validate_ordered_rung({'rung_id': 1, 'ordered_instructions': [{'op': 'OUT', 'args': [operand + '0']}]},
                              plc_model=cpu, require_catalogued=False)


@pytest.mark.parametrize('operand,reads,writes', [
    ('@SD705', ['SD705'], ['@SD705']),
    ('J1\\W20', [], ['J1\\W20']),
    ('U0Z1\\G0', ['Z1'], ['U0\\G0']),
    ("'DInput.手动[2]", [], []),
])
def test_ordered_nonlocal_and_symbolic_destinations_do_not_become_local_devices(operand, reads, writes):
    from plc.ordered_source import build_instruction_ir
    from plc.ir import analyze_instruction_access, validate_plc_ir
    actual_reads, actual_writes = analyze_instruction_access('MOV', ['K1', operand], plc_model='Q03UDV')
    assert set(actual_reads) == set(reads) and set(actual_writes) == set(writes)
    ir = build_instruction_ir([{'instructions': [{'op': 'MOV', 'args': ['K1', operand]}]}], plc_model='Q03UDV')
    validate_plc_ir(ir, require_catalogued_instructions=False)
    assert any(row['severity'] == 'unverified' for row in ir['analysis']['findings'])


@pytest.mark.parametrize('unary', ['MEP', 'MEF', 'INV', 'ANDE>', 'ANDD='])
def test_ordered_circuit_partition_retains_pending_logic_across_result_conditions(unary):
    from gxworks2.csv_importer import RawInstruction, _split_networks
    from plc.ordered_source import build_instruction_ir
    from plc.ir import validate_plc_ir
    args = ['D0', 'E1'] if unary == 'ANDE>' else ['D0', 'K1'] if unary == 'ANDD=' else []
    rows = [RawInstruction('0', 'LD', ['M0']), RawInstruction('1', 'LD', ['M1']),
            RawInstruction('2', unary, args), RawInstruction('3', 'LD', ['M2']),
            RawInstruction('4', 'ORB'), RawInstruction('5', 'ANB'), RawInstruction('6', 'OUT', ['Y0'])]
    groups = _split_networks(rows)
    assert len(groups) == 1 and groups[0][1] == rows
    ir = build_instruction_ir([{'instructions': [r.as_ir() for r in group]} for _, group, _ in groups], plc_model='Q03UDV')
    validate_plc_ir(ir, require_catalogued_instructions=False)


@pytest.mark.parametrize('opcode,args', [('MEP', []), ('MEF', []), ('*', ['K2', 'D0', 'D2']),
    ('+', ['K1', 'D0']), ('D/', ['D0', 'K2', 'D2']), ('E/', ['D0', 'E2.0', 'D2']),
    ('ANDFI', ['M1']), ('BKRST', ['M0', 'K2'])])
def test_saved_ordered_source_uses_shared_default_policy_and_still_checks_arity(opcode, args):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, source_instruction_arities
    from plc.ordered_source import build_instruction_ir, validate_ordered_rung
    rows = [{'op': 'LD', 'args': ['M0']}, {'op': opcode, 'args': args}, {'op': 'OUT', 'args': ['Y0']}]
    ir = build_instruction_ir([{'instructions': rows}], plc_model='Q03UDV')
    assert validate_plc_ir(ir) is ir
    assert len(args) in source_instruction_arities(opcode, 'Q03UDV')
    if DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu='Q03UDV') is None:
        with pytest.raises(ValueError, match='not catalogued'):
            validate_plc_ir(ir, require_catalogued_instructions=True)
    with pytest.raises(ValueError, match='operand|arity|argument'):
        validate_ordered_rung({'rung_id': 1, 'ordered_instructions': [
            {'op': opcode, 'args': ['M0'] * 11}]}, plc_model='Q03UDV', require_catalogued=False)


@pytest.mark.parametrize('opcode,args,reads,writes', [
    ('+', ['K1', 'D0'], ['D0'], ['D0']),
    ('+', ['D2', 'D4', 'D6'], ['D2', 'D4'], ['D6']),
    ('*', ['D0', 'K2', 'D4'], ['D0'], ['D4', 'D5']),
    ('D/', ['D0', 'D4', 'D8'], ['D0', 'D1', 'D4', 'D5'], ['D8', 'D9', 'D10', 'D11']),
    ('$MOV', ['"text"', 'D0'], [], ['D0']),
    ('BKRST', ['M0', 'D0'], ['D0'], ['M0']),
    ('FF', ['M0'], ['M0'], ['M0']),
    ('SFTP', ['M0'], ['M0'], ['M0']),
    ('OUTH', ['T0', 'K100'], [], ['T0']),
    ('TTMR', ['D0', 'K1'], ['D0', 'D1'], ['D0', 'D1']),
    ('STMR', ['T0', 'K10', 'M0'], ['T0'], ['T0', 'M0']),
    ('LEN', ['D0', 'D2'], ['D0'], ['D2']),
    ('RIGHT', ['D0', 'D2', 'D4'], ['D0', 'D4'], ['D2']),
    ('INSTR', ['D0', 'D2', 'D4', 'D6'], ['D0', 'D2', 'D6'], ['D4']),
])
def test_q_source_manual_roles_keep_destinations_and_memory_extents(opcode, args, reads, writes):
    from plc.ir import analyze_instruction_access
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.ordered_source import validate_ordered_rung
    actual_reads, actual_writes = analyze_instruction_access(opcode, args, plc_model='Q03UDV')
    assert set(actual_reads) == set(reads) and set(actual_writes) == set(writes)
    if opcode not in {'TTMR', 'STMR', 'LEN', 'RIGHT', 'INSTR'}:
        assert DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu='Q03UDV') is None
    with pytest.raises(ValueError, match='writable|read.only|read-only'):
        validate_ordered_rung({'rung_id': 1, 'ordered_instructions': [dict(op='+', args=['K1', 'K2'])]},
                              plc_model='Q03UDV', require_catalogued=False)


@pytest.mark.parametrize('cpu', [cpu for cpu in Q_CPUS if cpu not in PARAMETER_ONLY])
@pytest.mark.parametrize('opcode,args,reads,writes', [
    ('ROLP', ['D0', 'D2'], ['D0', 'D2'], ['D0']),
    ('SFRP', ['D0', 'K2'], ['D0'], ['D0']),
    ('BSFL', ['M4', 'D2'], ['M4', 'D2'], ['M4']),
    ('ENCOP', ['D0', 'D2', 'D4'], ['D0', 'D4'], ['D2']),
    ('STRDEL', ['D0', 'D2', 'D4'], ['D0', 'D2', 'D4'], ['D0']),
])
def test_q_saved_source_forms_use_manual_operand_directions_for_each_cpu(cpu, opcode, args, reads, writes):
    from plc.ir import analyze_instruction_access
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, source_instruction_arities
    if (cpu, opcode) == ('Q02/Q02H', 'STRDEL'):
        # This menu's original lexical catalogue omits the instruction; source
        # roles for other Q CPUs must not invent a form for the older menu.
        from plc.ordered_source import validate_ordered_rung
        assert not source_instruction_arities(opcode, cpu)
        assert DEFAULT_INSTRUCTION_REGISTRY.resolve_source_form(opcode, len(args), cpu=cpu) is None
        with pytest.raises(ValueError, match='not catalogued'):
            validate_ordered_rung({'rung_id': 1, 'ordered_instructions': [dict(op=opcode, args=args)]},
                                  plc_model=cpu, require_catalogued=True)
        return
    assert len(args) in source_instruction_arities(opcode, cpu)
    assert DEFAULT_INSTRUCTION_REGISTRY.resolve_source_form(opcode, len(args), cpu=cpu) is not None
    actual_reads, actual_writes = analyze_instruction_access(opcode, args, plc_model=cpu)
    assert set(actual_reads) == set(reads) and set(actual_writes) == set(writes)


@pytest.mark.parametrize('merge', ['ANB', 'ORB'])
def test_ordered_circuit_partition_keeps_an_output_before_a_later_block_merge(merge):
    from gxworks2.csv_importer import RawInstruction, _split_networks
    from plc.ordered_source import validate_ordered_rung
    rows = [RawInstruction('0', 'LD', ['M0']), RawInstruction('1', 'OUT', ['M1']),
            RawInstruction('2', 'LD', ['M2']), RawInstruction('3', merge), RawInstruction('4', 'OUT', ['Y0'])]
    groups = _split_networks(rows)
    assert len(groups) == 1 and groups[0][1] == rows
    validate_ordered_rung({'rung_id': 1, 'ordered_instructions': [row.as_ir() for row in rows]}, plc_model='Q03UDV')
    with pytest.raises(ValueError, match='logic stack underflow'):
        validate_ordered_rung({'rung_id': 1, 'ordered_instructions': [row.as_ir() for row in rows[2:]]}, plc_model='Q03UDV')


@settings(max_examples=30, deadline=None, derandomize=True)
@given(first=st.booleans(), second=st.booleans(), negate=st.booleans())
@pytest.mark.parametrize('merge', ['ANB', 'ORB'])
def test_legacy_display_boundaries_keep_the_same_boolean_circuit_in_validation_drawing_and_scan(first, second, negate, merge):
    from plc.ordered_source import build_instruction_ir
    from plc.ir import ir_to_ladder, validate_plc_ir
    from simulator.bounded import ScanMachine
    from rendering.ladder_svg import AdvancedSVGLadder
    import xml.etree.ElementTree as ET
    rows = [{'op': 'LDI' if negate else 'LD', 'args': ['M0']}, {'op': 'OUT', 'args': ['M0']},
            {'op': 'LD', 'args': ['M1']}, {'op': merge, 'args': []}, {'op': 'OUT', 'args': ['Y0']}]
    ir = build_instruction_ir([{'instructions': rows[:2]}, {'instructions': rows[2:]}], plc_model='FX3U')
    validate_plc_ir(ir, require_catalogued_instructions=False)
    ladder = ir_to_ladder(ir)
    initial = {'M0': first, 'M1': second, 'Y0': False}
    machine = ScanMachine(ladder, plc_model='FX3U', initial=initial, execution_context={})
    result = machine.scan({'inputs': {}}, first_scan=True)
    expected = _execute_boolean_instructions(rows, initial)
    assert not machine.gaps
    assert result['after']['Y0'] == expected['Y0'] and result['after']['M0'] == expected['M0']
    drawing = ET.fromstring(AdvancedSVGLadder().generate_ladder(json.dumps(ladder)))
    assert len(drawing.findall('.//{http://www.w3.org/2000/svg}g[@class="source-network"]')) == 1
    assert ir_to_ladder(ir) == ladder


def _execute_boolean_instructions(instructions, values):
    """Independent relay algebra, used only as the lowering test oracle."""
    acc, blocks, saved = True, [], []
    state = dict(values)
    for item in instructions:
        op, args = item['op'], item['args']
        if op in {'LD','LDI'}:
            blocks.append(acc)
            acc = bool(state[args[0]]) if op == 'LD' else not state[args[0]]
        elif op in {'AND','ANI','OR','ORI'}:
            bit = bool(state[args[0]])
            if op in {'ANI','ORI'}:
                bit = not bit
            acc = (acc and bit) if op in {'AND','ANI'} else (acc or bit)
        elif op in {'ORB','ANB'}:
            previous = blocks.pop()
            acc = previous or acc if op == 'ORB' else previous and acc
        elif op == 'MPS':
            saved.append(acc)
        elif op == 'MRD':
            acc = saved[-1]
        elif op == 'MPP':
            acc = saved.pop()
        elif op == 'OUT':
            state[args[0]] = bool(acc)
        else:
            raise AssertionError(op)
    assert not saved
    return state


_contact = st.tuples(st.sampled_from(['NO','NC']),st.sampled_from(['M0','M1','M2','M3']))
_serial = st.lists(_contact,min_size=1,max_size=3)


def test_ordered_source_keeps_outputs_and_shared_stack_in_original_order(tmp_path):
    from plc.ordered_source import build_instruction_ir
    from plc.ir import ir_to_ladder, lower_rung_instructions, validate_plc_ir
    from plc.core import PLCCore
    from plc.st_renderer import render_plc_ir_to_st, STTranslationError
    rows = [{'op': op, 'args': args.split()} for op, args in [
        ('LD', 'M0'), ('OUT', 'M1'), ('MPS', ''), ('AND', 'M2'), ('OUT', 'M3'),
        ('MRD', ''), ('ANI', 'M2'), ('OUT', 'M4'), ('MPP', ''), ('END', '')]]
    ir = build_instruction_ir([{'instructions': rows}], plc_model='FX3U',
        device_comments={'M1': '保留完整的已有设备说明，不应用生成短名称的截断规则'})
    assert ir['source']['format'] == 'instruction_sequence_v1'
    assert validate_plc_ir(ir) is not False
    rung = ir_to_ladder(ir)['rungs'][0]
    assert [{key: item[key] for key in ('op', 'args')} for item in lower_rung_instructions(rung)] == rows
    assert ir_to_ladder(ir)['device_comments']['M1'].endswith('截断规则')
    with pytest.raises(STTranslationError, match='Ordered instruction'):
        render_plc_ir_to_st(ir)
    compiled = PLCCore().compile_project(ir, tmp_path)
    assert set(compiled['artifacts']) == {'ir', 'json', 'svg'}
    assert 'MPS' in (tmp_path / compiled['artifacts']['svg']).read_text(encoding='utf-8')


@settings(max_examples=60, deadline=None, derandomize=True)
@given(first=st.sampled_from(['LD', 'LDI']), contact=st.sampled_from(['AND', 'ANI', 'OR', 'ORI']),
       bits=st.dictionaries(st.sampled_from(['M0', 'M1', 'M2', 'Y0', 'Y1']), st.booleans(), min_size=5))
def test_ordered_graph_junctions_do_not_reread_contacts_after_writes(first, contact, bits):
    from plc.ordered_source import project_ordered_graph
    rows = [{'op': op, 'args': args.split()} for op, args in [
        (first, 'M0'), ('OUT', 'M0'), ('MPS', ''), (contact, 'M1'), ('OUT', 'Y0'),
        ('MRD', ''), ('ANI', 'M2'), ('OUT', 'Y1'), ('MPP', ''), ('OUT', 'M2')]]
    original = copy.deepcopy(rows)
    state, junctions = dict(bits), {}

    def evaluate(value):
        kind = value['kind']
        if kind == 'true':
            return True
        if kind == 'reference':
            return junctions[value['anchor']]
        if kind == 'contact':
            bit = state[value['args'][0]]
            return not bit if value['op'] in {'LDI', 'ANI', 'ORI'} else bit
        values = [evaluate(child) for child in value['children']]
        return all(values) if kind == 'and' else any(values)

    plan = project_ordered_graph({'ordered_instructions': rows})
    for event in plan:
        if event['kind'] == 'expression':
            junctions[event['anchor']] = evaluate(event['expression'])
        elif event['kind'] == 'output':
            state[event['args'][0]] = junctions[event['anchor']]
    assert state == _execute_boolean_instructions(rows, bits)
    assert rows == original


def test_ordered_ladder_svg_draws_contacts_outputs_and_saved_junctions_once():
    import xml.etree.ElementTree as ET
    from rendering.ladder_svg import AdvancedSVGLadder
    rows = [{'op': op, 'args': args.split()} for op, args in [
        ('LD', 'M0'), ('OUT', 'M1'), ('MPS', ''), ('AND', 'M2'), ('OUT', 'Y0'),
        ('MRD', ''), ('ANI', 'M2'), ('OUT', 'Y1'), ('MPP', ''), ('END', '')]]
    document = {'rungs': [{'rung_id': 42, 'ordered_instructions': rows}], 'device_comments': {'M0': '条件'}}
    before = copy.deepcopy(document)
    renderer = AdvancedSVGLadder()
    root = ET.fromstring(renderer.generate_ladder(json.dumps(document)))
    ns = {'s': 'http://www.w3.org/2000/svg'}
    assert root.get('data-source-format') == 'ordered-ladder'
    contacts = root.findall('.//s:g[@class="ladder-element"]', ns)
    assert [(node.get('data-op'), node.get('data-source-index')) for node in contacts] == [
        ('LD', '0'), ('OUT', '1'), ('AND', '3'), ('OUT', '4'), ('ANI', '6'), ('OUT', '7'), ('END', '9')]
    assert [node.get('data-op') for node in root.findall('.//s:g[@class="source-stack"]', ns)] == ['MPS', 'MRD', 'MPP']
    assert json.loads(root.find('.//s:metadata', ns).text) == rows
    assert document == before
    assert renderer.rung_bounds[42]['display_number'] == 1
    assert root.findall('.//s:line[@stroke-width="3.5"]', ns)


@pytest.mark.parametrize('operation', ['INV', 'MEP', 'MEF'])
def test_ordered_unary_conditions_feed_the_following_output_in_the_diagram(operation):
    from plc.ordered_source import project_ordered_graph
    from rendering.ladder_svg import AdvancedSVGLadder
    import xml.etree.ElementTree as ET
    rows = [dict(op='LD', args=['M0']), dict(op=operation, args=[]), dict(op='OUT', args=['Y0'])]
    plan = project_ordered_graph({'ordered_instructions': rows})
    assert [event['op'] for event in plan if event['kind'] == 'output'] == ['OUT']
    gate = plan[0]['expression']
    assert gate['op'] == operation and gate['child']['args'] == ['M0']
    root = ET.fromstring(AdvancedSVGLadder().generate_ladder(json.dumps({
        'rungs': [{'ordered_instructions': rows}]})))
    ns = {'s': 'http://www.w3.org/2000/svg'}
    assert [node.get('data-op') for node in root.findall('.//s:g[@class="ladder-element"]', ns)] == [
        'LD', operation, 'OUT']
    assert json.loads(root.find('.//s:metadata', ns).text) == rows


def test_unknown_record_does_not_draw_the_following_output_as_an_unconditional_coil():
    from plc.ordered_source import project_ordered_graph
    from rendering.ladder_svg import AdvancedSVGLadder
    import xml.etree.ElementTree as ET
    rows = [dict(kind='opaque', raw_hex='037f03', reason='unknown source record'),
            dict(op='OUT', args=['Y0'])]
    plan = project_ordered_graph({'ordered_instructions': rows})
    assert [event['expression']['kind'] for event in plan if event['kind'] == 'expression'] == ['unknown']
    root = ET.fromstring(AdvancedSVGLadder().generate_ladder(json.dumps({
        'rungs': [{'ordered_instructions': rows}]})))
    ns = {'s': 'http://www.w3.org/2000/svg'}
    assert '?' in [element.text for element in root.findall('.//s:text', ns)]
    assert json.loads(root.find('.//s:metadata', ns).text) == rows


@pytest.mark.parametrize('merge', ['ANB', 'ORB'])
def test_ordered_graph_freezes_earlier_ld_blocks_before_an_output_changes_their_device(merge):
    from plc.ordered_source import project_ordered_graph
    rows = [{'op': op, 'args': args.split()} for op, args in [
        ('LD', 'M0'), ('LD', 'M1'), ('OUT', 'M0'), (merge, ''), ('OUT', 'Y0')]]
    plan = project_ordered_graph({'ordered_instructions': rows})
    first_output = next(i for i, event in enumerate(plan) if event['kind'] == 'output')
    assert [event['expression']['args'] for event in plan[:first_output]] == [['M0'], ['M1']]
    assert all(child['kind'] == 'reference' for event in plan[first_output:] if event['kind'] == 'expression'
               for child in event['expression']['children'])


@pytest.mark.parametrize('kind,raw_hex,text', [('opaque', '037f03', None), ('label', '033c0304d00104', 'P1')])
def test_partial_source_keeps_all_records_and_stops_bounded_execution_at_unknown_control(kind, raw_hex, text):
    from plc.ordered_source import build_instruction_ir
    from plc.ir import ir_to_ladder, validate_plc_ir
    from plc.core import PLCCore
    from simulator.bounded import ScanMachine
    records = [dict(record_index=0, source_offset=79, raw_hex='03200304900004', op='OUT', args=['M0']),
               dict(record_index=1, source_offset=86, raw_hex=raw_hex, kind=kind, text=text),
               dict(record_index=2, source_offset=93, raw_hex='03230304900104', op='SET', args=['M1'])]
    program = build_instruction_ir([{'instructions': records}], plc_model='FX3U')
    validate_plc_ir(program, validate_ladder=False)
    assert program['source']['format'] == 'instruction_sequence_v2'
    assert PLCCore().validate_project(program)['status'] == 'unverified'
    rows = program['networks'][0]['ladder']['ordered_instructions']
    assert [row['raw_hex'] for row in rows] == [row['raw_hex'] for row in records]
    engine = ScanMachine(ir_to_ladder(program), plc_model='FX3U', initial={'M0': False, 'M1': False}, execution_context={})
    result = engine.scan({'inputs': {}}, first_scan=True)
    assert any(gap['reason'] == 'source_record_' + kind + '_unverified' for gap in engine.gaps)
    assert result['after']['M1'] is None
    assert not any(write['device'] == 'M1' and write['after'] is True for write in result['writes'])
    legacy = copy.deepcopy(program)
    for finding in legacy['analysis']['findings']:
        if finding['code'].startswith('SOURCE_'):
            for field in ('suggestion', 'addresses', 'network_refs', 'json_paths', 'evidence', 'confidence', 'fixable'):
                finding.pop(field)
    before = json.dumps(legacy, sort_keys=True)
    validate_plc_ir(legacy, validate_ladder=False)
    assert json.dumps(legacy, sort_keys=True) == before
    assert PLCCore().validate_project(legacy)['status'] == 'unverified'
    next(row for row in legacy['analysis']['findings'] if row['code'].startswith('SOURCE_'))['severity'] = 'info'
    with pytest.raises(ValueError, match='analysis is stale'):
        validate_plc_ir(legacy, validate_ladder=False)
    tampered = copy.deepcopy(program)
    tampered['networks'][0]['ladder']['ordered_instructions'].pop(1)
    with pytest.raises(ValueError):
        validate_plc_ir(tampered, validate_ladder=False)


@settings(max_examples=40, deadline=None, derandomize=True)
@given(value=st.integers(-2147483648, 2147483647), register=st.integers(0, 255),
       hexadecimal=st.booleans())
@pytest.mark.parametrize('cpu,index_prefix', [('Q03UDV', 'Z'), ('FX3U', 'V'), ('FX3U', 'Z')])
def test_ordered_indexed_integer_sources_keep_index_dependencies_and_unverified_semantics(value, register, hexadecimal,
                                                                                          cpu, index_prefix):
    from plc.ordered_source import build_instruction_ir
    from plc.ir import analyze_instruction_access, validate_plc_ir
    from plc.core import PLCCore
    register = register % 8 if cpu == 'FX3U' else register
    index_device = index_prefix + str(register)
    literal = ('H' + format(value & 0xffffffff, 'X') if hexadecimal else 'K' + str(value)) + index_device
    rows = [{'op': 'MOV', 'args': [literal, 'D0']}, {'op': 'END', 'args': []}]
    program = build_instruction_ir([{'instructions': rows}], plc_model=cpu)
    assert program['networks'][0]['reads'] == [index_device]
    assert program['networks'][0]['writes'] == ['D0']
    assert analyze_instruction_access('LD=', [literal, 'D0'], plc_model=cpu) == (['D0', index_device], [])
    findings = [row for row in program['analysis']['findings']
                if row['code'] == 'SOURCE_INDEXED_LITERAL_SEMANTICS_UNVERIFIED']
    assert len(findings) == 1
    assert findings[0]['severity'] == 'unverified'
    assert findings[0]['operands'] == [literal]
    assert findings[0]['addresses'] == [index_device]
    assert PLCCore().validate_project(program)['status'] == 'unverified'
    before = copy.deepcopy(program)
    validate_plc_ir(program, validate_ladder=False)
    assert program == before


@settings(max_examples=35, deadline=None, derandomize=True)
@given(unit=st.integers(0, 7), address=st.integers(0, 32766), register=st.integers(0, 7),
       prefix=st.sampled_from(['', 'V', 'Z']), write=st.booleans())
def test_ordered_buffer_references_keep_module_identity_index_dependencies_and_unverified_access(unit, address,
                                                                                              register, prefix, write):
    from plc.ordered_source import build_instruction_ir
    from plc.ir import validate_plc_ir
    from plc.core import PLCCore
    index = prefix + str(register) if prefix else ''
    base = f'U{unit}\\G{address}'
    operand = base + index
    args = ['D0', operand] if write else [operand, 'D0']
    program = build_instruction_ir([{'instructions': [{'op': 'MOV', 'args': args}]}], plc_model='FX3U')
    network = program['networks'][0]
    assert set(network['reads']) == ({'D0'} if write else {base}) | ({index} if index else set())
    assert network['writes'] == ([base] if write else ['D0'])
    assert network['ladder']['ordered_instructions'][0]['args'] == args
    findings = [row for row in program['analysis']['findings']
                if row['code'] == 'SOURCE_BUFFER_MEMORY_SEMANTICS_UNVERIFIED']
    assert len(findings) == 1 and findings[0]['severity'] == 'unverified'
    assert findings[0]['operands'] == [operand]
    report = PLCCore().validate_project(program)
    assert report['status'] == 'unverified'
    assert not report['counts'].get('error')
    before = copy.deepcopy(program)
    validate_plc_ir(program, validate_ladder=False)
    assert program == before


@pytest.mark.parametrize('operand', ['U0Z1\\G0', 'U0\\G0Z1'])
def test_ordered_buffer_dependencies_support_q_module_and_buffer_index_positions(operand):
    from plc.ir import analyze_instruction_access
    from plc.ordered_source import build_instruction_ir
    from plc.core import PLCCore
    assert analyze_instruction_access('MOV', [operand, 'D0'], plc_model='Q03UDV') == (['Z1', 'U0\\G0'], ['D0'])
    program = build_instruction_ir([{'instructions': [{'op': 'MOV', 'args': [operand, 'D0']}]}], plc_model='Q03UDV')
    assert PLCCore().validate_project(program)['status'] == 'unverified'


def test_buffer_source_execution_remains_unknown_and_graph_syntax_remains_strict():
    from plc.ir import ir_to_ladder, build_plc_ir
    from plc.ordered_source import build_instruction_ir
    from plc.core import PLCCore
    from simulator.bounded import ScanMachine
    args = ['U0\\G0V1', 'D0']
    source = build_instruction_ir([{'instructions': [{'op': 'MOV', 'args': args}]}], plc_model='FX3U')
    machine = ScanMachine(ir_to_ladder(source), plc_model='FX3U', initial={'V1': 3, 'D0': 0}, execution_context={})
    result = machine.scan({'inputs': {}}, first_scan=True)
    assert machine.gaps and result['after']['D0'] is None
    graph = build_plc_ir({'device_comments': {}, 'rungs': [rung(1, outputs=[instruction('MOV', args)])]},
                         plc_model='FX3U')
    assert PLCCore().validate_project(graph)['counts']['error']


@pytest.mark.parametrize('operand', ['K1', 'HFFFF', '"K1Z0"', "'K1Z0", "'K1Z0'", 'K4M0Z1'])
def test_indexed_integer_detection_does_not_treat_plain_literals_labels_or_grouped_devices_as_indexed_constants(operand):
    from plc.ordered_source import indexed_literal_register, build_instruction_ir
    assert indexed_literal_register(operand) is None
    program = build_instruction_ir([{'instructions': [{'op': 'M.USER', 'args': [operand]}]}], plc_model='Q03UDV')
    assert not any(row['code'] == 'SOURCE_INDEXED_LITERAL_SEMANTICS_UNVERIFIED'
                   for row in program['analysis']['findings'])


def test_indexed_integer_source_execution_remains_unknown_instead_of_using_a_plain_constant():
    from plc.ordered_source import build_instruction_ir
    from plc.ir import ir_to_ladder
    from simulator.bounded import ScanMachine
    program = build_instruction_ir([{'instructions': [{'op': 'MOV', 'args': ['K1Z0', 'D0']}]}],
                                   plc_model='Q03UDV')
    machine = ScanMachine(ir_to_ladder(program), plc_model='Q03UDV', initial={'Z0': 3, 'D0': 0},
                          execution_context={})
    result = machine.scan({'inputs': {}}, first_scan=True)
    assert machine.gaps
    assert result['after']['D0'] is None


@pytest.mark.parametrize('form,status', [('graph', 'error'), ('ordered_write', 'error'),
                                        ('ordered_bit', 'unverified')])
def test_indexed_integer_source_boundary_keeps_graph_write_and_unverified_operand_checks(form, status):
    from plc.ordered_source import build_instruction_ir
    from plc.ir import build_plc_ir
    from plc.core import PLCCore
    if form == 'graph':
        program = build_plc_ir({'device_comments': {}, 'rungs': [
            rung(1, outputs=[instruction('MOV', ['K1Z0', 'D0'])])]}, plc_model='Q03UDV')
    else:
        row = {'op': 'MOV', 'args': ['D0', 'K1Z0']} if form == 'ordered_write' else {'op': 'LD', 'args': ['K1Z0']}
        program = build_instruction_ir([{'instructions': [row]}], plc_model='Q03UDV')
    report = PLCCore().validate_project(program)
    assert not report['valid']
    if status == 'error':
        assert report.get('counts', {}).get('error') == 1
        assert 'error' in report
    else:
        # The existing LD catalogue does not establish numeric operand types;
        # the source-level finding still prevents a clean validation claim.
        assert report['status'] == 'unverified'
        assert report['counts']['unverified']


def test_uniform_common_condition_is_not_reread_after_an_earlier_branch_write():
    from application.compact_protocol import expand_compact_ladder
    from plc.ir import lower_rung_instructions
    wire = {"r": [{"c": ["NO M0"], "b": [
        {"i": ["NC M1"], "o": ["COIL M0"]},
        {"i": [], "o": ["COIL M20"]},
    ]}]}
    instructions = lower_rung_instructions(expand_compact_ladder(wire)["rungs"][0])
    state = _execute_boolean_instructions(instructions, {"M0": True, "M1": True, "M20": False})
    assert state["M0"] is False and state["M20"] is True
    state = _execute_boolean_instructions(instructions, state)
    assert state["M0"] is False and state["M20"] is False


@settings(max_examples=100,deadline=None)
@given(prefix=st.lists(_contact,max_size=2), paths=st.lists(_serial,min_size=1,max_size=4),
       suffix=st.lists(_contact,max_size=2))
@pytest.mark.parametrize("common_field", ["c", "s"])
def test_compact_or_and_polarity_survive_normalization_ir_and_consecutive_scans(prefix,paths,suffix,common_field):
    import itertools
    from application.compact_protocol import normalize_compact, expand_compact_ladder
    from plc.ir import lower_rung_instructions
    wire = {'r':[{common_field:[f'{k} {a}' for k,a in prefix], 'b':[{
        'i':[{'or':[[f'{k} {a}' for k,a in path] for path in paths]}] + [f'{k} {a}' for k,a in suffix],
        'o':['COIL M20']}]}]}
    normalized, _ = normalize_compact(wire)
    ladder = expand_compact_ladder(normalized)
    instructions = lower_rung_instructions(ladder['rungs'][0])
    old = True
    for bits in itertools.product([False,True],repeat=4):
        state = dict(zip(['M0','M1','M2','M3'],bits))
        def series(contacts):
            return all(state[address] if kind == 'NO' else not state[address] for kind,address in contacts)
        expected = series(prefix) and any(series(path) for path in paths) and series(suffix)
        old = _execute_boolean_instructions(instructions,{**state,'M20':old})['M20']
        assert old == expected


@pytest.mark.parametrize('cpu,op,args,reads,writes', [
    ('Q00J', 'DUTY', ['K1', 'K3', 'SM420'], [], ['SM420']),
    ('L02', 'DMOV', ['SD1880', 'D0'], ['SD1880', 'SD1881'], ['D0', 'D1']),
    ('FX1N', 'TWR', ['D0'], ['D0', 'D1', 'D2', 'D3', 'D4', 'D5', 'D6'], []),
    ('Q03UDV', 'LD', ['SD1282.0'], ['SD1282.0'], []),
    ('Q03UDV', 'OUT', ['YAF'], [], ['YAF']),
    ('Q03UDV', 'MOV', ['"D0"', 'D2'], [], ['D2']),
])
def test_ir_uses_selected_cpu_contract_and_preserves_word_bit_dependencies(cpu, op, args, reads, writes):
    assert analyze_instruction_access(op, args, plc_model=cpu) == (reads, writes)


@pytest.mark.parametrize('index', ['V', 'Z'])
def test_ir_preserves_unnumbered_index_register_dependencies(index):
    ladder = {'device_comments': {}, 'rungs': [{'rung_id': 1, 'branches': [{'inputs': [{'type': 'NO', 'address': 'X0'}],
        'outputs': [{'type': 'APP_INSTR', 'opcode': 'MOV', 'operands': ['D10' + index, 'D20']}]}]}]}
    program = build_plc_ir(ladder, plc_model='FX3U')
    assert program['devices'][index]['kind'] == index
    assert analyze_instruction_access('MOV', ['D10' + index, 'D20'], plc_model='FX3U') == (
        ['D10', index], ['D20'])


@given(st.integers(min_value=0, max_value=511))
@settings(max_examples=80)
def test_source_verified_double_word_dependency_includes_adjacent_word(index):
    reads, writes = analyze_instruction_access('DMOV', [f'D{index}', 'D1000'], plc_model='L02')
    assert reads == [f'D{index}', f'D{index + 1}']
    assert writes == ['D1000', 'D1001']
from application.generation import GenerationRequest, GenerationWorkflow, GenerationDependencies

from rendering.ladder_svg import AdvancedSVGLadder
from gxworks2.csv_export import generate_gx_works2_csv
from plc.ir import (
    PLCIRValidationError,
    analyze_instruction_access,
    apply_ladder_partial_to_ir,
    apply_network_patch,
    build_plc_ir,
    canonical_sha256,
    ir_to_ladder,
    validate_plc_ir,
)
from plc.st_renderer import (
    STTranslationError,
    render_plc_ir_to_st,
    validate_st_traceability,
)


@settings(max_examples=120, deadline=None)
@given(case=st.sampled_from([('FX1N', 'o', 127), ('Q00J', 'X', 2047),
                            ('Q03UDV', 'X', 8191), ('L02', 'X', 8191)]),
       index=st.integers(0, 10000), padding=st.integers(0, 4))
def test_source_io_address_spelling_matches_independent_manual_allocation(case, index, padding):
    from plc.validation import parse_device_address
    # Literal bounds/radices are the reviewed default allocations from the four
    # CPU manuals. They are not read back from the profile under test.
    cpu, format_code, ceiling = case
    address = 'X' + format(index, format_code).zfill(padding)
    result = parse_device_address(address.lower(), cpu)
    assert result == (('X', index) if index <= ceiling else None)


@settings(max_examples=80, deadline=None)
@given(padding=st.integers(0, 4), comment=st.text(alphabet='abcXYZ0123456789 \n', max_size=80))
def test_st_comments_and_device_zero_padding_preserve_static_call_identity(padding, comment):
    from plc.st_validation import inspect_st_program
    source = f'(* {comment} *)\nIF M{str(5000).zfill(padding)} AND SD1282.0 THEN\n S_SOCRCVS(TRUE,"U0",1,D5000);\nEND_IF;'
    report = inspect_st_program(source, plc_model='Q03UDV')
    assert report['status'] == 'passed' and report['statement_count'] == 1
    assert report['coverage']['ast_statement_count'] == 2  # IF plus its invocation.
    assert report['calls'] == [{'name': 'S_SOCRCVS', 'opcode': 'S.SOCRCVS',
                                'operands': ['"U0"', 'K1', 'D5000']}]
    assert report['devices'] == ['D5000', 'M5000', 'SD1282.0']


def test_q03udv_ladder_st_export_uses_source_bit_addresses_and_language_call_form():
    ladder = {'device_comments': {}, 'rungs': [{
        'rung_id': 1, 'header_element': None, 'shared_inputs': [], 'branches': [{
            'branch_id': 1, 'y_offset_level': 0,
            'inputs': [{'type': 'NO', 'address': address} for address in ('M5000','SD1282.0','SD1286.0')],
            'outputs': [{'type': 'APP_INSTR', 'opcode': 'S.SOCRCVS', 'operands': ['"U0"','K1','D5000']}],
        }]}]}
    program = build_plc_ir(ladder, plc_model='Q03UDV')
    text = render_plc_ir_to_st(program)
    assert 'SD1282.0' in text and 'SD1286.0' in text
    assert 'S_SOCRCVS(' in text and 'S.SOCRCVS(' not in text
    from plc.st_validation import inspect_st_program
    assert inspect_st_program(text, plc_model='Q03UDV')['status'] == 'passed'


def input_element(kind, address, label=""):
    return {"type": kind, "address": address, "label": label}


def instruction(opcode, operands, label=""):
    return {
        "type": "APP_INSTR",
        "opcode": opcode,
        "operands": operands,
        "label": label,
    }


def rung(rung_id, *, inputs=None, outputs=None, note=""):
    return {
        "rung_id": rung_id,
        "debug_note": note,
        "header_element": None,
        "shared_inputs": [],
        "branches": [
            {
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": inputs or [],
                "outputs": outputs or [],
            }
        ],
    }


def sample_ladder():
    return {
        "device_comments": {
            "X0": "启动",
            "X1": "停止",
            "M0": "运行保持",
            "D100": "HMI 累计值",
        },
        "rungs": [
            rung(
                10,
                inputs=[input_element("P", "X0", "启动沿")],
                outputs=[instruction("SET", ["M0"], "置位运行")],
                note="启动保持",
            ),
            rung(
                20,
                inputs=[input_element("NO", "M0"), input_element("NC", "X1")],
                outputs=[instruction("INC", ["D100"], "供 HMI 显示")],
                note="运行累计",
            ),
        ],
    }


def read_tab_rows(path):
    with open(path, encoding="utf-16", newline="") as handle:
        return list(csv.reader(handle, delimiter="\t"))


@pytest.mark.parametrize("operands,expected", [
    (["M10", "M13"], ["M10", "M11", "M12", "M13"]),
    (["D98", "D100"], ["D98", "D99", "D100"]),
    (["Y6", "Y11"], ["Y6", "Y7", "Y10", "Y11"]),
    (["T510", "T511"], ["T510", "T511"]),
    (["C235", "C237"], ["C235", "C236", "C237"]),
    (["C234", "C236"], ["C234", "C235", "C236"]),
    (["M13", "M10"], ["M13"]),
    (["M10", "M10"], ["M10"]),
    ([" m0010 ", "m0012"], ["M10", "M11", "M12"]),
])
def test_zrst_access_uses_the_documented_inclusive_range_and_descending_exception(operands, expected):
    # FX3 programming manual FNC 40, printed pp. 316-319, including octal Y.
    assert analyze_instruction_access("ZRST", operands) == ([], expected)


@pytest.mark.parametrize("operands,expected", [
    (["M100", "M103"], ["M100", "M101", "M102", "M103"]),
    (["D98", "D100"], ["D98", "D99", "D100"]),
    (["M103", "M100"], ["M103"]),
    (["M100", "M100"], ["M100"]),
])
def test_fx5u_zrst_source_checked_range_and_descending_exception(operands, expected):
    # JY997D55801-AB PDF319-320: like devices, inclusive; start>=end resets start only.
    assert analyze_instruction_access("ZRST", operands, plc_model="FX5U") == ([], expected)


@pytest.mark.parametrize("model,operands,reads,writes", [
    ("FX3U", ["M10", "D13"], [], ["M10", "D13"]),
    ("FX3U", ["M10Z0", "M13Z0"], ["Z0"], ["M10", "M13"]),
    ("FX3U", ["C199", "C201"], [], ["C199", "C201"]),
    ("FX3U", ["C201", "C199"], [], ["C199", "C201"]),
    ("FX3U", ["M10", "M99999999"], [], ["M10", "M99999999"]),
    ("FX3U", ["Y7", "Y9"], [], ["Y7", "Y9"]),
    ("FX5U", ["Y7", "Y10"], [], ["Y7", "Y10"]),
    ("Q03UDV", ["M10", "M13"], [], ["M10", "M13"]),
])
def test_zrst_unknown_or_invalid_ranges_do_not_claim_interior_writes(model, operands, reads, writes):
    assert analyze_instruction_access("ZRST", operands, plc_model=model) == (reads, writes)


@pytest.mark.parametrize("model", ["FX3U", "FX5U"])
@settings(max_examples=150)
@given(first=st.integers(min_value=0, max_value=7000), span=st.integers(min_value=0, max_value=50))
@example(first=500, span=50)
@example(first=0, span=0)
def test_zrst_memory_range_has_exact_bounds_and_no_gaps(model, first, span):
    reads, writes = analyze_instruction_access("ZRST", [f"M{first}", f"M{first + span}"], plc_model=model)
    assert reads == []
    assert len(writes) == span + 1
    assert writes[0] == f"M{first}"
    assert writes[-1] == f"M{first + span}"
    assert len(set(writes)) == len(writes)
    assert all(address.startswith("M") and first <= int(address[1:]) <= first + span for address in writes)


def test_zrst_full_access_survives_ir_validation_and_keeps_native_operands():
    data = {"device_comments": {}, "rungs": [
        rung(1, inputs=[input_element("NO", "M8002")], outputs=[instruction("ZRST", ["M500", "M599"])])
    ]}
    program = build_plc_ir(data)
    assert len(program["networks"][0]["writes"]) == 100
    assert program["devices"]["M550"]["written_by"] == ["N0001"]
    assert program["analysis"]["dependency_graph"]["writers"]["M550"][0]["op"] == "ZRST"
    assert program["networks"][0]["instructions"][-1]["args"] == ["M500", "M599"]
    assert ir_to_ladder(program) == data
    assert validate_plc_ir(program) is program


def test_ladder_builds_project_ir_with_deterministic_access_and_timing():
    ladder = sample_ladder()
    confirmed = {
        "io_table": [
            {"kind": "X", "address": "X0", "label": "启动按钮", "source": "user"},
            {"kind": "Y", "address": "Y0", "label": "电机", "source": "user"},
        ]
    }

    program = build_plc_ir(
        ladder,
        plc_model="FX3U",
        program_name="MAIN",
        revision=18,
        confirmed_spec=confirmed,
    )

    assert program["kind"] == "plc_program_ir"
    assert program["plc"] == {"series": "FX", "cpu": "FX3U"}
    assert program["program_name"] == "MAIN"
    assert program["revision"] == 18
    assert [item["id"] for item in program["networks"]] == ["N0010", "N0020"]
    assert program["networks"][0]["reads"] == ["X0"]
    assert program["networks"][0]["writes"] == ["M0"]
    assert program["networks"][1]["reads"] == ["X1", "M0", "D100"]
    assert program["networks"][1]["writes"] == ["D100"]
    assert program["timing"]["edge_triggers"] == [
        {"network": "N0010", "device": "X0", "edge": "rising"}
    ]
    assert program["devices"]["D100"]["access"] == "read_write"
    assert program["devices"]["D100"]["read_by"] == ["N0020"]
    assert program["devices"]["D100"]["written_by"] == ["N0020"]
    assert program["io_map"]["Y0"]["label"] == "电机"
    assert ir_to_ladder(program) == ladder
    assert validate_plc_ir(program) is program


def test_network_patch_is_revision_and_hash_guarded_and_does_not_touch_neighbors():
    program = build_plc_ir(sample_ladder(), revision=18)
    untouched = copy.deepcopy(program["networks"][1])
    replacement = copy.deepcopy(program["networks"][0]["ladder"])
    replacement["branches"][0]["inputs"].append(
        input_element("NC", "X3", "急停")
    )

    updated = apply_network_patch(
        program,
        {
            "base_revision": 18,
            "base_ir_sha256": canonical_sha256(program),
            "operations": [
                {
                    "operation": "modify_network",
                    "network": "N0010",
                    "ladder": replacement,
                }
            ],
        },
    )

    assert updated["revision"] == 19
    assert updated["networks"][0]["reads"] == ["X0", "X3"]
    assert updated["networks"][1]["ladder"] == untouched["ladder"]
    assert updated["networks"][1]["instructions"] == untouched["instructions"]
    assert validate_plc_ir(updated) is updated

    with pytest.raises(PLCIRValidationError, match="stale patch revision"):
        apply_network_patch(
            program,
            {
                "base_revision": 17,
                "operations": [
                    {
                        "operation": "modify_network",
                        "network": "N0010",
                        "ladder": replacement,
                    }
                ],
            },
        )

    with pytest.raises(PLCIRValidationError, match="base_ir_sha256"):
        apply_network_patch(
            program,
            {
                "base_ir_sha256": "0" * 64,
                "operations": [
                    {
                        "operation": "delete_network",
                        "network": "N0010",
                    }
                ],
            },
        )


def test_existing_partial_ladder_response_can_patch_ir_without_full_regeneration():
    program = build_plc_ir(sample_ladder(), revision=5)
    replacement = copy.deepcopy(program["networks"][1]["ladder"])
    replacement["branches"][0]["outputs"] = [
        instruction("DINC", ["D100"], "双字累计")
    ]

    updated = apply_ladder_partial_to_ir(
        program,
        {
            "mode": "partial",
            "device_comments": {"D100": "HMI 双字累计值"},
            "rungs": [replacement],
            "delete_rung_ids": [],
        },
    )

    assert updated["revision"] == 6
    assert updated["networks"][0]["ladder"] == program["networks"][0]["ladder"]
    assert updated["networks"][1]["instructions"][-1]["op"] == "DINC"
    assert updated["devices"]["D100"]["comment"] == "HMI 双字累计值"


def test_ir_rejects_stale_derived_access_sets():
    program = build_plc_ir(sample_ladder())
    tampered = copy.deepcopy(program)
    tampered["networks"][0]["reads"] = []

    with pytest.raises(PLCIRValidationError, match="reads are stale"):
        validate_plc_ir(tampered, validate_ladder=False)


def test_ir_is_a_compatible_source_for_csv_and_svg(tmp_path):
    ladder = sample_ladder()
    program = build_plc_ir(ladder)
    legacy_program = tmp_path / "legacy_program.csv"
    legacy_comments = tmp_path / "legacy_comments.csv"
    ir_program = tmp_path / "ir_program.csv"
    ir_comments = tmp_path / "ir_comments.csv"

    assert generate_gx_works2_csv(ladder, legacy_program, legacy_comments)
    assert generate_gx_works2_csv(program, ir_program, ir_comments)
    assert read_tab_rows(ir_program) == read_tab_rows(legacy_program)
    assert read_tab_rows(ir_comments) == read_tab_rows(legacy_comments)

    drawer = AdvancedSVGLadder()
    legacy_svg = drawer.generate_ladder(json.dumps(ladder, ensure_ascii=False))
    ir_svg = AdvancedSVGLadder().generate_ladder(
        json.dumps(program, ensure_ascii=False)
    )
    assert ir_svg == legacy_svg


def test_gxworks2_interline_statements_stay_within_64_legacy_bytes(tmp_path):
    ladder = sample_ladder()
    ladder["rungs"][0]["debug_note"] = (
        "停止状态且未满料时启动进入运行；NC X1为停止常闭触点，未按下时导通"
    )
    program_path = tmp_path / "program.csv"
    comments_path = tmp_path / "comments.csv"

    assert generate_gx_works2_csv(ladder, program_path, comments_path)
    rows = read_tab_rows(program_path)
    statements = [row[1] for row in rows[3:] if len(row) > 1 and row[1]]

    assert statements
    assert all(len(value.encode("gb18030")) <= 64 for value in statements)


def test_ir_renders_traceable_gxworks2_st_with_scan_conditions():
    program = build_plc_ir(sample_ladder(), revision=18)

    st_text = render_plc_ir_to_st(program)

    assert "(* NETWORK N0010 - 启动保持 *)" in st_text
    assert "SET(LDP(TRUE, X0), M0);" in st_text
    assert "(* NETWORK N0020 - 运行累计 *)" in st_text
    assert "INC((M0) AND (NOT X1), D100);" in st_text
    assert validate_st_traceability(program, st_text)


def test_ir_st_renderer_handles_parallel_compare_timer_counter_and_coil():
    ladder = {
        "device_comments": {},
        "rungs": [
            {
                "rung_id": 1,
                "debug_note": "复合逻辑",
                "header_element": {
                    "type": "BLOCK_INPUT",
                    "expression": "= D0 K10",
                    "label": "状态十",
                },
                "shared_inputs": [
                    {
                        "type": "parallel_block",
                        "branches": [
                            [input_element("NO", "X0")],
                            [input_element("NC", "X1")],
                        ],
                    }
                ],
                "branches": [
                    {
                        "branch_id": 1,
                        "y_offset_level": 0,
                        "inputs": [],
                        "outputs": [
                            {"type": "TIMER", "address": "T0", "value": "K50"},
                            {"type": "COUNTER", "address": "C200", "value": "K3"},
                            {"type": "COIL", "address": "Y0"},
                        ],
                    }
                ],
            }
        ],
    }
    program = build_plc_ir(ladder)

    st_text = render_plc_ir_to_st(program)

    condition = "((D0 = 10)) AND ((X0) OR (NOT X1))"
    assert f"OUT_T({condition}, TC0, 50);" in st_text
    assert f"OUT_C_32({condition}, CC200, 3);" in st_text
    assert f"OUT({condition}, Y0);" in st_text


def test_ir_st_renderer_fails_closed_on_unrepresentable_free_form_operand():
    ladder = sample_ladder()
    ladder["rungs"][1]["branches"][0]["outputs"] = [
        instruction("MOV", ["D0 + K1", "D100"], "invalid expression")
    ]
    # Skip the public ladder validator here so the test reaches the renderer's
    # own fail-closed boundary for corrupted/imported legacy IR.
    program = build_plc_ir(ladder)

    with pytest.raises(STTranslationError, match="unsupported ST operand"):
        render_plc_ir_to_st(program)


def test_generation_workflow_persists_ir_and_all_interchange_artifacts(monkeypatch, tmp_path):
    import application.model_api as api

    ladder = sample_ladder()
    monkeypatch.setattr(
        api,
        "stream_model_response",
        lambda *args, **kwargs: (
            "",
            json.dumps(ladder, ensure_ascii=False),
        ),
    )
    progress = []
    request = GenerationRequest('生成测试程序', effort='high', target_mode='ladder', model_name='offline', plc_model='FX3U', program_name='MAIN', revision=23)
    worker = GenerationWorkflow(request, tmp_path, on_event=lambda kind, payload: progress.append(("task-ir", payload)) if kind == "progress" else None)

    result = worker.run()

    assert result["revision"] == 23
    assert result["program_name"] == "MAIN"
    assert set(result["artifacts"]) == {
        "json",
        "ir",
        "svg",
        "program_csv",
        "comment_csv",
        "st_from_ir",
    }
    for name in result["artifacts"].values():
        assert (tmp_path / name).is_file()

    program = json.loads((tmp_path / result["artifacts"]["ir"]).read_text(encoding="utf-8"))
    persisted_ladder = json.loads(
        (tmp_path / result["artifacts"]["json"]).read_text(encoding="utf-8")
    )
    assert validate_plc_ir(program) is program
    assert ir_to_ladder(program) == persisted_ladder == ladder
    assert (tmp_path / result["artifacts"]["svg"]).read_text(encoding="utf-8").lstrip().startswith("<svg")
    assert result["ir_sha256"] == canonical_sha256(program)
    assert result["ladder_sha256"] == canonical_sha256(ladder)
    parsing_messages = [
        payload.get("message", "")
        for task_id, payload in progress
        if task_id == "task-ir" and payload.get("stage") in {"parsing", "parsed"}
    ]
    assert any("读取 JSON 结构" in message for message in parsing_messages)
    assert any("规范化梯形图协议" in message for message in parsing_messages)
    assert any("检查结构与地址" in message for message in parsing_messages)
    assert any("构建 PLC IR" in message for message in parsing_messages)
    assert "模型输出已解析为候选程序" in parsing_messages
    st_text = (tmp_path / result["artifacts"]["st_from_ir"]).read_text(
        encoding="utf-8"
    )
    assert validate_st_traceability(program, st_text)
    assert result["st_from_ir_sha256"] == hashlib.sha256(
        st_text.encode("utf-8")
    ).hexdigest()
    assert result["semantic_schema_version"] == 1
    assert result["semantic_summary"]["state_machine_count"] == 0
    assert program["logic"]["execution_model"] == "plc_scan_cycle"


def test_compiler_does_not_repair_semantic_mismatch_after_confirmed_generation(
    monkeypatch, tmp_path
):
    import application.model_api as api

    level = {
        "device_comments": {"X0": "按钮", "D0": "计数"},
        "rungs": [
            rung(
                1,
                inputs=[input_element("NO", "X0")],
                outputs=[instruction("INC", ["D0"])],
            )
        ],
    }
    edge = copy.deepcopy(level)
    edge["rungs"][0]["branches"][0]["inputs"][0]["type"] = "P"
    monkeypatch.setattr(
        api,
        "stream_model_response",
        lambda *args, **kwargs: ("", json.dumps(level, ensure_ascii=False)),
    )
    repair_requests = []

    def fake_repair(user_input, *args, **kwargs):
        repair_requests.append(user_input)
        return json.dumps(edge, ensure_ascii=False)

    request = GenerationRequest('每次按下 X0 一次，INC D0', effort='high', target_mode='ladder', model_name='offline', plc_model='FX3U', requirement_text='每次按下 X0 一次，INC D0')
    worker = GenerationWorkflow(request, tmp_path, dependencies=GenerationDependencies(generate_json=fake_repair, repair_response=fake_repair))

    result = worker.run()

    # The requirement text is model context, not a second local semantic judge.
    # If the model returns LEVEL logic, direct generation publishes that
    # candidate instead of spending another model call to rewrite it.
    assert repair_requests == []
    program = json.loads((tmp_path / "program.ir.json").read_text(encoding="utf-8"))
    assert program["networks"][0]["execution"]["semantics"] == ["LEVEL"]
    assert program["logic"]["requirements"] == []
    assert program["timing"]["coverage"] == []


def test_compiler_partial_edit_uses_ir_revision_and_preserves_other_networks(
    monkeypatch, tmp_path
):
    import application.model_api as api

    ladder = sample_ladder()
    base_ir = build_plc_ir(ladder, revision=23)
    replacement = copy.deepcopy(ladder["rungs"][0])
    replacement["branches"][0]["inputs"].append(
        input_element("NC", "X3", "急停")
    )
    partial = {
        "mode": "partial",
        "device_comments": {"X3": "急停"},
        "rungs": [replacement],
        "delete_rung_ids": [],
    }
    monkeypatch.setattr(
        api,
        "stream_model_response",
        lambda *args, **kwargs: ("", json.dumps(partial, ensure_ascii=False)),
    )
    request = GenerationRequest('把 X3 急停加进去', effort='high', target_mode='ladder', model_name='offline', previous_json=ladder, previous_ir=base_ir, current_version_json=ladder, plc_model='FX3U', program_name='MAIN', revision=24)
    worker = GenerationWorkflow(request, tmp_path)

    result = worker.run()

    program = json.loads((tmp_path / "program.ir.json").read_text(encoding="utf-8"))
    assert program["revision"] == 24
    assert program["networks"][0]["reads"] == ["X0", "X3"]
    assert program["networks"][1]["ladder"] == base_ir["networks"][1]["ladder"]
    assert ir_to_ladder(program)["device_comments"]["X3"] == "急停"


def test_session_service_reads_ir_first_and_explicitly_upgrades_legacy(tmp_path):
    from application.session import ProjectSession as SessionStore

    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project(name="IR project", plc_model="FX3U")

    legacy_id, legacy_dir = store.prepare_version(project["id"])
    legacy = sample_ladder()
    store._write_json(legacy_dir / "ladder.json", legacy)
    original_ladder_bytes = (legacy_dir / "ladder.json").read_bytes()
    store.complete_version(
        project["id"],
        legacy_id,
        {
            "target_mode": "ladder",
            "plc_model": "FX3U",
            "artifacts": {"json": "ladder.json"},
        },
    )
    assert store.load_ladder(project["id"], legacy_id) == legacy
    upgraded = store.load_program_ir(project["id"], legacy_id)
    assert upgraded["kind"] == "plc_program_ir"
    assert ir_to_ladder(upgraded) == legacy
    assert not (legacy_dir / "program.ir.json").exists()
    assert store.migrate_program_ir(project["id"], legacy_id) == upgraded
    assert (legacy_dir / "program.ir.json").exists()
    assert (legacy_dir / "ladder.json").read_bytes() == original_ladder_bytes
    migrated_version = store.get_version(project["id"], legacy_id)
    assert migrated_version["id"] == legacy_id
    assert migrated_version["revision"] == 1
    assert migrated_version["artifacts"]["ir"] == "program.ir.json"
    assert migrated_version["ir_sha256"] == canonical_sha256(upgraded)
    assert migrated_version["static_analysis_summary"]["rules_checked"]
    assert migrated_version["timing_summary"]["profile"]
    assert store.load_program_ir(project["id"], legacy_id) == upgraded

    current_id, current_dir = store.prepare_version(project["id"])
    current_ir = build_plc_ir(legacy, revision=2)
    store._write_json(current_dir / "ladder.json", {"device_comments": {}, "rungs": []})
    store._write_json(current_dir / "program.ir.json", current_ir)
    store.complete_version(
        project["id"],
        current_id,
        {
            "target_mode": "ladder",
            "plc_model": "FX3U",
            "revision": 2,
            "artifacts": {"json": "ladder.json", "ir": "program.ir.json"},
        },
    )
    assert store.load_ladder(project["id"], current_id) == legacy
    assert store.load_program_ir(project["id"], current_id) == current_ir


def test_session_store_never_overwrites_an_existing_future_ir(tmp_path):
    from application.session import ProjectSession as SessionStore

    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project(name="Future IR", plc_model="FX3U")
    version_id, version_dir = store.prepare_version(project["id"])
    legacy = sample_ladder()
    future_ir = {"kind": "plc_program_ir", "schema_version": 999, "future": True}
    store._write_json(version_dir / "ladder.json", legacy)
    store._write_json(version_dir / "future.ir.json", future_ir)
    original_future_bytes = (version_dir / "future.ir.json").read_bytes()
    store.complete_version(
        project["id"],
        version_id,
        {
            "target_mode": "ladder",
            "plc_model": "FX3U",
            "artifacts": {"json": "ladder.json", "ir": "future.ir.json"},
        },
    )

    compatibility_ir = store.load_program_ir(project["id"], version_id)
    assert ir_to_ladder(compatibility_ir) == legacy
    assert store.load_ladder(project["id"], version_id) == legacy
    assert (version_dir / "future.ir.json").read_bytes() == original_future_bytes
    assert not (version_dir / "program.ir.json").exists()
    assert store.get_version(project["id"], version_id)["artifacts"]["ir"] == "future.ir.json"


@pytest.mark.parametrize("platform,env_name,env_value,suffix", [
    ("win32", "APPDATA", "roaming", ""),
    ("linux", "XDG_DATA_HOME", "data", ""),
    ("darwin", "HOME", "home", "Library/Application Support"),
])
def test_default_workspace_keeps_historical_identity_without_gui(monkeypatch, tmp_path, platform, env_name, env_value, suffix):
    import storage.session as session
    monkeypatch.setattr(session.sys, "platform", platform)
    monkeypatch.setattr(session.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.delenv("PLC_AI_WORKSPACE_DIR", raising=False)
    root = tmp_path / env_value
    monkeypatch.setenv(env_name, str(root))
    if suffix:
        root = root / suffix
    expected = root / "PLC AI Studio" / "PLC AI Workbench" / "workspace"
    assert session.SessionStore(create=False).base_dir == expected
    assert not expected.exists()


def test_explicit_workspace_and_override_do_not_migrate_old_projects(monkeypatch, tmp_path):
    from application.session import ProjectSession as SessionStore
    selected, override = tmp_path / "selected", tmp_path / "override"
    monkeypatch.setenv("PLC_AI_WORKSPACE_DIR", str(override))
    assert SessionStore(create=False).base_dir == override
    assert SessionStore(selected, create=False).base_dir == selected
    assert list(tmp_path.iterdir()) == []


@given(st.integers(min_value=0, max_value=8), st.booleans(), st.sampled_from(['statement', 'note']))
@settings(max_examples=36, deadline=None)
def test_circuit_annotations_preserve_record_order_and_do_not_cut_a_branch(position, typed, text_kind):
    from plc.ordered_source import ordered_circuit_rows, validate_ordered_circuits
    rows=[{'op':op,'args':args} for op,args in [('LD',['X0']),('OUT',['Y0']),('MPS',[]),
          ('AND',['X1']),('OUT',['Y1']),('MPP',[]),('OUT',['Y2']),('END',[])]]
    note={'kind':'note','op':'','args':[],'record_index':30,'source_offset':60,'raw_hex':'00','text':'保留此注释'}
    if typed:
        rows = [{'kind': 'instruction', 'record_index': index, 'source_offset': index * 2,
                 'raw_hex': '00', **row} for index, row in enumerate(rows)]
    note['kind'] = text_kind
    rows.insert(position,note)
    rungs=[{'ordered_instructions':rows[:3]},{'ordered_instructions':rows[3:]}]
    circuits=ordered_circuit_rows(rungs)
    assert len(circuits)==1
    assert [row['_record'] for row in circuits[0][1]]==[row for row in rows if row['op']!='END']
    validate_ordered_circuits(rungs,plc_model='FX3U')


def test_annotation_does_not_hide_a_branch_without_an_incoming_condition():
    from plc.ordered_source import validate_ordered_circuits
    with pytest.raises(ValueError,match='condition|stack'):
        validate_ordered_circuits([{'ordered_instructions':[{'op':'MPS','args':[]},
            {'op':'OUT','args':['Y0']},{'op':'MPP','args':[]}]}],plc_model='FX3U')

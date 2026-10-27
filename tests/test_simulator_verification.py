
from tools.offline_inputs import private_path
import copy
import json
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from plc.ir import build_plc_ir, canonical_sha256
from application.session import ProjectSession as SessionStore
from simulator import InMemoryTestBackend, PLCTestRunner, SimulatorRegressionService
from simulator.models import normalize_test_suite
from simulator.verification import (
    SimulatorEvidenceError, evaluate_suite_result, execution_binding,
)


@pytest.mark.parametrize("case", json.loads((private_path('benchmarks/composition_calibration.json')).read_text(encoding="utf-8"))["cases"], ids=lambda row: row["id"])
def test_frozen_mutation_calibration_has_execution_witness_and_correct_alternatives(case):
    from simulator.calibration import replay_calibration
    programs = {role: _bounded_ladder(*rungs) for role, rungs in case["programs"].items()}
    report = replay_calibration(case, programs)
    controls = [r for r in report["results"] if r["role"] != "mutant"]
    mutant = next(r for r in report["results"] if r["role"] == "mutant")
    assert all(r["outcome"] == "correct_control" and r["first_difference"] is None for r in controls)
    assert mutant["outcome"] == "detected" and mutant["first_difference"]
    assert mutant["checker"]["violations"][0]["requirement_id"] == case["requirement_id"]
    assert mutant["native_compile"] == "not_executed"


def test_calibration_retains_unsupported_execution_and_structural_failures():
    import json
    from pathlib import Path
    from simulator.calibration import replay_calibration, calibration_summary
    case = json.loads((private_path('benchmarks/composition_calibration.json')).read_text(encoding="utf-8"))["cases"][0]
    programs = {"reference": _bounded_ladder(*case["programs"]["reference"]),
                "mutant": _bounded_ladder((["NO M9"], ["COIL M0"]))}
    record = replay_calibration(case, programs)
    assert record["results"][-1]["outcome"] == "unverified"
    summary = calibration_summary([record])
    assert summary["counts"]["unverified"] == 1 and summary["execution_coverage"] == 0.5


def _bounded_expr(op, name=None, value=None, args=(), integer=False):
    result = {'op': op, 'type': {'kind': 'int', 'bits': 16, 'signed': False} if integer else {'kind': 'bool'}}
    if name is not None: result['name'] = name
    if value is not None: result['value'] = value
    if args: result['args'] = list(args)
    return result


def _bounded_row(identity, kind, **fields):
    return {'id': identity, 'kind': kind, 'status': 'confirmed',
            'provenance': {'source': 'user_authored', 'evidence': ['independently specified relation']}, **fields}


def _bounded_ladder(*rows):
    from application.compact_protocol import expand_compact_ladder
    return expand_compact_ladder({'r': [{'h': None, 's': [], 'b': [{'i': i, 'o': o}]} for i, o in rows]})


def _bounded_spec(rows, model='FX3U'):
    return {'plc_model': model, 'behavior_constraints': rows, 'selected_approach': {'construction_plan': {
        'instances': [], 'internal_ranges': [], 'execution_context': {'program_type': 'scan', 'initial_execution_program': False}}}}


@pytest.mark.parametrize('base,model', [('D100','FX3U'), ('SD1880','L02'), ('SWF','L02')])
@settings(max_examples=40, derandomize=True)
@given(raw=st.binary(min_size=4,max_size=4), signed=st.booleans())
def test_multiword_reads_match_independent_byte_decoding(base,model,raw,signed):
    from simulator.bounded import _leaf
    # Spell the second address independently, including a hexadecimal carry.
    second = {'D100':'D101','SD1880':'SD1881','SWF':'SW10'}[base]
    memory = {base:int.from_bytes(raw[:2],'little'),second:int.from_bytes(raw[2:],'little')}
    assert _leaf(base,memory,bits=32,signed=signed,plc_model=model) == int.from_bytes(raw,'little',signed=signed)


@pytest.mark.parametrize('high,expected', [(0x1234,0x12345678),(0x8000,-2147461512),(None,None)])
def test_special_register_doubleword_boundaries(high,expected):
    from simulator.bounded import _leaf
    from plc.instruction_definition import UnknownInstructionSemantics
    memory = {'SD1880':0x5678,'SD1881':high}
    if high is None:
        with pytest.raises(UnknownInstructionSemantics,match='unknown_multiword_memory'):
            _leaf('SD1880',memory,bits=32,plc_model='L02')
    else:
        assert _leaf('SD1880',memory,bits=32,plc_model='L02') == expected


@pytest.mark.parametrize('model', ['Q03UDE', 'Q03UDV', 'L02'])
@settings(max_examples=40, derandomize=True)
@given(word=st.integers(min_value=0, max_value=65535), bit=st.integers(min_value=0, max_value=15))
def test_word_bit_views_match_independent_masking_and_remain_unknown_without_parent(model,word,bit):
    from simulator.bounded import _leaf
    from plc.instruction_definition import UnknownInstructionSemantics
    address='SD1282.'+format(bit,'X')
    assert _leaf(address, {'SD1282':word}, signed=False, plc_model=model) is bool(word & (1 << bit))
    with pytest.raises(UnknownInstructionSemantics,match='unknown_memory'):
        _leaf(address, {}, plc_model=model)


@pytest.mark.parametrize('language', ['ladder','st'])
@pytest.mark.parametrize('model', ['FX1S','FX1N','FX2N','FX3S','FX2C','Q03UDE','Q03UDV','Q12PH','Q12PRH','L02','L26-BT'])
def test_reviewed_boolean_control_needs_no_unrelated_startup_fact(language,model):
    from simulator.bounded import check_bounded_traces
    spec=_bounded_spec([],model)
    spec['io_bindings']=[{'address':address,'kind':'M'} for address in ('M0','M1','M20')]
    predicate=lambda value:_bounded_expr('eq',args=[_bounded_expr('device',name='M20'),_bounded_expr('constant',value=value)])
    trace={'initial':{'M20':True},'frames':[{'inputs':{'M0':a,'M1':b}} for a,b in [(True,False),(True,True),(False,False)]],
           'expectations':[{'scan':i,'requirement_id':'priority','predicate':predicate(v)} for i,v in enumerate([True,False,False])]}
    program=({'st_code':'M20 := M0 AND NOT M1;'} if language=='st' else _bounded_ladder((['NO M0','NC M1'],['COIL M20'])))
    report=check_bounded_traces(program,spec,[trace])
    assert report['status']=='no_violation_found_in_tested_scope',report
    assert not report['first_scan_fact']['available']
    assert not report['unsupported_or_missing']
    wrong=({'st_code':'IF M1 THEN M20 := FALSE; END_IF; M20 := M0;'} if language=='st' else
           _bounded_ladder((['NO M1'],['RST M20']),(['NO M0'],['COIL M20'])))
    report=check_bounded_traces(wrong,spec,[trace])
    assert report['status']=='violated' and report['violations'][0]['scan']==1,report
    assert report['violations'][0]['writes']


@pytest.mark.parametrize('model',['FX1NC','Q03UD','L02S','FX2N/FX2NC'])
def test_reviewed_control_groups_never_supply_facts_to_unreviewed_members(model):
    from simulator.bounded import check_bounded_traces
    from plc.runtime_semantics import control_runtime_facts
    assert not control_runtime_facts(model)
    report=check_bounded_traces(_bounded_ladder((['NO M0'],['COIL M20'])),_bounded_spec([],model),
        [{'initial':{'M20':False},'frames':[{'inputs':{'M0':True}}]}])
    assert report['status']=='unverified' and report['coverage']['traces_tested']==0


@pytest.mark.parametrize('model',['FX1S','Q03UDE','L26-BT'])
def test_missing_startup_semantics_still_block_initialization_claims(model):
    from simulator.bounded import check_bounded_traces
    spec=_bounded_spec([_bounded_row('boot','initialize',values={'M20':False})],model)
    report=check_bounded_traces(_bounded_ladder((['NO M0'],['RST M20'])),spec,
        [{'initial':{'M20':True},'frames':[{'inputs':{'M0':True}}]}])
    assert report['status']=='unverified'
    assert 'first_scan_condition_unverified' in report['checks'][0]['reasons']


@pytest.mark.parametrize('binding',[None, {'address':'M0','kind':'D'}])
def test_partial_st_types_require_a_confirmed_matching_binding(binding):
    from simulator.bounded import check_bounded_traces
    spec=_bounded_spec([],'FX1S')
    spec['io_bindings']=[] if binding is None else [binding]
    report=check_bounded_traces({'st_code':'M8028 := M0;'},spec,
        [{'initial':{'M8028':False},'frames':[{'inputs':{'M0':True}}]}])
    assert report['status']=='unverified' and report['coverage']['traces_tested']==0


@pytest.mark.parametrize('cpu,device',[('FX2C','D8001.0'),('FX3S','D8030.0'),('FX1S','M8028.0')])
def test_definition_of_a_device_does_not_grant_unreviewed_bit_view(cpu,device):
    from plc.device_definitions import resolve_device_definition
    from simulator.bounded import check_bounded_traces
    spec=_bounded_spec([],cpu)
    spec['hardware_profile']={'hardware_variant':'FX3S-20MR/ES'}
    spec['io_bindings']=[{'address':'M20','kind':'M'}]
    assert resolve_device_definition(device,cpu,context=spec['hardware_profile'])['value_type'] is None
    result=check_bounded_traces({'st_code':f'M20 := {device};'},spec,
        [{'initial':{'M20':False},'frames':[{'inputs':{device:True}}]}])
    assert result['status']=='unverified' and result['coverage']['traces_tested']==0


@pytest.mark.parametrize('input_address', ['X0','M0','S0'])
def test_automatic_traces_vary_confirmed_external_bits_but_not_internal_state(input_address):
    from simulator.bounded import finite_behavior_traces
    spec = _bounded_spec([_bounded_row('check','assertion',when=_bounded_expr('device',name=input_address),
        predicate=_bounded_expr('device',name='M100'))])
    spec['io_bindings'] = [{'address':input_address,'kind':input_address[0],'role':'input'},
                           {'address':'M100','kind':'M','role':'state'}]
    frames = [f['inputs'] for t in finite_behavior_traces(spec) for f in t['frames']]
    assert {f[input_address] for f in frames} == {False,True}
    assert all(set(f)=={input_address} for f in frames)


@pytest.mark.parametrize('source,violated', [
    ('M20 := (M0 OR M1) AND NOT M2;',False),
    ('IF M0 OR M1 THEN M20 := TRUE; END_IF; IF M2 THEN M20 := FALSE; END_IF;',True),
    ('M20 := FALSE;',True),
    ('M20 := NOT (M2 OR NOT (M1 OR M0));',False),
    ('IF M2 THEN M20 := FALSE; ELSE M20 := M0 OR M1; END_IF;',False),
    ('IF M2 THEN M20 := FALSE; ELSIF M0 THEN M20 := TRUE; ELSE M20 := M1; END_IF;',False),
    ('IF M2 THEN M20 := FALSE; END_IF; M20 := M0 OR M1;',True),
])
def test_st_shared_scan_checker_detects_priority_withdrawal_and_constant_false(source,violated):
    from simulator.bounded import check_confirmed_behavior
    dev = lambda a:_bounded_expr('device',name=a)
    required = _bounded_expr('and',args=[_bounded_expr('or',args=[dev('M0'),dev('M1')]),_bounded_expr('not',args=[dev('M2')])])
    spec = _bounded_spec([_bounded_row('output_relation','assertion',when=_bounded_expr('constant',value=True),
        predicate=_bounded_expr('eq',args=[dev('M20'),required]))])
    spec['io_bindings'] = [{'kind':'M','address':f'M{i}','role':'input'} for i in range(3)]
    result = check_confirmed_behavior({'st_code':source},spec)
    assert result['status']==('violated' if violated else 'no_violation_found_in_tested_scope'), result
    assert bool(result['violations']) == violated
    if violated:
        assert result['violations'][0]['input_sequence']


def test_st_if_samples_guard_once_and_unknown_branch_taints_writes():
    from plc.st_validation import bounded_st_instructions
    from simulator.bounded import ScanMachine
    for initial, expected in [({'M0':True},{'M0':False,'M1':True}), ({'M0':None},{'M0':None,'M1':None})]:
        instructions=bounded_st_instructions('IF M0 THEN M0 := FALSE; M1 := TRUE; ELSE M1 := FALSE; END_IF;',plc_model='FX3U')
        machine=ScanMachine({'rungs':[{'rung_id':1}]},plc_model='FX3U',initial=initial,
            execution_context={'program_type':'scan'},instructions=[instructions])
        observation=machine.scan({})
        assert {key:observation['after'][key] for key in expected}==expected


def test_st_unsupported_body_remains_unverified_even_for_trivial_assertion():
    from simulator.bounded import check_confirmed_behavior
    spec=_bounded_spec([_bounded_row('check','assertion',when=_bounded_expr('constant',value=True),predicate=_bounded_expr('constant',value=True))])
    result=check_confirmed_behavior({'st_code':'FOR i := 0 TO 2 DO M20 := TRUE; END_FOR;'},spec)
    assert result['status']=='unverified' and result['coverage']['traces_tested']==0


@pytest.mark.parametrize('language', ['ladder','st'])
@pytest.mark.parametrize('mutant', [False,True])
def test_filter_scope_is_not_an_execution_condition(language,mutant):
    from simulator.bounded import check_bounded_traces
    condition='M0 AND X0' if mutant else 'M0'
    program=({'st_code':f'IF {condition} THEN MOV(TRUE,K5,D8020); END_IF;'} if language=='st' else
        _bounded_ladder((['NO M0']+(['NO X0'] if mutant else []),['MOV K5 D8020'])))
    expected=lambda value:_bounded_expr('eq',args=[_bounded_expr('device',name='D8020',integer=True),_bounded_expr('constant',value=value,integer=True)])
    trace={'initial':{'D8020':17},'frames':[{'inputs':{'M0':False,'X0':False}},
        {'inputs':{'M0':True,'X0':False}}, {'inputs':{'M0':False,'X0':True}}],
        'expectations':[{'scan':i,'requirement_id':'filter','predicate':expected(v)} for i,v in enumerate([17,5,5])]}
    result=check_bounded_traces(program,_bounded_spec([],'FX1N'),[trace])
    assert result['status']==('violated' if mutant else 'no_violation_found_in_tested_scope'),result
    if mutant:
        assert result['violations'][0]['scan']==1 and result['violations'][0]['writes']==[]


@pytest.mark.parametrize('language',['ladder','st'])
@pytest.mark.parametrize('high',[0x1234,0x8000,None])
def test_l02_transfer_preserves_doubleword_order_and_unknown_high_word(language,high):
    from simulator.bounded import check_bounded_traces
    program=({'st_code':'DMOV(M0,SD1880,D100);'} if language=='st' else _bounded_ladder((['NO M0'],['DMOV SD1880 D100'])))
    predicate=_bounded_expr('and',args=[_bounded_expr('eq',args=[_bounded_expr('device',name='D100',integer=True),_bounded_expr('constant',value=0x5678,integer=True)]),
        _bounded_expr('eq',args=[_bounded_expr('device',name='D101',integer=True),_bounded_expr('constant',value=high or 0,integer=True)])])
    trace={'initial':{'SD1880':0x5678,'SD1881':high,'D100':0,'D101':0},'frames':[{'inputs':{'M0':True}}, {'inputs':{'M0':False}}],
        'expectations':[{'scan':i,'requirement_id':'words','predicate':predicate} for i in range(2)]}
    result=check_bounded_traces(program,_bounded_spec([],'L02'),[trace])
    assert result['status']==('unverified' if high is None else 'no_violation_found_in_tested_scope'),result


@pytest.mark.parametrize('opcode', ['PLS', 'PLF'])
@settings(max_examples=20, derandomize=True)
@given(current=st.sampled_from([False, True, None]), previous=st.sampled_from([False, True, None]))
def test_pulse_unknowns_match_every_possible_boolean_history(opcode, current, previous):
    from simulator.bounded import ScanMachine
    ladder = _bounded_ladder((['NO X0'], [opcode+' M100']))
    machine = ScanMachine(ladder, plc_model='FX3U', initial={'X0': current, 'M100': True},
        execution_context={'program_type': 'scan'}, previous_enables={'0.1': previous})
    observed = machine.scan({'inputs': {'X0': current}}, first_scan=True)
    # Independently enumerate all completions of the unknown history; only a
    # unanimous Boolean result permits a known output.
    possibilities = set()
    for before in [False, True] if previous is None else [previous]:
        for now in [False, True] if current is None else [current]:
            possibilities.add((before, now) == ((False, True) if opcode == 'PLS' else (True, False)))
    expected = possibilities.pop() if len(possibilities) == 1 else None
    assert observed['after']['M100'] is expected
    assert bool(machine.gaps) is (expected is None)


def test_disabled_pulse_establishes_history_before_read_modify_write():
    from simulator.bounded import ScanMachine
    ladder = _bounded_ladder((['NO X0'], ['PLS M100']), (['NO M100'], ['INC D0']))
    machine = ScanMachine(ladder, plc_model='FX3U', initial={'X0': False, 'M100': False, 'D0': 0},
        execution_context={'program_type': 'scan'})
    values = []
    for active in (False, True, True, False, True):
        values.append(machine.scan({'inputs': {'X0': active}})['after']['D0'])
    assert values == [0, 1, 1, 1, 2]
    assert not machine.gaps


@pytest.mark.parametrize('model,boot',[('FX3U','M8002'),('FX5U','SM402')])
def test_bounded_check_uses_ir_cpu_when_confirmed_projection_omits_runtime_identity(model,boot):
    from simulator.bounded import check_bounded_traces
    spec=_bounded_spec([_bounded_row('boot','initialize',values={'M100':False})],model)
    spec.pop('plc_model')
    trace={'initial':{'M100':True},'frames':[{'inputs':{}}]}
    correct=build_plc_ir(_bounded_ladder((['NO '+boot],['RST M100'])),plc_model=model)
    result=check_bounded_traces(correct,spec,[trace])
    assert result['status']=='no_violation_found_in_tested_scope'
    assert result['first_scan_fact']['available'] and boot in result['first_scan_fact']['devices']
    assert result['program_version']['plc_model']==model and 'plc_model' not in spec
    wrong=build_plc_ir(_bounded_ladder(([],['SET M100'])),plc_model=model)
    result=check_bounded_traces(wrong,spec,[trace])
    assert result['status']=='violated' and result['activation_blocked']


@pytest.mark.parametrize('conflict',[False,True])
def test_bounded_check_does_not_execute_with_missing_or_conflicting_cpu(conflict):
    from simulator.bounded import check_bounded_traces
    spec=_bounded_spec([_bounded_row('boot','initialize',values={'M100':False})])
    program=_bounded_ladder(([],['RST M100']))
    if conflict:
        program=build_plc_ir(program,plc_model='FX5U')
        reason='program_cpu_conflicts_with_specification'
    else:
        spec.pop('plc_model')
        reason='missing_cpu_control_facts'
    result=check_bounded_traces(program,spec,[{'initial':{'M100':True},'frames':[{'inputs':{}}]}])
    assert result['status']=='unverified' and result['coverage']['traces_tested']==0
    assert result['checks'][0]['reasons']==[reason]
    assert not result['violations'] and not result['activation_blocked']


@pytest.mark.parametrize('model,first', [('FX3U','M8002'), ('FX5U','SM402')])
def test_bounded_boot_old_state_and_nonzero_initialization(model, first):
    from simulator.bounded import check_bounded_traces
    spec = _bounded_spec([_bounded_row('boot', 'initialize', values={'D100': 17, 'Y0': True},
        execution_context={'program_type': 'scan', 'initial_execution_program': False})], model)
    trace = {'initial': {'D100': 923, 'Y0': False}, 'frames': [{'inputs': {'X0': True}}]}
    correct = _bounded_ladder((['NO '+first], ['MOV K17 D100', 'SET Y0']))
    wrong = _bounded_ladder((['NO '+first], ['MOV K17 D100', 'SET Y0']), (['NC '+first], ['COIL Y0']), (['NO X0'], ['MOV K0 D100']))
    assert check_bounded_traces(correct, spec, [trace])['status'] == 'no_violation_found_in_tested_scope'
    report = check_bounded_traces(wrong, spec, [trace], version_binding={'version_id': 'v3'})
    assert report['status'] == 'violated'
    witness = report['violations'][0]
    assert witness['scan_before']['D100'] == 923
    assert witness['program_version']['version_id'] == 'v3'
    assert witness['writes'][-1]['location']['rung_index'] >= 1


def test_bounded_shared_condition_is_saved_but_independent_condition_reads_immediate_write():
    from simulator.bounded import ScanMachine
    shared = {'device_comments': {}, 'rungs': [{'rung_id':1, 'header_element': {'type':'NO','address':'M0'},
        'shared_inputs': [], 'branches': [
            {'inputs': [], 'outputs': [{'type':'APP_INSTR','opcode':'RST','operands':['M0']}]},
            {'inputs': [], 'outputs': [{'type':'APP_INSTR','opcode':'SET','operands':['M1']}]}]}]}
    machine = ScanMachine(shared, plc_model='FX3U', initial={'M0': True, 'M1':False}, execution_context={'program_type':'scan'})
    assert machine.scan({'inputs':{}})['after']['M1'] is True
    independent = _bounded_ladder((['NO M0'], ['RST M0']), (['NO M0'], ['SET M1']))
    machine = ScanMachine(independent, plc_model='FX3U', initial={'M0':True,'M1':False}, execution_context={'program_type':'scan'})
    assert machine.scan({'inputs':{}})['after']['M1'] is False


@settings(max_examples=32, deadline=None, derandomize=True)
@given(frames=st.lists(st.tuples(st.booleans(), st.booleans()), min_size=1, max_size=8))
def test_ordered_native_sources_preserve_immediate_outputs_and_stack_across_scans(frames):
    from plc.ordered_source import build_instruction_ir
    from plc.ir import ir_to_ladder, lower_rung_instructions
    from simulator.bounded import ScanMachine
    rows = [{'op': op, 'args': args.split()} for op, args in [
        ('LD', 'M0'), ('OUT', 'M1'), ('MPS', ''), ('AND', 'M2'), ('OUT', 'M3'),
        ('MRD', ''), ('ANI', 'M2'), ('OUT', 'M4'), ('MPP', ''), ('AND', 'M3'),
        ('OUT', 'M5'), ('END', '')]]
    ir = build_instruction_ir([{'instructions': rows}], plc_model='FX3U')
    ladder = ir_to_ladder(ir)
    assert [row['op'] for row in lower_rung_instructions(ladder['rungs'][0])] == [row['op'] for row in rows]
    machine = ScanMachine(ladder, plc_model='FX3U', initial={f'M{i}': False for i in range(6)},
                          execution_context={'program_type': 'scan'})
    for a, b in frames:
        result = machine.scan({'inputs': {'M0': a, 'M2': b}})
        assert not machine.gaps
        assert {address: result['after'][address] for address in ('M1', 'M3', 'M4', 'M5')} == {
            'M1': a, 'M3': a and b, 'M4': a and not b, 'M5': a and b}


def test_bounded_unknown_memory_and_unsupported_instruction_are_not_zero_or_pass():
    from simulator.bounded import check_bounded_traces
    spec = _bounded_spec([_bounded_row('must_run', 'assertion', when=_bounded_expr('constant', value=True), predicate=_bounded_expr('device', name='Y0'))])
    missing = _bounded_ladder((['NO M9'], ['COIL Y0']))
    assert check_bounded_traces(missing, spec, [{'initial': {}, 'frames':[{'inputs':{}}]}])['status'] == 'unverified'
    unknown = _bounded_ladder(([], ['VENDOR_UNKNOWN M0']), ([], ['COIL Y0']))
    report = check_bounded_traces(unknown, spec, [{'initial':{},'frames':[{'inputs':{}}]}])
    assert report['status'] == 'unverified'
    assert any(r['reason'] == 'unknown_instruction_write_scope' for r in report['unsupported_or_missing'])


@pytest.mark.parametrize('model,address,opcode,elapsed,preset,expected', [
    ('FX3U','T201','OUT',1699,170,False), ('FX3U','T201','OUT',1700,170,True),
    ('FX5U','T32','OUT',1699,17,False), ('FX5U','T32','OUT',1700,17,True),
    ('FX5U','T0','OUTH',1700,170,True), ('FX5U','T700','OUTHS',1700,1700,True)])
def test_bounded_explicit_time_by_form_not_scan_count(model,address,opcode,elapsed,preset,expected):
    from simulator.bounded import ScanMachine
    ladder = _bounded_ladder((['NO X0'], [f'{opcode} {address} K{preset}']), (['NO '+address], ['COIL Y0']))
    machine = ScanMachine(ladder, plc_model=model, initial={}, execution_context={'program_type':'scan','initial_execution_program':False})
    result = machine.scan({'inputs':{'X0': True}, 'timer_elapsed_ms':{address:elapsed}})
    assert result['after']['Y0'] == expected
    without_time = machine.scan({'inputs': {'X0':True}})
    assert without_time['after']['Y0'] is None
    assert 'explicit_timer_elapsed_time_missing' in [g['reason'] for g in machine.gaps]


@pytest.mark.parametrize('digit,segments', list(enumerate([
    'abcdef', 'bc', 'abdeg', 'abcdg', 'bcfg', 'acdfg', 'acdefg', 'abcf',
    'abcdefg', 'abcdfg', 'abcefg', 'cdefg', 'adef', 'bcdeg', 'adefg', 'aefg',
])))
@given(upper=st.integers(min_value=0, max_value=255), high_source=st.integers(min_value=0, max_value=4095))
def test_segd_official_all_hex_glyphs_preserve_upper_destination_byte(digit, segments, upper, high_source):
    from simulator.bounded import ScanMachine
    source = high_source*16+digit
    expected = sum(1 << 'abcdefg'.index(segment) for segment in segments)
    machine = ScanMachine(_bounded_ladder((['NO X0'], ['SEGD D0 D10'])), plc_model='FX3U',
        initial={'X0':False, 'D0':source, 'D10':upper*256+255}, execution_context={'program_type':'scan'})
    actual = machine.scan({'inputs':{'X0':True}})['after']['D10']
    assert actual == upper*256+expected
    assert not machine.gaps
    assert machine.scan({'inputs':{'X0':False}})['after']['D10'] == actual


@pytest.mark.parametrize('digits', [1, 2, 3, 4])
@pytest.mark.parametrize('prefix', ['Y', 'M', 'S'])
@given(value=st.integers(min_value=0, max_value=65535), guard=st.booleans())
def test_digit_specified_transfer_crosses_octal_boundary_and_preserves_neighbours(digits, prefix, value, guard):
    from simulator.bounded import ScanMachine
    # Independently frozen sequential-device list: FX3U Y is octal, M/S decimal.
    suffixes = (['7','10','11','12','13','14','15','16','17','20','21','22','23','24','25','26','27']
                if prefix == 'Y' else [str(index) for index in range(7,24)])
    addresses = [prefix+suffix for suffix in suffixes[:4*digits]]
    left, right = prefix+'6', prefix+suffixes[4*digits]
    initial = {**dict.fromkeys(addresses,False), left:guard, right:guard, 'D0':value, 'D1':0}
    operand = f'K{digits}{prefix}7'
    machine = ScanMachine(_bounded_ladder(([],['MOV D0 '+operand]),([],['MOV '+operand+' D1'])),
        plc_model='FX3U', initial=initial, execution_context={'program_type':'scan'})
    result = machine.scan({'inputs':{}})['after']
    assert result['D1'] == value % (1 << (4*digits))
    assert result[left] is result[right] is guard
    if prefix == 'Y':
        assert not any(device in result for device in ('Y8','Y9'))
    assert not machine.gaps


@pytest.mark.parametrize('enabled_at_reset', [False, True])
def test_timer_reset_uses_explicit_reset_instant_only_when_already_running(enabled_at_reset):
    from simulator.bounded import ScanMachine
    machine = ScanMachine(_bounded_ladder((['NO X0'],['OUT T0 K10']),(['NO X1'],['RST T0'])),
        plc_model='FX3U', initial={'T0':0,'T0.contact':False}, execution_context={'program_type':'scan'})
    machine.scan({'timestamp_ms':0,'inputs':{'X0':True,'X1':False}})
    result = machine.scan({'timestamp_ms':500,'inputs':{'X0':enabled_at_reset,'X1':True}})
    assert result['after']['T0'] == 0 and result['after']['T0.contact'] is False
    result = machine.scan({'timestamp_ms':600,'inputs':{'X0':True,'X1':False}})
    assert result['after']['T0'] == (1 if enabled_at_reset else 0)
    assert not machine.gaps


@pytest.mark.parametrize('damage', ['unknown_group_bit', 'other_cpu'])
def test_digit_specified_unknown_inputs_and_other_cpu_remain_unverified(damage):
    from simulator.bounded import ScanMachine
    machine = ScanMachine(_bounded_ladder(([],['MOV K2M0 D0'])),
        plc_model='FX5U' if damage=='other_cpu' else 'FX3U',
        initial={'D0':3}, execution_context={'program_type':'scan','initial_execution_program':False})
    machine.scan({'inputs':{}})
    assert machine.gaps


def test_segd_device_access_includes_the_occupied_group_with_cpu_radix():
    from plc.ir import analyze_instruction_access
    reads,writes = analyze_instruction_access('SEGD',['D0','K2Y7'],plc_model='FX3U')
    assert set(writes) == {'Y7','Y10','Y11','Y12','Y13','Y14','Y15','Y16'}
    assert set(reads) >= set(writes) | {'D0'}
    assert not analyze_instruction_access('SEGD',['D0','K2Y7'],plc_model='FX5U')[1]


def test_bounded_all_off_does_not_pass_normal_progress():
    from simulator.bounded import check_bounded_traces
    spec = _bounded_spec([_bounded_row('start_progress', 'assertion', when=_bounded_expr('device', name='X0'), predicate=_bounded_expr('device', name='Y0'))])
    ladder = _bounded_ladder((['NC M8000'], ['COIL Y0']))
    report = check_bounded_traces(ladder, spec, [{'initial':{},'frames':[{'inputs':{'X0':True}}]}])
    assert report['status'] == 'violated'


def test_bounded_double_flip_and_stop_completion_competition():
    from simulator.bounded import check_bounded_traces
    dev = lambda a: _bounded_expr('device', name=a)
    const = lambda v: _bounded_expr('constant', value=v)
    effect = lambda v: {'target': {'device':'M100','kind':'bit'}, 'value':const(v)}
    group = _bounded_row('choice','transition_group', enable=const(True), transitions=[
        {'id':'stop', 'when':dev('X2'), 'effects':[effect(False)]},
        {'id':'complete', 'when':dev('X1'), 'effects':[effect(True)]}])
    spec = _bounded_spec([group])
    wrong = _bounded_ladder((['NO X2'], ['RST M100']), (['NO X1'], ['SET M100']))
    correct = _bounded_ladder((['NO X1','NC X2'], ['SET M100']), (['NO X2'], ['RST M100']))
    trace = {'starts_in_run':False,'initial':{'M100':False}, 'frames':[{'inputs':{'X1':True,'X2':True}}]}
    assert check_bounded_traces(wrong,spec,[trace])['status'] == 'violated'
    assert check_bounded_traces(correct,spec,[trace])['status'] == 'no_violation_found_in_tested_scope'


@given(index=st.integers(100,200), sequence=st.lists(st.tuples(st.booleans(),st.booleans()), min_size=2,max_size=8))
@settings(max_examples=50)
def test_bounded_event_tracks_source_not_acceptance(index, sequence):
    from simulator.bounded import check_bounded_traces
    output = 'M'+str(index)
    row = _bounded_row('edge','event', source=_bounded_expr('device',name='X0'), accept=_bounded_expr('device',name='X1'),
                       output=output, edge='rising', startup_policy='require_opposite')
    # Independently authored history/arming program. No constructor output is
    # used as the oracle or program under test.
    ladder = _bounded_ladder((['NO M8002'],['RST M10']), (['NC X0'],['SET M10']),
        (['NC M8002','NO M10','NO X0','NC M11','NO X1'],['COIL '+output]),
        (['NO X0'],['COIL M11']))
    frames = [{'inputs':{'X0':signal,'X1':accept}} for signal,accept in sequence]
    trace = {'initial':{'M10': True,'M11':True},'frames':frames}
    report = check_bounded_traces(ladder,_bounded_spec([row]),[trace])
    assert report['status'] == 'no_violation_found_in_tested_scope'


def test_bounded_adjacent_merged_events_and_wrong_second_edge():
    from simulator.bounded import check_bounded_traces
    const = _bounded_expr('constant',value=True)
    a = _bounded_row('a','event',source=_bounded_expr('device',name='X0'),accept=const,edge='rising',startup_policy='allow_initial_event',output='M100')
    b = _bounded_row('b','event',source=_bounded_expr('device',name='X1'),accept=const,edge='rising',startup_policy='allow_initial_event',output='M101')
    merge = _bounded_row('union','merge_events',events=['a','b'],output='M102')
    # Events are explicit test inputs to the merge-only relation, independently
    # frozen in the trace; adjacent scans must both remain ON.
    spec = _bounded_spec([merge])
    trace = {'starts_in_run':False,'initial':{'M100':True,'M101':False},
        'event_history':{'event.a':True,'event.b':False}, 'previous_inputs':{'M100':False},
        'frames':[{'inputs':{}},{'inputs':{}}]}
    wrong = _bounded_ladder((['P M100'],['COIL M102']))
    correct = _bounded_ladder(([{'or':[['NO M100'],['NO M101']]}],['COIL M102']))
    assert check_bounded_traces(correct,spec,[trace])['status'] == 'no_violation_found_in_tested_scope'
    assert check_bounded_traces(wrong,spec,[trace])['status'] == 'violated'


def test_bounded_trace_budget_and_scans_are_explicit():
    from simulator.bounded import check_bounded_traces
    row = _bounded_row('ok','assertion',when=_bounded_expr('constant',value=True),predicate=_bounded_expr('constant',value=True))
    trace = {'initial':{},'frames':[{'inputs':{}}]}
    report = check_bounded_traces(_bounded_ladder(([],['COIL M0'])), _bounded_spec([row]), [trace]*3, max_traces=2)
    assert report['status'] == 'unverified'
    assert report['coverage']['budget_exhausted']
    report = check_bounded_traces(_bounded_ladder(([],['COIL M0'])), _bounded_spec([row]), [{'initial':{},'frames':[{}]*9}])
    assert report['coverage']['traces_tested'] == 0
    assert report['status'] == 'unverified'


@pytest.mark.parametrize('model',['FX3U','FX5U'])
def test_independent_transition_groups_observe_prior_groups_but_members_share_prestate(model):
    from simulator.bounded import check_bounded_traces
    dev = lambda a: _bounded_expr('device',name=a)
    true = _bounded_expr('constant',value=True)
    effect = lambda a: {'target':{'device':a,'kind':'bit'},'value':true}
    first = _bounded_row('first','transition_group',enable=true,transitions=[{'id':'go','when':dev('X0'),'effects':[effect('M100')]}])
    second = _bounded_row('second','transition_group',enable=true,transitions=[{'id':'go','when':dev('M100'),'effects':[effect('M101')]}])
    ladder = _bounded_ladder((['NO X0'],['SET M100']),(['NO M100'],['SET M101']))
    trace = {'starts_in_run':False,'initial':{'M100':False,'M101':False},'frames':[{'inputs':{'X0':True}}]}
    result = check_bounded_traces(ladder,_bounded_spec([first,second],model),[trace])
    assert result['status'] == 'no_violation_found_in_tested_scope'


def test_two_transitions_are_detected_even_when_final_state_is_restored():
    from simulator.bounded import check_bounded_traces
    true = _bounded_expr('constant',value=True)
    row = _bounded_row('stop','transition_group',enable=true,transitions=[{'id':'stop','when':true,
        'effects':[{'target':{'device':'M100','kind':'bit'},'value':_bounded_expr('constant',value=False)}]}])
    ladder = _bounded_ladder(([],['SET M100']),([],['RST M100']))
    trace = {'starts_in_run':False,'initial':{'M100':False},'frames':[{'inputs':{}}]}
    result = check_bounded_traces(ladder,_bounded_spec([row]),[trace])
    assert result['violations'][0]['reason'] == 'repeated_group_write'
    assert len(result['violations'][0]['writes']) == 2


@pytest.mark.parametrize('model',['FX3U','FX5U'])
def test_uncertain_write_is_not_a_confirmed_duplicate_transition(model):
    from simulator.bounded import check_bounded_traces
    true = _bounded_expr('constant',value=True)
    row = _bounded_row('start','transition_group',enable=true,transitions=[{'id':'go','when':true,
        'effects':[{'target':{'device':'M100','kind':'bit'},'value':true}]}])
    # X9 is intentionally undeclared; the possible reset must remain unknown.
    ladder = _bounded_ladder(([],['SET M100']),(['NO X9'],['RST M100']))
    trace = {'starts_in_run':False,'initial':{'M100':False},'frames':[{'inputs':{}}]}
    result = check_bounded_traces(ladder,_bounded_spec([row],model),[trace])
    assert result['status'] == 'unverified' and not result['activation_blocked']
    assert not result['violations']
    assert result['checks'][0]['unknown_observations'] == 1


@pytest.mark.parametrize('model',['FX3U','FX5U'])
def test_bit_group_transfer_checks_fx3u_and_keeps_other_cpu_unverified(model):
    from simulator.bounded import check_bounded_traces
    row = _bounded_row('history','assertion',when=_bounded_expr('constant',value=True),
        predicate=_bounded_expr('device',name='M121'))
    ladder = _bounded_ladder(([],['MOV K1X0 K1M120']))
    trace = {'starts_in_run':False,'initial':{'M121':False},
             'frames':[{'inputs':{'X0':False,'X1':True,'X2':False,'X3':False}}]}
    result = check_bounded_traces(ladder,_bounded_spec([row],model),[trace])
    # The source-scoped FX3U transfer copies X1 to M121. Missing support for
    # another CPU must still never turn supplied initial memory into a fault.
    assert result['status'] == ('no_violation_found_in_tested_scope' if model == 'FX3U' else 'unverified')
    assert not result['activation_blocked']
    assert not result['violations']
    if model == 'FX5U':
        assert result['checks'][0]['unverified_witnesses']
        assert 'unknown_instruction_write_scope' in result['checks'][0]['reasons']


def test_generated_trace_domain_reports_budget_cutoff_and_checker_exceptions(monkeypatch):
    import simulator.bounded as bounded
    signals = [_bounded_expr('device',name='X'+str(i)) for i in range(6)]
    predicate = signals[0]
    for signal in signals[1:]:
        predicate = _bounded_expr('or',args=[predicate,signal])
    row = _bounded_row('progress','assertion',when=predicate,predicate=_bounded_expr('constant',value=True))
    spec = _bounded_spec([row])
    result = bounded.check_bounded_traces(_bounded_ladder(([],['COIL Y0'])),spec,
        bounded.finite_behavior_traces(spec,max_traces=2),max_traces=2)
    assert result['coverage']['budget_exhausted'] and result['status'] == 'unverified'
    def broken(*args,**kwargs): raise RuntimeError('fixture')
    monkeypatch.setattr(bounded.ScanMachine,'scan',broken)
    result = bounded.check_bounded_traces(_bounded_ladder(([],['COIL Y0'])),spec,[{'initial':{},'frames':[{}]}])
    assert result['status'] == 'unverified' and result['checker_exceptions'][0]['reason'] == 'checker_exception'


def test_independent_frozen_clock_progress_cannot_be_satisfied_by_all_off():
    from simulator.bounded import check_bounded_traces
    correct = _bounded_ladder((['NO X0'],['TIMER T0 K13']),(['NO T0'],['COIL Y0']))
    wrong = _bounded_ladder((['NC X0'],['COIL Y0']))
    trace={'id':'clock-progress','initial':{'Y0':False},'starts_in_run':False,
        'frames':[{'inputs':{'X0':True},'timer_elapsed_ms':{'T0':0}},
                  {'inputs':{'X0':True},'timer_elapsed_ms':{'T0':1299}},
                  {'inputs':{'X0':True},'timer_elapsed_ms':{'T0':1300}}],
        'expectations':[{'scan':1,'requirement_id':'delay','predicate':_bounded_expr('not',args=[_bounded_expr('device',name='Y0')])},
                        {'scan':2,'requirement_id':'progress','predicate':_bounded_expr('device',name='Y0')}]}
    result=check_bounded_traces(correct,_bounded_spec([]),[],frozen_time_traces=[trace])
    assert result['status'] == 'no_violation_found_in_tested_scope'
    assert result['coverage']['frozen_time_traces'] == 1
    result=check_bounded_traces(wrong,_bounded_spec([]),[],frozen_time_traces=[trace])
    assert result['violations'][0]['reason'] == 'explicit_progress_or_time_expectation'


@pytest.mark.parametrize('edge,opposite,active',[('rising',False,True),('falling',True,False)])
def test_startup_opposite_level_arms_the_first_new_edge(edge,opposite,active):
    from simulator.bounded import check_bounded_traces
    source=_bounded_expr('device',name='X0')
    row=_bounded_row('source','event',source=source,accept=_bounded_expr('constant',value=True),
        edge=edge,startup_policy='require_opposite',output='M100')
    source_kind='NO' if edge=='rising' else 'NC'
    opposite_kind='NC' if edge=='rising' else 'NO'
    history_kind='NC' if edge=='rising' else 'NO'
    ladder=_bounded_ladder((['NO M8002'],['RST M10']),([opposite_kind+' X0'],['SET M10']),
        (['NC M8002','NO M10',source_kind+' X0',history_kind+' M11'],['COIL M100']),(['NO X0'],['COIL M11']))
    trace={'initial':{'M10':True,'M11':active},'frames':[{'inputs':{'X0':opposite}},{'inputs':{'X0':active}}],
        'expectations':[{'scan':1,'requirement_id':'fresh_edge','predicate':_bounded_expr('device',name='M100')}]}
    assert check_bounded_traces(ladder,_bounded_spec([row]),[trace])['status']=='no_violation_found_in_tested_scope'


@pytest.mark.parametrize('model',['FX3U','FX5U'])
@pytest.mark.parametrize('projected_snapshot',[False,True])
def test_behavior_diagnostic_draft_cannot_activate_and_corrected_version_is_rechecked(tmp_path,model,projected_snapshot):
    from application.confirmed_generation_context import project_confirmed_specification
    from plc.ir import build_plc_ir
    store = SessionStore(base_dir=tmp_path/'workspace',legacy_dir=tmp_path)
    project = store.create_project(name='behavior',plc_model=model)
    row = _bounded_row('progress','assertion',when=_bounded_expr('device',name='X0'),predicate=_bounded_expr('device',name='Y0'))
    spec = _bounded_spec([row],model)
    store.set_confirmed_spec(project['id'],spec)
    snapshot_spec = project_confirmed_specification(spec) if projected_snapshot else spec
    def save(inputs):
        ladder = _bounded_ladder((inputs,['COIL Y0']))
        ir = build_plc_ir(ladder,plc_model=model)
        identity,folder = store.prepare_version(project['id'])
        store._write_json(folder/'program.ir.json',ir)
        return store.complete_version(project['id'],identity,{'target_mode':'ladder','plc_model':model,
            'validation_profile':'generation_structural','artifacts':{'ir':'program.ir.json'},'confirmed_spec_snapshot':snapshot_spec})
    wrong = save(['NC X0'])
    assert wrong['activation_blocked'] and wrong['lifecycle_status'] == 'diagnostic'
    assert wrong['behavior_check']['program_version']['version_id'] == wrong['id']
    assert wrong['behavior_check']['program_version']['plc_model'] == model
    with pytest.raises(ValueError,match='不能激活'): store.activate_version(project['id'],wrong['id'])
    correct = save(['NO X0'])
    assert correct['behavior_check']['status'] == 'no_violation_found_in_tested_scope'
    assert correct['behavior_check']['program_version'] != wrong['behavior_check']['program_version']
    assert store.get_project(project['id'])['active_version_id'] == correct['id']


def _suite():
    return normalize_test_suite({
        "name": "motor",
        "tests": [{
            "name": "start_stop", "initial": {"X0": 0},
            "steps": [
                {"id": "start", "at_ms": 0, "set": {"X0": 1}, "expect": {"Y0": 1}},
                {"id": "stop", "at_ms": 10, "set": {"X0": 0}, "wait_for": {"Y0": 0}},
            ],
        }],
    })


def _logic(backend, values):
    if "X0" in values:
        backend.values["Y0"] = values["X0"]


def _result(suite=None, backend=None):
    return PLCTestRunner(backend or InMemoryTestBackend(on_write=_logic)).run_suite(suite or _suite())


@pytest.fixture
def version(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project_id = store.create_project(name="evidence", plc_model="FX3U")["id"]
    version_id, folder = store.prepare_version(project_id)
    ladder = {
        "device_comments": {},
        "rungs": [{
            "rung_id": 1, "header_element": None, "shared_inputs": [],
            "branches": [{
                "branch_id": 1, "y_offset_level": 0,
                "inputs": [{"type": "NO", "address": "X0"}],
                "outputs": [{"type": "COIL", "address": "Y0"}],
            }],
        }],
    }
    program = build_plc_ir(ladder)
    store._write_json(folder / "ladder.json", ladder)
    store._write_json(folder / "program.ir.json", program)
    store.complete_version(project_id, version_id, {
        "target_mode": "ladder", "plc_model": "FX3U",
        "revision": program["revision"], "ir_sha256": canonical_sha256(program),
        "artifacts": {"json": "ladder.json", "ir": "program.ir.json"},
    })
    return store, project_id, version_id, program, folder


def _execute(version):
    store, project_id, version_id, _, _ = version
    return SimulatorRegressionService(
        store, backend=InMemoryTestBackend(on_write=_logic),
    ).run_version_suite(project_id, version_id, _suite())


def test_complete_observed_run_passes_without_mutating_evidence():
    suite = _suite()
    result = _result(suite)
    before = copy.deepcopy((suite, result))
    verification = evaluate_suite_result(suite, result)
    assert verification["status"] == "passed"
    assert verification["program_repair_allowed"] is False
    assert (suite, result) == before


@pytest.mark.parametrize("corruption,category", [
    ("missing_case", "incomplete"), ("duplicate_case", "incomplete"),
    ("missing_assertion", "incomplete"), ("duplicate_assertion", "incomplete"),
    ("missing_observation", "incomplete"), ("missing_final_values", "incomplete"),
    ("not_executed", "incomplete"), ("partial_execution", "incomplete"),
    ("contradictory_assertion", "evidence_invalid"),
    ("wrong_counts", "evidence_invalid"), ("wrong_suite_hash", "evidence_invalid"),
    ("wrong_case_hash", "evidence_invalid"), ("malformed_counts", "evidence_invalid"),
])
def test_pass_claim_needs_complete_consistent_observations(corruption, category):
    result = _result()
    case = result["results"][0]
    if corruption == "missing_case":
        result["results"] = []
    elif corruption == "duplicate_case":
        result["results"].append(copy.deepcopy(case))
    elif corruption == "missing_assertion":
        case["assertions"].pop()
    elif corruption == "duplicate_assertion":
        case["assertions"].append(copy.deepcopy(case["assertions"][0]))
    elif corruption == "missing_observation":
        case["trace"] = [event for event in case["trace"] if event["event"] != "wait_for"]
    elif corruption == "missing_final_values":
        next(event for event in case["trace"] if event["event"] == "final_sample")["values"] = {}
    elif corruption == "not_executed":
        case["execution_started"] = False
    elif corruption == "partial_execution":
        result["executed_count"] = 0
    elif corruption == "contradictory_assertion":
        case["assertions"][0]["passed"] = False
    elif corruption == "wrong_counts":
        result["counts"]["failed"] = 1
    elif corruption == "wrong_suite_hash":
        result["suite_sha256"] = "0" * 64
    elif corruption == "wrong_case_hash":
        case["test_sha256"] = "0" * 64
    elif corruption == "malformed_counts":
        result["counts"] = []
    verdict = evaluate_suite_result(_suite(), result)
    assert verdict["status"] == "blocked"
    assert verdict["category"] == category
    assert verdict["program_repair_allowed"] is False


def test_action_only_run_is_not_behavioral_verification():
    suite = normalize_test_suite({"tests": [{
        "name": "stimulus_only", "initial": {"X0": 0},
        "steps": [{"set": {"X0": 1}}],
    }]})
    result = _result(suite)
    assert result["status"] == "passed"  # The runner did complete its actions.
    assert evaluate_suite_result(suite, result)["category"] == "incomplete"


def test_changed_expectation_cannot_reuse_run_with_same_case_and_step_names():
    original = _suite()
    result = _result(original)
    changed = copy.deepcopy(original)
    changed["tests"][0]["steps"][0]["expect"][0]["value"] = 0
    assert evaluate_suite_result(changed, result)["category"] == "evidence_invalid"


def test_missing_device_value_cannot_satisfy_not_equal_assertion():
    class MissingValueBackend(InMemoryTestBackend):
        def read_many(self, addresses):
            return {address: None if address == "Y0" else 0 for address in addresses}

    suite = normalize_test_suite({"tests": [{
        "name": "observed",
        "steps": [{"expect": [{"address": "Y0", "operator": "ne", "value": 0}]}],
    }]})
    result = _result(suite, MissingValueBackend())
    assert result["status"] == "passed"
    assert evaluate_suite_result(suite, result)["category"] == "incomplete"


def test_assertion_and_invariant_failures_are_reviewable():
    suite = _suite()
    failed = _result(suite, InMemoryTestBackend())
    verification = evaluate_suite_result(suite, failed)
    assert verification["category"] == "assertion"
    assert verification["program_repair_allowed"]
    suite["tests"][0]["invariants"] = [{"type": "mutual_exclusion", "devices": ["Y0", "Y1"]}]
    suite = normalize_test_suite(suite)
    result = _result(suite, InMemoryTestBackend(initial={"Y1": 1}, on_write=_logic))
    verification = evaluate_suite_result(suite, result)
    assert verification["category"] == "invariant"
    assert verification["program_repair_allowed"]


@pytest.mark.parametrize("phase,category", [
    ("connect", "environment"), ("initial", "setup"), ("stimulus", "setup"), ("read", "runtime"),
])
def test_failure_taxonomy_uses_execution_stage_not_program_patch(phase, category):
    class BrokenBackend(InMemoryTestBackend):
        def connect(self):
            if phase == "connect":
                raise RuntimeError("simulator unavailable")
            return super().connect()

        def write_many(self, values):
            if phase == "initial" or (phase == "stimulus" and values.get("X0") == 1):
                raise RuntimeError("invalid test stimulus")
            return super().write_many(values)

        def read_many(self, addresses):
            if phase == "read" and self.values.get("X0") == 1:
                raise RuntimeError("observation read failed")
            return super().read_many(addresses)

    verdict = evaluate_suite_result(_suite(), _result(backend=BrokenBackend(on_write=_logic)))
    assert verdict["category"] == category
    assert verdict["status"] == "blocked"
    assert verdict["program_repair_allowed"] is False


def test_saved_run_binds_program_suite_result_and_keeps_backend_identity(version):
    execution = _execute(version)
    store, project_id, version_id, program, _ = version
    loaded = store.load_simulator_run(project_id, version_id, execution["record"]["run_id"])
    binding = loaded["binding"]
    assert binding["ir_sha256"] == canonical_sha256(program)
    assert binding["suite_sha256"] == canonical_sha256(loaded["suite"])
    assert binding["result_sha256"] == canonical_sha256(loaded["result"])
    assert binding["binding_scope"] == "execution_snapshot"
    assert loaded["verification"]["status"] == "passed"
    assert loaded["record"]["backend_kinds"] == ["test_memory_not_plc_simulator"]


@pytest.mark.parametrize("corruption", ["suite_binding", "trace_binding", "suite_contents", "trace_contents", "missing_trace"])
def test_mismatched_or_modified_artifacts_cannot_be_loaded_as_evidence(version, corruption):
    execution = _execute(version)
    store, project_id, version_id, _, folder = version
    record = execution["record"]
    target = folder / record["suite_artifact" if corruption.startswith("suite") else "trace_artifact"]
    payload = store._read_json(target)
    if corruption.endswith("binding"):
        payload["binding"]["version_id"] = "another_version"
    elif corruption == "suite_contents":
        payload["suite"]["tests"][0]["steps"][0]["expect"][0]["value"] = 0
    elif corruption == "missing_trace":
        target.unlink()
        assert store.load_simulator_run(project_id, version_id, record["run_id"]) is None
        return
    else:
        payload["result"]["results"][0]["trace"].clear()
    store._write_json(target, payload)
    with pytest.raises(SimulatorEvidenceError):
        store.load_simulator_run(project_id, version_id, record["run_id"])


def test_program_change_during_execution_cannot_be_rebound_at_save(version):
    store, project_id, version_id, program, folder = version
    changed = copy.deepcopy(program)
    changed["revision"] += 1
    changed_once = False

    def change_program(backend, values):
        nonlocal changed_once
        _logic(backend, values)
        if not changed_once:
            changed_once = True
            store._write_json(folder / "program.ir.json", changed)
            store.update_version_metadata(project_id, version_id, {
                "revision": changed["revision"], "ir_sha256": canonical_sha256(changed),
            })

    service = SimulatorRegressionService(store, backend=InMemoryTestBackend(on_write=change_program))
    with pytest.raises(SimulatorEvidenceError) as error:
        service.run_version_suite(project_id, version_id, _suite())
    assert error.value.category == "version_conflict"
    assert store.list_simulator_runs(project_id, version_id) == []


def test_invalid_pass_is_not_persisted_as_success(version):
    store, project_id, version_id, program, _ = version
    result = _result()
    result["results"] = []
    with pytest.raises(SimulatorEvidenceError):
        store.save_simulator_run(
            project_id, version_id, _suite(), result,
            execution_snapshot=execution_binding(project_id, version_id, program, _suite()),
        )
    assert store.list_simulator_runs(project_id, version_id) == []


def test_stale_approved_binding_is_rejected_before_runtime_preparation(version):
    store, project_id, version_id, program, _ = version

    class Preparer:
        def prepare(self):
            pytest.fail("A stale plan must not prepare the runtime")

    binding = execution_binding(project_id, version_id, program, _suite())
    binding["suite_sha256"] = "0" * 64
    service = SimulatorRegressionService(store, backend=InMemoryTestBackend(), preparer=Preparer())
    with pytest.raises(SimulatorEvidenceError):
        service.run_version_suite(project_id, version_id, _suite(), expected_binding=binding)
    assert store.list_simulator_runs(project_id, version_id) == []


@pytest.mark.parametrize("field,value", [
    ("backend_kinds", ["gx_simulator2_gateway"]), ("counts", {"passed": 999}), ("passed", False),
])
def test_index_cannot_change_result_or_backend_provenance(version, field, value):
    execution = _execute(version)
    store, project_id, version_id, _, _ = version
    project = store.get_project(project_id)
    project["versions"][0]["simulator_runs"][0][field] = value
    store.save_project(project)
    with pytest.raises(SimulatorEvidenceError):
        store.load_simulator_run(project_id, version_id, execution["record"]["run_id"])


def test_legacy_record_remains_readable_without_migration_or_new_verdict(version):
    execution = _execute(version)
    store, project_id, version_id, _, folder = version
    record = execution["record"]
    new_fields = (
        "evidence_schema_version", "binding_scope", "suite_sha256", "result_sha256",
    )
    for key in ("suite_artifact", "trace_artifact"):
        target = folder / record[key]
        payload = store._read_json(target)
        for field in new_fields:
            payload["binding"].pop(field)
        store._write_json(target, payload)
    project = store.get_project(project_id)
    old_record = project["versions"][0]["simulator_runs"][0]
    for field in (*new_fields, "verification"):
        old_record.pop(field)
    # A genuinely old version may not have a persisted IR yet.
    project["versions"][0]["artifacts"].pop("ir")
    store.save_project(project)
    (folder / "program.ir.json").unlink()
    project_folder = store.project_dir(project_id)
    before = {path: path.read_bytes() for path in project_folder.rglob("*") if path.is_file()}
    loaded = store.load_simulator_run(project_id, version_id, record["run_id"])
    assert loaded["result"]["status"] == "passed"
    assert loaded["verification"]["category"] == "legacy_evidence"
    assert loaded["verification"]["program_repair_allowed"] is False
    assert before == {path: path.read_bytes() for path in project_folder.rglob("*") if path.is_file()}


@pytest.mark.parametrize('source,expected', [
    ('VAR i:INT; total:INT:=0; END_VAR FOR i:=3 TO 1 BY -1 DO total:=total+i; END_FOR; D0:=total;', [6,12]),
    ('VAR i:INT:=0; END_VAR WHILE i<3 DO i:=i+1; END_WHILE; REPEAT i:=i-1; UNTIL i=0 END_REPEAT; D0:=i;', [0,0]),
    ('VAR i:INT; END_VAR FOR i:=0 TO 3 DO IF i=2 THEN EXIT; END_IF; D0:=i; END_FOR;', [1,1]),
    ('D0:=3; RETURN; D0:=7;', [3,3]),
    ('CASE D0 OF -1,0: D0:=2; 1..3: D0:=4; ELSE D0:=0; END_CASE;', [2,4]),
    ('TYPE RecordType:STRUCT Value:INT; END_STRUCT; END_TYPE PROGRAM MAIN VAR item:RecordType; values:ARRAY[-1..1] OF INT:=[2,4,6]; END_VAR item.Value:=values[0]; D0:=item.Value; END_PROGRAM', [4,4]),
    ('FUNCTION Twice:INT VAR_INPUT A:INT; END_VAR Twice:=A*2; END_FUNCTION PROGRAM MAIN D0:=Twice(3); END_PROGRAM', [6,6]),
    ('FUNCTION_BLOCK Accumulate VAR_INPUT Amount:INT; END_VAR VAR_OUTPUT Sum:INT:=0; END_VAR Sum:=Sum+Amount; END_FUNCTION_BLOCK PROGRAM MAIN VAR instance:Accumulate; END_VAR instance(Amount:=2); D0:=instance.Sum; END_PROGRAM', [2,4]),
    ('FUNCTION_BLOCK Increment VAR_IN_OUT Value:INT; END_VAR Value:=Value+1; END_FUNCTION_BLOCK PROGRAM MAIN VAR instance:Increment; count:INT:=0; END_VAR instance(Value:=count); D0:=count; END_PROGRAM', [1,2]),
])
def test_st_extended_control_and_scoped_storage_execute_in_shared_machine(source,expected):
    from plc.st_validation import bounded_st_instructions
    from simulator.bounded import ScanMachine
    instructions=bounded_st_instructions(source,plc_model='FX3U')
    machine=ScanMachine({'rungs':[{'rung_id':1}]},plc_model='FX3U',initial={'D0':0},execution_context={},instructions=[instructions])
    for value in expected:
        observation=machine.scan({'inputs':{}})
        assert not machine.gaps,machine.gaps
        assert observation['after']['D0']==value


@pytest.mark.parametrize('source,value,expected', [
    ('IF D1 < 0 THEN D0:=1; ELSE D0:=0; END_IF;', 65535, 1),
    ('IF D1 > 0 THEN D0:=1; ELSE D0:=0; END_IF;', 65535, 0),
    ('IF D1 < 0 THEN D0:=1; ELSE D0:=0; END_IF;', 32768, 1),
    ('IF Unsigned > WORD#0 THEN D0:=1; ELSE D0:=0; END_IF;', 65535, 1),
])
def test_st_raw_word_comparison_is_signed_while_word_declaration_is_unsigned(source, value, expected):
    from plc.st_validation import bounded_st_instructions
    from simulator.bounded import ScanMachine
    context = {'selected_source': 'MAIN.Program.pou', 'pous': [dict(name='MAIN', program='MAIN.Program.pou',
        kind='program', language='st', st_text=source,
        declarations=[dict(name='Unsigned', data_type='WORD', class_name='VAR', device='D1')])]}
    instructions = bounded_st_instructions(source, plc_model='FX3U', project_context=context)
    machine = ScanMachine({'rungs': [{}]}, plc_model='FX3U', initial={'D0': 9, 'D1': value},
        execution_context={}, instructions=[instructions])
    observation = machine.scan({})
    assert not machine.gaps, machine.gaps
    assert observation['after']['D0'] == expected


@pytest.mark.parametrize('exponent,expected', [(3, 8.0), (-126, 0.0), (-127, 0.0)])
def test_st_power_uses_shared_compiler_lowering_for_ql_underflow(exponent, expected):
    from plc.st_validation import bounded_st_instructions, inspect_st_program
    from simulator.bounded import ScanMachine
    source = f'VAR Result:REAL; END_VAR Result:=2.0 ** ({exponent}.0);'
    context = {'software_version': '1.635.0.1'}
    assert inspect_st_program(source, plc_model='Q03UDV', project_context=context)['semantic'] == 'passed'
    instructions = bounded_st_instructions(source, plc_model='Q03UDV', project_context=context)
    machine = ScanMachine({'rungs': [{}]}, plc_model='Q03UDV', initial={},
        execution_context={}, instructions=[instructions])
    machine.scan({})
    assert not machine.gaps, machine.gaps
    assert next(value for key, value in machine.memory.items() if key.endswith('.RESULT')) == expected


@pytest.mark.parametrize('source,expected', [
    ('FUNCTION_BLOCK Forever WHILE TRUE DO END_WHILE; END_FUNCTION_BLOCK PROGRAM MAIN VAR instance:Forever; END_VAR D0:=1; instance(); D0:=2; END_PROGRAM', None),
    ('WHILE TRUE DO END_WHILE;', None),
])
def test_st_loop_and_nested_calls_share_execution_budget(source, expected):
    from plc.st_validation import bounded_st_instructions
    from simulator.bounded import ScanMachine
    from simulator.st_execution import ExecutionBudget
    instructions=bounded_st_instructions(source,plc_model='FX3U')
    budget=ExecutionBudget(per_scan=20,total=30,call_depth=4)
    machine=ScanMachine({'rungs':[{}]},plc_model='FX3U',initial={'D0':9},execution_context={},instructions=[instructions],execution_budget=budget)
    observation=machine.scan({})
    assert observation['after']['D0'] == expected
    assert budget.remaining==10 and budget.depth==0
    assert any(g['reason']=='st_step_budget_exhausted' for g in machine.gaps)


@pytest.mark.parametrize('language',['fbd','ladder'])
def test_st_calls_graph_fb_through_existing_source_connectivity(language):
    from gxw.object_model import default_baseline,read_project_context
    from gxw.execution import project_execution_source
    from plc.st_validation import bounded_st_instructions
    from simulator.bounded import ScanMachine
    execution=project_execution_source(read_project_context(default_baseline()))
    assert execution['status']=='available'
    context={'pous':[dict(name='RelayLogic',program='RelayLogic.pou',kind='function_block',language=language,
        declarations=[dict(name='X0',data_type='BOOL',class_name='VAR_INPUT'),dict(name='X1',data_type='BOOL',class_name='VAR_INPUT'),
                      dict(name='Y0',data_type='BOOL',class_name='VAR_OUTPUT')],execution_source=execution)]}
    instructions=bounded_st_instructions('VAR instance:RelayLogic; END_VAR instance(X0:=M0,X1:=M1); M20:=instance.Y0;',
        plc_model='FX3U',project_context=context)
    machine=ScanMachine({'rungs':[{}]},plc_model='FX3U',initial={},execution_context={},instructions=[instructions])
    assert machine.scan({'inputs':{'M0':True,'M1':False}})['after']['M20'] is False
    assert machine.scan({'inputs':{'M0':True,'M1':True}})['after']['M20'] is True
    assert not machine.gaps


@settings(max_examples=30,derandomize=True,deadline=None)
@given(start=st.integers(min_value=-20,max_value=20),count=st.integers(min_value=0,max_value=12),step=st.sampled_from([-3,-2,-1,1,2,3]))
def test_st_for_accumulation_matches_independent_finite_sequence(start,count,step):
    from plc.st_validation import bounded_st_instructions
    from simulator.bounded import ScanMachine
    stop=start+(count-1)*step
    source=f'VAR i:INT; total:INT:=0; END_VAR FOR i:={start} TO {stop} BY {step} DO total:=total+i; END_FOR; D0:=total;'
    ops=bounded_st_instructions(source,plc_model='FX3U')
    machine=ScanMachine({'rungs':[{}]},plc_model='FX3U',initial={},execution_context={},instructions=[ops])
    actual=machine.scan({})['after']['D0']
    assert actual==(sum(range(start,start+count*step,step)) & 65535)
    assert not machine.gaps


@pytest.mark.parametrize('base,power,expected,status', [
    (2.0, 3.0, 8.0, 'passed'), (0.0, 3.0, None, 'failed'), (-2.0, 2.0, None, 'failed'),
    # Compiler LOG/multiply/EXP rounding gives a subnormal at the nominal
    # minimum. The physical-CPU manual domain must not inherit Simulator2's
    # wider implementation thresholds.
    (2.0, -126.0, None, 'failed'), (2.0, -127.0, None, 'failed'), (2.0, 128.0, None, 'failed'),
])
def test_dynamic_st_power_checks_input_and_result_before_assigning(base, power, expected, status):
    from plc.st_validation import bounded_st_instructions
    from simulator.bounded import ScanMachine
    source = 'PROGRAM MAIN VAR Base:REAL; Power:REAL; Result:REAL; END_VAR Result:=Base ** Power; END_PROGRAM'
    instructions = bounded_st_instructions(source, plc_model='FX3U')
    machine = ScanMachine({'rungs': [{}]}, plc_model='FX3U', initial={'MAIN.BASE': base, 'MAIN.POWER': power},
                          execution_context={}, instructions=[instructions])
    machine.scan({})
    assert machine.memory.get('MAIN.RESULT') == expected
    assert bool(machine.constraint_violations) == (status == 'failed')
    if status == 'failed':
        assert any(row['constraint']['status'] == 'failed' for row in machine.constraint_violations)
    else:
        assert not machine.gaps


@pytest.mark.parametrize('exponent,expected', [(3.0, 8.0), (-126.0, 1.1754907074462803e-38)])
def test_fx_power_native_simulator_context_uses_its_own_range_without_relaxing_cpu_rules(exponent, expected):
    from plc.st_validation import bounded_st_instructions
    from simulator.bounded import ScanMachine
    source = 'PROGRAM MAIN VAR Base:REAL; Power:REAL; Result:REAL; END_VAR Result:=Base ** Power; END_PROGRAM'
    context = dict(software_version='1.635.0.1', numeric_engine='gx_simulator2_fx', simulator_version='11.60.0.0')
    instructions = bounded_st_instructions(source, plc_model='FX3U', project_context=context)
    machine = ScanMachine({'rungs': [{}]}, plc_model='FX3U', initial={'MAIN.BASE': 2.0, 'MAIN.POWER': exponent},
        execution_context={}, instructions=[instructions])
    machine.scan({})
    assert not machine.gaps and not machine.constraint_violations
    assert machine.memory['MAIN.RESULT'] == expected


def test_device_column_qualification_does_not_block_verified_ordinary_arithmetic():
    from plc.instruction_constraints import inspect_ladder_constraints
    from simulator.bounded import ScanMachine
    ladder = _bounded_ladder((['NO X0'], ['APP ADD D20 D21 D22']))
    reports = inspect_ladder_constraints(ladder, plc_model='FX3U')
    assert reports and all(report['status'] == 'passed' for report in reports)
    machine = ScanMachine(ladder, plc_model='FX3U', initial={'D20': 17, 'D21': 6, 'D22': 0}, execution_context={})
    machine.scan({'inputs': {'X0': True}})
    assert machine.memory['D22'] == 23
    assert not machine.gaps


@pytest.mark.parametrize('high_word,failed', [(1, False), (2, True)])
def test_shared_dynamic_constraints_read_the_verified_double_word_width(high_word, failed):
    from simulator.bounded import ScanMachine
    from plc.instruction_definition import UnknownInstructionSemantics
    machine = ScanMachine({'rungs': []}, plc_model='FX3U',
        initial={'D0': 0, 'D1': high_word, 'D2': 1, 'D3': 0},
        execution_context={'hardware': {'output_type': 'transistor'}})
    if failed:
        with pytest.raises(UnknownInstructionSemantics) as captured:
            machine._check_constraints('DPLSY', ['D0', 'D2', 'Y0'])
        assert captured.value.constraint_report['status'] == 'failed'
        assert any('131072' in f['message'] for f in captured.value.constraint_report['findings'])
    else:
        machine._check_constraints('DPLSY', ['D0', 'D2', 'Y0'])


def test_hexadecimal_constraints_match_signed_instruction_values_in_static_and_dynamic_checks():
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.instruction_constraints import literal_operand_values, check_instruction_constraints
    from simulator.bounded import ScanMachine
    operands = ['HFFFF', 'Y2', 'Y4']
    form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form('PLSV', cpu='FX3U')
    values = literal_operand_values(form.spec, operands)
    assert values['$0'] == -1
    assert check_instruction_constraints('PLSV', values, plc_model='FX3U', language='ladder',
        context={'hardware': {'output_type': 'transistor'}})['status'] == 'passed'
    machine = ScanMachine({'rungs': []}, plc_model='FX3U', initial={},
        execution_context={'hardware': {'output_type': 'transistor'}})
    machine._check_constraints('PLSV', operands)

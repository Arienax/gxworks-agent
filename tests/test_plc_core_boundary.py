
from tools.offline_inputs import private_path
import copy
import json

import pytest


def native_warning_receipt(code, *, owner='MAIN'):
    identity = {'software_version': '1.635.0.1', 'cpu_menu': 'FX3U/FX3UC'}
    return dict(warnings=1, check_warnings=0, identity=identity, revision=7,
        compile_run_id='synthetic-bound-build', source_unchanged=True,
        diagnostics=dict(status='observed', identity=identity, revision=7,
            compile_run_id='synthetic-bound-build', rows=[dict(severity='Warning', code=code,
                data_name=owner, native_row=0)]))


@pytest.mark.parametrize('source,code,expected', [
    ('IF D0 < 0 THEN D10:=1; END_IF;', 'C9215', 'passed'),
    ('VAR u:WORD; END_VAR IF D0 < u THEN D10:=1; END_IF;', 'C9215', 'unverified'),
    ('IF D0 < 65536 THEN D10:=1; END_IF;', 'C9215', 'unverified'),
    ('D10:=1;', 'C9215', 'unverified'),
    ('D10:=1;', 'F0207', 'passed'),
    ('M0:=M8023;', 'F0207', 'unverified'),
    ('M0:=ResetBit;', 'F0207', 'unverified'),
    ('VAR r:REAL; END_VAR r:=2.0 ** 3.0;', 'C9146', 'passed'),
    ('VAR r:REAL; END_VAR r:=0.0 ** 3.0;', 'C9146', 'failed'),
    ('VAR r,b:REAL; END_VAR r:=b ** 3.0;', 'C9146', 'unverified'),
    ('D10:=1;', 'C9999', 'unverified'),
])
def test_native_warning_review_checks_bound_source_meaning(source, code, expected):
    from plc.st_validation import inspect_st_program, review_native_warnings
    context = {'selected_source': 'MAIN.Program.pou', 'pous': [dict(name='MAIN', program='MAIN.Program.pou',
        kind='program', language='st', st_text=source,
        declarations=[dict(name='ResetBit', data_type='BOOL', class_name='VAR', device='M8023')])]}
    inspection = inspect_st_program(source, plc_model='FX3U', project_context=context)
    report = review_native_warnings(source, inspection, native_warning_receipt(code), owner='MAIN', plc_model='FX3U',
        project_context=context)
    assert report['status'] == expected
    assert report['checks'][0]['code'] == code
    if expected == 'passed':
        assert report['checks'][0]['fact_id'] and report['checks'][0]['sources']


@pytest.mark.parametrize('damage', ['row_missing', 'count_unknown', 'stale_revision', 'other_run',
    'other_identity', 'source_changed', 'other_owner', 'other_version', 'syntax'])
def test_warning_review_never_closes_without_current_native_evidence(damage):
    from plc.st_validation import inspect_st_program, review_native_warnings
    receipt = native_warning_receipt('C9215')
    source = 'IF D0 < 0 THEN D10:=1; END_IF;'
    if damage == 'row_missing': receipt['diagnostics']['rows'] = []
    elif damage == 'count_unknown': receipt['warnings'] = None
    elif damage == 'stale_revision': receipt['diagnostics']['revision'] -= 1
    elif damage == 'other_run': receipt['diagnostics']['compile_run_id'] = 'another-build'
    elif damage == 'other_identity': receipt['diagnostics']['identity'] = {'software_version': '1.635.0.1'}
    elif damage == 'source_changed': receipt['source_unchanged'] = False
    elif damage == 'other_owner': receipt['diagnostics']['rows'][0]['data_name'] = 'Other'
    elif damage == 'other_version': receipt['identity']['software_version'] = '1.634.0.1'
    else: source = 'IF D0 < THEN'
    report = review_native_warnings(source, inspect_st_program(source, plc_model='FX3U'), receipt,
        owner='MAIN', plc_model='FX3U')
    assert report['status'] == 'unverified'


@pytest.mark.parametrize('cpu', ['Q00U', 'Q03UDV', 'Q06UDEH', 'L02', 'L06', 'L26'])
@pytest.mark.parametrize('bits,result,expected', [
    (32, 0.0, 'passed'), (32, 2**-127, 'passed'), (32, 2**128, 'failed'),
    (64, 2**128, 'passed'), (64, float('inf'), 'failed'),
])
def test_ql_power_result_constraints_select_actual_precision(cpu, bits, result, expected):
    from plc.instruction_constraints import check_language_constraints
    report = check_language_constraints('ST.OPERATOR.POWER', {'LEFT': 2.0, 'RIGHT': -127.0, 'RESULT': result},
        plc_model=cpu, phase='result', context={'numeric_precision': bits})
    assert report['status'] == expected
    if expected == 'failed':
        assert any(row.get('error_code') == '4141' for row in report['findings'])


@pytest.mark.parametrize('cpu', ['Q00U', 'Q03UDV', 'Q06UDEH', 'L02', 'L06', 'L26'])
@pytest.mark.parametrize('exponent,expected', [(3, 8.0), (-126, 0.0), (-127, 0.0)])
def test_shared_numeric_lowering_matches_independent_native_boundary_results(cpu, exponent, expected):
    from plc.instruction_constraints import evaluate_language_numeric
    from plc.instruction_definition import ValueType
    actual = evaluate_language_numeric('ST.OPERATOR.POWER', 2.0, exponent,
        plc_model=cpu, precision=32, version='1.635.0.1')
    assert ValueType.from_iec('REAL').decode_bits(actual) == expected


@pytest.mark.parametrize('base,result,expected', [
    (-1.0, 1.0, 'failed'), (0.0, 0.0, 'failed'), (2.0, 8.0, 'passed'),
    (2.0, 2**-126, 'passed'), (2.0, 2**-127, 'failed'), (2.0, 2**128, 'failed'),
])
def test_reviewed_power_constraints_are_shared_and_source_scoped(base, result, expected):
    from plc.instruction_constraints import check_language_constraints, check_instruction_constraints
    values = {'IN1': base, 'IN2': 3.0, 'RESULT': result}
    reports = [check_instruction_constraints('EXPT', values, plc_model='FX3U', language='st', phase=phase)
               for phase in ('input', 'result')]
    operator = [check_language_constraints('ST.OPERATOR.POWER', {'LEFT': base, 'RIGHT': 3.0, 'RESULT': result},
                                          plc_model='FX3U', phase=phase) for phase in ('input', 'result')]
    status = lambda rows: 'failed' if any(row['status'] == 'failed' for row in rows) else 'passed'
    assert status(reports) == status(operator) == expected
    if expected == 'failed':
        assert any(row.get('error_code') == 'K6706' and row.get('error_domain') == 'runtime'
                   for report in reports for row in report['findings'])
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    assert DEFAULT_INSTRUCTION_REGISTRY.describe_call('EXPT', cpu='FX3U') == {}


@pytest.mark.parametrize('enabled,expected', [(True, 'failed'), (False, 'passed'), (None, 'unverified')])
def test_expt_enable_condition_is_not_discarded(enabled, expected):
    from plc.instruction_constraints import check_instruction_constraints
    report = check_instruction_constraints('EXPT_E', {'EN': enabled, 'IN1': -1.0}, plc_model='FX3U', language='st')
    assert report['status'] == expected
    if enabled is False:
        assert any(row['status'] == 'not_applicable' for row in report['checks'])
        assert not report['findings']


def test_constraint_selection_keeps_unknown_context_dependency_and_conflict_distinct():
    from plc.instruction_constraints import check_fact_constraints
    fact = {'id': 'test', 'dimension': 'constraints.predicate', 'status': 'source_verified',
            'verification': 'source_checked', 'sources': [{'manual_id': 'synthetic'}],
            'scope': {'models': ['FX3U'], 'forms': ['TEST'], 'languages': ['st'], 'versions': ['1.635.0.1']},
            'value': {'predicate': {'op': 'constant', 'type': {'kind': 'bool'}, 'value': False}}}
    def check(**kwargs):
        return check_fact_constraints([fact], {}, target='TEST', plc_model='FX3U', language='st', **kwargs)
    assert check()['status'] == 'unverified'
    assert check(version='other')['checks'][0]['status'] == 'not_applicable'
    assert check(version='1.635.0.1')['status'] == 'failed'
    fact['depends_on'] = ['missing']
    assert check(version='1.635.0.1')['findings'][0]['message'] == 'constraint_dependency_unresolved'
    fact['status'] = 'conflict'
    assert check(version='1.635.0.1')['findings'][0]['message'] == 'source_conflict'


@pytest.mark.parametrize('value', [
    {'limits': [{'minimum': 0, 'maximum': 15}]},
    {'ranges': []},
    {'ranges': [{'minimum': 0, 'maximum': 15}]},
    {'ranges': [{'operand_index': 0}]},
])
def test_unbound_manual_range_cannot_pass_as_an_empty_executable_constraint(value):
    from plc.instruction_constraints import check_fact_constraints
    fact = {'id': 'range', 'dimension': 'constraints.numeric_ranges', 'value': value,
            'status': 'source_verified', 'verification': 'source_checked',
            'scope': {'models': ['FX3U'], 'forms': ['TEST']},
            'sources': [{'manual_id': 'synthetic'}]}
    result = check_fact_constraints([fact], {'$0': 100}, target='TEST', plc_model='FX3U')
    assert result['status'] == 'unverified'
    assert result['findings'][0]['reason'] == 'numeric_range_operand_binding_missing'


@pytest.mark.parametrize('number,expected', [(0, 'passed'), (15, 'passed'), (16, 'failed')])
def test_bound_manual_range_remains_enforced(number, expected):
    from plc.instruction_constraints import check_fact_constraints
    fact = {'id': 'range', 'dimension': 'constraints.numeric_ranges',
            'value': {'ranges': [{'operand_index': 0, 'minimum': 0, 'maximum': 15}]},
            'status': 'source_verified', 'verification': 'source_checked',
            'scope': {'models': ['FX3U'], 'forms': ['TEST']},
            'sources': [{'manual_id': 'synthetic'}]}
    assert check_fact_constraints([fact], {'$0': number}, target='TEST', plc_model='FX3U')['status'] == expected


@pytest.mark.parametrize('expression,expected', [('2.0 ** 3.0', 'passed'), ('0.0 ** 3.0', 'failed'),
                                               ('(-1.0) ** 2.0', 'failed'), ('B ** 2.0', 'unverified')])
def test_st_operator_inspection_consumes_retrievable_constraints(expression, expected):
    from plc.st_validation import inspect_st_program
    source = f'PROGRAM MAIN VAR R:REAL; B:REAL; END_VAR R := {expression}; END_PROGRAM'
    result = inspect_st_program(source, plc_model='FX3U')
    assert result['syntax'] == 'passed'
    assert result['semantic'] == expected
    assert result['native_compile'] == result['behavior'] == 'unverified'
    if expected == 'failed':
        assert any(f['code'] == 'ST_NUMERIC_DOMAIN' for f in result['findings'])
    assert result['constraint_checks']


@pytest.mark.parametrize('call', ['EXPT(In2:=2, In1:=0.0)', 'EXPT(0.0,2)'])
def test_unadmitted_function_still_checks_reviewed_named_or_positional_constraints(call):
    from plc.st_validation import inspect_st_program
    result = inspect_st_program(f'PROGRAM MAIN VAR R:REAL; END_VAR R:={call}; END_PROGRAM', plc_model='FX3U')
    assert result['semantic'] == 'failed'
    assert any(row['code'] == 'ST_NUMERIC_DOMAIN' for row in result['findings'])
    assert result['calls'][0]['call_available'] is False

from model_runtime.provider import ToolCall
from agent_runtime.plc_tools import build_default_tool_registry, build_tool_context
from plc.core import PLCCore, accept_candidate_patch
from plc.ir import build_plc_ir, canonical_sha256, ir_to_ladder, validate_plc_ir
from application.session import ProjectSession as SessionStore
from agent_runtime.runtime import InProcessToolRuntime


def test_device_numbering_covers_every_supported_native_cpu_and_each_menu_member():
    from gxworks2.project_profiles import NATIVE_PROJECT_PROFILES
    from plc.device_profiles import device_address_rules
    from plc.validation import device_address_status
    for menu in NATIVE_PROJECT_PROFILES:
        if not menu.program_supported:
            continue
        for cpu in dict.fromkeys([menu.cpu, *menu.cpu.split('/')]):
            rules = device_address_rules(cpu)
            assert rules['status'] == 'available', cpu
            assert rules['cpu'] == cpu and rules['sources'], cpu
            assert all(source['target_model'] == cpu and source['pdf_page'] > 0 for source in rules['sources'])
            assert {'X', 'Y', 'M', 'D', 'T', 'C'} <= rules['families'].keys(), cpu
            assert all(device_address_status(address, cpu) == 'valid' for address in ('X0', 'M0', 'D0'))


@pytest.mark.parametrize('cpu,good,bad', [
    ('Q13UDV', ['D41983', 'D4779007', 'M28671', 'X1FFF', 'ST0'], ['D4779008', 'X2000', 'SM2048', 'D0.G']),
    ('Q26UDPV', ['D41983', 'BEFFF'], ['BF000', 'Y2000']),
    ('Q00J', ['S127', 'X7FF'], ['S128', 'X800', 'R0']),
    ('Q02/Q02H', ['S511', 'X1FFF', 'ZR1042431'], ['S512', 'ZR1042432']),
    ('Q00U', ['ZR65535', 'D20000'], ['ZR65536']),
    ('L26-BT/L26-PBT', ['X1FFF', 'D143359', 'D421887', 'ZR393215', 'D0.A'], ['D421888', 'X2000', 'ZR393216', 'Z0.0']),
    ('L02S/L02S-P', ['ZR65535', 'D45055'], ['ZR65536']),
    ('FX3G/FX3GC', ['X177', 'T319', 'R23999'], ['X200', 'X8', 'T320', 'R24000', 'M7680']),
    ('FX3S', ['X17', 'Y15', 'T137', 'C200'], ['X20', 'Y16', 'T138', 'C32', 'D3000', 'R0']),
    ('FX1S', ['D2499', 'C235'], ['D2500', 'C234', 'M512']),
    ('FX0S/FX0', ['C254', 'T55', 'V', 'Z'], ['C255', 'C239', 'T56', 'D32', 'V0', 'Z1']),
])
def test_manual_numbering_boundaries_include_radices_holes_and_extended_regions(cpu, good, bad):
    from plc.validation import device_address_status
    assert [(address, device_address_status(address, cpu)) for address in good] == [(address, 'valid') for address in good]
    assert [(address, device_address_status(address, cpu)) for address in bad] == [(address, 'invalid') for address in bad]


def test_device_numbering_facts_keep_configuration_and_model_conditions():
    from plc.device_profiles import device_address_rules
    q13 = device_address_rules('Q13UDV')
    assert q13['default_allocations']['D'] == 41984
    assert q13['families']['D']['maximum_number'] > 41983
    assert q13['families']['D']['allocation']['extended']['start'] == 'ordinary_D_points'
    assert q13['allocation_constraints']['internal_word_budget_k'] == 60
    assert q13['families']['M']['allocation']['conditional_limits'][1]['serial_first_five_at_least'] == 10042
    assert q13['default_allocations']['ST'] == 0
    assert device_address_rules('FX3UC')['model_conditions'][0]['device_limits']['X'] == 0o357
    assert device_address_rules('L02')['allocation_constraints']['shared_file_capacity_points'] == 65536
    assert device_address_rules('L26-BT')['allocation_constraints']['shared_file_capacity_points'] == 393216


@pytest.mark.parametrize('cpu,address', [('Q13UDV', 'D41983'), ('Q01U', 'X1FFF'), ('L26-BT', 'D143359'), ('FX3G/FX3GC', 'T319')])
def test_runtime_address_lookup_and_generation_use_the_shared_cpu_rules(cpu, address):
    from knowledge.structured_facts import resolve_device_records, resolve_device_family_records, retrieval_coverage
    from plc.device_profiles import device_address_rules
    from plc.validation import DEVICE_ADDRESS_RE
    from application.fbd_context import compose_fbd_context
    rows = resolve_device_records([address], plc_model=cpu)
    exact = [row for row in rows if row.get('device_address_rule')]
    assert len(exact) == 1
    fact = exact[0]['device_address_rule']
    assert fact['cpu'] == cpu and fact['numbering_status'] == 'valid'
    prefix = DEVICE_ADDRESS_RE.fullmatch(address)[1]
    expected = device_address_rules(cpu)
    assert fact['maximum_number'] == expected['families'][prefix]['maximum_number']
    families = resolve_device_family_records([prefix], plc_model=cpu)
    from knowledge.structured_facts import authored_packet
    assert authored_packet(families[0])['value']['cpu'] == cpu
    report = retrieval_coverage(address + ' 地址编号范围', rows, plc_model=cpu)
    assert report['status'] == 'complete', report
    from knowledge.retriever import build_knowledge_context
    knowledge = build_knowledge_context(address + ' 地址编号范围', plc_model=cpu, task_type='analysis')
    assert address in knowledge and 'maximum_number' in knowledge
    assert '"cpu":"' + cpu + '"' in knowledge
    assert knowledge.manifest['retrieval_coverage']['status'] == 'complete'
    payload = compose_fbd_context(request='Use ' + address, plc_model=cpu,
                                  catalog={'program': 'MAIN', 'nodes': []}).payload
    assert payload['device_address_rules']['cpu'] == cpu
    assert payload['device_address_rules']['families'][prefix]['maximum_number'] == fact['maximum_number']


@pytest.mark.parametrize("address,valid", [
    ("M15359", True), ("M15360", True),
    ("D22527", True), ("D22528", True),
    ("BBF", True), ("B1FFF", True), ("B2000", True),
    ("W1FFF", True), ("W2000", True),
    ("SM400", True), ("SM2048", False), ("SD2048", False),
])
def test_q06udv_address_subset_uses_its_own_manual_rows(address, valid):
    from plc.validation import parse_device_address
    assert (parse_device_address(address, "Q06UDV") is not None) is valid


@pytest.mark.parametrize("cpu", ["Q00UJ", "Q00U"])
@pytest.mark.parametrize("address,valid", [
    ("M8191", True), ("M8192", True), ("D12287", True), ("D12288", True),
    ("B1FFF", True), ("B2000", True), ("W1FFF", True), ("W2000", True),
    ("SM2047", True), ("SM2048", False), ("SD2047", True), ("SD2048", False),
])
def test_small_q_numbering_does_not_treat_default_allocation_as_the_maximum(cpu, address, valid):
    from plc.validation import parse_device_address
    assert (parse_device_address(address, cpu) is not None) is valid


@pytest.mark.parametrize("cpu", ["Q00UJ", "Q00U"])
def test_small_q_device_profile_keeps_model_and_access_boundaries(cpu):
    from plc.validation import SOURCE_DEVICE_PROFILES, parse_device_address
    from plc.device_policy import native_read_plan, native_write_plan, native_feedback_plan, normalize_read_addresses, DevicePolicyError, HardwareError
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    assert all(s['target_model'] == cpu for s in SOURCE_DEVICE_PROFILES[cpu]['sources'])
    assert parse_device_address('D12602', cpu) == ('D', 12602)
    with pytest.raises(DevicePolicyError):
        native_read_plan(['D12602'], cpu)
    assert parse_device_address('D12602', 'Q06UDV') is not None
    assert native_read_plan(['SM400', 'SD0', 'BBF', 'W21'], cpu) == [
        {'key': a, 'device': a} for a in ['SM400', 'SD0', 'BBF', 'W21']]
    assert native_feedback_plan({'B03F': 1, 'W2': 39}, cpu, allowed_addresses=['B3F', 'W2']) == [
        {'key': 'B03F', 'device': 'B03F', 'value': 1}, {'key': 'W2', 'device': 'W2', 'value': 39}]
    for address in ('SM400', 'SD0', 'B3F', 'W2'):
        with pytest.raises(DevicePolicyError):
            native_write_plan({address: 1}, cpu)
    with pytest.raises(DevicePolicyError):
        native_feedback_plan({'W3': 0}, cpu, allowed_addresses=['W2'])
    with pytest.raises(HardwareError):
        normalize_read_addresses(['D0'], cpu)
    contract = DEFAULT_INSTRUCTION_REGISTRY.describe_contract('MOV', cpu=cpu)
    assert not any(source.get('target_model') == cpu for source in contract['sources'])


@pytest.mark.parametrize("cpu", ["Q00UJ", "Q00U"])
def test_small_q_device_plan_preserves_values_and_rejects_the_next_default_address(cpu):
    from hypothesis import given, settings, strategies as st
    from plc.device_policy import native_write_plan, native_read_plan, DevicePolicyError
    from plc.validation import parse_device_address

    @settings(max_examples=36, derandomize=True, database=None)
    @given(st.one_of(
        st.tuples(st.just('M'), st.integers(0, 8191), st.integers(0, 1)),
        st.tuples(st.just('D'), st.integers(0, 12287), st.integers(-32768, 65535)),
    ))
    def check(case):
        prefix, index, value = case
        address = prefix + str(index)
        plan, = native_write_plan({address: value}, cpu)
        assert plan['value'] == value and parse_device_address(plan['device'], cpu) == (prefix, index)
        assert native_read_plan([plan['device']], cpu) == [{'key': address, 'device': address}]
        with pytest.raises(DevicePolicyError):
            native_read_plan([prefix + str(8192 if prefix == 'M' else 12288)], cpu)

    check()


def test_l02_native_device_preparation_retains_its_model_and_access_boundaries():
    from plc.validation import SOURCE_DEVICE_PROFILES, parse_device_address
    from plc.device_policy import native_read_plan, native_write_plan, native_feedback_plan, DevicePolicyError

    assert all(source['target_model'] == 'L02' for source in SOURCE_DEVICE_PROFILES['L02']['sources'])
    assert any(source.get('manual_id') == 'sh080889engz'
               for source in SOURCE_DEVICE_PROFILES['L02']['sources'])
    addresses = ['M8191', 'D12287', 'B1FFF', 'W1FFF', 'SM2047', 'SD2047']
    assert native_read_plan(addresses, 'L02') == [{'key': a, 'device': a} for a in addresses]
    # Retain the device classes that already used the default native policy.
    assert native_read_plan(['X1F', 'Y20', 'S8191', 'T2047', 'C1023'], 'L02') == [
        {'key': a, 'device': n} for a, n in
        [('X1F', 'X1F'), ('Y20', 'Y20'), ('S8191', 'S8191'), ('T2047', 'TN2047'), ('C1023', 'CN1023')]]
    for address in ['M8192', 'D12288', 'B2000', 'W2000', 'SM2048', 'SD2048']:
        assert (parse_device_address(address, 'L02') is None) == address.startswith(('SM', 'SD'))
        with pytest.raises(DevicePolicyError):
            native_read_plan([address], 'L02')
    for address in ['SM400', 'SD0', 'B3F', 'W2']:
        with pytest.raises(DevicePolicyError):
            native_write_plan({address: 1}, 'L02')
    with pytest.raises(DevicePolicyError):
        native_feedback_plan({'SM400': 1}, 'L02', allowed_addresses=['SM400'])
    with pytest.raises(DevicePolicyError):
        native_read_plan(['W2.0'], 'L02')
    assert SOURCE_DEVICE_PROFILES['L26-BT']['native_read_prefixes'] == []


@pytest.mark.parametrize('cpu,maximum_m,maximum_d,special', [
    ('FX3S', 1535, 2999, True), ('FX3G', 7679, 7999, True),
    ('FX3GC', 7679, 7999, True), ('FX3G/FX3GC', 7679, 7999, True),
    ('FX3UC', 7679, 7999, True), ('FX3U/FX3UC', 7679, 7999, True),
    ('L02-P', 8191, 12287, False), ('L02/L02-P', 8191, 12287, False),
    ('L06', 8191, 12287, False), ('L06-P', 8191, 12287, False), ('L06/L06-P', 8191, 12287, False),
    ('L26', 8191, 12287, False), ('L26-P', 8191, 12287, False), ('L26/L26-P', 8191, 12287, False),
    ('Q06UDEH', 8191, 12287, False),
])
def test_sampled_native_m_d_profiles_follow_their_own_source_rows(cpu, maximum_m, maximum_d, special):
    from hypothesis import given, settings, strategies as st
    from plc.validation import SOURCE_DEVICE_PROFILES, normalize_plc_model, parse_device_address
    from plc.device_policy import native_read_plan, native_write_plan, DevicePolicyError
    from plc.target_support import TargetSupportRegistry
    profile = SOURCE_DEVICE_PROFILES[cpu]
    assert normalize_plc_model(cpu) == cpu
    assert all(source['target_model'] == cpu and source['pdf_page'] > 0 and source['revision']
               for source in profile['sources'])
    assert set(profile['device_limits']) >= {'X', 'Y', 'M', 'D', 'T', 'C'}
    assert parse_device_address('X0', cpu) == ('X', 0)
    for prefix, maximum in [('M', maximum_m), ('D', maximum_d)]:
        assert parse_device_address(prefix + str(maximum), cpu) == (prefix, maximum)
        # FX D7999 is followed by the separate CPU-owned D8000 namespace;
        # it is readable as an address but never a user-writable continuation.
        next_expected = (prefix, maximum + 1) if not special or maximum == 7999 else None
        assert parse_device_address(prefix + str(maximum + 1), cpu) == next_expected
    @settings(max_examples=45, derandomize=True, database=None)
    @given(st.sampled_from(['M', 'D']), st.integers(0, max(8512, maximum_d + 1)))
    def addresses(prefix, number):
        maximum = maximum_m if prefix == 'M' else maximum_d
        expected = not special or number <= maximum or 8000 <= number <= 8511
        assert parse_device_address(prefix + str(number), cpu) == ((prefix, number) if expected else None)
    addresses()
    assert native_read_plan(['M52', 'D0'], cpu) == [{'key': value, 'device': value} for value in ['M52', 'D0']]
    assert native_write_plan({'M52': 1, 'D0': -7}, cpu) == [
        dict(key='M52', device='M52', value=1), dict(key='D0', device='D0', value=-7)]
    if special:
        for address in ('M8000', 'D8000'):
            with pytest.raises(DevicePolicyError): native_write_plan({address: 0}, cpu)
    # Source facts alone do not grant a native operation. Completed native
    # audits are separately registered in the product's default provider.
    assert all(row['status'] == 'unverified' for row in TargetSupportRegistry().live_capabilities(
        cpu, software_version='1.635.0.1', native_ui_language='en').values())


def test_sampled_source_subsets_preserve_existing_fuller_profiles_and_other_model_rejections():
    from plc.validation import DEVICE_LIMITS, SOURCE_DEVICE_PROFILES, parse_device_address, normalize_plc_model, ValidationProfileUnavailableError
    assert set(DEVICE_LIMITS['FX3U']) >= {'X', 'Y', 'M', 'D', 'T', 'C', 'S', 'V', 'Z'}
    assert parse_device_address('X367', 'FX3U') == ('X', 0o367)
    assert parse_device_address('M2000', 'FX3S') is None
    assert parse_device_address('M2000', 'FX3G/FX3GC') == ('M', 2000)
    assert parse_device_address('M9000', 'Q06UDEH') == ('M', 9000)
    assert parse_device_address('M9000', 'Q03UDV') == ('M', 9000)
    assert set(SOURCE_DEVICE_PROFILES['L02']['device_limits']) > {'M', 'D'}
    for cpu in ('FX1S', 'Q06UDH', 'L26-BT'):
        assert normalize_plc_model(cpu) == cpu


def test_fx3u_and_fx3uc_io_numbering_matches_the_octal_manual_ceiling():
    from hypothesis import given, settings, strategies as st
    from plc.validation import device_address_status, is_bit_device, parse_device_address

    @settings(max_examples=60, derandomize=True, database=None)
    @given(cpu=st.sampled_from(['FX3U', 'FX3UC', 'FX3U/FX3UC']),
           prefix=st.sampled_from(['X', 'Y']), number=st.integers(0, 0o777))
    def check(cpu, prefix, number):
        address = prefix + format(number, 'o')
        expected = number <= 0o367
        assert parse_device_address(address, cpu) == ((prefix, number) if expected else None)
        assert device_address_status(address, cpu) == ('valid' if expected else 'invalid')
        assert is_bit_device(address, cpu) is expected
        for digit in '89':
            assert device_address_status(address + digit, cpu) == 'invalid'
    check()


@pytest.mark.parametrize('cpu,address,status', [
    ('FX3U/FX3UC', 'X0', 'valid'), ('FX3UC', 'Y367', 'valid'),
    ('FX3U/FX3UC', 'X8', 'invalid'), ('FX3UC', 'Y400', 'invalid'),
    ('FX3UC', 'M7680', 'invalid'), ('FX3UC', 'T0', 'valid'),
    ('FX3S', 'X0', 'valid'), ('Q01U', 'X0', 'valid'),
    ('FX3U', 'T0', 'valid'), ('FX3UC', 'D-1', 'invalid'),
])
def test_shared_device_status_keeps_invalid_and_uncovered_addresses_apart(cpu, address, status):
    from plc.validation import device_address_status
    assert device_address_status(address, cpu) == status


@pytest.mark.parametrize('cpu', ['FX3U', 'FX3UC', 'FX3U/FX3UC'])
@pytest.mark.parametrize('source,status,code', [
    ('Y0 := X0;', 'passed', None),
    ('IF X0 THEN Y0 := TRUE; END_IF;', 'passed', None),
    ('Y0 := X8;', 'failed', 'ST_OPERAND'),
    ('Y400 := TRUE;', 'failed', 'ST_OPERAND'),
    ('X0 := TRUE;', 'failed', 'ST_OPERAND'),
])
def test_st_io_uses_the_selected_cpu_identity_radix_type_and_write_policy(cpu, source, status, code):
    from plc.st_validation import inspect_st_program
    result = inspect_st_program(source, plc_model=cpu)
    assert result['plc_model'] == cpu and result['status'] == status
    assert code is None or any(row['code'] == code for row in result['findings'])
    assert result['native_compile'] == result['runtime'] == 'unverified'


@pytest.mark.parametrize('source,status', [
    ('Y0 := T0;', 'unverified'), ('IF T0 THEN Y0 := TRUE; END_IF;', 'unverified'),
    ('MOV(TRUE,K1,R0);', 'unverified'), ('MOV(TRUE,X8,R0);', 'failed'),
])
def test_partial_st_device_coverage_does_not_invent_an_address_error_or_word_type(source, status):
    from plc.st_validation import inspect_st_program
    result = inspect_st_program(source, plc_model='FX3U/FX3UC')
    assert result['status'] == status
    assert any(row['code'] == 'ST_DEVICE_COVERAGE' for row in result['findings'])
    assert not any(row['code'] in {'ST_TYPE', 'ST_WRITE_TARGET'} for row in result['findings'])
    assert any(row['code'] == 'ST_OPERAND' for row in result['findings']) is (status == 'failed')


@pytest.mark.parametrize('cpu', ['FX3UC', 'FX3U/FX3UC'])
def test_reviewed_io_numbering_does_not_authorize_native_io_access(cpu):
    from plc.device_policy import DevicePolicyError, native_read_plan, native_write_plan
    from plc.validation import device_address_status
    assert device_address_status('X0', cpu) == device_address_status('Y0', cpu) == 'valid'
    with pytest.raises(DevicePolicyError):
        native_read_plan(['X0'], cpu)
    with pytest.raises(DevicePolicyError):
        native_write_plan({'Y0': 1}, cpu)


@pytest.mark.parametrize('cpu', ['FX3S', 'FX3G', 'FX3G/FX3GC'])
@pytest.mark.parametrize('source', ['X0 := TRUE;', 'MOV(TRUE,K1,X0);'])
def test_partial_address_coverage_does_not_weaken_the_input_write_rejection(cpu, source):
    from plc.st_validation import inspect_st_program
    result = inspect_st_program(source, plc_model=cpu)
    assert result['status'] == 'failed'
    assert any(row['code'] == 'ST_OPERAND' and 'input device X0' in row['message']
               for row in result['findings'])


def test_l02_native_feedback_uses_hex_identity_and_only_the_bound_input_members():
    from hypothesis import example, given, settings, strategies as st
    from plc.device_policy import native_feedback_plan, DevicePolicyError
    from plc.validation import parse_device_address

    @settings(max_examples=36, derandomize=True, database=None)
    @example(index=0, bit=0, word=-32768)
    @example(index=8191, bit=1, word=65535)
    @given(index=st.integers(0, 8191), bit=st.integers(0, 1), word=st.integers(-32768, 65535))
    def check(index, bit, word):
        b, w = 'B' + format(index, 'X'), 'W' + format(index, 'X')
        values = {b: bit, w: word}
        plan = native_feedback_plan(values, 'L02', allowed_addresses=[
            'B' + format(index, '04X'), 'W' + format(index, '04X')])
        assert [(parse_device_address(row['device'], 'L02'), row['value']) for row in plan] == [
            (('B', index), bit), (('W', index), word)]
        assert {row['key']: row['value'] for row in plan} == values
        with pytest.raises(DevicePolicyError, match='outside the bound project input members'):
            native_feedback_plan({'W' + format((index + 1) % 8192, 'X'): word},
                                 'L02', allowed_addresses=[w])
        with pytest.raises(DevicePolicyError):
            native_feedback_plan({b: 2}, 'L02', allowed_addresses=[b])

    check()


def test_q06udv_address_profile_does_not_promote_other_cpus_or_instruction_facts():
    from plc.validation import normalize_plc_model, parse_device_address, ValidationProfileUnavailableError
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.device_profiles import device_address_rules
    assert parse_device_address("M15359", "Q03UDV") == ('M', 15359)
    assert parse_device_address("D22527", "Q03UDV") == ('D', 22527)
    assert device_address_rules('Q03UDV')['default_allocations']['D'] == 13312
    assert device_address_rules('Q06UDV')['default_allocations']['D'] == 22528
    assert normalize_plc_model("Q06UDH") == "Q06UDH"
    contract = DEFAULT_INSTRUCTION_REGISTRY.describe_contract("MOV", cpu="Q06UDV")
    assert not any(source.get("target_model") == "Q06UDV" for source in contract["sources"])


def test_q06udv_native_read_plan_preserves_hex_addresses_and_blocks_word_bit_views():
    from plc.device_policy import native_read_plan, DevicePolicyError
    addresses = ["BBF", "BFF", "W21", "SM400", "D12602", "M8866"]
    assert native_read_plan(addresses, "Q06UDV") == [
        {"key": address, "device": address} for address in addresses]
    with pytest.raises(DevicePolicyError):
        native_read_plan(["W21.0"], "Q06UDV")
    with pytest.raises(DevicePolicyError):
        native_read_plan(["BBF"], "FX3U")


@pytest.mark.parametrize("address", ["M8866", "D12602"])
def test_q06udv_high_user_devices_are_not_fx_special_devices(address):
    from plc.device_policy import native_write_plan, native_reset_plan
    assert native_write_plan({address: 1}, "Q06UDV") == [
        {"key": address, "device": address, "value": 1}]
    assert native_reset_plan([address], {}, "Q06UDV")["clear"][0]["device"] == address


@pytest.mark.parametrize("address", ["SM400", "SD0", "Y0", "B0", "W0"])
def test_q06udv_read_support_does_not_authorize_output_or_system_stimulus(address):
    from plc.device_policy import DevicePolicyError, native_write_plan
    with pytest.raises(DevicePolicyError):
        native_write_plan({address: 1}, "Q06UDV")


def test_q06udv_source_subset_does_not_expand_physical_plc_access():
    from plc.device_policy import normalize_read_addresses, HardwareError
    with pytest.raises(HardwareError):
        normalize_read_addresses(["D0"], "Q06UDV")


def test_q06udv_feedback_plan_is_bounded_to_current_input_members():
    from plc.device_policy import native_feedback_plan, DevicePolicyError
    assert native_feedback_plan({"B03F":1,"W2":0x27}, "Q06UDV",
                                allowed_addresses=["B3F","W2"]) == [
        {"key":"B03F","device":"B03F","value":1}, {"key":"W2","device":"W2","value":0x27}]
    with pytest.raises(DevicePolicyError, match="outside the bound"):
        native_feedback_plan({"W41":15}, "Q06UDV", allowed_addresses=["B3F","W2"])


@pytest.mark.parametrize("cpu,address,value,allowed", [
    ("Q03UDV","B3F",1,["B3F"]), ("FX3U","M0",1,["M0"]),
    ("Q06UDV","SM400",1,["SM400"]), ("Q06UDV","D0",1,["D0"]),
    ("Q06UDV","W2.0",1,["W2.0"]), ("Q06UDV","B3F",2,["B3F"]),
    ("Q06UDV","W2",65536,["W2"]), ("Q06UDV","W2",1.5,["W2"]),
    ("Q06UDV","W2",0,[]),
])
def test_q06udv_feedback_does_not_admit_other_models_classes_widths_or_empty_scope(cpu,address,value,allowed):
    from plc.device_policy import native_feedback_plan, DevicePolicyError
    with pytest.raises(DevicePolicyError):
        native_feedback_plan({address:value}, cpu, allowed_addresses=allowed)


def test_native_q06udv_user_device_policy_across_the_high_address_range():
    from hypothesis import given, settings, strategies as st
    from plc.device_policy import native_write_plan

    @settings(max_examples=30, derandomize=True, database=None)
    @given(st.one_of(
        st.tuples(st.just("M"), st.integers(min_value=8000, max_value=15359), st.integers(0, 1)),
        st.tuples(st.just("D"), st.integers(min_value=8000, max_value=22527), st.integers(-32768, 65535)),
    ))
    def check(case):
        prefix, number, value = case
        address = f"{prefix}{number}"
        # The manual identifies M/D as user devices; the CPU system namespace is SM/SD.
        plan = native_write_plan({address: value}, "Q06UDV")
        assert plan == [{"key": address, "device": address, "value": value}]

    check()


@pytest.mark.parametrize('cpu,address,context,status,access', [
    ('FX1S','M8028',{},'source_verified','user'),
    ('FX1N','M8028',{},'not_applicable','unknown'),
    ('FX2N','M8028',{},'conditions_missing','user'),
    ('FX2N','M8028',{'cpu_version':'2.99'},'not_applicable','user'),
    ('FX2N','M8028',{'cpu_version':'3.00'},'source_verified','user'),
    ('FX1S','D8021',{'input_voltage':'24V DC'},'source_verified','user'),
    ('FX1S','D8021',{'input_voltage':'100V AC'},'not_applicable','user'),
    ('FX1N','D8021',{},'not_applicable','unknown'),
    ('Q03UD','SD1282.0',{},'not_applicable','unknown'),
    ('Q03UDE','SD1282.0',{},'source_verified','system'),
    ('L02S','SD1282.0',{},'not_applicable','unknown'),
    ('L02','SD1282.0',{},'source_verified','system'),
    ('Q03UDE','SM605',{},'conditions_missing','user'),
    ('Q03UDE','SM605',{'serial_first5':10102},'source_verified','user'),
    ('Q03UDE','SM605',{'serial_first5':10101},'not_applicable','user'),
    ('Q03UDV','SM605',{},'source_verified','user_system'),
    ('L02','SM605',{},'source_verified','system'),
    ('Q03UDV','SM9999',{},'unknown',None),
])
def test_special_device_definition_is_specific_to_cpu_and_conditions(cpu,address,context,status,access):
    from plc.device_definitions import resolve_device_definition
    result = resolve_device_definition(address,cpu,context=context)
    assert result['status'] == status
    assert {row['access'] for row in result['definitions']} == ({access} if access else set())
    for row in result['definitions']:
        assert all(s['pdf_pages'] and s['revision'] and s['official_url'] for s in row['sources'])


@pytest.mark.parametrize('cpu,address,expected', [
    ('FX1N','M8028','DEVICE_NOT_APPLICABLE'),
    ('L02','SM605','DEVICE_SYSTEM_WRITE'),
    ('Q03UD','SM605','DEVICE_CONDITIONS_MISSING'),
])
def test_ladder_and_st_share_source_diagnostics_even_without_cpu_range_profile(cpu,address,expected):
    from plc.device_definitions import ladder_device_findings
    from plc.st_validation import inspect_st_program
    ladder = ir_to_ladder(_program())
    ladder['rungs'][0]['branches'][0]['outputs'] = [{'type':'COIL','address':address}]
    ld = ladder_device_findings(ladder,cpu)
    st = inspect_st_program(f'{address} := M0;',plc_model=cpu)
    assert expected in {f['code'] for f in ld}
    assert expected in {f['code'] for f in st['findings']}
    assert not st['valid']


def test_source_known_error_is_checked_before_missing_cpu_profile():
    from plc.validation import validate_ladder_full
    ladder = ir_to_ladder(_program())
    ladder['rungs'][0]['branches'][0]['outputs'] = [{'type':'COIL','address':'SM1500'}]
    with pytest.raises(ValueError, match='DEVICE_NOT_APPLICABLE'):
        validate_ladder_full(ladder,plc_model='Q03UD')


@pytest.mark.parametrize('cpu', ['FX1N/FX1NC','FX2C','FX3U'])
@pytest.mark.parametrize('output,reason', [
    ({'type':'APP_INSTR','opcode':'MOV','operands':['SD?','D100']}, 'invalid operand token'),
    ({'type':'APP_INSTR','opcode':'MOV','operands':['D0']}, 'operands'),
    ({'type':'COIL','address':'M?'}, 'invalid device address'),
    ({'type':'MADE_UP','address':'M0'}, 'unknown type'),
])
def test_universal_ladder_errors_are_not_hidden_by_cpu_profile(cpu,output,reason):
    from application.compact_protocol import expand_compact_ladder
    from plc.validation import validate_ladder_full, ValidationProfileUnavailableError
    ladder = expand_compact_ladder({'r':[{'h':None,'s':[],'b':[{'i':['NO M0'],'o':['MOV D0 D100']}]}]})
    ladder['rungs'][0]['branches'][0]['outputs'] = [output]
    if reason == 'operands' and cpu != 'FX3U':
        # These CPUs have no source-reviewed MOV signature yet. A signature
        # from another CPU must not be imported just to make this test pass.
        with pytest.raises(ValidationProfileUnavailableError):
            validate_ladder_full(ladder,plc_model=cpu)
        return
    with pytest.raises(ValueError,match=reason) as caught:
        validate_ladder_full(ladder,plc_model=cpu)
    assert not isinstance(caught.value,ValidationProfileUnavailableError)


def test_correct_syntax_for_a_removed_profile_stays_unverified(monkeypatch):
    import plc.validation as validation
    import plc.st_semantics as st_semantics
    import plc.st_validation as st_validation
    available = validation.SUPPORTED_PLC_MODELS - {'FX2C'}
    for module in (validation, st_semantics, st_validation):
        monkeypatch.setattr(module, 'SUPPORTED_PLC_MODELS', available)
    from application.compact_protocol import expand_compact_ladder
    from plc.validation import validate_ladder_full, ValidationProfileUnavailableError
    from plc.st_validation import inspect_st_program
    ladder = expand_compact_ladder({'r':[{'h':None,'s':[],'b':[{'i':['NO M0'],'o':['MOV D8001 D100']}]}]})
    with pytest.raises(ValidationProfileUnavailableError):
        validate_ladder_full(ladder,plc_model='FX2C')
    assert inspect_st_program('IF M0 THEN D100 := D8001; END_IF;',plc_model='FX2C')['status']=='unverified'
    assert inspect_st_program('IF M0 THEN D100 := SD?; END_IF;',plc_model='FX2C')['status']=='failed'


@pytest.mark.parametrize('name', ['validate_ladder_full','validate_ladder_candidate_structure','validate_ladder_partial','validate_ladder_partial_structure'])
def test_all_ladder_validation_entrypoints_reject_placeholder_before_profile(name):
    import plc.validation as validation
    from application.compact_protocol import expand_compact_ladder
    ladder=expand_compact_ladder({'r':[{'h':None,'s':[],'b':[{'i':['NO M0'],'o':['MOV SD? D100']}]}]})
    if 'partial' in name:
        ladder['mode']='partial'
    with pytest.raises(ValueError,match='invalid operand token'):
        getattr(validation,name)(ladder,plc_model='FX1N/FX1NC')


def test_missing_hardware_condition_is_visible_in_ladder_project_validation():
    ladder = ir_to_ladder(_program())
    ladder['rungs'][0]['branches'][0]['outputs'] = [
        {'type':'APP_INSTR','opcode':'MOV','operands':['K5','D8020']}]
    program = build_plc_ir(ladder,plc_model='FX1N')
    report = PLCCore().validate_project(program)
    assert report['status'] == 'unverified' and report['valid'] is False
    assert any(f['code']=='DEVICE_CONDITIONS_MISSING' for f in report['findings'])
    verified = PLCCore().validate_project(program,{'hardware_profile':{'input_voltage':'24V DC'}})
    assert verified['valid'] is True
    context = build_tool_context({'id':'condition-check','plc_model':'FX1N'},
        version={'id':'v1'},program_ir=program,ladder=ladder)
    for tool in ('validate_current_program','validate_project','get_diagnostics'):
        receipt = build_default_tool_registry().call(tool,{},context)
        assert receipt['ok'], receipt
        assert receipt['data']['valid'] is False
        assert receipt['data']['status'] == 'unverified'


@pytest.mark.parametrize("cpu,source", [
    ("FX1N", 'MOV(M8002,K2000,D8018); M0 := LDP(TRUE,X0); TWR(M0,D0); M8017 := X1;'),
    ("Q00J", 'DUTY(X0,1,3,SM420);'),
    ("Q03UDV", 'IF M5000 AND SD1282.0 AND SD1286.0 THEN S_SOCRCVS(TRUE,"U0",1,D5000); END_IF;'),
    ("L02", 'ICCNTRD1(M0); DMOV(M0,SD1880,D0);'),
])
def test_st_shared_tools_check_the_bound_source_without_claiming_native_compile(cpu, source):
    context = build_tool_context({'id': 'isolated', 'plc_model': cpu, 'target_mode': 'st'},
                                 version={'id': 'v1', 'plc_model': cpu}, st_text=source)
    registry = build_default_tool_registry()
    for name in ('validate_current_program', 'validate_project', 'get_diagnostics'):
        result = registry.call(name, {}, context)
        assert result['ok'], result
        report = result['data']
        assert report['valid'] is True and report['status'] == 'passed'
        assert report['plc_model'] == cpu and report['version_id'] == 'v1'
        assert report['native'] == report['runtime'] == report['requirements'] == 'unverified'
    result = registry.call('compile_project', {}, context)
    assert result['ok'] and result['data']['compiled'] is False
    assert result['data']['status'] == 'unverified'


@pytest.mark.parametrize("cpu,source,status,code", [
    ('FX1N', '(* TODO: code *)', 'failed', 'ST_SYNTAX'),
    ('FX1N', 'LD X0\nOUT Y0\nEND', 'failed', 'ST_SYNTAX'),
    ('FX1N', 'M8002 := X0;', 'failed', 'ST_OPERAND'),
    ('Q03UDV', 'SD1282.0 := M0;', 'failed', 'ST_OPERAND'),
    ('Q03UDV', 'S.SOCRCVS(TRUE,"U0",1,D5000);', 'failed', 'ST_CALL_FORM'),
    ('L02', 'ICCNTRD1(M0,1);', 'failed', 'ST_CALL_ARITY'),
    ('Q00J', 'DUTY(X0,1,3,SM419);', 'failed', 'ST_OPERAND'),
    ('FX1N', 'MOV(TRUE,K1,M0);', 'failed', 'ST_OPERAND'),
    ('FX1N', 'PLACEHOLDER(X0);', 'unverified', 'ST_CALL_COVERAGE'),
    ('L02', 'FOR i := 0 TO 1 DO D0 := 1; END_FOR;', 'unverified', 'ST_LABEL_COVERAGE'),
    ('FX1N', 'out_label := X0;', 'unverified', 'ST_LABEL_COVERAGE'),
    ('L02', 'values[0] := 1;', 'unverified', 'ST_LABEL_COVERAGE'),
    ('Q01U', 'Y0 := X0;', 'unverified', 'ST_DEVICE_COVERAGE'),
    ('L02', 'IF M0 THEN DMOV(TRUE,SD1880,D0);', 'failed', 'ST_SYNTAX'),
])
def test_st_diagnostics_separate_known_errors_from_missing_coverage(cpu, source, status, code):
    from plc.st_validation import inspect_st_program
    report = inspect_st_program(source, plc_model=cpu)
    assert report['status'] == status and report['valid'] is False
    assert code in {item['code'] for item in report['findings']}


@pytest.mark.parametrize("cpu,source", [
    ('Q00J', 'DUTY(X0,3,1,SM420);'),
    ('Q03UDV', 'IF M5000 AND SD1282.0 AND NOT SD1286.0 THEN S_SOCRCVS(TRUE,"U0",1,D5000); END_IF;'),
    ('L02', 'ICCNTRD1(M0); DMOV(M0,SD1900,D0);'),
])
def test_valid_st_mutations_are_not_claimed_as_requirement_or_runtime_failures(cpu, source):
    from plc.st_validation import inspect_st_program
    report = inspect_st_program(source, plc_model=cpu)
    assert report['status'] == 'passed'
    assert report['requirements'] == report['runtime'] == 'unverified'


@pytest.mark.parametrize("cpu,project_type,modes", [
    ("FX3U/FX3UC", "simple", ["ladder"]),
    ("FX0S/FX0", "structured", ["st", "fbd"]),
    ("Q02/Q02H", "structured", ["ladder", "st", "fbd"]),
    ("L26-BT/L26-PBT", "simple", ["ladder", "st"]),
    ("LJ72GF15-T2", "parameter", []),
])
def test_native_project_creation_languages_follow_core_catalogue(cpu, project_type, modes):
    from plc.project_capabilities import project_creation_options, normalize_project_selection
    options = project_creation_options()
    row = next(row for row in options["native_targets"] if row["menu_cpu"] == cpu)
    selected = next(option for option in row["project_types"] if option["value"] == project_type)
    assert [item["value"] for item in selected["target_modes"]] == modes
    for mode in modes or ["ladder"]:
        selection = normalize_project_selection(cpu, mode, project_type)
        assert selection["plc_model"] == cpu
        assert selection["use_labels"] == (project_type == "structured" or mode == "st")
    assert sum(row["program_supported"] for row in options["native_targets"]) == 57


@pytest.mark.parametrize("cpu,mode,project_type,labels", [
    ("FX3U", "st", "simple", None),
    ("FX3U", "ladder", "structured", True),
    ("Q03UDV", "fbd", "simple", True),
    ("L02", "st", "structured", False),
    ("LJ72MS15", "fbd", "parameter", None),
    ("LJ72MS15", "ladder", "simple", False),
    ("FX5U", "ladder", None, None),
    ("FX1", "st", "structured", True),
    ("FX1", "fbd", "structured", True),
    ("FX0", "ladder", "simple", True),
    ("FX0S/FX0", "ladder", "simple", True),
    ("FX0N", "ladder", "simple", True),
    ("FXU", "ladder", "simple", True),
    ("FX2C", "ladder", "simple", True),
])
def test_invalid_native_project_combination_is_rejected(cpu, mode, project_type, labels):
    from plc.project_capabilities import normalize_project_selection
    with pytest.raises(ValueError):
        normalize_project_selection(cpu, mode, project_type, labels)


@pytest.mark.parametrize('profile', __import__('gxworks2.project_profiles', fromlist=['NATIVE_PROJECT_PROFILES']).NATIVE_PROJECT_PROFILES,
                         ids=lambda profile: profile.cpu)
def test_every_native_menu_target_has_the_official_language_matrix(profile):
    from plc.project_capabilities import project_creation_options, normalize_project_selection
    target = next(row for row in project_creation_options()['native_targets'] if row['menu_cpu'] == profile.cpu)
    # Expected families and restrictions are frozen in the native catalogue and
    # official manuals, not derived from the new factory's language list.
    expected = ({'parameter': []} if not profile.program_supported else
                {'simple':['ladder']} if profile.cpu == 'FX1' else
                {'simple':['ladder'], 'structured':['st','fbd']} if profile.series == ('FXCPU',) else
                {'simple':['ladder','st'], 'structured':['ladder','st','fbd']})
    actual = {row['value']: [m['value'] for m in row['target_modes']] for row in target['project_types']}
    assert actual == expected
    for option in target['project_types']:
        for mode in option['target_modes']:
            chosen = normalize_project_selection(profile.cpu, mode['value'], option['value'])
            assert chosen['use_labels'] == mode['labels_required']


def test_all_declared_creation_modes_keep_cpu_and_language_in_native_menu_witness():
    import json
    from pathlib import Path
    from plc.project_capabilities import project_creation_options
    fixture = json.loads((private_path('tests/fixtures/gxworks2_native_cpu_menu.json')).read_text(encoding='utf-8'))
    rows = fixture['project_mode_observation']['rows']
    types = {'simple': '简单工程', 'structured': '结构化工程'}
    languages = {'ladder': '梯形图', 'st': 'ST', 'fbd': '结构化梯形图/FBD'}
    for target in project_creation_options()['native_targets']:
        for kind in target['project_types']:
            if kind['value'] == 'parameter':
                assert all(not r['language_enabled'] for r in rows if r['cpu'] == target['menu_cpu'])
                continue
            for mode in kind['target_modes']:
                labels = [True] if mode['labels_required'] else [False, True] if kind['labels_supported'] else [False]
                for flag in labels:
                    match, = [r for r in rows if r['cpu'] == target['menu_cpu']
                              and r['requested_type'] == types[kind['value']] and r['requested_labels'] == flag]
                    assert match['selected_cpu'] == target['menu_cpu']
                    assert match['selected_type'] == types[kind['value']]
                    assert bool(match['labels']) == flag
                    assert match['language_enabled'] and languages[mode['value']] in match['languages']


def _rung(rung_id, input_address, output_address, note):
    return {
        "rung_id": rung_id,
        "debug_note": note,
        "header_element": None,
        "shared_inputs": [],
        "branches": [
            {
                "branch_id": 1,
                "y_offset_level": 0,
                "inputs": [
                    {"type": "NO", "address": input_address, "label": ""}
                ],
                "outputs": [
                    {"type": "COIL", "address": output_address, "label": ""}
                ],
            }
        ],
    }


def _program(two_networks=False):
    rungs = [_rung(1, "X0", "Y0", "启动输出")]
    comments = {"X0": "启动", "Y0": "运行"}
    if two_networks:
        rungs.append(_rung(2, "X1", "Y1", "辅助输出"))
        comments.update({"X1": "辅助输入", "Y1": "辅助输出"})
    return build_plc_ir(
        {"device_comments": comments, "rungs": rungs},
        plc_model="FX3U",
        program_name="MAIN",
        revision=1,
    )


def _modify_patch(program):
    replacement = copy.deepcopy(program["networks"][0]["ladder"])
    replacement["branches"][0]["inputs"].append(
        {"type": "NC", "address": "X2", "label": ""}
    )
    return {
        "base_revision": program["revision"],
        "base_ir_sha256": canonical_sha256(program),
        "target_revision": program["revision"] + 1,
        "operations": [
            {
                "operation": "modify_network",
                "network": "N0001",
                "ladder": replacement,
            }
        ],
        "device_comments": {"X2": "停止"},
    }


def _persist_base(store, project_id, program):
    core = PLCCore()
    version_id, output_dir = store.prepare_version(project_id)
    compiled = core.compile_project(program, output_dir)
    metadata = store._ir_metadata(program)
    metadata.update(
        {
            "target_mode": "ladder",
            "plc_model": "FX3U",
            "program_name": "MAIN",
            "artifacts": dict(compiled["artifacts"]),
            "confirmed_spec_snapshot": None,
            "confirmed_spec_hash": None,
        }
    )
    return store.complete_version(project_id, version_id, metadata)


def test_plc_core_reads_validates_and_compiles_through_existing_pipeline(tmp_path):
    program = _program()
    output_dir = tmp_path / "compiled"
    core = PLCCore()

    network = core.read_network(program, "N0001")
    diagnostics = core.get_diagnostics(program)
    validation = core.validate_project(program)
    compiled = core.compile_project(program, output_dir)

    assert network["writes"] == ["Y0"]
    assert diagnostics["counts"]["error"] == 0
    assert validation["valid"] is True
    assert set(compiled["artifacts"]) == {
        "json",
        "ir",
        "svg",
        "st_from_ir",
        "program_csv",
        "comment_csv",
    }
    assert all((output_dir / name).is_file() for name in compiled["artifacts"].values())
    assert len(compiled["hashes"]) == 6


@pytest.mark.parametrize('cpu', ['Q01U', 'L26-BT', 'FX0N'])
def test_shared_ladder_tools_report_missing_profile_without_counting_a_program_error(cpu, tmp_path, monkeypatch):
    import plc.validation as validation
    monkeypatch.setattr(validation, 'SUPPORTED_PLC_MODELS', validation.SUPPORTED_PLC_MODELS - {cpu})
    from plc.validation import ValidationProfileUnavailableError
    program = build_plc_ir({'device_comments': {}, 'rungs': [_rung(1, 'M0', 'M1', '')]},
                           plc_model=cpu)
    core = PLCCore()
    report = core.validate_project(program)
    assert report['available'] is False and report['valid'] is False
    assert report['status'] == 'unverified' and report['plc_model'] == cpu
    assert report['counts']['error'] == 0 and report['reason'] == 'validation_profile_unavailable'
    # Direct compilation still fails closed and creates no compiled program.
    with pytest.raises(ValidationProfileUnavailableError):
        core.compile_project(program, tmp_path / 'compiled')
    assert not list(tmp_path.rglob('MAIN.csv'))
    context = build_tool_context({'id': 'unverified', 'plc_model': cpu}, program_ir=program)
    registry = build_default_tool_registry()
    checked = registry.call('validate_project', {}, context)
    compiled = registry.call('compile_project', {}, context)
    assert checked['ok'] and checked['data'] == report
    assert compiled['ok'] and compiled['data']['compiled'] is False
    assert compiled['data']['status'] == 'unverified' and compiled['data']['artifacts'] == []
    assert compiled['data']['validation'] == report


def test_malformed_ladder_still_counts_as_an_error_with_missing_cpu_profile():
    program = _program()
    program['plc']['cpu'] = 'Q00U'
    program['networks'] = 'broken'
    report = PLCCore().validate_project(program)
    assert report['valid'] is False and report['counts']['error'] == 1


def test_network_patch_supports_add_modify_and_delete_with_structured_diff():
    core = PLCCore()
    base = _program(two_networks=True)
    replacement = copy.deepcopy(base["networks"][0]["ladder"])
    replacement["branches"][0]["inputs"].append(
        {"type": "NC", "address": "X2", "label": ""}
    )
    patch = {
        "base_revision": 1,
        "base_ir_sha256": canonical_sha256(base),
        "target_revision": 2,
        "operations": [
            {
                "operation": "modify_network",
                "network": "N0001",
                "ladder": replacement,
            },
            {"operation": "delete_network", "network": "N0002"},
            {
                "operation": "add_network",
                "network": "N0003",
                "after": "N0001",
                "ladder": _rung(3, "X3", "M0", "新增状态"),
            },
        ],
        "device_comments": {"X2": "停止", "X3": "条件", "M0": "状态"},
    }

    candidate = core.patch_program(base, patch)

    assert candidate["target_revision"] == 2
    assert candidate["diff"]["added"] == ["N0003"]
    assert candidate["diff"]["deleted"] == ["N0002"]
    assert candidate["diff"]["modified"] == ["N0001"]
    assert candidate["diff"]["device_comments_changed"] is True
    assert [item["marker"] for item in candidate["diff"]["changes"]] == [
        "+",
        "-",
        "~",
    ]
    assert candidate["diagnostics"]["valid"] is True


def test_tool_runtime_hides_candidate_ir_from_model_but_keeps_it_for_ui():
    program = _program()
    version = {
        "id": "v0001",
        "target_mode": "ladder",
        "plc_model": "FX3U",
        "program_name": "MAIN",
        "revision": 1,
        "confirmed_spec_snapshot": None,
    }
    context = build_tool_context(
        {
            "id": "project1",
            "name": "测试项目",
            "plc_model": "FX3U",
            "active_version_id": "v0001",
            "versions": [version],
        },
        version=version,
        program_ir=program,
    )
    runtime = InProcessToolRuntime(build_default_tool_registry())

    result = runtime.invoke(
        ToolCall("call_patch", "patch_program", {"patch": _modify_patch(program)}),
        context,
    )

    public = json.loads(result.content)
    assert result.is_error is False
    assert public["status"] == "confirmation_required"
    assert "_candidate_ir" not in result.content
    assert "_confirmed_spec" not in result.content
    pending = result.data["data"]["pending_action"]
    assert pending["_candidate_ir"]["revision"] == 2


def test_invalid_candidate_never_creates_a_version(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("候选测试", plc_model="FX3U")
    base = _program()
    version = _persist_base(store, project["id"], base)
    context = build_tool_context(
        store.get_project(project["id"]),
        version=version,
        program_ir=base,
    )
    invalid = _modify_patch(base)
    invalid["operations"][0]["ladder"]["rung_id"] = 99

    result = build_default_tool_registry().call(
        "patch_program", {"patch": invalid}, context
    )

    assert result["ok"] is False
    assert len(store.get_project(project["id"])["versions"]) == 1
    assert sorted(path.name for path in store.version_dir(project["id"], "v0001").parent.iterdir()) == [
        "v0001"
    ]


def test_candidate_cancel_changes_neither_current_version_nor_gx_state(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("候选测试", plc_model="FX3U")
    base = _program()
    version = _persist_base(store, project["id"], base)
    original_project = copy.deepcopy(store.get_project(project["id"]))
    original_ir = copy.deepcopy(store.load_program_ir(project["id"], version["id"]))
    context = build_tool_context(original_project, version=version, program_ir=base)

    result = build_default_tool_registry().call(
        "patch_program", {"patch": _modify_patch(base)}, context
    )
    assert result["status"] == "confirmation_required"
    # Cancellation is deliberately represented by not invoking the UI-only accept action.

    assert store.get_project(project["id"])["active_version_id"] == version["id"]
    assert len(store.get_project(project["id"])["versions"]) == 1
    assert store.load_program_ir(project["id"], version["id"]) == original_ir
    assert not (store.project_dir(project["id"]) / "GX Works2 Backups").exists()


def test_accept_candidate_creates_only_one_local_child_version(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("候选测试", plc_model="FX3U")
    base = _program()
    version = _persist_base(store, project["id"], base)
    base_ir_path = store.version_dir(project["id"], version["id"]) / "program.ir.json"
    base_bytes = base_ir_path.read_bytes()
    context = build_tool_context(
        store.get_project(project["id"]), version=version, program_ir=base
    )
    tool_result = build_default_tool_registry().call(
        "patch_program", {"patch": _modify_patch(base)}, context
    )
    action = tool_result["data"]["pending_action"]

    accepted = accept_candidate_patch(store, action)

    current = store.get_project(project["id"])
    assert accepted["id"] == "v0002"
    assert accepted["parent_version_id"] == "v0001"
    assert accepted["source_candidate_id"] == action["candidate_id"]
    assert accepted["lifecycle_status"] == "accepted"
    assert current["active_version_id"] == "v0002"
    assert len(current["versions"]) == 2
    assert base_ir_path.read_bytes() == base_bytes
    assert store.load_program_ir(project["id"], "v0002")["revision"] == 2
    assert all(
        (store.version_dir(project["id"], "v0002") / name).is_file()
        for name in accepted["artifacts"].values()
    )
    assert not (store.project_dir(project["id"]) / "GX Works2 Backups").exists()


def test_tampered_candidate_is_rejected_without_creating_a_version(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("候选测试", plc_model="FX3U")
    base = _program()
    version = _persist_base(store, project["id"], base)
    context = build_tool_context(
        store.get_project(project["id"]), version=version, program_ir=base
    )
    result = build_default_tool_registry().call(
        "patch_program", {"patch": _modify_patch(base)}, context
    )
    action = copy.deepcopy(result["data"]["pending_action"])
    action["_candidate_ir"]["revision"] = 100

    with pytest.raises(ValueError):
        accept_candidate_patch(store, action)

    assert len(store.get_project(project["id"])["versions"]) == 1


@pytest.mark.parametrize("plc_model", ["FX3U", "FX5U"])
def test_create_program_candidate_builds_valid_initial_ir(plc_model):
    ladder = ir_to_ladder(_program())
    ladder["device_comments"] = {" x0 ": "启动", "y0": "运行"}
    original = copy.deepcopy(ladder)
    spec = {"io_table": [{"address": "Y0", "kind": "Y", "label": "电机"}]}
    core = PLCCore()

    candidate = core.create_program_candidate(ladder, plc_model=plc_model, confirmed_spec=spec)
    program = candidate["candidate_ir"]

    assert candidate["revision"] == program["revision"] == 1
    assert program["plc"]["cpu"] == plc_model
    assert program["program_name"] == "MAIN"
    assert validate_plc_ir(program, confirmed_spec=spec) == program
    assert ir_to_ladder(program) == {
        **ladder, "device_comments": {"X0": "启动", "Y0": "电机"},
    }
    assert candidate["candidate_ir_sha256"] == canonical_sha256(program)
    assert candidate["ladder_sha256"] == canonical_sha256(ir_to_ladder(program))
    assert program["io_map"]["Y0"]["label"] == "电机"
    assert program["networks"][0]["reads"] == ["X0"]
    assert program["networks"][0]["writes"] == ["Y0"]
    assert candidate["diagnostics"]["valid"] is True
    assert candidate["summary"] == {
        "network_count": 1, "network_ids": ["N0001"], "instruction_count": 2,
        "device_count": 2, "devices_by_kind": {"X": 1, "Y": 1},
    }
    repeated = core.create_program_candidate(ladder, plc_model=plc_model, confirmed_spec=spec)
    assert candidate["candidate_id"] != repeated["candidate_id"]
    assert candidate["candidate_ir_sha256"] == repeated["candidate_ir_sha256"]
    assert ladder == original


@pytest.mark.parametrize("comment", [
    "计数输入：OFF→ON 每次计数一次",
    "D0 的16位计数值达到3及以上时接通",
    "高电平优先复位：D0 清零，Y0 断开",
])
def test_generated_candidate_shows_only_complete_short_device_names(comment):
    ladder = ir_to_ladder(_program())
    ladder["device_comments"]["X0"] = comment

    candidate = PLCCore().create_program_candidate(ladder, plc_model="FX3U")
    saved_comment = ir_to_ladder(candidate["candidate_ir"])["device_comments"]["X0"]

    assert saved_comment == ""
    assert candidate["diagnostics"]["valid"] is True


def test_full_candidate_uses_same_confirmed_comment_source_as_compact_api():
    from application.compact_protocol import _confirmed_comments

    spec = {"io_table": [{"address": "X0", "label": "计数按钮"},
                         {"address": "Y0", "label": "计满指示灯"}]}
    ladder = ir_to_ladder(_program())
    ladder["device_comments"] = {
        "X0": "计数输入：OFF→ON 每次计数一次",
        "M0": "内部状态用于检测上升沿并禁止首扫描计数",
    }
    candidate = PLCCore().create_program_candidate(ladder, plc_model="FX3U", confirmed_spec=spec)

    assert ir_to_ladder(candidate["candidate_ir"])["device_comments"] == {
        **_confirmed_comments(spec), "M0": "",
    }


def test_existing_long_comment_does_not_block_unrelated_partial_edit():
    previous = _program()
    previous["devices"]["X0"]["comment"] = "保留旧版本中已经存在的详细输入说明"
    # Rebuild to keep the IR source hash consistent with the historical text.
    previous = build_plc_ir(ir_to_ladder(previous), plc_model="FX3U", revision=1)
    partial = {"mode": "partial", "device_comments": {}, "delete_rung_ids": [],
               "rungs": [_rung(1, "X0", "Y0", "只改梯级说明")]}

    candidate = PLCCore().create_program_candidate(
        partial, plc_model="FX3U", previous_program=previous)

    assert ir_to_ladder(candidate["candidate_ir"])["device_comments"]["X0"] == (
        "保留旧版本中已经存在的详细输入说明")


@pytest.mark.parametrize("ladder", [None, [], "text", {}, {"device_comments": {}, "rungs": []}])
def test_create_program_candidate_rejects_invalid_ladder(ladder):
    with pytest.raises((TypeError, ValueError)):
        PLCCore().create_program_candidate(ladder, plc_model="FX3U")


@pytest.mark.parametrize("opcode,operands,error", [
    ("UNVERIFIED", [], "unsupported APP_INSTR"),
    ("turn on motor", [], "invalid APP_INSTR opcode"),
    ("RD3A", ["D0"], "exactly 3 operands"),
    ("DRVTBL", [], "not supported by FX3U"),
])
def test_create_program_candidate_requires_real_catalogued_application_instructions(opcode, operands, error):
    ladder = ir_to_ladder(_program())
    ladder["rungs"][0]["branches"][0]["outputs"] = [
        {"type": "APP_INSTR", "opcode": opcode, "operands": operands},
    ]
    with pytest.raises(ValueError, match=error):
        PLCCore().create_program_candidate(ladder, plc_model="FX3U")


@pytest.mark.parametrize("kind", ["derived_top", "block_output", "nested_parallel", "long_label"])
def test_generation_cannot_smuggle_derived_fields_or_import_only_syntax(kind):
    ladder = ir_to_ladder(_program())
    rung = ladder["rungs"][0]
    branch = rung["branches"][0]
    if kind == "derived_top":
        ladder["analysis"] = {"counts": {"error": 0}}
    elif kind == "derived_rung":
        rung["instructions"] = [{"op": "OUT", "args": ["Y7"]}]
    elif kind == "block_output":
        branch["outputs"] = [{"type": "BLOCK_OUTPUT", "expression": "UNVERIFIED D0"}]
    elif kind == "nested_parallel":
        branch["inputs"] = [{"type": "parallel_block", "branches": [[
            {"type": "parallel_block", "branches": [[{"type": "NO", "address": "X0"}]]},
        ]]}]
    else:
        branch["outputs"][0]["label"] = "长" * 65
    with pytest.raises(ValueError):
        PLCCore().create_program_candidate(ladder, plc_model="FX3U")


def test_create_program_candidate_defers_confirmed_approach_conflict_to_review():
    spec = {"selected_approach": {
        "name": "必须自锁", "generation_contract": {"required_structures": ["self_hold"]},
    }}
    core = PLCCore()
    candidate = core.create_program_candidate(ir_to_ladder(_program()), plc_model="FX3U", confirmed_spec=spec)
    assert candidate["validation_profile"] == "generation_structural"
    assert not core.validate_project(candidate["candidate_ir"], spec)["valid"]


def test_create_program_candidate_defers_hardware_heuristics_to_review():
    ladder = ir_to_ladder(_program())
    ladder["rungs"][0]["branches"][0]["outputs"] = [{
        "type": "APP_INSTR", "opcode": "PLSY", "operands": ["K100", "K1000", "Y0"],
    }]
    spec = {"hardware_profile": {"plc_family": "FX3U", "output_type": "relay"}}
    core = PLCCore()
    candidate = core.create_program_candidate(ladder, plc_model="FX3U", confirmed_spec=spec)
    assert candidate["diagnostics"]["valid"]
    assert not core.validate_project(candidate["candidate_ir"], spec)["valid"]


def test_create_program_candidate_retains_nonblocking_static_diagnostics(monkeypatch):
    core = PLCCore()

    def diagnostics(program):
        validate_plc_ir(program)
        return {"counts": {"error": 1}, "findings": [{"code": "STATIC_ERROR", "message": "test diagnostic"}]}

    monkeypatch.setattr(core, "get_diagnostics", diagnostics)
    candidate = core.create_program_candidate(ir_to_ladder(_program()), plc_model="FX3U")
    assert candidate["diagnostics"]["valid"] is True
    assert candidate["diagnostics"]["findings"][0]["code"] == "STATIC_ERROR"


@pytest.mark.parametrize("existing_version", [False, True])
@pytest.mark.parametrize("compile_failure", [False, True])
def test_generated_candidate_uses_only_temporary_artifacts_and_never_persists(tmp_path, monkeypatch, existing_version, compile_failure):
    import plc.generation as plc_generation

    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("新程序候选", plc_model="FX3U")
    version = _persist_base(store, project["id"], _program()) if existing_version else None
    project = store.get_project(project["id"])
    before = {path.relative_to(store.base_dir): path.read_bytes() for path in store.base_dir.rglob("*") if path.is_file()}
    renderer = plc_generation.render_generation_artifacts
    directories = []

    def render(program, directory):
        directories.append(directory)
        assert not directory.is_relative_to(store.base_dir)
        if compile_failure:
            (directory / "partial.txt").write_text("partial")
            raise ValueError("test compile failure")
        rendered = renderer(program, directory)
        assert len(rendered["artifacts"]) == 6
        assert all((directory / name).exists() for name in rendered["artifacts"].values())
        return rendered

    def forbid_persistence(*args, **kwargs):
        pytest.fail("Candidate creation must not persist a version")

    monkeypatch.setattr(plc_generation, "render_generation_artifacts", render)
    monkeypatch.setattr(SessionStore, "prepare_version", forbid_persistence)
    monkeypatch.setattr(SessionStore, "complete_version", forbid_persistence)
    result = build_default_tool_registry().call(
        "create_program_candidate", {"ladder": ir_to_ladder(_program()), "program_name": "MOTOR"},
        build_tool_context(project, version=version),
    )

    if compile_failure:
        assert result["ok"] is False
        assert "status" not in result
        assert "test compile failure" in result["error"]["message"]
    else:
        assert result["status"] == "confirmation_required"
        assert result["data"]["verification"]["behavior_verified"] is False
        assert result["data"]["verification"]["native_verified"] is False
        pending = result["data"]["pending_action"]
        assert pending["type"] == "accept_generated_program"
        assert pending["_candidate_ir"]["program_name"] == "MOTOR"
        assert pending["_candidate_ir"]["revision"] == pending["revision"] == 1
        assert len(pending["artifact_hashes"]) == 6
    assert len(directories) == 1
    assert all(not directory.exists() for directory in directories)
    assert store.get_project(project["id"]) == project
    assert store.get_project(project["id"])["active_version_id"] == (version["id"] if version else None)
    assert {path.relative_to(store.base_dir): path.read_bytes() for path in store.base_dir.rglob("*") if path.is_file()} == before


def test_generation_rejects_non_ladder_target_before_core_invocation(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("The core must not be called for an ST project")

    monkeypatch.setattr(PLCCore, "create_program_candidate", forbidden)
    result = build_default_tool_registry().call(
        "create_program_candidate", {"ladder": ir_to_ladder(_program())},
        build_tool_context({"id": "st-project", "target_mode": "st"}),
    )
    assert result["ok"] is False
    assert "ladder" in result["error"]["message"]


@pytest.mark.parametrize('source', [
    'VAR count:INT:=0; END_VAR count:=count+1;',
    'Y0:=BOOL#TRUE;',
    'VAR i:INT; a:ARRAY[-1..2] OF DINT; END_VAR FOR i:=-1 TO 2 DO a[i]:=DINT#16#FF; END_FOR;',
    'VAR state:INT; END_VAR CASE state OF -1,0: state:=1; 1..3: state:=0; ELSE RETURN; END_CASE;',
    'VAR value:REAL; wait:TIME; END_VAR value:=2.0**3.0**2.0; wait:=T#1s;',
    'VAR count:INT:=0; END_VAR WHILE count<3 DO count:=count+1; END_WHILE; REPEAT EXIT; UNTIL FALSE END_REPEAT;',
])
def test_st_frontend_covers_gx_control_and_declarations(source):
    from plc.st_validation import inspect_st_program
    report=inspect_st_program(source,plc_model='FX3U')
    assert report['syntax']==report['semantic']=='passed',report
    assert report['native_compile']==report['behavior']=='unverified'


@pytest.mark.parametrize('engine,version,expected', [
    ('cpu_manual', None, 'failed'), ('gx_simulator2_fx', '11.60.0.0', 'passed'),
    ('gx_simulator2_fx', None, 'unverified'), ('gx_simulator2_fx', 'another-version', 'unverified'),
    ('unrecognized-engine', '11.60.0.0', 'unverified'),
])
def test_power_range_keeps_the_cpu_manual_and_exact_simulator_engine_separate(engine, version, expected):
    from plc.instruction_constraints import check_language_constraints
    context = dict(numeric_engine=engine, numeric_precision=32)
    if version is not None:
        context['simulator_version'] = version
    result = check_language_constraints('ST.OPERATOR.POWER',
        dict(LEFT=2.0, RIGHT=-126.0, RESULT=1.1754907074462803e-38), plc_model='FX3U',
        version='1.635.0.1', context=context, phase='result')
    assert result['status'] == expected, result


@pytest.mark.parametrize('stored', [float('nan'), float('inf'), -1.0, 0.0, 7.0])
def test_native_numeric_profile_checks_the_actual_stored_output(stored):
    from plc.instruction_constraints import check_language_constraints
    result = check_language_constraints('ST.OPERATOR.POWER', dict(LEFT=2.0, RIGHT=3.0, RESULT=stored),
        plc_model='FX3U', version='1.635.0.1', phase='result',
        context=dict(numeric_engine='gx_simulator2_fx',simulator_version='11.60.0.0',numeric_precision=32))
    assert result['status'] == 'failed', result


def test_disabled_native_power_does_not_need_an_execution_engine_version():
    from plc.instruction_constraints import check_instruction_constraints
    result = check_instruction_constraints('EXPT_E', {'EN':False}, plc_model='FX3U', language='st', phase='result',
        context={'numeric_engine':'gx_simulator2_fx'})
    assert result['status'] == 'passed' and not result['findings'], result


def test_st_frontend_gx_expression_tree_and_source_positions():
    from plc.st_frontend import parse_body
    source='(* 中文 *)\nD0:=2.0**3.0**2.0;\nM0:=D1<D2=D3<D4;'
    first,second=parse_body(source).body
    assert first[2][0:2]==('binary','**') and first[2][2][0:2]==('binary','**')
    assert second[2][0:2]==('binary','=')
    assert second[2][2][0:2]==second[2][3][0:2]==('binary','<')
    assert (first.span.line,first.span.column,first.span.start)==(2,1,source.index('D0'))
    assert parse_body('M0:=M1 & M2;').body[0][2][1]=='&'
    assert parse_body('D0.0:=M0;').body[0][1][1]=='D0.0'


@pytest.mark.parametrize('keyword', ['VAR_RETAIN', 'VAR RETAIN'])
def test_st_retain_declarations_are_stored_and_checked_without_claiming_reset_or_power_cycle_semantics(keyword):
    from plc.st_frontend import parse_body, STUncovered
    from plc.st_validation import inspect_st_program, bounded_st_instructions
    source = f'(* storage *)\n{keyword} count:INT:=0; END_VAR count:=count+1;'
    parsed = parse_body(source)
    assert parsed.declarations[0].class_name == 'VAR_RETAIN'
    assert parsed.declarations[0].span.start == source.index('count')
    report = inspect_st_program(source, plc_model='Q13UDV')
    assert report['syntax'] == 'passed' and report['semantic'] == 'unverified'
    assert {row['code'] for row in report['findings']} == {'ST_RETAIN_COVERAGE'}
    with pytest.raises(STUncovered, match='ST_RETAIN_COVERAGE'):
        bounded_st_instructions(source, plc_model='Q13UDV')


@pytest.mark.parametrize('source,code', [
    ('IF M0 THEN M1:=TRUE;', 'ST_SYNTAX'),
    ('(* outer (* inner *) outer *) M0:=TRUE;', 'ST_SYNTAX'),
    ('EXIT;', 'ST_EXIT_SCOPE'),
    ('VAR i:INT; END_VAR FOR i:=0 TO 1 BY 0 DO M0:=TRUE; END_FOR;', 'ST_LOOP_STEP'),
    ('VAR a:ARRAY[-1..1] OF INT; END_VAR a[2]:=1;', 'ST_BINDING'),
    ('CASE D0 OF 0..2: M0:=TRUE; 2: M0:=FALSE; END_CASE;', 'ST_CASE_SELECTION'),
    ('VAR CONSTANT count:INT:=1; END_VAR count:=2;', 'ST_WRITE_TARGET'),
])
def test_st_frontend_rejects_actual_syntax_and_semantic_errors(source,code):
    from plc.st_validation import inspect_st_program
    report=inspect_st_program(source,plc_model='FX3U')
    assert report['status']=='failed',report
    assert code in {r['code'] for r in report['findings']}


def test_st_named_fb_parameters_bind_current_interface_and_verify_body():
    from plc.st_validation import inspect_st_program
    prefix='FUNCTION_BLOCK Adder VAR_INPUT A:INT; END_VAR VAR_OUTPUT Q:INT; END_VAR Q:=A; END_FUNCTION_BLOCK '
    suffix='PROGRAM MAIN VAR instance:Adder; END_VAR instance(A:=2); D0:=instance.Q; END_PROGRAM'
    assert inspect_st_program(prefix+suffix,plc_model='FX3U')['semantic']=='passed'
    for source,code in [(prefix+suffix.replace('A:=2','A:=2,A:=3'),'ST_CALL_ARGUMENT'),
                        (prefix+suffix.replace('instance(A:=2)','Adder(A:=2)'),'ST_FB_INSTANCE'),
                        (prefix.replace('Q:=A','Q:=TRUE')+suffix,'ST_TYPE')]:
        report=inspect_st_program(source,plc_model='FX3U')
        assert report['semantic']=='failed' and code in {r['code'] for r in report['findings']},report
    report=inspect_st_program(prefix+suffix.replace('A:=2','A:=2,Q=>D1'),plc_model='FX3U')
    assert report['syntax']=='passed' and report['semantic']=='unverified'
    assert 'ST_FB_OUTPUT_OPTION' in {r['code'] for r in report['findings']}


def test_st_candidate_v2_binds_real_task_and_preserves_existing_declarations():
    from application.native_st import candidate_native_plan
    from gxw.project_templates import blank_fbd_baseline
    from gxw.st_program import project_context_snapshot
    baseline=blank_fbd_baseline('Q03UDV')
    context=project_context_snapshot(baseline)
    task=context['tasks'][0]
    candidate=dict(schema_version=2,language='st',program=dict(name='New_ST',kind='program',body='VAR count:INT:=0; END_VAR count:=count+1;'),
        declaration_edits={'upserts':[]},task_binding={k:task[k] for k in ('resource','task')})
    result=candidate_native_plan(candidate,baseline=baseline,plc_model='Q03UDV')
    command=result['plan']['commands'][0]
    assert command['operation']=='create_st_program' and command['st_text']=='count:=count+1;'
    assert command['declarations'][0]['name']=='count'
    assert [c['operation'] for c in result['plan']['commands'][-3:]]==['compile','save','reopen']
    candidate['task_binding']['task']='invented_task'
    with pytest.raises(ValueError,match='Task binding'):
        candidate_native_plan(candidate,baseline=baseline,plc_model='Q03UDV')

def test_full_ladder_validation_visits_each_rung_once_without_mutating_input(monkeypatch):
    import plc.validation as validation
    from test_timer_semantics_regressions import _ladder, _output_rung
    data = _ladder(*[_output_rung(i, {"type": "COIL", "address": "M" + str(i), "label": ""},
        inputs=[{"type": "NO", "address": "X0", "label": ""}]) for i in range(1, 4)])
    before = copy.deepcopy(data)
    visits = []
    validate = validation._validate_rung
    def counted(rung, *args, **kwargs):
        visits.append(rung["rung_id"])
        return validate(rung, *args, **kwargs)
    monkeypatch.setattr(validation, "_validate_rung", counted)
    assert validation.validate_ladder_full(data, "FX3U") is data
    assert visits == [1, 2, 3]
    assert data == before

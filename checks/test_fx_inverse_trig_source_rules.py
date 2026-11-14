"""Own R524/526 Native inverse-trigonometric interfaces; no numeric executor."""
import json
from pathlib import Path
import pytest

HERE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-inverse-trig-native-contract-repair')
MATH_HERE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-math-first-callable-repair')


def rows():
    return json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']


def own(entry, suffix):
    return next(f for f in entry['facts'] if f['id'] == 'authored:applied:' + suffix + '@fx3_programming_r')


def test_exact_original_eight_and_complete_namespace():
    selectors = json.loads((HERE/'owner-selectors.json').read_text())
    assert {(e['target_model'],e['opcode']) for e in rows()} == {(s['target_model'],s['opcode']) for s in selectors}
    assert len(selectors) == 8
    baseline = {(e['target_model'],e['opcode']):e for e in json.loads((HERE/'baseline-source.json').read_text())['entries']}
    for entry in rows():
        current = {f['id']:f for f in entry['facts']}
        for f in baseline[entry['target_model'],entry['opcode']]['facts']:
            if f['id'].startswith(('authored:applied:','authored:ql:')):
                assert f['id'] in current
            if f['id'].startswith('authored:applied:') and not f['id'].endswith('@fx3_programming_r'):
                assert current[f['id']] == f


@pytest.mark.parametrize('family,pages,fnc', [('DASIN',[524,525],133),('DACOS',[526,527],134)])
def test_native_real_types_own_pages_and_version(family,pages,fnc):
    for e in rows():
        if not e['opcode'].startswith(family):
            continue
        f = own(e,'native_typed_operands')
        assert f['status'] == 'source_verified' and f['verification'] == 'source_checked'
        assert {'context':'project_mode','equals':'native_ladder_instruction'} in f['conditions']
        assert {'context':'cpu_version','minimum':'2.20' if e['target_model']=='FX3U' else '1.00'} in f['conditions']
        assert any(s.get('pdf_pages') == pages for s in f['sources'])
        s,d = f['value']['operands']
        assert [s['name'],d['name']] == ['S','D']
        assert [s['type'],d['type']] == ['REAL','REAL']
        assert [s['direction'],d['direction']] == ['input','output']
        assert s['word_device_layout']['word_count'] == d['word_device_layout']['word_count'] == 2
        assert own(e,'variant_family')['value']['fnc_number'] == fnc


def test_own_real_literal_device_grid_and_modify():
    for e in rows():
        s,d = own(e,'native_typed_operands')['value']['operands']
        for p in (s,d):
            assert p['range']['device_prefixes'] == ['D','R']
            assert p['range']['address_forms'] == ['buffer_memory']
            assert p['range']['index_modification'] is True
        assert s['range']['literal_prefixes'] == ['E'] and d['range']['literal_prefixes'] == []
        assert s.get('passing') != 'device_reference'  # Own source explicitly permits real constant S.
        assert s['range']['minimum'] == -1 and s['range']['maximum'] == 1


def test_own_continuous_and_pulse_are_separate():
    for e in rows():
        assert own(e,'execution_form')['value']['form'] == ('pulse' if e['opcode'].endswith('P') else 'continuous')
        assert own(e,'program_steps')['value']['steps'] == 9


@pytest.mark.parametrize('family,operation,low,high,example', [
    ('DASIN','arcsine','-pi/2','pi/2',0.5235988),
    ('DACOS','arccosine',0,'pi',1.047198)])
def test_documented_input_domain_radian_result_error_and_unknowns(family,operation,low,high,example):
    for e in rows():
        if not e['opcode'].startswith(family):
            continue
        v = own(e,'inverse_trig_documented_processing')['value']
        assert v['operation'] == operation and v['input_range'] == [-1,1]
        assert v['output_angle_unit'] == 'radian' and v['output_range'] == dict(minimum=low,maximum=high)
        assert v['documented_example'] == dict(input=0.5,output=example)
        for key in ('command_FALSE_output_behavior_established','source_destination_overlap_behavior_established',
                    'indexed_effective_address_verified','current_source_value_verified',
                    'special_float_input_behavior_established','core_execution_established',
                    'native_execution_verified','full_source_relation_complete'):
            assert v[key] is False
        error = own(e,'inverse_trig_documented_error')['value']
        assert error['error_flag'] == 'M8067' and error['error_code_device'] == 'D8067' and error['error_code'] == 6706
        gate = own(e,'inverse_trig_execution_gate')['value']
        assert gate['condition'] == ('rising_edge' if e['opcode'].endswith('P') else 'execution_condition_ON')
        assert gate['Core_execution_verified'] is gate['Native_execution_verified'] is False


def test_J_double_trig_forms_follow_their_own_step_row_and_CPU_form_cells():
    from tools import fact_applied_review as producer

    def record(opcode, fnc, name, *, include_form_cells=True):
        cpu_cells = {'FX2N': True, 'FX2NC': True}
        form_models = ({'16': {'FX2N': False, 'FX2NC': False},
                        '32': cpu_cells.copy(), 'pulse': cpu_cells.copy()}
                       if include_form_cells else {})
        return {
            'opcode': opcode,
            'manual_id': 'jy992d48301j',
            'manual_number': 'JY992D48301',
            'revision': 'J',
            'pdf_pages': [253 if opcode == 'SIN' else 254],
            'text': 'floating point 32-bit double-word operation',
            'parameter_conventions': {'source_destination_symbols': True, 'd_prefix_is_32_bit': True},
            'manual_models': ['FX2N', 'FX2NC'],
            'applicability': {'basis': 'instruction_header', 'model_marks': [
                {'model': model, 'state': 'supported'} for model in ('FX2N', 'FX2NC')]},
            'source_form_models': form_models,
            'table_groups': [[
                ['Mnemonic', 'Function', 'Operands', '', 'Program steps'],
                ['', '', 'S', 'D', ''],
                [f'{opcode} FNC {fnc}', 'Calculates a floating point value',
                 'D source (32 bits)', 'D destination (32 bits)', f'{name}, {name}P: 9 steps'],
            ]],
        }

    for rec, pair in ((record('SIN', 130, 'DSIN'), ('DSIN', 'DSINP')),
                      (record('TAN', 132, 'DTAN'), ('DTAN', 'DTANP'))):
        variants = producer._legacy_variants(rec)
        assert [(variant['opcode'], variant['width_bits'], variant['execution_form'])
                for variant in variants] == [(pair[0], 32, 'continuous'), (pair[1], 32, 'pulse')]
        for variant in variants:
            assert producer._legacy_form_states(rec, variant) == {
                'FX2N': 'supported', 'FX2NC': 'supported'}
            native = producer._legacy_native_parameter_rows(rec, variant)
            assert native and native['order'] == ['S', 'D']
            assert [(row['type'], row['direction']) for row in native['rows']] == [
                ('REAL', 'input'), ('REAL', 'output')]

    # The program-steps declaration alone cannot establish a pulse form or
    # CPU applicability when its own width/pulse badge matrix was not read.
    incomplete = record('SIN', 130, 'DSIN', include_form_cells=False)
    variants = producer._legacy_variants(incomplete)
    assert [(variant['opcode'], variant['execution_form']) for variant in variants] == [
        ('DSIN', 'continuous'), ('DSINP', 'unknown')]
    assert all(set(producer._legacy_form_states(incomplete, variant).values()) == {'unknown'}
               for variant in variants)


def test_J_legacy_math_patch_contains_only_the_eight_supported_CPU_forms():
    bundle = json.loads((MATH_HERE/'source-fact-additions.json').read_text(encoding='utf-8'))
    assert bundle['source_group'] == 'J-legacy-math'
    assert bundle['namespace_prefixes_replaced'] == ['authored:applied:', 'authored:ql:']
    entries = bundle['entries']
    assert {(e['target_model'], e['opcode']) for e in entries} == {
        (cpu, opcode) for cpu in ('FX2N', 'FX2NC')
        for opcode in ('DSIN', 'DSINP', 'DTAN', 'DTANP')}
    for entry in entries:
        assert entry['source_group'] == 'J-legacy-math'
        facts = {fact['id']: fact for fact in entry['facts']}
        manual = 'jy992d48301j'
        availability = facts[f'authored:applied:cpu_availability@{manual}']
        assert availability['status'] == 'source_verified'
        assert availability['value']['available'] is True
        assert availability['scope']['models'] == [entry['target_model']]
        assert availability['scope']['forms'] == [entry['opcode']]
        page = 253 if entry['opcode'].startswith('DSIN') else 254
        assert any(s['manual_id'] == manual and s['revision'] == 'J' and page in s['pdf_pages']
                   for s in availability['sources'])
        typed = facts[f'authored:applied:native_typed_operands@{manual}']
        assert [(p['type'], p['direction']) for p in typed['value']['operands']] == [
            ('REAL', 'input'), ('REAL', 'output')]
        assert facts[f'authored:applied:program_steps@{manual}']['value']['steps'] == 9
        execution = facts[f'authored:applied:execution_form@{manual}']['value']
        assert execution == {'form': 'pulse' if entry['opcode'].endswith('P') else 'continuous',
                             'width_bits': 32}
        own_j = [fact for fact in entry['facts']
                 if any(source.get('manual_id') == manual for source in fact.get('sources', []))]
        assert not any(c.get('context') == 'cpu_version'
                       for fact in own_j for c in fact.get('conditions', []))

import sqlite3

from tools.fact_applied_review import (
    _for_model,
    _models,
    _repair_m_anrd_anwr_applicability,
    _repair_m_anrd_anwr_call_constraints,
    _repair_m_anrd_anwr_cpu_availability,
    _structured_variant_table,
)


def _record(opcode='ANRD', page=423):
    counterpart = 'ANRDP' if opcode == 'ANRD' else 'ANWRP'
    plain = 'ANRD(EN, s, n, d1, d2)' if opcode == 'ANRD' else 'ANWR(EN, s1, s2, n, d)'
    pulse = 'ANRDP(EN, s, n, d1, d2)' if opcode == 'ANRD' else 'ANWRP(EN, s1, s2, n, d)'
    return {
        'id': f'chunk:fxcpu_basic_applied_m:{opcode}', 'opcode': opcode,
        'manual_id': 'fxcpu_basic_applied_m', 'manual_number': 'JY997D34701',
        'revision': 'M', 'section': f'16.x {opcode} / analog unit',
        'pdf_pages': [page], 'manual_models': ['FX3U', 'FXU', 'FX2C'],
        'text': f'{opcode} 16 bits Continuous {plain};\n'
                f'{counterpart} 16 bits Pulse {pulse};\n'
                + ('Head input number of FX2-24EI connected to F2-6A (16 points occupied) Bit\n'
                   'Channel number of analog input (n=10,11,12,13) ANY16\n'
                   'Head output number of FX2-24EI connected to F2-6A (8 points occupied) Bit\n'
                   'Device storing analog input value (8-bit binary) ANY16\n'
                   if opcode == 'ANRD' else
                   'Device for storing analog output data (8-bit binary) ANY16\n'
                   'Head input number of FX2-24EI connected to F2-6A Bit\n'
                   'Channel number of analog output ANY16\n'
                   'Head output number of FX2-24EI connected to F2-6A Bit\n'),
        'applicability': {'basis': 'instruction_header', 'model_marks': [
            {'model': 'FX3U', 'state': 'unsupported'},
            {'model': 'FXU', 'state': 'conditional'},
            {'model': 'FX2C', 'state': 'conditional'},
        ]},
    }


def test_m_analog_header_binds_only_the_conditional_fxu_fx2c_marks_to_ver_330():
    source = sqlite3.connect(':memory:')
    source.row_factory = sqlite3.Row
    source.execute('CREATE TABLE page_artifacts(manual_id TEXT, pdf_page INTEGER, clean_text TEXT)')
    source.executemany('INSERT INTO page_artifacts VALUES(?,?,?)', [
        ('fxcpu_basic_applied_m', 36,
         '*5. The instruction is not provided in the FX U and FX2C PLCs Ver. 3.30 or later.'),
        ('fxcpu_basic_applied_m', 423,
         '16.2 ANRD / Read from F2-6A ANRD 16 bits Continuous ANRD(EN, s, n, d1, d2); '
         'ANRDP 16 bits Pulse ANRDP(EN, s, n, d1, d2); '
         'Caution The instruction is not provided in the FXU and FX2C PLCs Ver. 3.30 or later.'),
    ])
    record = _record()
    assert _repair_m_anrd_anwr_applicability(source, [record]) == 1
    source.close()

    assert [(row['opcode'], row['width_bits'], row['execution_form'], row['call_form'])
            for row in _structured_variant_table(record)] == [
        ('ANRD', 16, 'continuous', 'ANRD(EN, s, n, d1, d2)'),
        ('ANRDP', 16, 'pulse', 'ANRDP(EN, s, n, d1, d2)'),
    ]
    models, gap, conditional, guard = _models(record['applicability'], record['manual_models'])
    assert gap is None and models == ['FXU', 'FX2C']
    assert conditional == ['FXU', 'FX2C']
    assert guard == {'context': 'cpu_version',
                     'maximum_exclusive_by_model': {'FXU': '3.30', 'FX2C': '3.30'}}
    facts = [{'id': 'authored:applied:st_signature@fxcpu_basic_applied_m',
              'dimension': 'parameters.st_signature', 'value': {}, 'status': 'source_verified',
              'verification': 'source_checked', 'sources': [], 'scope': {'models': ['FXU']}}]
    bound = _for_model(facts, 'FXU', conditional, guard)
    assert bound[0]['conditions'] == [{'context': 'cpu_version', 'maximum_exclusive': '3.30'}]
    assert bound[0]['status'] == 'source_verified'


def test_m_analog_cpu_facts_keep_unsupported_models_and_bind_the_retirement_interval():
    record = _record()
    record['applicability']['model_marks'][1]['maximum_version_exclusive'] = '3.30'
    record['applicability']['model_marks'][2]['maximum_version_exclusive'] = '3.30'
    facts = {}
    for model in ('FX3U', 'FXU', 'FX2C'):
        facts[(model, 'ANRD')] = [{
            'id': 'authored:applied:st_signature@fxcpu_basic_applied_m',
            'dimension': 'parameters.st_signature', 'value': {}, 'status': 'source_verified',
            'verification': 'source_checked', 'sources': [], 'scope': {'models': [model]},
        }]

    assert _repair_m_anrd_anwr_cpu_availability([record], facts) == 20
    fxu_rows = {row['id']: row for row in facts[('FXU', 'ANRD')]}
    conditional = fxu_rows['authored:applied:cpu_availability:header@fxcpu_basic_applied_m']
    version = fxu_rows['authored:applied:cpu_version:header_maximum@fxcpu_basic_applied_m']
    denial = fxu_rows['authored:applied:cpu_availability:at_or_above_header_version@fxcpu_basic_applied_m']
    header = fxu_rows['authored:applied:source_cpu_header@fxcpu_basic_applied_m']
    assert header['value']['model_group'] == 'FXU/FX2C'
    assert header['value']['source_mark'] == '△'
    assert 'available' not in conditional['value']
    assert conditional['value']['model_states'] == {'FXU': 'conditional'}
    assert conditional['value']['model_group'] == 'FXU/FX2C'
    assert conditional['value']['source_mark'] == '△'
    assert conditional['conditions'] == [{'context': 'cpu_version', 'maximum_exclusive': '3.30'}]
    assert conditional['depends_on'] == [
        'authored:applied:source_cpu_header@fxcpu_basic_applied_m', version['id']]
    assert version['value']['maximum_series_version_exclusive'] == 'Ver.3.30'
    assert any(source['pdf_pages'] == [36] for source in version['sources'])
    assert denial['value']['available'] is False
    assert denial['value']['model_group'] == 'FXU/FX2C'
    assert denial['value']['source_mark'] == '△'
    assert denial['conditions'] == [{'context': 'cpu_version', 'minimum': '3.30'}]
    fx3u_header = next(row for row in facts[('FX3U', 'ANRD')]
                       if row['id'] == 'authored:applied:source_cpu_header@fxcpu_basic_applied_m')
    assert fx3u_header['value']['state'] == 'unsupported'
    assert fx3u_header['value']['model_group'] == 'FX3U(C)'
    assert fx3u_header['value']['source_mark'] == '×'
    assert facts[('FXU', 'ANRD')][0]['depends_on'][-1] == conditional['id']


def test_m_analog_constraints_resolve_only_from_own_st_signature_and_page():
    from plc.structured_call_contract import resolve_structured_call_contract

    source = sqlite3.connect(':memory:')
    source.row_factory = sqlite3.Row
    source.execute('CREATE TABLE page_artifacts(manual_id TEXT, pdf_page INTEGER, clean_text TEXT)')
    source.executemany('INSERT INTO page_artifacts VALUES(?,?,?)', [
        ('fxcpu_basic_applied_m', 423,
         '16.2 ANRD / Read from F2-6A. FX2-24EI type special adapter. '
         'Head input number of FX2-24EI connected to F2-6A (16 points occupied) Bit. '
         'Channel number of analog input (n=10,11,12,13) ANY16. '
         'Head output number of FX2-24EI connected to F2-6A (8 points occupied) Bit. '
         'Device storing analog input value (8-bit binary) ANY16. '
         'ANRD 16 bits Continuous ANRD(EN,s,n,d1,d2); '
         'ANRDP 16 bits Pulse ANRDP(EN,s,n,d1,d2);'),
        ('fxcpu_basic_applied_m', 425,
         '16.3 ANWR / Write to F2-6A. FX2-24EI type special adapter. '
         'Device for storing analog output data (8-bit binary) ANY16. '
         'Head input number of FX2-24EI connected to F2-6A Bit. '
         'Channel number of analog output ANY16. '
         'Head output number of FX2-24EI connected to F2-6A Bit. '
         'ANWR 16 bits Continuous ANWR(EN,s1,s2,n,d); '
         'ANWRP 16 bits Pulse ANWRP(EN,s1,s2,n,d);'),
    ])

    records = [_record('ANRD'), _record('ANWR', 425)]
    for record in records:
        for mark in record['applicability']['model_marks']:
            if mark['model'] in {'FXU', 'FX2C'}:
                mark['maximum_version_exclusive'] = '3.30'

    facts = {}
    for model in ('FXU', 'FX2C'):
        for opcode, form, parameters in (
                ('ANRD', 'continuous', [
                    ('EN', 'BOOL', 'input', 'execution_condition'),
                    ('s', 'BOOL', 'input', None), ('n', 'ANY16', 'input', None),
                    ('d1', 'BOOL', 'output', None), ('d2', 'ANY16', 'output', None)]),
                ('ANRDP', 'pulse', [
                    ('EN', 'BOOL', 'input', 'execution_condition'),
                    ('s', 'BOOL', 'input', None), ('n', 'ANY16', 'input', None),
                    ('d1', 'BOOL', 'output', None), ('d2', 'ANY16', 'output', None)]),
                ('ANWR', 'continuous', [
                    ('EN', 'BOOL', 'input', 'execution_condition'),
                    ('s1', 'ANY16', 'input', None), ('s2', 'BOOL', 'input', None),
                    ('n', 'ANY16', 'input', None), ('d', 'BOOL', 'output', None)]),
                ('ANWRP', 'pulse', [
                    ('EN', 'BOOL', 'input', 'execution_condition'),
                    ('s1', 'ANY16', 'input', None), ('s2', 'BOOL', 'input', None),
                    ('n', 'ANY16', 'input', None), ('d', 'BOOL', 'output', None)])):
            page = 423 if opcode.startswith('ANRD') else 425
            args = ','.join(item[0] for item in parameters)
            source_ref = {'id': f'chunk:fxcpu_basic_applied_m:{page}',
                          'manual_id': 'fxcpu_basic_applied_m',
                          'manual_number': 'JY997D34701', 'revision': 'M',
                          'pdf_pages': [page]}
            signature = {
                'id': 'authored:applied:st_signature@fxcpu_basic_applied_m',
                'dimension': 'parameters.st_signature',
                'value': {
                    'name': opcode, 'call_form': opcode,
                    'documented_form': f'{opcode}({args})', 'call_kind': 'statement',
                    'enable': True, 'available': True,
                    'parameters': [{'name': name, 'type': type_, 'direction': direction,
                                    **({'role': role} if role else {})}
                                   for name, type_, direction, role in parameters],
                    'returns': {'role': 'not_assigned_in_documented_statement'},
                    'execution_output': {'name': 'ENO', 'type': 'BOOL', 'direction': 'output'}},
                'status': 'source_verified', 'verification': 'source_checked',
                'sources': [source_ref],
                'scope': {'models': [model], 'forms': [opcode], 'languages': ['st']},
                'conditions': [{'context': 'cpu_version', 'maximum_exclusive': '3.30'}],
            }
            facts[(model, opcode)] = [signature]

    assert _repair_m_anrd_anwr_cpu_availability(records, facts) == 40
    added = _repair_m_anrd_anwr_call_constraints(source, records, facts)
    source.close()
    assert added == 12

    for model in ('FXU', 'FX2C'):
        for opcode in ('ANRD', 'ANRDP', 'ANWR', 'ANWRP'):
            owner = facts[(model, opcode)]
            resolution = resolve_structured_call_contract(owner, opcode=opcode, model=model)
            assert resolution.accepted, (model, opcode, resolution.refusal_reasons)
            requirements = next(fact for fact in owner
                                if fact['dimension'] == 'constraints.documented_requirements')
            io = requirements['value']['io']
            assert requirements['depends_on'] == [
                'authored:applied:st_signature@fxcpu_basic_applied_m']
            if opcode.startswith('ANRD'):
                ranges = next(fact for fact in owner if fact['dimension'] == 'constraints.numeric_ranges')
                assert ranges['value']['ranges'] == [
                    {'binding': {'name': 'n'}, 'minimum': 10, 'maximum': 13}]
                assert ranges['depends_on'] == [
                    'authored:applied:st_signature@fxcpu_basic_applied_m']
                assert io == [
                    {'binding': {'name': 's'}, 'direction': 'input', 'device_prefix': 'X', 'points': 16},
                    {'binding': {'name': 'd1'}, 'direction': 'output', 'device_prefix': 'Y', 'points': 8}]
            else:
                assert not any(fact['dimension'] == 'constraints.numeric_ranges' for fact in owner)
                assert io == [
                    {'binding': {'name': 's2'}, 'direction': 'input', 'device_prefix': 'X'},
                    {'binding': {'name': 'd'}, 'direction': 'output', 'device_prefix': 'Y'}]
                assert all('points' not in item for item in io)

    anrd = facts[('FXU', 'ANRD')]
    without_io = [fact for fact in anrd
                  if fact['dimension'] != 'constraints.documented_requirements']
    refused = resolve_structured_call_contract(without_io, opcode='ANRD', model='FXU')
    assert not refused.accepted and 'fx_analog_constraint_missing_or_ambiguous:constraints.documented_requirements' \
        in refused.refusal_reasons
    without_range = [fact for fact in anrd if fact['dimension'] != 'constraints.numeric_ranges']
    refused = resolve_structured_call_contract(without_range, opcode='ANRD', model='FXU')
    assert not refused.accepted and 'fx_analog_constraint_missing_or_ambiguous:constraints.numeric_ranges' \
        in refused.refusal_reasons

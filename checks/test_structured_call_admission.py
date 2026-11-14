import copy
import pytest

from knowledge.instruction_compiler import refresh_reviewed_structured_call_admission
from plc.instructions import InstructionRegistry, InstructionSpec
from test_structured_call_contract import MODEL, abs_facts, abs_e_facts, fx_k_concat_facts


def _start_data_facts(opcode):
    from test_structured_call_contract import fx_fact
    result_type = {'GET_BOOL_ADDR': 'BOOL', 'GET_INT_ADDR': 'INT', 'GET_WORD_ADDR': 'WORD'}[opcode]
    page = {'GET_BOOL_ADDR': 156, 'GET_INT_ADDR': 157, 'GET_WORD_ADDR': 158}[opcode]
    source = dict(opcode=opcode, model='FX3U', pages=(page,))
    inputs = (['BIT', 'BIT_STRING'] if result_type == 'BOOL' else
              ['INT', 'DINT', 'WORD', 'REAL', 'STRING', 'TIME', 'BITARR_INT', 'BITARR_DINT',
               'BITARR_WORD', 'BITARR_DWORD', 'BITARR_REAL', 'BITARR_TIME'])
    rows = [fx_fact('addr:cpu', 'constraints.cpu_availability',
                    {'available': True, 'model_states': {'FX3U': 'supported'}}, **source),
            fx_fact('addr:types', 'constraints.accepted_input_types',
                    {'input': inputs, 'output': result_type,
                     'literal_or_expression_support': 'not_stated'}, **source)]
    if result_type != 'BOOL':
        rows.append(fx_fact('addr:labels', 'constraints.label_requirement',
            {'applies_to': 'array_and_32bit_data', 'sixteen_bit_device_directly': False,
             'global_label_required': True, 'direct_32bit_counter': True}, **source))
    signature = fx_fact('addr:signature', 'parameters.st_signature', {
        'name': opcode, 'call_kind': 'function', 'enable': False, 'instance_required': False,
        'documented_call': opcode + '(s)', 'argument_order': ['s'], 'arguments_are_named': False,
        'call_form_verification': 'documented_arguments_verified',
        'parameters': [{'name': 's', 'type': {'flexible_type': 'ANY'}, 'direction': 'input'}],
        'returns': {'name': 'd', 'type': result_type, 'role': 'function_result',
                    'result_semantics': 'start_data_reference', 'address_source_parameter': 's'},
    }, **source)
    signature['depends_on'] = [r['id'] for r in rows]
    rows.append(signature)
    return rows


@pytest.mark.parametrize('change', ['none', 'missing_domain', 'no_dependency', 'wrong_page',
                                   'wrong_owner', 'no_result_semantics', 'unknown_domain',
                                   'missing_labels', 'weakened_labels'])
def test_reference_any_requires_own_source_domain_and_label_contract(change):
    from plc.structured_call_contract import resolve_structured_call_contract
    facts = _start_data_facts('GET_INT_ADDR')
    if change == 'missing_domain':
        facts.pop(1)
    elif change == 'no_dependency':
        facts[-1]['depends_on'].remove('addr:types')
    elif change == 'wrong_page':
        facts[1]['sources'][0]['pdf_pages'] = [156]
    elif change == 'wrong_owner':
        facts[1]['scope']['forms'] = ['GET_WORD_ADDR']
    elif change == 'no_result_semantics':
        facts[-1]['value']['returns'].pop('result_semantics')
    elif change == 'unknown_domain':
        facts[1]['value']['input'].append('ANYTHING')
    elif change == 'missing_labels':
        facts.pop(2)
    elif change == 'weakened_labels':
        facts[2]['value']['global_label_required'] = False
    result = resolve_structured_call_contract(facts, opcode='GET_INT_ADDR', model='FX3U')
    assert result.accepted is (change == 'none'), result.refusal_reasons
    if result.accepted:
        from shared.model_context import model_knowledge_value
        projected = model_knowledge_value(result.contract.as_mapping())
        reference = projected['parameters'][0]['input_reference']
        assert reference['literal_or_expression_support'] == 'not_stated'
        assert reference['label_requirement']['global_label_required'] is True
        assert reference['label_requirement']['direct_32bit_counter'] is True
        assert projected['return_binding']['kind'] == 'start_data_reference'


@pytest.mark.parametrize('opcode,result_type', [('GET_INT_ADDR', 'INT'), ('GET_WORD_ADDR', 'WORD')])
@pytest.mark.parametrize('data_type,global_label,expression,status', [
    ('INT', False, 'input', 'passed'), ('WORD', False, 'input', 'passed'),
    ('DINT', True, 'input', 'passed'), ('REAL', True, 'input', 'passed'),
    ('TIME', True, 'input', 'passed'), ('STRING[12]', False, 'input', 'passed'),
    ('ARRAY[0..2] OF DWORD', True, 'input', 'passed'),
    ('ARRAY[0..2] OF INT', False, 'input', 'failed'),
    ('DINT', False, 'input', 'failed'), ('DWORD', True, 'input', 'failed'),
    ('ARRAY[0..2] OF BOOL', True, 'input', 'failed'),
    ('INT', False, 'INT#1', 'unverified'), ('INT', False, 'input + INT#1', 'unverified'),
])
def test_start_data_reference_preserves_type_scope_and_global_label_rules(
        opcode, result_type, data_type, global_label, expression, status):
    from test_structured_call_contract import inspect_fx
    context = {'globals': [{'declarations': [{'name': 'input', 'data_type': data_type,
                'class_name': 'VAR_GLOBAL'}]}]} if global_label else None
    declaration = '' if global_label else 'input : ' + data_type + ';'
    source = (f'PROGRAM Main VAR {declaration} result : {result_type}; END_VAR '
              f'result := {opcode}({expression}); END_PROGRAM')
    result, _ = inspect_fx(opcode, 'FX3U', _start_data_facts(opcode), source, project_context=context)
    assert result['status'] == status, result['findings']
    if status == 'passed':
        assert result['calls'][0]['return_binding'] == {
            'kind': 'start_data_reference', 'source_parameter': 's', 'result_type': result_type}
    else:
        assert not result['calls']


@pytest.mark.parametrize('data_type,expression,status', [
    ('BOOL', 'input', 'passed'), ('WORD', 'input', 'unverified'),
    ('DWORD', 'input', 'unverified'), ('ARRAY[0..2] OF BOOL', 'input', 'unverified'),
    ('INT', 'input', 'failed'), ('BOOL', 'TRUE', 'unverified'),
])
def test_bool_address_does_not_invent_bit_string_width_or_array_domain(data_type, expression, status):
    from test_structured_call_contract import inspect_fx
    source = (f'PROGRAM Main VAR input : {data_type}; result : BOOL; END_VAR '
              f'result := GET_BOOL_ADDR({expression}); END_PROGRAM')
    result, _ = inspect_fx('GET_BOOL_ADDR', 'FX3U', _start_data_facts('GET_BOOL_ADDR'), source)
    assert result['status'] == status, result['findings']
    if status == 'passed':
        from plc.structured_call_contract import resolve_structured_call_contract
        from shared.model_context import model_knowledge_value
        contract = resolve_structured_call_contract(
            _start_data_facts('GET_BOOL_ADDR'), opcode='GET_BOOL_ADDR', model='FX3U').contract
        reference = model_knowledge_value(contract.parameters[0])['input_reference']
        assert reference['array_element_types'] == []
        assert reference['scalar_types'] == ['BOOL']
        assert reference['literal_or_expression_support'] == 'not_stated'


@pytest.mark.parametrize('token,status', [('C200', 'passed'), ('C0', 'passed'),
                                         ('D0', 'passed'), ('M10', 'failed')])
def test_address_input_uses_counter_data_width_instead_of_contact_type(token, status):
    from test_structured_call_contract import inspect_fx
    source = (f'PROGRAM Main VAR result : INT; END_VAR '
              f'result := GET_INT_ADDR({token}); END_PROGRAM')
    result, _ = inspect_fx('GET_INT_ADDR', 'FX3U', _start_data_facts('GET_INT_ADDR'), source)
    assert result['status'] == status, result['findings']


def _counter_instance_facts():
    from test_structured_call_contract import fx_fact
    model, opcode = 'FX3U', 'CTU'
    source = dict(opcode=opcode, model=model, pages=(246, 247))
    call = 'CTU_Instance(CU:=g_bool1,RESET:=g_bool2,PV:=g_int1,Q:=g_bool3,CV:=g_int2);'
    parameters = [{'name': name, 'type': {'kind': 'bool'} if token == 'BOOL' else
                   {'kind': 'int', 'bits': 16, 'signed': True}, 'direction': direction}
                  for name, token, direction in [('CU', 'BOOL', 'input'),
                      ('RESET', 'BOOL', 'input'), ('PV', 'INT', 'input'),
                      ('Q', 'BOOL', 'output'), ('CV', 'INT', 'output')]]
    return [
        fx_fact('counter:cpu', 'constraints.cpu_availability',
            {'available': True, 'model_states': {model: 'supported'}}, **source),
        fx_fact('counter:form', 'identity.form',
            {'category': 'function_block', 'call_basis': 'instance_call'}, **source),
        fx_fact('counter:signature', 'parameters.st_signature', {
            'name': opcode, 'call_kind': 'function_block', 'instance_required': True,
            'enable': False, 'arguments_are_named': True,
            'call_form_verification': 'documented_arguments_verified',
            'argument_order': [row['name'] for row in parameters], 'parameters': parameters,
            'returns': {'role': 'no_return_value', 'outputs_via_arguments': True},
            'documented_call': call,
            'source_witness': {'instance_name': 'CTU_Instance', 'instance_type': opcode,
                'manual_id': 'fxcpu_application_k', 'revision': 'K', 'pdf_pages': [247],
                'quote': {'instance_type_label': 'CTU\nCTU_Instance', 'call': call}},
        }, **source),
    ]


@pytest.mark.parametrize('change', ['none', 'wrong_page', 'wrong_instance_type',
                                   'function_return', 'positional', 'negative_cpu', 'unresolved'])
def test_counter_instance_contract_keeps_source_and_instance_boundaries(change):
    from plc.structured_call_contract import resolve_structured_call_contract
    facts = _counter_instance_facts()
    value = facts[-1]['value']
    if change == 'wrong_page':
        value['source_witness']['pdf_pages'] = [240]
    elif change == 'wrong_instance_type':
        value['source_witness']['instance_type'] = 'CTD'
    elif change == 'function_return':
        value['returns'] = {'type': {'kind': 'bool'}, 'role': 'function_result'}
    elif change == 'positional':
        value['documented_call'] = 'CTU_Instance(g_bool1,g_bool2,g_int1,g_bool3,g_int2);'
        value['source_witness']['quote']['call'] = value['documented_call']
    elif change == 'negative_cpu':
        facts[0]['value'].update(available=False, model_states={'FX3U': 'unsupported'})
    elif change == 'unresolved':
        value['unresolved_items'] = ['source_argument_binding']
    result = resolve_structured_call_contract(facts, opcode='CTU', model='FX3U')
    assert result.accepted is (change == 'none'), result.refusal_reasons
    if result.accepted:
        assert result.contract.instance_required and result.contract.returns is None
        assert result.contract.call_kind == 'function_block'
        assert [(p['name'], p['direction']) for p in result.contract.parameters][-2:] == [
            ('Q', 'output'), ('CV', 'output')]


@pytest.mark.parametrize('case', ['valid', 'direct_type', 'assigned_result',
                                 'positional', 'wrong_input', 'constant_output', 'option_off'])
def test_counter_instance_static_binding_uses_current_library_and_named_actuals(monkeypatch, case):
    from plc import st_semantics
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.st_validation import inspect_st_program
    from knowledge.instruction_compiler import _fbd_graphic_context
    registry = InstructionRegistry([])
    monkeypatch.setattr(registry, 'language_feature', DEFAULT_INSTRUCTION_REGISTRY.language_feature)
    entry = {'opcode': 'CTU', 'target_model': 'FX3U', 'call_available': False,
             'facts': _counter_instance_facts()}
    assert refresh_reviewed_structured_call_admission(entry, registry)
    registry._definition_owners[('mitsubishi', 'CTU', 'FX3U')] = entry
    monkeypatch.setattr(st_semantics, 'DEFAULT_INSTRUCTION_REGISTRY', registry)
    call = 'counter(CU:=TRUE,RESET:=FALSE,PV:=INT#10,Q:=q,CV:=cv);'
    if case == 'direct_type':
        call = call.replace('counter(', 'CTU(')
    elif case == 'assigned_result':
        call = 'q := ' + call
    elif case == 'positional':
        call = 'counter(TRUE,FALSE,INT#10,q,cv);'
    elif case == 'wrong_input':
        call = call.replace('CU:=TRUE', 'CU:=INT#1')
    elif case == 'constant_output':
        call = call.replace('Q:=q', 'Q:=TRUE')
    source = 'PROGRAM Main VAR counter : CTU; q : BOOL; cv : INT; END_VAR ' + call + ' END_PROGRAM'
    result = inspect_st_program(source, plc_model='FX3U',
        project_context={'configuration': {'compile_options': {'allow_fb_output_arguments': case != 'option_off'}}},
        callable_sources=_fbd_graphic_context('FX3U').sources)
    assert result['status'] == ('passed' if case == 'valid' else 'failed'), result['findings']
    if case == 'valid':
        assert result['calls'][0]['kind'] == 'structured_function_block'
        assert result['calls'][0]['instance_name'].upper() == 'COUNTER'
        assert result['calls'][0]['definition'] == 'CTU'
        call_contract = registry.describe_call('CTU', cpu='FX3U')
        assert call_contract['st_call']['instance_required'] is True
        assert call_contract['st_call']['returns'] is None
        assert call_contract['st_signature'].startswith('<declared_instance>(')


@pytest.mark.parametrize('case,expected', [
    ('supported', 'passed'), ('wrong_cpu', 'unverified'), ('missing_dependency', 'unverified'),
    ('conflict', 'unverified'), ('missing_condition', 'unverified'), ('met_condition', 'passed'),
])
def test_st_declared_type_uses_source_applicability_without_inventing_native_code(monkeypatch, case, expected):
    from gxw.declarations import BASIC_TYPES
    from plc import st_semantics
    from plc.st_validation import inspect_st_program

    row = {'id': 'reviewed:datatype:lreal', 'dimension': 'constraints.datatype_availability',
           'value': {'data_type': 'LREAL', 'available': True},
           'status': 'source_verified', 'verification': 'source_checked',
           'sources': [{'manual_id': 'own-types', 'revision': 'A', 'pdf_pages': [1]}],
           'scope': {'models': [MODEL], 'forms': ['ST.DATATYPES'], 'languages': ['st']}}
    rows, context = [row], {}
    if case == 'wrong_cpu':
        row['scope']['models'] = ['L02']
    elif case == 'missing_dependency':
        row['depends_on'] = ['missing:cpu']
    elif case == 'conflict':
        other = copy.deepcopy(row)
        other['id'], other['value']['available'] = 'reviewed:datatype:denial', False
        rows.append(other)
    elif case in {'missing_condition', 'met_condition'}:
        row['conditions'] = [{'context': 'type_profile', 'equals': 'double_precision'}]
        if case == 'met_condition':
            context['type_profile'] = 'double_precision'
    registry = InstructionRegistry([])
    registry._language_features = {'ST.DATATYPES': {'target': 'ST.DATATYPES',
        'language': 'st', 'models': [MODEL], 'facts': rows}}
    monkeypatch.setattr(st_semantics, 'DEFAULT_INSTRUCTION_REGISTRY', registry)
    native_codes = dict(BASIC_TYPES)
    result = inspect_st_program('PROGRAM Main VAR x : LREAL; END_VAR x := LREAL#1.0; END_PROGRAM',
                                plc_model=MODEL, project_context=context)
    assert result['status'] == expected, result['findings']
    assert any(f['code'] == 'ST_TYPE_COVERAGE' for f in result['findings']) is (expected != 'passed')
    assert BASIC_TYPES == native_codes and 'LREAL' not in BASIC_TYPES


def test_compiler_admits_exact_st_contract_without_registering_native_forms():
    registry = InstructionRegistry([])
    for opcode, facts in [('ABS', abs_facts()), ('ABS_E', abs_e_facts())]:
        entry = {'opcode': opcode, 'target_model': MODEL, 'call_available': False,
                 'facts': facts, 'gaps': ['manual_only_not_admitted', 'runtime_unverified']}
        original_facts = copy.deepcopy(facts)
        assert refresh_reviewed_structured_call_admission(entry, registry)
        assert entry['call_available'] is True
        assert entry['call_admission_details']['interfaces'] == ['st']
        assert entry['support_status'] == 'reviewed_st_signature'
        assert entry['facts'] == original_facts
        assert entry['gaps'] == ['runtime_unverified']
        assert registry.resolve_catalog_form(opcode, cpu=MODEL) is None
        assert opcode not in registry.known_mnemonics()


def test_incomplete_source_is_not_enabled_by_structured_refresh():
    registry = InstructionRegistry([])
    facts = abs_facts()
    next(f for f in facts if f['dimension'] == 'parameters.st_signature')['value']['unresolved_items'] = ['return_type']
    entry = {'opcode': 'ABS', 'target_model': MODEL, 'call_available': False, 'facts': facts}
    before = copy.deepcopy(entry)
    assert not refresh_reviewed_structured_call_admission(entry, registry)
    assert entry == before


def test_st_admission_of_catalogue_name_does_not_enable_native_call():
    from plc.target_capabilities import apply_owner_admission
    registry = InstructionRegistry([InstructionSpec('ABS', cpu_support=frozenset({MODEL}))])
    entry = {'opcode': 'ABS', 'target_model': MODEL, 'call_available': False, 'facts': abs_facts()}
    assert refresh_reviewed_structured_call_admission(entry, registry)
    registry._definition_owners[('mitsubishi', 'ABS', MODEL)] = entry
    assert registry.resolve_catalog_form('ABS', cpu=MODEL) is not None
    assert registry.resolve_form('ABS', cpu=MODEL) is None
    result = apply_owner_admission({'available': False, 'support_status': 'unknown'}, 'ABS', MODEL, registry=registry)
    assert result['available'] is True
    assert result['interfaces'] == ['st']
    assert 'inconsistent' not in result['owner_admission']
    assert result['call_contract']['st_signature'] == 'ABS(s)'
    assert 'ladder_signature' not in result['call_contract']
    contract = registry.describe_contract('ABS', cpu=MODEL)
    assert contract['interfaces'] == ['st']
    assert contract['contract_level'] == 'signature_verified'
    assert 'native_operand_order' not in contract


def test_default_model_context_receives_own_st_contract(monkeypatch):
    from application.capability_context import _call_contract_units
    import plc.instructions as instructions
    registry = InstructionRegistry([])
    entry = {'opcode': 'ABS_E', 'target_model': MODEL, 'call_available': False, 'facts': abs_e_facts()}
    assert refresh_reviewed_structured_call_admission(entry, registry)
    registry._definition_owners[('mitsubishi', 'ABS_E', MODEL)] = entry
    monkeypatch.setattr(instructions, 'DEFAULT_INSTRUCTION_REGISTRY', registry)
    def unexpected_catalogue_scan():
        raise AssertionError('A complete literal call does not require a catalogue scan')
    monkeypatch.setattr(registry, 'known_mnemonics', unexpected_catalogue_scan)
    units = _call_contract_units([], plc_model=MODEL, explicit=[{'opcode': 'ABS_E'}])
    assert len(units) == 1
    assert 'ABS_E(EN, s, d)' in units[0][1]
    assert 'BOOL' in units[0][1]
    assert 'ladder_signature' not in units[0][1]
    entry['facts'] = []
    assert registry.describe_call('ABS_E', cpu=MODEL) == {}


def test_default_model_context_preserves_variadic_input_range(monkeypatch):
    from application.capability_context import _call_contract_units
    import plc.instructions as instructions

    registry = InstructionRegistry([])
    monkeypatch.setattr(instructions, 'DEFAULT_INSTRUCTION_REGISTRY', registry)
    for opcode in ('CONCAT', 'CONCAT_E'):
        entry = {'opcode': opcode, 'target_model': 'FX3U', 'call_available': False,
                 'facts': fx_k_concat_facts(opcode=opcode, model='FX3U')}
        assert refresh_reviewed_structured_call_admission(entry, registry)
        registry._definition_owners[('mitsubishi', opcode, 'FX3U')] = entry
        call = registry.describe_call(opcode, cpu='FX3U')['st_call']
        assert call['variadic_parameter_name'] == '_IN'
        assert call['minimum_variadic_arguments'] == 2
        assert call['maximum_variadic_arguments'] == 28
        assert call['argument_binding'] == 'positional'
        units = _call_contract_units([], plc_model='FX3U', explicit=[{'opcode': opcode}])
        assert len(units) == 1
        visible = units[0][1].replace(' ', '').replace('\n', '')
        assert '"minimum_variadic_arguments":2' in visible
        assert '"maximum_variadic_arguments":28' in visible
        assert '"argument_binding":"positional"' in visible


def test_native_and_st_contracts_keep_independent_admission_and_revocation():
    fields = ('form_identity', 'cpu_applicability', 'arity', 'operand_order', 'operand_roles', 'operand_types')
    registry = InstructionRegistry([InstructionSpec('ABS', cpu_support=frozenset({MODEL}),
        verified_fields=fields, contract_sources=({'manual_id': 'own-native', 'revision': 'A', 'pdf_page': 1},))])
    entry = {'opcode': 'ABS', 'target_model': MODEL, 'call_available': True,
             'facts': abs_facts(), 'support_status': 'reviewed_native_signature'}
    assert refresh_reviewed_structured_call_admission(entry, registry)
    assert entry['support_status'] == 'reviewed_native_signature'
    assert entry['call_admission_details']['interfaces'] == ['ladder', 'st']
    assert entry['call_admission_details']['structured_call']['available'] is True
    registry._definition_owners[('mitsubishi', 'ABS', MODEL)] = entry
    assert registry.resolve_form('ABS', cpu=MODEL) is not None
    call = registry.describe_call('ABS', cpu=MODEL)
    assert call['st_signature'] == 'ABS(s)'
    assert call['st_contract_verified'] is True
    assert call['interfaces'] == ['ladder', 'st']
    description = registry.describe_contract('ABS', cpu=MODEL)
    assert description['st_call'] == call['st_call']
    assert description['native_operand_order'] == []
    entry['facts'] = []
    assert not refresh_reviewed_structured_call_admission(entry, registry)
    assert entry['call_available'] is True
    assert entry['support_status'] == 'reviewed_native_signature'
    assert entry['call_admission_details']['interfaces'] == ['ladder']
    native_call = registry.describe_call('ABS', cpu=MODEL)
    assert native_call['interfaces'] == ['ladder']
    assert native_call['native_contract_verified'] is True
    assert 'st_signature' not in native_call
    assert registry.resolve_form('ABS', cpu=MODEL) is not None


def test_default_model_context_receives_reviewed_native_only_contract(monkeypatch):
    import json
    import plc.instructions as instructions
    from application.capability_context import _call_contract_units
    from plc.instructions import OperandRole, OperandSpec

    registry = InstructionRegistry([InstructionSpec('NATIVE_STRING',
        cpu_support=frozenset({'FX3U'}), native_operand_order=('S', 'D'),
        operands=(OperandSpec('S', data_type='string', optional=False,
                              device_prefixes=('D', 'R'), digit_groups_max=4,
                              address_forms=('buffer_memory',)),
                  OperandSpec('D', role=OperandRole.WRITE, data_type='word', optional=False)),
        notes='STRING head devices; FX3U >=2.20; K1-K4 for 16-bit data.',
        contract_sources=({'manual_id': 'own-native', 'revision': 'A', 'pdf_page': 1},))])
    entry = {'opcode': 'NATIVE_STRING', 'target_model': 'FX3U', 'call_available': True,
             'support_status': 'reviewed_native_signature', 'facts': [],
             'call_admission_details': {'interfaces': ['ladder']}}
    registry._definition_owners[('mitsubishi', 'NATIVE_STRING', 'FX3U')] = entry
    monkeypatch.setattr(instructions, 'DEFAULT_INSTRUCTION_REGISTRY', registry)
    units = _call_contract_units([], plc_model='FX3U', explicit=[{'opcode': 'NATIVE_STRING'}])
    assert len(units) == 1
    payload = json.loads(units[0][1].split('\n', 1)[1].rsplit('\n', 1)[0])
    assert payload['ladder_signature'] == 'NATIVE_STRING S D'
    assert payload['interfaces'] == ['ladder']
    assert payload['parameters'][0]['type'] == 'string'
    assert payload['parameters'][0]['digit_groups_max'] == 4
    assert payload['parameters'][0]['device_prefixes'] == ['D', 'R']
    assert payload['parameters'][0]['address_forms'] == ['buffer_memory']
    assert payload['parameters'][1]['type'] == 'word'
    assert payload['parameters'][1]['role'] == 'write'
    assert payload['conditions_evaluated'] is False
    assert 'FX3U >=2.20' in payload['notes']
    assert 'st_signature' not in payload and 'st_call' not in payload
    entry['call_available'] = False
    assert registry.describe_call('NATIVE_STRING', cpu='FX3U') == {}
    assert registry.describe_call('NATIVE_STRING', cpu='FX3UC') == {}


def test_expression_token_lookup_uses_admitted_owner_and_rejects_alias_ambiguity():
    registry = InstructionRegistry([])
    entry = {'vendor': 'mitsubishi', 'opcode': 'POWER', 'target_model': 'FX3U',
             'call_available': True, 'facts': [], 'call_admission_details': {
                 'interfaces': ['st'], 'structured_expression': {
                     'available': True, 'call_kind': 'expression', 'operator': '**',
                     'owner_opcode': 'POWER'}}}
    registry._definition_owners[('mitsubishi', 'POWER', 'FX3U')] = entry
    assert registry.expression_entry('**', cpu='FX3U')['opcode'] == 'POWER'
    assert registry.expression_entry('**', cpu='FX1N') is None
    entry['call_available'] = False
    assert registry.expression_entry('**', cpu='FX3U') is None
    entry['call_available'] = True
    other = copy.deepcopy(entry)
    other['opcode'] = 'OTHER_POWER'
    other['call_admission_details']['structured_expression']['owner_opcode'] = 'OTHER_POWER'
    registry._definition_owners[('mitsubishi', 'OTHER_POWER', 'FX3U')] = other
    assert registry.expression_entry('**', cpu='FX3U') is None


@pytest.mark.parametrize('with_st', [False, True])
def test_default_fbd_contract_preserves_conditions_and_independent_st_interface(monkeypatch, with_st):
    import json
    import plc.instructions as instructions
    from application.capability_context import _call_contract_units
    from test_structured_call_contract import fact

    registry = InstructionRegistry([])
    condition = {'context': 'serial_verified', 'equals': True}
    cpu = fact('fbd:cpu', 'constraints.cpu_availability', {'available': True,
        'model_states': {MODEL: 'supported'}}, opcode='ABS', languages=('fbd',), conditions=[condition])
    signature = fact('fbd:signature', 'parameters.fbd_signature', {
        'available': True, 'name': 'ABS', 'call_kind': 'instruction_block',
        'representation': 'fbd_instruction_block', 'instance_required': False,
        'pins': [{'name': 's', 'direction': 'input', 'type': 'ANY16', 'role': 'source',
                  'word_device_layout': {'word_count': 3}},
                 {'name': 'ENO', 'direction': 'output', 'type': 'BOOL', 'role': 'enable_output'}]},
        opcode='ABS', languages=('fbd',), depends_on=[cpu['id']])
    entry = {'opcode': 'ABS', 'target_model': MODEL, 'call_available': False,
             'facts': [*(abs_facts() if with_st else []), cpu, signature]}
    if with_st:
        assert refresh_reviewed_structured_call_admission(entry, registry)
    else:
        entry.update(call_available=True, support_status='reviewed_fbd_signature',
                     call_admission_details={'interfaces': []})
    details = entry['call_admission_details']
    details['interfaces'].append('fbd')
    details['fbd_call'] = {'available': True, 'call_kind': 'instruction_block',
                           'graphical_interface_verified': True}
    registry._definition_owners[('mitsubishi', 'ABS', MODEL)] = entry
    monkeypatch.setattr(instructions, 'DEFAULT_INSTRUCTION_REGISTRY', registry)
    call = registry.describe_call('ABS', cpu=MODEL)
    assert set(call['interfaces']) == ({'st', 'fbd'} if with_st else {'fbd'})
    contract = registry.describe_contract('ABS', cpu=MODEL)
    assert contract['contract_level'] == 'signature_verified'
    assert ('st_call' in contract) is with_st
    units = _call_contract_units([], plc_model=MODEL, explicit=[{'opcode': 'ABS'}])
    payload = json.loads(units[0][1].split('\n', 1)[1].rsplit('\n', 1)[0])
    fbd = payload['fbd_contract']
    assert fbd['conditions'] == [condition] and fbd['conditions_evaluated'] is False
    assert fbd['pins'][0]['word_device_layout'] == {'word_count': 3}
    assert 'ladder_signature' not in call
    details['fbd_call']['graphical_interface_verified'] = False
    assert 'fbd_contract' not in registry.describe_call('ABS', cpu=MODEL)


@pytest.mark.parametrize('change,accepted', [
    ('none', True), ('short', False), ('no_address', False), ('member', False),
    ('array', False), ('wrong_type', False), ('missing_capacity', False),
])
def test_fbd_source_string_layout_requires_complete_bound_label(change, accepted):
    from gxw.fbd_validation import _source_label_device
    value = {'kind': 'label', 'data_type': 'STRING[6]', 'string_capacity': 6,
             'storage': {'device': 'D100'}, 'member_path': [], 'shape': []}
    pin = {'type': 'STRING', 'declared_capacity': 6}
    if change == 'short': value.update(data_type='STRING[4]', string_capacity=4)
    elif change == 'no_address': value['storage'] = {}
    elif change == 'member': value['member_path'] = [{'name': 'field'}]
    elif change == 'array': value['shape'] = [[0, 3]]
    elif change == 'wrong_type': value.update(data_type='DWORD', string_capacity=None)
    elif change == 'missing_capacity': pin.pop('declared_capacity')
    assert (_source_label_device(value, pin, 3) == 'D100') is accepted


@pytest.mark.parametrize('output_array', [False, True])
@pytest.mark.parametrize('case,code', [
    ('valid', None), ('boundary', None), ('scalar', 'ST_ARRAY_REFERENCE'),
    ('variable_count', 'ST_ARRAY_LENGTH'), ('invalid_count', 'ST_ARRAY_LENGTH'),
    ('overrun', 'ST_ARRAY_EXTENT'), ('wrong_element', 'ST_TYPE'),
])
def test_bit_array_call_checks_reference_constant_extent_and_result_binding(output_array, case, code):
    from test_structured_call_contract import fx_k_typed_function_facts, inspect_fx

    binding = {'argument_passing': 'array_element_reference', 'array_element_reference': {
        'element_type': {'kind': 'bool'}, 'start': 'selected_array_element',
        'extent': {'parameter': 'n', 'unit': 'logical_elements', 'constant_only': True,
                   'allowed_counts': [4, 8, 12, 16]}}}
    opcode = 'INT_TO_BITARR' if output_array else 'BITARR_TO_INT'
    facts = fx_k_typed_function_facts(opcode, 'FX3U', parameter_names=['s', 'n'],
        parameter_types=['INT' if output_array else 'BOOL', 'INT'],
        return_type='BOOL' if output_array else 'INT', pages=[144 if output_array else 140])
    signature = next(f['value'] for f in facts if f['dimension'] == 'parameters.st_signature')
    if output_array:
        signature['returns'].update(copy.deepcopy(binding))
    else:
        signature['parameters'][0].update(copy.deepcopy(binding))
    array_type = 'INT' if case == 'wrong_element' else 'BOOL'
    reference = 'flag' if case == 'scalar' else 'bits[28]' if case == 'boundary' else 'bits[29]' if case == 'overrun' else 'bits[0]'
    count = 'count' if case == 'variable_count' else 'INT#3' if case == 'invalid_count' else 'INT#4'
    target, source = (reference, 'INT#7') if output_array else ('result', reference)
    program = (f'PROGRAM Main VAR bits : ARRAY[0..31] OF {array_type}; '
               'flag : BOOL; count : INT; result : INT; END_VAR '
               f'{target} := {opcode}({source}, {count}); END_PROGRAM')
    result, entry = inspect_fx(opcode, 'FX3U', facts, program)
    if code is None:
        assert result['status'] == 'passed', result['findings']
        if output_array:
            registry = InstructionRegistry([])
            registry._definition_owners[('mitsubishi', opcode, 'FX3U')] = entry
            assert registry.describe_call(opcode, cpu='FX3U')['st_call']['return_binding'] == binding
    else:
        assert result['status'] == 'failed', result['findings']
        assert any(f['code'] == code for f in result['findings'])


@pytest.mark.parametrize('generic,alternatives,destination,passed', [
    ('ANY16', ('INT', 'WORD'), 'INT', True),
    ('ANY16', ('INT', 'WORD'), 'WORD', True),
    ('ANY16', ('INT', 'WORD'), 'DINT', False),
    ('ANY16', ('INT', 'WORD'), 'REAL', False),
    ('ANY16', ('INT', 'WORD'), 'BOOL', False),
    ('ANY16', (), 'INT', False),
    ('ANY32', ('DINT', 'DWORD'), 'DINT', True),
    ('ANY32', ('DINT', 'DWORD'), 'DWORD', True),
    ('ANY32', ('DINT', 'DWORD'), 'INT', False),
    ('ANY32', ('DINT', 'DWORD'), 'REAL', False),
])
def test_output_only_generic_return_binds_only_documented_assignment_types(
        generic, alternatives, destination, passed):
    from test_structured_call_contract import fx_k_typed_function_facts, inspect_fx

    opcode = 'BITARR_TO_INT' if generic == 'ANY16' else 'BITARR_TO_DINT'
    facts = fx_k_typed_function_facts(opcode, 'FX3U', parameter_names=['s', 'n'],
        parameter_types=['BOOL', 'INT'], return_type={'flexible_type': generic}, pages=[140])
    signature = next(f['value'] for f in facts if f['dimension'] == 'parameters.st_signature')
    if alternatives:
        signature['returns']['type_alternatives'] = list(alternatives)
    source = ('PROGRAM Main VAR flag : BOOL; result : ' + destination
              + '; END_VAR result := ' + opcode + '(flag, INT#4); END_PROGRAM')
    result, _ = inspect_fx(opcode, 'FX3U', facts, source)
    assert (result['status'] == 'passed') is passed, result['findings']
    if passed:
        assert result['calls'][0]['return_type'] == destination
        assert result['calls'][0]['source_return_type'] == generic
    else:
        assert any(f['code'] == 'ST_TYPE' for f in result['findings'])


@pytest.mark.parametrize('enabled,destination,alternatives,passed', [
    (False, 'WORD', ('WORD', 'DWORD'), True),
    (False, 'DWORD', ('WORD', 'DWORD'), True),
    (False, 'BOOL', ('WORD', 'DWORD'), False),
    (False, 'INT', ('WORD', 'DWORD'), False),
    (False, 'DINT', ('WORD', 'DWORD'), False),
    (False, 'REAL', ('WORD', 'DWORD'), False),
    (False, 'WORD', (), False),
    (True, 'WORD', ('WORD', 'DWORD'), True),
    (True, 'DWORD', ('WORD', 'DWORD'), True),
    (True, 'BOOL', ('WORD', 'DWORD'), False),
    (True, 'INT', ('WORD', 'DWORD'), False),
    (True, 'DINT', ('WORD', 'DWORD'), False),
])
def test_ql_bcd_output_binds_only_own_documented_word_branches(
        enabled, destination, alternatives, passed):
    from test_structured_call_contract import fact, inspect_fx

    opcode = 'STR_TO_BCD_E' if enabled else 'STR_TO_BCD'
    parameters = [{'name': 's', 'symbol': 's', 'type': 'STRING',
                   'direction': 'input', 'role': 'source'}]
    if enabled:
        parameters = [
            {'name': 'EN', 'symbol': 'EN', 'type': 'BOOL', 'direction': 'input', 'role': 'enable'},
            *parameters,
            {'name': 'd', 'symbol': 'd', 'type': 'ANY_BIT', 'direction': 'output',
             'role': 'result', 'type_alternatives': list(alternatives)},
        ]
    signature = {'name': opcode, 'parameters': parameters, 'available': True,
        'call_kind': 'function', 'instance_required': False, 'enable': enabled,
        'returns': 'BOOL' if enabled else 'ANY_BIT'}
    if not enabled:
        signature['return_type_alternatives'] = list(alternatives)
    facts = [
        fact('bcd:cpu', 'constraints.cpu_availability',
             {'available': True, 'model_states': {MODEL: 'supported'}}, opcode=opcode),
        fact('bcd:signature', 'parameters.st_signature', signature,
             opcode=opcode, depends_on=['bcd:cpu']),
        fact('bcd:arity', 'parameters.arity', {'count_kind': 'ST_call_arguments',
             'minimum': len(parameters), 'maximum': len(parameters)}, opcode=opcode),
        fact('bcd:order', 'parameters.order', {'order_source': 'ST_call_arguments',
             'symbols': [p['name'] for p in parameters]}, opcode=opcode),
    ]
    for item in facts:
        item['sources'][0].update(pdf_pages=[95, 97], source_ref='sh080784engn:N:pages=95,97')
    call = (f'eno := {opcode}(EN := TRUE, s := text, d => result);' if enabled
            else f'result := {opcode}(text);')
    source = (f'PROGRAM Main VAR text : STRING[8]; result : {destination}; eno : BOOL; '
              f'END_VAR {call} END_PROGRAM')
    inspected, _ = inspect_fx(opcode, MODEL, facts, source)
    assert (inspected['status'] == 'passed') is passed, inspected['findings']
    if passed and not enabled:
        assert inspected['calls'][0]['return_type'] == destination
        assert inspected['calls'][0]['source_return_type'] == 'ANY_BIT'
    elif not passed:
        assert any(f['code'] == 'ST_TYPE' for f in inspected['findings'])


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('input_type', ['WORD', 'DWORD', 'BOOL', 'INT', 'DINT', 'REAL'])
def test_partial_source_type_branches_do_not_approve_unresolved_inputs(enabled, input_type):
    from test_structured_call_contract import fact, inspect_fx
    from shared.model_context import model_knowledge_value

    opcode = 'BCD_TO_STR_E' if enabled else 'BCD_TO_STR'
    parameters = [{'name': 's', 'symbol': 's', 'type': 'ANY_BIT',
                   'direction': 'input', 'role': 'source',
                   'source_verified_type_branches': ['WORD', 'DWORD'],
                   'other_type_branches': 'unresolved'}]
    projected = model_knowledge_value(parameters[0])
    assert projected['source_verified_type_branches'] == ['WORD', 'DWORD']
    assert projected['other_type_branches'] == 'unresolved'
    if enabled:
        parameters = [
            {'name': 'EN', 'symbol': 'EN', 'type': 'BOOL', 'direction': 'input', 'role': 'enable'},
            *parameters,
            {'name': 'd', 'symbol': 'd', 'type': 'STRING', 'direction': 'output', 'role': 'result'},
        ]
    facts = [
        fact('bcd:cpu', 'constraints.cpu_availability',
             {'available': True, 'model_states': {MODEL: 'supported'}}, opcode=opcode),
        fact('bcd:signature', 'parameters.st_signature', {
            'name': opcode, 'parameters': parameters, 'available': True,
            'call_kind': 'function', 'instance_required': False, 'enable': enabled,
            'returns': 'BOOL' if enabled else 'STRING'}, opcode=opcode, depends_on=['bcd:cpu']),
        fact('bcd:arity', 'parameters.arity', {'count_kind': 'ST_call_arguments',
             'minimum': len(parameters), 'maximum': len(parameters)}, opcode=opcode),
        fact('bcd:order', 'parameters.order', {'order_source': 'ST_call_arguments',
             'symbols': [p['name'] for p in parameters]}, opcode=opcode),
    ]
    call = (f'eno := {opcode}(EN := TRUE, s := value, d => result);' if enabled
            else f'result := {opcode}(value);')
    source = (f'PROGRAM Main VAR value : {input_type}; result : STRING[8]; eno : BOOL; '
              f'END_VAR {call} END_PROGRAM')
    inspected, _ = inspect_fx(opcode, MODEL, facts, source)
    if input_type in {'WORD', 'DWORD'}:
        assert inspected['status'] == 'passed', inspected['findings']
        assert len(inspected['calls']) == 1
    else:
        assert inspected['status'] == 'unverified', inspected['findings']
        assert inspected['calls'] == []
        assert any(f['code'] == 'ST_TYPE_SOURCE_UNRESOLVED' for f in inspected['findings'])


@pytest.mark.parametrize('opcode', ['MOD', 'XOR'])
def test_expression_only_owner_refuses_function_syntax_before_native_parameters(monkeypatch, opcode):
    from plc import st_semantics
    from plc.st_validation import inspect_st_program

    registry = InstructionRegistry([InstructionSpec(opcode)])
    entry = {'opcode': opcode, 'target_model': MODEL, 'call_available': True,
             'support_status': 'reviewed_st_expression', 'facts': [],
             'call_admission_details': {'interfaces': ['st'], 'structured_expression': {
                 'available': True, 'call_kind': 'expression', 'operator': opcode}}}
    registry._definition_owners[('mitsubishi', opcode, MODEL)] = entry
    monkeypatch.setattr(st_semantics, 'DEFAULT_INSTRUCTION_REGISTRY', registry)
    result = inspect_st_program('PROGRAM Main VAR x : INT; y : INT; END_VAR '
                                f'x := {opcode}(x, y); END_PROGRAM', plc_model=MODEL)
    assert result['status'] == 'failed', result['findings']
    assert any(f['code'] == 'ST_CALL_FORM' for f in result['findings'])
    assert result['calls'] == []


def test_changed_source_revokes_previously_compiled_st_admission():
    registry = InstructionRegistry([])
    entry = {'opcode': 'ABS_E', 'target_model': MODEL, 'call_available': False, 'facts': abs_e_facts()}
    assert refresh_reviewed_structured_call_admission(entry, registry)
    signature = next(f for f in entry['facts'] if f['dimension'] == 'parameters.st_signature')
    signature['value']['parameters'] = signature['value']['parameters'][:-1]
    assert not refresh_reviewed_structured_call_admission(entry, registry)
    assert entry['call_available'] is False
    assert entry['call_admission_details']['interfaces'] == []


def _ctu_instance_contract(*, pv_type='INT', model='FX3U'):
    # Deliberately not in IEC.lif declaration order: FB calls bind by formal
    # name and direction, not by the source declaration sequence.
    parameters = [
        {'name': 'CV', 'direction': 'output', 'type': 'INT'},
        {'name': 'PV', 'direction': 'input', 'type': pv_type},
        {'name': 'Q', 'direction': 'output', 'type': 'BOOL'},
        {'name': 'RESET', 'direction': 'input', 'type': 'BOOL'},
        {'name': 'CU', 'direction': 'input', 'type': 'BOOL'},
    ]
    mapping = {'opcode': 'CTU', 'model': model, 'name': 'CTU',
        'call_kind': 'function_block', 'enable': False, 'instance_required': True,
        'parameters': parameters, 'returns': None}

    class Contract:
        call_kind = 'function_block'
        fact_ids = ('structured:ctu:st_signature',)
        conditions = ()

        def as_mapping(self):
            return copy.deepcopy(mapping)

    return Contract()


def _accepted_fb_resolution(contract):
    return type('Resolution', (), {'accepted': True, 'contract': contract,
                                  'refusal_reasons': ()})()


def test_library_fb_instance_contract_matches_exact_named_interface_and_rejects_ambiguity():
    from knowledge.instruction_compiler import _fbd_graphic_context

    sources = _fbd_graphic_context('FX3U').sources
    matched = sources.st_instance_contract_interface('CTU', _ctu_instance_contract().as_mapping())
    assert matched is not None
    assert matched['source_kind'] == 'library'
    assert matched['library'] == matched['source_stream'] == 'IEC.lif'
    assert matched['definition'] == 'CTU'
    assert matched['kind'] == 'FUNCTION_BLOCK'
    assert matched['binding'] == 'exact_parameter_name_direction_type'
    assert [(row['name'], row['type']) for row in matched['inputs']] == [
        ('CU', 'BOOL'), ('RESET', 'BOOL'), ('PV', 'INT')]
    assert [(row['name'], row['type']) for row in matched['outputs']] == [
        ('Q', 'BOOL'), ('CV', 'INT')]

    assert sources.st_instance_contract_interface(
        'CTU', _ctu_instance_contract(pv_type='DINT').as_mapping()) is None
    assert sources.st_instance_contract_interface(
        'CTU', _ctu_instance_contract(model='FX3G').as_mapping()) is None
    malformed = _ctu_instance_contract().as_mapping()
    malformed['parameters'][0]['direction'] = 'input'
    assert sources.st_instance_contract_interface('CTU', malformed) is None

    original = list(sources.catalog['ctu'])
    try:
        sources.catalog['ctu'].append(('OTHER.lif', copy.deepcopy(original[0][1])))
        assert sources.st_instance_contract_interface(
            'CTU', _ctu_instance_contract().as_mapping()) is None
    finally:
        sources.catalog['ctu'][:] = original


def test_fxu_fx2c_menu_template_binds_only_its_exact_iec_library_source():
    from gxw.object_model import read_project_context
    from gxw.project_templates import blank_fbd_baseline

    context = read_project_context(blank_fbd_baseline('FXU'))
    sources = context.sources
    assert sources.cpu == 'FXU/FX2C'
    assert sources.library_cpu == 'FX2C'
    matched = sources.st_instance_contract_interface(
        'CTU', _ctu_instance_contract(model='FX2C').as_mapping())
    assert matched is not None
    assert matched['source_kind'] == 'library'
    assert matched['library'] == matched['source_stream'] == 'IECFunction.lif'
    assert matched['definition'] == 'CTU'
    assert matched['kind'] == 'FUNCTION_BLOCK'

    # A duplicate IEC definition is ambiguous even when both streams are
    # recognized names for this merged native profile.
    original = list(sources.catalog['ctu'])
    try:
        sources.catalog['ctu'].append(('IEC.lif', copy.deepcopy(original[0][1])))
        assert sources.st_instance_contract_interface(
            'CTU', _ctu_instance_contract(model='FX2C').as_mapping()) is None
    finally:
        sources.catalog['ctu'][:] = original

    # Same formal names from a different native menu do not transfer.
    assert sources.st_instance_contract_interface(
        'CTU', _ctu_instance_contract(model='FX3G').as_mapping()) is None


def test_fb_structured_admission_requires_real_library_match_and_keeps_fbd_marker(monkeypatch):
    import plc.structured_call_contract as structured_contract
    from knowledge.instruction_compiler import _fbd_graphic_context

    monkeypatch.setattr(structured_contract, 'resolve_structured_call_contract',
                        lambda *args, **kwargs: _accepted_fb_resolution(_ctu_instance_contract()))
    registry = InstructionRegistry([])
    entry = {'opcode': 'CTU', 'target_model': 'FX3U', 'call_available': False, 'facts': []}
    assert refresh_reviewed_structured_call_admission(entry, registry)
    marker = entry['call_admission_details']['structured_call']
    assert marker['call_kind'] == 'function_block'
    assert marker['instance_required'] is True
    assert marker['instance_interface_verified'] is True
    assert marker['instance_interface_source']['library'] == 'IEC.lif'
    assert 'inputs' not in marker['instance_interface_source']
    assert entry['call_admission_details']['interfaces'] == ['st']

    monkeypatch.setattr(structured_contract, 'resolve_structured_call_contract',
                        lambda *args, **kwargs: _accepted_fb_resolution(
                            _ctu_instance_contract(pv_type='DINT')))
    entry.update(call_available=True, support_status='reviewed_st_signature',
        call_admission_details={'interfaces': ['st'], 'structured_call': marker})
    assert not refresh_reviewed_structured_call_admission(entry, registry)
    assert entry['call_available'] is False
    assert entry['support_status'] == 'definition_only'
    assert 'structured_call' not in entry['call_admission_details']

    entry.update(call_available=True, support_status='reviewed_fbd_signature',
        call_admission_details={'interfaces': ['fbd', 'st'],
            'fbd_call': {'available': True, 'graphical_interface_verified': True},
            'structured_call': marker})
    assert not refresh_reviewed_structured_call_admission(entry, registry)
    assert entry['call_available'] is True
    assert entry['support_status'] == 'reviewed_fbd_signature'
    assert entry['call_admission_details']['interfaces'] == ['fbd']
    assert entry['call_admission_details']['fbd_call']['available'] is True
    assert 'structured_call' not in entry['call_admission_details']

    required = ('form_identity', 'cpu_applicability', 'arity', 'operand_order',
                'operand_roles', 'operand_types')
    native_registry = InstructionRegistry([InstructionSpec('CTU', cpu_support=frozenset({'FX3U'}),
        min_operands=0, max_operands=0, verified_fields=required,
        contract_sources=({'manual_id': 'native-ctu', 'revision': 'A', 'pdf_page': 1},))])
    entry.update(call_available=True, support_status='reviewed_native_signature',
        call_admission_details={'interfaces': ['ladder', 'st'],
            'structured_call': marker})
    assert not refresh_reviewed_structured_call_admission(entry, native_registry)
    assert entry['call_available'] is True
    assert entry['support_status'] == 'reviewed_native_signature'
    assert entry['call_admission_details']['interfaces'] == ['ladder']
    assert 'structured_call' not in entry['call_admission_details']


def test_native_refresh_revalidates_existing_st_interface():
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    from test_structured_call_contract import fact
    fields = ('form_identity', 'cpu_applicability', 'arity', 'operand_order', 'operand_roles', 'operand_types')
    registry = InstructionRegistry([InstructionSpec('ABS', cpu_support=frozenset({MODEL}),
        min_operands=0, max_operands=0,
        verified_fields=fields, contract_sources=({'manual_id': 'own-native', 'revision': 'A', 'pdf_page': 1},))])
    native_facts = [
        fact('native:cpu', 'constraints.cpu_availability', {'available': True}, opcode='ABS', languages=('ladder',)),
        fact('native:signature', 'parameters.typed_operands',
             {'operands': [], 'operand_count': 0, 'declaration_kind': 'instruction',
              'representation': 'native_ladder_instruction', 'native_form': {'operand_order': []}},
             opcode='ABS', languages=('ladder',)),
    ]
    for row in native_facts:
        row['sources'] = [{'manual_id': 'own-native', 'revision': 'A', 'pdf_pages': [1]}]
    entry = {'opcode': 'ABS', 'target_model': MODEL, 'call_available': False,
             'facts': [*abs_facts(), *native_facts]}
    assert refresh_reviewed_structured_call_admission(entry, registry)
    assert refresh_reviewed_call_admission(entry, registry)
    assert entry['support_status'] == 'reviewed_native_signature'
    assert entry['call_admission_details']['interfaces'] == ['ladder', 'st']
    assert entry['call_admission_details']['structured_call']['available'] is True
    entry['facts'] = native_facts
    assert refresh_reviewed_call_admission(entry, registry)
    assert entry['call_available'] is True
    assert 'structured_call' not in entry['call_admission_details']
    assert 'st' not in entry['call_admission_details'].get('interfaces', [])
    from knowledge.instruction_compiler import refresh_reviewed_fbd_instruction_block_admission
    from unittest.mock import patch
    assert not refresh_reviewed_fbd_instruction_block_admission(entry, registry)
    assert entry['call_admission_details']['interfaces'] == ['ladder']
    with patch.object(registry, 'definition_header', return_value=entry):
        assert registry.resolve('ABS', cpu=MODEL, operand_count=0) is not None


@pytest.mark.parametrize('change', ['none', 'no_positive', 'unbound', 'other_version',
                                   'wrong_model', 'wrong_page', 'unknown', 'weakened_guard'])
def test_native_conditional_summary_requires_own_positive_version(change):
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    from test_structured_call_contract import fact
    fields = ('form_identity', 'cpu_applicability', 'arity', 'operand_order', 'operand_roles', 'operand_types')
    registry = InstructionRegistry([InstructionSpec('ABS', cpu_support=frozenset({MODEL}),
        min_operands=0, max_operands=0, verified_fields=fields,
        contract_sources=({'manual_id': 'own-native', 'revision': 'A', 'pdf_page': 1},))])
    native = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
    minimum = {'context': 'cpu_version', 'minimum': '2.20'}
    positive = fact('own:positive', 'constraints.cpu_availability', {'available': True},
                    opcode='ABS', languages=('ladder',))
    positive.update(conditions=[native, minimum], depends_on=['own:version'])
    version = fact('own:version', 'constraints.cpu_version', {'minimum_series_version': 'Ver.2.20'},
                   opcode='ABS', languages=('ladder',))
    version['conditions'] = [native]
    summary = copy.deepcopy(positive)
    summary.update(id='catalogue:conditional', value={'model_states': {MODEL: 'conditional'}})
    typed = fact('own:typed', 'parameters.typed_operands',
        {'operands': [], 'operand_count': 0, 'declaration_kind': 'instruction',
         'representation': 'native_ladder_instruction', 'native_form': {'operand_order': []}},
        opcode='ABS', languages=('ladder',))
    typed['conditions'] = [native, minimum]
    rows = [positive, version, summary, typed]
    for row in rows:
        row['sources'] = [{'manual_id': 'own-native', 'revision': 'A', 'pdf_pages': [1]}]
    if change == 'no_positive': rows.remove(positive)
    elif change == 'unbound': summary['depends_on'] = []
    elif change == 'other_version': version['value']['minimum_series_version'] = 'Ver.1.00'
    elif change == 'wrong_model': version['scope']['models'] = ['FX3U']
    elif change == 'wrong_page': version['sources'][0]['pdf_pages'] = [2]
    elif change == 'unknown': version['status'] = 'unknown'
    elif change == 'weakened_guard': summary['conditions'] = [native]
    entry = {'opcode': 'ABS', 'target_model': MODEL, 'call_available': False, 'facts': rows}
    assert bool(refresh_reviewed_call_admission(entry, registry)) is (change == 'none')
    assert entry['call_available'] is (change == 'none')
    assert 'available' not in summary['value']

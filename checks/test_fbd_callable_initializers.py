"""Declaration initializers are source metadata, not optional-input contracts."""
import pytest

from gxw.callable_sources import interface
from gxw.object_model import (build_object_program, read_project_context,
                              generate_object_project, source_checks, source_node_template)
from gxw.project_templates import blank_fbd_baseline
from gxw.fbd_validation import inspect_graph
from gxw.models import GXWFormatError


def test_library_initializers_survive_formals_without_inventing_omission_defaults():
    definition = {'name': 'Example', 'return_type_raw': None, 'raw': b'''FUNCTION_BLOCK Example
VAR_INPUT
  Plain: BOOL;
  Initialized: INT := 0;
END_VAR
VAR_OUTPUT
  Result: BOOL := FALSE;
END_VAR
END_FUNCTION_BLOCK
'''}
    inputs, outputs = interface(definition)
    assert 'declared_initial_value_raw' not in inputs[0]
    assert inputs[1]['declared_initial_value_raw'] == '0'
    assert outputs[0]['declared_initial_value_raw'] == 'FALSE'
    assert all('optional' not in p and 'default_value' not in p for p in inputs + outputs)


def test_project_label_initializers_remain_distinct_from_input_optionality():
    from types import SimpleNamespace
    row = SimpleNamespace(name='Input', class_code=3, data_type='INT', offset=42,
                          record_id=1, initial_value='5')
    definition = {'name': 'Example', 'source_kind': 'project_labels', 'return_type_raw': None,
                  'declarations': SimpleNamespace(rows=[row], owner_pou_status=None)}
    inputs, outputs = interface(definition)
    assert not outputs
    assert inputs[0]['declared_initial_value_raw'] == '5'
    assert 'optional' not in inputs[0] and 'default_value' not in inputs[0]


def test_current_Q03UDV_TON_declaration_initializers_are_visible_in_formals_and_ports():
    baseline = blank_fbd_baseline('Q03UDV')
    current = read_project_context(baseline)
    formals = current.sources.fixed_interface('TON')
    assert {p['name']: p['declared_initial_value_raw'] for p in formals['inputs']} == {'IN': 'FALSE', 'PT': 'T#0s'}
    assert {p['name']: p['declared_initial_value_raw'] for p in formals['outputs']} == {'Q': 'FALSE', 'ET': 'T#0s'}
    model = {'program': current.program.logical_name, 'networks': [{'calls': [{
        'id': 'timer', 'type': 'TON', 'instance': 'Timer1', 'arguments': {'Q': 'M0'}}]}]}
    saved = generate_object_project(model, baseline=baseline)
    context = read_project_context(saved.data)
    call = next(n for n in context.program.nodes if n.type_name == 'TON')
    binding = context.sources.callable(call)
    assert {p['formal_name']: p['declared_initial_value_raw'] for p in binding['ports'] if p['side'] == 'in'} == {'IN': 'FALSE', 'PT': 'T#0s'}
    findings = [f for f in source_checks(context)['findings'] if f['code'] == 'fbd_unbound_formal_coverage']
    assert {f['formal']: f['declared_initial_value_raw'] for f in findings} == {'IN': 'FALSE', 'PT': 'T#0s'}
    assert all(f['severity'] == 'unverified' and f['omission_behavior'] == 'unknown' for f in findings)
    assert all('optional' not in f and 'default_value' not in f for f in findings)


def _build_source_instruction_block(model, opcode, *, condition_context=None):
    raw = blank_fbd_baseline(model)
    context = read_project_context(raw, fbd_condition_context=condition_context)
    object_model = context.empty_model()
    object_model['nodes'] = [{'id': 'instruction', 'template': 'function:' + opcode,
                              'symbol': opcode, 'x': 2, 'y': 2}]
    program = build_object_program(context.program, object_model, sources=context.sources,
                                   save_policy='draft')
    return context, program


def _build_connected_source_instruction_block(model, opcode, arguments, *, condition_context=None):
    context = read_project_context(blank_fbd_baseline(model), fbd_condition_context=condition_context)
    value = {
        'program': context.program.logical_name,
        'networks': [{'nodes': [{'id': 'instruction', 'template': 'function:' + opcode,
                                 'symbol': opcode, 'arguments': dict(arguments)}]}],
    }
    from gxw.ladder_lowering import fbd_topology_to_object_model
    object_model = fbd_topology_to_object_model(value, context)
    program = build_object_program(context.program, object_model,
                                   sources=context.draft_sources({}), save_policy='draft')
    return context, program


def test_source_comparison_blocks_use_verified_fbd_owner_facts_and_generic_function_abi():
    context = read_project_context(blank_fbd_baseline('L02'))
    entries = [entry for entry in context.catalog()
               if entry.get('representation') == 'fbd_instruction_block']
    assert {
        'function:AND$<', 'function:AND$<=', 'function:AND$<>', 'function:AND$=',
        'function:AND$>', 'function:AND$>=', 'function:ANDDT<', 'function:ANDDT<='
    } <= {entry['template'] for entry in entries}
    block = next(entry for entry in entries if entry['template'] == 'function:AND$<')
    assert block['call_kind'] == 'instruction_block'
    assert block['conditions_status'] == 'not_required'
    assert {'authored:ql:argument_table_fbd:parameters.fbd_signature',
            'authored:ql:cpu_applicability:constraints.cpu_availability:variant:2'} <= set(block['fact_ids'])

    context, program = _build_source_instruction_block('L02', 'AND$<')
    node = program.nodes[0]
    binding = context.sources.callable(node, bind_instance=False)
    pins = {port['name']: port for port in binding['ports']}
    assert binding['binding'] == 'source_instruction_block'
    assert binding['source_contract']['representation'] == 'fbd_instruction_block'
    assert {name: (pins[name]['side'], pins[name]['data_type']) for name in pins} == {
        'EN': ('in', 'BOOL'), 'ENO': ('out', 'BOOL'),
        's1': ('in', 'STRING'), 's2': ('in', 'STRING')
    }
    assert all('string_layout' in pins[name]['source_pin'] for name in ('s1', 's2'))
    inspected = inspect_graph(program, context.sources)
    assert inspected['contracts'][node.offset]
    assert not any(row['code'] == 'fbd_call_binding' for row in inspected['findings'])


def test_source_date_instruction_block_preserves_serial_condition_and_negative_cpu():
    context, program = _build_source_instruction_block('Q02U', 'ANDDT<')
    node = program.nodes[0]
    binding = context.sources.callable(node, bind_instance=False)
    pins = {port['name']: port for port in binding['ports']}
    assert {name: (pins[name]['side'], pins[name]['data_type']) for name in pins} == {
        'EN': ('in', 'BOOL'), 'ENO': ('out', 'BOOL'),
        's1': ('in', 'ANY16'), 's2': ('in', 'ANY16'), 'n': ('in', 'ANY16')
    }
    assert pins['s1']['source_pin']['word_device_layout']['word_count'] == 3
    assert pins['s2']['source_pin']['referent_fields'][0]['role'] == 'year'
    condition_findings = [row for row in inspect_graph(program, context.sources)['findings']
                          if row['code'] == 'fbd_instruction_block_condition_unverified']
    assert len(condition_findings) == 1
    assert condition_findings[0]['conditions'][0]['minimum'] == '10102'

    supported_context, supported_program = _build_source_instruction_block(
        'Q02U', 'ANDDT<', condition_context={'cpu_serial_first_five': 10102})
    supported_binding = supported_context.sources.callable(
        supported_program.nodes[0], bind_instance=False)
    assert supported_binding['source_contract']['conditions_status'] == 'satisfied'
    assert not any(row['code'] == 'fbd_instruction_block_condition_unverified'
                   for row in inspect_graph(supported_program, supported_context.sources)['findings'])

    rejected_context = read_project_context(blank_fbd_baseline('Q02U'),
        fbd_condition_context={'cpu_serial_first_five': 10101})
    try:
        source_node_template('function:ANDDT<', rejected_context.sources)
    except GXWFormatError as error:
        assert 'fbd_condition_not_satisfied' in str(error)
    else:
        raise AssertionError('an ineligible CPU serial must reject the source block')

    negative_context = read_project_context(blank_fbd_baseline('Q00'))
    assert not any(row['template'] == 'function:AND$<' for row in negative_context.catalog())
    try:
        negative_context.sources.fbd_instruction_block_contract('AND$<')
    except GXWFormatError as error:
        assert 'fbd_cpu_not_supported' in str(error)
    else:
        raise AssertionError('the exact owner source must retain its negative CPU applicability')
    try:
        source_node_template('function:AND$<', negative_context.sources)
    except GXWFormatError:
        pass
    else:
        raise AssertionError('a source-negative CPU without a visible graphic interface must not build')


def test_source_date_block_checks_any16_domain_and_contiguous_word_layout():
    arguments = {'EN': 'TRUE', 'ENO': 'M0', 's1': 'D0', 's2': 'D3', 'n': 'D6'}
    context, program = _build_connected_source_instruction_block(
        'Q02U', 'ANDDT<', arguments, condition_context={'cpu_serial_first_five': 10102})
    inspected = inspect_graph(program, context.sources)
    findings = inspected['findings']
    assert not any(row['severity'] == 'error' for row in findings)
    assert not any(row['severity'] == 'unverified' for row in findings)
    assert not any(row['code'] in {
        'fbd_generic_type_coverage', 'fbd_source_device_layout_unverified',
        'fbd_source_device_layout_mismatch', 'fbd_source_device_type_coverage',
        'fbd_source_device_type_conflict', 'fbd_device_span',
    } for row in findings)
    assert not any(row['code'] == 'fbd_instruction_block_condition_unverified' for row in findings)
    views = {row['formal']: row for row in inspected['source_device_views']}
    assert {name: (row['start_device'], row['end_device'], row['word_count'])
            for name, row in views.items()} == {
        's1': ('D0', 'D2', 3), 's2': ('D3', 'D5', 3), 'n': ('D6', 'D6', 1),
    }
    assert all(row['device_unit_bits'] == 16
               and row['actual_types'] == ['INT', 'WORD']
               and row['actual_types'] == row['source_types']
               for row in views.values())

    boundary_context, boundary_program = _build_connected_source_instruction_block(
        'Q02U', 'ANDDT<', {**arguments, 's1': 'D4212734'},
        condition_context={'cpu_serial_first_five': 10102})
    boundary_findings = inspect_graph(boundary_program, boundary_context.sources)['findings']
    assert any(row['code'] == 'fbd_device_span' and row['formal'] == 's1'
               for row in boundary_findings)

    incompatible_context, incompatible_program = _build_connected_source_instruction_block(
        'Q02U', 'ANDDT<', {**arguments, 's1': 'M1'},
        condition_context={'cpu_serial_first_five': 10102})
    incompatible_findings = inspect_graph(incompatible_program, incompatible_context.sources)['findings']
    assert any(row['code'] == 'fbd_source_device_layout_mismatch' and row['formal'] == 's1'
               for row in incompatible_findings)


def test_fbd_compiler_admission_requires_matching_graphic_pins_and_preserves_conditions():
    from knowledge.instruction_compiler import refresh_reviewed_fbd_instruction_block_admission
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    text_entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry('AND$<', cpu='L02')
    assert text_entry['call_available'] is False
    assert refresh_reviewed_fbd_instruction_block_admission(text_entry, DEFAULT_INSTRUCTION_REGISTRY)
    assert text_entry['call_available'] is True
    assert text_entry['support_status'] == 'reviewed_fbd_signature'
    assert text_entry['call_admission_details']['interfaces'] == ['fbd']
    text_marker = text_entry['call_admission_details']['fbd_call']
    assert text_marker['available'] is True
    assert text_marker['graphical_interface_verified'] is True
    assert text_marker['graphical_interface_source'] == {
        'library': 'MELSEC.lif', 'definition': 'AND$<', 'kind': 'FUNCTION',
        'target_model': 'L02', 'binding': 'exact_pin_name_direction_type'}
    assert text_marker['conditions_evaluated'] is False

    date_entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry('ANDDT<', cpu='Q02U')
    assert refresh_reviewed_fbd_instruction_block_admission(date_entry, DEFAULT_INSTRUCTION_REGISTRY)
    date_marker = date_entry['call_admission_details']['fbd_call']
    assert date_marker['applicability_cases']
    assert date_marker['conditions_evaluated'] is False
    assert date_marker['graphical_interface_source']['target_model'] == 'Q02U'

    negative = DEFAULT_INSTRUCTION_REGISTRY.definition_entry('AND$<', cpu='Q00')
    negative.update(call_available=True, support_status='reviewed_fbd_signature',
                    call_admission_details={'interfaces': ['fbd'], 'fbd_call': {'available': True}})
    assert not refresh_reviewed_fbd_instruction_block_admission(negative, DEFAULT_INSTRUCTION_REGISTRY)
    assert negative['call_available'] is False
    assert negative['support_status'] == 'definition_only'
    assert 'fbd_cpu_not_supported' in negative['call_admission_details']['reasons']

    mixed = DEFAULT_INSTRUCTION_REGISTRY.definition_entry('AND$<', cpu='L02')
    mixed.update(call_available=True, support_status='reviewed_native_signature',
                 call_admission_details={'interfaces': ['ladder'], 'native': {'available': True}})
    mixed['facts'] = [fact for fact in mixed['facts'] if fact.get('dimension') != 'parameters.fbd_signature']
    assert not refresh_reviewed_fbd_instruction_block_admission(mixed, DEFAULT_INSTRUCTION_REGISTRY)
    assert mixed['call_available'] is True
    assert mixed['call_admission_details']['interfaces'] == ['ladder']


def test_fbd_resolver_requires_cpu_evidence_in_signature_dependency_closure():
    from copy import deepcopy
    from plc.fbd_instruction_block_contract import resolve_fbd_instruction_block_contract
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry('AND$<', cpu='L02')
    facts = deepcopy(entry['facts'])
    signature = next(f for f in facts if f.get('dimension') == 'parameters.fbd_signature')
    cpu_facts = [f for f in facts if f.get('dimension') == 'constraints.cpu_availability']
    assert len(cpu_facts) >= 1

    # An exact model scope on the signature cannot substitute for explicit
    # source-verified CPU applicability evidence.
    without_evidence = deepcopy(entry)
    without_evidence['facts'] = [f for f in deepcopy(facts)
                                 if f.get('dimension') != 'constraints.cpu_availability']
    signature_without_evidence = next(
        f for f in without_evidence['facts'] if f.get('dimension') == 'parameters.fbd_signature')
    signature_without_evidence['depends_on'] = []
    result = resolve_fbd_instruction_block_contract(without_evidence)
    assert not result.accepted
    assert 'fbd_cpu_applicability_evidence_missing' in result.refusal_reasons

    # A positive CPU fact is insufficient when the signature no longer closes
    # over it as a required dependency.
    without_dependency = deepcopy(entry)
    signature_without_dependency = next(
        f for f in without_dependency['facts'] if f.get('dimension') == 'parameters.fbd_signature')
    signature_without_dependency['depends_on'] = []
    result = resolve_fbd_instruction_block_contract(without_dependency)
    assert not result.accepted
    assert 'fbd_cpu_applicability_dependency_missing' in result.refusal_reasons


def test_fbd_resolver_rejects_same_condition_negative_cpu_conflict():
    from copy import deepcopy
    from plc.fbd_instruction_block_contract import resolve_fbd_instruction_block_contract
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry('AND$<', cpu='L02')
    facts = deepcopy(entry['facts'])
    signature = next(f for f in facts if f.get('dimension') == 'parameters.fbd_signature')
    positive = next(f for f in facts
                    if f.get('id') in signature.get('depends_on', [])
                    and f.get('dimension') == 'constraints.cpu_availability'
                    and f.get('value', {}).get('available') is True)
    negative = deepcopy(positive)
    negative['id'] = 'test:negative_cpu_availability'
    negative['value']['available'] = False
    negative['value']['model_states'] = {'L02': 'unsupported'}
    # Keep the exact same owner, source, and conditions, but do not add the
    # contradictory row to the signature dependency list.
    facts.append(negative)
    conflicted = deepcopy(entry)
    conflicted['facts'] = facts

    result = resolve_fbd_instruction_block_contract(conflicted)
    assert not result.accepted
    assert any(reason.startswith('fbd_cpu_applicability_conflict:')
               for reason in result.refusal_reasons)


def test_fbd_explicit_logical_model_flows_through_merged_native_menu():
    baseline = blank_fbd_baseline('L02-P')
    from application.fbd import _target_context
    context = _target_context(baseline, 'L02-P')
    assert context.sources.cpu == 'L02'
    assert context.sources.target_model == 'L02-P'

    contract = {
        'opcode': 'AND$<', 'model': 'L02-P',
        'pins': [
            {'name': 'EN', 'direction': 'input', 'type': 'BOOL'},
            {'name': 's1', 'direction': 'input', 'type': 'STRING'},
            {'name': 's2', 'direction': 'input', 'type': 'STRING'},
            {'name': 'ENO', 'direction': 'output', 'type': 'BOOL'},
        ],
    }
    graphic = context.sources.fbd_instruction_block_graphic_interface('AND$<', contract)
    assert graphic is not None
    assert graphic['target_model'] == 'L02-P'

    # The same physical Q02/Q02H template does not select either logical model
    # unless a caller passes the selected target explicitly.
    merged = read_project_context(blank_fbd_baseline('Q02'))
    assert merged.sources.cpu == 'Q02/Q02H'
    assert merged.sources.target_model is None
    q02_contract = {**contract, 'model': 'Q02'}
    assert merged.sources.fbd_instruction_block_graphic_interface('AND$<', q02_contract) is None

    with pytest.raises(GXWFormatError, match='does not match project CPU menu'):
        read_project_context(baseline, target_model='Q02')


@pytest.mark.parametrize('opcode', ['BCDDA', 'BCDDAP'])
def test_fbd_library_string_bound_requires_the_exact_source_capacity(opcode):
    context = read_project_context(blank_fbd_baseline('Q03UDV'), target_model='Q03UDV')
    destination = {'name': 'd', 'direction': 'output', 'type': 'STRING', 'declared_capacity': 6}
    contract = {'opcode': opcode, 'model': 'Q03UDV', 'pins': [
        {'name': 'EN', 'direction': 'input', 'type': 'BOOL'},
        {'name': 's', 'direction': 'input', 'type': 'ANY16'},
        {'name': 'ENO', 'direction': 'output', 'type': 'BOOL'}, destination,
    ]}
    graphic = context.sources.fbd_instruction_block_graphic_interface(opcode, contract)
    assert graphic is not None
    assert graphic['outputs'][-1] == {'name': 'd', 'type': 'STRING(6)'}
    for invalid in (4, 10, 0, True):
        destination['declared_capacity'] = invalid
        assert context.sources.fbd_instruction_block_graphic_interface(opcode, contract) is None
    destination.pop('declared_capacity')
    destination['declared_capacity_choices'] = [6, 10]
    assert context.sources.fbd_instruction_block_graphic_interface(opcode, contract) is None


def test_fbd_contract_lookup_uses_only_an_explicit_merged_menu_member(monkeypatch):
    from types import SimpleNamespace
    import plc.fbd_instruction_block_contract as contracts

    class Registry:
        def __init__(self):
            self.models = []

        def definition_entry(self, opcode, *, cpu, vendor):
            self.models.append(cpu)
            if cpu == 'Q02/Q02H':
                return None
            return {'opcode': opcode, 'model': cpu}

    registry = Registry()
    resolved_models = []

    def resolve(entry, *, opcode, model, condition_context):
        resolved_models.append(model)
        mapped = {'opcode': opcode, 'model': model}
        return SimpleNamespace(accepted=True, refusal_reasons=(),
                               contract=SimpleNamespace(as_mapping=lambda: mapped))

    monkeypatch.setattr(contracts, 'resolve_fbd_instruction_block_contract', resolve)
    baseline = blank_fbd_baseline('Q02')
    selected = read_project_context(baseline, instruction_registry=registry, target_model='Q02')
    assert selected.sources.fbd_instruction_block_contract('AND$<') == {
        'opcode': 'AND$<', 'model': 'Q02'}
    assert registry.models == ['Q02']
    assert resolved_models == ['Q02']

    merged = read_project_context(baseline, instruction_registry=registry)
    assert merged.sources.fbd_instruction_block_contract('AND$<') is None
    assert registry.models[-1] == 'Q02/Q02H'
    assert resolved_models == ['Q02']


@pytest.mark.parametrize(('value_type', 'expected'), [
    ({'kind': 'int', 'bits': 32, 'signed': True}, 'DINT'),
    ({'kind': 'int', 'bits': 32, 'signed': False}, 'DWORD'),
    ({'kind': 'real', 'bits': 32, 'signed': False}, 'REAL'),
    ({'kind': 'int', 'bits': 32}, None),
    ({'kind': 'int', 'bits': 32, 'signed': True, 'unit': 'ms'}, None),
    ({'kind': 'int', 'bits': 16, 'signed': True}, None),
])
def test_fbd_operation_value_type_requires_an_exact_32_bit_identity(value_type, expected):
    from gxw.fbd_validation import _operation_value_iec_type

    assert _operation_value_iec_type(value_type) == expected


@pytest.mark.parametrize(('value_type', 'expected'), [
    ({'kind': 'real', 'bits': 64}, 'LREAL'),
    ({'kind': 'real', 'bits': 64, 'signed': True}, None),
    ({'kind': 'real', 'bits': 64, 'unit': 'ms'}, None),
    ({'kind': 'real', 'bits': 32}, 'REAL'),
])
def test_fbd_operation_value_type_maps_only_explicit_real_widths(value_type, expected):
    from gxw.fbd_validation import _operation_value_iec_type

    assert _operation_value_iec_type(value_type) == expected


@pytest.mark.parametrize(('prefix', 'domain', 'bits'), [
    ('ANDE', 'REAL', 32), ('ANDED', 'LREAL', 64),
])
@pytest.mark.parametrize('comparison', ('<', '<=', '<>', '=', '>', '>='))
def test_real_comparison_operation_and_source_typed_relation_resolve_per_form(prefix, domain, bits, comparison):
    from plc.fbd_instruction_block_contract import resolve_fbd_instruction_block_contract
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    opcode = prefix + comparison
    entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(opcode, cpu='Q03UDV')
    result = resolve_fbd_instruction_block_contract(entry)
    assert result.accepted, result.refusal_reasons
    contract = result.contract.as_mapping()
    assert contract['operation']['comparison'] == comparison
    assert contract['operation']['composition'] == 'and'
    assert contract['operation']['data_domain'] == domain
    assert contract['operation_fact_id'] in contract['fact_ids']
    pins = {pin['name'].casefold(): pin for pin in contract['pins']}
    assert pins['s1']['type'] == pins['s2']['type'] == domain
    assert pins['s1']['operation_value_type'] == pins['s2']['operation_value_type'] == {
        'kind': 'real', 'bits': bits,
    }
    assert 'operation_value_type' not in pins['eno']


@pytest.mark.parametrize('model', ('Q00', 'Q00J', 'Q01'))
@pytest.mark.parametrize('comparison', ('<', '<=', '<>', '=', '>', '>='))
def test_ande_source_serial_guard_is_unresolved_below_threshold_and_satisfied_at_04122(model, comparison):
    from plc.fbd_instruction_block_contract import resolve_fbd_instruction_block_contract
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry('ANDE' + comparison, cpu=model)
    unresolved = resolve_fbd_instruction_block_contract(entry)
    assert unresolved.accepted, unresolved.refusal_reasons
    assert unresolved.contract.conditions_status == 'unresolved'
    assert any(condition.get('minimum') == '04122' for condition in unresolved.contract.conditions)

    below = resolve_fbd_instruction_block_contract(
        entry, condition_context={'cpu_serial_first_five': 4121})
    assert not below.accepted
    assert 'fbd_condition_not_satisfied' in below.refusal_reasons

    at_threshold = resolve_fbd_instruction_block_contract(
        entry, condition_context={'cpu_serial_first_five': 4122})
    assert at_threshold.accepted, at_threshold.refusal_reasons
    assert at_threshold.contract.conditions_status == 'satisfied'


def test_ande_fbd_builder_keeps_serial_condition_unknown_and_rejects_low_serial():
    arguments = {'EN': 'TRUE', 'ENO': 'M0', 's1': 'D0', 's2': 'D2'}
    unknown_context, unknown_program = _build_connected_source_instruction_block(
        'Q00', 'ANDE<', arguments)
    unknown_node = unknown_program.nodes[0]
    unknown_binding = unknown_context.sources.callable(unknown_node, bind_instance=False)
    assert unknown_binding['source_contract']['conditions_status'] == 'unresolved'
    assert any(row['code'] == 'fbd_instruction_block_condition_unverified'
               for row in inspect_graph(unknown_program, unknown_context.sources)['findings'])

    supported_context, supported_program = _build_connected_source_instruction_block(
        'Q00', 'ANDE<', arguments, condition_context={'cpu_serial_first_five': 4122})
    supported_binding = supported_context.sources.callable(
        supported_program.nodes[0], bind_instance=False)
    assert supported_binding['source_contract']['conditions_status'] == 'satisfied'
    assert not any(row['severity'] in {'error', 'unverified'}
                   for row in inspect_graph(supported_program, supported_context.sources)['findings'])

    from gxw.object_model import source_node_template
    rejected_context = read_project_context(blank_fbd_baseline('Q00'),
        fbd_condition_context={'cpu_serial_first_five': 4121})
    with pytest.raises(GXWFormatError, match='fbd_condition_not_satisfied'):
        source_node_template('function:ANDE<', rejected_context.sources)


@pytest.mark.parametrize('model', (
    'Q00', 'Q00J', 'Q01', 'Q02', 'Q02H', 'Q02PH', 'Q06H', 'Q06PH',
    'Q12H', 'Q12PH', 'Q12PRH', 'Q25H', 'Q25PH', 'Q25PRH',
))
@pytest.mark.parametrize('comparison', ('<', '<=', '<>', '=', '>', '>='))
def test_anded_explicit_negative_fbd_source_owners_remain_rejected(model, comparison):
    from plc.fbd_instruction_block_contract import resolve_fbd_instruction_block_contract
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry('ANDED' + comparison, cpu=model)
    result = resolve_fbd_instruction_block_contract(entry)
    assert not result.accepted
    assert 'fbd_cpu_not_supported' in result.refusal_reasons


def test_andd_comparison_operation_is_separate_from_eno_and_typed_relation_is_source_bound():
    from plc.fbd_instruction_block_contract import resolve_fbd_instruction_block_contract
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry('ANDD<', cpu='Q03UDV')
    result = resolve_fbd_instruction_block_contract(entry)
    assert result.accepted, result.refusal_reasons
    contract = result.contract.as_mapping()
    assert contract['operation'] == {
        'comparison': '<', 'composition': 'and', 'data_domain': 'signed_BIN32',
        'left': 'S1', 'operation': 'compose_comparison_contact', 'right': 'S2'}
    assert contract['operation_fact_id'] in contract['fact_ids']
    pins = {pin['name'].casefold(): pin for pin in contract['pins']}
    assert pins['eno']['role'] == 'execution_status'
    assert pins['eno']['direction'] == 'output'
    assert 'operation_value_type' not in pins['eno']
    assert pins['s1']['type'] == pins['s2']['type'] == 'ANY32'
    assert pins['s1']['operation_value_type'] == pins['s2']['operation_value_type'] == {
        'kind': 'int', 'bits': 32, 'signed': True}
    assert any(fact['id'] == contract['operation_fact_id']
               and fact['dimension'] == 'effects.operation' for fact in contract['facts'])

    # A comparison symbol mismatch cannot be hidden by the otherwise valid
    # source signature or graphic connector pins.
    from copy import deepcopy
    conflicted = deepcopy(entry)
    operation = next(fact for fact in conflicted['facts']
                     if fact.get('dimension') == 'effects.operation')
    operation['value']['comparison'] = '>='
    rejected = resolve_fbd_instruction_block_contract(conflicted)
    assert not rejected.accepted
    assert 'fbd_comparison_operation_mismatch' in rejected.refusal_reasons


def test_andd_comparison_blocks_build_and_roundtrip_as_signed_dint_device_views():
    from gxw.object_model import generate_object_project
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    model = 'Q03UDV'
    baseline = blank_fbd_baseline(model)
    context = read_project_context(baseline)
    operators = ('<', '<=', '<>', '=', '>', '>=')
    nodes = [{
        'id': 'comparison_' + str(index),
        'template': 'function:ANDD' + operator,
        'symbol': 'ANDD' + operator,
        'arguments': {'EN': 'TRUE', 'ENO': 'M' + str(index),
                      's1': 'D' + str(index * 4), 's2': 'D' + str(index * 4 + 2)},
    } for index, operator in enumerate(operators)]
    saved = generate_object_project({
        'program': context.program.logical_name,
        'networks': [{'nodes': nodes}],
    }, baseline=baseline)
    reopened = read_project_context(saved.data)
    inspected = inspect_graph(reopened.program, reopened.sources)

    assert not any(row['severity'] in {'error', 'unverified'} for row in inspected['findings'])
    assert len(reopened.program.nodes) >= len(operators)
    actual_operators = {}
    for node in reopened.program.nodes:
        if node.symbol not in {'ANDD' + operator for operator in operators}:
            continue
        binding = reopened.sources.callable(node, bind_instance=False)
        contract = binding['source_contract']
        actual_operators[node.symbol] = contract['operation']['comparison']
        assert contract['operation']['data_domain'] == 'signed_BIN32'
        assert {pin['name']: pin.get('operation_value_type') for pin in contract['pins']
                if pin['name'].casefold() in {'s1', 's2'}} == {
                    's1': {'kind': 'int', 'bits': 32, 'signed': True},
                    's2': {'kind': 'int', 'bits': 32, 'signed': True}}
        assert all(value == ['DINT'] for key, value in inspected['resolved_types'].items()
                   if key.startswith(str(node.offset) + ':') and key.rsplit(':', 1)[-1] in {'1', '2'})
    assert actual_operators == {'ANDD' + operator: operator for operator in operators}

    wrong_context, wrong_program = _build_connected_source_instruction_block(
        model, 'ANDD<', {'EN': 'TRUE', 'ENO': 'M10', 's1': 'M0', 's2': 'D20'})
    wrong_findings = inspect_graph(wrong_program, wrong_context.sources)['findings']
    assert any(row['code'] == 'fbd_operation_value_type_device_mismatch'
               and row.get('formal') == 's1' for row in wrong_findings)
    assert DEFAULT_INSTRUCTION_REGISTRY.resolve_form('ANDD<', cpu=model) is None


def test_andd_default_call_contract_projects_its_fbd_operation_and_typed_pins(monkeypatch):
    from application.capability_context import _call_contract_units
    from knowledge.instruction_compiler import refresh_reviewed_fbd_instruction_block_admission
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    registry = DEFAULT_INSTRUCTION_REGISTRY
    original_definition_entry = registry.definition_entry
    entry = original_definition_entry('ANDD<', cpu='Q03UDV')
    assert refresh_reviewed_fbd_instruction_block_admission(entry, registry)
    monkeypatch.setattr(registry, 'definition_entry',
                        lambda mnemonic, *, cpu, vendor='mitsubishi': entry)

    described = registry.describe_call('ANDD<', cpu='Q03UDV')
    fbd = described['fbd_contract']
    assert fbd['operation'] == {
        'comparison': '<', 'composition': 'and', 'data_domain': 'signed_BIN32',
        'left': 'S1', 'operation': 'compose_comparison_contact', 'right': 'S2'}
    pins = {pin['name'].casefold(): pin for pin in fbd['pins']}
    assert pins['eno']['role'] == 'execution_status'
    assert pins['s1']['operation_value_type'] == {'kind': 'int', 'bits': 32, 'signed': True}
    assert pins['s2']['operation_value_type'] == {'kind': 'int', 'bits': 32, 'signed': True}

    units = _call_contract_units([], plc_model='Q03UDV', explicit=[{'opcode': 'ANDD<'}])
    unit = next(body for identity, body in units
                if identity == 'call-contract:Q03UDV:ANDD<')
    assert '"comparison":"<"' in unit
    assert '"data_domain":"signed_BIN32"' in unit
    assert '"operation_value_type":{"bits":32,"kind":"int","signed":true}' in unit


def test_ande_anded_comparison_blocks_build_and_roundtrip_real_and_lreal_views(monkeypatch):
    from gxw.object_model import generate_object_project
    import gxw.fbd_validation as fbd_validation

    model = 'Q03UDV'
    baseline = blank_fbd_baseline(model)
    context = read_project_context(baseline)
    nodes = [
        {'id': 'real_comparison', 'template': 'function:ANDE<', 'symbol': 'ANDE<',
         'arguments': {'EN': 'TRUE', 'ENO': 'M0', 's1': 'D0', 's2': 'D2'}},
        {'id': 'lreal_comparison', 'template': 'function:ANDED<', 'symbol': 'ANDED<',
         'arguments': {'EN': 'TRUE', 'ENO': 'M1', 's1': 'D4', 's2': 'D8'}},
    ]
    saved = generate_object_project({
        'program': context.program.logical_name,
        'networks': [{'nodes': nodes}],
    }, baseline=baseline)
    reopened = read_project_context(saved.data)

    storage_views = []
    original_storage_keys = fbd_validation.storage_keys

    def observe_storage_view(value, *, plc_model=None, view_bits=None):
        keys = original_storage_keys(value, plc_model=plc_model, view_bits=view_bits)
        storage_views.append((value.get('text') if isinstance(value, dict) else value,
                              view_bits, len(keys)))
        return keys

    monkeypatch.setattr(fbd_validation, 'storage_keys', observe_storage_view)
    inspected = inspect_graph(reopened.program, reopened.sources)
    assert not any(row['severity'] in {'error', 'unverified'} for row in inspected['findings'])

    expected_by_symbol = {'ANDE<': 'REAL', 'ANDED<': 'LREAL'}
    actual = {}
    for node in reopened.program.nodes:
        if node.symbol not in expected_by_symbol:
            continue
        binding = reopened.sources.callable(node, bind_instance=False)
        source_contract = binding['source_contract']
        domain = expected_by_symbol[node.symbol]
        actual[node.symbol] = source_contract['operation']['data_domain']
        assert actual[node.symbol] == domain
        pins = {pin['name'].casefold(): pin for pin in source_contract['pins']}
        expected_type = {'kind': 'real', 'bits': 64 if domain == 'LREAL' else 32}
        assert pins['s1']['operation_value_type'] == pins['s2']['operation_value_type'] == expected_type
        assert 'operation_value_type' not in pins['eno']
        assert all(types == [domain] for key, types in inspected['resolved_types'].items()
                   if key.startswith(str(node.offset) + ':')
                   and key.rsplit(':', 1)[-1] in {'1', '2'})
    assert actual == expected_by_symbol
    assert ('D0', 32, 2) in storage_views
    assert ('D4', 64, 4) in storage_views and ('D8', 64, 4) in storage_views

    wrong_context, wrong_program = _build_connected_source_instruction_block(
        model, 'ANDED<', {'EN': 'TRUE', 'ENO': 'M2', 's1': 'M2', 's2': 'D12'})
    wrong_findings = inspect_graph(wrong_program, wrong_context.sources)['findings']
    assert any(row['code'] == 'fbd_operation_value_type_device_mismatch'
               and row.get('formal') == 's1' for row in wrong_findings)


@pytest.mark.parametrize('comparison', ('<', '<=', '<>', '=', '>', '>='))
def test_andtm_time_operation_resolves_only_through_its_fbd_source_chain(comparison):
    from plc.fbd_instruction_block_contract import resolve_fbd_instruction_block_contract
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    opcode = 'ANDTM' + comparison
    entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(opcode, cpu='Q03UDV')
    result = resolve_fbd_instruction_block_contract(entry, opcode=opcode, model='Q03UDV')
    assert result.accepted, result.refusal_reasons
    contract = result.contract.as_mapping()
    assert contract['operation']['comparison'] == comparison
    assert contract['operation']['data_domain'] == 'time_hour_minute_second'
    assert contract['operation']['selector'] == 'n'
    assert contract['operation']['domain_comparison']['comparison_span_words'] == 3
    assert contract['operation_fact_id'] == 'authored:ql:literal_comparison:effects.operation:variant:2'
    assert contract['operation_fact_id'] in contract['fact_ids']
    assert 'authored:ql:argument_table_fbd:parameters.fbd_signature' in contract['fact_ids']
    pins = {pin['name'].casefold(): pin for pin in contract['pins']}
    assert set(pins) == {'en', 'eno', 's1', 's2', 'n'}
    assert pins['eno']['role'] == 'execution_status'
    assert pins['eno']['direction'] == 'output'
    assert pins['s1']['word_device_layout']['word_count'] == 3
    assert pins['s2']['word_device_layout']['word_count'] == 3
    assert pins['n']['word_device_layout']['word_count'] == 1
    assert all('operation_value_type' not in pins[name] for name in ('s1', 's2', 'n'))
    operation_sources = [source for source in contract['sources']
                         if source.get('manual_id') == 'sh080783engp'
                         and source.get('target_model') == 'Q03UDV']
    assert operation_sources
    assert all(source.get('revision') == 'P' for source in operation_sources)


def test_andtm_fbd_build_and_roundtrip_keeps_time_words_and_selector_separate():
    from gxw.object_model import generate_object_project

    model = 'Q03UDV'
    baseline = blank_fbd_baseline(model)
    context = read_project_context(baseline)
    comparisons = ('<', '<=', '<>', '=', '>', '>=')
    nodes = [{
        'id': 'andtm_' + str(index),
        'template': 'function:ANDTM' + comparison,
        'symbol': 'ANDTM' + comparison,
        'arguments': {'EN': 'TRUE', 'ENO': 'M' + str(index),
                      's1': 'D' + str(index * 8), 's2': 'D' + str(index * 8 + 3),
                      'n': 'D' + str(index * 8 + 6)},
    } for index, comparison in enumerate(comparisons)]
    saved = generate_object_project({'program': context.program.logical_name,
                                     'networks': [{'nodes': nodes}]}, baseline=baseline)
    reopened = read_project_context(saved.data)
    inspected = inspect_graph(reopened.program, reopened.sources)
    assert not any(row['severity'] in {'error', 'unverified'} for row in inspected['findings'])

    actual = {}
    views = {}
    for node in reopened.program.nodes:
        if not node.symbol.startswith('ANDTM'):
            continue
        binding = reopened.sources.callable(node, bind_instance=False)
        contract = binding['source_contract']
        actual[node.symbol] = contract['operation']['comparison']
        assert contract['operation']['data_domain'] == 'time_hour_minute_second'
        assert contract['operation']['domain_comparison']['comparison_span_words'] == 3
        assert contract['operation_fact_id'] in contract['fact_ids']
        assert not any('operation_value_type' in pin for pin in contract['pins']
                       if pin['name'].casefold() in {'s1', 's2', 'n'})
        for row in inspected['source_device_views']:
            if row['node_offset'] == node.offset:
                views[(node.symbol, row['formal'])] = (
                    row['start_device'], row['end_device'], row['word_count'],
                    row['actual_types'], row['source_types'])
    assert actual == {'ANDTM' + comparison: comparison for comparison in comparisons}
    assert len(views) == 18
    assert views[('ANDTM<', 's1')] == ('D0', 'D2', 3, ['INT', 'WORD'], ['INT', 'WORD'])
    assert views[('ANDTM<', 's2')] == ('D3', 'D5', 3, ['INT', 'WORD'], ['INT', 'WORD'])
    assert views[('ANDTM<', 'n')] == ('D6', 'D6', 1, ['INT', 'WORD'], ['INT', 'WORD'])

    wrong_context, wrong_program = _build_connected_source_instruction_block(
        model, 'ANDTM<', {'EN': 'TRUE', 'ENO': 'M20', 's1': 'M0', 's2': 'D20', 'n': 'D23'})
    wrong_findings = inspect_graph(wrong_program, wrong_context.sources)['findings']
    assert any(row['code'] == 'fbd_source_device_layout_mismatch'
               and row.get('formal') == 's1' for row in wrong_findings)

    boundary_context, boundary_program = _build_connected_source_instruction_block(
        model, 'ANDTM<', {'EN': 'TRUE', 'ENO': 'M21', 's1': 'D4322302',
                          's2': 'D30', 'n': 'D33'})
    boundary_findings = inspect_graph(boundary_program, boundary_context.sources)['findings']
    assert any(row['code'] == 'fbd_device_span' and row.get('formal') == 's1'
               for row in boundary_findings)


def test_andtm_operation_cannot_lose_or_conflict_with_its_signature_source_chain():
    from copy import deepcopy
    from plc.fbd_instruction_block_contract import resolve_fbd_instruction_block_contract
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    opcode = 'ANDTM<'
    entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(opcode, cpu='Q03UDV')
    operation_id = 'authored:ql:literal_comparison:effects.operation:variant:2'

    unlinked = deepcopy(entry)
    operation = next(fact for fact in unlinked['facts'] if fact.get('id') == operation_id)
    operation['depends_on'] = []
    result = resolve_fbd_instruction_block_contract(unlinked, opcode=opcode, model='Q03UDV')
    assert not result.accepted
    assert 'fbd_time_operation_not_linked_to_signature' in result.refusal_reasons

    duplicate = deepcopy(entry)
    operation = deepcopy(next(fact for fact in duplicate['facts'] if fact.get('id') == operation_id))
    operation['id'] += ':duplicate'
    duplicate['facts'].append(operation)
    result = resolve_fbd_instruction_block_contract(duplicate, opcode=opcode, model='Q03UDV')
    assert not result.accepted
    assert 'ambiguous_fbd_time_operation' in result.refusal_reasons

    condition_conflict = deepcopy(entry)
    operation = deepcopy(next(fact for fact in condition_conflict['facts']
                              if fact.get('id') == operation_id))
    operation['id'] += ':unconditional'
    operation['conditions'] = [{'field': 'project_mode', 'equals': 'structured_ladder_block'}]
    condition_conflict['facts'].append(operation)
    result = resolve_fbd_instruction_block_contract(
        condition_conflict, opcode=opcode, model='Q03UDV')
    assert not result.accepted
    assert 'fbd_time_operation_condition_mismatch' in result.refusal_reasons

    wrong_domain = deepcopy(entry)
    operation = next(fact for fact in wrong_domain['facts'] if fact.get('id') == operation_id)
    operation['value']['data_domain'] = 'signed_BIN32'
    result = resolve_fbd_instruction_block_contract(wrong_domain, opcode=opcode, model='Q03UDV')
    assert not result.accepted
    assert 'fbd_time_operation_mismatch' in result.refusal_reasons


def test_andtm_serial_condition_is_unknown_below_threshold_and_satisfied_at_threshold():
    from plc.fbd_instruction_block_contract import resolve_fbd_instruction_block_contract
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    opcode = 'ANDTM<'
    entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(opcode, cpu='Q02U')
    unresolved = resolve_fbd_instruction_block_contract(entry, opcode=opcode, model='Q02U')
    assert unresolved.accepted, unresolved.refusal_reasons
    assert unresolved.contract.conditions_status == 'unresolved'
    assert any(condition.get('minimum') == '10102'
               for condition in unresolved.contract.conditions)

    below = resolve_fbd_instruction_block_contract(
        entry, opcode=opcode, model='Q02U',
        condition_context={'cpu_serial_first_five': 10101})
    assert not below.accepted
    assert 'fbd_condition_not_satisfied' in below.refusal_reasons

    at_threshold = resolve_fbd_instruction_block_contract(
        entry, opcode=opcode, model='Q02U',
        condition_context={'cpu_serial_first_five': 10102})
    assert at_threshold.accepted, at_threshold.refusal_reasons
    assert at_threshold.contract.conditions_status == 'satisfied'

    context, program = _build_connected_source_instruction_block(
        'Q02U', opcode, {'EN': 'TRUE', 'ENO': 'M0', 's1': 'D0', 's2': 'D3', 'n': 'D6'},
        condition_context={'cpu_serial_first_five': 10102})
    assert not any(row['severity'] in {'error', 'unverified'}
                   for row in inspect_graph(program, context.sources)['findings'])


@pytest.mark.parametrize('comparison', ('<', '<=', '<>', '=', '>', '>='))
def test_andtm_explicit_negative_cpu_owner_stays_unavailable(comparison):
    from plc.fbd_instruction_block_contract import resolve_fbd_instruction_block_contract
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    opcode = 'ANDTM' + comparison
    entry = DEFAULT_INSTRUCTION_REGISTRY.definition_entry(opcode, cpu='Q00')
    result = resolve_fbd_instruction_block_contract(entry, opcode=opcode, model='Q00')
    assert not result.accepted
    assert 'fbd_cpu_not_supported' in result.refusal_reasons

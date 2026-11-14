"""Source-bound bit sequences are storage references, not reset execution."""
from dataclasses import replace

import pytest

from plc.device_identity import instruction_operand_regions
from plc.instructions import InstructionSpec


COUNT = {'op': 'parameter', 'name': 'n', 'type': {'kind': 'int', 'bits': 32, 'signed': True}}


def _spec(head_type='any'):
    spec = InstructionSpec.from_mapping({'mnemonic': 'BKRST', 'arity': {'min': 2, 'max': 2},
        'operands': [{'name': 'D', 'role': 'write', 'data_type': head_type},
                     {'name': 'n', 'role': 'read', 'data_type': 'word'}]})
    return replace(spec, native_operand_order=('D', 'n'), verified_fields=('operand_order', 'operand_types'))


def _parameters(cross='next_word_bit_zero'):
    return [[{'name': 'D', 'occupied_bit_count': COUNT,
              'bit_sequence_layout': {'word_bits': 16, 'bit_order': 'ascending', 'cross_word': cross}}], []]


def _regions(head, count, parameters=None):
    return instruction_operand_regions(_spec(), 'BKRST', [head, count], 'Q03UDV',
        parameters=_parameters() if parameters is None else parameters)


def test_bkrst_own_cross_word_example_resolves_the_exact_selected_bits():
    region, = _regions('D10.2', 'K16')
    assert region['addresses'] == ['D10.' + format(bit, 'X') for bit in range(2, 16)] + ['D11.0', 'D11.1']
    assert region['resolved_count'] == 16
    assert region['physical_bit_region'] == {'address_space': 'D', 'start_bit': 162, 'bit_count': 16}
    assert 'requires' not in region
    region, = _regions('M0', 'K8')
    assert region['addresses'] == ['M' + str(i) for i in range(8)]
    assert region['physical_bit_region'] == {'address_space': 'M', 'start_bit': 0, 'bit_count': 8}


def test_word_bit_same_word_rule_does_not_grant_unknown_cross_word_order():
    region, = _regions('D0.4', 'K4', _parameters('not_stated'))
    assert region['addresses'] == ['D0.4', 'D0.5', 'D0.6', 'D0.7']
    assert region['physical_bit_region'] == {'address_space': 'D', 'start_bit': 4, 'bit_count': 4}
    region, = _regions('D0.F', 'K4', _parameters('not_stated'))
    assert 'word_bit_cross_word_layout_not_established' in region['requires']
    assert 'addresses' not in region and 'physical_bit_region' not in region
    # The opcode alone never supplies the missing source statement.
    region, = _regions('D10.2', 'K16', [[{'occupied_bit_count': COUNT}], []])
    assert 'word_bit_sequence_layout' in region['requires']
    assert 'physical_bit_region' not in region


def test_word_bit_layout_keeps_plain_word_dynamic_and_out_of_range_cases_distinct():
    region, = _regions('D10', 'K16')
    assert 'plain_word_bit_count_semantics_not_established' in region['requires']
    assert 'addresses' not in region and 'physical_bit_region' not in region and 'resolved_count' not in region
    region, = _regions('D10.2', 'D0')
    assert 'parameter_value:n' in region['requires']
    assert 'resolved_count' not in region and 'physical_bit_region' not in region
    from plc.device_profiles import DEVICE_LIMITS
    region, = _regions('D' + str(DEVICE_LIMITS['Q03UDV']['D']) + '.F', 'K2')
    assert 'storage_region_exceeds_device_range' in region['requires']
    assert 'addresses' not in region and 'physical_bit_region' not in region
    region, = _regions('T0', 'K2')
    assert region['addresses'] == ['T0', 'T1']
    assert 'physical_bit_region' not in region  # Coil/value alias mapping is not inferred.


def test_word_block_physical_coordinates_use_the_same_device_space():
    params = [[{'word_device_layout': {'kind': 'independent_elements', 'single_scalar': False,
                                      'device_unit_bits': 16, 'element_bits': 16, 'word_count': COUNT}}], []]
    region, = instruction_operand_regions(_spec('word'), 'BKRST', ['D0', 'K2'], 'Q03UDV', parameters=params)
    assert region['addresses'] == ['D0', 'D1']
    assert region['physical_bit_region'] == {'address_space': 'D', 'start_bit': 0, 'bit_count': 32}


def _validate_new_call(opcode, operands, model):
    from plc.ir import build_plc_ir, validate_plc_ir
    payload = {'device_comments': {}, 'rungs': [{
        'rung_id': 0, 'header_element': None, 'branches': [{
            'branch_id': 1, 'y_offset_level': 0,
            'inputs': [{'type': 'NO', 'address': 'X0'}],
            'outputs': [{'type': 'APP_INSTR', 'opcode': opcode, 'operands': operands}]}]}]}
    return validate_plc_ir(build_plc_ir(payload, plc_model=model))


def test_bkrst_registry_keeps_own_bit_head_scope_and_two_native_operands():
    import json
    from pathlib import Path
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from knowledge.instruction_compiler import call_admission_explanation

    common = json.loads((Path(__file__).parents[1] /
        'resources/instructions/mitsubishi/common.json').read_text(encoding='utf-8'))
    rows = [r for r in common['cpu_contracts'] if r['instruction']['mnemonic'] in {'BKRST', 'BKRSTP'}]
    assert len(rows) == 2
    for row in rows:
        opcode = row['instruction']['mnemonic']
        assert len(row['cpus']) == 53
        for model in row['cpus']:
            form = registry.resolve_catalog_form(opcode, cpu=model)
            assert form is not None and form.opcode == opcode
            assert form.spec.native_operand_order == ('D', 'n')
            assert [p.role.value for p in form.spec.operands] == ['write', 'read']
            assert form.spec.operands[0].data_type == 'any'
            assert 'Z' not in form.spec.operands[0].device_prefixes
            assert not form.spec.operands[0].address_forms
            assert form.spec.execution_form == ('pulse' if opcode.endswith('P') else 'continuous')
            assert call_admission_explanation(form, model)[0] == 'admitted'
            assert any(s['manual_id'] == 'sh080809engx' and s['pdf_page'] == 421
                       for s in form.spec.contract_sources)


@pytest.mark.parametrize('model', ['Q03UDV', 'Q02H', 'L02'])
@pytest.mark.parametrize('opcode', ['BKRST', 'BKRSTP'])
def test_bkrst_new_call_and_source_bit_span_share_the_active_candidate(model, opcode):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.device_profiles import DEVICE_LIMITS
    from gxw.token_encoder import encode_instruction
    from gxw.models import GXWFormatError

    spec = registry.resolve(opcode, cpu=model, operand_count=2)
    assert spec.definition_facts  # Never substitute catalogue metadata for the owner source.
    for operands in (['M0', 'K8'], ['D10.2', 'K16'], ['M0', 'D0'], ['T0', 'K2'], ['C0', 'K2'], ['F0', 'K2']):
        _validate_new_call(opcode, operands, model)
    for operands in (['K0', 'K8'], ['K4M0', 'K8'], ['Z0', 'K8'],
                     ['J1\\W0.2', 'K8'], ['U0\\G0.2', 'K8'], ['M0', 'E1.0']):
        with pytest.raises(ValueError):
            _validate_new_call(opcode, operands, model)
    region, = instruction_operand_regions(spec, opcode, ['D10.2', 'K16'], model)
    assert region['addresses'] == ['D10.' + format(i, 'X') for i in range(2, 16)] + ['D11.0', 'D11.1']
    assert region['physical_bit_region'] == {'address_space': 'D', 'start_bit': 162, 'bit_count': 16}
    last = DEVICE_LIMITS[model]['D']
    with pytest.raises(ValueError, match='extent exceeds'):
        _validate_new_call(opcode, ['D' + str(last) + '.F', 'K2'], model)
    # Syntax acceptance does not resolve the unselected word's count unit.
    _validate_new_call(opcode, ['D10', 'K16'], model)
    region, = instruction_operand_regions(spec, opcode, ['D10', 'K16'], model)
    assert 'plain_word_bit_count_semantics_not_established' in region['requires']
    assert not {'addresses', 'resolved_count', 'physical_bit_region'} & region.keys()
    profile = {'Q03UDV': 'q03udv', 'Q02H': 'q02', 'L02': 'l02'}[model]
    with pytest.raises(GXWFormatError, match='ambiguous'):
        encode_instruction(opcode, ['D10', 'K16'], profile=profile, plc_model=model, text_encoding='cp932')


def test_bkrst_own_selected_heads_roundtrip_the_existing_q03udv_source():
    from gxw.offline_project import edit_ladder_project, read_offline_project, read_ordered_program
    from gxw.project_templates import blank_ladder_baseline

    source = blank_ladder_baseline('Q03UDV')
    before = read_ordered_program(read_offline_project(source))
    end = next(i for i, row in enumerate(before['listing'].records) if getattr(row, 'mnemonic', None) == 'END')
    calls = [(op, operands) for op in ('BKRST', 'BKRSTP') for operands in (['M0', 'K8'], ['D10.2', 'K16'])]
    instructions = []
    for opcode, operands in calls:
        _validate_new_call(opcode, operands, 'Q03UDV')
        instructions += [{'op': 'LD', 'args': ['X0']}, {'op': opcode, 'args': operands}]
    result = edit_ladder_project(source, before['selected']['name'], [{
        'operation': 'insert_before', 'record_index': end, 'expected': {'op': 'END', 'args': []},
        'instructions': instructions}])
    after = read_ordered_program(read_offline_project(result.data), before['selected']['name'])
    assert [(i.mnemonic, i.args) for i in after['listing'].instructions if i.mnemonic in {'BKRST', 'BKRSTP'}] == [
        (op, tuple(operands)) for op, operands in calls]


def test_reviewed_admission_requires_the_own_bool_reference_bit_sequence_shape():
    import copy
    import json
    from pathlib import Path
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from knowledge.instruction_compiler import refresh_reviewed_call_admission

    path = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-bkrst-native-contract-repair/source-facts.json')
    source = next(e for e in json.loads(path.read_text(encoding='utf-8'))['entries']
                  if e['target_model'] == 'L02' and e['opcode'] == 'BKRST')
    original = copy.deepcopy(registry.definition_entry('BKRST', cpu='L02'))
    original['facts'] = [f for f in original['facts'] if not f['id'].startswith('authored:ql:')] + source['facts']
    typed_id = 'authored:ql:native_arguments_d_n:parameters.typed_operands'
    for cross in ('not_stated', 'next_word_bit_zero'):
        entry = copy.deepcopy(original)
        typed = next(f for f in entry['facts'] if f['id'] == typed_id)
        typed['value']['operands'][0]['bit_sequence_layout']['cross_word'] = cross
        assert refresh_reviewed_call_admission(entry, registry)
    mutations = [
        lambda p: p.pop('passing'),
        lambda p: p.update(passing='value'),
        lambda p: p.update(occupied_bit_count=0),
        lambda p: p.update(occupied_bit_count={'op': 'missing'}),
        lambda p: p.pop('bit_sequence_layout'),
        lambda p: p['bit_sequence_layout'].update(word_bits=True),
        lambda p: p['bit_sequence_layout'].update(word_bits=32),
        lambda p: p['bit_sequence_layout'].update(bit_order='descending'),
        lambda p: p['bit_sequence_layout'].update(cross_word='assumed'),
        lambda p: p['bit_sequence_layout'].update(extra=True),
        lambda p: p.update(direction='input'),
    ]
    for mutate in mutations:
        entry = copy.deepcopy(original)
        typed = next(f for f in entry['facts'] if f['id'] == typed_id)
        mutate(typed['value']['operands'][0])
        assert not refresh_reviewed_call_admission(entry, registry)
    for key, value in [('status', 'candidate_evidence'), ('sources', []),
                       ('scope', {'languages': ['st']})]:
        entry = copy.deepcopy(original)
        typed = next(f for f in entry['facts'] if f['id'] == typed_id)
        typed[key] = value
        assert not refresh_reviewed_call_admission(entry, registry)


def test_reviewed_bkcompare_candidate_bool_head_needs_its_exact_dynamic_layout():
    import copy
    from plc.instruction_owner_store import OwnerDefinitionStore
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    manifest = 'D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/runtime-integration-20261030/followup-54/candidate/instruction_definitions.manifest.json'
    store = OwnerDefinitionStore(manifest)
    try:
        original = copy.deepcopy(store.get_entry('BKCMP<', cpu='L02'))
    finally:
        store.close()
    spec = registry.resolve_catalog_form('BKCMP<', cpu='L02').spec
    assert spec.operands[2].data_type == 'bool' and spec.operands[2].bit_count == 1
    assert refresh_reviewed_call_admission(copy.deepcopy(original), registry)
    for mutation in ('shape', 'role'):
        entry = copy.deepcopy(original)
        typed = next(f for f in entry['facts'] if f['id'] ==
                     'authored:ql:native_arguments_s1_s2_d_n:parameters.typed_operands')
        head = typed['value']['operands'][2]
        if mutation == 'shape':
            head['bit_sequence_layout']['cross_word'] = 'assumed'
        else:
            head['direction'] = 'input'
        assert not refresh_reviewed_call_admission(entry, registry)

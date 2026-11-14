import copy

import pytest

from plc.instruction_knowledge import compose_programming_facts
from plc.instructions import InstructionRegistry, InstructionSpec


def test_family_signature_correction_preserves_own_cpu_dependency():
    from plc.instruction_knowledge import family_fact_groups
    registry = InstructionRegistry([InstructionSpec('SYNTH', cpu_support=frozenset({'FX3U'}))])
    scope = {'models': ['FX3U'], 'forms': ['SYNTH'], 'languages': ['st']}
    source = {'manual_id': 'own', 'revision': 'M', 'pdf_pages': [7]}
    cpu = {'id': 'authored:own:cpu', 'dimension': 'constraints.cpu_availability',
           'scope': scope, 'status': 'source_verified', 'verification': 'source_checked',
           'value': {'available': True}, 'sources': [source]}
    signature = {**copy.deepcopy(cpu), 'id': 'authored:own:signature',
                 'dimension': 'parameters.st_signature', 'value': {'call_form': 'SYNTH'},
                 'depends_on': [cpu['id']]}
    corrected = copy.deepcopy(signature)
    corrected.pop('depends_on')
    corrected['value']['call_kind'] = 'statement'
    registry._family_knowledge = ({'vendor': 'mitsubishi', 'base_opcode': 'SYNTH', 'facts': [corrected]},)
    bound = family_fact_groups(registry, 'SYNTH', cpu='FX3U', existing=[signature, cpu])
    assert bound[0]['depends_on'] == [cpu['id']]
    assert bound[0]['value']['call_kind'] == 'statement'
    unrelated = copy.deepcopy(cpu)
    unrelated['sources'][0]['pdf_pages'] = [8]
    assert 'depends_on' not in family_fact_groups(registry, 'SYNTH', cpu='FX3U', existing=[signature, unrelated])[0]


def test_native_parameter_replacement_keeps_own_version_through_recompose():
    from plc.instruction_knowledge import family_fact_groups, merge_family_facts
    registry = InstructionRegistry([InstructionSpec('SYNTH', cpu_support=frozenset({'FX3U'}))])
    native = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
    minimum = {'context': 'cpu_version', 'minimum': '2.20'}
    own = {'id': 'authored:own:parameters', 'dimension': 'parameters.typed_operands',
        'scope': {'models': ['FX3U'], 'forms': ['SYNTH'], 'languages': ['ladder']},
        'status': 'source_verified', 'verification': 'source_checked',
        'value': {'native_form': True, 'operands': [{'name': 'S', 'direction': 'input',
            'type': 'ANY16', 'range': {'device_prefixes': ['D'], 'literal_prefixes': []},
            'word_device_layout': {'word_count': 1, 'device_unit_bits': 16}}]},
        'conditions': [minimum, native],
        'sources': [{'manual_id': 'own', 'revision': 'R', 'pdf_pages': [266]}]}
    family = {**copy.deepcopy(own), 'id': 'family:SYNTH:parameters',
        'conditions': [native], 'replaces': [own['id']]}
    family['value']['operands'][0] = {'name': 'S', 'direction': 'input',
        'type': {'kind': 'int', 'bits': 16, 'signed': False},
        'range': {'minimum': 0, 'maximum': 9999}}
    registry._family_knowledge = ({'vendor': 'mitsubishi', 'base_opcode': 'SYNTH', 'facts': [family]},)
    incoming = family_fact_groups(registry, 'SYNTH', cpu='FX3U', existing=[own])
    assert incoming[0]['conditions'] == [native, minimum]
    pin = incoming[0]['value']['operands'][0]
    assert pin['type'] == family['value']['operands'][0]['type']
    assert pin['range'] == {'minimum': 0, 'maximum': 9999,
                           'device_prefixes': ['D'], 'literal_prefixes': []}
    assert pin['word_device_layout'] == {'word_count': 1, 'device_unit_bits': 16}
    active, history = merge_family_facts([own], incoming)
    assert own in history and len(active) == 1
    again = family_fact_groups(registry, 'SYNTH', cpu='FX3U', existing=active)
    assert again[0]['conditions'] == [native, minimum]
    assert again[0]['value']['operands'][0] == pin
    assert family['conditions'] == [native] and own['conditions'] == [minimum, native]
    for mismatch in ('model', 'form', 'language', 'manual', 'revision', 'page', 'mode', 'status'):
        wrong = copy.deepcopy(own)
        if mismatch == 'model': wrong['scope']['models'] = ['FX3UC']
        elif mismatch == 'form': wrong['scope']['forms'] = ['SYNTHP']
        elif mismatch == 'language': wrong['scope']['languages'] = ['st']
        elif mismatch == 'manual': wrong['sources'][0]['manual_id'] = 'other'
        elif mismatch == 'revision': wrong['sources'][0]['revision'] = 'S'
        elif mismatch == 'page': wrong['sources'][0]['pdf_pages'] = [267]
        elif mismatch == 'mode': wrong['conditions'] = [minimum, {'context': 'project_mode', 'equals': 'structured_ladder_block'}]
        else: wrong['status'] = 'unknown'
        result = family_fact_groups(registry, 'SYNTH', cpu='FX3U', existing=[wrong])
        assert result[0]['conditions'] == [native], mismatch
        assert 'literal_prefixes' not in result[0]['value']['operands'][0]['range'], mismatch


def test_family_cpu_denial_links_only_same_owner_condition_and_source():
    from plc.instruction_knowledge import _bind_owner_cpu_source
    source = {'manual_id': 'own', 'revision': 'P', 'pdf_pages': [7]}
    family = {'id': 'family:SYNTH:denied', 'dimension': 'constraints.cpu_availability',
              'scope': {'models': ['Q00'], 'forms': ['SYNTH'], 'languages': ['st']},
              'status': 'source_verified', 'verification': 'source_checked',
              'value': {'available': False, 'model_states': {'Q00': 'unsupported', 'Q01': 'unsupported'},
                        'rule': 'documented_CPU_instruction_scope'},
              'conditions': [{'context': 'cpu_serial_first_five', 'equals': 1000}], 'sources': [source]}
    own = copy.deepcopy(family)
    own['id'] = 'authored:own:denied'
    own['value']['model_states'] = {'Q00': 'unsupported'}
    own['sources'] = [{**source, 'opcode': 'SYNTH', 'target_model': 'Q00', 'source_ref': 'own:P:pages=7'}]
    bound = _bind_owner_cpu_source(copy.deepcopy(family), [own], opcode='SYNTH', cpu='Q00')
    assert bound['depends_on'] == [own['id']]
    assert bound['sources'][-1] == own['sources'][0]
    assert bound['value'] == family['value']
    assert bound['conditions'] == family['conditions']
    for changed in ('model', 'revision', 'page', 'condition', 'claim', 'status'):
        wrong = copy.deepcopy(own)
        if changed == 'model': wrong['scope']['models'] = ['Q01']
        elif changed == 'revision': wrong['sources'][0]['revision'] = 'Q'
        elif changed == 'page': wrong['sources'][0]['pdf_pages'] = [8]
        elif changed == 'condition': wrong['conditions'][0]['equals'] = 999
        elif changed == 'claim': wrong['value']['available'] = True
        else: wrong['status'] = 'declared_unverified'
        assert _bind_owner_cpu_source(copy.deepcopy(family), [wrong], opcode='SYNTH', cpu='Q00') == family


@pytest.mark.parametrize('change', ['none', 'model', 'form', 'revision', 'page', 'minimum', 'unverified', 'ambiguous'])
def test_conditional_native_family_binds_only_exact_owner_version(change):
    from plc.instruction_knowledge import _bind_owner_cpu_source
    native = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
    source = {'manual_id': 'own', 'revision': 'R', 'pdf_pages': [679]}
    family = {'id': 'family:SYNTH:conditional', 'dimension': 'constraints.cpu_availability',
        'scope': {'models': ['FX3U'], 'forms': ['SYNTH'], 'languages': ['ladder']},
        'status': 'source_verified', 'verification': 'source_checked',
        'value': {'model_states': {'FX3U': 'conditional'}},
        'conditions': [native, {'context': 'cpu_version', 'minimum': '2.20'}], 'sources': [source]}
    own = {**copy.deepcopy(family), 'id': 'authored:own:version', 'dimension': 'constraints.cpu_version',
        'value': {'minimum_series_version': 'Ver.2.20'}, 'conditions': [native],
        'sources': [{**source, 'opcode': 'SYNTH', 'target_model': 'FX3U'}]}
    if change == 'model': own['scope']['models'] = ['FX3UC']
    elif change == 'form': own['scope']['forms'] = ['SYNTHP']
    elif change == 'revision': own['sources'][0]['revision'] = 'Q'
    elif change == 'page': own['sources'][0]['pdf_pages'] = [678]
    elif change == 'minimum': own['value']['minimum_series_version'] = 'Ver.1.00'
    elif change == 'unverified': own['status'] = 'unknown'
    existing = [own]
    if change == 'ambiguous': existing.append({**own, 'id': 'authored:other:version'})
    bound = _bind_owner_cpu_source(copy.deepcopy(family), existing, opcode='SYNTH', cpu='FX3U')
    assert bound.get('depends_on', []) == ([own['id']] if change == 'none' else [])
    assert bound['value'] == family['value'] and bound['conditions'] == family['conditions']


@pytest.mark.parametrize('removed', [False, True])
def test_narrowed_family_scope_retires_old_owner_copy_and_keeps_history(removed):
    registry = InstructionRegistry([InstructionSpec('SYNTH', cpu_support=frozenset({'FX3U'}))])
    old = {'id': 'family:SYNTH:purpose', 'dimension': 'purpose.summary',
           'scope': {'models': ['FX3U'], 'forms': ['SYNTH'], 'languages': ['st']},
           'value': {'summary': 'Old form description'}, 'status': 'source_verified',
           'verification': 'source_checked', 'sources': [{'manual_id': 'own', 'revision': 'A', 'pdf_pages': [1]}]}
    template = copy.deepcopy(old)
    template['scope']['forms'] = ['SYNTHP']
    registry._family_knowledge = ({'vendor': 'mitsubishi', 'base_opcode': 'SYNTH',
                                  'facts': [] if removed else [template]},)
    own = {**copy.deepcopy(old), 'id': 'authored:own:purpose', 'value': {'summary': 'Own source description'}}
    current, history = compose_programming_facts(registry, 'SYNTH', cpu='FX3U', existing=[old, own])
    assert old['id'] not in {fact['id'] for fact in current}
    assert own in current
    assert old in history
    assert old['scope']['forms'] == ['SYNTH']
    dependent = {**copy.deepcopy(own), 'id': 'authored:own:dependent', 'depends_on': [old['id']]}
    with pytest.raises(ValueError, match='retired family scope'):
        compose_programming_facts(registry, 'SYNTH', cpu='FX3U', existing=[old, own, dependent])


@pytest.mark.parametrize('opcode', [
    '*P', 'D*', 'D*P', '+P', 'D+', 'D+P', '-P', 'D-', 'D-P', '/P', 'D/', 'D/P',
])
def test_exact_ql_arithmetic_owner_cpu_claim_survives_base_family_template(opcode):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    registry = DEFAULT_INSTRUCTION_REGISTRY
    owner = registry.definition_entry(opcode, cpu='Q03UDV')
    assert owner and owner['call_available'] is True
    facts = copy.deepcopy(owner['facts'])
    cpu_claim = next(fact for fact in facts
        if fact['id'] == 'authored:ql:cpu_applicability:constraints.cpu_availability:variant:2')
    assert cpu_claim['scope']['models'] == ['Q03UDV']
    assert cpu_claim['scope']['forms'] == [opcode]
    assert any(source.get('opcode') == opcode and source.get('target_model') == 'Q03UDV'
               for source in cpu_claim['sources'])

    composed, _history = compose_programming_facts(registry, opcode, cpu='Q03UDV', existing=facts)
    retained = next(fact for fact in composed if fact['id'] == cpu_claim['id'])
    assert retained == cpu_claim
    assert any(cpu_claim['id'] in fact.get('depends_on', [])
               and fact.get('scope', {}).get('forms') == [opcode] for fact in composed)

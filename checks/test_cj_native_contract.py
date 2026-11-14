"""CJ's direct P identity does not prove indexed forms or dynamic execution."""
import copy
import json
from pathlib import Path

import pytest

HERE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-cj-native-contract-repair')


@pytest.fixture(scope='module')
def source_registry():
    import plc.instructions as instructions
    import plc.validation as validation
    import plc.ir as ir
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    registry = instructions.DEFAULT_INSTRUCTION_REGISTRY
    local = copy.copy(registry)
    for name in ('_cpu_contracts', '_definition_contract_bases', '_definition_owners'):
        setattr(local, name, dict(getattr(registry, name)))
    local._loaded_definition_contracts = set(registry._loaded_definition_contracts)
    local._clear_definition_contracts()
    local.load_target_contracts(json.loads((HERE/'contract-additions.json').read_text())['cpu_contracts'])
    for source in json.loads((HERE/'source-facts.json').read_text())['entries']:
        model, op = source['target_model'], source['opcode']
        entry = copy.deepcopy(registry.definition_entry(op, cpu=model))
        entry['facts'] = [f for f in entry['facts'] if not f['id'].startswith('authored:ql:')] + source['facts']
        assert refresh_reviewed_call_admission(entry, local) and entry['call_available']
        local._definition_owners['mitsubishi', op, model] = entry
    mp = pytest.MonkeyPatch()
    for module in (instructions, validation, ir):
        mp.setattr(module, 'DEFAULT_INSTRUCTION_REGISTRY', local)
    yield local
    mp.undo()


def call(pointer, cpu, *, ordered=False, opcode='CJ'):
    from plc.ir import build_plc_ir, validate_plc_ir
    from checks.test_bklogic_native_contract import app_program
    program = (dict(device_comments={}, rungs=[dict(rung_id=1,
        ordered_instructions=[dict(op='LD', args=['X0']), dict(op=opcode, args=[pointer])])])
        if ordered else app_program(opcode, [pointer]))
    validate_plc_ir(build_plc_ir(program, plc_model=cpu))


@pytest.mark.parametrize('cpu', ['Q02H', 'Q03UDV', 'L02'])
def test_cj_direct_pointer_paths_keep_index_policy_unknown(source_registry, cpu):
    typed = next(f for f in source_registry.definition_entry('CJ', cpu=cpu)['facts']
                 if f['id'] == 'authored:ql:native_arguments_p:parameters.typed_operands')
    assert 'index_modification' not in typed['value']['operands'][0]['range']
    for ordered in (False, True):
        call('P1', cpu, ordered=ordered)
        for bad in ('D0', 'K1', 'P-1', 'P1Z0', 'P1.0'):
            with pytest.raises(ValueError):
                call(bad, cpu, ordered=ordered)
        for bad_op in ('DCJ', 'CJP', 'CJPP'):
            with pytest.raises(ValueError):
                call('P1', cpu, ordered=ordered, opcode=bad_op)


@pytest.mark.parametrize('cpu', ['Q02H', 'Q03UDV', 'L02'])
def test_cj_numeric_domain_comes_from_cpu_not_instruction_guess(source_registry, cpu):
    from plc.device_profiles import program_pointer_domain
    domain = program_pointer_domain(cpu)
    assert domain and domain['allocation_verified'] is False
    for ordered in (False, True):
        call('P'+str(domain['maximum_number']), cpu, ordered=ordered)
        with pytest.raises(ValueError, match='source-verified CPU domain'):
            call('P'+str(domain['maximum_number']+1), cpu, ordered=ordered)


@pytest.mark.parametrize('corruption', ['word_type', 'word_layout', 'role', 'range', 'unreviewed'])
def test_cj_pointer_identity_still_requires_own_non_memory_proof(source_registry, corruption):
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    entry = copy.deepcopy(source_registry.definition_entry('CJ', cpu='Q03UDV'))
    typed = next(f for f in entry['facts'] if f['id'] == 'authored:ql:native_arguments_p:parameters.typed_operands')
    pointer = typed['value']['operands'][0]
    if corruption == 'word_type':
        pointer['type'] = 'ANY16'
    elif corruption == 'word_layout':
        pointer['word_device_layout'] = {'word_count': 1}
    elif corruption == 'role':
        pointer['role'] = 'count'
    elif corruption == 'range':
        pointer.pop('range')
    else:
        typed.update(status='candidate_evidence', verification='not_performed')
    assert not refresh_reviewed_call_admission(entry, source_registry)

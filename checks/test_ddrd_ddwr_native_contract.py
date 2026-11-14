"""Distributed device-access descriptors do not prove remote execution semantics."""
import copy
import json
import os
from pathlib import Path

import pytest


@pytest.mark.parametrize('opcode', ['D.DDRD', 'D.DDWR'])
def test_distributed_read_write_existing_q_profile_descriptor_roundtrip(opcode):
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    # Native order was read from this family's own declaration. These addresses
    # only test the shipped descriptor; no remote access or flag behavior runs.
    args = ['H3E0', 'D0', 'D100', 'D200', 'M0']
    encoded = encode_instruction(opcode, args, profile='q03udv', plc_model='Q03UDV', text_encoding='cp932')
    listing = decode_token_program(parse_token_fragment(encoded, 0, len(encoded)),
                                   profile='q03udv', text_encoding='cp932')
    assert not listing.gaps
    assert [(r.mnemonic, list(r.args)) for r in listing.instructions] == [(opcode, args)]
    assert listing.reconstruct() == encoded


SOURCE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-ddrd-ddwr-native-contract-repair')


def _registry(opcode='D.DDRD', cpu='Q03UDV', mutate=None):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    if mutate is None and os.environ.get('GXWORKS_DD_TEST_SOURCE_OVERLAY') != '1':
        entry = original.definition_entry(opcode, cpu=cpu)
        assert entry and entry.get('call_available')
        return original
    registry = copy.copy(original)
    for name in ('_cpu_contracts', '_definition_contract_bases', '_definition_owners'):
        setattr(registry, name, dict(getattr(original, name)))
    registry._loaded_definition_contracts = set(original._loaded_definition_contracts)
    registry._clear_definition_contracts()
    if ('mitsubishi', opcode, cpu) not in registry._cpu_contracts:
        registry.load_target_contracts(json.loads((SOURCE / 'contract-additions.json').read_text(encoding='utf-8'))['cpu_contracts'])
    own = next(e for e in json.loads((SOURCE / 'source-facts.json').read_text(encoding='utf-8'))['entries']
               if e['target_model'] == cpu and e['opcode'] == opcode)
    entry = copy.deepcopy(original.definition_entry(opcode, cpu=cpu))
    facts = copy.deepcopy(own['facts'])
    if mutate is not None:
        mutate(facts)
    entry['facts'] = [f for f in entry['facts'] if not f['id'].startswith('authored:ql:')] + facts
    entry['facts'], _ = compose_programming_facts(registry, opcode, cpu=cpu, existing=entry['facts'])
    entry['call_available'] = False
    admitted = refresh_reviewed_call_admission(entry, registry)
    registry._definition_owners[('mitsubishi', opcode, cpu)] = entry
    return registry, admitted


def _bind(monkeypatch, registry):
    import plc.instructions
    import plc.ir
    import plc.validation
    monkeypatch.setattr(plc.instructions, 'DEFAULT_INSTRUCTION_REGISTRY', registry)
    monkeypatch.setattr(plc.ir, 'DEFAULT_INSTRUCTION_REGISTRY', registry)
    monkeypatch.setattr(plc.validation, 'DEFAULT_INSTRUCTION_REGISTRY', registry)


def _rungs(opcode, args):
    return [dict(rung_id=1, ordered_instructions=[dict(op='LD', args=['X0']),
        dict(op=opcode, args=args), dict(op='END', args=[])]),
        dict(rung_id=1, branches=[dict(branch_id=1, y_offset_level=0,
            inputs=[dict(type='NO', address='X0')],
            outputs=[dict(type='APP_INSTR', opcode=opcode, operands=args)])])]


@pytest.mark.parametrize('opcode,remote_index,remote_access', [('D.DDRD', 2, 'read'), ('D.DDWR', 3, 'write')])
def test_distributed_remote_identity_and_local_control_transfer_regions(monkeypatch, opcode, remote_index, remote_access):
    from plc.ir import build_plc_ir, validate_plc_ir
    selected = _registry(opcode)
    registry = selected[0] if isinstance(selected, tuple) else selected
    if isinstance(selected, tuple): assert selected[1]
    _bind(monkeypatch, registry)
    args = ['H3E0', 'D0', 'D100', 'D200', 'M0']
    for rung in _rungs(opcode, args):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
        network = ir['networks'][0]
        remote = args[remote_index]
        assert remote not in network['reads'] and remote not in network['writes']
        regions = network.get('access_regions', [])
        target = next(r for r in regions if r.get('address_space') == 'remote_CPU')
        assert target['operand_index'] == remote_index and target['access'] == remote_access
        assert target['target_CPU_parameter'] == 'n' and target['target_CPU_token'] == 'H3E0'
        assert 'remote_device_range_not_established' in target['requires']
        assert 'addresses' not in target
        data_index = 3 if opcode == 'D.DDRD' else 2
        data = next(r for r in regions if r['operand_index'] == data_index)
        from plc.instruction_definition import Expression, evaluate_expression, UnknownInstructionSemantics
        expression = Expression.from_mapping(data['count'])
        with pytest.raises(UnknownInstructionSemantics):
            evaluate_expression(expression, {})
        assert data['count']['op'] == 'read'
        assert data['count']['args'][0]['name'] == 'S1'
        assert data['count']['args'][1]['value'] == 1
        assert 'D1' in data['dependencies'] and 'resolved_count' not in data
        assert data['source_layout']['minimum'] == 1 and data['source_layout']['maximum'] == 100
        assert 'D1' in network['reads'] and 'D0' not in network['reads']
        assert {'D0', 'M0', 'M1'} <= set(network['writes'])
        for slot, expected in [(1, [('write', 0, 16), ('read', 16, 16)]),
                               (4, [('write', 0, 1), ('write', 1, 1)])]:
            physical = [r for r in regions if r['operand_index'] == slot]
            assert [(r['access'], r['physical_bit_region']['start_bit'],
                     r['physical_bit_region']['bit_count']) for r in physical] == expected
        assert registry.resolve(opcode, cpu='Q03UDV').completion is None
        validate_plc_ir(json.loads(json.dumps(ir)))


def _selected(opcode='D.DDRD'):
    selected = _registry(opcode)
    if isinstance(selected, tuple):
        assert selected[1]
        return selected[0]
    return selected


def test_distributed_unreviewed_source_cannot_authorize_remote_or_transfer_regions():
    from dataclasses import replace
    from plc.device_identity import instruction_operand_regions
    registry = _selected()
    spec = registry.resolve('D.DDRD', cpu='Q03UDV', operand_count=5)
    spec = replace(spec, definition_facts=tuple(
        replace(g, status='candidate_evidence') if g.dimension == 'parameters.typed_operands' else g
        for g in spec.definition_facts))
    assert instruction_operand_regions(spec, 'D.DDRD', ['H3E0', 'D0', 'D100', 'D200', 'M0'], 'Q03UDV') == []


def test_distributed_quoted_remote_keeps_namespace_and_unverified_grammar(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir
    _bind(monkeypatch, _selected())
    for rung in _rungs('D.DDRD', ['H3E1', 'D10', '"D999999"', 'D200', 'M0']):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
        network = ir['networks'][0]
        region = next(r for r in network['access_regions'] if r.get('address_space') == 'remote_CPU')
        assert region['head'] == '"D999999"' and region['target_CPU_token'] == 'H3E1'
        assert 'quoted_remote_device_grammar_not_established' in region['requires']
        assert 'remote_device_range_not_established' in region['requires']
        assert 'D999999' not in network['reads'] and 'D999999' not in network['writes']
        data = next(r for r in network['access_regions'] if r['operand_index'] == 3)
        assert data['dependencies'] == ['D11']
        assert data['count']['args'][0]['name'] == 'S1'
        assert 'H3E1' not in network['reads']
        validate_plc_ir(json.loads(json.dumps(ir)))


def test_distributed_incomplete_transfer_source_stays_unknown():
    from plc.device_identity import instruction_operand_regions
    from plc.instructions import reviewed_native_operand_parameters
    registry = _selected()
    args = ['H3E0', 'D0', 'D100', 'D200', 'M0']
    spec = registry.resolve('D.DDRD', cpu='Q03UDV', operand_count=5)
    parameters = copy.deepcopy(reviewed_native_operand_parameters(spec, 'D.DDRD', args, 'Q03UDV'))
    for pin in parameters[3]:
        pin['transfer_layout']['word_count_source'].pop('offset')
    region = next(r for r in instruction_operand_regions(spec, 'D.DDRD', args, 'Q03UDV', parameters=parameters)
                  if r['operand_index'] == 3)
    assert region['count'] is None and 'addresses' not in region
    assert 'transfer_count_source_not_resolved' in region['requires']


@pytest.mark.parametrize('change', ['device_value_read', 'local_memory_access', 'word_device_layout'])
def test_distributed_remote_identity_requires_explicit_nonlocal_semantics(change):
    from plc.instructions import reviewed_remote_device_identity, reviewed_native_operand_parameters
    registry = _selected()
    args = ['H3E0', 'D0', 'D100', 'D200', 'M0']
    spec = registry.resolve('D.DDRD', cpu='Q03UDV', operand_count=5)
    parameters = reviewed_native_operand_parameters(spec, 'D.DDRD', args, 'Q03UDV')
    remote = copy.deepcopy(next(p for p in parameters[2] if reviewed_remote_device_identity(p)))
    remote[change] = {'word_count': 1} if change == 'word_device_layout' else True
    assert not reviewed_remote_device_identity(remote)


@pytest.mark.parametrize('slot,prefix', [(1, 'D'), (4, 'M')])
def test_distributed_fixed_control_and_completion_extents_respect_cpu_range(monkeypatch, slot, prefix):
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.device_profiles import DEVICE_LIMITS
    _bind(monkeypatch, _selected())
    maximum = DEVICE_LIMITS['Q03UDV'][prefix]
    for delta, fits in [(1, True), (0, False)]:
        args = ['H3E0', 'D0', 'D100', 'D200', 'M0']
        args[slot] = prefix + str(maximum - delta)
        for rung in _rungs('D.DDRD', args):
            if fits:
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV'))
            else:
                with pytest.raises(ValueError, match='memory extent exceeds'):
                    validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV'))


@pytest.mark.parametrize('opcode,remote_index', [('D.DDRD', 2), ('D.DDWR', 3)])
def test_distributed_remote_source_exceptions_and_digit_alignment(monkeypatch, opcode, remote_index):
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.device_profiles import DEVICE_LIMITS
    from plc.validation import parse_device_address
    _bind(monkeypatch, _selected(opcode))
    limits = DEVICE_LIMITS['Q03UDV']
    accepted = [f'R{limits["R"] + 1}', f'ZR{limits["ZR"] + 1}',
                f'"D{limits["D"] + 1}"', 'K4M0']
    assert all(parse_device_address(token.strip('"'), 'Q03UDV') is None for token in accepted[:3])
    for token in accepted:
        args = ['H3E0', 'D0', 'D100', 'D200', 'M100']
        args[remote_index] = token
        for rung in _rungs(opcode, args):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
            validate_plc_ir(ir)
            network = ir['networks'][0]
            region = next(r for r in network['access_regions'] if r.get('address_space') == 'remote_CPU')
            assert region['head'] == token and 'addresses' not in region
            assert 'remote_device_range_not_established' in region['requires']
            for shadow in [token, token.strip('"'), 'M0', 'M1']:
                assert shadow not in network['reads'] and shadow not in network['writes']
    for token in ['K3M0', 'K4M1', 'Z0', 'D100Z0']:
        args = ['H3E0', 'D0', 'D100', 'D200', 'M100']
        args[remote_index] = token
        for rung in _rungs(opcode, args):
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV'))

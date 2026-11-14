"""CRC source contracts and storage access are separate from Native execution."""
import pytest


@pytest.mark.parametrize('cpu', ['Q03UDV', 'L02', 'FX3U'])
@pytest.mark.parametrize('opcode', ['CRC', 'CRCP'])
def test_crc_existing_profile_descriptors_roundtrip_without_implying_execution(cpu, opcode):
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    from gxw.token_profiles import source_token_profile
    args = ['D0', 'D100', 'K3']
    profile = source_token_profile(cpu)
    encoded = encode_instruction(opcode, args, profile=profile, plc_model=cpu, text_encoding='cp932')
    listing = decode_token_program(parse_token_fragment(encoded, 0, len(encoded)),
                                   profile=profile, text_encoding='cp932')
    assert not listing.gaps
    assert [(r.mnemonic, list(r.args)) for r in listing.instructions] == [(opcode, args)]
    assert listing.reconstruct() == encoded
import copy
import json
import os
from pathlib import Path

import pytest

BASE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029')


def _source_registry(cpu='Q00U', opcode='CRC', mutate=None):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    if mutate is None and os.environ.get('GXWORKS_CRC_TEST_SOURCE_OVERLAY') != '1':
        entry = original.definition_entry(opcode, cpu=cpu)
        assert entry and entry.get('call_available'), (cpu, opcode, original.fact_bundle_version)
        return original
    registry = copy.copy(original)
    for name in ('_cpu_contracts', '_definition_contract_bases', '_definition_owners'):
        setattr(registry, name, dict(getattr(original, name)))
    registry._loaded_definition_contracts = set(original._loaded_definition_contracts)
    registry._clear_definition_contracts()
    folder = BASE / ('fx-crc-native-contract-repair' if cpu.startswith('FX') else 'ql-crc-native-contract-repair')
    contracts = json.loads((folder / 'contract-additions.json').read_text(encoding='utf-8'))['cpu_contracts']
    if ('mitsubishi', opcode, cpu) not in registry._cpu_contracts:
        registry.load_target_contracts(contracts)
    source = next(e for e in json.loads((folder / 'source-facts.json').read_text(encoding='utf-8'))['entries']
                  if e['target_model'] == cpu and e['opcode'] == opcode)
    entry = copy.deepcopy(original.definition_entry(opcode, cpu=cpu))
    own = copy.deepcopy(source['facts'])
    if mutate is not None:
        mutate(own)
    entry['facts'] = [f for f in entry['facts'] if not f['id'].startswith(('authored:ql:', 'authored:applied:'))] + own
    entry['facts'], _ = compose_programming_facts(registry, opcode, cpu=cpu, existing=entry['facts'])
    entry['call_available'] = False
    assert refresh_reviewed_call_admission(entry, registry)
    registry._definition_owners[('mitsubishi', opcode, cpu)] = entry
    return registry


def _bind(monkeypatch, registry):
    import plc.instructions
    import plc.ir
    import plc.validation
    monkeypatch.setattr(plc.instructions, 'DEFAULT_INSTRUCTION_REGISTRY', registry)
    monkeypatch.setattr(plc.ir, 'DEFAULT_INSTRUCTION_REGISTRY', registry)
    monkeypatch.setattr(plc.validation, 'DEFAULT_INSTRUCTION_REGISTRY', registry)


def _rung(source='D0', destination='D100', count='K3', opcode='CRC'):
    return dict(rung_id=1, ordered_instructions=[dict(op='LD', args=['X0']),
        dict(op=opcode, args=[source, destination, count]), dict(op='END', args=[])])


@pytest.mark.parametrize('cpu', ['Q00U', 'Q03UDV', 'L02'])
@pytest.mark.parametrize('opcode', ['CRC', 'CRCP'])
def test_crc_source_qualified_heads_preserve_conditional_payload_regions(monkeypatch, cpu, opcode):
    from plc.ir import build_plc_ir, validate_plc_ir
    registry = _source_registry(cpu, opcode)
    _bind(monkeypatch, registry)
    ir = build_plc_ir(dict(device_comments={}, rungs=[_rung(opcode=opcode)]), plc_model=cpu)
    regions = ir['networks'][0].get('access_regions', [])
    assert regions, 'reviewed conditional payload was discarded as a scalar head'
    source_off = next(r for r in regions if r['operand_index'] == 0 and r.get('condition', {}).get('equals') is False)
    assert source_off['count'] is None and 'resolved_count' not in source_off
    assert 'conditional_source_span_not_established' in source_off['requires']

    assert source_off['source_layout']['published_example']['source_word_count'] == 3
    assert source_off['source_layout']['general_exact_span_verified'] is False
    assert all(r.get('condition', {}).get('device') == 'SM772' for r in regions)
    source_on = next(r for r in regions if r['operand_index'] == 0 and r['condition']['equals'] is True)
    assert source_on['addresses'] == ['D0', 'D1', 'D2']
    destinations = [r for r in regions if r['operand_index'] == 1]
    assert [(r['condition']['equals'], r['resolved_count']) for r in destinations] == [(False, 1), (True, 2)]
    assert {'D100', 'D101'} <= set(ir['networks'][0]['writes'])
    assert 'SM772' in ir['networks'][0]['reads']
    validate_plc_ir(json.loads(json.dumps(ir)))  # Includes count=None unknown span.


def test_crc_register_count_keeps_symbolic_span_and_conservative_head(monkeypatch):
    from plc.ir import build_plc_ir
    registry = _source_registry()
    _bind(monkeypatch, registry)
    ir = build_plc_ir(dict(device_comments={}, rungs=[_rung(count='D50')]), plc_model='Q00U')
    network = ir['networks'][0]
    source_on = next(r for r in network['access_regions'] if r['operand_index'] == 0 and r['condition']['equals'] is True)
    assert isinstance(source_on['count'], dict) and source_on['count']['name'] == 'n'
    assert 'resolved_count' not in source_on and 'addresses' not in source_on
    assert 'parameter_value:n' in source_on['requires']
    assert {'D0', 'D50', 'SM772'} <= set(network['reads'])
    assert {'D100', 'D101'} <= set(network['writes'])


@pytest.mark.parametrize('count', ['K0', 'K257'])
def test_crc_literal_count_outside_own_range_rejects(monkeypatch, count):
    from plc.ir import build_plc_ir, validate_plc_ir
    registry = _source_registry()
    _bind(monkeypatch, registry)
    with pytest.raises(ValueError):
        validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[_rung(count=count)]), plc_model='Q00U'))


@pytest.mark.parametrize('source,destination', [('K1', 'D100'), ('D0', 'K1')])
def test_crc_source_and_destination_are_storage_heads_not_literals(monkeypatch, source, destination):
    from plc.ir import build_plc_ir, validate_plc_ir
    registry = _source_registry()
    _bind(monkeypatch, registry)
    with pytest.raises(ValueError):
        validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[_rung(source, destination)]), plc_model='Q00U'))


def test_crc_conditional_range_requirements_do_not_become_unconditional_spans(monkeypatch):
    from plc.ir import build_plc_ir
    from plc.device_profiles import DEVICE_LIMITS
    registry = _source_registry()
    _bind(monkeypatch, registry)
    maximum = DEVICE_LIMITS['Q00U']['D']
    ir = build_plc_ir(dict(device_comments={}, rungs=[_rung(f'D{maximum - 1}', f'D{maximum}')]), plc_model='Q00U')
    regions = ir['networks'][0]['access_regions']
    for index in (0, 1):
        region = next(r for r in regions if r['operand_index'] == index and r['condition']['equals'] is True)
        assert 'conditional_storage_region_exceeds_device_range' in region['requires']
        assert 'storage_region_exceeds_device_range' not in region['requires']
        assert 'addresses' not in region
    destination_off = next(r for r in regions if r['operand_index'] == 1 and r['condition']['equals'] is False)
    assert destination_off['addresses'] == [f'D{maximum}']


def test_crc_incomplete_mode_branch_preserves_unknown_payload(monkeypatch):
    from plc.ir import build_plc_ir
    def mutate(facts):
        fact = next(f for f in facts if f['dimension'] == 'parameters.typed_operands' and f.get('value', {}).get('native_form'))
        fact['value']['operands'][0]['conditional_read_layout'].pop('ON')
    registry = _source_registry(mutate=mutate)
    _bind(monkeypatch, registry)
    ir = build_plc_ir(dict(device_comments={}, rungs=[_rung()]), plc_model='Q00U')
    region = next(r for r in ir['networks'][0]['access_regions'] if r['operand_index'] == 0 and r['condition']['equals'] is True)
    assert region['count'] is None and 'addresses' not in region
    assert 'conditional_source_span_not_established' in region['requires']


def test_crc_unreviewed_parameters_do_not_authorize_conditional_regions():
    from dataclasses import replace
    from plc.device_identity import instruction_operand_regions
    registry = _source_registry()
    spec = registry.resolve('CRC', cpu='Q00U', operand_count=3)
    groups = [replace(g, status='candidate_evidence') if g.dimension == 'parameters.typed_operands' else g
              for g in spec.definition_facts]
    spec = replace(spec, definition_facts=tuple(groups))
    assert instruction_operand_regions(spec, 'CRC', ['D0', 'D100', 'K3'], 'Q00U') == []



@pytest.mark.parametrize('count,expected', [('K1', 1), ('H100', 256)])
def test_crc_literal_count_endpoints_use_the_source_bound_span(monkeypatch, count, expected):
    from plc.ir import build_plc_ir, validate_plc_ir
    registry = _source_registry()
    _bind(monkeypatch, registry)
    ir = build_plc_ir(dict(device_comments={}, rungs=[_rung(destination='D1000', count=count)]), plc_model='Q00U')
    validate_plc_ir(ir)
    source_on = next(r for r in ir['networks'][0]['access_regions'] if r['operand_index'] == 0 and r['condition']['equals'] is True)
    assert source_on['resolved_count'] == expected
    assert len(source_on['addresses']) == expected


def test_crc_APP_uses_the_same_conditional_payload_projection(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir
    registry = _source_registry()
    _bind(monkeypatch, registry)
    rung = dict(rung_id=1, branches=[dict(branch_id=1, y_offset_level=0,
        inputs=[dict(type='NO', address='X0')],
        outputs=[dict(type='APP_INSTR', opcode='CRC', operands=['D0', 'D100', 'K3'])])])
    ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q00U')
    validate_plc_ir(ir)
    assert {'D100', 'D101'} <= set(ir['networks'][0]['writes'])
    assert 'SM772' in ir['networks'][0]['reads']
    assert len(ir['networks'][0]['access_regions']) == 4


@pytest.mark.parametrize('cpu', ['FX3U', 'FX3UC'])
@pytest.mark.parametrize('opcode', ['CRC', 'CRCP'])
def test_crc_FX_own_mode_projects_the_source_M8161_regions(monkeypatch, cpu, opcode):
    from plc.ir import build_plc_ir, validate_plc_ir
    registry = _source_registry(cpu, opcode)
    _bind(monkeypatch, registry)
    rungs = [_rung(opcode=opcode), dict(rung_id=1, branches=[dict(branch_id=1, y_offset_level=0,
        inputs=[dict(type='NO', address='X0')],
        outputs=[dict(type='APP_INSTR', opcode=opcode, operands=['D0', 'D100', 'K3'])])])]
    for rung in rungs:
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        validate_plc_ir(ir)
        regions = ir['networks'][0]['access_regions']
        assert regions and all(r.get('condition', {}).get('device') == 'M8161' for r in regions)
        assert 'M8161' in ir['networks'][0]['reads'] and 'SM772' not in ir['networks'][0]['reads']
        assert {'D100', 'D101'} <= set(ir['networks'][0]['writes'])
        source_off = next(r for r in regions if r['operand_index'] == 0 and r['condition']['equals'] is False)
        assert source_off['count'] is None and 'conditional_source_span_not_established' in source_off['requires']
        source_on = next(r for r in regions if r['operand_index'] == 0 and r['condition']['equals'] is True)
        assert source_on['addresses'] == ['D0', 'D1', 'D2']



def test_crc_FX_own_count_digit_and_existing_buffer_rules(monkeypatch):
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.instruction_constraints import check_instruction_constraints, literal_operand_values
    registry = _source_registry('FX3U', 'CRC')
    _bind(monkeypatch, registry)
    spec = registry.resolve('CRC', cpu='FX3U', operand_count=3)
    context = {'project_mode': 'native_ladder_instruction', 'cpu_version': '2.20'}
    for source, destination, count in [('D0', 'D1000', 'K1'), ('R0', 'D1000', 'H100'),
                                       ('K4M0', 'K4M1000', 'K3'), (r'U0\G0', 'D1000', 'K3')]:
        args = [source, destination, count]
        report = check_instruction_constraints('CRC', literal_operand_values(spec, args, plc_model='FX3U', opcode='CRC'),
            plc_model='FX3U', language='ladder', context=context, registry=registry)
        assert not any(f.get('severity') == 'error' for f in report['findings']), report
        ir = build_plc_ir(dict(device_comments={}, rungs=[_rung(source, destination, count)]), plc_model='FX3U')
        validate_plc_ir(ir)  # Structural acceptance does not prove current CPU version or execution.
        if source == 'K4M0':
            assert any('digit_group_sequence_layout' in r.get('requires', []) for r in ir['networks'][0]['access_regions'])
    for source, destination in [('K1M0', 'D1000'), ('D0', 'K3M1000')]:
        args = [source, destination, 'K3']
        report = check_instruction_constraints('CRC', literal_operand_values(spec, args, plc_model='FX3U', opcode='CRC'),
            plc_model='FX3U', language='ladder', context=context, registry=registry)
        assert any(c.get('dimension') == 'constraints.operand_devices' and c['status'] == 'failed'
                   for c in report['checks']), report
    for source, destination, count in [('D0', 'D1000', 'K0'), ('D0', 'D1000', 'K257'),
                                       ('K1', 'D1000', 'K3'), ('D0', 'K1', 'K3'),
                                       ('D0', 'D1000', 'M0'), ('D0', 'D1000', 'D50Z0')]:
        with pytest.raises(ValueError):
            validate_plc_ir(build_plc_ir(dict(device_comments={}, rungs=[_rung(source, destination, count)]), plc_model='FX3U'))

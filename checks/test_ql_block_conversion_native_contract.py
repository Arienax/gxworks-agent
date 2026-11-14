"""Native block conversion heads remain word arrays, separate from their count."""
import json
from pathlib import Path

import pytest

from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
from knowledge.instruction_compiler import call_admission_explanation
from plc.ordered_source import validate_ordered_rung


FORMS = {'BKBCD': (0x11, False, 295), 'BKBCDP': (0x11, True, 295),
         'BKBIN': (0x12, False, 297), 'BKBINP': (0x12, True, 297)}


def _rung(opcode, args):
    return {'rung_id': 1, 'ordered_instructions': [
        {'op': 'LD', 'args': ['X0']}, {'op': opcode, 'args': args}]}


def _validate_rung(opcode, args, model):
    from plc.ir import build_plc_ir, validate_plc_ir
    # A new APP_INSTR uses the public strict boundary. Ordered source is a
    # separate preservation path and cannot establish new-call permission.
    payload = {'device_comments': {}, 'rungs': [{
        'rung_id': 0, 'header_element': None, 'branches': [{
            'branch_id': 1, 'y_offset_level': 0,
            'inputs': [{'type': 'NO', 'address': 'X0'}],
            'outputs': [{'type': 'APP_INSTR', 'opcode': opcode, 'operands': args}]}]}]}
    return validate_plc_ir(build_plc_ir(payload, plc_model=model))


def test_block_conversion_native_contracts_keep_the_own_order_and_exact_cpus():
    common = json.loads((Path(__file__).parents[1] /
        'resources/instructions/mitsubishi/common.json').read_text(encoding='utf-8'))
    rows = [r for r in common['cpu_contracts'] if r['instruction']['mnemonic'] in FORMS]
    assert len(rows) == 4
    for row in rows:
        opcode = row['instruction']['mnemonic']
        assert len(row['cpus']) == 53
        for model in row['cpus']:
            form = registry.resolve_catalog_form(opcode, cpu=model)
            assert form is not None and form.opcode == opcode
            spec = form.spec
            assert spec.native_operand_order == ('S', 'D', 'n')
            assert [p.role.value for p in spec.operands] == ['read', 'write', 'read']
            assert all(p.data_type == 'word' for p in spec.operands)
            for head in spec.operands[:2]:
                assert 'Z' not in head.device_prefixes
                assert not head.address_forms
            assert spec.execution_form == ('pulse' if FORMS[opcode][1] else 'continuous')
            assert call_admission_explanation(form, model)[0] == 'admitted'
            assert spec.accepts_arity(3) and not spec.accepts_arity(2) and not spec.accepts_arity(4)
            assert any(s['manual_id'] == 'sh080809engx' and s['pdf_page'] == FORMS[opcode][2]
                       for s in spec.contract_sources)
        fx = registry.resolve_catalog_form(opcode, cpu='FX3U')
        assert fx is None or not fx.spec.supports_cpu('FX3U')


@pytest.mark.parametrize('opcode', FORMS)
def test_block_conversion_public_validation_rejects_constant_or_bit_heads(opcode):
    for count in ('K3', 'H3', 'D0', 'K4M0'):
        _validate_rung(opcode, ['D100', 'D200', count], 'Q03UDV')
    for args in (['K100', 'D200', 'K3'], ['D100', 'K200', 'K3'],
                 ['M100', 'D200', 'K3'], ['D100', 'M200', 'K3'],
                 ['K4M0', 'D200', 'K3'], ['D100', 'K4M0', 'K3'],
                 ['Z0', 'D200', 'K3'], ['D100', 'Z0', 'K3'],
                 ['J1\\W0', 'D200', 'K3'], ['D100', 'J1\\W0', 'K3'],
                 ['U0\\G0', 'D200', 'K3'], ['D100', 'U0\\G0', 'K3'],
                 ['D100', 'D200', 'E1.0']):
        with pytest.raises(ValueError):
            _validate_rung(opcode, args, 'Q03UDV')


@pytest.mark.parametrize('model,profile', [('Q03UDV', 'q03udv'), ('Q02H', 'q02'), ('L02', 'l02')])
def test_block_conversion_codec_uses_existing_exact_profile_and_literal_count(model, profile):
    from gxw.token_encoder import encode_instruction

    for opcode, (family, pulse, _) in FORMS.items():
        validate_ordered_rung(_rung(opcode, ['D100', 'D200', 'K3']), plc_model=model)
        encoded = encode_instruction(opcode, ['D100', 'D200', 'K3'],
            profile=profile, text_encoding='cp932', plc_model=model)
        width = 5 if pulse and model == 'Q03UDV' else 4
        header = bytes([6, 0x4b, width, family, 2, 6] if pulse else [5, 0x4b, width, family, 5])
        # The existing CPU grammar fixes opcode, width and operand order;
        # K3 is the single count value, never a device-array head.
        assert encoded == header + bytes.fromhex('04a8640404a8c80404e80304')


def test_block_conversion_existing_q03udv_gxw_preserves_all_four_literals():
    from gxw.offline_project import edit_ladder_project, read_offline_project, read_ordered_program
    from gxw.project_templates import blank_ladder_baseline

    baseline = blank_ladder_baseline('Q03UDV')
    initial = read_ordered_program(read_offline_project(baseline))
    end = next(i for i, row in enumerate(initial['listing'].records)
               if getattr(row, 'mnemonic', None) == 'END')
    instructions = []
    for opcode in FORMS:
        _validate_rung(opcode, ['D100', 'D200', 'K3'], 'Q03UDV')
        instructions.extend(_rung(opcode, ['D100', 'D200', 'K3'])['ordered_instructions'])
    saved = edit_ladder_project(baseline, initial['selected']['name'], [{
        'operation': 'insert_before', 'record_index': end, 'expected': {'op': 'END', 'args': []},
        'instructions': instructions}])
    after = read_ordered_program(read_offline_project(saved.data), initial['selected']['name'])
    actual = [(i.mnemonic, i.args) for i in after['listing'].instructions if i.mnemonic in FORMS]
    assert actual == [(opcode, ('D100', 'D200', 'K3')) for opcode in FORMS]


def test_block_conversion_layout_needs_its_own_literal_and_conversion_clause():
    from types import SimpleNamespace
    from tools.fact_ql_behavior_review import _native_bk_conversion_layout

    for opcode in FORMS:
        section = SimpleNamespace(manual='sh080809engx', bind=lambda _: [(1, FORMS[opcode][2])])
        for name in ('S', 'D'):
            pin = _native_bk_conversion_layout(section, opcode, {'name': name}, [])
            assert pin['word_device_layout']['word_count'] == {
                'op': 'parameter', 'name': 'n', 'type': {'kind': 'int', 'bits': 32, 'signed': True}}
            assert pin['word_device_layout']['single_scalar'] is False
            assert pin['range']['literal_prefixes'] == []
        count = _native_bk_conversion_layout(section, opcode, {'name': 'n'}, [])
        assert count['word_device_layout']['word_count'] == 1
        assert count['range']['literal_prefixes'] == ['K', 'H']
        section.bind = lambda _: []
        assert _native_bk_conversion_layout(section, opcode, {'name': 'S'}, []) == {'name': 'S'}
        section.bind = lambda _: [(1, 295)]
        section.manual = 'sh080783engp'
        assert _native_bk_conversion_layout(section, opcode, {'name': 'S'}, []) == {'name': 'S'}
        section.manual = 'sh080809engx'
        assert _native_bk_conversion_layout(section, 'BCD', {'name': 'S'}, []) == {'name': 'S'}

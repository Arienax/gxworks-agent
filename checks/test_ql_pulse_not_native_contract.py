"""Literal pulse-NOT contacts keep their input and source encoding identity."""
import pytest

from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
from plc.ordered_source import validate_ordered_rung


FAMILIES = {'LDPI': 0x04, 'LDFI': 0x05, 'ORPI': 0x0A,
            'ORFI': 0x0B, 'ANDPI': 0x15, 'ANDFI': 0x16}


def _rung(opcode, operand, number=1):
    rows = [] if opcode.startswith('LD') else [{'op': 'LD', 'args': ['X1']}]
    rows += [{'op': opcode, 'args': [operand]}, {'op': 'OUT', 'args': ['Y0']}]
    return {'rung_id': number, 'ordered_instructions': rows}


def test_pulse_not_contacts_have_exact_native_signature_without_aliasing_the_edge():
    for opcode in FAMILIES:
        for model in ('Q03UDV', 'Q03UD', 'Q00U', 'L02'):
            form = registry.resolve_catalog_form(opcode, cpu=model)
            assert form and form.opcode == opcode and not form.pulse
            spec = form.spec
            assert spec.supports_cpu(model)
            assert spec.native_operand_order == ('S',)
            assert spec.operands[0].role.value == 'read'
            assert spec.operands[0].data_type == 'bit'
            assert spec.operands[0].bit_count == 1
            assert spec.accepts_arity(1) and not spec.accepts_arity(0) and not spec.accepts_arity(2)
            assert '_NOT_' in spec.canonical_op
            assert spec.execution_form == 'continuous'
            assert 'execution_form' not in spec.verified_fields
            assert spec.st_call is None
        for model in ('FX3U', 'Q02H', 'Q25H'):
            form = registry.resolve_catalog_form(opcode, cpu=model)
            assert form is None or not form.spec.supports_cpu(model)


def test_pulse_not_contact_input_remains_one_source_authorized_bit():
    for opcode in FAMILIES:
        for operand in ('X0', 'D0.2', 'T0', 'J1\\X0', 'J1\\W0.2', 'U0\\G0.2'):
            validate_ordered_rung(_rung(opcode, operand), plc_model='Q03UDV')
        for operand in ('D0', 'K0', 'K1J1\\X0', 'J1\\W0', 'U0\\G0', 'U0\\G0.10'):
            try:
                validate_ordered_rung(_rung(opcode, operand), plc_model='Q03UDV')
            except ValueError:
                continue
            pytest.fail(f'{opcode} accepted a non-bit source argument: {operand}')


def test_pulse_not_ir_and_codecs_preserve_literals_and_q03udv_source_gxw():
    from gxw.offline_project import edit_ladder_project, read_offline_project, read_ordered_program
    from gxw.project_templates import blank_ladder_baseline
    from gxw.token_encoder import encode_instruction
    from plc.ir import build_plc_ir, ir_to_ladder

    for model, profile in (('Q03UDV', 'q03udv'), ('L02', 'l02')):
        rungs = [_rung(opcode, 'D0.2', index) for index, opcode in enumerate(FAMILIES, 1)]
        ir = build_plc_ir({'rungs': rungs, 'device_comments': {}}, plc_model=model)
        body = [row for rung in ir_to_ladder(ir)['rungs'] for row in rung['ordered_instructions']]
        assert [(row['op'], row['args']) for row in body if row['op'] in FAMILIES] == [
            (opcode, ['D0.2']) for opcode in FAMILIES]
        for opcode, family in FAMILIES.items():
            raw = encode_instruction(opcode, ['D0.2'], profile=profile,
                                     text_encoding='cp932', plc_model=model)
            assert raw[1] == family
        # L02 has a reviewed codec but no captured blank Ladder project.
        # The actual project-write assertion uses the Q03UDV source baseline.
        if model != 'Q03UDV':
            continue
        baseline = blank_ladder_baseline(model)
        initial = read_ordered_program(read_offline_project(baseline))
        end = next(i for i, row in enumerate(initial['listing'].records)
                   if getattr(row, 'mnemonic', None) == 'END')
        saved = edit_ladder_project(baseline, initial['selected']['name'], [{
            'operation': 'insert_before', 'record_index': end,
            'expected': {'op': 'END', 'args': []}, 'instructions': body}])
        readback = read_ordered_program(read_offline_project(saved.data), initial['selected']['name'])
        actual = [(record.mnemonic, tuple(record.args)) for record in readback['listing'].instructions
                  if record.mnemonic in FAMILIES]
        assert actual == [(opcode, ('D0.2',)) for opcode in FAMILIES]

"""COM is a reached zero-slot instruction; offline checks are not Native execution."""
import pytest

MODELS = ('Q02H', 'Q03UDV', 'L02')


def _rungs(opcode='COM', operands=()):
    return [('ordered', dict(rung_id=1, ordered_instructions=[dict(op=opcode, args=list(operands)),
                                                            dict(op='END', args=[])])),
            ('APP', dict(rung_id=1, branches=[dict(branch_id=1, y_offset_level=0, inputs=[],
                       outputs=[dict(type='APP_INSTR', opcode=opcode, operands=list(operands))])]))]


@pytest.mark.parametrize('cpu', MODELS)
def test_com_zero_slot_ordered_and_APP_do_not_require_execution_contacts(cpu):
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, reviewed_native_fact_groups
    spec = DEFAULT_INSTRUCTION_REGISTRY.resolve('COM', cpu=cpu, operand_count=0)
    gates = reviewed_native_fact_groups(spec, 'COM', [], cpu, dimensions={'execution.operation_condition'})
    own = [g for g in gates if g.value.get('enabled_when') == 'instruction_reached'
           and g.value.get('execution_input') == 'none'
           and g.value.get('ordinary_contact_execution') is False]
    assert own
    assert all(g.value.get('core_execution_established') is False for g in own)
    for path, rung in _rungs():
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu)
        validate_plc_ir(ir)
        instructions = ir['networks'][0]['instructions']
        assert [(row['op'], row['args']) for row in instructions] == (
            [('COM', []), ('END', [])] if path == 'ordered' else [('COM', [])])
        execution = ir['networks'][0]['execution']
        assert execution['triggers'] == [{'semantic': 'LEVEL', 'path': '$constant_true'}]
        assert ir['networks'][0]['reads'] == []


@pytest.mark.parametrize('operands', [('K1',), ('D0', 'D10')])
def test_com_zero_slot_contract_rejects_extra_operands(operands):
    from plc.ir import build_plc_ir, validate_plc_ir
    for _, rung in _rungs(operands=operands):
        with pytest.raises(ValueError):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
            validate_plc_ir(ir)


@pytest.mark.parametrize('opcode', ['DCOM', 'COMP'])
def test_com_does_not_invent_double_word_or_pulse_forms(opcode):
    from plc.ir import build_plc_ir, validate_plc_ir
    for _, rung in _rungs(opcode=opcode):
        with pytest.raises(ValueError):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
            validate_plc_ir(ir)


@pytest.mark.parametrize('cpu', MODELS)
def test_com_existing_zero_operand_descriptors_roundtrip(cpu):
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    from gxw.token_profiles import source_token_profile
    profile = source_token_profile(cpu)
    encoded = encode_instruction('COM', [], profile=profile, plc_model=cpu, text_encoding='cp932')
    assert encoded.hex() == '056a010605'
    listing = decode_token_program(parse_token_fragment(encoded, 0, len(encoded)),
                                   profile=profile, text_encoding='cp932')
    assert not listing.gaps
    assert [(r.mnemonic, list(r.args)) for r in listing.instructions] == [('COM', [])]
    assert listing.reconstruct() == encoded



@pytest.mark.parametrize('contact_location', ['branch', 'header', 'shared'])
def test_com_APP_rejects_contacts_that_would_claim_an_execution_gate(contact_location):
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.core import PLCCore
    from application.projects import public
    rung = _rungs()[1][1]
    contact = dict(type='NO', address='X0')
    if contact_location == 'branch':
        rung['branches'][0]['inputs'] = [contact]
    elif contact_location == 'header':
        rung['header_element'] = contact
    else:
        rung['shared_inputs'] = [contact]
    ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
    with pytest.raises(ValueError, match='COM'):
        validate_plc_ir(ir)
    result = public(PLCCore().validate_project(ir))
    assert result['valid'] is False and result['counts']['error'] > 0
    assert result.get('status') != 'unverified'
    assert 'COM' in result['error']


def test_com_ungated_APP_does_not_reject_an_unrelated_branch_contact():
    from plc.ir import build_plc_ir, validate_plc_ir
    rung = _rungs()[1][1]
    rung['branches'].append(dict(branch_id=2, y_offset_level=1,
        inputs=[dict(type='NO', address='X0')], outputs=[dict(type='COIL', address='Y0')]))
    ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model='Q03UDV')
    validate_plc_ir(ir)
    assert ir['networks'][0]['ladder']['branches'][0]['inputs'] == []
    assert [(r['op'], r['args']) for r in ir['networks'][0]['instructions']] == [
        ('COM', []), ('LD', ['X0']), ('OUT', ['Y0'])]

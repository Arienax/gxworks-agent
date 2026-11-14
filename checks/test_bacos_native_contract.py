"""BACOS owns a three-word BCD input record, not a scalar or a REAL call."""
import pytest
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
from plc.ordered_source import validate_ordered_rung


def rung(source='D0',destination='D10'):
    return dict(rung_id=1,ordered_instructions=[dict(op='LD',args=['X0']),dict(op='BACOS',args=[source,destination])])


def test_bacos_native_signature_is_literal_and_source_extent_is_three_words():
    for model in ['Q03UDV','Q02H','Q02PH','L02']:
        form=registry.resolve_catalog_form('BACOS',cpu=model)
        assert form and form.opcode=='BACOS'
        spec=form.spec
        assert spec.contract_level=='signature' and spec.semantic_kind.value=='vendor'
        assert spec.native_operand_order==('S','D')
        assert [o.device_point_count for o in spec.operands]==[3,1]
        assert spec.operands[0].role.value=='read' and spec.operands[1].role.value=='write'
        assert spec.accepts_arity(2) and not spec.accepts_arity(1) and not spec.accepts_arity(3)
        assert spec.st_call is None
    for model in ['Q00J','Q00','Q01','FX3U']:
        f=registry.resolve_catalog_form('BACOS',cpu=model)
        assert f is None or not f.spec.supports_cpu(model)
    pulse=registry.resolve_catalog_form('BACOSP',cpu='Q03UDV')
    assert pulse is not None and pulse.spec.execution_form=='pulse'
    assert pulse.spec.native_operand_order==('S','D')


def test_bacos_native_source_and_destination_device_cells_are_distinct():
    for source,dest in [('D0','D10'),('D0','K4Y40'),('R0','Z0'),('D0','J1\\W0'),('D0','U0\\G0')]:
        validate_ordered_rung(rung(source,dest),plc_model='Q03UDV')
    for source,dest in [('K0','D10'),('K4X0','D10'),('J1\\W0','D10'),('U0\\G0','D10'),('D0','K0')]:
        from plc.validation import _validate_source_operand_types
        spec=registry.resolve('BACOS',cpu='Q03UDV',operand_count=2)
        with pytest.raises(ValueError): _validate_source_operand_types(spec,[source,dest],'BACOS','Q03UDV')


def test_bacos_q_l_codecs_roundtrip_without_native_execution():
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    for model,profile in [('Q03UDV','q03udv'),('Q02H','q02'),('L02','l02')]:
        raw=encode_instruction('BACOS',['D0','D10'],profile=profile,plc_model=model,text_encoding='cp932')
        listing=decode_token_program(parse_token_fragment(raw,0,len(raw)),profile=profile,text_encoding='cp932')
        assert not listing.gaps
        assert [(i.mnemonic,i.args) for i in listing.instructions]==[('BACOS',('D0','D10'))]
        assert listing.reconstruct()==raw


def test_bacos_q03udv_source_bound_gxw_writeback():
    from gxw.offline_project import edit_ladder_project,read_offline_project,read_ordered_program
    from gxw.project_templates import blank_ladder_baseline
    baseline=blank_ladder_baseline('Q03UDV')
    initial=read_ordered_program(read_offline_project(baseline))
    end=next(i for i,row in enumerate(initial['listing'].records) if getattr(row,'mnemonic',None)=='END')
    saved=edit_ladder_project(baseline,initial['selected']['name'],[dict(operation='insert_before',record_index=end,expected=dict(op='END',args=[]),instructions=rung()['ordered_instructions'])])
    after=read_ordered_program(read_offline_project(saved.data))
    assert any(i.mnemonic=='BACOS' and i.args==('D0','D10') for i in after['listing'].instructions)


def test_bacos_three_word_layout_requires_the_actual_component_clauses():
    from types import SimpleNamespace
    from unittest.mock import patch
    from tools.fact_ql_behavior_review import _native_parameter_specs
    section=SimpleNamespace(manual='sh080809engx',heading_line='synthetic BACOS',text='synthetic',
        _native_parameter_contracts=[dict(allowed=['BACOS'],order=['S','D'],minimum=2,rows={
            'S':dict(description='Source device BCD 4 digits',witness='source'),
            'D':dict(description='Result device BCD 4 digits',witness='destination')})],bind=lambda w:[(1,662)])
    with patch('tools.fact_ql_behavior_review._native_data_type',return_value=dict(type='ANY16',encoding='BCD')),patch(
            'tools.fact_ql_behavior_review._parameter_form_type',side_effect=lambda s,o,p:p),patch('tools.fact_ql_behavior_review.family_of',return_value='special_math'):
        pins=_native_parameter_specs(section,'BACOS')[0]['value']['operands']
        assert [p['word_device_layout']['word_count'] for p in pins]==[3,1]
        assert pins[0]['word_device_layout']['single_scalar'] is False
        assert [f['role'] for f in pins[0]['referent_fields']]==['sign','integer_part','four_digit_fraction_part']
        assert [f['offset'] for f in pins[0]['referent_fields']]==[0,1,2]
        section.bind=lambda w:[]
        pins=_native_parameter_specs(section,'BACOS')[0]['value']['operands']
        assert all('word_device_layout' not in p for p in pins)


@pytest.mark.parametrize('model',['Q03UDV','Q02H','L02'])
@pytest.mark.parametrize('source_offset,destination_at_end,accepted',[(2,False,True),(1,False,False),(0,False,False),(None,True,True)])
def test_bacos_new_call_checks_actual_three_word_source_span_and_single_word_destination(model,source_offset,destination_at_end,accepted):
    from plc.device_profiles import DEVICE_LIMITS
    from plc.ir import build_plc_ir,validate_plc_ir
    last=DEVICE_LIMITS[model]['D']
    source='D'+str(last-source_offset) if source_offset is not None else 'D0'
    destination='D'+str(last) if destination_at_end else 'D10'
    payload=dict(rungs=[rung(source,destination)],device_comments={})
    if accepted:
        validate_plc_ir(build_plc_ir(payload,plc_model=model))
    else:
        with pytest.raises(ValueError,match='extent exceeds'):
            validate_plc_ir(build_plc_ir(payload,plc_model=model))

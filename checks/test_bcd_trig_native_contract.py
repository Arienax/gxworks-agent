"""Own BCD trigonometric Native forms retain literal, storage and call boundaries."""
import pytest
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
OPS=['BACOSP','BASIN','BASINP','BATAN','BATANP','BCOS','BCOSP','BSIN','BSINP','BTAN','BTANP']

def inverse(op):return op.removesuffix('P') in {'BACOS','BASIN','BATAN'}

@pytest.mark.parametrize('op',OPS)
def test_bcd_trig_own_literal_contract_and_actual_new_call_forms(op):
    from plc.validation import _validate_source_operand_types
    spec=registry.resolve(op,cpu='Q03UDV',operand_count=2)
    assert spec and spec.contract_level=='signature'
    assert spec.native_operand_order==('S','D') and spec.accepts_arity(2) and not spec.accepts_arity(3)
    assert [p.device_point_count for p in spec.operands]==([3,1] if inverse(op) else [1,3])
    assert spec.execution_form==('pulse' if op.endswith('P') else 'continuous')
    assert spec.st_call is None
    _validate_source_operand_types(spec,['D0','D10'],op,'Q03UDV')
    if inverse(op):
        for args in [['K0','D10'],['K4X0','D10'],['J1\\W0','D10']]:
            with pytest.raises(ValueError):_validate_source_operand_types(spec,args,op,'Q03UDV')
        _validate_source_operand_types(spec,['D0','K4Y40'],op,'Q03UDV')
    else:
        for arg in ['K0','H0360','K4X0','J1\\W0','U0\\G0']:_validate_source_operand_types(spec,[arg,'D10'],op,'Q03UDV')
        with pytest.raises(ValueError):_validate_source_operand_types(spec,['D0','K4Y40'],op,'Q03UDV')
    for model in ['FX3U','Q00J','Q00','Q01']:
        form=registry.resolve_catalog_form(op,cpu=model)
        assert form is None or not form.spec.supports_cpu(model)

@pytest.mark.parametrize('op',OPS)
@pytest.mark.parametrize('model,profile',[('Q03UDV','q03udv'),('Q02H','q02'),('L02','l02')])
def test_bcd_trig_q_l_lexical_roundtrip(op,model,profile):
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    raw=encode_instruction(op,['D0','D10'],profile=profile,plc_model=model,text_encoding='cp932')
    listing=decode_token_program(parse_token_fragment(raw,0,len(raw)),profile=profile,text_encoding='cp932')
    assert not listing.gaps and [(i.mnemonic,i.args) for i in listing.instructions]==[(op,('D0','D10'))]
    assert listing.reconstruct()==raw

@pytest.mark.parametrize('op',OPS)
@pytest.mark.parametrize('model',['Q03UDV','L02'])
def test_bcd_trig_new_call_record_span_is_three_device_points(op,model):
    from plc.device_profiles import DEVICE_LIMITS
    from plc.ir import build_plc_ir,validate_plc_ir
    last=DEVICE_LIMITS[model]['D'];record_slot=0 if inverse(op) else 1
    def check(record,scalar):
        args=[record,scalar] if record_slot==0 else [scalar,record]
        payload=dict(rungs=[dict(rung_id=1,ordered_instructions=[dict(op='LD',args=['X0']),dict(op=op,args=args)])],device_comments={})
        return validate_plc_ir(build_plc_ir(payload,plc_model=model))
    check('D'+str(last-2),'D'+str(last))
    with pytest.raises(ValueError,match='extent exceeds'):check('D'+str(last-1),'D0')
    with pytest.raises(ValueError,match='extent exceeds'):check('D'+str(last),'D0')


def test_native_condition_is_formal_only_for_the_existing_exact_project_mode():
    from tools.fact_ql_behavior_review import _formal_conditions,_typed_condition
    for mode in ['native_ladder_instruction','structured_ladder_block']:
        item=dict(context='project_mode',equals=mode)
        assert _typed_condition(item)
        conditions,classified=_formal_conditions([item])
        assert classified and conditions==[item]
    for item in [dict(context='project_mode',equals='guessed_mode'),dict(context='other',equals='native_ladder_instruction'),dict(context='project_mode',equals='native_ladder_instruction',extra=True)]:
        assert not _typed_condition(item)

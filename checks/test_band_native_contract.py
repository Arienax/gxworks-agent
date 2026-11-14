"""Own dead-band Native forms and version-qualified FX header facts."""
import json
from pathlib import Path
import pytest

OPS=('BANDP','DBAND','DBANDP')


@pytest.mark.parametrize('model,low,minimum',[('FX3U','2.19','2.20'),('FX3UC','0.99','1.00')])
@pytest.mark.parametrize('op',['BAND',*OPS])
def test_band_fx_own_version_header_has_executable_boundaries(model,low,minimum,op):
    from plc.instruction_constraints import check_fact_constraints
    from plc.instruction_knowledge import _bind_shared_definition
    payload=json.loads((Path(__file__).parents[1]/'resources/instructions/mitsubishi/instruction_families.json').read_text(encoding='utf-8'))
    family=next(f for f in payload['instruction_families'] if f['base_opcode']=='BAND')
    facts=[]
    for f in family['facts']:
        if f['dimension'] not in {'constraints.source_cpu_header','constraints.cpu_availability'} or model not in f['scope']['models'] or op not in f['scope']['forms']:continue
        facts.extend(_bind_shared_definition(family,f,[]))
    assert len(facts)==3
    for version,status in [(None,'unverified'),(low,'failed'),(minimum,'passed')]:
        context={'project_mode':'native_ladder_instruction'}
        if version is not None:context['cpu_version']=version
        report=check_fact_constraints(facts,{},target=op,plc_model=model,language='ladder',context=context)
        assert report['status']==status,report
    typed=next(f for f in family['facts'] if f['dimension']=='parameters.typed_operands' and f['id'].endswith(':native:typed') and op in f['scope']['forms'])
    assert {'context':'cpu_version','minimum_by_model':{'FX3U':'2.20','FX3UC':'1.00'}} in typed['conditions']


@pytest.mark.parametrize('op',OPS)
def test_band_native_registered_forms_and_actual_app_calls(op):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.ir import build_plc_ir,validate_plc_ir
    bits=32 if op.startswith('D') else 16
    spec=registry.resolve(op,cpu='Q03UDV',operand_count=4)
    assert spec and spec.native_operand_order==('S1','S2','S3','D') and spec.st_call is None
    assert [p.device_point_count for p in spec.operands]==[bits//16]*4
    def check(args):
        payload=dict(device_comments={},rungs=[dict(rung_id=1,header_element=None,branches=[dict(branch_id=1,y_offset_level=0,inputs=[dict(type='NO',address='X0')],outputs=[dict(type='APP_INSTR',opcode=op,operands=args)])])])
        validate_plc_ir(build_plc_ir(payload,plc_model='Q03UDV'))
    for args in [['K-10','K10','D0','D10'],['D0','D10','D20','D30'],['HFFFF','HFFFF','D0','K4Y40']]:check(args)
    for args in [['K-10','K10','D0','K0'],['D0','D10','D20'],['X0','D10','D20','D30']]:
        with pytest.raises(ValueError):check(args)


@pytest.mark.parametrize('op',OPS)
@pytest.mark.parametrize('model,profile',[('Q03UDV','q03udv'),('L02','l02'),('FX3U','fx3u')])
def test_band_native_literal_codec_roundtrip(op,model,profile):
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    operands=['D0','D10','D20','D30']
    raw=encode_instruction(op,operands,profile=profile,plc_model=model,text_encoding='cp932')
    listing=decode_token_program(parse_token_fragment(raw,0,len(raw)),profile=profile,text_encoding='cp932')
    assert not listing.gaps and [(i.mnemonic,i.args) for i in listing.instructions]==[(op,tuple(operands))]
    assert listing.reconstruct()==raw


@pytest.mark.parametrize('model',['FX3U','FX3UC'])
@pytest.mark.parametrize('op',['BAND',*OPS])
def test_band_fx_actual_app_signature_does_not_imply_version_is_known(model,op):
    from plc.ir import build_plc_ir,validate_plc_ir
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    spec=registry.resolve(op,cpu=model,operand_count=4)
    assert spec and spec.native_operand_order==('S1','S2','S3','D')
    def check(args):
        payload=dict(device_comments={},rungs=[dict(rung_id=1,header_element=None,branches=[dict(branch_id=1,y_offset_level=0,inputs=[dict(type='NO',address='X0')],outputs=[dict(type='APP_INSTR',opcode=op,operands=args)])])])
        validate_plc_ir(build_plc_ir(payload,plc_model=model))
    check(['K-10','K10','D0','D10'])
    with pytest.raises(ValueError):check(['K-10','K10','D0','K0'])
    # Missing CPU version has a separately tested unverified source outcome;
    # accepting the representation must not be reported as firmware proof.

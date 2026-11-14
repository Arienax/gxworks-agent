"""Exact own-R inverse trig construction/storage; no Core/Native math execution."""
import copy
import json
import os
from pathlib import Path
import pytest

HERE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-inverse-trig-native-contract-repair')
OPS = ('DACOS','DACOSP','DASIN','DASINP')


@pytest.fixture(scope='module')
def registry():
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as original
    if os.environ.get('GXWORKS_INVERSE_TRIG_TEST_SOURCE_OVERLAY') != '1':
        for cpu in ('FX3U','FX3UC'):
            for op in OPS:
                assert original.definition_entry(op,cpu=cpu)['call_available'], (cpu,op,original.fact_bundle_version)
        return original
    from plc.instruction_knowledge import compose_programming_facts
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    local = copy.copy(original)
    for name in ('_specs','_cpu_contracts','_definition_contract_bases','_definition_owners'):
        setattr(local,name,dict(getattr(original,name)))
    local._variant_index = None
    local._loaded_definition_contracts = set(original._loaded_definition_contracts)
    local._clear_definition_contracts()
    for e in json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']:
        cpu,op = e['target_model'],e['opcode']
        entry = copy.deepcopy(original.definition_entry(op,cpu=cpu))
        entry['facts'] = [f for f in entry['facts'] if not f['id'].startswith(('authored:ql:','authored:applied:'))] + copy.deepcopy(e['facts'])
        entry['facts'],_ = compose_programming_facts(local,op,cpu=cpu,existing=entry['facts'])
        entry['call_available'] = False
        assert refresh_reviewed_call_admission(entry,local),(cpu,op)
        local._definition_owners['mitsubishi',op,cpu] = entry
    return local


@pytest.fixture
def bound(monkeypatch,registry):
    import plc.instructions,plc.ir,plc.validation
    for module in (plc.instructions,plc.ir,plc.validation):
        monkeypatch.setattr(module,'DEFAULT_INSTRUCTION_REGISTRY',registry)
    return registry


def rungs(op,args):
    return [dict(rung_id=1,branches=[dict(branch_id=1,y_offset_level=0,
                inputs=[dict(type='NO',address='X0')],outputs=[dict(type='APP_INSTR',opcode=op,operands=args)])]),
            dict(rung_id=1,ordered_instructions=[dict(op='LD',args=['X0']),dict(op=op,args=args),dict(op='END',args=[])])]


def validate(cpu,op,args):
    from plc.ir import build_plc_ir,validate_plc_ir
    for rung in rungs(op,args):
        yield validate_plc_ir(build_plc_ir(dict(device_comments={},rungs=[rung]),plc_model=cpu))


@pytest.mark.parametrize('cpu', ['FX3U','FX3UC'])
@pytest.mark.parametrize('op', OPS)
def test_exact_REAL_double_word_APP_ordered_and_permissions(bound,cpu,op):
    from plc.ir import analyze_instruction_access
    from plc.device_profiles import DEVICE_LIMITS
    form = bound.resolve_catalog_form(op,cpu=cpu)
    assert form.opcode == op and form.spec.native_operand_order == ('S','D')
    assert form.spec.execution_form == ('pulse' if op.endswith('P') else 'continuous')
    assert [p.data_type for p in form.spec.operands] == ['real','real']
    assert [p.word_count for p in form.spec.operands] == [2,2]
    for args in (['D100','D200'],['R100','R200'],['E0.5','D200'],[r'U0\G100',r'U0\G200'],['D100Z0','D200Z1']):
        assert len(list(validate(cpu,op,args))) == 2
    assert analyze_instruction_access(op,['D100','D200'],plc_model=cpu) == (['D100','D101'],['D200','D201'])
    assert analyze_instruction_access(op,['E0.5','D200'],plc_model=cpu) == ([],['D200','D201'])
    for token in ('T0','C0','V0','Z0','M0','K4M0','K0','H0'):
        for index in (0,1):
            args = ['D100','D200'];args[index] = token
            for rung in rungs(op,args):
                from plc.ir import build_plc_ir,validate_plc_ir
                with pytest.raises(ValueError):
                    validate_plc_ir(build_plc_ir(dict(device_comments={},rungs=[rung]),plc_model=cpu))
    for args in (['D100','E0.5'],['D100'],['D100','D200','D300']):
        with pytest.raises(ValueError):list(validate(cpu,op,args))
    last = DEVICE_LIMITS[cpu]['D']
    # Extent rejection is independent of any stronger special-device write policy.
    with pytest.raises(ValueError):list(validate(cpu,op,[f'D{last}','D200']))
    with pytest.raises(ValueError):list(validate(cpu,op,['D100',f'D{last}']))


@pytest.mark.parametrize('op',OPS)
def test_literal_input_domain_is_actually_enforced_by_public_paths(bound,op):
    for value in ('E-1','E0','E1'):
        assert len(list(validate('FX3U',op,[value,'D200']))) == 2
    for value in ('E-1.25','E1.25','E1+9999'):
        for rung in rungs(op,[value,'D200']):
            from plc.ir import build_plc_ir,validate_plc_ir
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(dict(device_comments={},rungs=[rung]),plc_model='FX3U'))


@pytest.mark.parametrize('cpu,below,minimum', [('FX3U','2.19','2.20'),('FX3UC','0.99','1.00')])
def test_own_versions_remain_conditional_in_constraint_consumer(bound,cpu,below,minimum):
    from plc.instruction_constraints import check_instruction_constraints,literal_operand_values
    for op in OPS:
        spec = bound.resolve(op,cpu=cpu,operand_count=2)
        for version,failed in [(below,True),(minimum,False)]:
            result = check_instruction_constraints(op,literal_operand_values(spec,['E0.5','D200'],plc_model=cpu,opcode=op),plc_model=cpu,language='ladder',registry=bound,context={'project_mode':'native_ladder_instruction','cpu_version':version})
            checks = [c for c in result['checks'] if c.get('dimension') == 'constraints.cpu_availability']
            assert checks and any(c['status']=='failed' for c in checks) == failed,(cpu,op,version,result)


@pytest.mark.parametrize('op',OPS)
def test_existing_codec_and_CSV_preserve_own_literal_name(bound,tmp_path,op):
    from checks.test_ascii_conversion_native_contract import _codec_csv_roundtrip
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    _codec_csv_roundtrip(tmp_path,op,'FX3U','fx3u')
    raw = encode_instruction(op,['E0.5','D200'],profile='fx3u',plc_model='FX3U',text_encoding='cp932')
    listing = decode_token_program(parse_token_fragment(raw,0,len(raw)),profile='fx3u',text_encoding='cp932')
    assert not listing.gaps and listing.reconstruct() == raw
    assert [(r.mnemonic,list(r.args)) for r in listing.instructions] == [(op,['E0.5','D200'])]


def test_native_conversion_bytes_match_all_eight_independent_FX_cases(bound):
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    witness = HERE.parent/'runtime-integration-20261030/followup-69/native-real-literals/probes.json'
    evidence = json.loads(witness.read_text(encoding='utf-8'))
    assert evidence['cpu_code'] == 520 and len(evidence['cases']) == 8
    for case in evidence['cases']:
        assert case['encode_result'] == case['decode_result'] == '0x00000000'
        assert case['native_consumed_all'] is True
        op,source,destination = case['text'].split()
        assert op in OPS and source in {'E0.5','E5-1'} and destination == 'D200'
        raw = encode_instruction(op,[source,destination],profile='fx3u',plc_model='FX3U',text_encoding='cp932')
        raw += encode_instruction('END',[],profile='fx3u',plc_model='FX3U',text_encoding='cp932')
        assert raw == bytes.fromhex(case['body_hex']),case['text']
        listing = decode_token_program(parse_token_fragment(bytes.fromhex(case['body_hex']),0,len(raw)),profile='fx3u',text_encoding='cp932')
        assert not listing.gaps and listing.reconstruct() == bytes.fromhex(case['body_hex'])
        assert [(r.mnemonic,list(r.args)) for r in listing.instructions] == [(r['op'],r['args']) for r in case['native_records']]

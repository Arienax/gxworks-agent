"""Literal block logic contracts and source-bound n-word access, offline only."""
import copy,json
from pathlib import Path
import pytest

from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
from plc.ordered_source import validate_ordered_rung

OPS=('BKAND','BKANDP','BKOR','BKORP')
SOURCE_DIR=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-bklogic-native-contract-repair')


def rung(op='BKAND',args=('D0','D10','D20','K2')):
    return dict(rung_id=1,ordered_instructions=[dict(op='LD',args=['X0']),dict(op=op,args=list(args))])


def app_program(op,args):
    return dict(device_comments={},rungs=[dict(rung_id=1,header_element=None,branches=[dict(
        branch_id=1,y_offset_level=0,inputs=[dict(type='NO',address='X0')],
        outputs=[dict(type='APP_INSTR',opcode=op,operands=list(args))])])])


def test_bklogic_default_contracts_are_exact_literals_and_do_not_grant_core_or_fx():
    for op in OPS:
        for model in ('Q00J','Q02H','Q03UDV','Q02PH','L02'):
            form=registry.resolve_catalog_form(op,cpu=model)
            assert form and form.opcode==op
            spec=form.spec
            assert spec.contract_level=='signature' and spec.semantic_kind.value=='vendor'
            assert spec.native_operand_order==('S1','S2','D','n')
            assert spec.accepts_arity(4) and not spec.accepts_arity(3) and not spec.accepts_arity(5)
            assert [o.role.value for o in spec.operands]==['read','read','write','read']
            assert spec.execution_form==('pulse' if op.endswith('P') else 'continuous')
            assert spec.st_call is None
        for model in ('FX1N','FX3U','iQ-R'):
            form=registry.resolve_catalog_form(op,cpu=model)
            assert form is None or not form.spec.supports_cpu(model)
    assert registry.resolve_catalog_form('DBKAND',cpu='Q03UDV') is None
    assert registry.resolve_catalog_form('DBKOR',cpu='Q03UDV') is None


def test_bklogic_actual_app_instr_new_calls_respect_parameter_device_cells():
    from plc.ir import build_plc_ir,validate_plc_ir
    for op in OPS:
        for args in [('D0','D10','D20','K2'),('R0','K-32768','ZR0','K1'),
                     ('D0','K32767','D20','K4X0'),('D0','HFFFF','D20','K1'),
                     ('D0','D10','D20','J1\\W0'),('D0','D10','D20','U0\\G0'),
                     ('D0','D10','D20','Z0')]:
            validate_plc_ir(build_plc_ir(app_program(op,args),plc_model='Q03UDV'))
        for args in [('K1','D10','D20','K1'),('D0','D10','K1','K1'),
                     ('K4X0','D10','D20','K1'),('D0','K4X0','D20','K1'),
                     ('D0','D10','K4Y0','K1'),('J1\\X0','D10','D20','K1'),
                     ('D0','D10','D20','X0'),('D0','K32768','D20','K1'),
                     ('D0','K-32769','D20','K1'),('D0','H10000','D20','K1'),
                     ('J1\\W0','D10','D20','K2'),('D0','J1\\W0','D20','K2'),
                     ('D0','D10','J1\\W0','K2'),('U0\\G0','D10','D20','K2'),
                     ('D0','U0\\G0','D20','K2'),('D0','D10','U0\\G0','K2'),
                     ('Z0','D10','D20','K2'),('D0','Z0','D20','K2'),
                     ('D0','D10','Z0','K2')]:
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(app_program(op,args),plc_model='Q03UDV'))


def test_bklogic_existing_q_l_codec_preserves_literal_operands_and_bytes():
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    for model,profile in [('Q03UDV','q03udv'),('Q02H','q02'),('L02','l02')]:
        for op in OPS:
            for args in [('D0','D10','D20','K2'),('D0','K-1','D20','D30'),
                         ('D0','D10','D20','J1\\W0'),('D0','D10','D20','U0\\G0')]:
                raw=encode_instruction(op,list(args),profile=profile,plc_model=model,text_encoding='cp932')
                listing=decode_token_program(parse_token_fragment(raw,0,len(raw)),profile=profile,text_encoding='cp932')
                assert not listing.gaps
                assert [(i.mnemonic,i.args) for i in listing.instructions]==[(op,args)]
                assert listing.reconstruct()==raw


def test_bklogic_q03udv_source_bound_gxw_offline_writeback():
    from gxw.offline_project import edit_ladder_project,read_offline_project,read_ordered_program
    from gxw.project_templates import blank_ladder_baseline
    baseline=blank_ladder_baseline('Q03UDV')
    initial=read_ordered_program(read_offline_project(baseline))
    end=next(i for i,row in enumerate(initial['listing'].records) if getattr(row,'mnemonic',None)=='END')
    instructions=[i for op in OPS for i in rung(op)['ordered_instructions']]
    saved=edit_ladder_project(baseline,initial['selected']['name'],[dict(operation='insert_before',record_index=end,
        expected=dict(op='END',args=[]),instructions=instructions)])
    after=read_ordered_program(read_offline_project(saved.data))
    actual=[(i.mnemonic,i.args) for i in after['listing'].instructions if i.mnemonic in OPS]
    assert actual==[(op,('D0','D10','D20','K2')) for op in OPS]


@pytest.fixture
def source_delta_registry(monkeypatch):
    """The own reviewed source input, before root compiles it into the library.

    Keep public build/validate/access paths intact; only the registry's exact
    owner input is replaced in this test-local copy. Candidate checks omit this.
    """
    import plc.instructions as instructions
    import plc.validation as validation
    import plc.ir as ir
    fresh=json.loads((SOURCE_DIR/'source-facts.json').read_text(encoding='utf-8'))['entries']
    local=copy.copy(registry)
    for name in ('_cpu_contracts','_definition_contract_bases','_definition_owners'):
        setattr(local,name,dict(getattr(registry,name)))
    local._loaded_definition_contracts=set(registry._loaded_definition_contracts)
    local._clear_definition_contracts()
    for e in fresh:
        old=copy.deepcopy(registry.definition_entry(e['opcode'],cpu=e['target_model']))
        old['facts']=[f for f in old['facts'] if not f['id'].startswith('authored:ql:')]+e['facts']
        local._definition_owners[('mitsubishi',e['opcode'],e['target_model'])]=old
    monkeypatch.setattr(instructions,'DEFAULT_INSTRUCTION_REGISTRY',local)
    monkeypatch.setattr(validation,'DEFAULT_INSTRUCTION_REGISTRY',local)
    monkeypatch.setattr(ir,'DEFAULT_INSTRUCTION_REGISTRY',local)
    return local


def test_bklogic_source_delta_public_new_call_rejects_actual_n_word_overrun(source_delta_registry):
    from plc.device_profiles import DEVICE_LIMITS
    from plc.ir import build_plc_ir,validate_plc_ir
    for model in ('Q03UDV','Q02H','L02'):
        last=DEVICE_LIMITS[model]['D']
        for op in OPS:
            for index in (0,1,2):
                args=['D0','D10','D20','K2'];args[index]='D'+str(last)
                with pytest.raises(ValueError,match='extent exceeds'):
                    validate_plc_ir(build_plc_ir(dict(rungs=[rung(op,args)],device_comments={}),plc_model=model))
                args[index]='D'+str(last-1)
                validate_plc_ir(build_plc_ir(dict(rungs=[rung(op,args)],device_comments={}),plc_model=model))
            args=['D'+str(last),'K-1','D20','K1']
            validate_plc_ir(build_plc_ir(dict(rungs=[rung(op,args)],device_comments={}),plc_model=model))


def test_bklogic_source_delta_access_keeps_dynamic_count_and_repeated_literal(source_delta_registry):
    from plc.device_identity import instruction_operand_regions
    from plc.ir import analyze_instruction_access
    for op in OPS:
        spec=source_delta_registry.resolve(op,cpu='Q03UDV',operand_count=4)
        regions=instruction_operand_regions(spec,op,['D0','D10','D20','K2'],'Q03UDV')
        blocks={r['operand_index']:r for r in regions if r['operand_index']<3}
        assert [blocks[i]['addresses'] for i in range(3)]==[['D0','D1'],['D10','D11'],['D20','D21']]
        reads,writes=analyze_instruction_access(op,['D0','K-1','D20','K2'],plc_model='Q03UDV')
        assert {'D0','D1'}<=set(reads) and {'D20','D21'}<=set(writes)
        regions=instruction_operand_regions(spec,op,['D0','K-1','D20','D30'],'Q03UDV')
        assert not any(r['operand_index']==1 for r in regions)
        dynamic=[r for r in regions if r['operand_index'] in (0,2)]
        assert len(dynamic)==2
        assert all('parameter_value:n' in r['requires'] and 'addresses' not in r for r in dynamic)


def test_bklogic_source_delta_public_validation_rejects_partial_overlap_and_keeps_identical_head(source_delta_registry):
    from plc.ir import build_plc_ir,validate_plc_ir
    for op in OPS:
        for args in [('D0','D10','D1','K2'),('D10','D0','D1','K2')]:
            with pytest.raises(ValueError,match='disjoint|overlap'):
                validate_plc_ir(build_plc_ir(dict(rungs=[rung(op,args)],device_comments={}),plc_model='Q03UDV'))
        for args in [('D0','D10','D0','K2'),('D10','D0','D0','K2')]:
            validate_plc_ir(build_plc_ir(dict(rungs=[rung(op,args)],device_comments={}),plc_model='Q03UDV'))


def test_bklogic_n_extent_requires_own_complete_source_and_exact_literal():
    from types import SimpleNamespace
    from tools.fact_ql_behavior_review import _native_bk_logic_witnesses
    primary='Performs a logical product operation on the data located in the n points from the device designated by (S1), and the data located in the n points from the device designated by (S2), and stores the results into the area starting from the device designated by (D).'
    constant='The constant designated by (S2) can be between -32768 and 32767 (BIN 16-bit data).'
    for clauses in ([primary],[constant],[]):
        section=SimpleNamespace(manual='sh080809engx',bind=lambda w: [('own',365)] if w in clauses else [])
        assert _native_bk_logic_witnesses(section,'BKAND')==[]
    section.bind=lambda w:[('own',365)] if w in [primary,constant] else []
    assert _native_bk_logic_witnesses(section,'BKAND')==[primary,constant]
    for symbol in ('BKANDX','DBKAND','BK+','BKOR'):
        assert _native_bk_logic_witnesses(section,symbol)==[]
    section.manual='sh080783engp'
    assert _native_bk_logic_witnesses(section,'BKAND')==[]

"""Own BKCMP n-word inputs/n-bit output contracts, without execution claims."""
import copy,json
from pathlib import Path
import pytest
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry

OPS=tuple('BKCMP'+s+p for s in ('<','<=','<>','=','>','>=') for p in ('','P'))
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-bkcompare-native-contract-repair')


def program(op,args):
    return dict(device_comments={},rungs=[dict(rung_id=1,header_element=None,branches=[dict(
        branch_id=1,y_offset_level=0,inputs=[dict(type='NO',address='X0')],
        outputs=[dict(type='APP_INSTR',opcode=op,operands=list(args))])])])


def validate(op,args,model='Q03UDV'):
    from plc.ir import build_plc_ir,validate_plc_ir
    validate_plc_ir(build_plc_ir(program(op,args),plc_model=model))


def test_bkcompare_default_literal_signatures_are_four_arguments_and_vendor_only():
    for op in OPS:
        for model in ('Q00J','Q02H','Q03UDV','Q02PH','L02'):
            form=registry.resolve_catalog_form(op,cpu=model)
            assert form and form.opcode==op
            spec=form.spec
            assert spec.contract_level=='signature' and spec.semantic_kind.value=='vendor'
            assert spec.native_operand_order==('S1','S2','D','n')
            assert spec.accepts_arity(4) and not spec.accepts_arity(3) and not spec.accepts_arity(5)
            assert [p.role.value for p in spec.operands]==['read','read','write','read']
            assert spec.operands[2].data_type in ('bit','bool')
            assert spec.execution_form==('pulse' if op.endswith('P') else 'continuous')
            assert spec.st_call is None
        for model in ('FX1N','FX3U'):
            f=registry.resolve_catalog_form(op,cpu=model)
            assert f is None or not f.spec.supports_cpu(model)


def test_bkcompare_actual_app_new_call_uses_own_merged_device_cells():
    for op in OPS:
        for args in [('D0','D100','M0','K4'),('K-32768','D100','D20.4','K4'),
                     ('HFFFF','D100','R20.4','K4'),('D0','D100','M0','J1\\W0'),
                     ('D0','D100','M0','U0\\G0'),('D0','D100','M0','Z0'),
                     ('D0','D100','M0','K4X0')]:validate(op,args)
        for index in (0,1,2):
            for head in ('J1\\W0','U0\\G0','Z0'):
                args=['D0','D100','M0','K4'];args[index]=head
                with pytest.raises(ValueError):validate(op,args)
        for args in [('D0','K1','M0','K4'),('K32768','D100','M0','K4'),
                     ('D0','D100','K4M0','K4'),
                     ('D0','D100','T0','K4'),('D0','D100','C0','K4'),
                     ('D0','D100','ST0','K4'),('D0','D100','D20.10','K4'),
                     ('D0','D100','M0','X0')]:
            with pytest.raises(ValueError):validate(op,args)
    for invalid in ('BKCMP<<','BKCMP==','BKCMP<=>','BKCMP<PX','BKCMP<>PP'):
        with pytest.raises(ValueError):validate(invalid,['D0','D100','M0','K4'])


def test_bkcompare_own_existing_q_l_codecs_preserve_bit_selector_and_literals():
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    for model,profile in [('Q03UDV','q03udv'),('Q02H','q02'),('L02','l02')]:
        for op in OPS:
            for args in [('D0','D100','M0','K4'),('K1000','D100','D20.4','K4')]:
                raw=encode_instruction(op,list(args),profile=profile,plc_model=model,text_encoding='cp932')
                listing=decode_token_program(parse_token_fragment(raw,0,len(raw)),profile=profile,text_encoding='cp932')
                assert not listing.gaps and listing.reconstruct()==raw
                assert [(i.mnemonic,i.args) for i in listing.instructions]==[(op,args)]


def test_bkcompare_q03udv_source_gxw_offline_writeback():
    from gxw.offline_project import edit_ladder_project,read_offline_project,read_ordered_program
    from gxw.project_templates import blank_ladder_baseline
    baseline=blank_ladder_baseline('Q03UDV');initial=read_ordered_program(read_offline_project(baseline))
    end=next(i for i,row in enumerate(initial['listing'].records) if getattr(row,'mnemonic',None)=='END')
    body=[i for op in OPS for i in [dict(op='LD',args=['X0']),dict(op=op,args=['D100','D200','D20.4','K4'])]]
    saved=edit_ladder_project(baseline,initial['selected']['name'],[dict(operation='insert_before',record_index=end,
        expected=dict(op='END',args=[]),instructions=body)])
    after=read_ordered_program(read_offline_project(saved.data))
    assert [(i.mnemonic,i.args) for i in after['listing'].instructions if i.mnemonic in OPS]==[
        (op,('D100','D200','D20.4','K4')) for op in OPS]


@pytest.fixture
def source_view(monkeypatch):
    """Real own reviewed source input; public validators stay intact."""
    import plc.instructions as instructions
    import plc.validation as validation
    import plc.ir as ir
    entries=json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    local=copy.copy(registry)
    for name in ('_cpu_contracts','_definition_contract_bases','_definition_owners'):
        setattr(local,name,dict(getattr(registry,name)))
    local._loaded_definition_contracts=set(registry._loaded_definition_contracts)
    local._clear_definition_contracts()
    for e in entries:
        owner=copy.deepcopy(registry.definition_entry(e['opcode'],cpu=e['target_model']))
        owner['facts']=[f for f in owner['facts'] if not f['id'].startswith(('authored:ql:','family:BAND:'))]+e['facts']
        local._definition_owners[('mitsubishi',e['opcode'],e['target_model'])]=owner
    monkeypatch.setattr(instructions,'DEFAULT_INSTRUCTION_REGISTRY',local)
    monkeypatch.setattr(validation,'DEFAULT_INSTRUCTION_REGISTRY',local)
    monkeypatch.setattr(ir,'DEFAULT_INSTRUCTION_REGISTRY',local)
    return local


def test_bkcompare_source_public_n_word_inputs_and_n_bit_output_bounds(source_view):
    from plc.device_profiles import DEVICE_LIMITS
    for model in ('Q03UDV','Q02H','L02'):
        last_word=DEVICE_LIMITS[model]['D'];last_bit=DEVICE_LIMITS[model]['M']
        for op in OPS:
            for index in (0,1):
                args=['D100','D200','M0','K2'];args[index]='D'+str(last_word)
                with pytest.raises(ValueError,match='extent exceeds'):validate(op,args,model)
                args[index]='D'+str(last_word-1);validate(op,args,model)
            with pytest.raises(ValueError,match='extent exceeds'):
                validate(op,['D100','D200','M'+str(last_bit),'K2'],model)
            validate(op,['D100','D200','M'+str(last_bit-1),'K2'],model)
            validate(op,['D100','D200','D'+str(last_word)+'.C','K4'],model)


def test_bkcompare_source_public_mixed_units_reject_real_overlap_without_identical_exception(source_view):
    for op in OPS:
        for args in [('D0','D100','D0.4','K4'),('D100','D0','D0.4','K4'),
                     ('D0','D100','D1.4','K4'),('D0','D100','D0.0','K1'),
                     ('K1000','D0','D0.4','K4')]:
            with pytest.raises(ValueError,match='overlap|disjoint'):validate(op,args)
        for args in [('D0','D100','D5.4','K4'),('D0','D100','M0','K4'),
                     ('K1000','D100','D0.4','K4')]:validate(op,args)


def test_bkcompare_source_regions_use_one_bit_coordinate_engine_and_keep_unknown(source_view):
    from plc.device_identity import instruction_operand_regions
    from plc.instruction_constraints import check_instruction_constraints,literal_operand_values
    for op in OPS:
        spec=source_view.resolve(op,cpu='Q03UDV',operand_count=4)
        regions=instruction_operand_regions(spec,op,['D1','D100','D0.4','K4'],'Q03UDV')
        source=next(r for r in regions if r['operand_index']==0)
        result=next(r for r in regions if r['operand_index']==2)
        assert source['physical_bit_region']==dict(address_space='D',start_bit=16,bit_count=64)
        assert result['physical_bit_region']==dict(address_space='D',start_bit=4,bit_count=4)
        for args in [('D0','D100','D0.F','K4'),('D0Z0','D100','D0.4','K4'),('D0','D100','D0.4','D300'),
                     ('D0','D100','D20','K4')]:
            report=check_instruction_constraints(op,literal_operand_values(spec,args,plc_model='Q03UDV',opcode=op),
                plc_model='Q03UDV',language='ladder',context={'project_mode':'native_ladder_instruction'},registry=source_view)
            own=[c for c in report['checks'] if c['fact_id'].startswith('authored:ql:block_compare_overlap_')]
            assert own and all(c['status']=='unverified' for c in own if c['fact_id'].endswith('s1:constraints.disjoint_regions'))
            unresolved=instruction_operand_regions(spec,op,args,'Q03UDV')
            assert any(r.get('requires') for r in unresolved)


def test_bkcompare_source_exact_units_unknown_does_not_fall_back_to_old_word_arithmetic(source_view):
    from plc.instruction_constraints import check_fact_constraints,literal_operand_values
    op='BKCMP=';spec=source_view.resolve(op,cpu='Q03UDV',operand_count=4)
    facts=copy.deepcopy(source_view.definition_entry(op,cpu='Q03UDV')['facts'])
    constraints=[f for f in facts if f['id'].startswith('authored:ql:block_compare_overlap_')]
    assert len(constraints)==2
    for fact in constraints:fact['value']['destination_unit']='unknown'
    report=check_fact_constraints(facts,literal_operand_values(spec,['D0','D100','D0.4','K4'],plc_model='Q03UDV',opcode=op),
        target=op,plc_model='Q03UDV',language='ladder',context={'project_mode':'native_ladder_instruction'},
        root_ids=[f['id'] for f in constraints],registry=source_view)
    assert report['status']=='unverified'

"""FX CALL operands are pointer identities even when their source symbol is D."""
import copy
import pytest
from tools import fact_applied_review as producer


@pytest.mark.parametrize('opcode,target,expected', [
    ('CALL', 'P0Z0', '055403010504f0000404d00004'),
    ('CALL', 'P64V7', '055403010504f4070404d04004'),
    ('CALLP', 'P0Z0', '06540301020604f0000404d00004'),
    ('CALLP', 'P64V7', '06540301020604f4070404d04004'),
])
def test_CALL_indexed_target_matches_independent_vendor_bytes(opcode,target,expected):
    from gxw.token_encoder import encode_instruction, _decode_one
    raw=encode_instruction(opcode,[target],profile='fx3u',plc_model='FX3U',text_encoding='cp936')
    assert raw==bytes.fromhex(expected)
    decoded=_decode_one(raw,'fx3u','cp936')
    assert decoded.mnemonic==opcode and tuple(decoded.args)==(target,)


@pytest.mark.parametrize('opcode,target,model,profile', [
    ('CJ','P0Z0','FX3U','fx3u'), ('CALL','P0Z0','FX3UC','fx3u'),
    ('CALL','P0Z0','FX3G','fx3g'), ('CALL','P63Z0','FX3U','fx3u'),
    ('CALLP','P4096Z0','FX3U','fx3u'), ('CALL','P0Z8','FX3U','fx3u'),
    ('CALL','P0V8','FX3U','fx3u'), ('CALL','P0.1Z0','FX3U','fx3u'),
])
def test_CALL_indexed_codec_does_not_expand_other_identity_or_index_domains(opcode,target,model,profile):
    from gxw.token_encoder import encode_instruction
    from gxw.models import GXWFormatError
    with pytest.raises(GXWFormatError):
        encode_instruction(opcode,[target],profile=profile,plc_model=model,text_encoding='cp936')


def test_indexed_P_reader_remains_context_bound():
    from gxw.models import GXWFormatError
    from gxw.token_listing import _operand_groups, decode_token_program
    from gxw.token_pou import parse_token_fragment
    # Exact vendor operand bytes do not become a generally accepted P device.
    operand=bytes.fromhex('04f0000404d00004')
    with pytest.raises(GXWFormatError,match='unobserved indexed base type'):
        _operand_groups(parse_token_fragment(operand,0,len(operand)).tokens,'cp936')
    # Previously observed CJ header with the newly witnessed CALL operand:
    # the reader must retain a gap, rather than granting CJ indexed-P support.
    raw=bytes.fromhex('054d030005')+operand
    assert decode_token_program(parse_token_fragment(raw,0,len(raw)),profile='fx3u',text_encoding='cp936').gaps
    from gxw.token_encoder import encode_instruction
    with pytest.raises(GXWFormatError):
        encode_instruction('BREAK',['P0Z0'],profile='fx3u',plc_model='FX3U',text_encoding='cp936')


def test_CALL_codec_source_projection_keeps_witness_scope_and_execution_unknown():
    import json
    from pathlib import Path
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units,technical_knowledge_text
    root=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-call-indexed-pointer-codec-repair')
    entries=json.loads((root/'source-facts.json').read_text(encoding='utf-8'))['entries']
    def walk(x):
        if isinstance(x,dict):
            yield x
            for v in x.values():yield from walk(v)
        elif isinstance(x,list):
            for v in x:yield from walk(v)
    assert len(entries)==2
    for e in entries:
        assert e['target_model']=='FX3U'
        p=project_packet(dict(kind='instruction',target=e['opcode'],scope={'models':['FX3U']},content_origin='authored_fact',value=e),plc_model='FX3U',language='ladder',project_mode='native_ladder_instruction',instance_context={'cpu_version':'2.20'},aspects=['programming_semantics'])
        record=dict(text=json.dumps(p),fact_kind='instruction',fact_target=e['opcode'],_knowledge_context=dict(model='FX3U',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
        nodes=list(walk(technical_context_units(technical_knowledge_text(record))))
        bound=next(n for n in nodes if n.get('indexed_pointer_codec_verified') is True)
        scope=bound['indexed_pointer_codec_verification_scope']
        assert scope['vendor_cpu_code']==520 and scope['encoding_profile']=='fx3u'
        assert scope['target_operands']==['P0Z0','P64V7'] and scope['opcode']==e['opcode']
        assert scope['native_project_compile_verified'] is False and scope['general_indexed_pointer_domain_verified'] is False
        for key in ('effective_indexed_target_verified','callee_context_verified','project_target_binding_verified','indexed_pointer_consumer_verified','contract_admission_granted','full_source_relation_complete'):
            assert bound[key] is False


def _record(manual,kind='pointer'):
    cell='Valid pointers from the range 0 to 62' if kind=='pointer' else 'D'
    return dict(manual_id=manual,opcode='CALL' if kind=='pointer' else 'MOV',text='',
        parameter_pages=[],parameter_conventions={'source_destination_symbols':True,'d_prefix_is_32_bit':True},
        table_groups=[[['Mnemonic','Function','Operands','Program steps'],['','','D',''],
            [('CALL' if kind=='pointer' else 'MOV')+' FNC 01','Executes the subroutine program starting at the identified pointer position',cell,'CALL, CALLP: 3 step']]])


@pytest.mark.parametrize('manual',['jy992d88101e','jy992d48301j'])
@pytest.mark.parametrize('opcode',['CALL','CALLP'])
def test_legacy_CALL_own_pointer_cell_keeps_D_as_input(manual,opcode):
    record=_record(manual);before=copy.deepcopy(record)
    rows=producer._legacy_native_parameter_rows(record,dict(opcode=opcode,width_bits=16))
    assert rows['order']==['D']
    assert rows['rows'][0]['type']=='POINTER'
    assert rows['rows'][0]['direction']=='input'
    assert rows['rows'][0]['direction_basis']=='own_explicit_valid_pointer_operand_description'
    assert record==before


def test_legacy_ordinary_D_keeps_same_manual_destination_convention():
    record=_record('jy992d88101e','word')
    row=producer._legacy_native_parameter_rows(record,dict(opcode='MOV',width_bits=16))['rows'][0]
    assert row['type']=='ANY16' and row['direction']=='output'
    assert row['direction_basis']=='same_manual_explicit_source_destination_symbol_definition'


def test_fx1nc_E_native_signature_scope_stays_candidate_without_touching_M_or_other_facts():
    rows=[dict(id='typed',dimension='parameters.typed_operands',value={'representation':'native_ladder_instruction','operands':[{'name':'D','type':'POINTER','direction':'input'}],'unresolved_items':['existing_gap']},status='source_verified',verification='source_checked',sources=[{'manual_id':'jy992d88101e'}]),
        dict(id='M',dimension='parameters.typed_operands',value={'representation':'structured_ladder_block'},status='source_verified',verification='source_checked',sources=[{'manual_id':'fxcpu_basic_applied_m'}]),
        dict(id='E_operation',dimension='execution.operation',value={'operation':'call'},status='source_verified',verification='source_checked',sources=[{'manual_id':'jy992d88101e'}])]
    before=copy.deepcopy(rows)
    producer._qualify_fx1nc_call_e_scope(rows,'CALLP')
    assert rows[0]['value']['operands']==before[0]['value']['operands']
    assert rows[0]['status']=='candidate_evidence' and rows[0]['verification']=='not_performed'
    assert rows[0]['value']['unresolved_items']==['existing_gap','native_form_source_scope_not_confirmed_for_FX1NC']
    assert rows[1]==before[1]
    assert rows[2]['status']=='candidate_evidence' and rows[2]['value']['source_scope_confirmed'] is False


@pytest.mark.parametrize('model,maximum',[('FX3S',255),('FX3G',2047),('FX3UC',4095)])
def test_own_R_pointer_contract_needs_Modify_and_P_cells(monkeypatch,model,maximum):
    text='CALL FEND SRET P0 to P62 P64 to P255 P64 to P2047 P64 to P4095 P63 is dedicated Nesting of up to five layers is allowed'
    words=[dict(text=t,x0=x,x1=x+5,top=y) for t,x,y in [('Modify',411,414),('P',530,414),('\uf050',411,427),('\uf050',530,427)]]
    pages=[dict(page=230,layout=text,words=words)]
    monkeypatch.setattr(producer,'_parameter_source_pages',lambda *_:pages)
    record=dict(id='own',manual_id='fx3_programming_r',manual_number='JY997D16601',revision='R',pdf_pages=[230,231])
    fact=producer._fx_call_documented_pointer_contract(None,record,'CALLP',model)
    assert fact['value']['pointer_number_intervals']==[[0,62],[64,maximum]]
    assert fact['value']['index_modification_allowed'] is True
    assert fact['value']['indexed_pointer_consumer_verified'] is False
    assert fact['value']['contract_admission_granted'] is False
    words.pop()
    assert producer._fx_call_documented_pointer_contract(None,record,'CALLP',model) is None
    assert producer._fx_call_documented_pointer_contract(None,record,'CALLP','FX1NC') is None


def test_frozen_FX_CALL_packet_delivers_own_domains_and_keeps_scope_unknown():
    import json
    from pathlib import Path
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units,technical_knowledge_text
    here=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-call-native-contract-repair')
    entries=json.loads((here/'source-facts.json').read_text(encoding='utf-8'))['entries']
    before={(e['target_model'],e['opcode']):e for e in json.loads((here/'base-source-facts-60.json').read_text(encoding='utf-8'))['entries']}
    def walk(x):
        if isinstance(x,dict):
            yield x
            for v in x.values():yield from walk(v)
        elif isinstance(x,list):
            for v in x:yield from walk(v)
    count=0;nc=0
    for entry in entries:
        model,op=entry['target_model'],entry['opcode'];rows=entry['facts'];active={f['id']:f for f in rows}
        for old in before[model,op]['facts']:
            if any(r['manual_id']=='fxcpu_basic_applied_m' for r in old['sources']):assert active[old['id']]==old
            if old['id']=='authored:applied:native_typed_operands@fx3_programming_r':
                assert active[old['id']]['value']['operands'][0]['name']=='Pn'
                assert active[old['id']]['status']=='source_verified'
        packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=[model]),content_origin='authored_fact',value=entry),plc_model=model,language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
        record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model=model,language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
        nodes=list(walk(technical_context_units(technical_knowledge_text(record))))
        for f in rows:
            if f['dimension']=='parameters.documented_pointer_contract':
                v=f['value'];count+=1
                assert any(n.get('source_symbol')==v['source_symbol'] and n.get('pointer_number_intervals')==v['pointer_number_intervals'] and n.get('indexed_pointer_consumer_verified') is False and n.get('contract_admission_granted') is False for n in nodes)
                assert v['full_source_relation_complete'] is False
                if model.startswith('FX3'):
                    assert v['index_modification_allowed'] is True and v['excluded_pointer_numbers']==[63]
                else:assert 'index_modification_allowed' not in v and v['source_index_permission_established'] is False
            if model=='FX1NC' and f['sources'] and all(r['manual_id']=='jy992d88101e' for r in f['sources']):
                nc+=1;assert f['status']=='candidate_evidence' and f['verification']=='not_performed'
                assert f['value']['source_scope_confirmed'] is False
        if model=='FX1NC':
            assert any(n.get('source_scope_confirmed') is False for n in nodes)
            assert any(n.get('complete_native_signature_verified') is False and n.get('indexed_pointer_consumer_verified') is False for n in nodes)
    assert len(entries)==29 and count==28 and nc==25


def _r230_parameter_record():
    text=('Pointer number (P) indicating the label number for the jump destination '
          'P0 to P62 P64 to P255 P64 to P2047 P64 to P4095 P63 is dedicated')
    def w(t,x,y):return dict(text=t,x0=x,x1=x+10,top=y,bottom=y+8)
    page=dict(page=230,layout=text,words=[w('Pn',120,310),w('Pointer',483,309),
              w('number',509,309),w('CALL',150,690),w('Pn',220,686)])
    return dict(manual_id='fx3_programming_r',revision='R',opcode='CALL',text=text,parameter_pages=[page])


def test_R230_own_pointer_recovery_requires_cell_and_diagram_without_generic_symbol_change():
    r=_r230_parameter_record();before=copy.deepcopy(r)
    rows=producer._native_set_data_rows(r,16)
    assert len(rows)==1 and rows[0]['name']=='Pn' and rows[0]['type']=='POINTER'
    assert rows[0]['direction']=='input' and rows[0]['role']=='branch_target'
    assert producer._slot_symbol('Pn') is None
    assert r==before
    for mutation in ('diagram','type','range','manual'):
        bad=copy.deepcopy(r)
        if mutation=='diagram':bad['parameter_pages'][0]['words'].pop()
        if mutation=='type':bad['parameter_pages'][0]['words'][1]['text']='Word'
        if mutation=='range':bad['parameter_pages'][0]['layout']=bad['parameter_pages'][0]['layout'].replace('P64 to P255','')
        if mutation=='manual':bad['manual_id']='jy992d88101e'
        assert producer._own_r_call_parameter_rows(bad,16)==[]


@pytest.mark.parametrize('model,version',[('FX3S','1.00'),('FX3G','1.00'),('FX3GC','1.40'),('FX3U','2.20'),('FX3UC','1.00')])
def test_R230_exact_badges_bind_only_own_heading(model,version):
    review=producer._r_call_header_review()
    record=dict(manual_id='fx3_programming_r',manual_number='JY997D16601',revision='R',
        section=review['section'],pdf_pages=[230,231,232,233],
        manual_models=['FX3S','FX3G','FX3GC','FX3U','FX3UC'])
    checked=producer._reviewed_header_records([record],[review])[0]
    models,_,conditional,condition=producer._models(checked['applicability'],record['manual_models'])
    assert model in models and model in conditional and condition['minimum_by_model'][model]==version
    bad={**record,'pdf_pages':[229]}
    assert 'applicability' not in producer._reviewed_header_records([bad],[review])[0]
    row=dict(id='own',dimension='parameters.arity',value={'minimum':1,'maximum':1},
        status='source_verified',verification='source_checked',sources=[],scope={})
    bound=producer._for_model([row],model,conditional,condition)[0]
    assert bound['status']=='source_verified' and bound['conditions']==[{'context':'cpu_version','minimum':version}]


@pytest.mark.parametrize('model,minimum,below',[('FX3S','1.00','0.99'),('FX3G','1.00','0.99'),('FX3GC','1.40','1.39'),('FX3U','2.20','2.19'),('FX3UC','1.00','0.99')])
def test_R230_default_projection_keeps_version_and_unfinished_index_boundary(model,minimum,below):
    import json
    from pathlib import Path
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_knowledge_text,technical_context_units
    entries=json.loads(Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-call-native-contract-repair/source-facts.json').read_text())['entries']
    e=next(e for e in entries if e['target_model']==model and e['opcode']=='CALL')
    def walk(x):
        if isinstance(x,dict):
            yield x
            for v in x.values():yield from walk(v)
        elif isinstance(x,list):
            for v in x:yield from walk(v)
    wrong=project_packet(dict(kind='instruction',target='CALL',scope={'models':[model]},content_origin='authored_fact',value=e),plc_model=model,language='ladder',project_mode='structured_ladder_block',instance_context={'cpu_version':minimum},aspects=['programming_semantics'])
    assert not any(f['id']=='authored:applied:native_typed_operands@fx3_programming_r' for f in wrong['value']['facts'])
    for version,applicable in ((minimum,True),(below,False),(None,None)):
        context={} if version is None else {'cpu_version':version}
        p=project_packet(dict(kind='instruction',target='CALL',scope={'models':[model]},content_origin='authored_fact',value=e),plc_model=model,language='ladder',project_mode='native_ladder_instruction',instance_context=context,aspects=['programming_semantics'])
        typed=next((f for f in p['value']['facts'] if f['id']=='authored:applied:native_typed_operands@fx3_programming_r'),None)
        if applicable is False:
            assert typed is None
            assert any(f['dimension']=='constraints.cpu_availability' and f['value'].get('available') is False for f in p['value']['facts'])
        else:
            assert typed['selection_applicability']==('applicable' if applicable else 'unverified')
            assert {'context':'cpu_version','minimum':minimum} in typed['conditions']
            assert {'context':'project_mode','equals':'native_ladder_instruction'} in typed['conditions']
            record=dict(text=json.dumps(p),fact_kind='instruction',fact_target='CALL',_knowledge_context=dict(model=model,language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
            nodes=list(walk(technical_context_units(technical_knowledge_text(record))))
            assert any(n.get('source_symbol')=='Pn' and n.get('indexed_pointer_codec_verified') is False and n.get('effective_indexed_target_verified') is False and n.get('full_source_relation_complete') is False for n in nodes)

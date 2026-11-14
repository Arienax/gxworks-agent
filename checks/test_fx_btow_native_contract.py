"""Own R byte packing is scoped to the two reviewed CPU badges."""
import copy,json
from pathlib import Path
import pytest
from tools import fact_applied_review as producer
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-btow-native-contract-repair')
FORMS=('BTOW','BTOWP')


def _fake(monkeypatch,mutation=None):
    descriptions=['Head device number storing data to be combined in byte units',
                  'Head device number storing data acquired by combination in byte units',
                  'Number of byte data to be combined (0 ≤ n)']
    table={'bbox':[0,0,100,50],'cells':[{'text':text,'bbox':bbox} for text,bbox in [
        ('Operand type',[0,0,20,10]),('Description',[20,0,70,10]),('Data type',[70,0,100,10])]]}
    table['cells'] += [dict(text=text,bbox=[20,11+10*i,70,20+10*i]) for i,text in enumerate(descriptions)]
    words=[dict(text=t,x0=2,x1=5,top=12+10*i,bottom=17+10*i) for i,t in enumerate(['S','D','n'])]
    words += [dict(text=t,x0=x,x1=x+7,top=25,bottom=29) for t,x in [('16-bit',72),('binary',84)]]
    text='combines the low-order 8 bits 16-bit binary high-order byte is ignored odd number 00H '
    text+='the BTOW instruction is not executed M8067 D8067 K6706 equivalent to devices storing the combined data erased and overwritten rounded up value'
    pages=[dict(page=538,layout=text,parameter_tables=[table],words=words),dict(page=539,layout='',parameter_tables=[],words=[])]
    record=dict(id='R538',manual_id='fx3_programming_r',manual_number='JY997D16601',revision='R',opcode='BTOW',pdf_pages=[538,539])
    typed=dict(id='authored:applied:native_typed_operands@fx3_programming_r',dimension='parameters.typed_operands',value={
        'representation':'native_ladder_instruction','operand_count':3,'operands':[
         dict(name='S',type='unknown',direction='input'),dict(name='D',type='ANY16',direction='output'),dict(name='n',type='unknown',direction='input')]},
        status='declared_unverified',verification='source_checked',conditions=[producer.HEADER_CONDITION],
        scope=dict(models=['FX3U'],forms=['BTOW'],languages=['ladder']),sources=[dict(manual_id='fx3_programming_r',revision='R',pdf_pages=[538,539])])
    if mutation=='missing_description':table['cells'].pop()
    elif mutation=='unshared_type':words[-1]['text']='integer'
    elif mutation=='wrong_symbol':words[0]['text']='S1'
    elif mutation=='foreign_manual':record['manual_id']='jy992d48301j'
    elif mutation=='wrong_type':typed['value']['operands'][0]['type']='BOOL'
    monkeypatch.setattr(producer,'_parameter_source_pages',lambda source,r:pages)
    rows={('FX3U','BTOW'):[typed],('FX1N','BTOW'):[copy.deepcopy(typed)]}
    return record,rows


def test_fx_btow_shared_bin16_cell_binds_all_three_operands_without_touching_legacy(monkeypatch):
    from plc.instruction_definition import Expression,evaluate_expression
    record,rows=_fake(monkeypatch);legacy=copy.deepcopy(rows['FX1N','BTOW'])
    producer._repair_fx_btow_source(None,[record],rows)
    typed=rows['FX3U','BTOW'][0];s,d,n=typed['value']['operands']
    assert typed['status']=='source_verified' and all(p['type']=='ANY16' for p in (s,d,n))
    assert rows['FX1N','BTOW']==legacy
    for count,expected in [(0,0),(1,1),(2,1),(5,3)]:
        assert evaluate_expression(Expression.from_mapping(d['word_device_layout']['word_count']),{'n':count})==expected
    assert s['range']['device_prefixes']==d['range']['device_prefixes']==['T','C','D','R']
    assert n['range']['device_prefixes']==['D','R'] and n['range']['index_modification'] is False
    assert all(p['range']['index_modification'] is True for p in (s,d))
    assert {'context':'cpu_version','minimum':'2.20'} in typed['conditions']


@pytest.mark.parametrize('mutation',['missing_description','unshared_type','wrong_symbol','foreign_manual','wrong_type'])
def test_fx_btow_merged_type_needs_its_exact_own_witness(monkeypatch,mutation):
    record,rows=_fake(monkeypatch,mutation);old=copy.deepcopy(rows)
    producer._repair_fx_btow_source(None,[record],rows)
    assert rows==old


def test_fx_btow_frozen_own_badges_keep_both_positive_and_below_version_guards():
    from plc.instruction_constraints import check_fact_constraints
    entries=json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    assert len(entries)==4
    for e in entries:
        model,op=e['target_model'],e['opcode'];rows=e['facts']
        positive=next(f for f in rows if f['id']=='authored:applied:cpu_availability:header@fx3_programming_r')
        negative=next(f for f in rows if f['id']=='authored:applied:cpu_availability:below_header_version@fx3_programming_r')
        observation=next(f for f in rows if f['id']=='authored:applied:source_cpu_header@fx3_programming_r')
        assert observation['value']['minimum_version']=='2.20'
        assert observation['conditions']==[{'context':'project_mode','equals':'native_ladder_instruction'}]
        for version,ps,ns in [('2.19','not_applicable','failed'),('2.20','passed','not_applicable'),(None,'unverified','unverified')]:
            for fact,expected in [(positive,ps),(negative,ns)]:
                report=check_fact_constraints(rows,{},target=op,plc_model=model,language='ladder',
                    context={'project_mode':'native_ladder_instruction','cpu_version':version},root_ids=[fact['id']])
                assert report['status']==('passed' if expected=='not_applicable' else expected),(model,op,version,report)
                assert next(c for c in report['checks'] if c['fact_id']==fact['id'])['status']==expected


def _validate_new_call(op,args,model,version='2.20'):
    from plc.ir import build_plc_ir,validate_plc_ir
    payload={'device_comments':{},'rungs':[{'rung_id':0,'header_element':None,'branches':[{
        'branch_id':1,'y_offset_level':0,'inputs':[{'type':'NO','address':'X0'}],
        'outputs':[{'type':'APP_INSTR','opcode':op,'operands':args}]}]}]}
    confirmed={'cpu_version':version} if version else None
    return validate_plc_ir(build_plc_ir(payload,plc_model=model,confirmed_spec=confirmed),confirmed_spec=confirmed)


@pytest.mark.usefixtures('source_view')
@pytest.mark.parametrize('model',['FX3U','FX3UC'])
def test_fx_btow_actual_public_calls_use_own_counts_devices_and_firmware(model):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.device_profiles import DEVICE_LIMITS
    from plc.device_identity import instruction_operand_regions
    for op in FORMS:
        spec=registry.resolve(op,cpu=model,operand_count=3)
        assert spec.native_operand_order==('S','D','n') and spec.definition_facts
        for args in (['D20','D10','K5'],['D20','D10','K0'],['D20','D10','D0'],
                     ['D20Z0','D10Z1','K2'],['D11','D10','K6']):
            _validate_new_call(op,args,model)
        for args in (['K0','D10','K2'],['K4M0','D10','K2'],['Z0','D10','K2'],
                     ['D20',r'U0\G0','K2'],['D20','D10','T0'],['D20','D10','D0Z0'],
                     ['D20','D10','K-1'],['D20','D10','E1.0']):
            with pytest.raises(ValueError):_validate_new_call(op,args,model)
        with pytest.raises(ValueError):_validate_new_call(op,['D20','D10','K2'],model,'2.19')
        regions=instruction_operand_regions(spec,op,['D20','D10','K5'],model)
        blocks={r['operand_index']:r for r in regions}
        assert blocks[0]['addresses']==['D'+str(i) for i in range(20,25)]
        assert blocks[1]['addresses']==['D10','D11','D12']
        zero=instruction_operand_regions(spec,op,['D20','D10','K0'],model)
        assert all(r['addresses']==[] and r['resolved_count']==0 for r in zero)
        dynamic=instruction_operand_regions(spec,op,['D20','D10','D0'],model)
        assert all('addresses' not in r and 'resolved_count' not in r for r in dynamic if r['operand_index'] in (0,1))
        last=DEVICE_LIMITS[model]['D']
        _validate_new_call(op,['D'+str(last),'D0','K1'],model)
        with pytest.raises(ValueError):_validate_new_call(op,['D'+str(last),'D0','K2'],model)
        if model != 'FX3UC':
            _validate_new_call(op,['D0','D'+str(last),'K2'],model)
        else:
            with pytest.raises(ValueError,match='special device'):
                _validate_new_call(op,['D0','D'+str(last),'K2'],model)
        extent2={r['operand_index']:r for r in instruction_operand_regions(spec,op,['D0','D'+str(last),'K2'],model)}
        extent3={r['operand_index']:r for r in instruction_operand_regions(spec,op,['D0','D'+str(last),'K3'],model)}
        assert extent2[1]['addresses']==['D'+str(last)]
        assert 'storage_region_exceeds_device_range' in extent3[1]['requires']
        with pytest.raises(ValueError):_validate_new_call(op,['D0','D'+str(last),'K3'],model)


def test_fx_btow_frozen_packet_keeps_M_and_partial_overlap_knowledge():
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units,technical_knowledge_text
    entries=json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    before={(e['target_model'],e['opcode']):e for e in json.loads((HERE/'base-source-facts.json').read_text(encoding='utf-8'))['entries']}
    def walk(x):
        if isinstance(x,dict):
            yield x
            for v in x.values():yield from walk(v)
        elif isinstance(x,list):
            for v in x:yield from walk(v)
    for e in entries:
        model,op=e['target_model'],e['opcode'];actual={f['id']:f for f in e['facts']}
        for old in before[model,op]['facts']:
            if any(r['manual_id']=='fxcpu_basic_applied_m' for r in old['sources']):assert actual[old['id']]==old
        packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=[model]),content_origin='authored_fact',value=e),
            plc_model=model,language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
        record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,
            _knowledge_context=dict(model=model,language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
        nodes=list(walk(technical_context_units(technical_knowledge_text(record))))
        assert any(n.get('minimum')=='2.20' for n in nodes)
        assert any(n.get('aliasing',{}).get('arbitrary_overlap_source_snapshot_semantics_established') is False
                   and n.get('full_source_relation_complete') is False for n in nodes)
        assert any(n.get('when_n_zero')=={'processing':False,'destination_writes':False} for n in nodes)


@pytest.fixture
def source_view(monkeypatch):
    """Use complete own reviewed input while preserving the public call chain."""
    import copy
    import plc.instructions as instructions
    import plc.validation as validation
    import plc.ir as ir
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    entries = json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    registry = instructions.DEFAULT_INSTRUCTION_REGISTRY
    local = copy.copy(registry)
    for name in ('_cpu_contracts','_definition_contract_bases','_definition_owners'):
        setattr(local,name,dict(getattr(registry,name)))
    local._loaded_definition_contracts = set(registry._loaded_definition_contracts)
    local._clear_definition_contracts()
    for source in entries:
        owner = copy.deepcopy(registry.definition_entry(source['opcode'],cpu=source['target_model']))
        owner['facts'] = [f for f in owner['facts'] if not f['id'].startswith('authored:applied:')] + source['facts']
        assert refresh_reviewed_call_admission(owner,local)
        local._definition_owners[('mitsubishi',source['opcode'],source['target_model'])] = owner
    monkeypatch.setattr(instructions,'DEFAULT_INSTRUCTION_REGISTRY',local)
    monkeypatch.setattr(validation,'DEFAULT_INSTRUCTION_REGISTRY',local)
    monkeypatch.setattr(ir,'DEFAULT_INSTRUCTION_REGISTRY',local)
    return local


def test_fx_btow_family_composition_keeps_own_dynamic_layout_and_overlap_limit():
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.instruction_knowledge import compose_programming_facts
    entries=json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    for e in entries:
        facts,_=compose_programming_facts(registry,e['opcode'],cpu=e['target_model'],existing=copy.deepcopy(e['facts']))
        active={f['id']:f for f in facts}
        source=next(f for f in e['facts'] if f['id']=='authored:applied:native_typed_operands@fx3_programming_r')
        assert active[source['id']]['value']==source['value']
        assert active[source['id']]['conditions']==source['conditions']
        effect=active[f"family:BTOW:bytepack20261029:{e['target_model']}:{e['opcode']}:authored:applied:native_typed_operands@fx3_programming_r:effect"]
        assert effect['value']['aliasing']['arbitrary_overlap_source_snapshot_semantics_established'] is False
        assert effect['value']['full_source_relation_complete'] is False
        for old in e['facts']:
            if any(ref['manual_id']=='fxcpu_basic_applied_m' for ref in old['sources']):
                assert old['id'] in active
                for key in ('value','status','verification','scope','conditions'):
                    assert active[old['id']].get(key)==old.get(key)

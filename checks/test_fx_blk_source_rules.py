"""Legacy BLK source qualification never admits a native call."""
import copy
import json
from pathlib import Path
import pytest
from tools import fact_applied_review as producer

HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-blk-native-contract-repair')


def _input(monkeypatch, changes=None):
    pages={241:'F2-30GM FX2-24EI uses 8 consecutive devices uses 8 consecutive devices '
        '0 to 31 (in decimal) cannot be in a BCD format determined by the position',
        335:'9.2.4 F2-30GM FX2-24EI Not applicable to FX2C FX units with CPU’s greater than version 3.06 FNC 97, BLK',
        21:'CPU Ver 3.30 phased out (removed) BLK (FNC97)'}
    pages.update(changes or {})
    monkeypatch.setattr(producer,'_parameter_source_pages',lambda source,record:[
        {'layout':pages[record['pdf_pages'][0]]}])
    record=dict(id='own',manual_id='jy992d48301j',manual_number='JY992D48301',revision='J',opcode='BLK',pdf_pages=[241])
    scope=dict(models=['FX1'],forms=['BLK'],languages=['ladder'])
    def fact(identity,dimension,value):
        return dict(id=identity+'@jy992d48301j',dimension=dimension,value=value,
                    status='source_verified',verification='source_checked',scope=copy.deepcopy(scope),
                    sources=[dict(manual_id='jy992d48301j',revision='J',pdf_pages=[241])])
    rows=[fact('authored:applied:native_typed_operands','parameters.typed_operands',{
        'representation':'native_ladder_instruction','operands':[
         dict(name='S1',type='ANY16',direction='input'),dict(name='S2',type='BOOL',direction='input'),
         dict(name='D',type='BOOL',direction='output')]}),
        fact('authored:applied:source_operand_devices','parameters.device_form',{'operands':[
            dict(name='S1',devices=['K','H','D']),dict(name='S2',devices=['X']),dict(name='D',devices=['Y'])]})]
    return record,rows


def test_blk_own_witnesses_bind_exact_eight_point_heads_and_never_bmov(monkeypatch):
    record,rows=_input(monkeypatch);facts={('FX1','BLK'):rows}
    producer._repair_legacy_blk_source(None,[record],facts)
    rows=facts['FX1','BLK']
    s1,s2,d=rows[0]['value']['operands']
    assert s1['encoding']=='binary' and s1['range']['minimum']==0 and s1['range']['maximum']==31
    assert [(p['occupied_bit_count'],p['range']['device_prefixes']) for p in (s2,d)]==[(8,['X']),(8,['Y'])]
    assert all(p['passing']=='device_reference' for p in (s2,d))
    assert not any(f['value'].get('available') is True for f in rows)
    assert all(f['scope']['models']==['FX1'] for f in rows)


@pytest.mark.parametrize('failure',['one_head_only','wrong_range','wrong_devices','wrong_manual'])
def test_blk_does_not_invent_span_without_both_own_cells_and_bound_devices(monkeypatch,failure):
    changes={241:'F2-30GM FX2-24EI uses8consecutivedevices 0to31(indecimal) cannotbeinaBCDformat determinedbytheposition'} if failure=='one_head_only' else {}
    record,rows=_input(monkeypatch,changes)
    if failure=='wrong_range':
        record,rows=_input(monkeypatch,{241:'F2-30GM FX2-24EI uses8consecutivedevices uses8consecutivedevices 0to63(indecimal) cannotbeinaBCDformat determinedbytheposition'})
    elif failure=='wrong_devices': rows[1]['value']['operands'][1]['devices']=['M']
    elif failure=='wrong_manual':record['manual_id']='jy992d88101e'
    facts={('FX1','BLK'):rows}
    producer._repair_legacy_blk_source(None,[record],facts)
    rows=facts['FX1','BLK']
    assert all('occupied_bit_count' not in p for p in rows[0]['value']['operands'])


def test_blk_exact_version_exclusion_does_not_turn_three_point_zero_six_into_three_point_zero_seven(monkeypatch):
    from plc.instruction_constraints import check_fact_constraints
    record,rows=_input(monkeypatch)
    facts={('FX1','BLK'):rows}
    producer._repair_legacy_blk_source(None,[record],facts)
    rows=facts['FX1','BLK']
    negative=next(f for f in rows if ':blk_native_connection_version_excluded@' in f['id'])
    assert {'context':'cpu_version','minimum_exclusive':'3.06'} in negative['conditions']
    for version,expected in [('3.06','not_applicable'),('3.06.1','failed'),('3.07','failed'),('3.30','failed'),(None,'unverified')]:
        report=check_fact_constraints(rows,{},target='BLK',plc_model='FX1',language='ladder',version=version,
            context={'project_mode':'native_ladder_instruction','cpu_version':version},root_ids=[negative['id']])
        assert report['checks'][0]['status']==expected,(version,report)
    removed=next(f for f in rows if ':blk_version_history_removed@' in f['id'])
    assert {'context':'cpu_version','minimum':'3.30'} in removed['conditions']
    assert negative['value']['documented_scope']!=removed['value']['documented_scope']


def test_blk_frozen_source_preserves_four_owners_unadmitted_and_exact_bit_spans():
    from dataclasses import replace
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry,InstructionSpec
    from plc.device_identity import instruction_operand_regions
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    entries=json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    assert {(e['target_model'],e['opcode']) for e in entries}=={(m,o) for m in ('FX1','FXU') for o in ('BLK','BLKP')}
    for e in entries:
        model,op=e['target_model'],e['opcode']
        assert registry.resolve_catalog_form(op,cpu=model) is None
        entry=copy.deepcopy(registry.definition_entry(op,cpu=model))
        entry['facts']=e['facts']
        assert entry['call_available'] is False and not refresh_reviewed_call_admission(entry,registry)
        assert any(f['dimension']=='constraints.source_cpu_header' and f['value']['model']==model and f['value']['state']=='supported' for f in e['facts'])
        typed=next(f for f in e['facts'] if f['id']=='authored:applied:native_typed_operands@jy992d48301j')
        assert typed['status']=='source_verified' and typed['verification']=='source_checked'
        ps=typed['value']['operands']
        # This source-only view is not registered and cannot grant a new call.
        spec=InstructionSpec.from_mapping(dict(mnemonic=op,arity=dict(min=3,max=3),operands=[
            dict(name='S1',role='read',data_type='word'),dict(name='S2',role='read',data_type='bit'),
            dict(name='D',role='write',data_type='bit')]))
        spec=replace(spec,native_operand_order=('S1','S2','D'),verified_fields=('operand_order','operand_types'))
        regions=instruction_operand_regions(spec,op,['K0','X40','Y30'],model,parameters=[[p] for p in ps])
        assert [(r['operand_index'],r['resolved_count'],r['addresses']) for r in regions]==[
            (1,8,['X4'+str(i) for i in range(8)]),(2,8,['Y3'+str(i) for i in range(8)])]


def test_blk_frozen_ranges_header_observations_and_default_projection_are_separate():
    from plc.instruction_constraints import check_fact_constraints
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units,technical_knowledge_text
    entries=json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
    def walk(x):
        if isinstance(x,dict):
            yield x
            for v in x.values():yield from walk(v)
        elif isinstance(x,list):
            for v in x:yield from walk(v)
    for e in entries:
        model,op=e['target_model'],e['opcode'];rows=e['facts']
        head=next(f for f in rows if f['dimension']=='constraints.source_cpu_header')
        assert head['value']==dict(model=model,state='supported',basis='own_instruction_CPU_header')
        form=next(f for f in rows if f['dimension']=='constraints.source_cpu_form')
        assert form['value']['width_cells']=={'16':True,'32':False} and form['value']['pulse_cell'] is True
        availability=next(f for f in rows if f['id']=='authored:applied:cpu_availability@jy992d48301j')
        assert availability['status']=='unknown' and 'available' not in availability['value']
        bound=next(f for f in rows if f['dimension']=='constraints.numeric_ranges')
        for number,expected in [(0,'passed'),(31,'passed'),(-1,'failed'),(32,'failed')]:
            report=check_fact_constraints(rows,{'$0':number},target=op,plc_model=model,language='ladder',
                context={'project_mode':'native_ladder_instruction'},root_ids=[bound['id']])
            assert report['status']==expected,(model,op,number,report)
        base=json.loads((HERE/'base-source-facts.json').read_text(encoding='utf-8'))['entries']
        before=next(b for b in base if (b['target_model'],b['opcode'])==(model,op))
        actual={f['id']:f for f in rows}
        assert all(f['id'] in actual for f in before['facts'])
        for old in before['facts']:
            if old['id'] not in {'authored:applied:native_typed_operands@jy992d48301j',
                                  'authored:applied:operation:297@jy992d48301j'}:
                assert actual[old['id']]==old
        packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=[model]),
            content_origin='authored_fact',value=e),plc_model=model,language='ladder',
            project_mode='native_ladder_instruction',aspects=['programming_semantics'])
        record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,
            _knowledge_context=dict(model=model,language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
        delivered=technical_context_units(technical_knowledge_text(record))
        nodes=list(walk(delivered))
        assert any(n.get('required_modules')==['FX2-24EI','F2-30GM'] for n in nodes)
        assert any(n.get('minimum_exclusive')=='3.06' for n in nodes)
        assert any(n.get('minimum')=='3.30' for n in nodes)
        assert any(n.get('head_assignment')=='FX2_24EI_position_in_FX_expansion_chain' for n in nodes)
        assert any(n.get('occupied_bit_count')==8 and n.get('range',{}).get('device_prefixes')==['X'] for n in nodes)
        assert any(n.get('occupied_bit_count')==8 and n.get('range',{}).get('device_prefixes')==['Y'] for n in nodes)

"""Own Q/L packing and exchange device spans; offline public new-call checks."""
from pathlib import Path
import pytest
from checks import test_bklogic_native_contract as overlay

OPS=('BTOW','BTOWP','BXCH','BXCHP')
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-btow-bxch-native-contract-repair')

@pytest.fixture
def source_registry(monkeypatch):
    monkeypatch.setattr(overlay,'SOURCE_DIR',HERE)
    return overlay.source_delta_registry.__wrapped__(monkeypatch)

def validate(op,args,model='Q03UDV'):
    from plc.ir import build_plc_ir,validate_plc_ir
    validate_plc_ir(build_plc_ir(overlay.app_program(op,args),plc_model=model))

@pytest.mark.parametrize('op',OPS)
def test_pack_exchange_actual_new_calls_and_own_head_cells(source_registry,op):
    spec=source_registry.resolve(op,cpu='Q03UDV',operand_count=3)
    assert spec.native_operand_order==(('S','D','n') if op.startswith('BTOW') else ('D1','D2','n'))
    assert [p.role.value for p in spec.operands]==(['read','write','read'] if op.startswith('BTOW') else ['read_write','read_write','read'])
    assert spec.execution_form==('pulse' if op.endswith('P') else 'continuous') and spec.st_call is None
    for args in [('D0','D20','K3'),('R0','ZR20','H2'),('D0','D20','D30'),('D0','D20','J1\\W0'),('D0','D20','U0\\G0')]:
        validate(op,args)
    for args in [('K0','D20','K1'),('D0','K0','K1'),('Z0','D20','K1'),('D0','J1\\W0','K1'),('U0\\G0','D20','K1'),('K4X0','D20','K1'),('D0','D20','E1.0')]:
        with pytest.raises(ValueError):validate(op,args)

@pytest.mark.parametrize('op',OPS)
@pytest.mark.parametrize('model',['Q03UDV','L02'])
def test_pack_exchange_actual_dynamic_span_and_alias_bounds(source_registry,op,model):
    from plc.device_profiles import DEVICE_LIMITS
    from plc.device_identity import instruction_operand_regions
    last=DEVICE_LIMITS[model]['D'];spec=source_registry.resolve(op,cpu=model,operand_count=3)
    validate(op,[f'D{last-2}','D0','K3'],model)
    with pytest.raises(ValueError,match='extent exceeds'):validate(op,[f'D{last-1}','D0','K3'],model)
    destination_start=last-1 if op.startswith('BTOW') else last-2
    validate(op,['D0',f'D{destination_start}','K3'],model)
    with pytest.raises(ValueError,match='extent exceeds'):validate(op,['D0',f'D{destination_start+1}','K3'],model)
    regions=instruction_operand_regions(spec,op,['D0','D20','D30'],model)
    assert all('parameter_value:n' in r['requires'] for r in regions)
    if op.startswith('BTOW'):
        validate(op,['D0','D0','K3'],model)
        validate(op,['D0','D1','K3'],model)
        for count,expected in [(1,1),(2,1),(5,3)]:
            regions=instruction_operand_regions(spec,op,['D0','D20',f'K{count}'],model)
            assert [len(r['addresses']) for r in regions]==[count,expected]
    else:
        for head in ['D0','D1','D2']:
            with pytest.raises(ValueError,match='block_exchange_regions_disjoint'):validate(op,['D0',head,'K3'],model)
        validate(op,['D0','D3','K3'],model)

@pytest.mark.parametrize('op',OPS)
def test_pack_exchange_documented_effect_is_own_and_not_core_execution(source_registry,op):
    entry=source_registry.definition_entry(op,cpu='Q03UDV')
    key='native_byte_pack_operation' if op.startswith('BTOW') else 'native_block_exchange_operation'
    fact=next(f for f in entry['facts'] if f['id'].startswith('authored:ql:'+key+':'))
    assert fact['status']=='source_verified' and fact['conditions']==[{'context':'project_mode','equals':'native_ladder_instruction'}]
    assert all(s['manual_id']=='sh080809engx' for s in fact['sources'])
    value=fact['value'];assert value['executor_capability']=='not_established' and value['negative_count_behavior_established'] is False
    if op.startswith('BTOW'):
        assert value['source_word_count']=='n' and value['destination_word_count']=='ceil_n_divided_by_2'
        assert value['source_upper_bytes']=='ignored' and value['odd_final_destination_upper_byte']==0
        assert value['when_n_zero']=={'processing':False,'destination_writes':False}
        assert value['aliasing']['source_and_destination_ranges_may_overlap'] is True
    else:
        assert value['both_heads_access']=='read_write' and value['same_head_allowed'] is False
        assert value['zero_count_behavior_established'] is False

    import copy,json
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_knowledge_text,technical_context_units
    packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['Q03UDV']),content_origin='authored_fact',value=copy.deepcopy(entry)),plc_model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
    record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
    visible=json.dumps(technical_context_units(technical_knowledge_text(record)))
    if op.startswith('BTOW'):
        for field in ['ceil_n_divided_by_2','odd_final_destination_upper_byte','source_upper_bytes','destination_writes','source_and_destination_ranges_may_overlap']:assert field in visible,field
    else:
        for field in ['same_head_allowed','source_and_destination_ranges_must_be_disjoint','zero_count_behavior_established']:assert field in visible,field

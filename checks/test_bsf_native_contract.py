"""Own BSF bit-count layout and unresolved cross-word boundary, offline only."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from checks import test_bklogic_native_contract as source_overlay
OPS=('BSFL','BSFLP','BSFR','BSFRP')
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-bsf-native-contract-repair')

@pytest.fixture
def source_registry(monkeypatch):
    monkeypatch.setattr(source_overlay,'SOURCE_DIR',HERE)
    return source_overlay.source_delta_registry.__wrapped__(monkeypatch)

def validate(op,args,model='Q03UDV'):
    from plc.ir import build_plc_ir,validate_plc_ir
    payload=dict(device_comments={},rungs=[dict(rung_id=1,header_element=None,branches=[dict(branch_id=1,y_offset_level=0,inputs=[dict(type='NO',address='X0')],outputs=[dict(type='APP_INSTR',opcode=op,operands=args)])])])
    validate_plc_ir(build_plc_ir(payload,plc_model=model))

@pytest.mark.parametrize('op',OPS)
def test_bsf_actual_new_call_and_own_device_cells(source_registry,op):
    spec=source_registry.resolve(op,cpu='Q03UDV',operand_count=2)
    assert spec.native_operand_order==('D','n')
    assert [p.role.value for p in spec.operands]==['read_write','read']
    assert spec.execution_form==('pulse' if op.endswith('P') else 'continuous') and spec.st_call is None
    for args in [['M0','K8'],['D10.2','K4'],['M0','D0'],['M0','J1\\W0'],['M0','U0\\G0'],['M0','K4X0']]:validate(op,args)
    for args in [['K0','K8'],['K4M0','K8'],['Z0','K8'],['R0.2','K8'],['J1\\W0.2','K8'],['U0\\G0.2','K8'],['T0','K8'],['D10.2','E1.0']]:
        with pytest.raises(ValueError):validate(op,args)

@pytest.mark.parametrize('op',OPS)
@pytest.mark.parametrize('model',['Q03UDV','L02'])
def test_bsf_bit_extent_same_word_and_unproved_cross_word(source_registry,op,model):
    from plc.device_profiles import DEVICE_LIMITS
    from plc.device_identity import instruction_operand_regions
    last=DEVICE_LIMITS[model]['M']
    validate(op,['M'+str(last-1),'K2'],model)
    with pytest.raises(ValueError,match='extent exceeds'):validate(op,['M'+str(last),'K2'],model)
    spec=source_registry.resolve(op,cpu=model,operand_count=2)
    region,=instruction_operand_regions(spec,op,['D10.2','K4'],model)
    assert region['addresses']==['D10.2','D10.3','D10.4','D10.5']
    region,=instruction_operand_regions(spec,op,['D10.F','K2'],model)
    assert 'word_bit_cross_word_layout_not_established' in region['requires'] and 'addresses' not in region
    region,=instruction_operand_regions(spec,op,['D10','K8'],model)
    assert 'plain_word_bit_count_semantics_not_established' in region['requires'] and 'addresses' not in region
    region,=instruction_operand_regions(spec,op,['M0','D0'],model)
    assert 'parameter_value:n' in region['requires'] and 'addresses' not in region

@pytest.mark.parametrize('op',OPS)
def test_bsf_layout_requires_own_direction_and_fill_clauses(op):
    from tools.fact_ql_behavior_review import _native_bsf_witnesses
    right=op.startswith('BSFR')
    clauses=['Shifts the data in n points from the device designated by (D) to the '+('right' if right else 'left')+' by one bit.',
        'The device designated by (D)'+('+(n-1)' if right else '')+' is filled with 0.']
    section=SimpleNamespace(manual='sh080809engx',bind=lambda w:[('own',398)] if w in clauses else [])
    assert _native_bsf_witnesses(section,op)==clauses
    assert _native_bsf_witnesses(section,'BKRST')==[]
    section.bind=lambda w:[('own',398)] if w==clauses[0] else []
    assert _native_bsf_witnesses(section,op)==[]
    section.manual='sh080783engp';section.bind=lambda w:[('other',360)]
    assert _native_bsf_witnesses(section,op)==[]


@pytest.mark.parametrize('op',OPS)
def test_bsf_own_active_effect_is_one_position_with_carry_fill_and_unknown_zero(source_registry,op):
    import copy,json
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units,technical_knowledge_text
    entry=source_registry.definition_entry(op,cpu='Q03UDV')
    effects=[f for f in entry['facts'] if f['dimension']=='effects.shift']
    assert effects and not any(f['value'].get('repetition_operand')=='n' for f in effects)
    own=next(f for f in effects if f['id'].startswith('authored:ql:span_shift_relation:')
        and any(s['manual_id']=='sh080809engx' for s in f['sources']))
    value=own['value'];right=op.startswith('BSFR')
    assert value['shift_units']==1 and value['range_count_operand']=='n' and value['element_bits']==1
    assert value['carry_device']=='SM700' and value['carry_source_position']==('first_element' if right else 'last_element')
    assert value['fill_value']==0 and value['fill_position']==('last_element' if right else 'first_element')
    assert any(f['id'].startswith('authored:ql:span_shift_zero:') and 'device_span_shift_with_zero_count' in f['value'].get('unresolved_items',[]) for f in entry['facts'])
    packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['Q03UDV']),content_origin='authored_fact',value=copy.deepcopy(entry)),plc_model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
    record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
    output=technical_context_units(technical_knowledge_text(record));visible=json.dumps(output)
    for field in ['SM700','shift_units','fill_value','carry_source_position','zero_count_behavior_established','cross_word_sequence_established']:
        assert field in visible,field
    assert '"zero_count_behavior_established": false' in visible


@pytest.mark.parametrize('op',OPS)
def test_bsf_family_snapshot_cannot_restore_old_native_typed_after_source_replace(op):
    import copy,json
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.instruction_knowledge import compose_programming_facts
    from scripts.compile_instruction_semantics import replace_source_namespaces
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    source=next(e for e in json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']
        if e['target_model']=='L02' and e['opcode']==op)
    identity='authored:ql:native_arguments_d_n:parameters.typed_operands'
    expected=next(f for f in source['facts'] if f['id']==identity)
    original=copy.deepcopy(registry.definition_entry(op,cpu='L02'))
    replaced,_=replace_source_namespaces(original['facts'],source['facts'],('authored:ql:','authored:applied:'))
    assert len({f['id'] for f in source['facts']})==len(source['facts'])
    assert next(f for f in replaced if f['id']==identity)['value']==expected['value']
    composed,_=compose_programming_facts(registry,op,cpu='L02',existing=replaced)
    typed=next(f for f in composed if f['id']==identity)
    assert typed['value']==expected['value'] and typed['scope']==expected['scope']
    assert typed['conditions']==[{'context':'project_mode','equals':'native_ladder_instruction'}]
    assert typed['depends_on']==expected['depends_on']
    entry={**original,'facts':composed}
    assert refresh_reviewed_call_admission(entry,registry)
    assert entry['call_available']

"""CRC own source establishes head signature and partial, mode-bound storage."""
import copy,json,sqlite3
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-crc-native-contract-repair')
def entries():return json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']

@pytest.mark.parametrize('op',['CRC','CRCP'])
@pytest.mark.parametrize('model,serial',[('Q00U',False),('Q03UD',False),('Q03UDV',True),('Q04UDPV',True),('L02',True)])
def test_crc_own_cpu_guard_signature_modes_and_algorithm_limits(model,serial,op):
    rows=entries();assert len(rows)==78 and {e['opcode'] for e in rows}=={'CRC','CRCP'}
    e=next(e for e in rows if e['target_model']==model and e['opcode']==op);facts={f['id']:f for f in e['facts']}
    cpu=facts['authored:ql:cpu_applicability:constraints.cpu_availability'];assert cpu['status']=='source_verified' and cpu['value']['available'] is True
    if serial:
        assert cpu['conditions'][0]['minimum']=='16112' and cpu['conditions'][0]['digits']==5
        assert facts['authored:ql:cpu_requirement_not_met:constraints.cpu_availability']['value']['available'] is False
    else:assert not cpu.get('conditions')
    typed=facts['authored:ql:native_arguments_s_d_n:parameters.typed_operands'];assert typed['status']=='source_verified'
    assert typed['value']['native_form']==dict(operand_order=['S','D','n'],minimum_operands=3,maximum_operands=3)
    s,d,n=typed['value']['operands'];assert [p['type'] for p in [s,d,n]]==['ANY16']*3
    assert [p['direction'] for p in [s,d,n]]==['input','output','input']
    assert s['range']['literal_prefixes']==d['range']['literal_prefixes']==[]
    assert s['range']['address_forms']==d['range']['address_forms']==[]
    assert s['range']['digit_groups_allowed']==d['range']['digit_groups_allowed']==[4]
    assert n['range']['literal_prefixes']==['K','H'] and n['range']['minimum']==1 and n['range']['maximum']==256
    assert s['word_device_layout']['head_storage_only'] is True and d['word_device_layout']['head_storage_only'] is True
    a=s['conditional_read_layout'];b=d['conditional_write_layout']
    assert a['selector']==b['selector']=='SM772'
    assert a['OFF']['published_example']==dict(n=6,source_word_count=3,CRC_value=42363)
    assert a['OFF']['general_exact_span_verified'] is False and a['OFF']['odd_count_byte_selection_verified'] is False
    assert a['ON']['word_count']['name']=='n' and a['ON']['word_stride']==1 and a['ON']['source_upper_byte']=='ignored'
    assert b['OFF']['word_count']==1 and b['ON']['word_count']==2
    assert b['ON']['elements']==[dict(word_offset=0,CRC_bits=[0,7]),dict(word_offset=1,CRC_bits=[8,15])]
    assert b['ON']['general_destination_upper_byte_policy_verified'] is False
    v=facts['authored:ql:crc_documented_processing:effects.documented_processing']['value']
    assert v['generator_polynomial']==dict(exponents=[16,15,2,0]) and v['count_unit']=='bytes'
    for key in ['initial_CRC_verified','input_bit_order_verified','input_byte_order_verified','reflection_input_verified','reflection_output_verified','final_xor_verified','full_algorithm_established','current_SM772_verified','source_destination_alias_policy_verified','disabled_state_verified','core_execution_established','native_execution_verified']:assert v[key] is False,key
    err=facts['authored:ql:crc_documented_error_conditions:effects.documented_processing'];assert err['status']=='source_verified'
    assert err['value']['documented_range_endpoints']==['S+n-1','D+1'] and err['value']['range_check_vs_exact_used_span_not_conflated'] is True
    assert facts['authored:ql:crc_execution_gate:execution.operation_condition']['value']['enabled_when']==('execution_condition_ON' if op=='CRC' else 'execution_condition_rising_edge')
    assert all(set(f.get('depends_on',[]))<=facts.keys() for f in e['facts'])


def test_crc_own_error_table_applicability_matches_selectors_and_x460_marks():
    rows=entries();selectors=json.loads((HERE/'owner-selectors.json').read_text(encoding='utf-8'))
    assert len(rows)==len(selectors)==78
    assert {(e['target_model'],e['opcode']) for e in rows}=={(e['target_model'],e['opcode']) for e in selectors}
    groups=['Q00J/ Q00/ Q01','QnH','QnPH','QnPRH','QnU','LCPU']
    group_ids=['Q00J/Q00/Q01','QnH','QnPH','QnPRH','QnU','LCPU']
    expected_marks={code:dict(zip(group_ids,['U+F0C9']*4+['U+F0C6']*2)) for code in ('4100','4101')}
    expected_applicability={code:dict(zip(group_ids,[False]*4+[True]*2)) for code in ('4100','4101')}
    for e in rows:
        error=next(f for f in e['facts'] if f['id']=='authored:ql:crc_documented_error_conditions:effects.documented_processing')
        assert error['status']=='source_verified'
        assert error['value']['CPU_error_row_applicability']=='QnU_and_LCPU_only'
        assert error['value']['CPU_error_row_applicability_established'] is True
        assert error['value']['error_flag']=='SM0'
        assert error['value']['error_flag_state']=='ON_when_operation_error_occurs'
        assert error['value']['error_code_register']=='SD0'
        assert error['value']['CPU_error_table_groups']==group_ids
        assert error['value']['CPU_error_table_group_marks']==expected_marks
        assert error['value']['CPU_error_table_group_applicability']==expected_applicability
        assert 'pending_symbol_semantics' not in error['value']
        assert any(s['manual_id']=='sh080809engx' and s['revision']=='X' and s['pdf_pages']==[460] and s['target_model']==e['target_model'] for s in error['sources'])
    from types import SimpleNamespace
    from tools.fact_ql_behavior_review import _native_crc_specs
    witnesses=['Generates the CRC value of n points of 8-bit data (byte units)',
        '"X16+X15+X2+1" is used as the generator polynomial','(setting range: 1 to 256)',
        '16-bit conversion mode (when SM772 is OFF)','8-bit conversion mode (when SM772 is ON)',
        'The operation result is stored in the 16 bits of one point of the device specified by (D).',
        'CRC operation is performed for the lower 8 bits (lower byte) of the data in the device specified by (S).',
        'lower 8 bits (byte units) are stored in the device specified by (D)',
        'upper 8 bits (byte units) are stored in the device specified by (D)+1.',
        'A digit other than 4 is specified in the digit-specified bit device in (S), (D).',
        'The value in the device specified by the instruction exceeds the setting range.',
        '(S)+n-1, (D)+1 is out of the device range.',
        'In any of the following cases, an operation error occurs, the error flag (SM0) turns ON, and an error code is stored into SD0.']
    table_witnesses=['%s %s %s'%(code,group,expected_marks[code][group_id])
                     for code in ('4100','4101') for group,group_id in zip(groups,group_ids)]
    bound=set(witnesses+table_witnesses)
    table={'groups':groups,'rows':{code:{'marks':dict(zip(groups,expected_marks[code].values())),
        'applicability':dict(zip(groups,expected_applicability[code].values()))} for code in ('4100','4101')},
        'witnesses':table_witnesses}
    def bind(witness):return [('x460',460)] if witness in bound else []
    section=SimpleNamespace(manual='sh080809engx',title='CRC operation',heading_line='CRC',
                            _native_parameter_contracts=[],_crc_error_table=table,bind=bind)
    for op in ('CRC','CRCP'):
        authored=next(s for s in _native_crc_specs(section,op) if s['key']=='crc_documented_error_conditions')
        assert authored['value']['CPU_error_row_applicability']=='QnU_and_LCPU_only'
        assert authored['value']['CPU_error_row_applicability_established'] is True
        assert authored['value']['error_flag']=='SM0'
        assert authored['value']['error_flag_state']=='ON_when_operation_error_occurs'
        assert authored['value']['error_code_register']=='SD0'
        assert authored['value']['CPU_error_table_groups']==group_ids
        assert authored['value']['CPU_error_table_group_marks']==expected_marks
        assert authored['value']['CPU_error_table_group_applicability']==expected_applicability
        assert 'pending_symbol_semantics' not in authored['value']


def test_crc_exact_78_proposal_and_shared_family_correction_preserve_ownership():
    rows=entries();models={e['target_model'] for e in rows};assert len(models)==39
    contracts=json.loads((HERE/'contract-additions.json').read_text())['cpu_contracts'];assert len(contracts)==2
    for contract in contracts:
        assert set(contract['cpus'])==models and contract['instruction']['cpu_support']==contract['cpus']
        assert contract['native_order']==['S','D','n'] and contract['instruction']['arity']==dict(min=3,max=3)
        assert contract['execution_form']==('continuous' if contract['instruction']['mnemonic']=='CRC' else 'pulse')
    prior={(e['target_model'],e['opcode']):e for e in json.loads((HERE/'prior-owner-contracts.json').read_text())}
    for e in rows:assert all(f in e['facts'] for f in prior[e['target_model'],e['opcode']]['preserved_facts'])
    proposal=json.loads((HERE/'family-corrections.json').read_text())['corrections'][0]
    old,new=proposal['original'],proposal['replacement'];assert old['id']==new['id']=='family:CRC:review20261029:l_native:ladder:typed'
    for key in ['scope','depends_on','conditions','replaces']:assert old[key]==new[key]
    assert len(new['scope']['models'])==10
    s,d,n=new['value']['operands'];assert 'conditional_read_layout' in s and 'conditional_write_layout' in d
    assert new['sources'][0]['pdf_pages']==[133,459,460]


@pytest.mark.parametrize('op',['CRC','CRCP'])
def test_crc_default_projection_keeps_mode_and_algorithm_unknowns(op):
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units,technical_knowledge_text
    e=next(e for e in entries() if e['target_model']=='Q00U' and e['opcode']==op)
    packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['Q00U']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q00U',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
    record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='Q00U',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
    rendered=json.dumps(technical_context_units(technical_knowledge_text(record)))
    for token in ['SM772','conditional_read_layout','conditional_write_layout','general_exact_span_verified','head_storage_only','generator_polynomial','initial_CRC_verified','full_algorithm_established','documented_range_endpoints']:
        assert token in rendered,token


def test_crc_author_pipeline_binds_x460_geometry_for_all_selected_owners():
    from tools.fact_ql_behavior_review import reviewed_ql_behavior
    selectors=json.loads((HERE/'owner-selectors.json').read_text(encoding='utf-8'))
    source=sqlite3.connect('file:D:/gxworks-agent-private/resources/knowledge/fx3u_knowledge.sqlite?mode=ro&immutable=1',uri=True)
    try:
        fresh,_=reviewed_ql_behavior(source,entries=selectors,manuals=['sh080809engx'],
            opcodes={'CRC','CRCP'},source_pdf_root=Path('D:/gxworks-agent-private/resources/knowledge/sources'))
    finally:
        source.close()
    expected_marks={code:{'Q00J/Q00/Q01':'U+F0C9','QnH':'U+F0C9','QnPH':'U+F0C9',
        'QnPRH':'U+F0C9','QnU':'U+F0C6','LCPU':'U+F0C6'} for code in ('4100','4101')}
    expected_app={code:{'Q00J/Q00/Q01':False,'QnH':False,'QnPH':False,
        'QnPRH':False,'QnU':True,'LCPU':True} for code in ('4100','4101')}
    for owner in selectors:
        facts=fresh[(owner['target_model'],owner['opcode'])]
        error=next(f for f in facts if f['id']=='authored:ql:crc_documented_error_conditions:effects.documented_processing')
        assert error['status']=='source_verified' and error['verification']=='source_checked'
        assert error['value']['CPU_error_row_applicability']=='QnU_and_LCPU_only'
        assert error['value']['CPU_error_table_groups']==['Q00J/Q00/Q01','QnH','QnPH','QnPRH','QnU','LCPU']
        assert error['value']['CPU_error_table_group_marks']==expected_marks
        assert error['value']['CPU_error_table_group_applicability']==expected_app
        assert error['value']['error_flag']=='SM0' and error['value']['error_code_register']=='SD0'
        assert 'pending_symbol_semantics' not in error['value']
        assert any(source.get('manual_id')=='sh080809engx' and source.get('revision')=='X'
            and source.get('pdf_pages')==[460] and source.get('target_model')==owner['target_model']
            and source.get('opcode')==owner['opcode'] for source in error['sources'])

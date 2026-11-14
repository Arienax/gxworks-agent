"""DDVIT own signature and conditional hardware facts, without motion execution."""
import json
from pathlib import Path

from tools import fact_applied_review as producer

HERE = Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/fx-ddvit-native-contract-repair')


def entries():
    return json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries']


def test_DDVIT_exact_32bit_signature_nonzero_rule_and_direction_destinations():
    assert len(entries()) == 2
    for e in entries():
        facts = {f['id']: f for f in e['facts']}
        typed = facts['authored:applied:native_typed_operands@fx3_programming_r']
        assert typed['status'] == 'source_verified' and typed['verification'] == 'source_checked'
        pins = typed['value']['operands']
        assert [p['name'] for p in pins] == ['S1','S2','D1','D2']
        assert [p['direction'] for p in pins] == ['input','input','output','output']
        for p in pins[:2]:
            assert p['type'] == 'ANY32' and p['encoding'] == 'binary' and p['signed'] is True
            assert p['word_device_layout'] == dict(device_unit_bits=16,word_count=2,value_bits=32,signed=True)
            assert p['range']['device_prefixes'] == ['T','C','D','R']
            assert p['range']['digit_group_device_prefixes'] == ['X','Y','M','S']
            assert p['range']['address_forms'] == ['buffer_memory'] and p['range']['literal_prefixes'] == ['K','H']
        assert pins[2]['type'] == pins[3]['type'] == 'BOOL'
        assert pins[2]['range']['device_prefixes'] == ['Y']
        assert pins[3]['range']['device_prefixes'] == ['Y','M','S','D']
        assert pins[3]['range']['word_bit_device_prefixes'] == ['D']
        assert pins[3]['word_device_requires_bit_designation'] is True
        assert pins[3]['range']['word_bit_index_modification_allowed'] is False
        rule = facts['authored:applied:ddvit_numeric_ranges@fx3_programming_r']
        assert rule['dimension'] == 'constraints.numeric_ranges'
        assert rule['value']['ranges'][0] == dict(operand_index=0,absolute=True,minimum=0,minimum_inclusive=False,maximum=999999)
        family = facts['authored:applied:variant_family@fx3_programming_r']['value']
        assert family['fnc_number'] == 151 and family['base_opcode'] == 'DVIT'
        assert family['variant_bindings'] == {'DVIT':dict(data_bits=16,double=False,pulse=False),'DDVIT':dict(data_bits=32,double=True,pulse=False)}
        assert facts['authored:applied:program_steps@fx3_programming_r']['value'] == dict(steps=17)


def test_DDVIT_hardware_modes_feature_versions_and_busy_stop_conditions_remain_explicit():
    for e in entries():
        cpu=e['target_model'];facts={f['id']:f for f in e['facts']}
        typed=facts['authored:applied:native_typed_operands@fx3_programming_r']
        assert typed['conditions'] == [dict(context='project_mode',equals='native_ladder_instruction'),dict(context='cpu_version',minimum='2.20' if cpu=='FX3U' else '1.00')]
        hw=facts['authored:applied:ddvit_output_requirements@fx3_programming_r']['value']
        assert hw['kind'] == 'pulse_output_path'
        assert hw['main_unit']['pulse_devices'] == ['Y0','Y1','Y2'] and hw['main_unit']['maximum_Hz'] == 100000
        adapter=hw['high_speed_output_adapter']
        assert adapter['allowed'] is (cpu=='FX3U') and adapter['maximum_Hz']==200000
        assert adapter['adapter_count_required'] == dict(Y0=1,Y1=1,Y2=2,Y3=2)
        assert adapter['direction_output_pairs'] == dict(Y0='Y4',Y1='Y5',Y2='Y6',Y3='Y7')
        assert hw['software_direction_destinations']==['Y','M','S','D_word_bit']
        assert hw['current_physical_direction_routing_verified'] is False and hw['hardware_validation_complete'] is False
        op=facts['authored:applied:ddvit_runtime_operation@fx3_positioning_k']['value']
        assert op['direction_ON']=='positive_S1_forward' and op['direction_OFF']=='negative_S1_reverse'
        assert op['command_OFF_during_motion']==dict(pulse_output='decelerate_and_stop',M8029_set_ON=False)
        assert op['full_source_relation_complete'] is False and op['Core_execution_verified'] is False
        cfg=facts['authored:applied:ddvit_interrupt_configuration@fx3_positioning_k']['value']
        assert cfg['input_selection_minimum_version']==('2.20' if cpu=='FX3U' else '1.30')
        assert cfg['selection_8']['minimum_cpu_version']=='2.20'
        assert cfg['selection_9_through_E']==dict(error_code='K6763',operation='not_executed')
        assert cfg['current_selection_register_values_verified'] is False
        assert facts['authored:applied:ddvit_axis_resources@fx3_positioning_k']['value']['same_pulse_output_positioning_reuse_while_BUSY_READY_ON_allowed'] is False


def test_DDVIT_source_guard_rejects_wrong_page_or_missing_explicit_32bit_form(monkeypatch):
    record=dict(opcode='DVIT',manual_id='fx3_programming_r',revision='R',pdf_pages=[552,553],text='DDVIT 999,999 200,000 100,000 D8336 M8336')
    output={}
    producer._repair_fx_ddvit_source(None,[dict(record,pdf_pages=[552])],output)
    assert not output
    monkeypatch.setattr(producer,'_source_table_groups',lambda *_:[])
    monkeypatch.setattr(producer,'_parameter_source_pages',lambda *_:[])
    monkeypatch.setattr(producer,'_programming_manual_variants',lambda *_:[dict(opcode='DVIT',width_bits=16,execution_form='continuous')])
    producer._repair_fx_ddvit_source(None,[record],output)
    assert not output


def test_DDVIT_CPU520_four_independent_native_bodies_and_reader():
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    from plc.device_identity import canonical_reference_device
    evidence=json.loads((HERE/'native-forms/probes.json').read_text(encoding='utf-8'))
    assert evidence['cpu_code']==520 and len(evidence['cases'])==4
    for case in evidence['cases']:
        op,*args=case['text'].split()
        assert op=='DDVIT' and case['encode_result']==case['decode_result']=='0x00000000'
        assert case['native_consumed_all'] is True
        raw=encode_instruction(op,args,profile='fx3u',plc_model='FX3U',text_encoding='cp936')
        assert raw[:5]==bytes.fromhex('0563112205') and raw[2]==17
        raw+=encode_instruction('END',[],profile='fx3u',plc_model='FX3U',text_encoding='cp936')
        assert raw==bytes.fromhex(case['body_hex']),case['text']
        listing=decode_token_program(parse_token_fragment(raw,0,len(raw)),profile='fx3u',text_encoding='cp936')
        assert not listing.gaps and listing.reconstruct()==raw
        actual=[(r.mnemonic,[canonical_reference_device(a) for a in r.args]) for r in listing.instructions]
        expected=[(r['op'],[canonical_reference_device(a) for a in r['args']]) for r in case['native_records']]
        assert actual==expected
        assert actual[0]==(op,args)


def test_DDVIT_constructor_is_CPU520_profile_only_and_does_not_infer_a_pulse_form():
    import pytest
    from gxw.models import GXWFormatError
    from gxw.token_encoder import encode_instruction
    for profile,cpu in [('fx3u','FX3UC'),('fx3g','FX3G'),('fx3u',None)]:
        with pytest.raises(GXWFormatError,match='only observed for FX3U/fx3u'):
            encode_instruction('DDVIT',['K1','K100','Y0','M0'],profile=profile,plc_model=cpu,text_encoding='cp936')
    for op,args in [('DDVITP',['K1','K100','Y0','M0']),('DDVIT',['K1','K100','Y0']),('DDVIT',['K1','K100','Y0','M0','M1']),('DDVIT',['K1','K100','K0','M0']),('DDVIT',['E1','K100','Y0','M0'])]:
        with pytest.raises(GXWFormatError):
            encode_instruction(op,args,profile='fx3u',plc_model='FX3U',text_encoding='cp936')

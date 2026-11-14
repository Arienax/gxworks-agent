"""Own-X zero-slot CHK blocks retain their surrounding grammar and runtime limits."""
import json
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-chk-native-contract-repair')
OPS=('CHKST','CHK','CHKCIR','CHKEND')

@pytest.mark.parametrize('op',OPS)
def test_chk_own_zero_slots_and_conditional_diagnostic_rules(op):
    rows=[e for e in json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries'] if e['opcode']==op]
    assert len(rows)==11
    for e in rows:
        facts={f['id']:f for f in e['facts']};assert len(facts)==len(e['facts'])
        typed=facts['authored:ql:native_arguments_zero:parameters.typed_operands']
        assert typed['status']=='source_verified' and typed['value']['declaration_kind']=='instruction'
        assert typed['value']['representation']=='native_ladder_instruction' and typed['value']['operand_count']==0
        assert typed['value']['operands']==[]
        assert typed['conditions']==[{'context':'project_mode','equals':'native_ladder_instruction'}]
        grammar=typed['value']['native_form']['surrounding_ladder_grammar']
        doc=facts['authored:ql:chk_diagnostic_context:effects.documented_processing']
        assert doc['status']=='source_verified' and typed['id'] in doc['depends_on']
        value=doc['value']
        assert value['ordinary_contact_execution'] is False
        for field in ['core_execution_established','native_execution_verified','current_SM710_verified','current_diagnostic_device_values_verified','all_executed_program_files_context_verified']:
            assert value[field] is False
        assert value['maximum_locations_per_program_file']==1 and value['maximum_locations_all_executed_program_files']==2
        assert value['diagnostic_output']['flag']=='SM80' and value['diagnostic_output']['failure_number']=='SD80' and value['diagnostic_output']['encoding']=='BCD'
        assert value['scan_order']=={'SM710_OFF':'contact_major_then_coil','SM710_ON':'coil_major_then_contact'}
        if op in ('CHKST','CHK'):
            assert grammar['condition_instructions']==['LD','LDI','AND','ANI']
            assert grammar['effective_condition_instructions']==['LD','AND']
            assert grammar['ignored_check_condition_instructions']==['LDI','ANI']
            assert value['ignored_conditions_still_allocate_contact_numbers'] is True
            assert value['maximum_condition_contacts']==150 and value['failure_detected_error_code']=='9010'
            assert value['user_reset_required_before_reexecution']==['SM80','SD80']
            assert value['CHKST_OFF']=='jump_to_instruction_after_CHK'
        else:
            assert grammar['maximum_pattern_steps']==256 and grammar['maximum_pattern_coils']==9
            assert value['pattern_contact_device_classes']==['X','Y'] and value['pattern_coil_device_class']=='F'
            assert value['dummy_F_actual_state_changes'] is False and value['dummy_F_numbers_may_overlap'] is True
        error_fact=facts['authored:ql:chk_documented_error_conditions:effects.documented_processing']
        assert error_fact['status']=='candidate_evidence' and error_fact['verification']=='source_checked'
        error=error_fact['value']
        assert error['CPU_error_row_applicability']=='unknown'
        assert error['CPU_error_row_applicability_established'] is False and error['pending_symbol_semantics'] is True
        assert error['source_CPU_markers']==['U+F0C6','U+F0C9']
        if op in ('CHKCIR','CHKEND'):
            assert error['index_modification_error_boundary_established'] is False
        assert all(set(f.get('depends_on',[]))<=facts.keys() for f in e['facts'])


@pytest.mark.parametrize('op',OPS)
def test_chk_default_projection_keeps_diagnostic_context_and_error_pending(op):
    import copy
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_context_units,technical_knowledge_text
    e=next(e for e in json.loads((HERE/'source-facts.json').read_text(encoding='utf-8'))['entries'] if e['opcode']==op and e['target_model']=='Q02H')
    packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['Q02H']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q02H',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
    record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='Q02H',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
    rendered=json.dumps(technical_context_units(technical_knowledge_text(record)))
    for item in ['SM80','SD80','BCD','SM710_OFF','pending_symbol_semantics','U+F0C6','U+F0C9']:
        assert item in rendered,item
    for field in ['ordinary_contact_execution','core_execution_established','current_SM710_verified','all_executed_program_files_context_verified','CPU_error_row_applicability_established']:
        assert '"'+field+'": false' in rendered,field
    if op in ('CHKST','CHK'):
        assert '9010' in rendered and 'ignored_conditions_still_allocate_contact_numbers' in rendered
    else:
        assert '"dummy_F_actual_state_changes": false' in rendered
        assert '"index_modification_error_boundary_established": false' in rendered

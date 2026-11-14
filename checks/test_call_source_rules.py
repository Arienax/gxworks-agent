"""CALL's own conditional argument knowledge does not establish a callee."""
import copy,json
from pathlib import Path
import pytest

HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-call-native-contract-repair')

@pytest.mark.parametrize('op',['CALL','CALLP'])
def test_call_own_source_keeps_optional_arguments_conditional_and_no_XCALL(op):
    entries=json.loads((HERE/'source-facts.json').read_text())['entries']
    selected=[e for e in entries if e['opcode']==op];assert len(selected)==53
    for e in selected:
        own=[f for f in e['facts'] if any(s['manual_id']=='sh080809engx' for s in f['sources'])]
        assert all(all(466<=p<=470 for p in s['pdf_pages']) for f in own for s in f['sources'])
        facts={f['id']:f for f in own}
        typed=facts['authored:ql:native_arguments_pn_s1_s2_s3_s4_s5:parameters.typed_operands']
        assert typed['status']=='source_verified' and typed['value']['native_form']==dict(operand_order=['Pn','S1','S2','S3','S4','S5'],minimum_operands=1,maximum_operands=6)
        pointer,*args=typed['value']['operands']
        assert pointer['type']=='POINTER' and pointer['device_class']=='P' and 'word_device_layout' not in pointer
        for arg in args:
            assert arg['optional'] is True and arg['direction']=='in_out'
            assert arg['type_alternatives']==['BOOL','ANY16','ANY32'] and 'word_device_layout' not in arg and 'bits' not in arg
            layout=arg['conditional_argument_layout'];assert layout['selector_binding_verified'] is False and layout['unbound_argument_layout_verified'] is False
            fx,fy,fd=layout['branches']
            assert [b['callee_function_device'] for b in layout['branches']]==['FX','FY','FD']
            assert fx['direction']=='input' and fy['direction']=='output' and fd['direction']=='in_out'
            assert fx['logical_bits']==fy['logical_bits']==1 and fd['word_device_layout']==dict(device_unit_bits=16,word_count=4)
        jump=facts['authored:ql:call:execution.jump']['value']
        assert jump['target_scope']=='local_same_program_file_or_configured_common_pointer' and jump['return_instruction']=='RET'
        assert jump['callee_binding_verified'] is False and jump['common_pointer_configuration_verified'] is False
        nesting=facts['authored:ql:call_return_context:effects.documented_processing']['value']
        assert nesting['maximum_shared_nesting_levels']==16 and len(nesting['shared_with'])==5 and nesting['actual_shared_call_graph_verified'] is False
        assert facts['authored:ql:call_context_pending:execution.unresolved_source']['status']=='unknown'

@pytest.mark.parametrize('op',['CALL','CALLP'])
def test_call_default_knowledge_retains_FD_four_words_and_unknown_callee(op):
    from knowledge.fact_selection import project_packet
    from shared.model_context import technical_knowledge_text,technical_context_units
    e=next(e for e in json.loads((HERE/'source-facts.json').read_text())['entries'] if e['opcode']==op and e['target_model']=='Q03UDV')
    packet=project_packet(dict(kind='instruction',target=op,scope=dict(models=['Q03UDV']),content_origin='authored_fact',value=copy.deepcopy(e)),plc_model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',aspects=['programming_semantics'])
    record=dict(text=json.dumps(packet),fact_kind='instruction',fact_target=op,_knowledge_context=dict(model='Q03UDV',language='ladder',project_mode='native_ladder_instruction',delivery_languages=['ladder']))
    value=json.dumps(technical_context_units(technical_knowledge_text(record)))
    for field in ['conditional_argument_layout','callee_function_device_interface','word_count','maximum_shared_nesting_levels','local_same_program_file_or_configured_common_pointer']:
        assert field in value,field
    assert '"selector_binding_verified": false' in value and '"callee_binding_verified": false' in value

def test_call_section_view_excludes_XCALL_even_with_same_grouped_title():
    from tools.fact_ql_behavior_review import Section,_native_call_section
    def row(identity,page,text):
        return dict(id=identity,pdf_page=page,pdf_page_end=page,text=text,instruction_opcode='XCALL',chunk_type='instruction')
    section=Section('sh080809engx','X','SH080809ENG','ladder','Subroutine program calls',[
        row(1,466,'CALL(P)\nOwn CALL statement.'),row(2,485,'XCALL\nForeign XCALL statement.')])
    section._flat=section.flat_rows();section._geometry_flat=[(1,466,'Own drawing'),(2,485,'Foreign drawing')]
    section._native_parameter_contracts=[dict(page=466,allowed=['CALL','CALLP'],order=['Pn'],minimum=1,rows={}),dict(page=485,allowed=['XCALL'],order=['Pn'],minimum=1,rows={})]
    own=_native_call_section(section)
    assert own.pages==[466] and not own.bind('Foreign XCALL statement.') and not own.bind('Foreign drawing')
    assert [r['id'] for r in section.rows]==[1,2] and len(section._native_parameter_contracts)==2
    assert len(own._native_parameter_contracts)==1

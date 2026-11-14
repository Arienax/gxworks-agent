"""A reviewed pointer identity domain never grants storage or allocation."""
import copy
import pytest

@pytest.mark.parametrize('op',['BREAK','BREAKP'])
def test_break_cpu_pointer_domain_binds_profile_and_preserves_other_unknowns(op):
    from tools.fact_ql_behavior_review import _bind_break_pointer_domain
    from plc.device_profiles import program_pointer_domain
    cpu={'id':'authored:ql:cpu_applicability:constraints.cpu_availability','status':'source_verified'}
    effect=dict(id='authored:ql:effect:effects.operation',status='source_verified',
        value=dict(operation='exit_FOR_NEXT_loop',cpu_pointer_number_limits_established=False,
            core_execution_established=False,control_flow_context_verified=False),
        scope=dict(models=['Q03UDV'],forms=[op],languages=['ladder']),
        conditions=[dict(context='project_mode',equals='native_ladder_instruction')],depends_on=[cpu['id']])
    rows=[cpu,effect];before=copy.deepcopy(rows)
    actual=_bind_break_pointer_domain(rows,'Q03UDV',op)
    assert rows==before
    domain=actual[-1];profile=program_pointer_domain('Q03UDV')
    assert domain['dimension']=='parameters.program_pointer_domain'
    assert domain['value']['maximum_number']==profile['maximum_number']
    assert domain['value']['minimum_number']==profile['minimum_number']
    assert domain['value']['allocation_verified'] is False and domain['value']['parameter']=='Pn'
    assert 'word_device_layout' not in domain['value'] and 'bits' not in domain['value']
    assert all(s['target_model']=='Q03UDV' and s['covered_models']==['Q03UDV'] and s['pdf_pages'] for s in domain['sources'])
    current=actual[1]
    assert current['value']['cpu_pointer_number_limits_established'] is True
    assert current['value']['core_execution_established'] is False and current['value']['control_flow_context_verified'] is False
    assert domain['id'] in current['depends_on']
    assert _bind_break_pointer_domain(rows,'FX3U',op)==before
    assert _bind_break_pointer_domain(rows,'unknown',op)==before
    assert _bind_break_pointer_domain(rows,'Q03UDV','CALL')==before
    assert _bind_break_pointer_domain([effect],'Q03UDV',op)==[effect]

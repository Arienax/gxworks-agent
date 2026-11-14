"""Saved FBD FB property bits are decoded only for the measured properties."""
from gxw.callable_sources import pou_status_options


def test_existing_FX1_FBD_project_keeps_its_exact_CPU_and_source_context():
    from application.fbd import _target_context
    from gxw.project_templates import blank_fbd_baseline
    baseline = blank_fbd_baseline('FX1')
    context = _target_context(baseline, 'FX1')
    assert context.raw == baseline
    assert context.sources.cpu == 'FX1'
    assert context.program.logical_name in context.fbd_programs


def test_existing_FBD_project_still_rejects_a_different_CPU():
    import pytest
    from application.fbd import _target_context, FBDValidationError
    from gxw.project_templates import blank_fbd_baseline
    with pytest.raises(FBDValidationError, match='does not match selected CPU'):
        _target_context(blank_fbd_baseline('FX1'), 'FX0N')


def test_FX1_blank_FBD_generation_keeps_its_unverified_allocation_restriction():
    import pytest
    from application.fbd import _target_context, FBDValidationError
    with pytest.raises(FBDValidationError, match='0x050F1034'):
        _target_context(None, 'FX1')


def test_fb_property_status_bits_map_to_the_three_saved_options():
    assert pou_status_options(None) == {
        'use_macrocode': None, 'use_mc_mcr': None, 'use_en_eno': None}
    assert pou_status_options(0) == {
        'use_macrocode': False, 'use_mc_mcr': False, 'use_en_eno': False}
    assert pou_status_options(1) == {
        'use_macrocode': True, 'use_mc_mcr': False, 'use_en_eno': False}
    assert pou_status_options(7) == {
        'use_macrocode': True, 'use_mc_mcr': True, 'use_en_eno': True}


def test_upper_status_bits_do_not_become_a_property_flag():
    assert pou_status_options(8) == {
        'use_macrocode': False, 'use_mc_mcr': None, 'use_en_eno': False}
    assert pou_status_options(15) == {
        'use_macrocode': True, 'use_mc_mcr': None, 'use_en_eno': True}
    assert pou_status_options(2)['use_mc_mcr'] is None
    assert pou_status_options(3)['use_mc_mcr'] is None


def test_task_event_modes_priority_and_disabled_outputs_keep_their_conditions():
    from plc.fbd_semantics import language_rules
    for cpu in ['FX3U','Q03UDV','L02']:
        rules=language_rules('FBD.PROJECT',cpu)
        tasks=rules['task_execution_conditions']
        assert tasks['event_field']['TRUE']=={'mode':'always','frequency':'every_scan'}
        assert tasks['event_field']['FALSE']=={'mode':'interval','period_field':'Interval'}
        assert tasks['event_field']['device_or_label']=={'mode':'event','execute_when':True}
        assert tasks['priority']=={'minimum':0,'maximum':31,'order':'ascending','equal_priority_order':'task_name_alphabetical'}
        assert tasks['interval']['minimum']=='sequence_program_scan_time'
        assert tasks['timer_output_control']=={'when':'task_not_executed','selected':'reset',
            'cleared':'retain','targets':['outputs','timer_current_values']}
        assert rules['program_file_and_task_structure']['FX']['program_file_execution_type_classification'] is False


def test_branch_compilation_does_not_turn_changing_scan_devices_into_stable_values():
    from plc.fbd_semantics import language_rules
    rules=language_rules('FBD.EXECUTION','Q03UDV')
    branches=rules['branch_evaluation_constraints']
    assert branches['one_contact_branch']['compiled_load']=='repeated_LD'
    assert branches['multiple_contacts']['compiled_result']=='temporary_variable'
    assert branches['changing_device_precaution']['condition']=='device_changes_within_scan'
    assert branches['changing_device_precaution']['example']=='SM412'
    assert 'skipped_branch_program_parts' in branches['changing_device_precaution']['possible_effects']
    assert rules['direct_edge_output_connection_conditions']['ALT_CPU_family']=='FX'
    assert rules['fbd_connection']['left_power_rail_required'] is False
    assert rules['structured_ladder_connection']['left_power_rail_connected'] is True


def test_compile_assignment_and_diagnostic_limits_are_distinct_from_execution():
    from plc.fbd_semantics import language_rules
    rules=language_rules('FBD.COMPILE_OPTIONS','Q03UDV')
    policy=rules['compile_target_and_device_assignment']
    assert policy['assignment_order']=={'label_scopes':['global','local'],'label_rows':'top_down',
        'device_numbers':'descending','P_numbers':'ascending'}
    assert policy['device_assignment']['when']=='blank_device_or_address'
    assert policy['retained_old_values']['may_remain_after_compile_all'] is True
    diagnostics=rules['diagnostic_display_and_stop_policy']
    assert diagnostics['error_count_range']=={'minimum':1,'maximum':9999}
    assert diagnostics['warning_count_range']=={'minimum':1,'maximum':9999}
    assert diagnostics['max_hidden_warning_codes']==100
    assert rules['option_caller_language_scope']['language_scope']['defines_CPU_execution_semantics'] is False
    assert rules['option_caller_language_scope']['st_caller_option']['caller_language']=='st'
    assert rules['option_caller_language_scope']['hex_device_exception']['may_cause_compile_error_with_option_enabled'] is True


def test_symbol_types_indexes_and_FX_retain_exclusion_survive_normal_model_projection():
    from plc.fbd_semantics import language_rules
    from shared.model_context import model_knowledge_value
    rules=model_knowledge_value(language_rules('FBD.SYMBOLS','FX3U'))
    classes=rules['label_class_scope_rules']
    assert classes['VAR_INPUT']['writable_in_POU'] is False
    assert classes['VAR_IN_OUT']['writable_in_POU'] is True
    assert classes['VAR_RETAIN']['unsupported_CPU_family']=='FX'
    indexes=rules['index_and_bounds_constraints']
    assert indexes['index_range']=={'minimum':-32768,'maximum':32767}
    assert indexes['index_types']==['INT','DINT']
    assert indexes['index_register_restriction']['unavailable_in_program']==['Z0','Z1']
    assert 'other_label_write' in indexes['out_of_range_possible_effects']
    structures=rules['structure_array_device_binding']
    assert structures['dimensions']['maximum']==3
    assert structures['bit_element_restriction']['bit_specified_word_device_allowed'] is False
    assert rules['index_and_bounds_constraints']['display_format_changes_label_identity'] is False


def test_language_rule_groups_merge_independent_fields_without_mutating_source():
    from plc.fbd_semantics import language_rules
    class Registry:
        values=[{'options':{'enabled':True}},{'options':{'limit':10}}]
        def fact_bundle_current(self):return True
        def language_feature(self,*args,**kwargs):
            return {'facts':[{'scope':{'models':['FX3U'],'languages':['fbd']},
                'status':'source_verified','value':v} for v in self.values]}
    registry=Registry()
    result=language_rules('FBD.PROJECT','FX3U',registry=registry)
    assert result['options']=={'enabled':True,'limit':10}
    result['options']['limit']=20
    assert registry.values==[{'options':{'enabled':True}},{'options':{'limit':10}}]


def test_nested_language_rule_disagreement_still_reports_the_exact_field():
    import pytest
    from plc.fbd_semantics import language_rules,FBDSemanticError
    class Registry:
        def fact_bundle_current(self):return True
        def language_feature(self,*args,**kwargs):
            return {'facts':[{'scope':{'models':['FX3U'],'languages':['fbd']},
                'status':'source_verified','value':{'options':{'enabled':v}}} for v in [True,False]]}
    with pytest.raises(FBDSemanticError) as caught:
        language_rules('FBD.PROJECT','FX3U',registry=Registry())
    assert caught.value.finding['code']=='fbd_rule_conflict'
    assert caught.value.finding['field']=='options.enabled'
    assert caught.value.finding['severity']=='unverified'


def test_project_rule_groups_load_for_every_published_GXWorks2_member(subtests):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.fbd_semantics import language_rules
    for target in ['FBD.COMPILE_OPTIONS','FBD.EXECUTION','FBD.PROJECT','FBD.SYMBOLS']:
        feature=DEFAULT_INSTRUCTION_REGISTRY.language_feature(target,language='fbd')
        models={m for f in feature['facts'] for m in f['scope']['models'] if not m.startswith('FX5')}
        assert len(models)==69
        for model in sorted(models):
            with subtests.test(rule=target,cpu=model):
                assert language_rules(target,model)


def _option_metadata(words, suffix):
    import struct
    from gxw.container_writer import validate_cfb_streams
    from gxw.project_metadata import project_stream_mapping
    from gxw.project_templates import blank_fbd_baseline
    outer=validate_cfb_streams(blank_fbd_baseline('FX1'))
    data=validate_cfb_streams(outer['_hdb'])[project_stream_mapping(outer)['CPU_MATRIX.prj']]
    # Independent native FX1 save: options start at byte 158, after the 88-byte ranges.
    assert data[158:166]==bytes.fromhex('01000000ffffffff')
    return data[:158]+struct.pack('<I',len(words))+struct.pack('<'+'I'*len(words),*words)+suffix


def _option_observation(values):
    return {'native_version':'1.635.0.1','creation':{'project_attributes':{'attribute':1,'mode':2},
        'compile_options_before':list(values),'compile_options_after':list(values)}}


def test_native_compile_profile_initialization_preserves_all_other_words_and_suffix():
    from hypothesis import given,settings,strategies as st
    from gxw.project_metadata import native_project_compile_option_initialization
    unknown=st.one_of(st.integers(0,0x1006ffff),st.integers(0x10080000,0xfffffffe))
    @settings(max_examples=80,deadline=None)
    @given(st.lists(unknown,max_size=30),st.binary(max_size=128))
    def check(words,suffix):
        old=[*words,0xffffffff];new=[*words,0x10070000,0xffffffff]
        before,after=_option_metadata(old,suffix),_option_metadata(new,suffix)
        original=(before,after)
        result=native_project_compile_option_initialization(before,after,_option_observation(new))
        assert result['before']==old and result['after']==new
        assert result['other_options_prefix_and_suffix']=='exact'
        assert (before,after)==original
    check()


def test_native_compile_profile_does_not_allow_changes_in_unknown_suffix_bytes():
    from hypothesis import given,settings,strategies as st
    from gxw.project_metadata import native_project_compile_option_initialization
    @settings(max_examples=60,deadline=None)
    @given(st.binary(min_size=1,max_size=128),st.integers(min_value=0,max_value=127))
    def check(suffix,index):
        modified=bytearray(suffix);modified[index%len(suffix)]^=1
        before=_option_metadata([0xffffffff],suffix)
        after=_option_metadata([0x10070000,0xffffffff],bytes(modified))
        assert native_project_compile_option_initialization(before,after,_option_observation([0x10070000,0xffffffff])) is None
    check()


def test_native_compile_profile_does_not_allow_reordering_existing_options():
    from hypothesis import given,settings,strategies as st
    from gxw.project_metadata import native_project_compile_option_initialization
    @settings(max_examples=50,deadline=None)
    @given(st.lists(st.integers(0,0xffff),min_size=2,max_size=30,unique=True))
    def check(words):
        before=_option_metadata([*words,0xffffffff],b'untouched')
        changed=[*reversed(words),0x10070000,0xffffffff]
        after=_option_metadata(changed,b'untouched')
        assert native_project_compile_option_initialization(before,after,_option_observation(changed)) is None
    check()


def test_native_compile_profile_requires_original_getters_and_matching_project_attributes():
    import copy
    from gxw.project_metadata import native_project_compile_option_initialization
    new=[0x10070000,0xffffffff]
    before,after=_option_metadata([0xffffffff],b'exact'),_option_metadata(new,b'exact')
    observation=_option_observation(new)
    variants=[]
    v=copy.deepcopy(observation);v['native_version']='1.634.0.0';variants.append(v)
    v=copy.deepcopy(observation);v['creation']['project_attributes']['mode']=1;variants.append(v)
    v=copy.deepcopy(observation);v['creation']['compile_options_before']=[0xffffffff];variants.append(v)
    v=copy.deepcopy(observation);v['creation'].pop('compile_options_after');variants.append(v)
    variants.append({'native_version':'1.635.0.1','creation':{}})
    for v in variants:assert native_project_compile_option_initialization(before,after,v) is None
    configured=_option_metadata([0x10070001,0xffffffff],b'exact')
    assert native_project_compile_option_initialization(configured,after,observation) is None
    modified=bytearray(after);modified[70]^=1  # first D allocation bound
    assert native_project_compile_option_initialization(before,bytes(modified),observation) is None



def test_bit_output_retention_lists_only_AG189_five_builtin_groups_and_both_modes():
    from tools.fact_structured_behavior_review import TOOL_BINDINGS
    fact=next(f for f in TOOL_BINDINGS if f['id']=='gxworks2:bit_output_retention')
    value=fact['value']['function_bit_output_retention']
    expected={
        'conversion':['INT_TO_BOOL_E','DINT_TO_BOOL_E','WORD_TO_BOOL_E','DWORD_TO_BOOL_E','TIME_TO_BOOL_E'],
        'not':['NOT_E'], 'selection_functions':['LIMITATION_E','MAXIMUM_E','MINIMUM_E'],
        'comparison':['EQ_E','NE_E','GT_E','GE_E','LT_E','LE_E'],
        'logic':['AND_E','OR_E','XOR_E']}
    assert value['option_groups']==expected
    listed={name for names in value['option_groups'].values() for name in names}
    assert len(listed)==18
    assert not listed.intersection({'STR_TO_BOOL_E','INT_TO_BITARR_E','DINT_TO_BITARR_E',
        'CPY_BITARR_E','GET_BIT_OF_INT_E','MOVE_E','SHL_E','SHR_E','SEL_E','MUX_E'})
    assert value['selected']=={'equivalent_instruction':'SET','bit_outputs_retained':True}
    assert value['cleared']=={'equivalent_instruction':'OUT','bit_outputs_retained':False}
    assert 'documented_alternative' not in fact['value']
    assert fact['languages']==['fbd']
    assert value['data_output_scope']=={'required_declared_type':'BOOL',
        'output':'function_data_return_value','excluded_outputs':['ENO'],'actual_output_declaration_required':True}
    assert value['effective_group_setting']=='requires_current_project_context'
    assert value['missing_group_setting']=='state_not_determined'
    assert value['project_option_default']=='not_assumed'
    assert value['st_named_call_assignment_contract']=='not_established_by_this_feature'
    assert value['example']=={'function':'INT_TO_BOOL_E','when_EN_false':{
        'selected_data_output':'retain_previous','cleared_data_output':False}}


def test_bit_output_retention_source_requires_explicit_OUT_and_SET_conditions():
    import copy
    import pytest
    from tools import fact_structured_behavior_review as review
    spec=next(f for f in review.TOOL_BINDINGS if f['id']=='gxworks2:bit_output_retention')
    text=' '.join(spec['witness'])
    row={'id':1,'manual_id':'gxworks2_structured_ag','manual_number':'SH-080781ENG',
         'revision':'AG','pdf_page':189,'pdf_page_end':189,'section':spec['section'],
         'document_plc_models':'FX3U','text':text}
    original=review.TOOL_BINDINGS
    try:
        review.TOOL_BINDINGS=(spec,)
        receipt={'tool_binding_sections':[]}
        facts=review._tool_binding_facts(None,{1:row},review.TOOL_MANUAL,receipt)
        assert len(facts)==1 and facts[0]['id']=='gxworks2:bit_output_retention'
        assert facts[0]['scope']['languages']==['fbd']
        assert facts[0]['sources'][0]['pdf_pages']==[189]
        for phrase in spec['witness']:
            changed=copy.deepcopy(row);changed['text']=text.replace(phrase,'')
            with pytest.raises(ValueError):
                review._tool_binding_facts(None,{1:changed},review.TOOL_MANUAL,{'tool_binding_sections':[]})
    finally:
        review.TOOL_BINDINGS=original


def test_bit_output_retention_conditions_survive_normal_model_projection_without_option_defaults():
    import copy
    from tools.fact_structured_behavior_review import BIT_OUTPUT_RETENTION_CONTRACT
    from shared.model_context import model_knowledge_value,technical_value
    from plc.fbd_semantics import language_rules
    value={'function_bit_output_retention':copy.deepcopy(BIT_OUTPUT_RETENTION_CONTRACT)}
    class Registry:
        def fact_bundle_current(self):return True
        def language_feature(self,*args,**kwargs):
            return {'facts':[{'scope':{'models':['FX3U'],'languages':['fbd']},
                'status':'source_verified','value':copy.deepcopy(value)}]}
    rules=language_rules('FBD.EXECUTION','FX3U',registry=Registry())
    displayed=model_knowledge_value(technical_value(rules))['function_bit_output_retention']
    for key in ('option_groups','selected','cleared','data_output_scope','effective_group_setting',
                'missing_group_setting','project_option_default','st_named_call_assignment_contract','example'):
        assert displayed[key]==BIT_OUTPUT_RETENTION_CONTRACT[key]
    assert 'effective_value' not in displayed and 'current_checkbox_value' not in displayed

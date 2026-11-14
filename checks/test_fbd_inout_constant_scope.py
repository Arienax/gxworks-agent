from tools.fbd_fact_review import INOUT_INPUT_CONSTANT


def test_validator_uses_the_bound_callee_language_and_output_use():
    from gxw.fbd_validation import inout_constant_allowed

    rules = {'FBD.CALL.BINDING': {'inout_input_constant': INOUT_INPUT_CONSTANT}}
    assert inout_constant_allowed(rules, 'fbd', 'BOOL', True) is False
    assert inout_constant_allowed(rules, 'fbd', 'BOOL', False) is True
    assert inout_constant_allowed(rules, 'st', 'INT', True) is False
    assert inout_constant_allowed(rules, 'ladder', 'BOOL', True) is True
    assert inout_constant_allowed(rules, 'ladder', 'INT', False) is True
    assert inout_constant_allowed(rules, 'ladder', 'INT', True) is False
    assert inout_constant_allowed(rules, 'fbd', 'BOOL', None) is None


def test_structured_caller_constant_depends_on_callee_output_use():
    assert INOUT_INPUT_CONSTANT['fbd']['BOOL'] == {'unused': True, 'written': False}
    assert INOUT_INPUT_CONSTANT['st']['BOOL'] == {'unused': True, 'written': False}
    assert INOUT_INPUT_CONSTANT['fbd']['other'] == {'unused': True, 'written': False}
    assert INOUT_INPUT_CONSTANT['st']['other'] == {'unused': True, 'written': False}


def test_ladder_callee_keeps_bool_constant_and_allows_non_bool_only_on_input():
    assert INOUT_INPUT_CONSTANT['ladder']['BOOL'] == {'unused': True, 'written': True}
    assert INOUT_INPUT_CONSTANT['ladder']['other'] == {'unused': True, 'written': False}


def test_no_unqualified_fx_prohibition_remains_in_the_call_consideration_fact():
    from tools.fact_structured_behavior_review import TOOL_BINDINGS

    fact = next(record for record in TOOL_BINDINGS
                if record['id'] == 'gxworks2:call_considerations')
    assert 'constant_for_input_side_of_VAR_IN_OUT' not in fact['value']
    assert fact['pages'] == (196, 197)


def test_unconnected_inputs_report_the_call_binding_rule_without_inventing_defaults():
    from gxw.project_templates import blank_fbd_baseline
    from gxw.object_model import generate_object_project,read_project_context,source_checks
    baseline=blank_fbd_baseline('Q03UDV')
    context=read_project_context(baseline)
    model={'program':context.program.logical_name,'networks':[{'calls':[{
        'id':'timer','type':'TON','instance':'Timer1','arguments':{'Q':'M0'}}]}]}
    saved=generate_object_project(model,baseline=baseline)
    checks=source_checks(read_project_context(saved.data))
    unbound=[f for f in checks['findings'] if f['code']=='fbd_unbound_formal_coverage']
    assert {f['formal'] for f in unbound}=={'IN','PT'}
    assert all(f['rule']=='FBD.CALL.BINDING' and f['severity']=='unverified' for f in unbound)
    assert all('default_value' not in f for f in unbound)


def test_current_call_operator_and_timer_rules_load_with_their_exact_conditions():
    from plc.fbd_semantics import language_rules
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    for cpu in ['FX3U','FX3UC','Q03UDV','L02']:
        call=language_rules('FBD.CALL.BINDING',cpu)
        values=call['argument_evaluation_storage']
        assert values['effect_when_enabled']['retained_across']==['calls','online_program_change']
        assert values['effect_when_disabled']['retained_across_online_program_change'] is False
        assert values['structure_arguments']['temporary_variables_supported'] is False
        assert call['inout_input_constant']['fbd']['BOOL']=={'unused':True,'written':False}
        assert 'st_caller_var_output_binding' not in call
        language_rules('FBD.OPERATORS',cpu)
        language_rules('FBD.TIMERS',cpu)
    st=registry.language_feature('ST.FB.OUTPUT_ARGUMENT',language='st')
    moved,=[f for f in st['facts'] if f['id']=='fbd.var_output_at_fb_call']
    assert moved['scope']['languages']==['st'] and moved['scope']['forms']==['ST.FB.OUTPUT_ARGUMENT']
    assert moved['value']['st_caller_var_output_binding']['caller_language']=='st'
    assert not set(moved['scope']['models']) & {'FX5S','FX5UJ','FX5U','FX5UC'}


def _reviewed_operator_fixture(monkeypatch):
    from types import ModuleType, SimpleNamespace
    from tools.fbd_fact_review import reviewed_fbd_features
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    source_helpers = ModuleType('research.review_tools.fact_condition_review')
    source_helpers._source = lambda source, manual, revision, page, section, witnesses: {
        'manual_id': manual, 'revision': revision, 'pdf_pages': [page],
        'source_ref': f'{manual}:{revision}:pages={page}'}
    source_helpers.header_availability = lambda source, reference: {
        'model_states': {'FX3U': 'supported', 'FX3UC': 'supported'}}
    monkeypatch.setitem(__import__('sys').modules, 'research.review_tools.fact_condition_review', source_helpers)
    class Source:
        def execute(self, sql, args):
            models = 'FX1,FX3U,FX3UC,Q03UDV' if args[0] == 'structured_fundamentals_o' else 'FX3U,FX3UC'
            return SimpleNamespace(fetchone=lambda: (models,))
    feature = next(f for f in reviewed_fbd_features(Source()) if f['target'] == 'FBD.OPERATORS')
    monkeypatch.setitem(DEFAULT_INSTRUCTION_REGISTRY._language_features, 'FBD.OPERATORS', feature)
    return feature


def test_GT_operator_difference_does_not_merge_the_GT_E_function_domain(monkeypatch):
    from plc.fbd_semantics import language_rules,call_type_groups
    _reviewed_operator_fixture(monkeypatch)
    fx=language_rules('FBD.OPERATORS','FX3U');ql=language_rules('FBD.OPERATORS','Q03UDV')
    assert fx['operators']['GT']['inputs']==['BOOL','WORD','DWORD']
    assert 'REAL' in fx['operators']['GT_E']['inputs']
    assert fx['standard_function_aliases']['GT_E']=='GT_E'
    assert fx['source_resolution']['GT']['FX_GT_example_uses_declared_INT']=='not_determined'
    assert not fx.get('source_conflicts')
    assert not ql.get('source_conflicts') and 'REAL' in ql['operators']['GT']['inputs']
    ports={i:{'side':side,'formal_name':name} for i,side,name in [(0,'in','EN'),(1,'in','a'),(2,'in','b'),(3,'out','GT_E'),(4,'out','ENO')]}
    groups,overrides=call_type_groups('GT_E',ports,fx)
    assert groups==[[1,2]] and 'REAL' in overrides[1] and overrides[3]==['BOOL']


def test_FX_GT_source_difference_remains_a_readable_unverified_finding(monkeypatch):
    from gxw.project_templates import blank_fbd_baseline
    from gxw.object_model import generate_object_project,read_project_context,source_checks
    from gxw.models import GXWFormatError
    _reviewed_operator_fixture(monkeypatch)
    for cpu in ['FX3U', 'FX1']:
        for arguments, rejected in [(['TRUE','FALSE','M0'],False),(['H1','H2','M0'],False),
                                    (['H10000','H20000','M0'],False),
                                    (['K-1','K-2','M0'],True),(['E1.0','E2.0','M0'],True)]:
            baseline=blank_fbd_baseline(cpu);context=read_project_context(baseline)
            model={'program':context.program.logical_name,'networks':[{'nodes':[{
                'id':'comparison','template':'function:@GT-2',
                'arguments':dict(zip(['0','1','2'],arguments))}]}]}
            try:
                saved=generate_object_project(model,baseline=baseline)
            except GXWFormatError as error:
                assert rejected
                findings=error.findings
            else:
                findings=source_checks(read_project_context(saved.data))['findings']
            assert not any(f['code']=='fbd_operator_source_conflict' for f in findings)
            if cpu=='FX3U':
                assert any(f['code']=='fbd_call_type_conflict' for f in findings)==rejected, (cpu, arguments, findings)
            else:
                if any(f.get('severity')=='error' for f in findings):
                    assert any(f['code']=='fbd_type_not_supported' for f in findings)
                else:
                    assert any(f['code']=='fbd_terminal_type_unverified' for f in findings)
                    assert not any(f['code']=='fbd_call_type_conflict' for f in findings)
            if not any(f.get('severity')=='error' for f in findings):
                assert any(f['code']=='intrinsic_operator_semantics_unverified' for f in findings)


def test_FX1_GT_and_GT_E_keep_missing_source_domains(monkeypatch):
    from plc.fbd_semantics import language_rules,call_type_groups
    _reviewed_operator_fixture(monkeypatch)
    ports={0:{'side':'in'},1:{'side':'in'},2:{'side':'out'}}
    rules=language_rules('FBD.OPERATORS','FX1')
    for name in ['GT','GT_E']:
        assert call_type_groups(name,ports,rules)==([], {})


def test_GT_E_source_checks_keep_own_cpu_and_type_boundaries(monkeypatch):
    from gxw.project_templates import blank_fbd_baseline
    from gxw.object_model import generate_object_project,read_project_context,source_checks
    from gxw.models import GXWFormatError
    _reviewed_operator_fixture(monkeypatch)
    for cpu in ['FX3U','FX1']:
        for domain,arguments in [('WORD',['H1','H2']),('INT',['K-1','K-2']),('REAL',['E1.0','E2.0'])]:
            baseline=blank_fbd_baseline(cpu);context=read_project_context(baseline)
            model={'program':context.program.logical_name,'networks':[{'nodes':[{
                'id':'comparison','template':'function:GT_E-2','arguments':{'EN':'TRUE',
                    'in._IN[1]':arguments[0],'in._IN[2]':arguments[1],'GT_E':'M0','ENO':'M1'}}]}]}
            try:
                saved=generate_object_project(model,baseline=baseline)
            except GXWFormatError as error:
                assert cpu=='FX1' and domain=='REAL'
                assert any(f['code']=='fbd_type_not_supported' for f in error.findings)
            else:
                findings=source_checks(read_project_context(saved.data))['findings']
                assert not any(f.get('severity')=='error' for f in findings)
                if domain!='REAL':
                    assert any(f['code']=='fbd_generic_type_coverage' for f in findings)


def test_timer_profiles_keep_fixed_FX_units_and_conditional_QL_units():
    from plc.fbd_semantics import language_rules,parse_literal,check_timer_resolution
    for cpu in ['Q03UDV','L02']:
        rules=language_rules('FBD.TIMERS',cpu)
        assert rules['resolution_ms']=={}
        issue=check_timer_resolution('TON',parse_literal('T#50ms',cpu),rules)
        assert issue['severity']=='unverified' and issue['requires']=='project_timer_limit_setting'
        for name,text in [('TON','T#3276701ms'),('TON_HIGH','T#327671ms'),('TON','T#-1ms')]:
            issue=check_timer_resolution(name,parse_literal(text,cpu),rules)
            assert issue['code']=='fbd_timer_preset_range' and issue['severity']=='error'
        profile=rules['shared_time_units_and_profile_selection']
        assert profile['high_speed']['time_unit_us_by_CPU_group']['universal_Q_and_L']['minimum']==10
    fx=language_rules('FBD.TIMERS','FX3U')
    assert check_timer_resolution('TON',parse_literal('T#50ms','FX3U'),fx)['code']=='fbd_timer_resolution'
    assert check_timer_resolution('TON_10',parse_literal('T#50ms','FX3U'),fx) is None
    for cpu in ['FX3S','FX1S']:
        rules=language_rules('FBD.TIMERS',cpu)
        assert rules['resolution_ms']=={'TON':100,'TON_E':100}
        assert check_timer_resolution('TON_10',{'text':'PT'},rules)['code']=='fbd_timer_form_unavailable'
    for cpu in ['FX0','FX0S','FX0N']:
        assert check_timer_resolution('TON',{'text':'PT'},language_rules('FBD.TIMERS',cpu))['code']=='fbd_timer_form_unavailable'
    issue=check_timer_resolution('TON',{'text':'PT'},language_rules('FBD.TIMERS','FX1'))
    assert issue['severity']=='unverified' and issue['requires']=='source_timer_profile'


def test_bound_QL_TON_graph_has_no_language_load_error_and_keeps_precision_unknown():
    from gxw.project_templates import blank_fbd_baseline
    from gxw.object_model import generate_object_project,read_project_context,source_checks
    baseline=blank_fbd_baseline('Q03UDV');context=read_project_context(baseline)
    model={'program':context.program.logical_name,'networks':[{'calls':[{
        'id':'timer','type':'TON','instance':'Timer1','arguments':{'IN':'TRUE','PT':'T#50ms','Q':'M0'}}]}]}
    saved=generate_object_project(model,baseline=baseline)
    findings=source_checks(read_project_context(saved.data))['findings']
    assert not any(f['code']=='fbd_rule_resource' for f in findings)
    assert not any(f.get('severity')=='error' for f in findings)
    assert any(f['code']=='fbd_timer_resolution_coverage' and f.get('requires')=='project_timer_limit_setting' for f in findings)

"""Revision, interruption and stage-save contracts of a persistent native owner.

Page fixtures exercise the real Core validators without controlling a desktop.
"""
from copy import deepcopy
from dataclasses import replace

import pytest

from application.live_engineering import LiveEngineeringSession, normalize_live_plan, verified_live_data
from gxworks2.project_profiles import NATIVE_PROJECT_PROFILES
from tests.test_gxworks2_live_boundaries import _live_pages_wrapper


def authorize_offline_live_pages(identity, command):
    """Fixture scope only: this authorizes no native capability evidence."""
    return dict(status='experimental_allowed', reason='offline_page_fixture', scope='isolated_test_fixture')


def session_pages(tmp_path, *, operation_authorizer=authorize_offline_live_pages):
    raw, pages, live = _live_pages_wrapper(tmp_path)
    session = LiveEngineeringSession(pages, 'FX3U', raw, 'a' * 32, 'project1', 'version1', live_driver=live,
                                     operation_authorizer=operation_authorizer)
    # The real page wrapper reopens the target and confirms its visible state;
    # this fixture records that completion without needing a native window.
    return raw, pages, live, session


@pytest.mark.parametrize('profile', NATIVE_PROJECT_PROFILES,
                         ids=lambda profile: profile.cpu)
def test_live_plan_selects_each_native_menu_without_granting_operation_evidence(profile):
    from plc.target_support import TargetSupportRegistry
    registry = TargetSupportRegistry()
    plan = dict(schema_version=1, mode='live', commands=[dict(operation='save')])
    models = {profile.cpu, *profile.cpu.removesuffix('(RemoteI/O)').split('/')}
    for model in models:
        if not profile.program_supported:
            with pytest.raises(ValueError, match='parameter-only'):
                normalize_live_plan(plan, plc_model=model)
            continue
        frozen = normalize_live_plan(plan, plc_model=model)
        assert frozen['commands'] == plan['commands'] and 'operation_capabilities' not in frozen
        assert all(row['status'] == 'unverified' for row in registry.live_capabilities(
            model, software_version='1.635.0.1', native_ui_language='en').values())
    assert plan == dict(schema_version=1, mode='live', commands=[dict(operation='save')])


@pytest.mark.parametrize('model', ['FX5U', 'FX5UC', 'Q', 'L', 'FX', 'Q03UDV-other', ''])
def test_live_plan_rejects_unknown_and_other_ide_targets(model):
    with pytest.raises(ValueError, match='known GX Works2'):
        normalize_live_plan(dict(schema_version=1, mode='live', commands=[]), plc_model=model)


def test_live_syntax_has_no_implicit_fx3u_device_limit():
    from agent_runtime.plc_tools import _validate_engineering_input
    from agent_runtime.runtime import build_default_tool_runtime
    command = dict(operation='configure_project', source_name='Project',
                   options=dict(system_variable_ranges=dict(D=[10000, 10100])))
    plan = dict(schema_version=1, mode='live', commands=[command])
    assert normalize_live_plan(plan)['commands'] == [command]
    schema = build_default_tool_runtime().registry._tools['execute_native_engineering'].parameters
    _validate_engineering_input('execute_native_engineering', schema, dict(plan=plan))
    with pytest.raises(ValueError, match='D7999'):
        normalize_live_plan(plan, plc_model='FX3UC')


@pytest.mark.parametrize('ranges', [dict(D=[20, 10]), dict(D=[0, True]), dict(ZZ=[0, 1])])
def test_live_syntax_rejects_malformed_allocations_without_a_selected_cpu(ranges):
    plan = dict(schema_version=1, mode='live', commands=[dict(operation='configure_project',
        source_name='Project', options=dict(system_variable_ranges=ranges))])
    with pytest.raises(ValueError):
        normalize_live_plan(plan)


def test_live_native_evidence_never_falls_back_between_cpu_version_language_or_operation():
    from plc.target_support import TargetEvidence, TargetSupportRegistry
    evidence = TargetEvidence('Q03UDV', 'compile', 'verified', 'owned-native-receipt',
        '1.635.0.1', 'live_native_session', 'ja')
    registry = TargetSupportRegistry([evidence])
    matched = registry.live_capabilities('Q03UDV', software_version='1.635.0.1', native_ui_language='ja')
    assert matched['compile']['status'] == 'verified'
    assert matched['save']['status'] == 'unverified'
    for cpu, version, language in [('Q04UDV', '1.635.0.1', 'ja'), ('Q03UDV', '1.635.0.2', 'ja'),
                                  ('Q03UDV', '1.635.0.1', 'en'), ('Q03UDV', '1.635.0.1', 'zh-CN')]:
        assert registry.live_capabilities(cpu, software_version=version,
            native_ui_language=language)['compile']['status'] == 'unverified'
    wrong_scope = TargetSupportRegistry([replace(evidence, scope='another-project')])
    assert wrong_scope.live_capabilities('Q03UDV', software_version='1.635.0.1',
        native_ui_language='ja')['compile']['status'] == 'unverified'
    with pytest.raises(ValueError, match='exact native UI language'):
        TargetSupportRegistry([replace(evidence, native_ui_language=None)])


_RECORDED_FX3U_ZH_LIVE_STEPS = {
    'edit_global_labels': 'continuous-mcp-6/103',
    'edit_pou_labels': 'continuous-mcp-6/105',
    'delete_label': '066-reconcile-observed-cursor-row-delete',
    'create_st_fb': 'continuous-mcp-6/104',
    'edit_st_fb': 'continuous-mcp-6/109',
    'create_st_program': '035-active-st-program-create',
    'edit_st_program': 'continuous-mcp-6/110',
    'configure_project': '043-active-allocation-ranges',
    'create_fbd_call': 'continuous-mcp-6/106',
    'edit_fbd_graph': 'continuous-mcp-6/107',
    'compile': 'continuous-mcp-6/108',
    'show_assignments': 'continuous-mcp-6/120',
    'save': 'continuous-mcp-6/120',
    'reopen': 'continuous-mcp-6/124',
}

_RECORDED_SAMPLED_LIVE_CELLS = {
    ('FX3S', 'en'): 's18-fx3s-en-mcp-02',
    ('Q03UDV', 'en'): 's18-q03udv-en-mcp-01',
    ('L02/L02-P', 'en'): 's18-l02-en-mcp-03',
    ('Q00U', 'en'): 's18-q00u-en-mcp-01',
    ('Q06UDEH', 'en'): 's18-q06udeh-en-mcp-01',
    ('FX3G/FX3GC', 'en'): 's18-fx3g-en-mcp-01',
    ('FX3U/FX3UC', 'en'): 's18-fx3u-en-mcp-01',
    ('L06/L06-P', 'en'): 's18-l06-en-mcp-01',
}
_RECORDED_SAMPLED_OPERATIONS = {
    'create_global_labels', 'edit_global_labels', 'edit_pou_labels',
    'create_st_fb', 'edit_st_fb', 'create_st_program', 'edit_st_program',
    'configure_project', 'create_fbd_call', 'edit_fbd_graph', 'compile',
    'show_assignments', 'save', 'reopen',
}


@pytest.mark.parametrize('profile', NATIVE_PROJECT_PROFILES, ids=lambda profile: profile.cpu)
@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
def test_default_live_evidence_retains_only_the_recorded_exact_menu_and_language(profile, language):
    from plc.target_support import DEFAULT_TARGET_SUPPORT, LIVE_OPERATIONS
    result = DEFAULT_TARGET_SUPPORT.live_capabilities(profile.cpu,
        software_version='1.635.0.1', native_ui_language=language)
    assert set(result) == set(LIVE_OPERATIONS)
    recorded = (set(_RECORDED_FX3U_ZH_LIVE_STEPS)
                if profile.cpu == 'FX3U/FX3UC' and language == 'zh-CN' else set())
    if (profile.cpu, language) in _RECORDED_SAMPLED_LIVE_CELLS:
        recorded |= _RECORDED_SAMPLED_OPERATIONS
    assert {operation for operation, row in result.items() if row['status'] == 'verified'} == recorded
    for operation, row in result.items():
        if operation not in recorded:
            assert row['status'] == ('unverified' if profile.program_supported else 'unsupported')
            assert row['evidence'] == []
    # A live compile receipt does not grant a different Core or runtime scope.
    assert all(row['status'] != 'verified' for row in DEFAULT_TARGET_SUPPORT.target(profile.cpu,
        software_version='1.635.0.1', native_ui_language=language,
        scope='live_native_session')['operations'].values())


@pytest.mark.parametrize('cpu,language', _RECORDED_SAMPLED_LIVE_CELLS)
def test_sampled_default_live_evidence_names_completed_cells_and_keeps_delete_unverified(cpu,language):
    from plc.target_support import DEFAULT_TARGET_SUPPORT
    source = _RECORDED_SAMPLED_LIVE_CELLS[cpu,language]
    actual = DEFAULT_TARGET_SUPPORT.live_capabilities(cpu,
        software_version='1.635.0.1',native_ui_language=language)
    for operation in _RECORDED_SAMPLED_OPERATIONS:
        assert actual[operation] == dict(status='verified',evidence=[dict(
            cpu=cpu,operation=operation,status='verified',
            source='gxw-live-sampled18-20261019/'+source+'/cell-audit#'+operation,
            software_version='1.635.0.1',scope='live_native_session',native_ui_language=language)])
    assert actual['delete_label']['status'] == 'unverified'


@pytest.mark.parametrize('cpu,language', _RECORDED_SAMPLED_LIVE_CELLS)
def test_registered_sampled_cell_has_a_complete_independent_native_audit(cpu,language):
    import json
    from tools.offline_inputs import private_path
    source = _RECORDED_SAMPLED_LIVE_CELLS[cpu,language]
    path = private_path('research/experiments/native-live-multitarget-20261018/' + source + '/cell-audit.json')
    if not path.is_file():
        pytest.skip('private independent native cell audit unavailable')
    actual = json.loads(path.read_text(encoding='utf-8'))
    assert actual['passed'] is True and actual['complete'] is True
    assert (actual['cpu_menu'],actual['native_ui_language'],actual['software_version']) == (cpu,language,'1.635.0.1')
    assert actual['first_compile_run_id'] != actual['second_compile_run_id']
    assert actual['original_identity']['process_id'] != actual['independent_identity']['process_id']
    assert set(actual['checks']) == {
        'globals_create_edit','local_VAR_create_edit','ST_program_create_edit_task','ST_FB_create_edit',
        'FBD_instance_ports_nodes_wires','model_allocation_configuration','first_native_compile_current_addresses',
        'incremental_stale_then_recompile_current_addresses','stage_Save_Core_match','same_process_reopen',
        'independent_process_relocation'}
    assert all(row['status']=='passed' for row in actual['checks'].values())
    assert actual['real_PLC_execution']=='not_run'


@pytest.mark.parametrize('cpu', ['FX3U/FX3UC', 'FX3U', 'FX3UC'])
def test_default_fx3u_menu_evidence_names_completed_author_owned_receipts(cpu):
    from plc.target_support import DEFAULT_TARGET_SUPPORT
    result = DEFAULT_TARGET_SUPPORT.live_capabilities(cpu,
        software_version='1.635.0.1', native_ui_language='zh-CN')
    assert len(_RECORDED_FX3U_ZH_LIVE_STEPS) == 14
    for operation, step in _RECORDED_FX3U_ZH_LIVE_STEPS.items():
        assert result[operation] == dict(status='verified', evidence=[dict(
            cpu='FX3U/FX3UC', operation=operation, status='verified',
            source='gxw-live-native-engineering-20261018/' + step,
            software_version='1.635.0.1', scope='live_native_session', native_ui_language='zh-CN')])
    assert result['create_global_labels']['status'] == 'unverified'
    result['compile']['evidence'][0]['source'] = 'caller_mutated'
    assert DEFAULT_TARGET_SUPPORT.live_capabilities(cpu, software_version='1.635.0.1',
        native_ui_language='zh-CN')['compile']['evidence'][0]['source'].endswith('/continuous-mcp-6/108')


@pytest.mark.parametrize('version,scope', [('1.635.0.2', 'live_native_session'),
    ('1.634.0.1', 'live_native_session'), (None, 'live_native_session'),
    ('1.635.0.1', 'another_session_scope')])
@pytest.mark.parametrize('cpu,language', [('FX3U/FX3UC','zh-CN'), *_RECORDED_SAMPLED_LIVE_CELLS])
def test_default_live_evidence_cannot_be_borrowed_for_another_version_or_scope(version, scope,cpu,language):
    from plc.target_support import DEFAULT_TARGET_SUPPORT
    result = DEFAULT_TARGET_SUPPORT.live_capabilities(cpu,
        software_version=version, native_ui_language=language, scope=scope)
    assert all(row['status'] == 'unverified' and not row['evidence'] for row in result.values())


@pytest.mark.parametrize('language,codepage', [('zh-CN', 936), ('en', 1252), ('ja', 932)])
def test_live_session_publishes_real_language_codepage_and_operation_capabilities(tmp_path, language, codepage):
    from types import SimpleNamespace
    from application.live_engineering import live_engineering_summary
    from plc.target_support import DEFAULT_TARGET_SUPPORT
    raw, pages, _, _ = session_pages(tmp_path)
    pages.context['identity'].update(native_ui_language=language, project_codepage=codepage)
    capabilities = dict(compile=dict(status='verified', evidence=['not_registry_evidence']))
    live = SimpleNamespace(inspect=pages.inspect, capabilities=lambda: capabilities)
    session = LiveEngineeringSession(pages, 'FX3U', raw, 'a' * 32, 'project1', 'version1', live_driver=live)
    public = live_engineering_summary(session.status())
    assert public['identity']['native_ui_language'] == public['native_ui_language'] == language
    assert public['identity']['project_codepage'] == public['project_codepage'] == codepage
    expected = DEFAULT_TARGET_SUPPORT.live_capabilities(pages.inspect()['cpu_menu'],
        software_version=pages.inspect()['software_version'], native_ui_language=language)
    assert public['operation_capabilities'] == expected
    assert not {'project_path', 'window_handle', 'process_started', 'executable'} & public['identity'].keys()
    public['operation_capabilities']['compile']['status'] = 'caller_changed'
    assert session.status()['operation_capabilities'] == expected
    assert capabilities['compile']['status'] == 'verified'
    session._check_identity()
    pages.context['identity']['native_ui_language'] = 'en' if language != 'en' else 'ja'
    with pytest.raises(RuntimeError, match='language'):
        session._check_identity()
    pages.context['identity']['native_ui_language'] = language
    pages.context['identity']['project_codepage'] = 932 if codepage != 932 else 1252
    with pytest.raises(RuntimeError, match='codepage'):
        session._check_identity()


def isolate_live_registry(monkeypatch, session, *operations, **changed):
    """Synthetic test records never enter the product's default evidence."""
    from gxw.native_locale import native_language
    from plc import target_support
    rows = [target_support.TargetEvidence(**{
        **dict(cpu=session._identity['cpu_menu'], operation=operation, status='verified',
               source='synthetic_controller_gate_test', software_version=session._identity['software_version'],
               scope='live_native_session', native_ui_language=native_language(session._identity)),
        **changed}) for operation in operations]
    registry = target_support.TargetSupportRegistry(rows)
    monkeypatch.setattr(target_support, 'DEFAULT_TARGET_SUPPORT', registry)
    return registry


def forbid_live_dispatch(monkeypatch, pages, live):
    def forbidden(*args, **kwargs):
        pytest.fail('Capability rejection must precede page reads, focus and native dispatch')
    for name in ('finish_completion', 'read_context', 'focus_target', 'apply', 'stage_save'):
        monkeypatch.setattr(live, name, forbidden, raising=False)
    for name in ('compile_current', 'read_assignments', 'reopen'):
        monkeypatch.setattr(pages, name, forbidden, raising=False)


@pytest.mark.parametrize('operation', [
    'create_global_labels', 'edit_global_labels', 'edit_pou_labels', 'delete_label',
    'create_st_fb', 'edit_st_fb', 'create_st_program', 'edit_st_program', 'configure_project',
    'create_fbd_call', 'edit_fbd_graph', 'compile', 'show_assignments', 'save', 'reopen'])
def test_default_session_requires_verified_native_capability_before_any_page_action(
        tmp_path, monkeypatch, operation):
    raw, pages, live, session = session_pages(tmp_path, operation_authorizer=None)
    isolate_live_registry(monkeypatch, session)
    # Graph requests are split into individual operations before preflight.
    command = (dict(operation=operation, actions=[dict(action='move_node')])
               if operation == 'edit_fbd_graph' else dict(operation=operation))
    forbid_live_dispatch(monkeypatch, pages, live)
    result = session.run([command])
    assert result['passed'] is False and result['status'] == 'failed'
    assert result['revision'] == result['saved_revision'] == 0 and session._inflight is None
    assert result['pending_commands'] == 0 and result['executed_steps'] == result['steps'] == []
    rejected = result['capability_authorization']
    assert rejected['status'] == 'rejected'
    assert rejected['operations'][0]['operation'] == operation
    assert rejected['operations'][0]['capability_status'] == 'unverified'
    assert pages.saves == pages.reopens == 0 and pages.path.read_bytes() == raw


def test_whole_sequence_is_authorized_before_the_first_verified_edit(tmp_path, monkeypatch):
    _, pages, live, session = session_pages(tmp_path, operation_authorizer=None)
    isolate_live_registry(monkeypatch, session, 'edit_global_labels')
    forbid_live_dispatch(monkeypatch, pages, live)
    result = session.run([label_edit(pages), dict(operation='save')])
    assert result['passed'] is False and result['revision'] == 0
    assert [row['status'] for row in result['capability_authorization']['operations']] == ['verified', 'rejected']
    assert result['capability_authorization']['operations'][-1]['operation'] == 'save'
    assert result['steps'] == [] and pages.calls == [] and session._inflight is None


@pytest.mark.parametrize('advertises_verified', [False, True])
def test_driver_capability_claim_or_missing_method_cannot_replace_registry_evidence(
        tmp_path, monkeypatch, advertises_verified):
    from types import SimpleNamespace
    raw, pages, _, _ = session_pages(tmp_path)
    live = SimpleNamespace(inspect=pages.inspect)
    if advertises_verified:
        live.capabilities = lambda: dict(edit_global_labels=dict(status='verified', evidence=['driver_claim']))
    session = LiveEngineeringSession(pages, 'FX3U', raw, 'a' * 32, 'project1', 'version1', live_driver=live)
    isolate_live_registry(monkeypatch, session)
    result = session.run([label_edit(pages)])
    assert result['passed'] is False and result['revision'] == 0 and session._inflight is None
    assert result['operation_capabilities']['edit_global_labels']['status'] == 'unverified'
    assert result['error']['type'] == 'LiveCapabilityError'


@pytest.mark.parametrize('changed', [dict(cpu='Q03UDV'), dict(software_version='1.635.0.2'),
    dict(native_ui_language='en'), dict(scope='another_scope'), dict(operation='save')])
def test_controller_does_not_borrow_evidence_from_another_native_tuple(tmp_path, monkeypatch, changed):
    _, pages, live, session = session_pages(tmp_path, operation_authorizer=None)
    isolate_live_registry(monkeypatch, session, 'edit_global_labels', **changed)
    forbid_live_dispatch(monkeypatch, pages, live)
    result = session.run([label_edit(pages)])
    assert result['passed'] is False and result['revision'] == 0
    assert result['capability_authorization']['operations'][0]['status'] == 'rejected'


@pytest.mark.parametrize('accepted', [True, None, dict(status='verified', reason='test', scope='fixture'),
    dict(status='experimental_allowed', reason='test'),
    dict(status='experimental_allowed', reason='', scope='fixture'),
    dict(status='experimental_allowed', reason='test', scope=' '),
    dict(status='experimental_allowed', reason='test', scope='fixture', bypass=True),
    dict(status='experimental_allowed', reason='test', scope='x' * 1025),
    dict(status='experimental_allowed', reason='test', scope='fixture\0foreign')])
def test_internal_acceptance_policy_requires_explicit_bounded_experimental_scope(tmp_path, monkeypatch, accepted):
    _, pages, live, session = session_pages(tmp_path, operation_authorizer=lambda *_: accepted)
    isolate_live_registry(monkeypatch, session)
    forbid_live_dispatch(monkeypatch, pages, live)
    result = session.run([label_edit(pages)])
    assert result['passed'] is False and session._inflight is None
    assert result['capability_authorization']['status'] == 'rejected'
    assert result['error']['capability_authorization']['reason'] == 'invalid_internal_acceptance_authorization'


def test_internal_acceptance_policy_failure_is_a_recorded_denial(tmp_path, monkeypatch):
    def broken_policy(identity, command):
        raise RuntimeError('Private policy detail that must not grant any operation')
    _, pages, live, session = session_pages(tmp_path, operation_authorizer=broken_policy)
    isolate_live_registry(monkeypatch, session)
    forbid_live_dispatch(monkeypatch, pages, live)
    result = session.run([label_edit(pages)])
    assert result['passed'] is False and result['capability_authorization']['status'] == 'rejected'
    assert result['error']['capability_authorization']['reason'] == 'internal_acceptance_authorizer_failed'
    assert 'Private policy detail' not in result['error']['message']


def test_per_step_authorization_precedes_focus_and_inflight_dispatch(tmp_path, monkeypatch):
    checks = []
    def expires_after_preflight(identity, command):
        checks.append(command['operation'])
        return authorize_offline_live_pages(identity, command) if len(checks) == 1 else False
    _, pages, live, session = session_pages(tmp_path, operation_authorizer=expires_after_preflight)
    isolate_live_registry(monkeypatch, session)
    def forbidden(*args, **kwargs):
        pytest.fail('The per-step gate must run before focus or dispatch')
    monkeypatch.setattr(live, 'focus_target', forbidden)
    monkeypatch.setattr(live, 'apply', forbidden)
    result = session.run([label_edit(pages)])
    assert checks == ['edit_global_labels', 'edit_global_labels']
    assert result['passed'] is False and result['revision'] == 0 and session._inflight is None
    assert result['steps'][0]['status'] == 'failed'
    assert result['steps'][0]['capability_authorization']['status'] == 'rejected'


def test_verified_capability_is_rechecked_after_each_completed_edit(tmp_path, monkeypatch):
    from plc import target_support
    _, pages, live, session = session_pages(tmp_path, operation_authorizer=None)
    isolate_live_registry(monkeypatch, session, 'edit_global_labels')
    applied, focused = [], []
    apply, focus = live.apply, live.focus_target
    def revoke_after_edit(command, prepared):
        result = apply(command, prepared)
        applied.append(deepcopy(command))
        monkeypatch.setattr(target_support, 'DEFAULT_TARGET_SUPPORT', target_support.TargetSupportRegistry())
        return result
    def focus_once(command, before):
        focused.append(deepcopy(command))
        return focus(command, before)
    monkeypatch.setattr(live, 'apply', revoke_after_edit)
    monkeypatch.setattr(live, 'focus_target', focus_once)
    result = session.run([label_edit(pages), label_edit(pages, 'SecondFlag')])
    assert result['passed'] is False and result['revision'] == 1 and result['pending_commands'] == 1
    assert len(applied) == len(focused) == 1 and session._inflight is None
    assert [row['status'] for row in result['steps']] == ['completed', 'failed']
    assert result['steps'][0]['capability_authorization']['status'] == 'verified'
    assert result['steps'][1]['capability_authorization']['status'] == 'rejected'


def test_experimental_trial_is_visible_and_never_registered_as_verified(tmp_path, monkeypatch):
    from application.live_engineering import live_engineering_summary
    def scoped_policy(identity, command):
        identity['cpu_menu'] = 'Q03UDV'
        command['declarations'][0]['name'] = 'PolicyMustNotChangeThis'
        return dict(status='experimental_allowed', reason='private_isolated_acceptance',
                    scope=r'D:\private\acceptance')
    _, pages, _, session = session_pages(tmp_path, operation_authorizer=scoped_policy)
    isolate_live_registry(monkeypatch, session)
    command = label_edit(pages)
    result = session.run([command])
    assert result['passed'] is True and result['revision'] == 1
    assert command['declarations'][0]['name'] == 'LiveFlag'
    assert result['identity']['cpu_menu'] == 'FX3U/FX3UC'
    assert result['capability_authorization']['status'] == 'experimental_allowed'
    assert result['steps'][0]['capability_authorization']['scope'] == r'D:\private\acceptance'
    assert result['steps'][0]['capability_authorization']['status'] == 'experimental_allowed'
    assert all(row['status'] == 'unverified' for row in result['operation_capabilities'].values())
    public = live_engineering_summary(result)
    assert public['capability_authorization']['status'] == 'experimental_allowed'
    assert public['steps'][0]['capability_authorization']['scope'] == '[private path]'
    assert 'LiveFlag' in str(result['current_context']) and 'PolicyMustNotChangeThis' not in str(result['current_context'])


def test_identity_is_checked_before_a_private_trial_is_authorized(tmp_path, monkeypatch):
    checks = []
    def policy(identity, command):
        checks.append(command)
        return authorize_offline_live_pages(identity, command)
    _, pages, live, session = session_pages(tmp_path, operation_authorizer=policy)
    isolate_live_registry(monkeypatch, session)
    pages.context['identity']['process_started'] = 'another_process_start'
    forbid_live_dispatch(monkeypatch, pages, live)
    result = session.run([label_edit(pages)])
    assert result['passed'] is False and result['error']['type'] == 'NativeEngineeringDriverError'
    assert checks == [] and result['capability_authorization']['status'] == 'not_checked'


def test_resuming_paused_work_requires_fresh_whole_sequence_authorization(tmp_path, monkeypatch):
    from plc import target_support
    _, pages, live, session = session_pages(tmp_path, operation_authorizer=None)
    isolate_live_registry(monkeypatch, session, 'edit_global_labels')
    first = session.run([label_edit(pages), label_edit(pages, 'SecondFlag')], dict(step_mode=True))
    assert first['passed'] is True and first['pending_commands'] == 1 and first['revision'] == 1
    monkeypatch.setattr(target_support, 'DEFAULT_TARGET_SUPPORT', target_support.TargetSupportRegistry())
    forbid_live_dispatch(monkeypatch, pages, live)
    resumed = session.run([], dict(expected_revision=1, resume_pending=True))
    assert resumed['passed'] is False and resumed['revision'] == 1 and resumed['pending_commands'] == 1
    assert resumed['executed_steps'] == [] and session._inflight is None


def test_unverified_session_can_observe_an_empty_call_without_authorizing_operations(tmp_path, monkeypatch):
    _, _, _, session = session_pages(tmp_path, operation_authorizer=None)
    isolate_live_registry(monkeypatch, session)
    result = session.run([])
    assert result['passed'] is True and result['revision'] == 0
    assert result['capability_authorization'] == dict(status='not_required', operations=[])
    assert all(row['status'] == 'unverified' for row in result['operation_capabilities'].values())


@pytest.mark.parametrize('metadata', [dict(native_ui_language='en'), dict(project_codepage=932),
    dict(native_ui_language='zh', project_codepage=936), dict(native_ui_language='en', project_codepage=True),
    dict(native_ui_language='ja', project_codepage=99999), dict(native_ui_language='ja', project_codepage=999)])
def test_live_identity_refuses_partial_or_unknown_language_and_codepage(tmp_path, metadata):
    from application.native_engineering import _identity
    _, pages, _, _ = session_pages(tmp_path)
    with pytest.raises(ValueError, match='language|codepage'):
        _identity({**pages.inspect(), **metadata}, 'FX3U')


@pytest.mark.parametrize('selected', ['Q03UDV', 'L02', 'FX3G'])
def test_live_baseline_uses_selected_cpu_instead_of_a_default_fx_target(tmp_path, selected):
    from application.native_engineering import validate_native_baseline
    raw, _, _, _ = session_pages(tmp_path)
    assert normalize_live_plan(dict(schema_version=1, mode='live', commands=[]), plc_model=selected)
    with pytest.raises(ValueError, match='CPU differs'):
        validate_native_baseline(raw, plc_model=selected)


@pytest.mark.parametrize('profile', [row for row in NATIVE_PROJECT_PROFILES if row.program_supported],
                         ids=lambda profile: profile.cpu)
def test_live_baseline_exact_saved_menus_keep_unobserved_and_uninitialized_gui_mirrors_distinct(profile):
    from application.native_engineering import validate_native_baseline
    from gxw.fb_authoring import _context
    from gxw.project_templates import blank_fbd_baseline
    raw = blank_fbd_baseline(profile.cpu)
    if profile.cpu == 'FX3U/FX3UC':
        # The public blank template has no initialized saved GUI mirror. Its
        # known FX3U layout must not inherit another CPU's unobserved exception.
        assert 'Gppw2.gpj' not in _context(raw)[1]
        with pytest.raises(ValueError, match='saved GUI allocation configuration'):
            validate_native_baseline(raw, plc_model=profile.cpu, live=True)
        return
    result = validate_native_baseline(raw, plc_model=profile.cpu, live=True)
    assert result['cpu'] == profile.cpu or result['gui_configuration']['cpu_menu'] == profile.cpu
    assert result['gui_configuration']['status'] == 'not_verified'
    assert result['gui_configuration']['effective_ranges'] == {}
    with pytest.raises(ValueError, match='saved GUI allocation configuration'):
        validate_native_baseline(raw, plc_model=profile.cpu)
    assert raw == blank_fbd_baseline(profile.cpu)


def test_live_baseline_verified_fx3u_source_remains_available_in_both_modes():
    from application.native_engineering import validate_native_baseline
    from tests.test_gxworks2_live_boundaries import _visible_cross_reference_witness
    raw = _visible_cross_reference_witness()
    live = validate_native_baseline(raw, plc_model='FX3UC', live=True)
    assert live['gui_configuration']['status'] == 'matched'
    assert validate_native_baseline(raw, plc_model='FX3UC') == live


@pytest.mark.parametrize('selected,actual', [('Q03UDV', 'Q04UDV'), ('L02', 'Q03UDV'), ('FX3G', 'FX3U')])
def test_live_baseline_observation_still_rejects_a_different_actual_saved_cpu(selected, actual):
    from application.native_engineering import validate_native_baseline
    from gxw.project_templates import blank_fbd_baseline
    with pytest.raises(ValueError, match='CPU differs'):
        validate_native_baseline(blank_fbd_baseline(actual), plc_model=selected, live=True)


@pytest.mark.parametrize('mode', (False, True))
@pytest.mark.parametrize('damage', ('uninitialized', 'different_range', 'unverified_selector'))
def test_live_baseline_keeps_fx3u_uninitialized_or_inconsistent_gui_mirrors_rejected(mode, damage):
    from application.native_engineering import validate_native_baseline
    from gxw.fb_authoring import _context
    from gxw.object_model import default_baseline
    from gxw.project_metadata import project_gui_configuration, write_gui_system_variables
    from tests.test_gxworks2_live_boundaries import (_replace_visible_logical_stream,
                                                   _visible_cross_reference_witness)
    if damage == 'uninitialized':
        raw = default_baseline()
    else:
        raw = _visible_cross_reference_witness()
        gui = _context(raw)[1]['Gppw2.gpj']
        changed = (write_gui_system_variables(gui, {'D': (600, 999)}) if damage == 'different_range' else
            gui.decode('utf-16le').replace('WordDevice\tTYPE_WORD\t0', 'WordDevice\tTYPE_WORD\t7').encode('utf-16le'))
        assert changed != gui
        raw = _replace_visible_logical_stream(raw, 'Gppw2.gpj', changed)
    assert project_gui_configuration(raw)['status'] != 'matched'
    with pytest.raises(ValueError, match='saved GUI allocation configuration'):
        validate_native_baseline(raw, plc_model='FX3U', live=mode)


@pytest.mark.parametrize('mode', (None, 1, 'live'))
def test_live_baseline_observation_mode_is_explicit_boolean(mode):
    from application.native_engineering import validate_native_baseline
    with pytest.raises(ValueError, match='mode must be boolean'):
        validate_native_baseline(b'not parsed before mode validation', plc_model='Q03UDV', live=mode)


@pytest.mark.parametrize('selected', [row.cpu for row in NATIVE_PROJECT_PROFILES if not row.program_supported])
def test_live_baseline_never_promotes_parameter_only_native_entries(selected):
    from application.native_engineering import validate_native_baseline
    with pytest.raises(ValueError, match='parameter-only'):
        validate_native_baseline(b'not parsed before target validation', plc_model=selected, live=True)


@pytest.mark.parametrize('selected', ('Q03UDV', 'L02', 'FX3G'))
def test_shared_native_tool_preflight_passes_live_observation_mode_for_the_selected_saved_cpu(selected):
    from agent_runtime.plc_tools import _request_native_engineering, build_tool_context
    from gxw.project_metadata import project_gui_configuration
    from gxw.project_templates import blank_fbd_baseline
    raw = blank_fbd_baseline(selected)
    context = build_tool_context(dict(id='project1', plc_model=selected),
        version=dict(id='version1', target_mode='fbd', plc_model=selected), native_snapshot=raw)
    result = _request_native_engineering(context, dict(plan=dict(schema_version=1, mode='live', commands=[])))
    assert result['pending_action']['plan']['mode'] == 'live'
    assert result['pending_action']['project_id'] == 'project1'
    assert project_gui_configuration(raw)['status'] == 'not_verified'


def test_native_engineering_rejects_missing_saved_cpu_instead_of_using_the_legacy_default():
    from agent_runtime.plc_tools import (_request_native_engineering, build_tool_context, ToolPreconditionError)
    context = build_tool_context(dict(id='project1'), version=dict(id='version1', target_mode='fbd'),
                                 native_snapshot=b'not-read-before-cpu-confirmation')
    assert context.plc_model == 'FX3U'  # Other legacy tools retain their existing default.
    with pytest.raises(ToolPreconditionError) as caught:
        _request_native_engineering(context, dict(plan=dict(schema_version=1, mode='live', commands=[])))
    assert caught.value.payload()['code'] == 'PLC_MODEL_REQUIRED'


@pytest.mark.parametrize('changed', [('native_ui_language', 'ja'), ('project_codepage', 932)])
def test_live_stage_receipt_cannot_change_the_language_or_project_codepage(changed):
    key, value = changed
    identity = dict(process_id=42, software_version='1.635.0.1', cpu_menu='Q03UDV',
                    native_ui_language='en', project_codepage=1252)
    validation = dict(status='matched')
    result = dict(passed=True, data_status='verified_stage_save', data=b'saved-native-stage', data_revision=2,
        identity=identity, _stage_context=dict(identity={**identity, key: value}),
        stage_saves=[dict(sequence=3, revision=2, validation=validation)],
        executed_steps=[dict(index=3, operation='save', status='completed', revision=2,
                             verification=dict(validation=validation))])
    with pytest.raises(ValueError, match='language or project codepage'):
        verified_live_data(result, b'baseline')


def test_pre_session_failure_message_survives_public_live_projection():
    from application.live_engineering import live_engineering_summary
    result = live_engineering_summary(dict(mode='live', status='error', passed=False,
        message='The selected saved baseline changed before native startup.'))
    assert result['message'] == 'The selected saved baseline changed before native startup.'
    assert result['passed'] is False and 'session_id' not in result


def test_unavailable_assignments_do_not_claim_or_restore_a_native_result_page(tmp_path, monkeypatch):
    raw, pages, live, _ = session_pages(tmp_path)
    calls = []
    monkeypatch.setattr(pages, 'show_assignments_completion', lambda result: calls.append(result), raising=False)
    observed = dict(status='unavailable', revision=0, results=[dict(status='unavailable',
                    reason='acknowledged_current_compile_save_allocations_required')])
    completion = live.show_completion(dict(operation='show_assignments'), {}, observed)
    assert completion == dict(completed=False, status='unavailable',
                              reason='native_assignment_results_unavailable')
    assert calls == [] and pages.saves == pages.reopens == 0 and pages.path.read_bytes() == raw


def test_reopen_completion_restores_last_verified_target_without_reopening_or_saving(tmp_path):
    raw, pages, live, session = session_pages(tmp_path)
    assert session.run([label_edit(pages)])['passed']
    target = deepcopy(live._last_target)
    completion = live.show_completion(dict(operation='reopen'), {})
    assert completion['completed'] is True and completion['page'] == '全局标签表'
    assert completion['reopen'] == 'same_process' and live._last_target == target
    assert pages.saves == pages.reopens == 0 and pages.path.read_bytes() == raw


def label_edit(pages, name='LiveFlag'):
    owner = pages.context['inventory']['globals'][0]
    return dict(operation='edit_global_labels', source_name=owner,
                declarations=[dict(name=name, data_type='BOOL', device='M50')])


def test_edit_and_save_use_one_full_read_at_each_phase_boundary(tmp_path, monkeypatch):
    raw, pages, live, session = session_pages(tmp_path)
    original, reads = live.read_context, []
    def read():
        reads.append(True)
        return original()
    monkeypatch.setattr(live, 'read_context', read)
    edited = session.run([label_edit(pages)])
    assert edited['passed'], edited.get('error')
    assert len(reads) == 2 and pages.saves == 0
    pages.saved_output = current_saved_bytes(raw, pages)
    reads.clear()
    saved = session.run([{'operation': 'save'}], {'expected_revision': 1})
    assert saved['passed'], saved.get('error')
    assert len(reads) == 2 and pages.saves == 1
    assert saved['data'] == pages.path.read_bytes() == pages.saved_output


def test_dependency_scope_preserves_reachable_project_types_without_visiting_unrelated_bodies():
    from hypothesis import given, settings, strategies as st
    from gxw.live_context import live_read_scope
    @settings(max_examples=40, deadline=None, derandomize=True)
    @given(edges=st.sets(st.tuples(st.integers(0, 5), st.integers(0, 5)), max_size=15),
           roots=st.sets(st.integers(0, 5), max_size=3), selector=st.sampled_from(['Main', 'main', 'MAIN']))
    def check(edges, roots, selector):
        owners = ['T' + str(i) for i in range(6)]
        table = lambda kind, owner, types: dict(owner_kind=kind, owner=owner,
            declarations=[dict(data_type=data_type) for data_type in types])
        tables = [table('global', 'Globals', ['BOOL']), table('program', 'Main', ['ARRAY [1..2] OF T0'])]
        tables += [table('fb', owner, [owners[right] for left, right in sorted(edges) if left == i])
                   for i, owner in enumerate(owners)]
        context = dict(inventory=dict(globals=['Globals'], structures=[], pous=[
            dict(owner_kind='program', owner='Main', language='fbd'),
            *[dict(owner_kind='fb', owner=owner, language='st') for owner in owners]]), tables=tables)
        reached = {0} | roots
        while True:
            expanded = reached | {right for left, right in edges if left in reached}
            if expanded == reached:
                break
            reached = expanded
        fresh_global = table('global', 'Globals', [owners[i] for i in sorted(roots)])
        scope = live_read_scope(context, dict(operation='edit_st_program', source_name=selector), [fresh_global])
        assert set(map(tuple, scope['tables'])) == {('global', 'Globals'), ('program', 'Main')} | {
            ('fb', owners[i]) for i in reached}
        assert set(map(tuple, scope['pous'])) == {('program', 'Main')} | {('fb', owners[i]) for i in reached}
        assert scope['configuration'] is False
    check()


def test_target_read_skips_unrelated_pages_and_full_boundary_detects_their_change(tmp_path):
    from gxw.live_context import live_context_state, verify_live_observations
    raw, pages, live, session = session_pages(tmp_path)
    owner = pages.context['inventory']['pous'][0]['owner']
    extra_owner = 'Unrelated'
    extra_table = deepcopy(next(row for row in pages.context['tables'] if row['owner_kind'] == 'program'))
    extra_table['owner'] = extra_table['source_binding']['owner'] = extra_owner
    extra_table['source_binding']['document_title'] = extra_table['source_binding']['document_title'].replace(owner, extra_owner)
    extra_graph = deepcopy(pages.context['graphs'][0])
    extra_graph['owner'] = extra_owner
    pages.context['inventory']['pous'].append(dict(owner=extra_owner, owner_kind='program', language='fbd'))
    pages.context['tables'].append(extra_table)
    pages.context['graphs'].append(extra_graph)
    before = live.read_context()
    pages.calls.clear()
    command = dict(operation='edit_fbd_graph', caller=owner, actions=[])
    scoped = live.read_target_context(before, command)
    assert live_context_state(scoped) == live_context_state(before)
    assert ('read_labels', owner, 'program') in pages.calls
    assert ('read_labels', extra_owner, 'program') not in pages.calls
    assert ('program_page', extra_owner, 'program', 'fbd') not in pages.calls
    assert ('read_configuration',) not in pages.calls
    assert scoped['_observation_scope']['kind'] == 'dependencies'
    wires = pages.context['graphs'][-1]['networks'][0]['wires']
    wires.append(dict(index=len(wires), start=[2, 3], end=[8, 3], selected=False))
    after = live.read_context()
    assert '_observation_scope' not in after
    with pytest.raises(ValueError):
        verify_live_observations(before, after)
    assert pages.saves == 0 and pages.path.read_bytes() == raw


def test_target_read_resolves_saved_source_roles_through_the_current_core_owner(tmp_path):
    from gxworks2.live_driver import LiveEngineeringDriver
    from tests.test_gxw_object_model import project_rows_variant
    raw, pages, original, session = session_pages(tmp_path)
    owner = pages.context['inventory']['pous'][0]['owner']
    alias = 'Localized.程序.pou'
    variant = project_rows_variant(raw, {owner + '.Program.pou': {'szName': alias}})
    pages.path.write_bytes(variant)
    live = LiveEngineeringDriver(pages, variant, 'FX3U', adapter=original.adapter)
    before = live.read_context()
    pages.calls.clear()
    scoped = live.read_target_context(before, dict(operation='edit_fbd_graph', caller=alias, actions=[]))
    assert ('read_labels', owner, 'program') in pages.calls
    assert ('program_page', owner, 'program', 'fbd') in pages.calls
    assert ('program', owner) in scoped['_observation_scope']['pous']
    assert pages.saves == 0 and pages.path.read_bytes() == variant


@pytest.mark.parametrize('active,requested', [(0, 0), (0, 1), (1, 0)])
def test_live_network_focus_preserves_source_and_only_jumps_when_needed(tmp_path, monkeypatch, active, requested):
    from gxw.live_context import live_context_state
    raw, pages, live, session = session_pages(tmp_path)
    graph = pages.context['graphs'][0]
    graph['networks'].append(dict(index=1, nodes=[], wires=[], junctions=[]))
    graph['active_network'] = active
    before = live.read_context()
    jumps = []
    def jump(adapter, owner, kind, network):
        jumps.append((owner, kind, network))
        graph['active_network'] = network
        return deepcopy(graph)
    monkeypatch.setattr(pages, 'focus_graph_network', jump, raising=False)
    command = dict(operation='edit_fbd_graph', caller=graph['owner'],
                   actions=[dict(action='delete_wire', network=requested, wire='b0:w0')])
    after = live.focus_target(command, before)
    assert after['graphs'][0]['active_network'] == requested
    assert jumps == ([] if active == requested else [(graph['owner'], graph['owner_kind'], requested)])
    assert live_context_state(before) == live_context_state(after)
    assert pages.saves == pages.reopens == 0 and pages.path.read_bytes() == raw


@pytest.mark.parametrize('damage', ['range', 'wrong_owner', 'source_changed'])
def test_live_network_focus_refuses_invalid_target_or_changed_source_before_edit(tmp_path, monkeypatch, damage):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    raw, pages, live, session = session_pages(tmp_path)
    graph = pages.context['graphs'][0]
    before = live.read_context()
    jumps = []
    monkeypatch.setattr(pages, 'focus_graph_network', lambda *args: jumps.append(args), raising=False)
    command = dict(operation='edit_fbd_graph', caller=graph['owner'],
                   actions=[dict(action='delete_wire', network=0, wire='b0:w0')])
    if damage == 'range':
        command['actions'][0]['network'] = 1
    elif damage == 'wrong_owner':
        command['caller'] = 'Missing'
    else:
        graph['networks'][0]['wires'].append(dict(index=0, start=[2, 3], end=[8, 3], selected=False))
    with pytest.raises(NativeEngineeringDriverError if damage == 'range' else ValueError):
        live.focus_target(command, before)
    assert jumps == [] and pages.saves == pages.reopens == 0 and pages.path.read_bytes() == raw


def test_live_controller_confirms_focused_source_before_preparing_an_edit(tmp_path, monkeypatch):
    from gxw import live_context
    raw, pages, live, session = session_pages(tmp_path)
    order = []
    original = live_context.prepare_live_command
    monkeypatch.setattr(live, 'focus_target', lambda command, context: order.append('focus') or context)
    def prepare(*args, **kwargs):
        order.append('prepare')
        assert order[-2:] == ['focus', 'prepare']
        return original(*args, **kwargs)
    monkeypatch.setattr(live_context, 'prepare_live_command', prepare)
    result = session.run([label_edit(pages)])
    assert result['passed'], result.get('error')
    assert result['revision'] == 1 and order == ['focus', 'prepare'] and pages.saves == 0


def current_saved_bytes(raw, pages):
    from gxw.fb_authoring import _context
    from gxw.declarations import edit_declarations, serialize_declarations
    from tests.test_gxw_object_model import replay_source_records
    replacements = {}
    for table in pages.context['tables']:
        if table['owner_kind'] != 'global':
            continue
        logical = table['owner'] + '.gh'
        doc = _context(raw)[3].declarations[logical]
        rows = [{key: row[key] for key in ('name', 'data_type', 'device', 'iec_address', 'comment')}
                for row in table['declarations']]
        replacements[logical] = serialize_declarations(edit_declarations(doc, upserts=rows))
    return replay_source_records(raw, replacements)


def test_live_native_writer_keeps_status_pause_and_version_updates_responsive(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    _, pages, live, session = session_pages(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = live.apply
    calls = []
    def apply(command, prepared):
        calls.append(command)
        entered.set()
        assert release.wait(5)
        return original(command, prepared)
    monkeypatch.setattr(live, 'apply', apply)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(session.run, [label_edit(pages), label_edit(pages, 'SecondFlag')])
        try:
            assert entered.wait(5)
            snapshot = pool.submit(session.status).result(timeout=2)
            assert snapshot['status'] == 'running' and snapshot['revision'] == 0
            assert snapshot['pending_commands'] == 2
            pool.submit(session.set_base_version, 'confirmed-version').result(timeout=2)
            assert pool.submit(session.request_pause).result(timeout=2)['status'] == 'pause_requested'
            for operation in (lambda: session.run([]), session.rebind, session.close):
                with pytest.raises(RuntimeError, match='already in progress'):
                    pool.submit(operation).result(timeout=2)
            snapshot['compile']['untrusted'] = True
            assert 'untrusted' not in session.status()['compile']
        finally:
            release.set()
        result = running.result(timeout=5)
    assert result['passed'] and result['status'] == 'paused'
    assert result['revision'] == 1 and result['pending_commands'] == 1 and len(calls) == 1
    assert result['base_version_id'] == 'confirmed-version'
    resumed = session.run([], dict(expected_revision=1, resume_pending=True))
    assert resumed['passed'] and resumed['revision'] == 2 and len(calls) == 2


def test_live_rebind_serializes_recovery_without_blocking_status(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    _, _, live, session = session_pages(tmp_path)
    assert session.run([])['passed']
    original = live.rebind
    entered, release = threading.Event(), threading.Event()
    def rebind(identity):
        entered.set()
        assert release.wait(5)
        return original(identity)
    monkeypatch.setattr(live, 'rebind', rebind)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(session.rebind)
        try:
            assert entered.wait(5)
            assert pool.submit(session.status).result(timeout=2)['revision'] == 0
            with pytest.raises(RuntimeError, match='already in progress'):
                session.close()
        finally:
            release.set()
        assert future.result(timeout=5)['passed']


def test_live_calls_share_unsaved_revision_and_only_explicit_stage_returns_version_bytes(tmp_path):
    raw, pages, live, session = session_pages(tmp_path)
    changed = session.run([label_edit(pages)])
    assert changed['passed'], changed.get('error')
    assert changed['revision'] == 1 and changed['saved_revision'] == changed['version_revision'] == 0
    assert changed['dirty'] and 'data' not in changed and pages.saves == 0
    observed = session.run([], {'expected_revision': 1})
    assert observed['passed'] and observed['revision'] == 1 and pages.saves == 0
    pages.saved_output = current_saved_bytes(raw, pages)
    saved = session.run([{'operation': 'save'}], {'expected_revision': 1})
    assert saved['passed'], saved.get('error')
    assert saved['saved_revision'] == 1 and saved['version_revision'] == 0 and not saved['dirty']
    assert pages.saves == 1 and verified_live_data(saved, raw) == pages.saved_output
    session.set_base_version('version2')
    assert session.status()['version_revision'] == 1
    assert session.status()['version_matches_active_source']
    assert not session.run([], {'expected_revision': 1}).get('data')


def test_live_single_step_and_pause_resume_never_replay_completed_changes(tmp_path):
    _, pages, _, session = session_pages(tmp_path)
    first = session.run([label_edit(pages), label_edit(pages, 'SecondFlag')], {'step_mode': True})
    assert first['passed'] and first['status'] == 'paused' and first['pending_commands'] == 1
    assert first['revision'] == 1 and len(first['executed_steps']) == 1
    with pytest.raises(ValueError, match='explicitly resume'):
        session.run([label_edit(pages)])
    second = session.run([], {'expected_revision': 1, 'resume_pending': True})
    assert second['passed'] and second['revision'] == 2 and second['pending_commands'] == 0
    assert [row['index'] for row in second['steps']] == [1, 2]
    assert sum(row[0] == 'visible' and row[1].endswith('标签表') for row in pages.calls) >= 2
    assert pages.saves == 0


@pytest.mark.parametrize('native_finished', [True, False])
def test_live_edit_timeout_reconciles_actual_state_without_sending_the_edit_again(tmp_path, monkeypatch, native_finished):
    _, pages, live, session = session_pages(tmp_path)
    original = live.apply
    calls = []
    def interrupted(command, prepared):
        calls.append(command)
        if native_finished:
            original(command, prepared)
        raise TimeoutError('native response interrupted')
    monkeypatch.setattr(live, 'apply', interrupted)
    first = session.run([label_edit(pages)])
    assert not first['passed'] and first['status'] == 'unconfirmed'
    with pytest.raises(RuntimeError, match='reconciliation'):
        session.run([label_edit(pages)])
    reconciled = session.rebind()
    assert reconciled['passed'], reconciled.get('error')
    assert len(calls) == 1 and reconciled['replayed'] is False
    assert reconciled['revision'] == int(native_finished)
    assert reconciled['pending_commands'] == int(not native_finished)
    if not native_finished:
        assert reconciled['steps'][-1]['verification']['status'] == 'not_applied'
    assert pages.saves == 0


def test_incomplete_compile_cannot_be_classified_not_applied_from_unchanged_source(tmp_path, monkeypatch):
    _, pages, _, session = session_pages(tmp_path)
    def interrupted(plan):
        raise TimeoutError('compile reply interrupted')
    monkeypatch.setattr(pages, 'compile_current', interrupted, raising=False)
    first = session.run([{'operation': 'compile'}])
    assert not first['passed'] and first['status'] == 'unconfirmed'
    reconciled = session.rebind()
    assert not reconciled['passed'] and reconciled['status'] == 'unconfirmed'
    assert reconciled['pending_commands'] == 1 and reconciled['revision'] == 0
    assert reconciled['replayed'] is False and pages.saves == 0


@pytest.mark.parametrize('interrupted', [False, True])
def test_compile_line_reordering_does_not_advance_revision_or_require_replay(tmp_path, monkeypatch, interrupted):
    _, pages, live, session = session_pages(tmp_path)
    network = pages.context['graphs'][0]['networks'][0]
    network['wires'] = [dict(index=0, start=[5,3], end=[18,3]),
                        dict(index=1, start=[16,3], end=[16,8])]
    # Start at an already verified unsaved graph revision; this case exercises
    # compile/reconciliation, while initial baseline validation has its own tests.
    session._context = live.read_context()
    session.revision = 3
    def compiled(plan):
        network['wires'].reverse()
        for index, wire in enumerate(network['wires']):
            wire['index'] = index
        return dict(command='rebuild_all', completed=True, pid=pages.inspect()['process_id'],
            revision=plan['revision'], compile_run_id='reordered-native-lines',
            output='已完成全部编译。Error: 0, Warning: 1, CheckWarning: 0')
    monkeypatch.setattr(pages, 'compile_current', compiled, raising=False)
    monkeypatch.setattr(live, 'show_completion', lambda *args: dict(completed=True, page='本次编译输出'))
    read = live.read_context
    fail = [interrupted]
    def current():
        if session._inflight and session._inflight.get('observation') and fail[0]:
            fail[0] = False
            raise TimeoutError('compile finished; reply read interrupted')
        return read()
    monkeypatch.setattr(live, 'read_context', current)
    result = session.run([dict(operation='compile')])
    if interrupted:
        assert result['status'] == 'unconfirmed'
        result = session.rebind()
        assert result['replayed'] is False
    assert result['passed'] and result['revision'] == 3, result.get('error')
    assert result['compile']['status'] == 'passed' and result['compile']['revision'] == 3
    assert result['pending_commands'] == 0 and pages.saves == pages.reopens == 0


def test_compile_result_expires_on_the_next_edit_and_errors_do_not_undo_source(tmp_path, monkeypatch):
    _, pages, live, session = session_pages(tmp_path)
    def compiled(plan):
        return dict(command='rebuild_all', completed=True, pid=pages.inspect()['process_id'],
            revision=plan['revision'], compile_run_id='this-real-run',
            output='已完成全部编译。Error: 2, Warning: 0, CheckWarning: 0')
    monkeypatch.setattr(pages, 'compile_current', compiled, raising=False)
    original = live.show_completion
    monkeypatch.setattr(live, 'show_completion', lambda command, prepared, observation=None:
        dict(completed=True, page='本次原生编译输出') if command['operation']=='compile'
        else original(command, prepared, observation))
    result = session.run([label_edit(pages), {'operation': 'compile'}])
    assert result['passed'] and result['revision'] == 1 and result['compile']['status'] == 'failed'
    assert result['compile']['errors'] == 2 and result['compile']['revision'] == 1
    changed = session.run([label_edit(pages, 'SecondFlag')], {'expected_revision': 1})
    assert changed['passed'] and changed['revision'] == 2 and changed['compile']['status'] == 'stale'
    assert pages.saves == 0


def test_stage_save_after_native_return_can_be_recovered_without_another_save(tmp_path, monkeypatch):
    raw, pages, live, session = session_pages(tmp_path)
    assert session.run([label_edit(pages)])['passed']
    pages.saved_output = current_saved_bytes(raw, pages)
    read = live.read_disk_change
    fail = [False]
    save = live.stage_save
    def finished_save(context, **kwargs):
        saved = save(context, **kwargs)
        fail[0] = True
        return saved
    def interrupted_read():
        if fail[0]:
            fail[0] = False
            raise TimeoutError('saved but reply read interrupted')
        return read()
    monkeypatch.setattr(live, 'stage_save', finished_save)
    # The controller reuses the checked page readback; its remaining I/O is
    # the independent final disk probe, which can still lose its response.
    monkeypatch.setattr(live, 'read_disk_change', interrupted_read)
    result = session.run([{'operation': 'save'}], {'expected_revision': 1})
    assert result['status'] == 'unconfirmed' and pages.saves == 1
    recovered = session.rebind()
    assert recovered['passed'], recovered.get('error')
    assert recovered['pending_commands'] == 0 and pages.saves == 1
    assert verified_live_data(recovered, raw) == pages.saved_output


@pytest.mark.parametrize('write_stage', ['driver_readback', 'controller_readback'])
@pytest.mark.parametrize('damage', ['container_metadata', 'unobserved_parameter'])
def test_late_native_save_write_requires_reconciliation_of_latest_bytes_without_replay(
        tmp_path, monkeypatch, write_stage, damage):
    from gxw.fb_authoring import _context
    from tests.test_gxw_object_model import replay_source_records
    raw, pages, live, session = session_pages(tmp_path)
    assert session.run([label_edit(pages)])['passed']
    first = pages.saved_output = current_saved_bytes(raw, pages)
    if damage == 'container_metadata':
        late = bytearray(first)
        late[52:56] = (1).to_bytes(4, 'little')
        late = bytes(late)
    else:
        parameter = bytearray(_context(first)[1]['Param.wpa'])
        parameter[-1] ^= 1
        late = replay_source_records(first, {'Param.wpa': bytes(parameter)})
    read = live.read_context if write_stage == 'driver_readback' else live.read_disk_change
    save, armed, written = live.stage_save, [False], [False]
    def finished_save(context, **kwargs):
        result = save(context, **kwargs)
        armed[0] = True
        return result
    def delayed_write_read():
        if not written[0] and pages.saves == 1 and (write_stage == 'driver_readback' or armed[0]):
            pages.path.write_bytes(late)
            written[0] = True
        return read()
    monkeypatch.setattr(live, 'read_context' if write_stage == 'driver_readback' else 'read_disk_change', delayed_write_read)
    if write_stage == 'controller_readback':
        monkeypatch.setattr(live, 'stage_save', finished_save)
    attempted = session.run([{'operation': 'save'}], {'expected_revision': 1})
    assert not attempted['passed'] and attempted['status'] == 'unconfirmed'
    assert 'data' not in attempted and not attempted['stage_saves'] and pages.saves == 1
    recovered = session.rebind()
    assert pages.saves == 1 and recovered['replayed'] is False and pages.path.read_bytes() == late
    if damage == 'container_metadata':
        assert recovered['passed'] and recovered['pending_commands'] == 0
        assert verified_live_data(recovered, raw) == late and late != first
        assert live.baseline == late and live.read_disk_change() is None
    else:
        assert not recovered['passed'] and recovered['status'] == 'unconfirmed'
        assert recovered['pending_commands'] == 1 and 'data' not in recovered


@pytest.mark.parametrize('lost_reply', ['snapshot', 'post_save_read'])
def test_reconciled_stage_save_keeps_this_build_allocations_for_next_query(tmp_path, monkeypatch, lost_reply):
    raw, pages, live, session = session_pages(tmp_path)
    assert session.run([label_edit(pages)])['passed']
    pages.saved_output = current_saved_bytes(raw, pages)
    monkeypatch.setattr(pages, 'compile_current', lambda plan: dict(command='rebuild_all', completed=True,
        pid=pages.inspect()['process_id'], revision=plan['revision'], compile_run_id='compiled-before-interrupted-save',
        output='已完成全部编译。Error: 0, Warning: 0, CheckWarning: 0'), raising=False)
    original_completion = live.show_completion
    monkeypatch.setattr(live, 'show_completion', lambda command, prepared, observation=None:
        dict(completed=True, page='本次原生结果') if command['operation'] in ('compile', 'show_assignments')
        else original_completion(command, prepared, observation))
    assert session.run([{'operation': 'compile'}], {'expected_revision': 1})['passed']
    if lost_reply == 'snapshot':
        snapshot = pages.snapshot
        def interrupted_snapshot(**kwargs):
            snapshot(**kwargs)
            raise TimeoutError('Native Save wrote bytes before its reply was lost')
        monkeypatch.setattr(pages, 'snapshot', interrupted_snapshot)
    else:
        save, read, fail = live.stage_save, live.read_disk_change, [False]
        def finished_save(context, **kwargs):
            result = save(context, **kwargs)
            fail[0] = True
            return result
        def interrupted_read():
            if fail[0]:
                fail[0] = False
                raise TimeoutError('Readback after the confirmed native Save was interrupted')
            return read()
        monkeypatch.setattr(live, 'stage_save', finished_save)
        monkeypatch.setattr(live, 'read_disk_change', interrupted_read)
    attempted = session.run([{'operation': 'save'}], {'expected_revision': 1})
    assert not attempted['passed'] and attempted['status'] == 'unconfirmed' and pages.saves == 1
    recovered = session.rebind()
    assert recovered['passed'] and recovered['replayed'] is False
    assert verified_live_data(recovered, raw) == pages.saved_output
    contexts = []
    def assignments(queries, *, revision, compilation, allocation_context):
        contexts.append(deepcopy(allocation_context))
        return dict(status='current' if allocation_context else 'unavailable', revision=revision,
                    compile_run_id=compilation['compile_run_id'], rows=[])
    monkeypatch.setattr(pages, 'read_assignments', assignments, raising=False)
    query = dict(operation='show_assignments', queries=[dict(scope='global',
                 source=pages.context['inventory']['globals'][0] + '.gh', name='LiveFlag')])
    queried = session.run([query], {'expected_revision': 1})
    assert queried['passed'] and queried['assignments']['status'] == 'current'
    assert contexts[0]['compile_run_id'] == 'compiled-before-interrupted-save'
    assert contexts[0]['revision'] == 1
    assert pages.saves == 1 and pages.path.read_bytes() == pages.saved_output


def test_live_plan_validates_revision_and_network_before_opening_a_native_owner():
    base = dict(schema_version=1, mode='live', session_action='continue', session_id='a'*32, commands=[])
    with pytest.raises(ValueError, match='expected_revision'):
        normalize_live_plan(base, plc_model='FX3U')
    assert normalize_live_plan({**base, 'controls': {'expected_revision': 2}}, plc_model='FX3U')['session_action']=='continue'
    command = dict(operation='edit_fbd_graph', caller='Main', actions=[dict(action='move_node', node='b0:n0', x=3, y=4)])
    with pytest.raises(ValueError, match='network'):
        normalize_live_plan({**base, 'commands': [command], 'controls': {'expected_revision': 2}}, plc_model='FX3U')


@pytest.mark.parametrize('second_result', ['failed', 'passed', 'timeout'])
def test_second_compile_at_same_revision_expires_the_previous_address_result_and_saved_build(
        tmp_path, monkeypatch, second_result):
    raw, pages, live, session = session_pages(tmp_path)
    compile_calls, assignment_contexts = [], []
    def compiled(plan):
        compile_calls.append(deepcopy(plan))
        if len(compile_calls) == 2 and second_result == 'timeout':
            raise TimeoutError('second native rebuild still has no completion receipt')
        errors = 1 if len(compile_calls) == 2 and second_result == 'failed' else 0
        return dict(command='rebuild_all', completed=True, pid=pages.inspect()['process_id'],
            revision=plan['revision'], compile_run_id='run-' + str(len(compile_calls)),
            output=f'已完成全部编译。Error: {errors}, Warning: 0, CheckWarning: 0')
    def assignments(queries, *, revision, compilation, allocation_context):
        assignment_contexts.append(deepcopy(allocation_context))
        # The driver owns address semantics. This double supplies its already
        # verified receipt so this case isolates the controller's build lifetime.
        return dict(status='current' if allocation_context is not None else 'unavailable',
            revision=revision, compile_run_id=compilation['compile_run_id'], rows=[])
    monkeypatch.setattr(pages, 'compile_current', compiled, raising=False)
    monkeypatch.setattr(pages, 'read_assignments', assignments, raising=False)
    original = live.show_completion
    monkeypatch.setattr(live, 'show_completion', lambda command, prepared, observation=None:
        dict(completed=True, page='本次原生结果') if command['operation'] in ('compile', 'show_assignments')
        else original(command, prepared, observation))
    assert session.run([label_edit(pages)])['passed']
    pages.saved_output = current_saved_bytes(raw, pages)
    first = session.run([{'operation': 'compile'}, {'operation': 'save'}], {'expected_revision': 1})
    assert first['passed'] and first['compile']['compile_run_id'] == 'run-1'
    query = dict(operation='show_assignments', queries=[dict(scope='global', source='Global1.gh', name='Result')])
    # Query preparation still checks a real declaration; the blank witness
    # does not define Result, so use its actual declaration owner and name.
    declaration = next((table['owner'], row['name']) for table in pages.context['tables']
                       if table['owner_kind'] == 'global' for row in table['declarations'])
    query['queries'][0].update(source=declaration[0] + '.gh', name=declaration[1])
    previous = session.run([query], {'expected_revision': 1})
    assert previous['passed'] and previous['assignments']['status'] == 'current'
    assert assignment_contexts[0]['compile_run_id'] == 'run-1'
    rebuilt = session.run([{'operation': 'compile'}], {'expected_revision': 1})
    assert rebuilt['revision'] == 1 and rebuilt['assignments']['status'] == 'stale'
    assert rebuilt['assignments']['rows'] == [] and len(compile_calls) == 2
    if second_result == 'passed':
        current = session.run([query], {'expected_revision': 1})
        assert current['passed'] and current['assignments']['status'] == 'unavailable'
        assert assignment_contexts[-1] is None  # No save from run-2 exists.
    else:
        assert rebuilt['compile']['status'] == ('failed' if second_result == 'failed' else 'running')
        assert rebuilt['passed'] is (second_result == 'failed')
        if second_result == 'timeout':
            rebound = session.rebind()
            assert not rebound['passed'] and rebound['status'] == 'unconfirmed'
            assert len(compile_calls) == 2 and rebound['replayed'] is False


@pytest.mark.parametrize('operation', ['show_assignments', 'save', 'reopen'])
def test_interrupted_auxiliary_operation_requires_its_own_receipt_despite_unchanged_source(
        tmp_path, monkeypatch, operation):
    _, pages, live, session = session_pages(tmp_path)
    calls = []
    def interrupted(*args, **kwargs):
        calls.append((args, kwargs))
        raise TimeoutError('original native operation has no completed receipt')
    if operation == 'show_assignments':
        monkeypatch.setattr(pages, 'compile_current', lambda plan: dict(command='rebuild_all', completed=True,
            pid=pages.inspect()['process_id'], revision=plan['revision'], compile_run_id='run-before-query',
            output='已完成全部编译。Error: 0, Warning: 0, CheckWarning: 0'), raising=False)
        monkeypatch.setattr(live, 'show_completion', lambda *args: dict(completed=True, page='本次原生编译'))
        assert session.run([{'operation': 'compile'}])['passed']
        monkeypatch.setattr(pages, 'read_assignments', interrupted, raising=False)
        command = dict(operation=operation, queries=[dict(scope='global', source='Global1.gh', name='Result')])
    elif operation == 'save':
        monkeypatch.setattr(live, 'stage_save', interrupted)
        command = dict(operation=operation)
    else:
        assert session.run([{'operation': 'save'}])['passed']
        monkeypatch.setattr(pages, 'reopen', interrupted, raising=False)
        command = dict(operation=operation)
    result = session.run([command], {'expected_revision': 0})
    assert not result['passed'] and result['status'] == 'unconfirmed'
    rebound = session.rebind()
    assert not rebound['passed'] and rebound['status'] == 'unconfirmed'
    assert rebound['revision'] == 0 and rebound['pending_commands'] == 1
    assert rebound['replayed'] is False and len(calls) == 1
    assert all(step.get('verification', {}).get('status') != 'not_applied' for step in rebound['steps'])


def test_rebind_cannot_call_an_edit_not_applied_when_observed_ports_contradict_the_original(
        tmp_path, monkeypatch):
    from types import SimpleNamespace
    from gxworks2.live_driver import LiveEngineeringDriver
    from tests.test_gxworks2_live_boundaries import _LiveNativePages
    from tests.test_gxw_object_model import live_graph_context_witness
    raw, context = live_graph_context_witness()
    pages = _LiveNativePages(tmp_path / 'controlled.gxw', raw)
    pages.context['graphs'] = deepcopy(context['graphs'])
    for graph in pages.context['graphs']:
        graph['identity'] = pages.inspect()
    adapter = SimpleNamespace(_pending={}, pending_operations=lambda: [], read_graph=lambda driver, owner, kind:
        deepcopy(next(row for row in pages.context['graphs'] if (row['owner'], row['owner_kind']) == (owner, kind))))
    live = LiveEngineeringDriver(pages, raw, 'FX3U', adapter)
    session = LiveEngineeringSession(pages, 'FX3U', raw, 'a' * 32, 'project1', 'version1', live_driver=live,
                                     operation_authorizer=authorize_offline_live_pages)
    assert session.run([])['passed']
    calls = []
    def interrupted(command, prepared):
        calls.append(deepcopy(command))
        pages.context['graphs'][0]['networks'][0]['nodes'][0]['connections']['ports'][0]['local_x'] += 1
        raise TimeoutError('edit interrupted before declaration changed')
    monkeypatch.setattr(live, 'apply', interrupted)
    assert session.run([label_edit(pages)])['status'] == 'unconfirmed'
    rebound = session.rebind()
    assert rebound['status'] == 'unconfirmed' and rebound.get('passed') is not True
    assert rebound['replayed'] is False and rebound['revision'] == 0 and len(calls) == 1


def test_early_stage_save_and_later_edit_deliver_only_the_saved_revision_then_rebase_metadata(tmp_path):
    raw, pages, _, session = session_pages(tmp_path)
    assert session.run([label_edit(pages)])['passed']
    first_saved = current_saved_bytes(raw, pages)
    pages.saved_output = first_saved
    later_edit = label_edit(pages, 'SecondFlag')
    later_edit['declarations'][0]['device'] = 'M51'
    result = session.run([{'operation': 'save'}, later_edit], {'expected_revision': 1})
    assert result['passed'], result.get('error')
    assert result['revision'] == 2 and result['saved_revision'] == result['data_revision'] == 1
    assert result['dirty'] and result['data'] == first_saved and pages.saves == 1
    assert result['executed_steps'][0]['revision'] == 1 and result['executed_steps'][1]['revision'] == 2
    assert verified_live_data(result, raw) == first_saved
    session.set_base_version('version2')
    status = session.status()
    assert status['base_version_id'] == 'version2' and status['version_revision'] == 1
    assert not status['version_matches_active_source'] and status['dirty']
    # The next phase saves the current pages using the newly acknowledged
    # native baseline; the previous call's stage cannot stand in for this one.
    pages.saved_output = current_saved_bytes(raw, pages)
    current = session.run([{'operation': 'save'}], {'expected_revision': 2})
    assert current['passed'], current.get('error')
    assert current['data_revision'] == current['saved_revision'] == current['revision'] == 2
    assert current['version_revision'] == 1 and not current['dirty'] and pages.saves == 2
    assert verified_live_data(current, first_saved) == pages.saved_output


@pytest.mark.parametrize('prior_success', [False, True])
def test_save_rebind_never_adopts_external_disk_change_rejected_before_native_dispatch(
        tmp_path, monkeypatch, prior_success):
    raw, pages, live, session = session_pages(tmp_path)
    operation_ids = []
    original = live.stage_save
    def save(context, **kwargs):
        operation_ids.append(kwargs['operation_id'])
        return original(context, **kwargs)
    monkeypatch.setattr(live, 'stage_save', save)
    if prior_success:
        first = session.run([{'operation': 'save'}])
        assert first['passed'] and first['save']['operation_id'] == operation_ids[-1]
        assert live.stage_save_status(operation_ids[-1])['save_dispatched'] is True
    else:
        assert session.run([])['passed']
    previous_saves = pages.saves
    # A CFB transaction signature can change without changing any PLC source.
    # This is an external disk write, not this controller's native Save.
    external = bytearray(pages.path.read_bytes())
    external[52:56] = (1).to_bytes(4, 'little')
    pages.path.write_bytes(external)
    attempted = session.run([{'operation': 'save'}], {'expected_revision': 0})
    assert attempted['status'] == 'unconfirmed' and not attempted['passed']
    assert pages.saves == previous_saves
    current = live.stage_save_status(operation_ids[-1])
    assert current['save_dispatched'] is False and current['snapshot_returned'] is False
    assert current['status'] == 'rejected'
    if prior_success:
        assert len(set(operation_ids)) == 2
        old = live.stage_save_status(operation_ids[0])
        assert old['status'] == 'unavailable' and old['save_dispatched'] is False
    for _ in range(2):
        rebound = session.rebind()
        assert rebound['status'] == 'unconfirmed' and not rebound['passed']
        assert 'data' not in rebound and not rebound.get('stage_saves')
        assert rebound['pending_commands'] == 1 and rebound['replayed'] is False
    assert pages.saves == previous_saves and pages.path.read_bytes() == external


@pytest.mark.parametrize('wrong_progress', [None, 'another_operation', 'not_dispatched'])
def test_save_dispatched_before_reply_loss_recovers_only_the_same_operation_without_saving_again(
        tmp_path, monkeypatch, wrong_progress):
    raw, pages, live, session = session_pages(tmp_path)
    assert session.run([label_edit(pages)])['passed']
    pages.saved_output = current_saved_bytes(raw, pages)
    snapshot = pages.snapshot
    def interrupted_snapshot(**kwargs):
        snapshot(**kwargs)
        raise TimeoutError('Native Save was dispatched and wrote bytes; its reply was lost')
    monkeypatch.setattr(pages, 'snapshot', interrupted_snapshot)
    attempted = session.run([{'operation': 'save'}], {'expected_revision': 1})
    assert attempted['status'] == 'unconfirmed' and pages.saves == 1
    status = live.stage_save_status
    if wrong_progress:
        def wrong(operation_id):
            value = status(operation_id)
            if wrong_progress == 'another_operation':
                value['operation_id'] = 'f' * 32
            else:
                value['save_dispatched'] = False
            return value
        monkeypatch.setattr(live, 'stage_save_status', wrong)
        refused = session.rebind()
        assert refused['status'] == 'unconfirmed' and not refused['passed'] and 'data' not in refused
        assert pages.saves == 1 and refused['pending_commands'] == 1
        monkeypatch.setattr(live, 'stage_save_status', status)
    recovered = session.rebind()
    assert recovered['passed'], recovered.get('error')
    assert recovered['save']['operation_id']
    dispatched = status(recovered['save']['operation_id'])
    assert dispatched['save_dispatched'] is True and dispatched['snapshot_returned'] is False
    assert recovered['pending_commands'] == 0 and recovered['replayed'] is False and pages.saves == 1
    assert verified_live_data(recovered, raw) == pages.saved_output

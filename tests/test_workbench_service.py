from application.proposal_workflow import ProposalWorkflow
"""Service boundary checks against temporary engineering projects only."""

import base64
import copy
import json
from pathlib import Path

import pytest

from application.projects import ProjectError, ProjectService
from application.settings import SettingsService
from application.workbench import WorkbenchService
from application.workspace import ConflictError
from plc.core import PLCCore
from plc.ir import build_plc_ir
from application.session import ProjectSession as SessionStore


@pytest.fixture(autouse=True)
def isolated_native_replay_desktop_lock(tmp_path, monkeypatch):
    """Offline source-save replays must not claim the user's desktop lock."""
    import application.execution as execution

    implementation = execution.DesktopResourceLock
    monkeypatch.setattr(execution, 'DesktopResourceLock',
        lambda path=None: implementation(path if path is not None else tmp_path / 'replay-desktop.lock'))


def test_workbench_start_close_are_idempotent_and_preserve_one_running_service(tmp_path):
    service = WorkbenchService(tmp_path / 'workspace', tmp_path / 'state')
    try:
        assert service.start() is service
        jobs, execution, lock = service.jobs, service.execution, service.lock
        assert service.start() is service
        assert (service.jobs, service.execution, service.lock) == (jobs, execution, lock)
        service.close()
        service.close()
        assert not lock.acquired and jobs._closed and execution._closed
        assert service.start() is service
        assert service.jobs is not jobs and service.execution is not execution
    finally:
        service.close()


@pytest.mark.parametrize('action,mode,retained', [('native_engineering', 'live', True),
    ('native_engineering', 'batch', False), ('gx_import', 'live', False)])
def test_trusted_native_live_receipts_use_typed_projection_without_widening_generic_payloads(action, mode, retained):
    from application.proposals import ProposalService
    from application.jobs import JobManager
    from application.workspace import public_payload
    receipt = dict(mode='live', session_id='a' * 32, project_id='p1', base_version_id='v1', version_id='v2',
        status='completed', passed=True, revision=2, saved_revision=1, dirty=True,
        steps=[dict(index=1, operation='save', status='completed', revision=1,
            verification=dict(status='passed', validation={'status': 'matched'}, raw_bytes=b'private'))],
        current_context=dict(st=[dict(owner='Main', st_text='Value := 2;', editor_handle=55)],
            note=r'D:\private\evidence.json'), data=b'private saved GXW', _stage_context={'private': True})
    record = dict(action=action, private_payload={'plan': {'mode': mode}}, result=receipt)
    result = ProposalService._public(record)['result']
    assert ('session_id' in result) is retained
    assert ('mode' in result) is retained
    assert public_payload({'mode': 'live', 'session_id': 'not-a-receipt', 'revision': 4}) == {}
    if retained:
        assert result['revision'] == 2 and result['saved_revision'] == 1 and result['version_id'] == 'v2'
        assert result['steps'][0]['verification']['status'] == 'passed'
        assert result['current_context']['st'] == [{'owner': 'Main', 'st_text': 'Value := 2;'}]
        assert 'data' not in result and '_stage_context' not in result
        assert result['current_context']['note'] == '[private path]'
        assert public_payload(result, native_live=True) == result
        job = dict(kind='execution', snapshot={'project_id': 'p1', 'version_id': 'v1'},
                   result={'proposal_id': 'proposal1', 'result': result})
        assert JobManager._public(job)['result']['result'] == result
    else:
        assert 'current_context' not in result and 'steps' not in result


@pytest.mark.parametrize('mode', ['live', 'batch'])
@pytest.mark.parametrize('cpu', [None, '', '  ', True])
def test_native_execution_proposal_requires_explicit_saved_cpu_before_artifact_or_approval(mode, cpu):
    from types import SimpleNamespace
    import threading
    def forbidden(*args, **kwargs):
        pytest.fail('Missing saved CPU reached artifact access or approval')
    service = SimpleNamespace(require_writable=lambda: None, lock=SimpleNamespace(thread_lock=threading.RLock()),
        projects=SimpleNamespace(raw_version=lambda *args: dict(target_mode='fbd', plc_model=cpu), artifact=forbidden),
        store=SimpleNamespace(get_project=lambda *args: dict()), proposals=SimpleNamespace(create=forbidden))
    plan = dict(schema_version=1, commands=[dict(operation='save')])
    if mode == 'live':
        plan['mode'] = 'live'
    with pytest.raises(ValueError, match='explicit saved project CPU'):
        ProposalWorkflow.create_execution(service, dict(project_id='p1', version_id='v1',
            action='native_engineering', plan=plan, request_id='missing-native-cpu'))


@pytest.mark.parametrize('cpu', ('Q03UDV', 'L02', 'FX3G'))
def test_native_live_proposal_accepts_unobserved_saved_gui_mirror_only_for_current_ui_observation(tmp_path, cpu):
    import threading
    from types import SimpleNamespace
    from gxw.project_metadata import project_gui_configuration
    from gxw.project_templates import blank_fbd_baseline
    raw = blank_fbd_baseline(cpu)
    source = tmp_path / 'selected.gxw'
    source.write_bytes(raw)
    proposals = []
    def create(action, project_id, payload, **options):
        row = dict(id='proposal1', action=action, project_id=project_id, private_payload=payload)
        proposals.append(row)
        return row
    service = SimpleNamespace(require_writable=lambda: None,
        lock=SimpleNamespace(thread_lock=threading.RLock()),
        projects=SimpleNamespace(raw_version=lambda *args: dict(target_mode='fbd', plc_model=cpu),
                                 artifact=lambda *args: source),
        store=SimpleNamespace(get_project=lambda *args: dict(plc_model='FX3U')),
        proposals=SimpleNamespace(find_request=lambda *args: None, create=create),
        apply_execution_policy=lambda row: row)
    result = ProposalWorkflow.create_execution(service, dict(project_id='p1', version_id='v1',
        action='native_engineering', request_id='unobserved-mirror-live',
        plan=dict(schema_version=1, mode='live', commands=[])))
    assert result == proposals[0] and result['private_payload']['plan']['mode'] == 'live'
    assert project_gui_configuration(raw)['status'] == 'not_verified'
    assert source.read_bytes() == raw


@pytest.mark.parametrize('cpu,menu', [('FX3UC', 'FX3U/FX3UC'), ('Q03UDV', 'Q03UDV'), ('L02', 'L02/L02-P')])
@pytest.mark.parametrize('language,codepage', [('zh-CN', 936), ('en', 936), ('ja', 932)])
def test_native_saved_generation_metadata_preserves_exact_menu_language_and_codepage(cpu, menu, language, codepage):
    from application.native_completion import native_generation_metadata as _native_generation_metadata
    identity = dict(cpu_menu=menu, software_version='1.635.0.1', native_ui_language=language,
                    project_codepage=codepage, project_path=r'D:\private\source.gxw', window_handle=123)
    receipt = dict(identity=identity)
    summary = dict(mode='live', status='completed', passed=True, identity={
        key: value for key, value in identity.items() if key not in ('project_path', 'window_handle')})
    metadata = _native_generation_metadata(cpu, receipt, summary)
    assert metadata == dict(native_engineering=summary, native_cpu_menu=menu, software_version='1.635.0.1',
                           native_ui_language=language, project_codepage=codepage)
    assert receipt == dict(identity=identity)


def test_native_saved_generation_metadata_rejects_another_menu_and_does_not_invent_absent_identity():
    from application.native_completion import native_generation_metadata as _native_generation_metadata
    with pytest.raises(ValueError, match='CPU differs'):
        _native_generation_metadata('Q03UDV', dict(identity=dict(cpu_menu='L02/L02-P')), dict())
    assert _native_generation_metadata('Q03UDV', dict(), dict()) == dict(
        native_engineering={}, native_cpu_menu='Q03UDV')


@pytest.mark.parametrize('prefix', ['shared-save/', 'diagnostic/action-out-x0-count-fixed/save_source/',
                                  'diagnostic/transition-out-x0/save_source/'])
def test_imported_sfc_child_candidates_reuse_native_save_versions_and_current_source(workbench, monkeypatch, prefix):
    import gxworks2.workspace_adapter as adapter
    from gxw.native_write import native_source_plan, verify_native_save
    from gxw.sfc_pou import read_sfc_project_context
    from tests.test_gxw_lossless import sfc_object_edit_witness

    files = sfc_object_edit_witness()
    baseline, candidate = files[prefix + 'input.gxw'], files[prefix + 'candidate.gxw']
    original_request = json.loads(files[prefix + 'request.json'])['source']
    observation = json.loads(files[prefix + 'observation.json'])
    original, final = read_sfc_project_context(baseline), read_sfc_project_context(candidate)
    changed = [child for child in final.source.layout['children'] if child['token_hex'] != next(
        row['token_hex'] for row in original.source.layout['children']
        if (row['kind'], row['name']) == (child['kind'], child['name']))]
    assert len(changed) == 1
    child = changed[0]
    edits = [{'kind': child['kind'], 'name': child['name'], 'records_hex': child['token_hex']}]
    calls = []

    def save(_self, prepared, *, timeout=None):
        assert timeout is None and prepared.baseline == baseline
        plan = native_source_plan(prepared)
        for key in ('program_name', 'program_kind', 'cpu', 'codepage', 'graph_body', 'before_children', 'after_children'):
            assert plan[key] == original_request[key]
        calls.append('save')
        return verify_native_save(prepared, candidate, observation)

    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'save', save)
    project = workbench.create_project(name='SFC source boundary', plc_model='FX3U', target_mode='ladder')
    command = {'operation': 'import', 'project_id': project['id'], 'request_id': 'sfc-import',
               'data_base64': base64.b64encode(baseline).decode()}
    imported = workbench.sfc.propose(command)
    base_id = imported['result']['version_id']
    original_model = workbench.projects.program(project['id'], base_id)
    assert original_model == workbench.sfc.read(project['id'], base_id)['model']
    assert original_model['cpu'] == 'FX3U/FX3UC' and not calls
    assert workbench.sfc.propose(command) == imported
    preview = workbench.sfc.preview(project['id'], base_id, edits)
    proposal_command = {'operation': 'edit', 'project_id': project['id'], 'version_id': base_id,
                        'request_id': 'sfc-edit', 'child_edits': edits}
    proposal = workbench.sfc.propose(proposal_command)
    assert proposal['status'] == 'accepted'
    version_id = proposal['result']['version_id']
    assert workbench.sfc.propose(proposal_command) == proposal and calls == ['save', 'save']
    view = workbench.projects.program(project['id'], version_id)
    assert view == preview['program'] == workbench.proposal_preview(proposal['id'])['program']
    assert preview['validation']['gx_compile'] == 'not_run'
    assert preview['validation']['native_save'] == 'passed'
    assert preview['diff']['modified_children'] == [{'kind': child['kind'], 'name': child['name']}]
    assert view['graph'] == original_model['graph'] and view['labels'] == original_model['labels']
    assert workbench.projects.artifact(project['id'], base_id, 'gxw').read_bytes() == baseline
    assert workbench.projects.artifact(project['id'], version_id, 'gxw').read_bytes() == candidate
    restarted = WorkbenchService(workbench.store.base_dir, workbench.state_dir, read_only=True)
    assert restarted.projects.program(project['id'], version_id) == view
    result = workbench.agent_call({'project_id': project['id'], 'version_id': version_id,
        'name': 'read_sfc_project', 'arguments': {}, 'call_id': 'read-sfc'})
    assert not result['is_error'] and result['data']['data']['model'] == view
    assert workbench.projects.version(project['id'], version_id)['capabilities']['operations']['sfc_edit']


@pytest.mark.parametrize('edits', [[], [{'kind': 'zoom', 'name': 'absent', 'records_hex': '030003'}],
    [{'kind': 'zoom', 'name': 'absent', 'records_hex': '0300'}],
    [{'kind': 'zoom', 'name': 'absent', 'records_hex': 'bad bytes'}],
    [{'kind': 'graph', 'name': 'absent', 'records_hex': '030003'}]])
def test_sfc_invalid_child_candidate_keeps_the_existing_version_and_never_saves(workbench, monkeypatch, edits):
    import gxworks2.workspace_adapter as adapter
    from tests.test_gxw_lossless import sfc_object_edit_witness
    baseline = sfc_object_edit_witness()['shared-save/input.gxw']
    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'save',
                        lambda *args, **kwargs: pytest.fail('Unbound child edit reached native save'))
    project = workbench.create_project(name='SFC existing source', target_mode='ladder')
    proposal = workbench.sfc.propose({'operation': 'import', 'project_id': project['id'], 'request_id': 'sfc-base',
                                    'data_base64': base64.b64encode(baseline).decode()})
    version_id = proposal['result']['version_id']
    before = workbench.projects.raw_project(project['id'])
    with pytest.raises(ValueError):
        workbench.sfc.propose({'operation': 'edit', 'project_id': project['id'], 'version_id': version_id,
                              'request_id': 'sfc-invalid-edit', 'child_edits': edits})
    assert workbench.projects.raw_project(project['id']) == before
    assert workbench.projects.artifact(project['id'], version_id, 'gxw').read_bytes() == baseline


_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


@pytest.mark.parametrize('case', ['baseline', 'action-error', 'jump-closure'])
def test_sfc_native_graph_candidates_keep_geometry_across_versions_and_standalone_mcp(workbench, monkeypatch, case):
    import zipfile
    import gxworks2.workspace_adapter as adapter
    from gxw.native_write import native_source_plan, verify_native_save, _native_sfc_graph_output
    from gxw.project_writer import prepare_sfc_project_write
    from gxw.sfc_pou import read_sfc_project_context
    from research.freeze_gxw_sfc_application_graph import ARCHIVE, EDITS as ACTION_EDITS
    from agent_runtime.runtime import build_default_tool_runtime
    from integrations.mcp.context_provider import SessionToolContextProvider
    from integrations.mcp.tool_adapter import MCPToolAdapter
    prefix = 'jump-edits/jump-closure/' if case == 'jump-closure' else case + '/'
    EDITS = ([dict(kind='jump', x=0, y=6, before_step_reference=0, target_step_reference=1)] + ACTION_EDITS
             if case == 'jump-closure' else ACTION_EDITS)
    with zipfile.ZipFile(ARCHIVE) as z:
        files = {name.removeprefix(prefix): z.read(name) for name in z.namelist() if name.startswith(prefix)}
    raw = files['input.gxw']; calls = []
    def read(_self, data, program=None, *, timeout=None):
        assert data == raw and timeout is None
        context = read_sfc_project_context(data, program)
        prepared = prepare_sfc_project_write(data, context.program_name, context.source, {})
        observed = json.loads(files['read-read_sfc_graph/observation.json'])
        body = files['read-read_sfc_graph/sfc-graph-after.bin']
        _, graph = _native_sfc_graph_output(prepared, {**native_source_plan(prepared), 'graph_edits': []}, body, observed)
        graph.update(logical_name=context.program_name, source_body_hex=body.hex(), native_workspace=observed)
        calls.append('read'); return graph
    def save(_self, prepared, *, timeout=None, graph_edits=None):
        assert timeout is None and graph_edits == EDITS and prepared.baseline == raw
        prefix = 'save-save_source/'
        assert native_source_plan(prepared, graph_edits=graph_edits) == json.loads(files[prefix + 'request.json'])['source']
        calls.append('save')
        return verify_native_save(prepared, files[prefix + 'candidate.gxw'], json.loads(files[prefix + 'observation.json']),
                                  graph_edits=graph_edits, graph_body=files[prefix + 'sfc-graph-after.bin'])
    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'read_sfc_graph', read)
    monkeypatch.setattr(adapter.NativeWorkspaceSourceSave, 'save', save)
    project = workbench.create_project(name='SFC graph version', target_mode='ladder')
    imported = workbench.sfc.propose(dict(operation='import', project_id=project['id'], request_id='sfc-import', data_base64=base64.b64encode(raw).decode()))
    base = imported['result']['version_id']
    first = workbench.sfc.read(project['id'], base, native=True)
    assert workbench.sfc.read(project['id'], base, native=True) == first and calls == ['read']
    preview = workbench.sfc.preview(project['id'], base, graph_edits=EDITS)
    command = dict(operation='edit', project_id=project['id'], version_id=base, request_id='sfc-graph', graph_edits=EDITS)
    proposal = workbench.sfc.propose(command); current = proposal['result']['version_id']
    assert workbench.sfc.propose(command) == proposal and calls == ['read', 'save', 'save']
    model = workbench.sfc.read(project['id'], current)['model']
    assert model == preview['program'] == workbench.projects.program(project['id'], current)
    assert model['graph']['position_resolution'] == 'native-source-bound'
    topology = model['graph']['topology']
    if case != 'jump-closure':
        assert topology == first['model']['graph']['topology']
    assert len(topology['segments']) == 10 and sum(row['style'] == 'parallel' for row in topology['segments']) == 2
    assert not topology['issues'] and topology['jumps'][0]['targets'] == (['cell:0:3'] if case == 'jump-closure' else ['cell:0:0'])
    cell, = [row for row in model['graph']['cells'] if row['role'] == 'step' and row['binding'].get('action_number') == 10]
    assert (cell['x'], cell['y'], cell['binding']['step_reference']) == (1, 3, 2)
    reopened = WorkbenchService(workbench.store.base_dir, workbench.state_dir, read_only=True)
    assert reopened.sfc.read(project['id'], current)['model'] == model
    result = MCPToolAdapter(build_default_tool_runtime(), SessionToolContextProvider(workbench.store.base_dir, project['id'], current)).call_tool('read_sfc_project', {}, 'sfc-read')
    assert not result.is_error and result.structured_content['data']['model'] == model
    assert workbench.projects.artifact(project['id'], base, 'gxw').read_bytes() == raw


def test_sfc_native_geometry_requires_the_current_body_without_blocking_source_reads():
    from copy import deepcopy
    import zipfile
    from hypothesis import given, settings, strategies as st
    from gxw.sfc_pou import read_sfc_project_context
    from research.freeze_gxw_sfc_application_graph import ARCHIVE
    with zipfile.ZipFile(ARCHIVE) as z:
        raw = z.read('baseline/candidate.gxw')
        report_name = next(name for name in z.namelist() if name.startswith('baseline/workspace/') and name.endswith('v0002/write_report.json'))
        evidence = json.loads(z.read(report_name))['sfc_graph']
    context = read_sfc_project_context(raw)
    @settings(max_examples=32, deadline=None, derandomize=True)
    @given(st.sampled_from(['current', 'body', 'cpu', 'program', 'cell', 'dimensions']))
    def property_case(change):
        graph = deepcopy(evidence)
        if change == 'body': graph['source_body_hex'] = graph['source_body_hex'][:-2] + 'ff'
        elif change == 'cpu': graph['cpu'] = 'FX3G'
        elif change == 'program': graph['logical_name'] = 'Other.Program.pou'
        elif change == 'cell': graph['after']['cells'][0]['x'] = True
        elif change == 'dimensions': graph['after']['height'] = 0
        model = context.object_model(native_graph=graph)
        assert model['graph']['position_resolution'] == ('native-source-bound' if change == 'current' else 'not-established')
        assert model['labels'] == context.object_model()['labels'] and model['children'] == context.object_model()['children']
    property_case()


@pytest.mark.parametrize("cpu,mode,project_type", [("Q03UDV", "st", "structured"),
    ("L02/L02-P", "ladder", "simple"), ("FX3U/FX3UC", "fbd", "structured"),
    ("QJ72LP25/QJ72BR15(RemoteI/O)", "ladder", "parameter")])
def test_creation_retains_native_cpu_type_and_language(tmp_path, cpu, mode, project_type):
    service = WorkbenchService(tmp_path / "workspace", tmp_path / "state")
    service.start()
    try:
        _assert_selected_creation(service, cpu, mode, project_type)
    finally:
        service.close()


def _assert_selected_creation(service, cpu, mode, project_type):
    project = service.create_project(name="Selected native target", plc_model=cpu, target_mode=mode,
                                     native_project_type=project_type)
    assert project["plc_model"] == cpu and project["target_mode"] == mode
    assert project["native_project_type"] == project_type
    assert service.store.get_project(project["id"])["native_project_type"] == project_type
    before = _bytes(service.store.base_dir)
    with pytest.raises(ValueError):
        service.create_project(name="Invalid", plc_model="FX3U", target_mode="fbd", native_project_type="simple")
    assert _bytes(service.store.base_dir) == before


def test_creation_settings_update_preserves_selected_structured_ladder(workbench):
    project = workbench.create_project(name='Structured ladder', plc_model='Q03UDV', target_mode='ladder',
                                       native_project_type='structured')
    updated = workbench.update_project(project['id'], plc_model='Q04UDV')
    assert updated['native_project_type'] == 'structured' and updated['use_labels'] is True
    with pytest.raises(ValueError):
        workbench.update_project(project['id'], use_labels=False)
    assert workbench.store.get_project(project['id'])['use_labels'] is True


@pytest.mark.parametrize('cpu,hardware', [
    ('FX2N', {'cpu_version': '3.00'}),
    ('FX3S', {'source': 'user_confirmed_spec', 'cpu_full_model': 'FX3S-20MR/ES'}),
    ('Q03UDE', {'serial_first5': 10102}),
    ('Q03UDV', {}), ('L02/L02-P', {}),
    ('L26-BT', {'cc_link_source': 'built_in', 'sb_refresh_start': 'SB0', 'station_role': 'master'}),
])
def test_confirmed_hardware_can_be_saved_for_declared_cpu_without_fx3u_fallback(workbench, cpu, hardware):
    project = workbench.create_project(name='CPU fact delivery', plc_model=cpu, target_mode='st')
    spec = {'plc_model': cpu, 'summary': 'M0 enables the requested function', 'parameters': [],
            'io_table': [{'address': 'M0', 'kind': 'M', 'label': 'Enable'}],
            'hardware_profile': hardware}
    result = workbench.set_spec(project['id'], spec, None)
    assert result['valid'] is True
    stored = workbench.projects.raw_project(project['id'])['confirmed_spec']
    assert stored['plc_model'] == cpu
    assert all(stored['hardware_profile'][key] == value for key, value in hardware.items())
    if cpu in {'FX2N', 'Q03UDE', 'L26-BT'}:
        assert any(row['code'] == 'device_profile_unverified' for row in result['issues']['warnings'])


@pytest.mark.parametrize('cpu,address', [('FX2N', 'SD?'), ('Q03UDE', 'M?'), ('Q03UDV', 'X00G'), ('L02', 'SD1282.10')])
def test_confirmation_rejects_bad_addresses_on_non_fx3u_targets(cpu, address):
    from plc.specification.confirmed import validate_spec_draft
    result = validate_spec_draft({'plc_model': cpu, 'parameters': [],
        'io_table': [{'address': address, 'kind': 'M', 'label': 'Bad address'}]}, cpu)
    assert any(row['code'] == 'invalid_io_address' for row in result['errors'])


def test_confirmation_unknown_cpu_does_not_become_a_known_profile():
    from plc.specification.confirmed import validate_spec_draft
    result = validate_spec_draft({'plc_model': 'Q_UNKNOWN', 'parameters': [], 'io_table': []}, 'Q_UNKNOWN')
    assert [row['code'] for row in result['errors']] == ['unsupported_plc_model']


def _bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _program(input_address="X0"):
    return build_plc_ir({"device_comments": {}, "rungs": [{"rung_id": 1, "debug_note": "启动输出",
        "header_element": None, "shared_inputs": [], "branches": [{"branch_id": 1, "y_offset_level": 0,
        "inputs": [{"type": "NO", "address": input_address, "label": ""}],
        "outputs": [{"type": "COIL", "address": "Y0", "label": ""}]}]}]},
        plc_model="FX3U", program_name="MAIN", revision=1)


@pytest.fixture
def saved(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace")
    project = store.create_project("测试工程")
    program = _program()
    version_id, output = store.prepare_version(project["id"])
    metadata = store._ir_metadata(program)
    metadata.update(target_mode="ladder", plc_model="FX3U", artifacts=PLCCore().compile_project(program, output)["artifacts"])
    version = store.complete_version(project["id"], version_id, metadata)
    return store, project, version, program


@pytest.fixture
def workbench(saved, tmp_path):
    store, _, _, _ = saved
    service = WorkbenchService(store.base_dir, tmp_path / "state", model_factory=lambda: (None, {"model": "unit-fake"}))
    service.start()
    try:
        yield service
    finally:
        service.close()


def _operation_proposal(workbench, project, version, *, request_id='operation-query'):
    return workbench.proposals.create('gx_import', project['id'],
        {'project_id': project['id'], 'version_id': version['id']},
        public_summary={'summary': 'isolated native operation'}, base_version_id=version['id'],
        request_id=request_id)


def test_shared_operation_query_reads_running_receipts_without_the_execution_writer_lock(workbench, saved):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    _, project, version, _ = saved
    proposal = _operation_proposal(workbench, project, version)
    entered, release = threading.Event(), threading.Event()

    def worker(ctx):
        with workbench.lock.thread_lock:
            record = workbench.proposals._load(proposal['id'])
            record['status'] = 'executing'
            workbench.proposals._save(record)
            entered.set()
            assert release.wait(5), 'The receipt query waited for an unrelated native write'
            record.update(status='accepted', result={'status': 'completed', 'passed': True,
                'version_id': version['id'], 'counts': {'completed': 2, 'total': 2},
                'validation': {'status': 'passed'}, 'message': r'Saved D:\private\experiment.gxw',
                'native_workspace': {'module_base': 123, 'raw': 'private vendor layout'}})
            workbench.proposals._save(record)
            return {'proposal_id': proposal['id'], 'status': 'accepted', 'result': record['result']}

    job = workbench.jobs.submit('execution', {'project_id': project['id'], 'version_id': version['id'],
        'proposal_id': proposal['id']}, worker)
    command = {'project_id': project['id'], 'name': 'get_operation_status',
               'arguments': {'execution_job_id': job['id']}, 'call_id': 'same-read-call'}
    try:
        assert entered.wait(5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            query = pool.submit(workbench.agent_call, command)
            try:
                running = query.result(timeout=2)
            finally:
                release.set()
        data = running['data']['data']
        assert data['status'] == data['job']['status'] == 'running'
        assert data['proposal']['status'] == 'executing'
        assert data['execution_result'] is None
        assert data['proposal_id'] == proposal['id'] and data['execution_job_id'] == job['id']
        assert data['base_version_id'] == version['id'] and 'version_id' not in data
        workbench.jobs._futures[job['id']].result(timeout=5)
        completed = workbench.agent_call(command)['data']['data']
        assert completed['status'] == 'completed' and completed['proposal']['status'] == 'accepted'
        assert completed['version_id'] == version['id']
        assert completed['execution_result']['validation'] == {'status': 'passed'}
        assert completed['execution_result']['counts'] == {'completed': 2, 'total': 2}
        encoded = json.dumps(completed)
        assert 'experiment.gxw' not in encoded and 'private vendor layout' not in encoded
        assert 'module_base' not in encoded and 'native_workspace' not in encoded and 'snapshot' not in encoded
    finally:
        release.set()


@pytest.mark.parametrize('query_kind', ['proposal', 'job'])
def test_shared_operation_query_cannot_read_another_selected_project(workbench, saved, query_kind):
    _, project, version, _ = saved
    proposal = _operation_proposal(workbench, project, version)
    other = workbench.create_project(name='another project')
    if query_kind == 'proposal':
        arguments = {'proposal_id': proposal['id']}
    else:
        job = workbench.jobs.submit('execution', {'project_id': project['id'], 'version_id': version['id'],
            'proposal_id': proposal['id']}, lambda ctx: {})
        workbench.jobs._futures[job['id']].result(timeout=5)
        arguments = {'execution_job_id': job['id']}
    response = workbench.agent_call({'project_id': other['id'], 'name': 'get_operation_status',
        'arguments': arguments, 'call_id': 'foreign-query'})
    assert response['is_error'] and response['data']['ok'] is False
    assert 'proposal' not in response['data'] and 'execution_result' not in response['data']


@pytest.mark.parametrize('damage', ['proposal_project', 'baseline', 'result_proposal', 'result_project'])
def test_shared_operation_query_rejects_inconsistent_job_proposal_bindings(workbench, saved, damage):
    _, project, version, _ = saved
    proposal = _operation_proposal(workbench, project, version)
    other = workbench.create_project(name='foreign project')
    job = workbench.jobs.submit('execution', {'project_id': project['id'], 'version_id': version['id'],
        'proposal_id': proposal['id']}, lambda ctx: {'proposal_id': proposal['id'], 'result': {'status': 'completed'}})
    workbench.jobs._futures[job['id']].result(timeout=5)
    record = workbench.jobs._load(job['id'])
    if damage == 'proposal_project':
        foreign = workbench.proposals._load(proposal['id'])
        foreign['project_id'] = other['id']
        workbench.proposals._save(foreign)
    elif damage == 'baseline':
        record['snapshot']['version_id'] = 'foreign-version'
    elif damage == 'result_proposal':
        record['result']['proposal_id'] = 'foreign-proposal'
    else:
        record['result']['result']['project_id'] = other['id']
    workbench.jobs._save(record)
    with pytest.raises((KeyError, ValueError)):
        workbench.operation_status(project['id'], execution_job_id=job['id'])


def test_operation_receipts_remain_readable_without_starting_a_writer_or_migrating_jobs(workbench, saved, monkeypatch):
    from application.workbench import WorkbenchService
    _, project, version, _ = saved
    proposal = _operation_proposal(workbench, project, version)
    job = workbench.jobs.submit('execution', {'project_id': project['id'], 'version_id': version['id'],
        'proposal_id': proposal['id']}, lambda ctx: {'proposal_id': proposal['id'], 'result': {'status': 'completed'}})
    workbench.jobs.wait(job['id'])
    before = {str(path): path.read_bytes() for path in workbench.state_dir.rglob('*') if path.is_file()}
    cold = WorkbenchService(workbench.store.base_dir, workbench.state_dir, read_only=True, settings=workbench.settings)
    monkeypatch.setattr(cold.store, 'migrate_confirmed_specs', lambda: pytest.fail('Receipt read started a writer'))
    result = cold.operation_status(project['id'], execution_job_id=job['id'])
    assert result['execution_job_id'] == job['id'] and result['proposal_id'] == proposal['id']
    assert result['execution_result'] == {'status': 'completed'}
    assert cold.operation_status(project['id'], proposal_id=proposal['id'])['proposal']['status'] == 'pending'
    assert cold.jobs is cold.proposals is cold.execution is cold.lock is None
    assert before == {str(path): path.read_bytes() for path in workbench.state_dir.rglob('*') if path.is_file()}


@pytest.mark.parametrize('arguments', [{}, {'proposal_id': 'p', 'execution_job_id': 'j'},
    {'proposal_id': '../private'}, {'execution_job_id': r'D:\private\job.json'}, {'proposal_id': 'p', 'path': 'private'}])
def test_shared_operation_query_rejects_ambiguous_or_path_arguments_before_receipt_reads(workbench, saved, monkeypatch, arguments):
    _, project, _, _ = saved
    monkeypatch.setattr(workbench, 'operation_status', lambda *a, **kw: pytest.fail('Invalid query reached receipt reads'))
    response = workbench.agent_call({'project_id': project['id'], 'name': 'get_operation_status',
        'arguments': arguments, 'call_id': 'invalid-query'})
    assert response['is_error'] and response['data']['error']['code'] == 'INVALID_ARGUMENTS'


def _change_artifact(store, project_id, artifact_id, path):
    project = store.get_project(project_id)
    project["versions"][0]["artifacts"][artifact_id] = str(path)
    store.save_project(project)


def test_legacy_analysis_output_restores_choices_without_writing(workbench, saved):
    store, project, _, _ = saved
    job = workbench.jobs.submit("analysis", {"project_id": project["id"]}, lambda context: {})
    workbench.jobs._futures[job["id"]].result(timeout=5)
    directory = workbench.state_dir / "outputs"
    directory.mkdir(exist_ok=True)
    output = {"analysis": {"missing_info": [{"id": "start_input", "question": "启动接哪个输入？", "options": ["X0", "X2"]}]},
              "spec_draft": {"parameters": [{"id": "start_input", "name": "旧问题措辞", "value": "X3", "source": "user"}]}}
    (directory / (job["id"] + ".json")).write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    state_before, workspace_before = _bytes(workbench.state_dir), _bytes(store.base_dir)
    result = workbench.output(job["id"])
    assert result["spec_draft"]["parameters"][0] == {"id": "start_input", "name": "旧问题措辞", "value": "X3", "source": "user", "options": ["X0", "X2"],
        "io_binding": {"binding_id": "legacy_start", "kind": "X", "role": "start"}}
    assert _bytes(workbench.state_dir) == state_before
    assert _bytes(store.base_dir) == workspace_before


def test_spec_save_rejects_duplicate_raw_rows_before_canonicalization(workbench, saved):
    store, project, _, _ = saved
    spec = {"summary": "启停", "plc_model": "FX3U", "io_table": [
        {"address": "X0", "kind": "X", "label": "启动"},
        {"address": "X0", "kind": "X", "label": "停止"},
    ], "parameters": []}
    before = _bytes(store.base_dir)
    result = workbench.set_spec(project["id"], spec, None)
    assert result["valid"] is False
    assert any(issue["code"] == "duplicate_io_address" for issue in result["issues"]["errors"])
    assert _bytes(store.base_dir) == before


def test_saved_address_answers_return_actual_hash_and_keep_contact_choice(workbench, saved):
    from plc.specification.confirmed import build_review_draft
    from plc.ir import canonical_sha256

    store, project, _, _ = saved
    draft = build_review_draft({"summary": "启停", "suggested_io": {"X": {"X0": "启动", "X1": "停止"}, "Y": {"Y0": "输出"}},
        "missing_info": [{"id": "start_input", "question": "启动接哪个输入？"},
                         {"id": "stop_input", "question": "停止接哪个输入？常开还是常闭？"}]})
    draft["parameters"][0].update(value="X2", source="user")
    draft["parameters"][1].update(value="X1，常闭", source="user")
    result = workbench.set_spec(project["id"], draft, None)
    assert result["valid"] is True
    persisted = store.get_project(project["id"])["confirmed_spec"]
    assert result["spec"] == persisted
    assert result["hash"] == canonical_sha256(persisted)
    assert persisted["parameters"][0]["value"] == "X1，常闭"
    repeated = workbench.set_spec(project["id"], persisted, result["hash"])
    assert repeated["valid"] is True
    assert repeated["hash"] == result["hash"]
    assert repeated["spec"] == persisted


def test_project_reads_and_legacy_program_loading_never_write(saved):
    store, project, version, program = saved
    changed = store.get_project(project["id"])
    changed["versions"][0]["artifacts"].pop("ir")
    store.save_project(changed)
    before = _bytes(store.base_dir)
    service = ProjectService(store.base_dir)
    assert service.list_projects()[0]["id"] == project["id"]
    assert service.project(project["id"])["version_count"] == 1
    assert service.version(project["id"], version["id"])["id"] == version["id"]
    assert service.program(project["id"], version["id"])["program_name"] == program["program_name"]
    assert service.diagnostics(project["id"], version["id"])["ok"] is True
    assert _bytes(store.base_dir) == before


def test_read_only_workbench_does_not_create_workspace_state_or_config(tmp_path, monkeypatch):
    missing = tmp_path / "missing-workspace"
    state = tmp_path / "application-state"
    monkeypatch.setattr(SettingsService, "read_config", lambda self: pytest.fail("Reading projects must not load model settings"))
    service = WorkbenchService(missing, state, read_only=True)
    service.start()
    try:
        assert service.projects.list_projects() == []
        assert not missing.exists()
        assert not state.exists()
        with pytest.raises(PermissionError):
            service.create_project(name="forbidden")
    finally:
        service.close()


@pytest.mark.parametrize("bad_id", ["../outside", "..", "C:\\private", "project:stream", "project/name"])
def test_malicious_project_index_is_rejected_without_reading_or_writing(saved, bad_id):
    store, _, _, _ = saved
    store._write_json(store.index_path, {"projects": [bad_id]})
    before = _bytes(store.base_dir)
    with pytest.raises((ProjectError, ValueError)):
        ProjectService(store.base_dir).list_projects()
    assert _bytes(store.base_dir) == before


@pytest.mark.parametrize("filename", ["../../../../../outside.json", "program.st:private.json"])
def test_manifest_path_escape_and_windows_ads_are_rejected(saved, filename):
    store, project, version, _ = saved
    if ":" in filename:
        # On Windows this creates a harmless ADS in a temporary test directory;
        # elsewhere it is a regular colon-named file, still forbidden by the API.
        path = store.version_dir(project["id"], version["id"]) / filename
        path.write_text('{"secret":"must-not-be-read"}', encoding="utf-8")
    _change_artifact(store, project["id"], "json", filename)
    before = _bytes(store.base_dir)
    with pytest.raises((ProjectError, ValueError)):
        ProjectService(store.base_dir).artifact(project["id"], version["id"], "json")
    assert _bytes(store.base_dir) == before


def test_manifest_absolute_path_outside_version_is_rejected(saved, tmp_path):
    store, project, version, _ = saved
    private = tmp_path / "private.json"
    private.write_text('{"api_key":"must-not-be-read"}', encoding="utf-8")
    _change_artifact(store, project["id"], "json", private)
    with pytest.raises(ProjectError):
        ProjectService(store.base_dir).artifact(project["id"], version["id"], "json")


def test_unregistered_artifacts_are_inaccessible_even_if_the_file_exists(saved):
    store, project, version, _ = saved
    (store.version_dir(project["id"], version["id"]) / "private.json").write_text("{}", encoding="utf-8")
    with pytest.raises(KeyError):
        ProjectService(store.base_dir).artifact(project["id"], version["id"], "private")


def test_artifact_symlink_cannot_escape_the_version_root(saved, tmp_path):
    store, project, version, _ = saved
    secret = tmp_path / "secret.json"
    secret.write_text('{"secret":"outside"}', encoding="utf-8")
    link = store.version_dir(project["id"], version["id"]) / "redirect.json"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("The test process cannot create symbolic links on this Windows installation")
    _change_artifact(store, project["id"], "json", link.name)
    with pytest.raises(ProjectError):
        ProjectService(store.base_dir).artifact(project["id"], version["id"], "json")


def test_legacy_metadata_and_reports_do_not_expose_paths_or_private_payloads(saved):
    store, project, version, _ = saved
    secret_path = ('C:\\' + 'Users\\operator\\private\\backup.gxw')
    changed = store.get_project(project["id"])
    changed["versions"][0]["gx_sync"] = {"status": "imported", "backup_path": secret_path,
        "details": {"destination": secret_path, "_candidate_ir": {"secret": True}, "api_key": "unit-secret"}}
    store.save_project(changed)
    report = store.create_report(project["id"], {"report_type": "inspection", "summary": "工程检查",
        "status": "passed", "workspace_dir": secret_path, "provider_configuration": {"api_key": "unit-secret"},
        "items": [{"message": "请核对", "source": secret_path}], "_candidate_ir": {"hidden": True}})
    service = ProjectService(store.base_dir)
    wire = json.dumps([service.version(project["id"], version["id"]), service.report(project["id"], report["report_id"])], ensure_ascii=False)
    assert "unit-secret" not in wire
    assert "backup.gxw" not in wire
    assert "_candidate_ir" not in wire
    assert "workspace_dir" not in wire


def test_report_embedded_error_paths_are_redacted(saved):
    store, project, _, _ = saved
    report = store.create_report(project["id"], {"report_type": "inspection", "summary": "工程检查",
        "status": "failed", "messages": [('读取 C:\\' + 'Users\\operator\\private\\backup.gxw 时失败')]})
    wire = json.dumps(ProjectService(store.base_dir).report(project["id"], report["report_id"]), ensure_ascii=False)
    assert "backup.gxw" not in wire
    assert "读取" in wire


def test_attachment_upload_uses_managed_names_and_returns_frozen_image_values(saved, workbench):
    store, project, _, _ = saved
    uploaded = workbench.upload_attachment(project["id"], "../../drawing.png", base64.b64encode(_PNG).decode())
    assert uploaded["filename"] == "drawing.png"
    assert set(uploaded) == {"attachment_id", "filename", "media_type", "size_bytes"}
    images = workbench._attachments(project["id"], [uploaded["attachment_id"]])
    assert images[0].data == _PNG
    assert images[0].filename == "drawing.png"
    saved_attachment = json.loads((workbench.state_dir / "attachments" / (uploaded["attachment_id"] + ".json")).read_text(encoding="utf-8"))
    stored = store.attachments_dir(project["id"]) / saved_attachment["record"]["stored_name"]
    stored.write_bytes(_PNG + b"changed-after-snapshot")
    assert images[0].data == _PNG


def test_attachment_ids_cannot_cross_projects_or_escape_indexes(saved, workbench):
    store, project, _, _ = saved
    second = store.create_project("another")
    uploaded = workbench.upload_attachment(project["id"], "drawing.png", base64.b64encode(_PNG).decode())
    with pytest.raises(ValueError, match="another project"):
        workbench._attachments(second["id"], [uploaded["attachment_id"]])
    with pytest.raises(ValueError):
        workbench._attachments(project["id"], ["../escape"])
    index_path = workbench.state_dir / "attachments" / (uploaded["attachment_id"] + ".json")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["record"]["stored_name"] = "../private.png"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(ValueError):
        workbench._attachments(project["id"], [uploaded["attachment_id"]])


def test_submitted_job_keeps_attachment_bytes_and_inputs_from_submission(saved, workbench, monkeypatch):
    store, project, version, _ = saved
    uploaded = workbench.upload_attachment(project["id"], "drawing.png", base64.b64encode(_PNG).decode())
    captured = {}
    class CaptureJobs:
        def submit(self, kind, snapshot, worker, request_id):
            captured.update(kind=kind, snapshot=snapshot, worker=worker)
            return {"id": "job_capture"}
    class Context:
        def checkpoint(self):
            pass
    old_jobs = workbench.jobs
    workbench.jobs = CaptureJobs()
    def inspect_job(ctx, snapshot, context, images, provider):
        captured["executed_text"] = snapshot["text"]
        captured["executed_images"] = images
        return {"status": "completed"}
    monkeypatch.setattr(workbench, "_run_job", inspect_job)
    command = {"kind": "analysis", "project_id": project["id"], "version_id": version["id"],
        "request_id": "capture", "text": "submission input", "response_language": "zh-CN",
        "attachment_ids": [uploaded["attachment_id"]]}
    try:
        workbench.submit(command)
        command["text"] = "later mutation"
        index = json.loads((workbench.state_dir / "attachments" / (uploaded["attachment_id"] + ".json")).read_text(encoding="utf-8"))
        (store.attachments_dir(project["id"]) / index["record"]["stored_name"]).write_bytes(_PNG + b"later")
        captured["worker"](Context())
        assert captured["executed_text"] == "submission input"
        assert captured["executed_images"][0].data == _PNG
        assert "api_key" not in json.dumps(captured["snapshot"])
    finally:
        workbench.jobs = old_jobs


def test_saved_simulator_run_is_read_by_run_id_without_migrating_legacy_ir(saved):
    store, project, version, _ = saved
    suite = {"name": "offline fixture", "plc_model": "FX3U", "tests": [{"name": "idle",
        "plc_model": "FX3U", "initial": {"X0": 0}, "steps": [{"at_ms": 0, "expect": {"Y0": 0}}]}]}
    record = store.save_simulator_run(project["id"], version["id"], suite,
        {"status": "unavailable", "plc_model": "FX3U", "results": [], "counts": {"unavailable": 1}})
    changed = store.get_project(project["id"])
    changed["versions"][0]["artifacts"].pop("ir")
    store.save_project(changed)
    before = _bytes(store.base_dir)
    result = ProjectService(store.base_dir).simulator_run(project["id"], version["id"], record["run_id"])
    assert result["record"]["run_id"] == record["run_id"]
    assert result["result"]["status"] == "unavailable"
    assert _bytes(store.base_dir) == before


@pytest.mark.parametrize("field,bad_path", [("suite_artifact", "../../outside.json"),
                                           ("trace_artifact", "trace.json:private.json")])
def test_simulator_evidence_manifest_rejects_escape_and_ads_before_loading(saved, field, bad_path):
    store, project, version, _ = saved
    suite = {"name": "offline fixture", "plc_model": "FX3U", "tests": [{"name": "idle",
        "plc_model": "FX3U", "initial": {"X0": 0}, "steps": [{"at_ms": 0, "expect": {"Y0": 0}}]}]}
    run = store.save_simulator_run(project["id"], version["id"], suite,
        {"status": "unavailable", "plc_model": "FX3U", "results": [], "counts": {"unavailable": 1}})
    changed = store.get_project(project["id"])
    changed["versions"][0]["simulator_runs"][0][field] = bad_path
    store.save_project(changed)
    before = _bytes(store.base_dir)
    with pytest.raises(ProjectError):
        ProjectService(store.base_dir).simulator_run(project["id"], version["id"], run["run_id"])
    assert _bytes(store.base_dir) == before


def test_snapshot_rejects_valid_ir_changed_in_place_without_metadata_change(saved, workbench):
    store, project, version, program = saved
    snapshot = {"project_id": project["id"], "project": store.get_project(project["id"]),
                "version": copy.deepcopy(version), "program_ir": copy.deepcopy(program)}
    workbench._check_snapshot(snapshot)
    path = store.version_dir(project["id"], version["id"]) / version["artifacts"]["ir"]
    store._write_json(path, _program("X1"))
    assert store.get_version(project["id"], version["id"]) == version
    before = _bytes(store.base_dir)
    with pytest.raises(ConflictError):
        workbench._check_snapshot(snapshot)
    assert _bytes(store.base_dir) == before


@pytest.fixture
def settings_files(tmp_path, monkeypatch):
    import storage.config as config_manager
    import storage.credentials as credential_store
    import shared.paths as resource_paths
    config_path = tmp_path / "user" / "config.json"
    template = tmp_path / "config.default.json"
    config = {"language": "zh-CN", "activeModelProfileId": "fake", "modelProfiles": [{
        "id": "fake", "name": "Fake model", "adapter": "openai_compatible", "baseUrl": "https://example.invalid/v1",
        "model": "unit-model", "credentialTarget": "test-target", "capabilities": {"tools": True}}]}
    template.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(config_manager, "get_config_path", lambda: config_path)
    monkeypatch.setattr(resource_paths, "resource_path", lambda name: template)
    monkeypatch.setattr(credential_store, "read_api_key", lambda target: "sk-unit-secret")
    monkeypatch.setattr(config_manager, "load_full_config", lambda: pytest.fail("Read-only settings cannot migrate configuration"))
    monkeypatch.setattr(config_manager, "save_config", lambda *args: pytest.fail("Reading settings cannot write configuration"))
    monkeypatch.setattr(credential_store, "write_api_key", lambda *args: pytest.fail("Reading settings cannot write credentials"))
    return config_path, template, config


def test_settings_read_does_not_create_or_migrate_config_or_return_credentials(settings_files):
    path, template, _ = settings_files
    before = template.read_bytes()
    result = SettingsService().public_settings()
    assert result["profiles"][0]["configured"] is True
    assert result["profiles"][0]["model"] == "unit-model"
    assert "sk-unit-secret" not in json.dumps(result)
    assert "credentialTarget" not in json.dumps(result)
    assert not path.exists()
    assert not path.parent.exists()
    assert template.read_bytes() == before


def test_legacy_inline_credentials_and_unknown_capabilities_are_not_public(settings_files):
    path, _, config = settings_files
    profile = config["modelProfiles"][0]
    profile["api_key"] = "legacy-inline-secret"
    profile["requestOverrides"] = {"headers": {"Authorization": "Bearer legacy-inline-secret"}}
    profile["capabilities"]["api_key"] = "nested-capability-secret"
    path.parent.mkdir()
    path.write_text(json.dumps(config), encoding="utf-8")
    before = path.read_bytes()
    wire = json.dumps(SettingsService().public_settings())
    assert "legacy-inline-secret" not in wire
    assert "nested-capability-secret" not in wire
    assert path.read_bytes() == before


def _save_test_plan(store, project_id, version_id):
    suite = {"name": "Regression", "plc_model": "FX3U", "tests": [{"name": "Start output",
        "plc_model": "FX3U", "initial": {"X0": 0}, "steps": [{"at_ms": 5, "set": {"X0": 1}},
        {"at_ms": 10, "expect": {"Y0": 1}}], "trace_devices": ["X0", "Y0"], "sample_ms": 5, "timeout_ms": 20}]}
    return store.save_simulator_test_plan(project_id, version_id, suite)


def test_plan_read_and_pending_execution_proposal_do_not_migrate_legacy_workspace(saved, workbench):
    store, project, version, _ = saved
    plan = _save_test_plan(store, project["id"], version["id"])
    changed = store.get_project(project["id"])
    changed["versions"][0]["artifacts"].pop("ir")
    store.save_project(changed)
    before = _bytes(store.base_dir)
    loaded = workbench.projects.plan(project["id"], version["id"], plan["binding"]["plan_id"])
    assert loaded["binding"] == plan["binding"]
    proposal = workbench.execution_proposal({"action": "simulation", "project_id": project["id"],
        "version_id": version["id"], "plan_id": plan["binding"]["plan_id"], "request_id": "legacy-plan"})
    assert proposal["status"] == "pending"
    assert _bytes(store.base_dir) == before
    store.load_simulator_test_plan(project["id"], version["id"], plan["binding"]["plan_id"])
    store.load_program_ir(project["id"], version["id"], persist_legacy=True)
    assert _bytes(store.base_dir) == before
    store.migrate_program_ir(project["id"], version["id"])
    assert "ir" in store.get_version(project["id"], version["id"])["artifacts"]


@pytest.mark.parametrize("bad_path", ["../../outside.json", "plan.json:private.json", "C:/outside.json"])
def test_plan_manifest_rejects_escape_and_ads_before_store_load(saved, bad_path, monkeypatch):
    store, project, version, _ = saved
    plan = _save_test_plan(store, project["id"], version["id"])
    changed = store.get_project(project["id"])
    changed["versions"][0]["simulator_test_plans"][0]["plan_artifact"] = bad_path
    store.save_project(changed)
    service = ProjectService(store.base_dir)
    monkeypatch.setattr(service.store, "load_simulator_test_plan", lambda *args, **kwargs: pytest.fail("Unsafe plan reached storage"))
    before = _bytes(store.base_dir)
    with pytest.raises(ProjectError):
        service.plan(project["id"], version["id"], plan["binding"]["plan_id"])
    assert _bytes(store.base_dir) == before


def test_plan_validates_underlying_ir_manifest_before_loading(saved, monkeypatch):
    store, project, version, _ = saved
    plan = _save_test_plan(store, project["id"], version["id"])
    _change_artifact(store, project["id"], "ir", "../../outside.json")
    service = ProjectService(store.base_dir)
    monkeypatch.setattr(service.store, "load_simulator_test_plan", lambda *args, **kwargs: pytest.fail("Unsafe IR reached storage"))
    with pytest.raises(ProjectError):
        service.plan(project["id"], version["id"], plan["binding"]["plan_id"])


@pytest.fixture
def saved_debug_plan(tmp_path):
    from test_plc_debug_loop import _project_with_failure, _diagnosis, _patch, _knowledge
    from application.debug_loop import DebugPatchLoopService
    store, project_id, version_id, program, run_id = _project_with_failure(tmp_path)
    plan = DebugPatchLoopService(store, retriever=_knowledge).prepare_plan(project_id, version_id, run_id, _diagnosis(), _patch(program))
    saved = store.save_debug_plan(project_id, version_id, plan)
    return store, project_id, version_id, saved


def test_debug_plan_rebuilds_candidate_from_saved_evidence_without_writes(saved_debug_plan):
    store, project_id, version_id, saved = saved_debug_plan
    before = _bytes(store.base_dir)
    loaded = ProjectService(store.base_dir).plan(project_id, version_id, saved["plan_id"], kind="debug")
    assert loaded["candidate_ir"] == saved["candidate_ir"]
    assert loaded["source_run_id"] == saved["source_run_id"]
    assert _bytes(store.base_dir) == before


@pytest.mark.parametrize("change", ["project", "version", "candidate", "suite"])
def test_debug_plan_rejects_cross_version_or_changed_candidate(saved_debug_plan, change):
    store, project_id, version_id, plan = saved_debug_plan
    path = store.project_dir(project_id) / "debug" / "plans" / (plan["plan_id"] + ".json")
    altered = copy.deepcopy(plan)
    if change == "project":
        altered["project_id"] = "other_project"
    elif change == "version":
        altered["base_version_id"] = "v9999"
    elif change == "candidate":
        altered["candidate_ir"]["revision"] += 1
    else:
        altered["regression_suite"]["name"] = "Different suite"
    store._write_json(path, altered)
    before = _bytes(store.base_dir)
    with pytest.raises(ProjectError):
        ProjectService(store.base_dir).plan(project_id, version_id, plan["plan_id"], kind="debug")
    assert _bytes(store.base_dir) == before



def test_job_receipt_uses_submission_provider_when_global_selection_changes(workbench, saved, monkeypatch):
    import threading
    from types import SimpleNamespace
    from model_runtime.provider import OpenAICompatibleProvider, ModelRequest, UserMessage
    from model_runtime.usage import tool_executed
    from test_model_provider import _Client
    _, project, version, _ = saved
    client = _Client([SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK",tool_calls=[]))])])
    profile = {"id":"frozen", "name":"Submitted model", "adapter":"openai_compatible",
               "baseUrl":"https://test.invalid/v1", "model":"exact-original", "generationDefaults":{"temperature":.37}}
    provider = OpenAICompatibleProvider(profile,"private-key",client=client)
    current = [provider]
    workbench.model_factory = lambda: (current[0], {"profile_id":current[0].profile["id"],
        "profile_name":current[0].profile["name"], "model":current[0].profile["model"]})
    entered, release = threading.Event(), threading.Event()
    def run_job(ctx, snapshot, context, images, bound):
        entered.set()
        assert release.wait(5)
        list(bound.stream(ModelRequest((UserMessage("private-program-text"),),stream=False)))
        tool_executed("search_plc_manual", True)
        return {"status":"complete"}
    monkeypatch.setattr(workbench,"_run_job",run_job)
    submitted = workbench.submit({"kind":"agent","project_id":project["id"],"version_id":version["id"],
                                 "text":"查询手册", "response_language":"zh-CN", "request_id":"frozen-model-usage"})
    try:
        assert entered.wait(5)
        assert submitted["model_usage"]["model"] == "exact-original"
        current[0] = OpenAICompatibleProvider({**profile,"id":"replacement","model":"next-task-only"},"other-key",client=client)
    finally:
        release.set()
    workbench.jobs._futures[submitted["id"]].result(timeout=10)
    completed = workbench.jobs.get(submitted["id"])
    assert completed["status"] == "completed", completed
    assert completed["model_usage"]["model"] == client.completions.calls[0]["model"] == "exact-original"
    assert next(p for p in completed["model_usage"]["parameters"] if p["name"] == "temperature")["value"] == .37
    events = workbench.jobs.events(submitted["id"])
    request = next(e for e in events if e["event_type"] == "model_request")
    assert request["payload"] == completed["model_usage"]
    assert next(e for e in events if e["event_type"] == "tool_execution")["payload"] == {"name":"search_plc_manual","success":True}
    public = json.dumps([completed,events])
    assert "private-key" not in public and "private-program-text" not in public


def _saved_native_st_program(stage='saved'):
    import zipfile
    from tools.offline_inputs import private_path
    witness = json.loads(private_path('tests/fixtures/gxw_program_task_native.json').read_text(
        encoding='utf-8'))['visible_st_program_creation']
    with zipfile.ZipFile(private_path(witness['archive'])) as archive:
        path = witness['saved'] if stage == 'saved' else witness['rejected_saves'][0]['saved']
        return archive.read(path)


def _native_st_artifacts(candidate):
    root = Path(candidate['staging_dir'])
    return {name: (root / entry['path']).read_bytes() for name, entry in candidate['artifacts'].items()}


def test_saved_native_st_candidate_keeps_exact_body_project_and_source_bound_task(tmp_path):
    from application.native_st import prepare_candidate, validate_candidate
    raw = _saved_native_st_program()
    candidate = prepare_candidate(tmp_path / 'st-source', imported=raw,
        program_name='CalcOutput_ST.Program.pou', plc_model='FX3U')
    artifacts = _native_st_artifacts(candidate)
    source = validate_candidate(artifacts, plc_model='FX3U', program_name='CalcOutput_ST.Program.pou',
                                metadata=candidate['metadata'])
    assert candidate['target_mode'] == 'st' and set(artifacts) == {'gxw', 'st', 'write_report'}
    assert artifacts['gxw'] == raw and artifacts['st'] == b';'
    assert source['selected_program']['kind'] == 'program'
    assert source['selected_program']['task_bindings'] == [dict(resource='MAIN', task='Task_01', line=2)]
    assert candidate['metadata']['validation']['source_readback'] == 'matched'
    assert candidate['metadata']['validation']['gx_compile'] == candidate['metadata']['validation']['native_save'] == 'not_run'


@pytest.mark.parametrize('damage', ['body', 'gxw-body', 'gxw-cpu', 'gxw-task', 'gxw-ranges', 'gxw-options',
    'gxw-parameters', 'report-source', 'report-project',
    'metadata-model', 'metadata-program', 'metadata-task'])
def test_native_st_candidate_rejects_changed_text_source_cpu_task_or_metadata(tmp_path, damage):
    from application.native_st import prepare_candidate, validate_candidate
    from gxw.fb_authoring import _context
    from gxw.declarations import _string
    from gxw.project_metadata import read_task_source
    from gxw.text_pou import parse_st_pou, serialize_st_pou
    from tests.test_gxw_object_model import replay_source_records
    candidate = prepare_candidate(tmp_path / 'st-source', imported=_saved_native_st_program(),
        program_name='CalcOutput_ST.Program.pou', plc_model='FX3U')
    artifacts = _native_st_artifacts(candidate)
    metadata = copy.deepcopy(candidate['metadata'])
    if damage == 'body':
        artifacts['st'] += b'\r\n'
    elif damage.startswith('gxw-'):
        streams = _context(artifacts['gxw'], require_fbd=False)[1]
        if damage == 'gxw-body':
            name = 'CalcOutput_ST.Program.pou'
            value = serialize_st_pou(parse_st_pou(streams[name]), 'Y0 := X0;')
        elif damage == 'gxw-task':
            name = 'Task_01.tsk'
            value = bytearray(streams[name])
            value[read_task_source(value)['count_offset'] - 8] ^= 1
        elif damage in {'gxw-ranges', 'gxw-options'}:
            import struct
            from gxw.project_metadata import read_project_compile_options, read_project_system_variables
            name, = [logical for logical in streams if logical.endswith('.prj')]
            value = bytearray(streams[name])
            if damage == 'gxw-ranges':
                offset = read_project_system_variables(value)['offset']
                struct.pack_into('<II', value, offset, 2333, 2444)
            else:
                options = read_project_compile_options(value)
                offset = options['count_offset']
                values = [*options['values'][:-1], 0x12345678, 0xffffffff]
                end = offset + 4 + 4 * len(options['values'])
                value = value[:offset] + struct.pack('<I', len(values)) + struct.pack('<' + 'I' * len(values), *values) + value[end:]
        elif damage == 'gxw-parameters':
            name = 'Param.wpa'
            value = bytearray(streams[name])
            value[-1] ^= 1
        else:
            name, = [logical for logical in streams if logical.endswith('.prj')]
            old = streams[name]
            start = 4 + 2 * int.from_bytes(old[:4], 'little')
            end = start + 4 + 2 * int.from_bytes(old[start:start + 4], 'little')
            value = old[:start] + _string('L02') + old[end:]
        artifacts['gxw'] = replay_source_records(artifacts['gxw'], {name: value})
    elif damage in {'report-source', 'report-project'}:
        report = json.loads(artifacts['write_report'])
        if damage == 'report-source':
            report['source']['compiler_sources']['programs'][0]['body'] = 'ForeignSource'
        else:
            report['source']['project']['globals'] = []
        artifacts['write_report'] = json.dumps(report).encode('utf-8')
    elif damage == 'metadata-model':
        metadata['plc_model'] = 'L02'
    elif damage == 'metadata-program':
        metadata['program_name'] = 'POU_01.Program.pou'
    else:
        metadata['validation']['source_binding']['task_bindings'][0]['line'] = 99
    with pytest.raises(ValueError):
        validate_candidate(artifacts, plc_model='FX3U', program_name='CalcOutput_ST.Program.pou', metadata=metadata)


@pytest.mark.parametrize('case', ['text-only', 'unbound', 'fbd', 'owner-name', 'cpu'])
def test_native_st_projection_requires_saved_gxw_logical_ordinary_program_and_matching_cpu(tmp_path, case):
    from application.native_st import prepare_candidate
    raw = ';' if case == 'text-only' else _saved_native_st_program('unbound' if case == 'unbound' else 'saved')
    program = 'POU_01.Program.pou' if case == 'fbd' else 'CalcOutput_ST' if case == 'owner-name' else 'CalcOutput_ST.Program.pou'
    with pytest.raises(ValueError):
        prepare_candidate(tmp_path / 'st-source', imported=raw, program_name=program,
                          plc_model='L02' if case == 'cpu' else 'FX3U')
    assert not (tmp_path / 'st-source').exists()


def _saved_st_service_version(workbench):
    from application.native_st import prepare_candidate
    project = workbench.create_project(name='saved ordinary ST', target_mode='st', plc_model='FX3U')
    candidate = prepare_candidate(workbench.state_dir / 'staging' / 'native-st-base', imported=_saved_native_st_program(),
        program_name='CalcOutput_ST.Program.pou', plc_model='FX3U')
    proposal = workbench.proposals.create('accept_local', project['id'], candidate,
                                          request_id='saved-native-st-base')
    result = workbench.proposals.accept(proposal['id'])
    return project['id'], result['result']['version_id']


@pytest.mark.parametrize('damage', [None, 'text', 'metadata', 'cpu', 'mode', 'missing-gxw', 'base-artifact', 'active-version'])
def test_native_st_result_uses_frozen_candidate_transaction_and_rejects_stale_delivery(workbench, damage):
    from application.native_st import prepare_candidate
    from application.workspace import ConflictError
    project_id, base_id = _saved_st_service_version(workbench)
    source = workbench.projects.artifact(project_id, base_id, 'gxw').read_bytes()
    candidate = prepare_candidate(workbench.state_dir / 'staging' / 'native-st-result', imported=source,
        program_name='CalcOutput_ST.Program.pou', plc_model='FX3U')
    proposal = workbench.proposals.create('native_engineering', project_id,
        {'project_id': project_id, 'version_id': base_id, 'plan': {'schema_version': 1, 'commands': [{'operation': 'save'}]}},
        base_version_id=base_id, request_id='native-st-result')
    before = workbench.projects.raw_version(project_id, base_id)
    if damage == 'text':
        (Path(candidate['staging_dir']) / 'program.st').write_text('Foreign body', encoding='utf-8')
    elif damage == 'metadata':
        candidate['metadata']['validation']['source_binding']['task_bindings'][0]['line'] = 99
    elif damage == 'cpu':
        candidate['plc_model'] = 'L02'
    elif damage == 'mode':
        candidate['target_mode'] = 'fbd'
    elif damage == 'missing-gxw':
        candidate['artifacts'].pop('gxw')
    elif damage == 'base-artifact':
        workbench.projects.artifact(project_id, base_id, 'gxw').write_bytes(b'changed-after-approval')
    elif damage == 'active-version':
        project = workbench.store.get_project(project_id)
        project['active_version_id'] = None
        workbench.store.save_project(project)

    def complete(_payload, approved_id):
        result = workbench.proposals.complete_native_candidate(approved_id, candidate)
        return {**result, 'status': 'completed'}

    if damage:
        with pytest.raises((ValueError, ConflictError)):
            workbench.proposals.accept(proposal['id'], executor=complete)
        project = workbench.projects.raw_project(project_id)
        assert len(project['versions']) == 1
        assert project['active_version_id'] == (None if damage == 'active-version' else base_id)
        assert workbench.proposals.get(proposal['id'])['status'] in ('interrupted', 'conflict')
        return
    accepted = workbench.proposals.accept(proposal['id'], executor=complete)
    version_id = accepted['result']['version_id']
    assert version_id != base_id and accepted['status'] == 'accepted'
    assert workbench.projects.raw_project(project_id)['active_version_id'] == version_id
    assert workbench.projects.raw_version(project_id, base_id) == before
    assert workbench.projects.artifact(project_id, version_id, 'gxw').read_bytes() == source
    assert workbench.projects.artifact(project_id, version_id, 'st').read_bytes() == b';'
    context = workbench.projects.tool_context(project_id, version_id)
    assert context.native_snapshot == source and context.st_text == ';' and context.fbd_baseline is None
    assert workbench.projects.raw_version(project_id, version_id)['validation']['source_binding'] == candidate['metadata']['validation']['source_binding']


def test_plain_st_generation_candidate_keeps_existing_text_only_save_contract(workbench):
    import hashlib
    project = workbench.create_project(name='ordinary ST generated text', target_mode='st')
    directory = workbench.state_dir / 'staging' / 'plain-st'
    directory.mkdir(parents=True)
    (directory / 'program.st').write_bytes(b'Y0 := X0;')
    candidate = dict(target_mode='st', plc_model='FX3U', staging_dir=str(directory),
        artifacts={'st': {'path': 'program.st', 'sha256': hashlib.sha256(b'Y0 := X0;').hexdigest()}},
        metadata={'target_mode': 'st', 'plc_model': 'FX3U', 'validation': {'status': 'candidate_ready', 'native': 'unverified'}})
    proposal = workbench.proposals.create('accept_local', project['id'], candidate, request_id='plain-st-source')
    saved = workbench.proposals.accept(proposal['id'])
    version = workbench.projects.raw_version(project['id'], saved['result']['version_id'])
    assert version['artifacts'] == {'st': 'program.st'} and version['validation']['native'] == 'unverified'


def _complete_generation_read(workbench, project_id, version_id, result):
    from application.read_delivery import assemble_read_pages
    pages = [result.data]
    while not pages[-1]['data']['delivery']['complete']:
        followup = pages[-1]['data']['delivery']['next']
        result = workbench.projects.invoke(project_id, version_id, 'continue-read',
            followup['tool'], followup['arguments'])
        assert not result.is_error, result.data
        pages.append(result.data)
    return assemble_read_pages(pages)


def test_saved_st_generation_context_follows_the_version_after_an_fbd_project_changes_language(workbench):
    from application.native_st import prepare_candidate
    from tests.test_gxw_object_model import st_program_semantic_witness

    _, raw, text = st_program_semantic_witness(False)
    project = workbench.create_project(name='FBD project with current ST version', target_mode='fbd', plc_model='FX3U')
    candidate = prepare_candidate(workbench.state_dir / 'staging' / 'st-current-context', imported=raw,
        program_name='OwnedProgram_C2.Program.pou', plc_model='FX3U')
    proposal = workbench.proposals.create('accept_local', project['id'], candidate, request_id='current-st-context')
    accepted = workbench.proposals.accept(proposal['id'])
    version_id = accepted['result']['version_id']
    assert workbench.projects.raw_project(project['id'])['target_mode'] == 'fbd'
    assert workbench.projects.raw_version(project['id'], version_id)['target_mode'] == 'st'
    before = workbench.projects.raw_version(project['id'], version_id)
    result = workbench.projects.invoke(project['id'], version_id, 'saved-st-generation-context',
        'get_generation_context', {'execution_mode': 'background'})
    assert not result.is_error, result.data
    data = _complete_generation_read(workbench, project['id'], version_id, result)
    assert data['current_version_id'] == version_id and data['target_mode'] == 'st'
    selected = data['current_context']['pous'][int(data['selected_program_ref'].rsplit('/', 1)[1])]
    assert selected['program'] == data['current_context']['selected_source'] == 'OwnedProgram_C2.Program.pou'
    assert selected['st_text'] == text
    assert data['output_contract']['tool'] == 'create_st_candidate'
    assert data['output_contract']['operation'] == 'edit_st_program'
    assert data['current_context']['globals'] and data['current_context']['tasks']
    assert data['current_context']['configuration']['name'].endswith('.prj')
    wire = json.dumps(data)
    assert not any('"' + key + '":' in wire for key in ('storage', '_configuration_storage', 'source_offset', 'staging_dir'))
    assert 'base64' not in wire
    assert workbench.projects.raw_version(project['id'], version_id) == before
    assert workbench.projects.artifact(project['id'], version_id, 'gxw').read_bytes() == raw


def test_plain_st_generation_context_keeps_the_text_protocol_when_no_saved_native_project_exists(workbench):
    project = workbench.create_project(name='plain ST generation context', target_mode='st', plc_model='FX3U')
    result = workbench.projects.invoke(project['id'], '', 'plain-st-generation-context',
        'get_generation_context', {'execution_mode': 'background'})
    assert not result.is_error, result.data
    data = _complete_generation_read(workbench, project['id'], '', result)
    assert data['target_mode'] == 'st' and data['output_contract']['format'] == 'st_body_json'
    assert 'current_context' not in data and 'selected_program' not in data


@pytest.mark.parametrize('status', ['error', 'unavailable'])
def test_live_start_failure_retains_coordinator_cause_without_inventing_a_session(workbench, status):
    result = workbench._complete_live_engineering({}, 'proposal-not-started', {
        'status': status, 'passed': False, 'message': 'GX desktop resource is owned by another process.',
    })
    assert result == {'mode': 'live', 'status': status, 'passed': False,
                      'message': 'GX desktop resource is owned by another process.'}
    assert 'session_id' not in result and 'version_id' not in result


@pytest.mark.parametrize('existing_owner', [False, True])
def test_native_st_candidate_diff_uses_same_program_in_fbd_baseline_without_st_artifact(workbench, existing_owner):
    from application.fbd import prepare_candidate as prepare_fbd
    from application.native_st import prepare_candidate as prepare_st
    from gxw.fb_authoring import _context
    from gxw.text_pou import parse_st_pou, serialize_st_pou
    from tests.test_gxw_object_model import replay_source_records
    from tools.offline_inputs import private_path
    source = _saved_native_st_program()
    baseline = source if existing_owner else private_path(
        'research/experiments/native-live-session-20261018/baseline.gxw').read_bytes()
    project = workbench.create_project(name='FBD source with ordinary ST creation', plc_model='FX3U', target_mode='fbd')
    base = prepare_fbd(workbench.state_dir / 'staging' / 'mixed-base', imported=baseline,
                       program_name='POU_01.Program.pou', plc_model='FX3U')
    proposal = workbench.proposals.create('accept_local', project['id'], base, request_id='mixed-st-base')
    base_id = workbench.proposals.accept(proposal['id'])['result']['version_id']
    assert 'st' not in workbench.projects.raw_version(project['id'], base_id)['artifacts']
    logical = 'CalcOutput_ST.Program.pou'
    body = _context(source, require_fbd=False)[1][logical]
    edited = replay_source_records(source, {logical: serialize_st_pou(parse_st_pou(body), 'Y0 := X0;')})
    candidate = prepare_st(workbench.state_dir / 'staging' / 'mixed-st-after', imported=edited,
                           program_name=logical, plc_model='FX3U')
    diff = workbench._candidate_diff(project['id'], base_id, candidate)
    assert diff['before'] == (';' if existing_owner else '')
    assert diff['after'] == 'Y0 := X0;' and diff['has_changes']


@pytest.mark.parametrize('fault', ['version', 'project'])
def test_native_st_delivery_write_failure_never_activates_or_exposes_a_half_version(workbench, monkeypatch, fault):
    from application.native_st import prepare_candidate

    project_id, base_id = _saved_st_service_version(workbench)
    source = workbench.projects.artifact(project_id, base_id, 'gxw').read_bytes()
    candidate = prepare_candidate(workbench.state_dir / 'staging' / 'native-write-failure',
        imported=source, program_name='CalcOutput_ST.Program.pou', plc_model='FX3U')
    proposal = workbench.proposals.create('native_engineering', project_id, {
        'project_id': project_id, 'version_id': base_id,
        'plan': {'schema_version': 1, 'commands': [{'operation': 'save'}]}},
        base_version_id=base_id, request_id='native-write-failure')
    before = workbench.store.get_project(project_id)
    project_bytes = workbench.store.project_path(project_id).read_bytes()
    write, version_paths = workbench.store._write_json, []

    def fail_write(path, value):
        path = Path(path)
        if path.name == 'version.json':
            version_paths.append(path)
            assert workbench.store.get_project(project_id) == before
            if fault == 'version':
                raise OSError('injected native version save failure')
        elif path == workbench.store.project_path(project_id):
            assert version_paths and version_paths[0].is_file()
            if fault == 'project':
                raise OSError('injected native project save failure')
        return write(path, value)

    monkeypatch.setattr(workbench.store, '_write_json', fail_write)
    save_record = workbench.store._save_project_record
    def fail_project(value):
        assert version_paths and version_paths[0].is_file()
        if fault == 'project':
            raise OSError('injected native project save failure')
        return save_record(value)
    monkeypatch.setattr(workbench.store, '_save_project_record', fail_project)
    def deliver(_payload, approved_id):
        saved = workbench.proposals.complete_native_candidate(approved_id, candidate)
        return {**saved, 'status': 'completed'}
    with pytest.raises(OSError, match=f'injected native {fault} save failure'):
        workbench.proposals.accept(proposal['id'], executor=deliver)
    assert workbench.store.get_project(project_id) == before
    assert workbench.store.project_path(project_id).read_bytes() == project_bytes
    assert workbench.projects.project(project_id)['version_count'] == 1
    assert workbench.projects.raw_project(project_id)['active_version_id'] == base_id
    assert workbench.projects.artifact(project_id, base_id, 'gxw').read_bytes() == source
    assert workbench.store.get_version(project_id, version_paths[0].parent.name) is None
    assert not version_paths[0].parent.exists()
    assert workbench.proposals.get(proposal['id'])['status'] == 'interrupted'
    reopened = SessionStore(base_dir=workbench.store.base_dir, create=False)
    assert reopened.get_project(project_id) == before


@pytest.mark.parametrize('code,source,expected', [
    ('C9215', 'IF D0 < 0 THEN D10:=1; END_IF;', 'passed'),
    ('C9146', 'VAR r:REAL; END_VAR r:=0.0 ** 3.0;', 'failed'),
    ('C9999', 'D10:=1;', 'unverified'),
])
def test_native_warning_review_keeps_candidate_static_fields_consistent(code, source, expected):
    from application.native_st import bind_verification
    from tests.test_plc_core_boundary import native_warning_receipt
    snapshot = {'cpu': 'FX3U/FX3UC', 'project': {},
        'selected_program': {'program': 'MAIN.Program.pou', 'name': 'MAIN', 'task_bindings': [], 'st_text': source}}
    metadata = {'plc_model': 'FX3U', 'validation': {}}
    inspection = bind_verification(metadata, snapshot, compile_status='passed',
        native_receipt=native_warning_receipt(code))
    assert inspection['native_compile'] == metadata['validation']['native_compile'] == 'passed'
    assert inspection['native_warning_review']['status'] == expected
    assert inspection['semantic'] == inspection['status'] == metadata['validation']['semantic'] == expected
    assert inspection['valid'] is (expected == 'passed')
    if expected != 'passed':
        assert any(f['code'] == 'ST_NATIVE_WARNING' for f in inspection['findings'])


def test_session_records_read_legacy_without_migration_and_split_on_explicit_write(tmp_path):
    from storage.session import SessionStore as Records
    store = Records(tmp_path / "workspace")
    project = store.create_project(name="Compatibility")
    legacy = {**project, "messages": [{"id": "m0", "content": "fixture"}], "versions": [
        {"id": "v0001", "simulator_runs": [{"run_id": "r1"}], "multi_agent_runs": [{"run_id": "a1"}]}]}
    store._write_json(store.project_path(project["id"]), legacy)
    before = _bytes(store.base_dir)
    assert store.get_project(project["id"])["messages"] == legacy["messages"]
    assert _bytes(store.base_dir) == before
    store.add_message(project["id"], "user", "new fixture")
    loaded = store.get_project(project["id"])
    assert [row["content"] for row in loaded["messages"]] == ["fixture", "new fixture"]
    assert loaded["versions"][0]["simulator_runs"] == [{"run_id": "r1"}]
    assert loaded["versions"][0]["multi_agent_runs"] == [{"run_id": "a1"}]
    assert set(json.loads(store.project_path(project["id"]).read_text())) == {"id", "storage_format"}


def test_message_append_does_not_read_or_rewrite_project_history(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from storage.session import SessionStore as Records
    store = Records(tmp_path / "workspace")
    project = store.create_project(name="Append")
    manifest = store.project_path(project["id"]).read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("message append loaded or rewrote full project history")
    monkeypatch.setattr(store, "get_project", forbidden)
    monkeypatch.setattr(store, "save_project", forbidden)
    with ThreadPoolExecutor(max_workers=4) as executor:
        messages = list(executor.map(lambda i: store.add_message(project["id"], "user", str(i)), range(24)))
    reopened = Records(store.base_dir, create=False).get_project(project["id"])
    assert len({row["id"] for row in reopened["messages"]}) == len(messages) == 24
    assert {row["content"] for row in reopened["messages"]} == {str(i) for i in range(24)}
    assert store.project_path(project["id"]).read_bytes() == manifest


def test_project_record_transaction_rolls_back_metadata_and_collections(tmp_path, monkeypatch):
    from storage.session import SessionStore as Records
    from storage import project_records
    store = Records(tmp_path / "workspace")
    project = store.create_project(name="Before")
    before = store.get_project(project["id"])
    changed = copy.deepcopy(before)
    changed.update(name="After", active_version_id="v0001", messages=[{"id": "new"}], versions=[{"id": "v0001"}])
    original = project_records._replace_rows
    def fail_mid_transaction(connection, collection, values):
        original(connection, collection, values)
        if collection == "versions":
            raise OSError("injected transaction failure")
    monkeypatch.setattr(project_records, "_replace_rows", fail_mid_transaction)
    with pytest.raises(OSError, match="injected transaction"):
        store.save_project(changed)
    assert store.get_project(project["id"]) == before


@pytest.mark.parametrize("action", ["read", "save", "append"])
def test_project_record_database_failure_is_reported_without_replacing_data(tmp_path, action):
    from storage.session import SessionStore as Records
    store = Records(tmp_path / "workspace")
    project = store.create_project(name="Retain failed records")
    database = store.project_dir(project["id"]) / "records.sqlite3"
    database.write_bytes(b"corrupt database fixture")
    before = _bytes(store.base_dir)
    with pytest.raises(OSError, match="Cannot access project records"):
        if action == "read":
            store.get_project(project["id"])
        elif action == "save":
            store.save_project(project)
        else:
            store.add_message(project["id"], "user", "fixture")
    assert _bytes(store.base_dir) == before

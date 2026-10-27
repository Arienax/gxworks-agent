"""Isolated GX execution ownership tests: no vendor software or desktop writes."""

import copy
import ast
import json
import os
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

from application.execution import DesktopResourceLock, GXExecutionCoordinator, read_environment
from gxworks2.project_profiles import NATIVE_PROJECT_PROFILES


class FakeStore:
    def __init__(self, root):
        self.root = root
        self.active = "v1"
        self.version = {"id": "v1", "revision": 1, "artifacts": {"program_csv": "program.csv", "comment_csv": "comments.csv"}}
        directory = self.version_dir("p1", "v1")
        directory.mkdir(parents=True)
        (directory / "program.csv").write_text("program", encoding="utf-8")
        (directory / "comments.csv").write_text("comments", encoding="utf-8")

    def get_project(self, project_id):
        return {"id": project_id, "active_version_id": self.active}

    def get_version(self, project_id, version_id):
        return copy.deepcopy(self.version)

    def project_dir(self, project_id):
        return self.root / project_id

    def version_dir(self, project_id, version_id):
        return self.project_dir(project_id) / version_id


class FakeCOM:
    def __init__(self, events):
        self.events = events

    def CoInitialize(self):
        self.events.append(("com_init", threading.get_ident()))

    def CoUninitialize(self):
        self.events.append(("com_uninit", threading.get_ident()))


def native_delivery_receipt(raw, commands):
    """A controlled controller receipt; no vendor process is created."""
    references = {'status': 'matched', 'queries': []}
    steps = []
    for index, command in enumerate(commands):
        verification = {'status': 'matched'}
        if command['operation'] == 'show_assignments':
            references = {'status': 'matched', 'queries': [{'query': copy.deepcopy(query)} for query in command['queries']]}
            verification['native_cross_references'] = references
        steps.append({'index': index, 'operation': command['operation'], 'status': 'completed',
                      'revision': 0, 'verification': verification,
                      'observation': {'evidence_directory': r'D:\private\native-evidence', 'raw': b'private'}})
    requested = {command['operation'] for command in commands}
    return {'status': 'completed', 'passed': True, 'data': raw, 'data_status': 'last_verified_source',
        'revision': 0, 'steps': steps,
        'compile': {'status': 'passed' if 'compile' in requested else 'not_run', 'revision': 0,
                    'errors': 0, 'warnings': 0, 'check_warnings': 0, 'output': r'D:\private\compiler-output'},
        'assignments': {'status': 'current' if 'show_assignments' in requested else 'not_run',
            'revision': 0, 'origin': 'current_gui_compile_saved_snapshot', 'source_usage': 'unverified',
            'native_cross_references': references},
        'save': {'status': 'passed' if 'save' in requested else 'not_run', 'revision': 0},
        'reopen': {'status': 'passed' if 'reopen' in requested else 'not_run', 'revision': 0},
        'identity': {'project_path': r'D:\private\managed.gxw', 'process_id': 123},
        'execution': 'not_run'}


def native_st_source_witness(*, kind='array'):
    """Frozen ordinary ST source/task evidence; this is not a compile proof."""
    import zipfile
    from tools.offline_inputs import private_path
    witness = json.loads(private_path('tests/fixtures/gxw_program_task_native.json').read_text(
        encoding='utf-8'))['visible_st_program_' + kind]
    assert witness['compilation'] == witness['PLC_execution'] == 'not_run'
    with zipfile.ZipFile(private_path(witness['archive'])) as archive:
        return archive.read(witness['saved'])


@pytest.fixture
def store(tmp_path):
    return FakeStore(tmp_path / "workspace")


def coordinator(store, tmp_path, importer, events):
    return GXExecutionCoordinator(
        store, resource_lock_path=tmp_path / "desktop.lock",
        dependencies_factory=lambda operation: {"importer": importer},
        com_factory=lambda: FakeCOM(events),
    )


@pytest.mark.parametrize('mode', ['live', 'batch'])
@pytest.mark.parametrize('cpu', [None, '', '   ', True, 17])
def test_native_execution_rejects_missing_explicit_cpu_before_artifacts_or_native_factories(
        store, tmp_path, mode, cpu):
    version = {**store.version, 'target_mode': 'fbd', 'plc_model': cpu}
    plan = dict(schema_version=1, commands=[dict(operation='save')])
    if mode == 'live':
        plan['mode'] = 'live'
    def forbidden(*args, **kwargs):
        pytest.fail('Missing CPU reached a desktop factory or artifact lookup')
    service = GXExecutionCoordinator(store, resource_lock_path=tmp_path / 'unused.lock',
        dependencies_factory=forbidden, com_factory=forbidden)
    with pytest.raises(ValueError, match='explicit saved project CPU'):
        service._native_engineering(dict(project_id='p1', version_id='v1', plan=plan),
                                    version, tmp_path / 'missing-artifacts', 'p1', None)
    assert service._thread is None and not (tmp_path / 'unused.lock').exists()


@pytest.mark.parametrize('version_cpu,project_cpu,expected', [
    ('Q03UDV', 'FX3U', 'Q03UDV'), ('L02', 'FX3U', 'L02'), (None, 'Q02H', 'Q02H'),
    ('FX3UC', 'L02', 'FX3UC')])
def test_native_live_start_preserves_explicit_saved_cpu_precedence_without_defaulting(
        store, tmp_path, monkeypatch, version_cpu, project_cpu, expected):
    monkeypatch.setattr(store, 'get_project', lambda project_id: dict(id=project_id, plc_model=project_cpu))
    service = GXExecutionCoordinator(store)
    calls = []
    monkeypatch.setattr(service, '_live_native_engineering',
        lambda payload, version, root, project_id, cpu, progress: calls.append(cpu) or dict(selected_cpu=cpu))
    version = {**store.version, 'target_mode': 'fbd', 'plc_model': version_cpu}
    payload = dict(project_id='p1', version_id='v1', plan=dict(schema_version=1, mode='live', commands=[]))
    assert service._native_engineering(payload, version, tmp_path, 'p1', None) == dict(selected_cpu=expected)
    assert calls == [expected] and service._thread is None


@pytest.mark.parametrize('cpu', ('Q03UDV', 'L02', 'FX3G'))
def test_native_live_start_reaches_owned_ui_factory_with_an_unverified_saved_mirror(store, tmp_path, cpu):
    from application.fbd import prepare_candidate
    from gxw.project_metadata import project_gui_configuration
    from gxw.project_templates import blank_fbd_baseline
    raw = blank_fbd_baseline(cpu)
    prepared = prepare_candidate(store.version_dir('p1', 'v1'), imported=raw, plc_model=cpu)
    store.version.update(prepared['metadata'], id='v1',
        artifacts={key: row['path'] for key, row in prepared['artifacts'].items()})
    reached, events = [], []
    def observe_factory(path, actual_cpu, **options):
        reached.append((actual_cpu, path.read_bytes()))
        raise RuntimeError('controlled stop before opening the native UI')
    coordinator = GXExecutionCoordinator(store,
        dependencies_factory=lambda _: dict(driver_factory=observe_factory))
    coordinator._worker_com = FakeCOM(events)
    payload = dict(project_id='p1', version_id='v1',
        plan=dict(schema_version=1, mode='live', commands=[]))
    with pytest.raises(RuntimeError, match='controlled stop before opening'):
        coordinator._live_native_engineering(payload, store.version, store.version_dir('p1', 'v1'),
                                             'p1', cpu, None)
    assert reached == [(cpu, raw)]
    assert project_gui_configuration(raw)['status'] == 'not_verified'
    assert [row[0] for row in events] == ['com_init', 'com_uninit']
    assert coordinator._thread is None and coordinator._live_sessions == {}


@pytest.mark.parametrize('plan,cpu', [
    ({'schema_version': 1, 'commands': [{'operation': 'save', 'pid': 42}]}, 'FX3U'),
    ({'schema_version': 1, 'commands': [{'operation': 'save'}], 'path': r'D:\user.gxw'}, 'FX3U'),
    ({'schema_version': 1, 'commands': [{'operation': 'create_structure', 'source_name': 'Pair', 'members': []}]}, 'FX3U'),
    ({'schema_version': 1, 'commands': [{'operation': 'configure_project', 'source_name': 'P.prj', 'options': {'program_capacity': 64000}}]}, 'FX3U'),
    ({'schema_version': 1, 'commands': [{'operation': 'configure_project', 'source_name': 'P.prj', 'options': {'system_variable_ranges': {'T': [100, 199]}}}]}, 'FX3U'),
    ({'schema_version': 1, 'commands': [{'operation': 'show_assignments', 'queries': []}]}, 'FX3U'),
    ({'schema_version': 1, 'commands': [{'operation': 'save'}]}, 'FX3UC'),
    ({'schema_version': 1, 'commands': [{'operation': 'save'}]}, 'Q03UDV'),
    ({'schema_version': True, 'commands': [{'operation': 'save'}]}, 'FX3U'),
])
def test_native_plan_ingress_rejects_process_paths_and_unverified_capabilities(plan, cpu):
    from application.native_engineering import normalize_native_plan
    with pytest.raises(ValueError):
        normalize_native_plan(plan, plc_model=cpu)


def test_native_product_preflight_rejects_uninitialized_gui_ranges_without_rewriting_source():
    from application.native_engineering import validate_native_baseline
    from gxw.object_model import default_baseline
    raw = default_baseline()
    with pytest.raises(ValueError, match='allocation configuration'):
        validate_native_baseline(raw, plc_model='FX3U')
    assert raw == default_baseline()


@pytest.mark.parametrize('operation,rows', [
    ('create_structure', [{'name': 'Factor', 'data_type': 'INT', 'initial_value': '2'}]),
    ('edit_global_labels', [{'name': 'Factor', 'data_type': 'INT', 'initial_value': '2'}]),
    ('edit_pou_labels', [{'name': 'Ready', 'data_type': 'BOOL', 'initial_value': 'TRUE'}]),
])
def test_native_product_initial_values_are_rejected_by_core_before_any_approved_session(operation, rows):
    from application.native_engineering import normalize_native_plan
    command = {'operation': operation, 'source_name': 'Source',
               'members' if operation == 'create_structure' else 'declarations': rows}
    with pytest.raises(ValueError, match='nondefault'):
        normalize_native_plan({'schema_version': 1, 'commands': [command]}, plc_model='FX3U')


def test_native_product_supports_witnessed_default_structure_and_explicit_constants():
    from application.native_engineering import normalize_native_plan
    plan = {'schema_version': 1, 'commands': [
        {'operation': 'create_structure', 'source_name': 'DemoDefaults', 'members': [
            {'name': 'Factor', 'data_type': 'INT', 'initial_value': '0'},
            {'name': 'Ready', 'data_type': 'BOOL', 'initial_value': 'FALSE'}]},
        {'operation': 'create_global_labels', 'source_name': 'Constants', 'declarations': [
            {'name': 'Gain', 'data_type': 'INT', 'class_name': 'VAR_GLOBAL_CONSTANT', 'initial_value': '2'}]},
        {'operation': 'save'}, {'operation': 'reopen'}]}
    frozen = normalize_native_plan(plan, plc_model='FX3U')
    assert frozen == plan and frozen is not plan and frozen['commands'] is not plan['commands']


def test_native_failure_summary_remains_whitelisted_and_keeps_counts_across_transport_projection():
    from application.native_engineering import native_engineering_summary
    receipt = native_delivery_receipt(b'private-source', [{'operation': 'compile'}])
    receipt.update(status='interrupted', passed=False, unverified_data=b'private-observed')
    receipt['compile'].update(status='failed', errors=2, warnings=3, check_warnings=4)
    receipt['steps'][0]['status'] = 'failed'
    summary = native_engineering_summary(receipt)
    assert native_engineering_summary(summary) == summary
    assert summary['gx_compile_status'] == 'failed' and summary['counts'] == {
        'completed': 0, 'total': 1, 'error': 2, 'warning': 3, 'info': 4}
    assert 'private' not in json.dumps(summary) and 'data' not in summary


@pytest.mark.parametrize('commands', [
    [{'operation': 'compile'}, {'operation': 'create_structure', 'source_name': 'Pair',
      'members': [{'name': 'Value', 'data_type': 'INT'}]}],
    [{'operation': 'save'}, {'operation': 'edit_global_labels', 'source_name': 'Global1', 'declarations': []}],
    [{'operation': 'reopen'}, {'operation': 'edit_pou_labels', 'source_name': 'POU_01', 'declarations': []}],
    [{'operation': 'configure_project', 'source_name': 'Current.prj',
      'options': {'system_variable_ranges': {'D': [7999, 1000]}}}],
    [{'operation': 'configure_project', 'source_name': 'Current.prj',
      'options': {'system_variable_ranges': {'D': [2000, 8000]}}}],
])
def test_native_batch_rejects_stale_final_layers_and_invalid_core_ranges_before_desktop_start(commands):
    from application.native_engineering import normalize_native_plan
    with pytest.raises(ValueError):
        normalize_native_plan({'schema_version': 1, 'commands': commands}, plc_model='FX3U')


@pytest.mark.parametrize('target_mode', ['fbd', 'st'])
def test_native_engineering_uses_managed_copy_in_the_approved_fixed_com_lock(store, tmp_path, target_mode):
    if target_mode == 'st':
        from application.native_st import prepare_candidate
        raw = native_st_source_witness()
        project_filename = 'STOnly.gxw'
    else:
        from application.fbd import prepare_candidate
        from tests.test_gxworks2_live_boundaries import _visible_cross_reference_witness
        raw = _visible_cross_reference_witness()
        project_filename = 'Live.gxw'
    baseline_path = store.version_dir('p1', 'v1')
    prepared = prepare_candidate(baseline_path, imported=raw, program_name='POU_01.Program.pou', plc_model='FX3U')
    store.version.update(prepared['metadata'], artifacts={key: row['path'] for key, row in prepared['artifacts'].items()})
    events, created = [], []
    commands = [{'operation': 'save'}, {'operation': 'reopen'}]
    path = tmp_path / 'desktop.lock'
    def driver_factory(filename, cpu, **options):
        with pytest.raises(Exception, match='owned by another process'), DesktopResourceLock(path):
            pass
        assert filename != baseline_path / 'program.gxw'
        assert filename.parent.parent.name == 'gxw_runs' and filename.name == project_filename
        assert filename.read_bytes() == raw and cpu == 'FX3U'
        assert set(options) == {'timeout', 'progress', 'evidence_directory'}
        assert options['timeout'] == 30 and options['progress'] is None
        created.append(filename)
        events.append(('driver', threading.get_ident()))
        return object()
    class Session:
        def __init__(self, driver, expected_cpu, progress):
            assert expected_cpu == 'FX3U'
            self.progress = progress
        def run(self, selected, baseline):
            assert selected == commands and baseline == raw
            events.append(('run', threading.get_ident()))
            return native_delivery_receipt(raw, selected)
    with GXExecutionCoordinator(store, resource_lock_path=path,
            dependencies_factory=lambda operation: {'driver_factory': driver_factory, 'session_factory': Session},
            com_factory=lambda: FakeCOM(events)) as service:
        result = service.submit_approved('native_engineering', {'project_id': 'p1', 'version_id': 'v1',
            'plan': {'schema_version': 1, 'commands': commands}}, approval_id='approved-native').result(15)
    assert result['status'] == 'completed', result
    assert result['_native_result']['data'] == raw and result['approval_id'] == 'approved-native'
    assert len(created) == 1 and (baseline_path / 'program.gxw').read_bytes() == raw
    diagnostic = json.loads((created[0].parent / 'receipt.json').read_text(encoding='utf-8'))
    assert diagnostic['data_status'] == 'last_verified_source'
    assert 'data' not in diagnostic and 'unverified_data' not in diagnostic
    assert diagnostic['steps'][0]['observation'] == {'evidence_directory': r'D:\private\native-evidence'}
    assert [name for name, _ in events] == ['com_init', 'driver', 'run', 'com_uninit']
    assert len({thread for _, thread in events}) == 1 and events[0][1] != threading.get_ident()


def test_native_observed_progress_is_readable_and_excludes_private_native_details(store, tmp_path):
    from application.fbd import prepare_candidate
    from tests.test_gxworks2_live_boundaries import _visible_cross_reference_witness
    raw = _visible_cross_reference_witness()
    prepared = prepare_candidate(store.version_dir('p1', 'v1'), imported=raw, program_name='POU_01.Program.pou')
    store.version.update(prepared['metadata'], artifacts={key: row['path'] for key, row in prepared['artifacts'].items()})
    received = []
    class Session:
        def __init__(self, _driver, expected_cpu, progress):
            self.observed = progress
        def run(self, commands, baseline):
            for status in ('executing', 'completed', 'failed', 'interrupted', r'D:\private\status'):
                self.observed({'operation': 'create_structure', 'status': status,
                    'message': r'D:\private\native-error', 'path': r'D:\private\project.gxw',
                    'observation': {'output': 'private native text'}})
            self.observed({'operation': r'D:\private\operation', 'status': 'executing'})
            return native_delivery_receipt(raw, commands)
    with GXExecutionCoordinator(store, resource_lock_path=tmp_path / 'native.lock',
            dependencies_factory=lambda operation: {'driver_factory': lambda *_args, **_options: object(), 'session_factory': Session},
            com_factory=lambda: FakeCOM([])) as service:
        result = service.submit_approved('native_engineering', {'project_id': 'p1', 'version_id': 'v1',
            'plan': {'schema_version': 1, 'commands': [{'operation': 'save'}]}},
            approval_id='approved-readable-progress', progress=received.append).result(15)
    assert result['status'] == 'completed', result
    assert received == ['正在建立结构类型。', '已完成：建立结构类型。', '建立结构类型未完成。',
                        '已中止建立结构类型。', '尚未确认建立结构类型是否完成。']
    assert all(isinstance(message, str) and 'private' not in message and 'create_structure' not in message
               and '[' not in message for message in received)
    assert result['_native_result']['steps'][0]['operation'] == 'save'


@pytest.mark.parametrize('stage', ['factory', 'session'])
def test_native_run_exception_records_private_diagnostics_without_binary_payloads(store, tmp_path, stage):
    from application.fbd import prepare_candidate
    from application.native_engineering import native_engineering_summary
    from tests.test_gxworks2_live_boundaries import _visible_cross_reference_witness
    raw = _visible_cross_reference_witness()
    baseline = store.version_dir('p1', 'v1')
    prepared = prepare_candidate(baseline, imported=raw, program_name='POU_01.Program.pou')
    store.version.update(prepared['metadata'], artifacts={key: row['path'] for key, row in prepared['artifacts'].items()})
    class Failure(RuntimeError):
        observation = {'dialog': '无法打开工程', 'path': r'D:\private\managed.gxw',
                       'binary': b'private-binary', 'nested': [bytearray(b'private'), {'data': b'source', 'status': 'observed'}]}
    def fail():
        raise Failure(r'Failed native open D:\private\managed.gxw')
    def factory(*_args, **_options):
        return fail() if stage == 'factory' else types.SimpleNamespace(close=lambda: None)
    class Session:
        def __init__(self, *_args, **_options):
            pass
        def run(self, *_args, **_options):
            fail()
    with GXExecutionCoordinator(store, resource_lock_path=tmp_path / 'native.lock',
            dependencies_factory=lambda operation: {'driver_factory': factory, 'session_factory': Session},
            com_factory=lambda: FakeCOM([])) as service:
        result = service.submit_approved('native_engineering', {'project_id': 'p1', 'version_id': 'v1',
            'plan': {'schema_version': 1, 'commands': [{'operation': 'save'}]}}, approval_id='approved-failure').result(15)
    assert result['status'] == 'error' and result['passed'] is False
    failure_path, = (store.project_dir('p1') / 'gxw_runs').glob('*/failure.json')
    diagnostic = json.loads(failure_path.read_text(encoding='utf-8'))
    assert diagnostic['type'] == 'Failure' and diagnostic['message'].endswith(r'D:\private\managed.gxw')
    assert diagnostic['observation'] == {'dialog': '无法打开工程', 'path': r'D:\private\managed.gxw',
                                        'nested': [{'status': 'observed'}]}
    assert not failure_path.with_name('receipt.json').exists()
    assert 'private' not in json.dumps(native_engineering_summary(result))
    assert (baseline / 'program.gxw').read_bytes() == raw


@pytest.mark.parametrize('name', ['../escape.prj', r'folder\escape.prj', 'C:escape.prj',
    'NUL.prj', 'COM1.prj', 'name?.prj', '.prj', ('a' * 31) + '.prj', 'name .prj', 'name..prj'])
def test_native_managed_copy_rejects_unsafe_or_unsupported_saved_project_identities(name):
    from application.execution import _native_engineering_filename
    with pytest.raises(ValueError, match='managed GX filename'):
        _native_engineering_filename({'configuration': {'name': name}})


@pytest.mark.parametrize('damage', ['compile', 'save', 'reopen', 'query', 'step', 'data_status', 'unverified'])
def test_native_result_delivery_rechecks_requested_final_layers_without_public_raw_receipt(damage):
    from application.native_engineering import verified_native_data, native_engineering_summary
    from application.workspace import public_payload
    commands = [{'operation': 'compile'}, {'operation': 'show_assignments',
        'queries': [{'scope': 'global', 'source': 'Global1.gh', 'name': 'Result'}]},
        {'operation': 'save'}, {'operation': 'reopen'}]
    receipt = native_delivery_receipt(b'saved-source', commands)
    if damage in ('compile', 'save', 'reopen'):
        receipt[damage]['revision'] = 999
    elif damage == 'query':
        receipt['steps'][1]['verification']['native_cross_references']['queries'] = []
    elif damage == 'step':
        receipt['steps'][0]['status'] = 'interrupted'
    elif damage == 'data_status':
        receipt['data_status'] = 'observed_source_not_verified'
    else:
        receipt['unverified_data'] = b'unverified-private'
    with pytest.raises(ValueError):
        verified_native_data(receipt, {'schema_version': 1, 'commands': commands})
    projected = public_payload(native_engineering_summary(receipt))
    encoded = json.dumps(projected)
    assert 'private' not in encoded and 'source' not in projected and 'data' not in projected
    assert 'identity' not in encoded and 'observation' not in encoded and 'output' not in encoded
    assert projected['findings'][-1] == {'kind': 'execution_correspondence', 'status': 'unverified'}


def test_queue_serializes_imports_on_one_fixed_com_thread_and_freezes_inputs(store, tmp_path):
    entered, release = threading.Event(), threading.Event()
    events = []
    calls = []

    def importer(path, **kwargs):
        calls.append(kwargs)
        events.append(("import", threading.get_ident()))
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return {"success": True, "message": "imported"}

    service = coordinator(store, tmp_path, importer, events)
    assert service._thread is None
    assert not (tmp_path / "desktop.lock").exists()
    try:
        payload = {"project_id": "p1", "version_id": "v1"}
        first = service.submit_approved("gx_import", payload, approval_id="a1")
        assert entered.wait(5)
        second = service.submit_approved("gx_import", payload, approval_id="a2")
        payload["version_id"] = "modified_after_submission"
        assert not second.running()
        assert len(calls) == 1
        release.set()
        assert first.result(5)["status"] == "imported"
        assert second.result(5)["status"] == "imported"
    finally:
        release.set()
        service.close()
    assert [name for name, _ in events] == ["com_init", "import", "com_uninit"] * 2
    assert len({thread_id for _, thread_id in events}) == 1
    assert events[0][1] != threading.get_ident()
    assert all(call["start_if_needed"] is True for call in calls)
    destinations = [call["create_project_path"] for call in calls]
    assert len(set(destinations)) == 2
    assert all(path.name == "gx_import.gxw" and path.parent.parent.name == "gx_runs" for path in destinations)


def test_native_preparation_timings_are_public_without_exposing_private_fields():
    from application.workspace import public_payload

    result = public_payload({"gx_import_summary": {"pre_import_policy": "protected", "backup_performed": True,
        "timings_ms": {"start_gxworks2": 2500, "prepare_project": 4500, "total": 11000,
                       "private_path": 99, "import": float("nan")}}})
    assert result["gx_import_summary"]["timings_ms"] == {
        "start_gxworks2": 2500, "prepare_project": 4500, "total": 11000}


def test_cross_process_desktop_lock_blocks_second_owner_and_is_released(tmp_path):
    path = tmp_path / "desktop.lock"
    code = "\n".join([
        "import sys",
        "from application.execution import DesktopResourceLock, ExecutionUnavailableError",
        "try:",
        "    with DesktopResourceLock(sys.argv[1]): print('acquired')",
        "except ExecutionUnavailableError:",
        "    print('owned')",
    ])
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    with DesktopResourceLock(path):
        checked = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, env=env, timeout=10)
        assert checked.returncode == 0, checked.stderr
        assert checked.stdout.strip() == "owned"
    checked = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, env=env, timeout=10)
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout.strip() == "acquired"


def test_failure_balances_com_and_never_reports_passed(store, tmp_path):
    events = []

    def importer(*args, **kwargs):
        raise RuntimeError("import interrupted")

    with coordinator(store, tmp_path, importer, events) as service:
        result = service.submit_approved("import_gx", {"project_id": "p1", "version_id": "v1"}, approval_id="a1").result(5)
    assert result["status"] == "error"
    assert result["passed"] is False
    assert [event[0] for event in events] == ["com_init", "com_uninit"]


def test_queued_cancel_and_stale_version_never_import(store, tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls, events = [], []

    def importer(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return {"success": True}

    with coordinator(store, tmp_path, importer, events) as service:
        first = service.submit_approved("gx_import", {"project_id": "p1", "version_id": "v1"}, approval_id="a1")
        assert entered.wait(5)
        cancelled = service.submit_approved("gx_import", {"project_id": "p1", "version_id": "v1"}, approval_id="a2")
        stale = service.submit_approved("gx_import", {"project_id": "p1", "version_id": "v1"}, approval_id="a3")
        assert cancelled.cancel()
        assert first.cancel() is False
        store.active = "v2"
        release.set()
        assert first.result(5)["status"] == "imported"
        assert stale.result(5)["status"] == "error"
        assert cancelled.cancelled()
    assert len(calls) == 1


def test_environment_observation_does_not_initialize_com_or_start_gateway(monkeypatch, store, tmp_path):
    import simulator.gateway
    import simulator.runtime

    def forbidden(*args, **kwargs):
        pytest.fail("Environment observation must not initialize execution")

    monkeypatch.setattr(simulator.runtime, "get_simulator_gateway_runtime", forbidden)
    monkeypatch.setattr(simulator.runtime.SimulatorGatewayRuntime, "ensure_gateway", forbidden)
    monkeypatch.setattr(simulator.gateway, "detect_simulator_environment", lambda: {"simulator_installed": False, "mx_component_installed": False})
    with GXExecutionCoordinator(store, dependencies_factory=forbidden, com_factory=forbidden) as service:
        assert service._thread is None
        result = read_environment()
    assert result["status"] == "unavailable"
    assert result["passed"] is False
    assert result["gateway_started"] is False


def test_import_is_not_a_gx_compile_or_simulator_pass(store, tmp_path):
    with coordinator(store, tmp_path, lambda *args, **kwargs: {"success": True}, []) as service:
        result = service.submit_approved("gx_import", {"project_id": "p1", "version_id": "v1"}, approval_id="a1").result(5)
    assert result["status"] == "imported"
    assert result["passed"] is False
    assert result["gx_compile_status"] == "unverified"
    assert result["simulation_status"] == "not_run"


def test_unavailable_simulator_service_remains_unavailable(monkeypatch, store, tmp_path):
    from simulator.workflow import SimulatorVersionWorkflowService

    calls = []

    def run(self, project_id, version_id, plan, **kwargs):
        calls.append((project_id, version_id, plan))
        return {"status": "unavailable", "message": "desktop locked", "execution": {"result": {"passed": False}}}

    monkeypatch.setattr(SimulatorVersionWorkflowService, "run_approved_plan", run)
    service = GXExecutionCoordinator(store, resource_lock_path=tmp_path / "desktop.lock", com_factory=lambda: FakeCOM([]), dependencies_factory=lambda operation: {"importer": object(), "preparer": object()})
    with service:
        result = service.submit_approved("simulation", {"project_id": "p1", "version_id": "v1", "plan": {"binding": "frozen"}}, approval_id="a1").result(5)
    assert calls == [("p1", "v1", {"binding": "frozen"})]
    assert result["status"] == "unavailable"
    assert result["passed"] is False


@pytest.mark.parametrize("filename", ["../../outside.csv", "program.csv:hidden-stream", "C:\\private\\program.csv"])
def test_import_artifact_traversal_is_rejected(store, tmp_path, filename):
    store.version["artifacts"]["program_csv"] = filename
    with coordinator(store, tmp_path, lambda *a, **kw: pytest.fail("Must not import"), []) as service:
        result = service.submit_approved("gx_import", {"project_id": "p1", "version_id": "v1"}, approval_id="a1").result(5)
    assert result["status"] == "error"
    assert "escapes" in result["message"]


def test_no_arbitrary_operation_or_blank_approval_is_accepted(store, tmp_path):
    with coordinator(store, tmp_path, lambda *a, **kw: None, []) as service:
        with pytest.raises(ValueError):
            service.submit_approved("write_plc", {}, approval_id="approval")
        with pytest.raises(ValueError):
            service.submit_approved("gx_import", {}, approval_id="")
        assert service._thread is None


@pytest.mark.parametrize('cpu', sorted({name for profile in NATIVE_PROJECT_PROFILES
    for name in (profile.cpu, *profile.cpu.removesuffix('(RemoteI/O)').split('/'))}))
def test_visible_session_cli_limits_every_native_menu_and_concrete_member_to_verified_fx3u(cpu):
    from tools.gxw_visible_session import _validate_plan
    plan = {'cpu': cpu, 'commands': [{'operation': 'save'}]}
    if cpu == 'FX3U':
        assert _validate_plan(plan) == plan
    else:
        with pytest.raises(ValueError, match='only for concrete FX3U'):
            _validate_plan(plan)


@pytest.mark.parametrize('cpu', ['fx3u', 'FX3u', ' FX3U '])
def test_visible_session_cli_normalizes_the_verified_concrete_cpu_name(cpu):
    from tools.gxw_visible_session import _validate_plan
    plan = {'cpu': cpu, 'commands': [{'operation': 'save'}]}
    normalized = _validate_plan(plan)
    assert normalized['cpu'] == 'FX3U' and plan['cpu'] == cpu


def test_visible_session_cli_rejects_unverified_cpu_before_desktop_lock_com_and_driver(monkeypatch, tmp_path):
    from tools.gxw_visible_session import execute_plan
    import application.execution
    def forbidden(*_args, **_options):
        pytest.fail('CPU preflight must run before any desktop lock, COM or native factory')
    monkeypatch.setattr(application.execution, 'DesktopResourceLock', forbidden)
    monkeypatch.setitem(sys.modules, 'pythoncom', types.SimpleNamespace(CoInitialize=forbidden, CoUninitialize=forbidden))
    monkeypatch.setitem(sys.modules, 'gxworks2.engineering_driver', types.SimpleNamespace(
        NativeEngineeringDriver=types.SimpleNamespace(start_new=forbidden, attach=forbidden)))
    monkeypatch.setitem(sys.modules, 'application.native_engineering', types.SimpleNamespace(NativeEngineeringSession=forbidden))
    output, project = tmp_path / 'rejected-cpu', tmp_path / 'untouched.gxw'
    result = execute_plan({'cpu': 'LJ72MS15', 'commands': [{'operation': 'save'}]},
        project=project, output_directory=output)
    assert result['status'] == 'interrupted' and result['passed'] is False and result['steps'] == []
    assert result['error']['type'] == 'ValueError' and 'only for concrete FX3U' in result['error']['message']
    assert json.loads((output / 'receipt.json').read_text(encoding='utf-8')) == result
    assert not project.exists() and not (output / 'request.json').exists()
    assert not (output / 'initial.gxw').exists() and not (output / 'final.gxw').exists()


@pytest.mark.parametrize("attached,passed,verified", [(False, True, True), (True, False, True), (True, False, False)])
def test_visible_session_cli_keeps_one_locked_com_apartment_and_preserves_receipts(
        monkeypatch, tmp_path, capsys, attached, passed, verified):
    from tools.gxw_visible_session import execute_plan
    import application.execution

    events = []
    state = {"locked": False, "com": False}
    project = tmp_path / "target.gxw"
    if attached:
        project.write_bytes(b"original")
    output = tmp_path / "receipt"

    class Lock:
        def __enter__(self):
            assert not state["locked"]
            state["locked"] = True
            events.append("lock")

        def __exit__(self, *args):
            assert not state["com"]
            state["locked"] = False
            events.append("unlock")

    def initialize():
        assert state["locked"] and not state["com"]
        state["com"] = True
        events.append("com_init")

    def uninitialize():
        assert state["locked"] and state["com"]
        state["com"] = False
        events.append("com_uninit")

    class Driver:
        @classmethod
        def start_new(cls, path, cpu, **options):
            assert not attached and path == project and state["locked"] and state["com"]
            events.append("new")
            options["progress"]("visible_completion", "Global1")
            return cls()

        @classmethod
        def attach(cls, pid, path, cpu, **options):
            assert attached and pid == 4312 and path == project and state["locked"] and state["com"]
            events.append("attach")
            return cls()

        def snapshot(self):
            assert state["locked"] and state["com"]
            events.append("snapshot")
            print("native diagnostic")
            return b"initial native bytes"

        def close(self):
            assert state["locked"] and state["com"]
            events.append("release_driver")

    class Session:
        def __init__(self, driver, expected_cpu, progress):
            assert expected_cpu == "FX3U"
            self.driver, self.progress = driver, progress

        def run(self, commands, baseline=None):
            assert baseline == b"initial native bytes" and state["locked"] and state["com"]
            events.append("run")
            step = {"index": 0, "operation": "save", "status": "completed"}
            self.progress(step)
            assert json.loads((output / "receipt.json").read_text(encoding="utf-8"))["steps"] == [step]
            self.driver.close()
            return {"passed": passed, "status": "completed" if passed else "interrupted", "steps": [step],
                    **({"data": b"fully verified" if passed else b"previous verified source"} if verified else {}),
                    **({} if passed else {"error": {"type": "RuntimeError", "message": "native edit unverified"},
                        "unverified_data": b"unverified native changes",
                        "unverified_data_status": "observed_source_not_verified"})}

    monkeypatch.setattr(application.execution, "DesktopResourceLock", Lock)
    monkeypatch.setitem(sys.modules, "pythoncom", types.SimpleNamespace(CoInitialize=initialize, CoUninitialize=uninitialize))
    monkeypatch.setitem(sys.modules, "gxworks2.engineering_driver", types.SimpleNamespace(NativeEngineeringDriver=Driver))
    monkeypatch.setitem(sys.modules, "application.native_engineering", types.SimpleNamespace(NativeEngineeringSession=Session))
    result = execute_plan({"cpu": "FX3U", "commands": [{"operation": "save"}]},
        project=project, output_directory=output, process_id=4312 if attached else None)
    assert events == ["lock", "com_init", "attach" if attached else "new", "snapshot", "run", "release_driver", "com_uninit", "unlock"]
    assert result["passed"] is passed and "data" not in result
    assert "unverified_data" not in result
    assert result["window_policy"] == "keep_open"
    assert json.loads((output / "receipt.json").read_text(encoding="utf-8")) == result
    artifact = output / ("final.gxw" if passed else "last-verified.gxw")
    if verified:
        assert artifact.read_bytes() == (b"fully verified" if passed else b"previous verified source")
    else:
        assert not artifact.exists() and "snapshot" not in result
    assert (output / "final.gxw").exists() is passed
    if attached:
        assert project.read_bytes() == b"original"
        assert result["unverified_data_present"] is True
        assert result["unverified_data_status"] == "observed_source_not_verified"
        assert not artifact.exists() or b"unverified native changes" not in artifact.read_bytes()
    captured = capsys.readouterr()
    assert captured.out == "" and "native diagnostic" in captured.err


def test_visible_session_cli_rejects_existing_new_target_and_reused_output(tmp_path):
    from tools.gxw_visible_session import execute_plan

    project = tmp_path / "existing.gxw"
    project.write_bytes(b"retain the existing project")
    output = tmp_path / "receipt"
    plan = {"cpu": "FX3U", "commands": [{"operation": "save"}]}
    failed = execute_plan(plan, project=project, output_directory=output)
    assert failed["passed"] is False and failed["error"]["type"] == "FileExistsError"
    original_receipt = (output / "receipt.json").read_bytes()
    reused = execute_plan(plan, project=tmp_path / "new.gxw", output_directory=output)
    assert reused["passed"] is False and "receipt_file" not in reused
    assert (output / "receipt.json").read_bytes() == original_receipt
    assert project.read_bytes() == b"retain the existing project"
    collided = tmp_path / "collided-output"
    result = execute_plan(plan, project=collided / "initial.gxw", output_directory=collided)
    assert result["passed"] is False and result["error"]["type"] == "ValueError"
    assert not (collided / "initial.gxw").exists()


def test_visible_session_cli_input_failure_writes_one_json_stdout_and_receipt(tmp_path, capsys):
    from tools.gxw_visible_session import main

    plan = tmp_path / "plan.json"
    plan.write_text("{broken input", encoding="utf-8")
    output = tmp_path / "receipt"
    assert main(["--plan", str(plan), "--project", str(tmp_path / "new.gxw"), "--output-dir", str(output)]) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert len(captured.out.splitlines()) == 1
    assert result["passed"] is False and result["error"]["type"] == "JSONDecodeError"
    assert result == json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    assert not (tmp_path / "new.gxw").exists()


@pytest.mark.parametrize('damage', ['st', 'gxw', 'report', 'metadata', 'logical-pou', 'cpu', 'text-only', 'path', 'stale'])
def test_saved_st_native_execution_rejects_tampering_before_driver_or_managed_copy(store, tmp_path, damage):
    from application.native_st import prepare_candidate
    raw = native_st_source_witness()
    root = store.version_dir('p1', 'v1')
    prepared = prepare_candidate(root, imported=raw, program_name='POU_01.Program.pou', plc_model='FX3U')
    store.version.update(prepared['metadata'], artifacts={key: value['path'] for key, value in prepared['artifacts'].items()})
    if damage == 'st':
        (root / 'program.st').write_bytes((root / 'program.st').read_bytes() + b' ')
    elif damage == 'gxw':
        (root / 'program.gxw').write_bytes(native_st_source_witness(kind='creation'))
    elif damage == 'report':
        report = json.loads((root / 'write_report.json').read_bytes())
        report['source']['compiler_sources']['programs'][0]['body'] = 'different source'
        (root / 'write_report.json').write_text(json.dumps(report), encoding='utf-8')
    elif damage == 'metadata':
        store.version['validation']['source_binding']['task_bindings'][0]['line'] = 99
    elif damage == 'logical-pou':
        store.version['program_name'] = 'POU_01'
    elif damage == 'cpu':
        store.version['plc_model'] = 'L02'
    elif damage == 'text-only':
        store.version['artifacts'] = {'st': 'program.st'}
    elif damage == 'path':
        store.version['artifacts']['gxw'] = '../outside.gxw'
    else:
        store.active = 'v2'
    before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    events = []
    def forbidden(_operation):
        pytest.fail('Invalid ST source reached native dependencies or desktop controls')
    with GXExecutionCoordinator(store, resource_lock_path=tmp_path / 'native-st.lock',
            dependencies_factory=forbidden, com_factory=lambda: FakeCOM(events)) as service:
        result = service.submit_approved('native_engineering', {'project_id': 'p1', 'version_id': 'v1',
            'plan': {'schema_version': 1, 'commands': [{'operation': 'save'}]}}, approval_id='approved-st-source').result(20)
    assert result['passed'] is False and result['status'] == 'error'
    assert not (store.project_dir('p1') / 'gxw_runs').exists()
    assert {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()} == before
    assert [name for name, _ in events] == ['com_init', 'com_uninit']
    assert len({thread for _, thread in events}) == 1


def test_saved_st_native_worker_keeps_frozen_plan_and_rejects_failed_source_receipt(store, tmp_path):
    from application.native_st import prepare_candidate
    raw = native_st_source_witness()
    root = store.version_dir('p1', 'v1')
    prepared = prepare_candidate(root, imported=raw, program_name='POU_01.Program.pou', plc_model='FX3U')
    store.version.update(prepared['metadata'], artifacts={key: row['path'] for key, row in prepared['artifacts'].items()})
    commands = [{'operation': 'save'}, {'operation': 'reopen'}]
    payload = {'project_id': 'p1', 'version_id': 'v1', 'plan': {'schema_version': 1, 'commands': copy.deepcopy(commands)}}
    entered, release = threading.Event(), threading.Event()
    events, paths = [], []
    def factory(path, cpu, **_options):
        paths.append(path)
        assert cpu == 'FX3U' and path.read_bytes() == raw
        entered.set()
        assert release.wait(10)
        return object()
    class Session:
        def __init__(self, _driver, expected_cpu, progress):
            assert expected_cpu == 'FX3U'
        def run(self, selected, baseline):
            assert selected == commands and baseline == raw
            result = native_delivery_receipt(raw, selected)
            result.update(status='interrupted', passed=False, unverified_data=b'unverified native change')
            return result
    with GXExecutionCoordinator(store, resource_lock_path=tmp_path / 'native-st.lock',
            dependencies_factory=lambda _: {'driver_factory': factory, 'session_factory': Session},
            com_factory=lambda: FakeCOM(events)) as service:
        future = service.submit_approved('native_engineering', payload, approval_id='approved-frozen-st')
        try:
            assert entered.wait(10)
            payload['plan']['commands'][0]['operation'] = 'compile'
            payload['version_id'] = 'different'
        finally:
            release.set()
        result = future.result(20)
    assert result['status'] == 'interrupted' and result['passed'] is False
    assert '_native_result' not in result and 'data' not in result and 'unverified_data' not in result
    assert store.active == 'v1' and (root / 'program.gxw').read_bytes() == raw
    assert len(paths) == 1 and paths[0].parent.parent.name == 'gxw_runs'
    assert [name for name, _ in events] == ['com_init', 'com_uninit']


def _live_execution_witness(store, tmp_path, monkeypatch):
    from application.native_st import prepare_candidate
    raw = native_st_source_witness()
    prepared = prepare_candidate(store.version_dir('p1', 'v1'), imported=raw,
                                 program_name='POU_01.Program.pou', plc_model='FX3U')
    store.version.update(prepared['metadata'], artifacts={key: row['path'] for key, row in prepared['artifacts'].items()})
    events, drivers, controllers = [], [], []
    state = types.SimpleNamespace(refs=0, entered=threading.Event(), release=threading.Event())
    class COM:
        def CoInitialize(self):
            state.refs += 1
            events.append(('init', threading.get_ident()))
        def CoUninitialize(self):
            state.refs -= 1
            assert state.refs >= 0
            events.append(('uninit', threading.get_ident()))
    def driver_factory(path, cpu, **options):
        assert state.refs > 0 and path.read_bytes() == raw
        def close():
            assert state.refs > 0
            events.append(('driver_close', threading.get_ident()))
        driver = types.SimpleNamespace(path=path, close=close)
        drivers.append(driver)
        return driver
    class Session:
        def __init__(self, driver, expected_cpu, baseline, session_id, project_id, base_version_id, progress):
            assert baseline == raw and expected_cpu == 'FX3U'
            self.driver, self.progress = driver, progress
            self.receipt = dict(mode='live', session_id=session_id, project_id=project_id,
                base_version_id=base_version_id, status='active', passed=True, revision=0, dirty=False,
                saved_revision=0, steps=[], compile={'status': 'not_run'}, save={'status': 'not_run'},
                identity={'process_id': 701})
            self.owner = threading.get_ident()
            self.sequence, self.pending, self.pause = 0, [], threading.Event()
            controllers.append(self)
        def status(self):
            events.append(('status', threading.get_ident()))
            return copy.deepcopy(self.receipt)
        def run(self, commands, controls=None):
            assert self.owner == threading.get_ident() and state.refs > 0
            controls = controls or {}
            for key in ('data', 'data_status', 'data_revision'):
                self.receipt.pop(key, None)
            self.receipt.update(executed_steps=[], stage_saves=[])
            work = self.pending if controls.get('resume_pending') else commands
            self.pause.clear()
            for index, command in enumerate(work):
                if (controls.get('step_mode') and index) or self.pause.is_set():
                    self.pending = work[index:]
                    self.receipt['status'] = 'paused'
                    break
                self.sequence += 1
                step = dict(operation=command['operation'], index=self.sequence, status='completed')
                if command['operation'] == 'hold':
                    state.entered.set()
                    assert state.release.wait(10)
                elif command['operation'] == 'fail':
                    self.receipt['dirty'] = True
                    raise RuntimeError('controlled partial live step')
                elif command['operation'] == 'save':
                    revision, validation = self.receipt['revision'], {'source': 'Core verified'}
                    self.receipt.update(data=b'Core checked live save', data_status='verified_stage_save',
                        data_revision=revision, saved_revision=revision, dirty=False,
                        save={'status': 'passed', 'revision': revision, 'validation': validation})
                    self.receipt['stage_saves'].append(dict(revision=revision, sequence=self.sequence,
                                                           validation=validation))
                    step['verification'] = copy.deepcopy(self.receipt['save'])
                else:
                    self.receipt.update(revision=self.receipt['revision'] + 1, dirty=True)
                step['revision'] = self.receipt['revision']
                self.receipt['steps'].append(copy.deepcopy(step))
                self.receipt['executed_steps'].append(step)
            else:
                self.pending = []
                self.receipt['status'] = 'completed'
            events.append(('run', threading.get_ident()))
            return copy.deepcopy(self.receipt)
        def request_pause(self):
            self.pause.set()
            return {'session_id': self.receipt['session_id'], 'status': 'pause_requested',
                    'boundary': 'after_current_step'}
        def rebind(self):
            assert self.owner == threading.get_ident() and state.refs > 0
            events.append(('rebind', threading.get_ident()))
            self.receipt.pop('data', None)
            return self.status()
        def set_base_version(self, value):
            self.receipt['base_version_id'] = value
        def close(self):
            assert self.owner == threading.get_ident() and state.refs > 0
            self.receipt.pop('data', None)
            self.receipt['status'] = 'closed'
            self.driver.close()
            return self.status()
    monkeypatch.setitem(sys.modules, 'application.live_engineering', types.SimpleNamespace(
        normalize_live_plan=lambda plan, plc_model: copy.deepcopy(plan)))
    coordinator = GXExecutionCoordinator(store, resource_lock_path=tmp_path / 'live-desktop.lock',
        dependencies_factory=lambda _: {'driver_factory': driver_factory, 'live_session_factory': Session},
        com_factory=COM)
    def submit(action, commands=None, session_id=None, project_id='p1', version_id=None, controls=None):
        plan = dict(mode='live', session_action=action, commands=commands or [])
        if session_id: plan['session_id'] = session_id
        if controls: plan['controls'] = controls
        return coordinator.submit_approved('native_engineering', dict(project_id=project_id,
            version_id=version_id or store.active, plan=plan), approval_id='approved-live-stage')
    return coordinator, submit, state, events, drivers, controllers


def test_live_coordinator_reuses_one_copy_worker_and_com_apartment_until_explicit_end(store, tmp_path, monkeypatch):
    coordinator, submit, state, events, drivers, controllers = _live_execution_witness(store, tmp_path, monkeypatch)
    try:
        assert coordinator.live_session_status('p1') == dict(mode='background', status='not_started', project_id='p1')
        initial = submit('start', [{'operation': 'edit'}]).result(15)
        session_id = initial['_live_result']['session_id']
        assert coordinator.live_session_status('p1')['session_id'] == session_id
        assert state.refs == 1 and len(drivers) == len(controllers) == 1
        assert initial['_live_result']['dirty'] and initial['_live_result']['revision'] == 1
        second = submit('continue', [{'operation': 'edit'}], session_id).result(15)
        assert second['_live_result']['revision'] == 2 and len(drivers) == 1
        assert '_native_result' not in second and state.refs == 1
        duplicate = submit('start').result(15)
        assert duplicate['status'] == 'error' and len(drivers) == 1
        submit('rebind', session_id=session_id).result(15)
        ended = submit('end', session_id=session_id).result(15)
        assert ended['_live_result']['status'] == 'closed' and state.refs == 0
        assert sum(name == 'driver_close' for name, _ in events) == 1
        assert submit('continue', session_id=session_id).result(15)['status'] == 'error'
        receipt = coordinator.live_session_status('p1', session_id)
        assert receipt['status'] == 'closed' and receipt['dirty']
        assert coordinator.live_session_status('p1')['status'] == 'not_started'
        with pytest.raises(ValueError): coordinator.request_live_session_pause('p1', session_id)
        assert drivers[0].path.exists() and drivers[0].path.parent.parent.name == 'gxw_runs'
    finally:
        coordinator.close()
    assert state.refs == 0 and len({thread for _, thread in events}) == 1
    assert next(thread for _, thread in events) != threading.get_ident()


def test_live_continuation_observes_its_own_job_instead_of_the_finished_start_job(store, tmp_path, monkeypatch):
    coordinator, _submit, state, events, drivers, controllers = _live_execution_witness(store, tmp_path, monkeypatch)
    first_messages, next_messages = [], []
    first_finished = False
    def first_progress(message):
        if first_finished:
            raise RuntimeError('Cannot append events to a finished job')
        first_messages.append(message)
    try:
        initial = coordinator.submit_approved('native_engineering', dict(project_id='p1', version_id='v1',
            plan=dict(mode='live', session_action='start', commands=[])), approval_id='start',
            progress=first_progress).result(15)
        session_id = initial['_live_result']['session_id']
        first_finished = True
        controller = controllers[0]
        original_run = controller.run
        def emitting_run(commands, controls=None):
            controller.progress(dict(status='executing', operation='edit'))
            result = original_run(commands, controls)
            controller.progress(dict(status='completed', operation='edit'))
            return result
        controller.run = emitting_run
        continued = coordinator.submit_approved('native_engineering', dict(project_id='p1', version_id='v1',
            plan=dict(mode='live', session_action='continue', session_id=session_id,
                commands=[dict(operation='edit')])), approval_id='continue', progress=next_messages.append).result(15)
        assert continued['passed'] and continued['_live_result']['revision'] == 1
        assert not first_messages and len(next_messages) == 2
        assert coordinator.live_session_status('p1')['revision'] == 1
        assert len(drivers) == len(controllers) == 1
    finally:
        coordinator.close()


def test_live_coordinator_status_is_cached_during_work_and_save_rebase_is_transaction_bound(store, tmp_path, monkeypatch):
    coordinator, submit, state, events, drivers, controllers = _live_execution_witness(store, tmp_path, monkeypatch)
    try:
        session_id = submit('start', [{'operation': 'edit'}]).result(15)['_live_result']['session_id']
        future = submit('continue', [{'operation': 'hold'}], session_id)
        assert state.entered.wait(10)
        before = list(events)
        cached = coordinator.live_session_status('p1', session_id)
        assert cached['operation_in_progress'] and events == before
        cached['steps'].clear()
        assert coordinator.live_session_status('p1', session_id)['steps']
        with pytest.raises(KeyError): coordinator.live_session_status('foreign', session_id)
        with pytest.raises(KeyError): coordinator.request_live_session_pause('foreign', session_id)
        pause = coordinator.request_live_session_pause('p1', session_id)
        assert pause['status'] == 'pause_requested' and events == before
        assert coordinator.live_session_status('p1')['pause_requested']
        state.release.set()
        assert future.result(15)['passed']
        saved = submit('continue', [{'operation': 'save'}], session_id).result(15)
        assert saved['_live_result']['data'] == b'Core checked live save'
        assert 'data' not in coordinator.live_session_status('p1', session_id)
        assert coordinator.live_session_status('p1')['pending_stage_save']['revision'] == 1
        assert submit('continue', session_id=session_id).result(15)['status'] == 'error'
        with pytest.raises(ValueError): coordinator.rebase_live_session('p1', session_id, 'v1', 'v2')
        store.active, store.version['id'] = 'v2', 'v2'
        with pytest.raises(ValueError): coordinator.rebase_live_session('p1', session_id, 'foreign', 'v2')
        rebound = coordinator.rebase_live_session('p1', session_id, 'v1', 'v2')
        assert rebound['base_version_id'] == 'v2' and not rebound['pending_save_transaction']
        assert submit('continue', [{'operation': 'edit'}], session_id).result(15)['_live_result']['revision'] == 2
        assert len(drivers) == 1
    finally:
        state.release.set()
        coordinator.close()
    assert state.refs == 0 and sum(name == 'driver_close' for name, _ in events) == 1


def test_live_coordinator_resume_can_deliver_only_the_save_completed_in_that_call(store, tmp_path, monkeypatch):
    coordinator, submit, state, events, drivers, controllers = _live_execution_witness(store, tmp_path, monkeypatch)
    try:
        paused = submit('start', [{'operation': 'edit'}, {'operation': 'save'}],
                        controls={'step_mode': True}).result(15)['_live_result']
        session_id = paused['session_id']
        assert paused['status'] == 'paused' and 'data' not in paused
        resumed = submit('continue', session_id=session_id,
                         controls={'resume_pending': True, 'expected_revision': 1}).result(15)['_live_result']
        assert resumed['data'] == b'Core checked live save'
        assert resumed['executed_steps'][0]['operation'] == 'save'
        assert coordinator.live_session_status('p1')['pending_stage_save']['sequence'] == 2
    finally:
        coordinator.close()


@pytest.mark.parametrize('damage', [
    'missing_current_steps', 'missing_stage_saves', 'historical_save_only', 'duplicate_sequence',
    'wrong_revision', 'wrong_sequence', 'different_validation', 'unconfirmed_step', 'unverified_bytes'])
def test_live_stage_save_bytes_require_a_matching_current_run_receipt(damage):
    validation = {'source': 'independent Core validation'}
    receipt = dict(data=b'verified native bytes', data_status='verified_stage_save', data_revision=2,
        saved_revision=2, revision=3, stage_saves=[dict(revision=2, sequence=7, validation=validation)],
        executed_steps=[dict(operation='save', status='completed', index=7, revision=2,
            verification=dict(status='passed', revision=2, validation=copy.deepcopy(validation)))])
    assert GXExecutionCoordinator._live_stage_save_receipt(receipt)['revision'] == 2
    receipt['saved_revision'] = 3  # Native compile may save a later source revision in the same run.
    assert GXExecutionCoordinator._live_stage_save_receipt(receipt)['revision'] == 2
    if damage == 'missing_current_steps': receipt.pop('executed_steps')
    elif damage == 'missing_stage_saves': receipt.pop('stage_saves')
    elif damage == 'historical_save_only':
        receipt['steps'], receipt['executed_steps'] = receipt['executed_steps'], []
    elif damage == 'duplicate_sequence': receipt['executed_steps'] *= 2
    elif damage == 'wrong_revision': receipt['stage_saves'][0]['revision'] = 3
    elif damage == 'wrong_sequence': receipt['stage_saves'][0]['sequence'] = 8
    elif damage == 'different_validation': receipt['executed_steps'][0]['verification']['validation'] = {}
    elif damage == 'unconfirmed_step': receipt['executed_steps'][0]['status'] = 'unconfirmed'
    elif damage == 'unverified_bytes': receipt['data_status'] = 'old_saved_project'
    with pytest.raises(ValueError): GXExecutionCoordinator._live_stage_save_receipt(receipt)


def test_live_coordinator_rejects_foreign_or_stale_tokens_and_keeps_partial_work_until_shutdown(store, tmp_path, monkeypatch):
    coordinator, submit, state, events, drivers, controllers = _live_execution_witness(store, tmp_path, monkeypatch)
    try:
        session_id = submit('start').result(15)['_live_result']['session_id']
        assert submit('continue', session_id='missing').result(15)['status'] == 'error'
        assert submit('continue', session_id=session_id, project_id='foreign').result(15)['status'] == 'error'
        store.active = 'new-base'
        assert submit('continue', session_id=session_id, version_id='v1').result(15)['status'] == 'error'
        store.active = 'v1'
        failed = submit('continue', [{'operation': 'fail'}], session_id).result(15)
        assert failed['status'] == 'error' and state.refs == 1
        cached = coordinator.live_session_status('p1', session_id)
        assert cached['status'] == 'requires_reconciliation' and cached['dirty']
        with pytest.raises(ValueError):
            coordinator.submit_approved('native_engineering', {}, approval_id='')
        assert not any(name == 'driver_close' for name, _ in events)
    finally:
        coordinator.close()
    assert state.refs == 0 and sum(name == 'driver_close' for name, _ in events) == 1


def test_live_shutdown_records_refused_close_without_claiming_a_running_child_was_ended(store, tmp_path, monkeypatch):
    coordinator, submit, state, events, drivers, controllers = _live_execution_witness(store, tmp_path, monkeypatch)
    session_id = submit('start').result(15)['_live_result']['session_id']
    controller = controllers[0]
    controller._private_native_error = {'native_task_id': 'actual-owned-native-task', 'child_process_id': 8317,
                                        'child_terminated': False}
    def refused():
        raise RuntimeError('Original native child is still running')
    controller.close = refused
    ended = submit('end', session_id=session_id).result(15)
    assert ended['status'] == 'error' and state.refs == 1
    assert not coordinator.live_session_status('p1', session_id).get('controller_closed')
    refused_receipt = json.loads((drivers[0].path.parent / 'receipt.json').read_text(encoding='utf-8'))
    assert refused_receipt['private_native_observation'] == controller._private_native_error
    coordinator.close()
    receipt = coordinator.live_session_status('p1', session_id)
    assert receipt['status'] == 'requires_reconciliation' and receipt['controller_closed'] is False
    assert receipt['com_release'] == 'worker_shutdown' and receipt['ide_policy'] == 'keep_open'
    assert 'private_native_observation' not in receipt and state.refs == 0
    durable = json.loads((drivers[0].path.parent / 'receipt.json').read_text(encoding='utf-8'))
    assert durable['private_native_observation'] == controller._private_native_error
    assert not any(name == 'driver_close' for name, _ in events)

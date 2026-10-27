"""Native selection leases recover after faults without replaying a mutation."""
import json
import os
from copy import deepcopy
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import gxworks2.active_document as module
from gxworks2.engineering_driver import NativeEngineeringDriverError


class PendingChild:
    pid = 9876
    returncode = None

    def __init__(self):
        self.calls = 0
        self.finished = False

    def communicate(self, value=None, timeout=None):
        self.calls += 1
        if not self.finished:
            raise subprocess.TimeoutExpired('owned-adapter', timeout)
        return json.dumps({'status': 'observed', 'protocol_version': 1,
                           'native_version': '1.635.0.1', 'process_id': 1234}), ''

    def poll(self):
        self.returncode = 0 if self.finished else None
        return self.returncode

    def kill(self):
        raise AssertionError('A timeout must not terminate a child inside the native critical section')


@pytest.fixture(scope='module')
def native_lease_harness(tmp_path_factory):
    if os.name != 'nt':
        pytest.skip('Windows native process and thread lifetime contract')
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
    assert compiler.is_file(), 'The native regression requires the installed .NET Framework compiler'
    root = tmp_path_factory.mktemp('native-lease')
    harness = root / 'LeaseHarness.cs'
    harness.write_text(r'''
using System;
using System.Diagnostics;
using System.IO.MemoryMappedFiles;
using System.Runtime.InteropServices;
using System.Threading;
namespace PlcAi.NativeAdapter {
    static class LeaseHarness {
        [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
        [DllImport("kernel32.dll")] static extern IntPtr OpenThread(uint access, bool inherit, uint tid);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        static int Main(string[] args) {
            if (args[0] == "--selection-guardian") return NativeUiLease.RunGuardian(args);
            if (args[0] == "target") {
                using (var map = MemoryMappedFile.OpenExisting(args[1]))
                using (var view = map.CreateViewAccessor()) {
                    Console.WriteLine(GetCurrentThreadId()); Console.Out.Flush();
                    int counter = 0;
                    while (true) { view.Write(0, ++counter); Thread.Sleep(2); }
                }
            }
            if (args.Length == 6) {
                using (var gate = EventWaitHandle.OpenExisting(args[5])) {
                    if (!gate.WaitOne(5000)) return 2;
                }
            }
            IntPtr thread = OpenThread(0x62, false, UInt32.Parse(args[2]));
            try {
                using (var lease = new NativeUiLease(thread, UInt32.Parse(args[1]), args[3] == "legacy", Int32.Parse(args[4]))) {
                    Console.WriteLine("held " + lease.GuardianId + " " + lease.Mechanism); Console.Out.Flush();
                    string command = Console.ReadLine();
                    if (command == "fail") throw new InvalidOperationException("injected_after_suspend");
                    lease.Check();
                }
                Console.WriteLine("released"); return 0;
            } catch (Exception error) { Console.WriteLine(error.Message); return 1; }
            finally { CloseHandle(thread); }
        }
    }
}
''', encoding='utf-8')
    executable = root / 'lease.exe'
    source = Path(__file__).resolve().parents[1] / 'native_adapters/NativeUiLease.cs'
    result = subprocess.run([str(compiler), '/nologo', '/platform:x86', '/target:exe',
        '/out:' + str(executable), str(source), str(harness)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return executable


@pytest.mark.parametrize(('mechanism', 'exit_mode'), [
    (mechanism, mode)
    for mechanism in ('native', 'legacy')
    for mode in ('normal', 'exception', 'kill', 'deadline', 'guardian_kill', 'already_suspended', 'job_kill', 'job_refused')
    if mechanism == 'legacy' or mode not in ('guardian_kill', 'job_kill', 'job_refused')
])
def test_native_ui_lease_recovers_owned_thread_after_process_fault(native_lease_harness, mechanism, exit_mode):
    import ctypes
    from ctypes import wintypes
    import mmap
    import time
    import uuid
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    for method in ('CloseHandle', 'SuspendThread', 'ResumeThread'):
        getattr(kernel, method).argtypes = [wintypes.HANDLE]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.SetEvent.argtypes = [wintypes.HANDLE]
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    native = ctypes.WinDLL('ntdll')
    native.NtQueryInformationThread.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p]
    name = 'Local\\lease-test-' + uuid.uuid4().hex
    memory = mmap.mmap(-1, 16, tagname=name)
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    target = subprocess.Popen([str(native_lease_harness), 'target', name], stdout=subprocess.PIPE,
        text=True, creationflags=flags)
    owner = None
    thread = None
    job = gate = None
    def read_line(process):
        # Kill the test-owned process on failure before joining a blocked reader.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as reader:
            try:
                return reader.submit(process.stdout.readline).result(timeout=5)
            except BaseException:
                process.kill()
                raise
    try:
        tid = int(read_line(target))
        thread = kernel.OpenThread(0x42, False, tid)
        assert thread
        def count():
            value = wintypes.ULONG()
            assert native.NtQueryInformationThread(thread, 35, ctypes.byref(value), 4, None) >= 0
            return value.value
        if exit_mode == 'already_suspended':
            assert kernel.SuspendThread(thread) == 0
        timeout = 300 if exit_mode == 'deadline' else 5000
        command = [str(native_lease_harness), 'lease', str(target.pid), str(tid), mechanism, str(timeout)]
        if exit_mode.startswith('job_'):
            class BasicLimits(ctypes.Structure):
                _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                    ('flags', wintypes.DWORD), ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t),
                    ('processes', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                    ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
            class ExtendedLimits(ctypes.Structure):
                _fields_ = [('basic', BasicLimits), ('io', ctypes.c_uint64 * 6),
                    ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                    ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
            job = kernel.CreateJobObjectW(None, None)
            assert job
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000 | (0x800 if exit_mode == 'job_kill' else 0)
            assert kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            gate_name = name + '-gate'
            gate = kernel.CreateEventW(None, True, False, gate_name)
            assert gate
            command.append(gate_name)
        owner = subprocess.Popen(command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=flags)
        if job:
            assert kernel.AssignProcessToJobObject(job, int(owner._handle)), ctypes.get_last_error()
            assert kernel.SetEvent(gate)
        line = read_line(owner).strip()
        if exit_mode == 'already_suspended':
            owner.communicate(timeout=5)
            assert owner.returncode == 1 and 'already_suspended' in line
            assert count() == 1
            assert kernel.ResumeThread(thread) == 1
        elif exit_mode == 'job_refused':
            owner.communicate(timeout=5)
            assert owner.returncode == 1 and 'job_breakaway_denied' in line
            assert count() == 0
        else:
            assert line.startswith('held '), line
            assert count() == 1
            initial = memory[:4]
            time.sleep(.03)
            assert memory[:4] == initial
            if exit_mode in ('kill', 'job_kill'):
                if job:
                    assert kernel.TerminateJobObject(job, 99)
                else:
                    owner.kill()
                owner.communicate(timeout=5)
            elif exit_mode == 'deadline':
                owner.wait(timeout=5)
                owner.communicate(timeout=5)
                assert owner.returncode != 0
            else:
                if exit_mode == 'guardian_kill':
                    guardian_id = int(line.split()[1])
                    guardian = kernel.OpenProcess(1, False, guardian_id)
                    assert guardian
                    try:
                        assert kernel.TerminateProcess(guardian, 99)
                    finally:
                        kernel.CloseHandle(guardian)
                    time.sleep(.05)
                output, errors = owner.communicate('fail\n' if exit_mode == 'exception' else 'release\n', timeout=5)
                assert owner.returncode == (0 if exit_mode == 'normal' else 1), (output, errors)
            until = time.monotonic() + 5
            while count() != 0 and time.monotonic() < until:
                time.sleep(.01)
            assert count() == 0
        initial = memory[:4]
        until = time.monotonic() + 2
        while memory[:4] == initial and time.monotonic() < until:
            time.sleep(.01)
        assert memory[:4] != initial and target.poll() is None
    finally:
        if owner is not None:
            if owner.poll() is None:
                owner.kill()
            owner.communicate(timeout=5)
        # Only the synthetic target created above is ever terminated.
        target.kill()
        target.communicate(timeout=5)
        if thread:
            kernel.CloseHandle(thread)
        if gate:
            kernel.CloseHandle(gate)
        if job:
            kernel.CloseHandle(job)
        memory.close()


def test_timeout_retains_child_and_never_replays_before_reconciliation(tmp_path, monkeypatch):
    executable = tmp_path / 'owned-adapter.exe'
    executable.write_bytes(b'test transport only')
    child, launches = PendingChild(), []
    def launch(*args, **kwargs):
        launches.append((args, kwargs))
        return child
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt', environ={}))
    monkeypatch.setattr(module.subprocess, 'Popen', launch)
    adapter = module.ActiveDocumentAdapter(executable, timeout=0.01)
    request = {'operation': 'select_graph_element', 'process_id': 1234}
    with pytest.raises(NativeEngineeringDriverError) as caught:
        adapter._call(request)
    assert caught.value.observation['status'] == 'unconfirmed'
    assert caught.value.observation['child_terminated'] is False
    task_id = caught.value.observation['native_task_id']
    assert adapter.pending_operations() == [{'native_task_id': task_id, 'status': 'running',
                                             'operation': 'select_graph_element'}]
    with pytest.raises(NativeEngineeringDriverError, match='未发送新的修改'):
        adapter._call(request)
    assert len(launches) == 1
    child.finished = True
    reconciled = adapter.pending_operations()
    assert reconciled[0]['status'] == 'ready_to_reconcile'
    assert reconciled[0]['native_observation']['process_id'] == 1234
    assert not adapter._pending
    assert len(launches) == 1


def test_crash_safe_selection_timeout_stops_writer_and_never_replays(tmp_path, monkeypatch):
    executable = tmp_path / 'owned-adapter.exe'
    executable.write_bytes(b'transport test')
    events = []
    class Child:
        pid, returncode = 123, None
        def communicate(self, *args, **kwargs):
            if self.returncode is None:
                raise subprocess.TimeoutExpired('owned-adapter', .01)
            events.append('drained')
            return '', ''
        def kill(self):
            events.append('writer_stopped')
            self.returncode = 1
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt', environ={}))
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: Child())
    adapter = module.ActiveDocumentAdapter(executable, timeout=.01)
    with pytest.raises(NativeEngineeringDriverError) as caught:
        adapter._call(dict(protocol_version=2, operation='select_graph_element', process_id=55))
    assert events == ['writer_stopped', 'drained']
    assert caught.value.observation['status'] == 'unconfirmed'
    assert caught.value.observation['child_terminated'] is True
    assert not adapter._pending


@pytest.mark.parametrize('value', [[], None, True, 'completed', {'status': 'observed', 'protocol_version': 99},
                                   {'status': 'observed', 'protocol_version': 1, 'native_version': '1.635.0.1', 'process_id': 5678}])
def test_malformed_or_other_process_observation_cannot_confirm_result(value):
    with pytest.raises(NativeEngineeringDriverError):
        module.ActiveDocumentAdapter._result(0, json.dumps(value), {'process_id': 1234})


def test_pending_native_child_blocks_ui_queries_before_a_fresh_read():
    adapter = module.ActiveDocumentAdapter('not-launched.exe')
    adapter._pending['retained-child'] = (object(), {})
    def unexpected_ui_query():
        raise AssertionError('An unconfirmed suspended UI must not be queried before polling its child')
    driver = SimpleNamespace(inspect=unexpected_ui_query)
    with pytest.raises(NativeEngineeringDriverError) as caught:
        adapter.read_graph(driver, owner='Main')
    assert caught.value.observation == {'status': 'unconfirmed', 'native_task_ids': ['retained-child']}


@pytest.mark.parametrize('scope', ['global', 'program', 'fb', 'structure'])
@pytest.mark.parametrize('damage', [None, 'owner', 'grids', 'grid_result', 'document_result',
                                    'title_result', 'version', 'saved', 'table', 'page_changed'])
def test_label_table_read_binds_current_owner_and_never_exports(scope, damage):
    from gxw.native_locale import label_title
    identity = dict(process_id=1234, window_handle=200, process_started='789', executable='owned.exe',
                    software_version='1.635.0.1', project_path='C:/Lab/Owned.gxw', cpu_menu='FX3U/FX3UC')
    owner, events = 'Current', []
    title = label_title('Other' if damage == 'owner' else owner, scope)
    grid = SimpleNamespace(handle=202, is_visible=lambda: True, is_enabled=lambda: True)
    document = SimpleNamespace(handle=201, window_text=lambda: title,
        descendants=lambda **kwargs: [grid, grid] if damage == 'grids' else [grid])
    def verify(current, page, caption):
        assert (current, page, caption) == (identity, document, title)
        events.append('verify')
        if damage == 'page_changed':
            raise NativeEngineeringDriverError('page changed')
    driver = SimpleNamespace(inspect=lambda: deepcopy(identity), _idle=lambda: None,
        _labels_page=lambda name, kind: document if (name, kind) == (owner, scope) else pytest.fail('wrong owner'),
        _verify_read_page=verify)
    observed = dict(document_handle=201, document_title=title, grid_handle=202, control_version='7.0.0.52',
                    table={'columns': ['Observed'], 'rows': [['actual']]}, save_performed=False)
    field = {'grid_result':'grid_handle', 'document_result':'document_handle', 'title_result':'document_title',
             'version':'control_version', 'saved':'save_performed', 'table':'table'}.get(damage)
    if field:
        observed[field] = True if field == 'save_performed' else None
    adapter = module.ActiveDocumentAdapter('not-launched.exe')
    def call(request):
        events.append(request)
        assert request == dict(protocol_version=1, operation='read_active_labels', process_id=1234,
            window_handle=200, process_started='789', executable='owned.exe', document_handle=201,
            document_title=title, grid_handle=202)
        return deepcopy(observed)
    adapter._call = call
    if damage:
        with pytest.raises(NativeEngineeringDriverError):
            adapter.read_label_table(driver, owner=owner, scope=scope)
    else:
        result = adapter.read_label_table(driver, owner=owner, scope=scope)
        assert result == {**observed, 'identity': identity}
    assert len([event for event in events if isinstance(event, dict)]) == (0 if damage in ('owner','grids') else 1)


def test_pending_native_child_blocks_label_navigation():
    adapter = module.ActiveDocumentAdapter('not-launched.exe')
    adapter._pending['retained-child'] = (object(), {})
    driver = SimpleNamespace(inspect=lambda: pytest.fail('Pending native operations must block label navigation'))
    with pytest.raises(NativeEngineeringDriverError):
        adapter.read_label_table(driver, owner='Current', scope='global')


@pytest.mark.parametrize('scope', ['global', 'program', 'fb', 'structure'])
def test_label_driver_projects_only_actual_native_table(scope):
    from gxworks2.engineering_driver import NativeEngineeringDriver
    from gxw.native_locale import label_title
    driver = object.__new__(NativeEngineeringDriver)
    identity = dict(process_id=1234, window_handle=200, process_started='789', executable='owned.exe',
                    software_version='1.635.0.1', project_path='C:/Lab/Owned.gxw', cpu_menu='FX3U/FX3UC')
    columns = ['标签名', '数据类型', '\u3000', '常量', '注释']
    row = ['Flag', 'Bit', '', '', 'Actual native comment']
    if scope != 'structure':
        columns.insert(0, '类')
        row.insert(0, 'VAR_GLOBAL' if scope == 'global' else 'VAR')
    grid = dict(columns=columns, rows=[row])
    calls = []
    def read(bound_driver, *, owner, scope):
        calls.append((bound_driver, owner, scope))
        return dict(table=deepcopy(grid), identity=identity, document_handle=201,
                    document_title=label_title(owner, scope))
    driver._label_reader = SimpleNamespace(read_label_table=read)
    driver._export_label_csv = lambda *args: pytest.fail('A table read must not export')
    driver._command = lambda *args: pytest.fail('A table read must not send engineering commands')
    driver.snapshot = lambda *args: pytest.fail('A table read must not save')
    result = driver.read_labels('Current', scope)
    assert calls == [(driver, 'Current', scope)]
    assert result['read_transport'] == 'native_label_table' and result['native_grid'] == grid
    assert result['declarations'][0]['data_type'] == 'BOOL'
    assert result['declarations'][0]['comment'] == 'Actual native comment'
    assert result['save_performed'] is False


@pytest.mark.parametrize('keep_evidence', [False, True])
@pytest.mark.parametrize('failure', [None, 'write', 'command', 'dialog', 'file', 'confirm', 'wait', 'page', 'visible'])
def test_label_transfer_directory_is_scoped_on_success_and_failure(tmp_path, monkeypatch, keep_evidence, failure):
    from gxworks2 import engineering_labels as owner
    import gxworks2.engineering_driver as native
    driver = object.__new__(native.NativeEngineeringDriver)
    driver.evidence_directory = tmp_path / 'evidence' if keep_evidence else None
    driver._transfers = 0
    driver.ui = SimpleNamespace(READ_CSV_COMMAND=123)
    driver.read_labels = lambda *args: {'raw_bytes': b'current table'}
    monkeypatch.setattr(owner, '_merge_label_csv', lambda *args, **kwargs: b'prepared import')
    directories = []
    original_temp = owner.tempfile.TemporaryDirectory
    def temporary(**kwargs):
        directory = original_temp(dir=tmp_path, **kwargs)
        directories.append(Path(directory.name))
        return directory
    monkeypatch.setattr(owner.tempfile, 'TemporaryDirectory', temporary)
    original_write = Path.write_bytes
    def write(self, data):
        if self.name == 'change.csv' and failure == 'write':
            raise OSError('injected write failure')
        return original_write(self, data)
    monkeypatch.setattr(Path, 'write_bytes', write)
    def stage(name, value=None):
        if failure == name:
            raise NativeEngineeringDriverError('injected ' + name)
        return value
    driver._command = lambda *args: stage('command')
    driver._dialog = lambda *args: stage('dialog', object())
    def filename(dialog, path):
        assert path.read_bytes() == b'prepared import'
        stage('file')
    driver._file_name = filename
    driver._confirm = lambda *args: stage('confirm')
    driver._wait = lambda *args: stage('wait')
    driver._labels_page = lambda *args: stage('page')
    driver._visible = lambda *args, **kwargs: stage('visible', kwargs)
    if failure:
        with pytest.raises((OSError, NativeEngineeringDriverError), match='injected'):
            driver._csv_labels('Main', 'program', [])
    else:
        result = driver._csv_labels('Main', 'program', [])
        assert Path(result['transfer_file']).exists() is keep_evidence
    if keep_evidence:
        assert (driver.evidence_directory / 'labels-001').is_dir()
    else:
        assert len(directories) == 1 and not directories[0].exists()


def action_observation():
    return {'status': 'observed', 'protocol_version': 1, 'native_version': '1.635.0.1',
        'owner': 'Main', 'owner_kind': 'program', 'active_network': 0,
        'identity': dict(process_id=1234, window_handle=200, process_started=789, executable='owned.exe'),
        'data_id': 'current-data-id', 'document_handle': 201, 'view_handle': 202,
        'native_edit_state': {'inline_editor': 0, 'network_clipboard_active': 0, 'base_mode': 0x643,
                             'auto_connect': 0, 'guided_override': 0,
                             'connection_stage': 0, 'pending_connection_network': -1},
        'networks': [{'index': 0, 'nodes': [dict(index=0, address=0x100000, record='input-record',
            name='A', source_kind_code=13, bounds=[3, 2, 5, 4], selected=False),
            dict(index=1, address=0x100078, record='output-record', name='Y0', source_kind_code=14,
                 bounds=[18, 2, 20, 4], selected=False)],
            'wires': [dict(index=0, record='wire-record', start=[5, 3], end=[18, 3], selected=False)], 'junctions': []}]}


def action_plan(observation, kind):
    # Transport fixtures contain only Core's execution fields. PLC direction,
    # type and topology expectations belong to test_gxw_active_graph instead.
    from gxw.active_graph import _action_state, graph_target_anchor, graph_wire_anchor
    plan = dict(kind='active_graph_action', schema_version=1, owner=observation['owner'],
        owner_kind=observation['owner_kind'], network=0, observation_state=_action_state(observation),
        action={'action': kind, 'network': 0}, target=None)
    if kind == 'delete_wire':
        plan['target'] = graph_wire_anchor(observation, {'network': 0, 'id': 'b0:w0'})
    elif kind != 'add_wire':
        plan['target'] = graph_target_anchor(observation, {'network': 0, 'id': 'b0:n0'})
    if kind == 'rebind_terminal':
        plan['action']['symbol'] = 'B'
        plan['expected_data_type'] = 'BOOL'
    elif kind == 'move_node':
        plan['action'].update(x=3, y=6)
    elif kind == 'add_wire':
        plan['action']['via'] = []
        plan['endpoints'] = {'from': {'point': [5, 3]}, 'to': {'point': [18, 3]}}
    return plan


class ActionDriver:
    def __init__(self, state, kind):
        self.state, self.kind, self.events = state, kind, []
        self.mode_pending, self.suppress_mode, self.fail_drag = None, False, False
        self.toggle_pending, self.suppress_toggle = None, False
        self.calibrations = 0
        self.view = SimpleNamespace(handle=202, control_id=lambda: 59648)

    def _active_document(self):
        return SimpleNamespace(window_text=lambda: 'Main [PRG] 程序本体 [结构化梯形图/FBD]',
                               descendants=lambda **kwargs: [self.view])

    def _command(self, value):
        self.events.append(('command', value))
        if value in (0x643, 0x644):
            self.mode_pending = (value, 2)
        elif value == 0x645:
            self.toggle_pending = 2
        elif value == 0x18CD:
            network = self.state['networks'][0]
            key = 'wires' if self.kind == 'delete_wire' else 'nodes'
            network[key] = [e for e in network[key] if not e['selected']]
            for index, element in enumerate(network[key]):
                element['index'] = index

    def _wait(self, predicate, message):
        for _ in range(8):
            result = predicate()
            if result:
                return result
        raise NativeEngineeringDriverError(message)

    def _choose_label(self, name, *, expected_type=None):
        self.events.append(('label', name, expected_type))
        self.state['networks'][0]['nodes'][0]['name'] = name

    def _graph_view(self, name):
        self.events.append(('view', name))
        return self.view

    def _graph_grid(self, view):
        assert view.handle == 202
        self.calibrations += 1
        self.events.append(('grid', self.calibrations))
        return {'calibration': self.calibrations}

    @staticmethod
    def _graph_point(grid, x, y):
        return round(x*20+grid['calibration']), round(y*20+grid['calibration'])

    def _graph_drag(self, view, grid, source, target):
        expected_mode = 0x644 if self.kind == 'add_wire' else 0x643
        assert self.state['native_edit_state']['base_mode'] == expected_mode
        self.events.append(('drag', grid['calibration'], source, target))
        if self.fail_drag:
            raise RuntimeError('native pointer delivery did not return')
        if self.kind == 'add_wire':
            self.state['networks'][0]['wires'] = [dict(index=0, start=[18, 3], end=[5, 3], selected=False)]
        else:
            self.state['networks'][0]['nodes'][0]['bounds'] = [3, 6, 5, 8]


class ActionAdapter(module.ActiveDocumentAdapter):
    def __init__(self, driver):
        super().__init__('not-launched-in-unit-tests.exe')
        self.driver = driver
        self.timeout_on_interconnect = False

    def read_graph(self, driver, *, owner, kind='program', data_id=None):
        assert driver is self.driver
        if driver.mode_pending:
            value, count = driver.mode_pending
            if self.timeout_on_interconnect and value == 0x644:
                self._pending['real-child-id'] = ('retained child handle', {})
                raise NativeEngineeringDriverError('pending read', observation={
                    'status': 'unconfirmed', 'native_task_id': 'real-child-id', 'child_terminated': False})
            count -= 1
            if not count:
                if not driver.suppress_mode:
                    driver.state['native_edit_state']['base_mode'] = value
                driver.mode_pending = None
            else:
                driver.mode_pending = value, count
        if driver.toggle_pending:
            driver.toggle_pending -= 1
            if not driver.toggle_pending and not driver.suppress_toggle:
                driver.state['native_edit_state']['auto_connect'] ^= 1
        driver.events.append(('read_mode', driver.state['native_edit_state']['base_mode']))
        return deepcopy(driver.state)

    def _call(self, request):
        assert request['operation'] == 'select_graph_element'
        key = 'wires' if request['target'].get('element_kind') == 'wire' else 'nodes'
        self.driver.events.append(('select', key))
        network = self.driver.state['networks'][0]
        for current_key in ('nodes', 'wires'):
            for element in network[current_key]:
                element['selected'] = current_key == key and element['index'] == request['target']['index']
        return dict(selection_applied=True, selection_writes=1, target_memory_written=True, own_ui_suspend_balanced=True)


@pytest.mark.parametrize('kind', ['rebind_terminal', 'delete_terminal', 'move_node', 'delete_wire', 'add_wire'])
def test_apply_action_uses_verified_selection_and_fresh_observation_without_saving(kind):
    before = action_observation()
    if kind == 'add_wire':
        before['networks'][0]['wires'] = []
    driver = ActionDriver(deepcopy(before), kind)
    adapter = ActionAdapter(driver)
    result = adapter.apply_graph_action(driver, before, action_plan(before, kind))
    commands = [event[1] for event in driver.events if event[0] == 'command']
    assert result['native_graph_action'] == dict(action=kind, network=0, status='observed', save_performed=False)
    assert result['status'] == 'observed'
    assert 'verified' not in result.values()
    if kind == 'add_wire':
        assert commands == [0x643, 0x644, 0x643]
        entered = driver.events.index(('command', 0x644))
        first_drag = next(i for i, event in enumerate(driver.events) if event[0] == 'drag')
        assert ('read_mode', 0x643) in driver.events[entered+1:first_drag]
        assert ('read_mode', 0x644) in driver.events[entered+1:first_drag]
        assert ('read_mode', 0x643) in driver.events[first_drag+1:]
    else:
        assert ('select', 'wires' if kind == 'delete_wire' else 'nodes') in driver.events
        assert result['selection_confirmation']['selection_applied']
        if kind == 'rebind_terminal':
            assert commands == [0x643, 0x60F]
            assert ('label', 'B', 'Bit') in driver.events
        elif kind in ('delete_terminal', 'delete_wire'):
            assert commands == [0x643, 0x18CD]
        else:
            assert commands == [0x643]
            assert driver.calibrations == 1


@pytest.mark.parametrize('block', ['inline', 'network_clipboard', 'inactive', 'identity', 'source', 'pending'])
def test_native_precondition_failure_never_issues_an_edit_command(block):
    before = action_observation()
    driver = ActionDriver(deepcopy(before), 'delete_terminal')
    adapter = ActionAdapter(driver)
    plan = action_plan(before, 'delete_terminal')
    if block == 'inline':
        driver.state['native_edit_state']['inline_editor'] = 999
    elif block == 'network_clipboard':
        driver.state['native_edit_state']['network_clipboard_active'] = 1
    elif block == 'inactive':
        driver.state['active_network'] = 1
    elif block == 'identity':
        driver.state['identity']['process_id'] += 1
    elif block == 'source':
        driver.state['networks'][0]['nodes'][0]['name'] = 'Unrequested'
    else:
        adapter._pending['retained-child'] = (object(), {})
    with pytest.raises(NativeEngineeringDriverError):
        adapter.apply_graph_action(driver, before, plan)
    assert not any(event[0] in ('command', 'select', 'drag') for event in driver.events)


def test_add_wire_does_not_drag_before_asynchronous_mode_command_is_observed():
    before = action_observation()
    before['networks'][0]['wires'] = []
    driver = ActionDriver(deepcopy(before), 'add_wire')
    driver.suppress_mode = True
    with pytest.raises(NativeEngineeringDriverError) as caught:
        ActionAdapter(driver).apply_graph_action(driver, before, action_plan(before, 'add_wire'))
    assert caught.value.observation['status'] == 'rejected'
    assert not any(event[0] == 'drag' for event in driver.events)


def test_mode_read_timeout_retains_pending_child_and_never_sends_cleanup_or_replay():
    before = action_observation()
    before['networks'][0]['wires'] = []
    driver = ActionDriver(deepcopy(before), 'add_wire')
    adapter = ActionAdapter(driver)
    adapter.timeout_on_interconnect = True
    with pytest.raises(NativeEngineeringDriverError) as caught:
        adapter.apply_graph_action(driver, before, action_plan(before, 'add_wire'))
    evidence = caught.value.observation
    assert evidence['status'] == 'unconfirmed'
    assert evidence['child_terminated'] is False
    assert evidence['native_task_id'] == 'real-child-id'
    assert 'real-child-id' in adapter._pending
    assert [e[1] for e in driver.events if e[0] == 'command'] == [0x643, 0x644]
    assert not any(event[0] == 'drag' for event in driver.events)


def test_failed_drag_returns_unconfirmed_and_does_not_retry_the_action():
    before = action_observation()
    driver = ActionDriver(deepcopy(before), 'move_node')
    driver.fail_drag = True
    with pytest.raises(NativeEngineeringDriverError) as caught:
        ActionAdapter(driver).apply_graph_action(driver, before, action_plan(before, 'move_node'))
    assert caught.value.observation['status'] == 'unconfirmed'
    assert caught.value.observation['mutation_may_have_started']
    assert sum(e[0] == 'drag' for e in driver.events) == 1


def test_each_explicit_wire_segment_recalibrates_the_current_view_before_dragging():
    before = action_observation()
    before['networks'][0]['wires'] = []
    driver = ActionDriver(deepcopy(before), 'add_wire')
    plan = action_plan(before, 'add_wire')
    plan['action']['via'] = [[5, 6], [18, 6]]
    ActionAdapter(driver).apply_graph_action(driver, before, plan)
    assert [e[1] for e in driver.events if e[0] == 'grid'] == [1, 2, 3]
    assert [e[1] for e in driver.events if e[0] == 'drag'] == [1, 2, 3]


def test_explicit_wire_waits_for_manual_mode_and_restores_original_automatic_setting():
    before = action_observation()
    before['networks'][0]['wires'] = []
    before['native_edit_state']['auto_connect'] = 1
    driver = ActionDriver(deepcopy(before), 'add_wire')
    original_drag = driver._graph_drag
    def manual_drag(*args):
        assert driver.state['native_edit_state']['auto_connect'] == 0
        original_drag(*args)
    driver._graph_drag = manual_drag
    result = ActionAdapter(driver).apply_graph_action(driver, before, action_plan(before, 'add_wire'))
    assert [e[1] for e in driver.events if e[0] == 'command'] == [0x643, 0x645, 0x644, 0x643, 0x645]
    assert result['native_edit_state']['auto_connect'] == 1
    assert sum(e[0] == 'drag' for e in driver.events) == 1


def test_move_preserves_wire_geometry_when_native_automatic_connection_would_extend_a_touching_port():
    before = action_observation()
    before['networks'][0]['wires'] = []
    before['native_edit_state']['auto_connect'] = 1
    driver = ActionDriver(deepcopy(before), 'move_node')
    original_drag = driver._graph_drag
    def native_drag(*args):
        # Independently observed Q03UDV/en behavior: moving a touching terminal
        # with AutoConnect enabled adds a wire, although only move was requested.
        automatic = driver.state['native_edit_state']['auto_connect']
        original_drag(*args)
        if automatic:
            driver.state['networks'][0]['wires'].append(
                dict(index=0, start=[3, 3], end=[5, 3], selected=False))
    driver._graph_drag = native_drag
    result = ActionAdapter(driver).apply_graph_action(driver, before, action_plan(before, 'move_node'))
    assert result['networks'][0]['nodes'][0]['bounds'] == [3, 6, 5, 8]
    assert result['networks'][0]['wires'] == []
    assert result['native_edit_state']['auto_connect'] == 1
    assert [e[1] for e in driver.events if e[0] == 'command'] == [0x643, 0x645, 0x645]
    assert sum(e[0] == 'drag' for e in driver.events) == 1


@pytest.mark.parametrize('kind', ['add_wire', 'move_node'])
@pytest.mark.parametrize('field,value', [('auto_connect', None), ('auto_connect', 2),
    ('guided_override', 1), ('connection_stage', 2), ('pending_connection_network', 0)])
def test_unconfirmed_connection_settings_reject_before_any_native_command(field, value, kind):
    before = action_observation()
    before['networks'][0]['wires'] = []
    before['native_edit_state'][field] = value
    driver = ActionDriver(deepcopy(before), kind)
    with pytest.raises(NativeEngineeringDriverError):
        ActionAdapter(driver).apply_graph_action(driver, before, action_plan(before, kind))
    assert not any(e[0] in ('command', 'drag') for e in driver.events)


@pytest.mark.parametrize('kind', ['add_wire', 'move_node'])
def test_manual_mode_toggle_without_readback_never_starts_or_repeats_a_wire(kind):
    before = action_observation()
    before['networks'][0]['wires'] = []
    before['native_edit_state']['auto_connect'] = 1
    driver = ActionDriver(deepcopy(before), kind)
    driver.suppress_toggle = True
    with pytest.raises(NativeEngineeringDriverError):
        ActionAdapter(driver).apply_graph_action(driver, before, action_plan(before, kind))
    assert [e[1] for e in driver.events if e[0] == 'command'] == [0x643, 0x645]
    assert not any(e[0] == 'drag' for e in driver.events)


def test_fresh_pending_connection_blocks_a_stale_clean_observation():
    before = action_observation()
    before['networks'][0]['wires'] = []
    driver = ActionDriver(deepcopy(before), 'add_wire')
    driver.state['native_edit_state']['connection_stage'] = 2
    with pytest.raises(NativeEngineeringDriverError):
        ActionAdapter(driver).apply_graph_action(driver, before, action_plan(before, 'add_wire'))
    assert not any(e[0] in ('command', 'drag') for e in driver.events)


def test_unfinished_segment_stops_before_the_next_requested_segment():
    before = action_observation()
    before['networks'][0]['wires'] = []
    driver = ActionDriver(deepcopy(before), 'add_wire')
    plan = action_plan(before, 'add_wire')
    plan['action']['via'] = [[5, 6], [18, 6]]
    original_drag = driver._graph_drag
    def incomplete_drag(*args):
        original_drag(*args)
        driver.state['native_edit_state']['pending_connection_network'] = 0
    driver._graph_drag = incomplete_drag
    with pytest.raises(NativeEngineeringDriverError) as caught:
        ActionAdapter(driver).apply_graph_action(driver, before, plan)
    assert caught.value.observation['status'] == 'unconfirmed'
    assert sum(e[0] == 'drag' for e in driver.events) == 1
    assert [e[1] for e in driver.events if e[0] == 'command'] == [0x643, 0x644]


def test_u_route_draws_both_horizontal_legs_before_the_vertical_join():
    before = action_observation()
    before['networks'][0]['wires'] = []
    driver = ActionDriver(deepcopy(before), 'add_wire')
    plan = action_plan(before, 'add_wire')
    plan['endpoints']['from']['point'] = [5, 8]
    plan['action']['via'] = [[16, 8], [16, 3]]
    ActionAdapter(driver).apply_graph_action(driver, before, plan)
    assert [e[2:] for e in driver.events if e[0] == 'drag'] == [
        ((363, 63), (323, 63)), ((324, 164), (104, 164)), ((325, 65), (325, 165))]


def test_fb_move_refuses_unverified_grid_mapping_before_any_native_command():
    before = action_observation()
    before['owner_kind'] = 'fb'
    driver = ActionDriver(deepcopy(before), 'move_node')
    with pytest.raises(RuntimeError, match='Program'):
        ActionAdapter(driver).apply_graph_action(driver, before, action_plan(before, 'move_node'))
    assert driver.events == [] and driver.calibrations == 0


class TerminalDriver(ActionDriver):
    """Native transitions are frozen 062 captures, independent of the planner."""
    def __init__(self, values, *, behavior='success', shift=0):
        super().__init__(deepcopy(values['before']), 'add_terminal')
        self.values, self.behavior, self.shift = values, behavior, shift
        self.armed, self.inline_live, self.clicked = False, False, False
        self.view = SimpleNamespace(handle=self.state['view_handle'], control_id=lambda:59648,
                                    click_input=self._click_input)

    def _active_document(self):
        return SimpleNamespace(handle=self.state['document_handle'],
            window_text=lambda:self.state['owner']+' [PRG] 程序本体 [结构化梯形图/FBD]',
            descendants=lambda **kwargs:[self.view])

    def _graph_grid(self, view):
        assert view.handle == self.state['view_handle']
        self.calibrations += 1
        self.events.append(('grid',self.calibrations))
        grid=deepcopy(self.values['placement']['grid'])
        grid['x'] += self.shift
        return grid

    def _graph_placement_point(self, view, grid, x, y):
        assert view.handle == self.state['view_handle']
        return self._graph_point(grid, x+2, y+1)

    @staticmethod
    def _graph_point(grid,x,y):
        from gxworks2.engineering_driver import NativeEngineeringDriver
        return NativeEngineeringDriver._graph_point(grid,x,y)

    def _click_input(self, *, coords):
        assert self.armed, 'The bounded native placement handler must finish before its only click'
        self.events.append(('click',coords))
        self.clicked = True
        if self.behavior=='click_failure':
            raise RuntimeError('Pointer delivery completion is unknown')
        if self.behavior=='no_placement':
            return
        self.state = deepcopy(self.values['placed'])
        self.inline_live = True
        if self.behavior=='wrong_geometry':
            self.state['networks'][0]['nodes'][7]['bounds']=[4,7,6,9]
        elif self.behavior=='old_source_changed':
            self.state['networks'][0]['nodes'][1]['name']='Probe_B002'

    def _choose_label(self,name,*,expected_type=None):
        self.events.append(('label',name,expected_type))
        if self.behavior=='chooser_failure':
            raise RuntimeError('Label chooser did not confirm its requested row')
        self.state = deepcopy(self.values['after'])
        if self.behavior=='wrong_symbol':
            self.state['networks'][0]['nodes'][7]['name']='Probe_A001'
        elif self.behavior=='old_source_after_label':
            self.state['networks'][0]['nodes'][1]['name']='Probe_B002'


class TerminalAdapter(ActionAdapter):
    def read_graph(self,driver,*,owner,kind='program',data_id=None):
        if driver.behavior=='pending_native_read' and driver.clicked:
            self._pending['owned-live-child'] = ('the retained live child', {})
            raise NativeEngineeringDriverError('Native child still running',observation={
                'status':'unconfirmed','native_task_id':'owned-live-child','child_terminated':False})
        return super().read_graph(driver,owner=owner,kind=kind,data_id=data_id)


def terminal_transport(monkeypatch, *, behavior='success', shift=0):
    import sys
    from gxw.active_graph import prepare_active_graph_action
    from tests.test_gxw_active_graph import input_action, live_input_witness
    values,sources = live_input_witness()
    driver=TerminalDriver(values,behavior=behavior,shift=shift)
    adapter=TerminalAdapter(driver)
    before=deepcopy(values['before'])
    prepared=prepare_active_graph_action(before,input_action(),sources=sources,device_profile='FX3U')
    inline=values['placed']['native_edit_state']['inline_editor']
    def send(handle,message,command,zero,flags,timeout):
        assert (handle,message,command,zero,flags,timeout)==(before['identity']['window_handle'],0x111,0x655,0,2,1500)
        driver.events.append(('synchronous_command',command))
        if behavior=='command_timeout':
            raise TimeoutError('The one native placement command did not return')
        driver.armed=True
    def post(handle,message,key,flags):
        assert handle==inline and key==13
        assert (message,flags) in ((0x100,0x001C0001),(0x101,0xC01C0001))
        driver.events.append(('inline_enter',message))
        if message==0x101 and behavior!='inline_timeout':
            driver.inline_live=False
            driver.state=deepcopy(values['idle'])
    monkeypatch.setitem(sys.modules,'win32gui',SimpleNamespace(SendMessageTimeout=send,PostMessage=post,
        IsWindow=lambda handle:handle==inline and driver.inline_live,
        IsWindowVisible=lambda handle:handle==inline and driver.inline_live,
        IsChild=lambda parent,child:parent==before['view_handle'] and child==inline and behavior!='foreign_inline_parent'))
    monkeypatch.setitem(sys.modules,'win32process',SimpleNamespace(GetWindowThreadProcessId=lambda handle:
        (777,999 if behavior=='foreign_inline_pid' else before['identity']['process_id'])))
    return driver,adapter,before,prepared,sources


@pytest.mark.parametrize('shift',[0,31])
def test_add_terminal_transport_uses_one_native_click_owned_enter_and_actual_pending_anchor(monkeypatch,shift):
    from gxw.active_graph import verify_active_graph_action
    driver,adapter,before,prepared,sources=terminal_transport(monkeypatch,shift=shift)
    result=adapter.apply_graph_action(driver,before,prepared)
    assert [event for event in driver.events if event[0]=='synchronous_command']==[('synchronous_command',0x655)]
    assert [event for event in driver.events if event[0]=='click']==[('click',(243+shift,195))]
    assert [event for event in driver.events if event[0]=='inline_enter']==[('inline_enter',0x100),('inline_enter',0x101)]
    assert [event for event in driver.events if event[0]=='command']==[('command',0x643),('command',0x60F)]
    assert [event for event in driver.events if event[0]=='label']==[('label','Live_Enable','Bit')]
    assert [event for event in driver.events if event[0]=='select']==[('select','nodes')]
    assert not any(event[0]=='drag' for event in driver.events)
    assert result['native_graph_action']['status']=='observed'
    assert result['native_graph_action']['save_performed'] is False
    assert verify_active_graph_action(before,result,prepared,sources=sources)['status']=='verified'


@pytest.mark.parametrize('behavior',['command_timeout','click_failure','no_placement','wrong_geometry',
    'old_source_changed','foreign_inline_pid','foreign_inline_parent','inline_timeout','chooser_failure',
    'wrong_symbol','old_source_after_label','pending_native_read'])
def test_add_terminal_transport_failure_remains_unconfirmed_and_never_repeats_placement(monkeypatch,behavior):
    driver,adapter,before,prepared,sources=terminal_transport(monkeypatch,behavior=behavior)
    with pytest.raises(NativeEngineeringDriverError) as caught:
        adapter.apply_graph_action(driver,before,prepared)
    assert caught.value.observation['status']=='unconfirmed'
    assert caught.value.observation['mutation_may_have_started'] is True
    assert caught.value.observation['save_performed'] is False
    assert sum(event[0]=='synchronous_command' for event in driver.events)==1
    assert sum(event[0]=='click' for event in driver.events)==(0 if behavior=='command_timeout' else 1)
    assert sum(event==('inline_enter',0x100) for event in driver.events)<=1
    assert sum(event==('inline_enter',0x101) for event in driver.events)<=1
    assert sum(event[0]=='label' for event in driver.events)<=1
    if behavior in ('wrong_geometry','old_source_changed','foreign_inline_pid','foreign_inline_parent','pending_native_read'):
        assert not any(event[0]=='inline_enter' for event in driver.events)
    if behavior=='pending_native_read':
        assert 'owned-live-child' in adapter._pending
        assert caught.value.observation['child_terminated'] is False


def test_add_terminal_does_not_open_a_pending_editor_before_original_preconditions_match(monkeypatch):
    driver,adapter,before,prepared,sources=terminal_transport(monkeypatch)
    driver.state['native_edit_state']['inline_editor']=777
    with pytest.raises(NativeEngineeringDriverError) as caught:
        adapter.apply_graph_action(driver,before,prepared)
    assert caught.value.observation['status']=='rejected'
    assert not any(event[0] in ('command','synchronous_command','click','inline_enter','label') for event in driver.events)

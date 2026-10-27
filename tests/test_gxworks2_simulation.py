from dataclasses import dataclass

import pytest
from hypothesis import given, strategies as st

from gxworks2.simulation import (
    GXSimulator2PreparationService,
    NativeGXWorks2SimulationAutomation,
)
from simulator.runtime import SimulatorGatewayRuntime


@pytest.fixture(autouse=True)
def _simulator_dependencies_installed(monkeypatch):
    monkeypatch.setattr(
        "simulator.gateway.detect_simulator_environment",
        lambda: {
            "simulator_installed": True,
            "mx_component_installed": True,
            "simulator_processes": [],
        },
    )


@dataclass
class _Session:
    process_id: int = 1
    window_handle: int = 2
    project_open: bool = True
    project_name: str = "fixture"


class _Finder:
    def __init__(self, session=None):
        self.session = session

    def find_running(self):
        return self.session


class _ProjectAutomation:
    def __init__(self, *, project_open=True, program_ready=True):
        self.project_open = project_open
        self.program_ready = program_ready

    def inspect_project(self, _session):
        return {
            "automation_available": True,
            "project_open": self.project_open,
            "program_ready": self.program_ready,
            "project_name": "fixture",
        }


class _SimulationAutomation:
    def __init__(self, runtime):
        self.runtime = runtime
        self.invocations = 0

    def start_stop_simulation(self, _session):
        self.invocations += 1
        self.runtime.ready = not self.runtime.ready
        return {"invoked": True, "command": "开始/停止模拟"}

    def handle_startup_dialogs(self, _session):
        return {"handled": False, "failure": ""}


class _Runtime:
    def __init__(self, *, ready=False, mx=True):
        self.ready = ready
        self.mx = mx
        self.disconnected = False

    def ensure_gateway(self):
        return {
            "service": "plc-ai-gx-simulator2-gateway",
            "simulator_only": True,
            "mx_component_available": self.mx,
        }

    def health(self):
        return self.ensure_gateway()

    def probe_simulator(self):
        return {
            "ready": self.ready,
            "health": self.ensure_gateway(),
            "error": "offline" if not self.ready else "",
        }

    def disconnect(self):
        self.disconnected = True


class _RuntimeClient:
    def __init__(self, run_value):
        self.run_value = run_value
        self.disconnected = False

    def connect(self):
        return {"route": "GX Simulator2"}

    def read_many(self, addresses):
        assert addresses == ["M8000"]
        return {"M8000": self.run_value}

    def disconnect(self):
        self.disconnected = True


def _service(runtime, *, session=True, project_open=True, program_ready=True):
    automation = _SimulationAutomation(runtime)
    return (
        GXSimulator2PreparationService(
            finder=_Finder(_Session() if session else None),
            project_automation=_ProjectAutomation(
                project_open=project_open,
                program_ready=program_ready,
            ),
            simulation_automation=automation,
            runtime=runtime,
        ),
        automation,
    )


@pytest.mark.parametrize('activated_by', ['bring', 'set', 'focus', None])
def test_simulation_foreground_uses_the_actual_bound_window_acknowledgement(monkeypatch, activated_by):
    import ctypes
    win32gui = pytest.importorskip('win32gui')
    win32process = pytest.importorskip('win32process')
    from types import SimpleNamespace
    from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation
    session = SimpleNamespace(process_id=1,window_handle=2,process_started='owned',
        executable='owned/GD2.exe',software_version='1.635.0.1')
    monkeypatch.setattr('gxworks2.finder.GXWorks2Finder.find_running', lambda *args, **kwargs: session)
    state = dict(foreground=1, sets=0, joins=[])
    monkeypatch.setattr(win32gui, 'GetForegroundWindow', lambda: state['foreground'])
    monkeypatch.setattr(win32gui, 'IsIconic', lambda handle: False)
    monkeypatch.setattr(win32process, 'GetWindowThreadProcessId', lambda handle: (handle+20,1))
    monkeypatch.setattr(ctypes.windll.kernel32, 'GetCurrentThreadId', lambda: 10)
    monkeypatch.setattr(ctypes.windll.user32, 'AttachThreadInput',
        lambda a,b,join: state['joins'].append((a,b,join)) or True)
    def bring(handle):
        if activated_by == 'bring':state['foreground']=handle
    def set_foreground(handle):
        state['sets'] += 1
        if activated_by == 'set':state['foreground']=handle
        return 0  # Return codes do not replace observing the actual HWND.
    monkeypatch.setattr(win32gui, 'BringWindowToTop', bring)
    monkeypatch.setattr(win32gui, 'SetForegroundWindow', lambda *args: pytest.fail('Redundant Win32 wrapper must not abort activation'))
    monkeypatch.setattr(ctypes.windll.user32, 'SetForegroundWindow', set_foreground)
    ui = PywinautoGXWorks2UIAutomation()
    def focus():
        if activated_by == 'focus':state['foreground']=session.window_handle
    monkeypatch.setattr(ui, '_main_window', lambda _: SimpleNamespace(set_focus=focus))
    if activated_by is None:
        with pytest.raises(RuntimeError, match='前台'):
            ui._foreground_native_session(session)
    else:
        ui._foreground_native_session(session)
        assert state['foreground'] == session.window_handle
    assert state['sets'] == (activated_by != 'bring')
    assert len([r for r in state['joins'] if r[2]]) == len([r for r in state['joins'] if not r[2]])


def test_prepare_reuses_running_simulator_without_toggling_menu():
    runtime = _Runtime(ready=True)
    service, automation = _service(runtime)
    result = service.prepare(timeout=0.1)
    assert result.success
    assert result.details["already_running"] is True
    assert automation.invocations == 0


def test_prepare_invokes_named_command_and_requires_route_probe():
    runtime = _Runtime(ready=False)
    service, automation = _service(runtime)
    result = service.prepare(timeout=0.1)
    assert result.success
    assert result.details["already_running"] is False
    assert automation.invocations == 1


def test_prepare_rejects_missing_project_program_and_mx_component():
    runtime = _Runtime()
    missing_project, _ = _service(runtime, project_open=False)
    assert not missing_project.prepare(timeout=0.1).success

    missing_program, _ = _service(runtime, program_ready=False)
    assert not missing_program.prepare(timeout=0.1).success

    no_mx, automation = _service(_Runtime(mx=False))
    result = no_mx.prepare(timeout=0.1)
    assert not result.success
    assert result.stage == "mx_component"
    assert automation.invocations == 0


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('implementation', ['st', 'fbd'])
@pytest.mark.parametrize('boundary', ['matched', 'other_owner', 'fb_only', 'missing_source',
                                     'duplicate_tree', 'unknown_cpu', 'parameter_only'])
def test_simulation_inspection_binds_structured_program_without_enabling_csv(
        monkeypatch, language, implementation, boundary):
    from types import SimpleNamespace
    from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation
    from gxworks2.project_profiles import native_project_profile
    from gxworks2.native_locale import native_tree_path
    from gxw.native_locale import program_title
    automation = PywinautoGXWorks2UIAutomation(program='SelectedProgram')
    baseline = dict(automation_available=True, project_open=True, program_ready=False,
                    read_csv_available=False, program_name='SelectedProgram')
    monkeypatch.setattr(automation, 'inspect_project', lambda _: dict(baseline))
    monkeypatch.setattr('gxworks2.native_locale.inspect_native_language', lambda *_: language)
    title = program_title('OtherProgram' if boundary == 'other_owner' else 'SelectedProgram',
                          kind='fb' if boundary == 'fb_only' else 'program',
                          implementation=implementation, language=language)
    monkeypatch.setattr(automation, '_child_window_titles', lambda _: [title])
    path = native_tree_path(('程序部件', '程序', 'SelectedProgram', '程序本体'), language)
    def source(selected, *, exact):
        assert selected == path and exact is True
        return None if boundary == 'missing_source' else object()
    tree = SimpleNamespace(control_id=lambda: 12051, get_item=source)
    trees = [tree, tree] if boundary == 'duplicate_tree' else [tree]
    window = SimpleNamespace(descendants=lambda **_: trees)
    monkeypatch.setattr(automation, '_main_window', lambda _: window)
    profile = None if boundary == 'unknown_cpu' else native_project_profile(
        'LJ72MS15' if boundary == 'parameter_only' else 'Q03UDV')
    monkeypatch.setattr(automation, '_current_native_project_profile', lambda _: profile)
    session = SimpleNamespace(process_id=1, executable=r'D:\GXWORKS2\GPPW2\GD2.exe')
    state = automation.inspect_simulation_project(session)
    assert state['program_ready'] is (boundary == 'matched')
    assert state['read_csv_available'] is False
    assert automation.inspect_project(session) == baseline
    if boundary == 'matched':
        assert state['program_language'] == implementation and state['cpu_menu'] == 'Q03UDV'


def test_prepare_uses_simulation_inspection_for_the_selected_structured_program():
    runtime = _Runtime(ready=False)
    service, automation = _service(runtime, program_ready=False)
    service.project_automation.inspect_simulation_project = lambda _: dict(
        automation_available=True, project_open=True, program_ready=True,
        program_name='POU_01', read_csv_available=False)
    result = service.prepare(timeout=0.1)
    assert result.success and automation.invocations == 1


def test_preflight_explains_that_gxworks2_simulator_and_external_api_are_separate(
    monkeypatch,
):
    monkeypatch.setattr(
        "simulator.gateway.detect_simulator_environment",
        lambda: {
            "simulator_installed": True,
            "mx_component_installed": False,
            "simulator_processes": ["fxsimrun2.exe"],
        },
    )
    runtime = _Runtime(mx=False)
    service, automation = _service(runtime)

    result = service.preflight()

    assert not result.success
    assert result.stage == "mx_component"
    assert "GX Works2 可以单独运行仿真" in result.message
    assert result.details["required_architecture"] == "x86"
    assert automation.invocations == 0


def test_stop_disconnects_gateway_before_toggling_simulation():
    runtime = _Runtime(ready=True)
    service, automation = _service(runtime)
    result = service.stop_if_running(timeout=0.5)
    assert result.success
    assert runtime.disconnected
    assert automation.invocations == 1
    assert runtime.ready is False
    assert result.details["verification_samples"] == 3


def test_stop_starts_cold_gateway_before_deciding_simulator_is_not_running():
    class ColdRuntime(_Runtime):
        def __init__(self):
            super().__init__(ready=True)
            self.gateway_starts = 0

        def health(self):
            return None

        def ensure_gateway(self):
            self.gateway_starts += 1
            return super().ensure_gateway()

    runtime = ColdRuntime()
    service, automation = _service(runtime)

    result = service.stop_if_running(timeout=0.5)

    assert result.success
    assert runtime.gateway_starts >= 1
    assert runtime.disconnected
    assert automation.invocations == 1
    assert runtime.ready is False


def test_prepare_fails_cleanly_when_gxworks2_is_not_running():
    runtime = _Runtime()
    service, automation = _service(runtime, session=False)
    result = service.prepare(timeout=0.1)
    assert not result.success
    assert "GX Works2" in result.message
    assert automation.invocations == 0


def test_runtime_probe_requires_m8000_run_monitor(monkeypatch):
    monkeypatch.setattr('simulator.gateway.detect_simulator_environment',
        lambda: {'simulator_processes': ['fxsimrun2.exe']})
    runtime = SimulatorGatewayRuntime(executable="missing.exe")
    monkeypatch.setattr(
        runtime,
        "ensure_gateway",
        lambda: {"mx_component_available": True, "simulator_only": True},
    )
    stopped_client = _RuntimeClient(0)
    monkeypatch.setattr(runtime, "client", lambda timeout=2.0: stopped_client)

    stopped = runtime.probe_simulator()

    assert stopped["connected"] is True
    assert stopped["cpu_run"] is False
    assert stopped["ready"] is False
    assert stopped_client.disconnected is True

    running_client = _RuntimeClient(1)
    monkeypatch.setattr(runtime, "client", lambda timeout=2.0: running_client)
    running = runtime.probe_simulator()
    assert running["ready"] is True
    assert running["run_monitor"] == 1
    assert running_client.disconnected is True


def test_runtime_probe_does_not_open_mx_for_a_manager_without_a_simulator_cpu(monkeypatch):
    runtime = SimulatorGatewayRuntime(executable='missing.exe')
    monkeypatch.setattr(runtime, 'ensure_gateway', lambda: {'mx_component_available': True})
    monkeypatch.setattr('simulator.gateway.detect_simulator_environment',
        lambda: {'simulator_processes': ['simmanager.exe']})
    monkeypatch.setattr(runtime, 'client', lambda **_kwargs: pytest.fail('Observational probe opened an absent CPU'))
    result = runtime.probe_simulator()
    assert result['ready'] is False and result['connected'] is False


@pytest.mark.parametrize('finish', ['run', 'read_error', 'caller_error', 'cpu_gone'])
def test_startup_probe_reuses_only_the_attested_connection_and_always_closes(monkeypatch, finish):
    from types import SimpleNamespace
    runtime = SimulatorGatewayRuntime(executable='missing.exe')
    events = []
    values = iter([0, 1])
    processes = ['fxsimrun2.exe']
    monkeypatch.setattr('simulator.gateway.detect_simulator_environment',
        lambda: {'simulator_processes': processes})
    monkeypatch.setattr(runtime, 'ensure_gateway', lambda: {'mx_component_available': True})
    def read(plan, binding):
        assert binding == 'selected-target' and [row['key'] for row in plan] == ['M8000']
        events.append('bound_read')
        if finish == 'read_error' and events.count('bound_read') == 2:
            raise RuntimeError('Simulator target changed')
        return {'M8000': next(values)}
    client = SimpleNamespace(
        connect=lambda: events.append('open') or {'target_binding': 'selected-target'},
        read_bound=read, disconnect=lambda: events.append('close'),
        read_many=lambda _: pytest.fail('Retained observations must attest the target'))
    monkeypatch.setattr(runtime, 'client', lambda **_: client)
    try:
        with runtime.startup_probe() as probe:
            first = probe()
            assert first['connected'] and not first['ready']
            assert events == ['open', 'bound_read']
            if finish == 'caller_error':
                raise ValueError('caller failed during transfer')
            if finish == 'cpu_gone':
                processes.clear()
            second = probe()
            assert second['ready'] is (finish == 'run')
            if finish != 'run':
                assert second['connected'] is False
    except ValueError:
        assert finish == 'caller_error'
    assert events.count('open') == events.count('close') == 1


@pytest.mark.parametrize('probe_result', ['ready', 'stopped', 'error'])
def test_unattested_legacy_connection_is_not_retained_between_startup_observations(monkeypatch, probe_result):
    runtime = SimulatorGatewayRuntime(executable='missing.exe')
    client = _RuntimeClient(1 if probe_result == 'ready' else 0)
    closed = []
    client.disconnect = lambda: closed.append(True)
    if probe_result == 'error':
        def fail(_): raise RuntimeError('read failed')
        client.read_many = fail
    monkeypatch.setattr('simulator.gateway.detect_simulator_environment',
        lambda: {'simulator_processes': ['fxsimrun2.exe']})
    monkeypatch.setattr(runtime, 'ensure_gateway', lambda: {'mx_component_available': True})
    monkeypatch.setattr(runtime, 'client', lambda **_: client)
    with runtime.startup_probe() as probe:
        for count in range(1, 3):
            assert probe()['ready'] is (probe_result == 'ready')
            assert len(closed) == count
    assert len(closed) == 2


def test_startup_connection_can_open_during_download_without_reading_the_cpu(monkeypatch):
    from types import SimpleNamespace
    runtime = SimulatorGatewayRuntime(executable='missing.exe')
    events = []
    monkeypatch.setattr('simulator.gateway.detect_simulator_environment',
        lambda: {'simulator_processes': ['fxsimrun2.exe']})
    monkeypatch.setattr(runtime, 'ensure_gateway', lambda: {'mx_component_available': True})
    client = SimpleNamespace(
        connect=lambda: events.append('open') or {'target_binding': 'selected-target'},
        read_bound=lambda *_: events.append('read') or {'M8000': 1},
        disconnect=lambda: events.append('close'))
    monkeypatch.setattr(runtime, 'client', lambda **_: client)
    with runtime.startup_probe() as probe:
        opening = probe(read_run=False)
        assert opening['connected'] and not opening['ready'] and opening['cpu_run'] is None
        assert events == ['open']
        assert probe()['ready'] is True
        assert events == ['open', 'read']
    assert events == ['open', 'read', 'close']


@pytest.mark.parametrize('listener', [False, True, None])
def test_runtime_skips_refused_connect_only_when_listener_absence_is_proven(monkeypatch, listener):
    from types import SimpleNamespace
    runtime = SimulatorGatewayRuntime(executable='missing.exe')
    calls = []
    health = dict(service='plc-ai-gx-simulator2-gateway', simulator_only=True)
    monkeypatch.setattr('simulator.runtime._loopback_listener_present', lambda _: listener)
    monkeypatch.setattr(runtime, 'client', lambda **_: SimpleNamespace(
        health=lambda: calls.append('http_health') or health))
    assert runtime.health() == (None if listener is False else health)
    assert calls == ([] if listener is False else ['http_health'])


def test_native_listener_inventory_distinguishes_listening_closed_and_unsupported_endpoints():
    import os
    import socket
    from simulator.runtime import _loopback_listener_present
    if os.name != 'nt':
        pytest.skip('Windows TCP listener inventory')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        listener.listen(1)
        url = 'http://127.0.0.1:' + str(port)
        assert _loopback_listener_present(url) is True
    assert _loopback_listener_present(url) is False
    assert _loopback_listener_present('http://[::1]:' + str(port)) is None


def test_plc_write_completion_dialog_is_closed_before_ready_probe(monkeypatch):
    class DialogAutomation:
        @staticmethod
        def _native_dialog_handles(_session):
            return [42]

        @staticmethod
        def _native_dialog_text(_handle):
            return "PLC写入 参数 写入：完成 程序（MAIN）写入：完成 PLC写入：结束 关闭"

        @staticmethod
        def _native_descendants(_handle):
            return []

        @staticmethod
        def _native_window_title(_handle):
            return ""

    automation = NativeGXWorks2SimulationAutomation(DialogAutomation())
    clicked = []
    monkeypatch.setattr(
        automation,
        "_click_dialog_button",
        lambda handle, pattern: clicked.append((handle, pattern.pattern)) or True,
    )

    result = automation.handle_startup_dialogs(_Session())

    assert result == {"handled": True, "pending": True, "failure": "", "transfer_complete": True}
    assert clicked and clicked[0][0] == 42


def test_custom_gxworks2_menu_without_bound_resource_never_invokes_or_sends_access_key():
    class Window:
        def __init__(self):
            self.keys = []

        def set_focus(self):
            pass

        def type_keys(self, keys):
            self.keys.append(keys)

    class Command:
        invoked = False

        @staticmethod
        def window_text():
            return "模拟开始/停止(S)"

        def invoke(self):
            self.invoked = True

    window = Window()
    command = Command()

    class UIAutomation:
        @staticmethod
        def _open_native_debug_menu(_session, _pattern):
            window.keys.append('bound_debug_menu')
        @staticmethod
        def _main_window(_session):
            return window

        @staticmethod
        def _find_menu_item(_window, _pattern, enabled=True):
            assert enabled is True
            return command

        @staticmethod
        def _cancel_native_menu(_session):
            window.keys.append('cancel')

    automation = NativeGXWorks2SimulationAutomation(UIAutomation())
    with pytest.raises(RuntimeError, match='原生资源身份'):
        automation._invoke_popup_command(_Session(), "no native menu")

    assert command.invoked is False
    assert window.keys == ['bound_debug_menu', 'cancel']


@pytest.mark.parametrize('fault', [None, 'resource_ambiguous', 'post_rejected'])
def test_enabled_codejock_item_uses_bound_resource_command_once_without_invoke_retry(monkeypatch, fault):
    events = []
    class Command:
        def window_text(self): return 'Start/Stop Simulation'
        def invoke(self): events.append('unexpected_invoke')
    class UI:
        def _main_window(self, _session): return 'bound-window'
        def _open_native_debug_menu(self, _session, _pattern): events.append('open')
        def _find_menu_item(self, window, pattern, enabled):
            assert window == 'bound-window' and enabled is True
            return Command()
        def _cancel_native_menu(self, _session): events.append('cancel')
    automation = NativeGXWorks2SimulationAutomation(UI())
    def resolve(_session, caption):
        assert caption == 'Start/Stop Simulation'
        events.append('resolve_current_resource')
        if fault == 'resource_ambiguous': raise RuntimeError('resource identity ambiguous')
        return 'api', dict(id=1677, text=caption, resource='bound-loaded-satellite', resource_menus=[128, 136])
    def post(api, session, command):
        assert api == 'api' and command['id'] == 1677
        events.append('post_once')
        if fault == 'post_rejected': raise RuntimeError('queue rejected')
        return dict(invoked=False, dispatched=True, confirmation='pending')
    monkeypatch.setattr(automation, '_resource_command', resolve)
    monkeypatch.setattr(automation, '_post_command', post)
    if fault:
        with pytest.raises(RuntimeError): automation._invoke_popup_command(_Session(), 'no frame HMENU')
    else:
        result = automation._invoke_popup_command(_Session(), 'no frame HMENU')
        assert result['method'] == 'visible_menu_resource_post'
        assert result['dispatched'] is True and result['invoked'] is False
    assert events == ['open', 'resolve_current_resource', 'cancel'] + ([] if fault == 'resource_ambiguous' else ['post_once'])


def test_native_menu_accelerators_are_removed_before_command_lookup():
    class API:
        def GetMenuStringW(self, _menu, _position, buffer, _size, _flags):
            buffer.value = '&Start/Stop Simulation'
    assert NativeGXWorks2SimulationAutomation._menu_text(API(), 10, 0) == 'Start/Stop Simulation'


@given(st.lists(st.sampled_from(['hidden', 'other_class', 'other_process', 'other_thread', 'other_frame']),
                max_size=24), st.integers(min_value=0, max_value=24), st.booleans())
def test_native_popup_scope_ignores_foreign_windows_in_any_enumeration_order(noise, position, list_result):
    import sys
    from types import SimpleNamespace
    from unittest.mock import patch
    from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation

    frame, target = 100, 200
    windows = {handle: kind for handle, kind in enumerate(noise, 300)}
    order = list(windows)
    order.insert(position % (len(order) + 1), target)
    windows[target] = 'target'
    gui = SimpleNamespace(
        EnumWindows=lambda visit, value: [visit(handle, value) for handle in order],
        IsWindowVisible=lambda handle: windows[handle] != 'hidden',
        GetClassName=lambda handle: 'Edit' if windows[handle] == 'other_class' else 'XTPPopupBar',
        GetAncestor=lambda handle, flag: 999 if windows[handle] == 'other_frame' else frame)
    def thread_process(handle):
        value = (
        (7, 9) if handle == frame else
        (7, 10) if windows[handle] == 'other_process' else
        (8, 9) if windows[handle] == 'other_thread' else (7, 9))
        return list(value) if list_result else value
    process = SimpleNamespace(GetWindowThreadProcessId=thread_process)
    with patch.dict(sys.modules, win32gui=gui, win32process=process):
        assert PywinautoGXWorks2UIAutomation._native_popup_menu_handles(
            SimpleNamespace(handle=frame)) == [target]


@pytest.mark.parametrize('popups,enabled,duplicates,outcome', [
    ([], True, False, 'absent'), ([20, 21], True, False, 'ambiguous'),
    ([20], False, False, 'absent'), ([20], True, True, 'ambiguous'),
    ([20], True, False, 'found'),
])
def test_menu_query_reads_only_unique_owned_popup_direct_items(monkeypatch, popups, enabled, duplicates, outcome):
    import sys
    from types import SimpleNamespace
    from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation

    calls = []
    command = SimpleNamespace(window_text=lambda: 'Start/Stop Simulation', is_enabled=lambda: enabled)
    def children(**criteria):
        assert criteria == {'control_type': 'MenuItem'}
        calls.append('direct_items')
        return [command, command] if duplicates else [command]
    popup = SimpleNamespace(children=children,
        descendants=lambda **_: pytest.fail('Popup subtree traversal is unnecessary'))
    def window(*, handle):
        assert handle == 20  # The engineering frame (10) must never reach UIA.
        calls.append('popup_root')
        return popup
    monkeypatch.setitem(sys.modules, 'pywinauto', SimpleNamespace(
        Desktop=lambda **_: SimpleNamespace(window=window)))
    ui = PywinautoGXWorks2UIAutomation()
    monkeypatch.setattr(ui, '_native_popup_menu_handles', lambda _: popups)
    if outcome == 'ambiguous':
        with pytest.raises(RuntimeError, match='不唯一'):
            ui._find_menu_item(SimpleNamespace(handle=10),
                NativeGXWorks2SimulationAutomation.START_STOP_SIMULATION, enabled=True)
    else:
        result = ui._find_menu_item(SimpleNamespace(handle=10),
            NativeGXWorks2SimulationAutomation.START_STOP_SIMULATION, enabled=True)
        assert result is (command if outcome == 'found' else None)
    assert calls == (['popup_root', 'direct_items'] if len(popups) == 1 else [])


@pytest.mark.parametrize('state', ['closed', 'already_open', 'missing_bar', 'multiple_bars',
                                  'disabled_debug', 'duplicate_debug'])
def test_open_debug_menu_never_sends_an_accelerator_to_the_editor(monkeypatch, state):
    import sys
    from types import SimpleNamespace
    from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation

    events = []
    ui = PywinautoGXWorks2UIAutomation()
    frame = SimpleNamespace(handle=10,
        type_keys=lambda *_args, **_kwargs: pytest.fail('An accelerator can reach the source editor'))
    monkeypatch.setattr(ui, '_foreground_native_session', lambda _: events.append('foreground_confirmed'))
    monkeypatch.setattr(ui, '_main_window', lambda _: frame)
    monkeypatch.setattr(ui, '_native_popup_menu_handles', lambda _: [20] if state == 'already_open' else [])
    monkeypatch.setattr(ui, '_native_window_title', lambda handle: 'Debug' if handle == 20 else 'MenuBar')
    bars = [] if state == 'missing_bar' else [30, 31] if state == 'multiple_bars' else [30]
    monkeypatch.setattr(ui, '_native_descendants', lambda _: bars)
    monkeypatch.setitem(sys.modules, 'win32gui', SimpleNamespace(
        IsWindowVisible=lambda _: True, GetClassName=lambda _: 'XTPToolBar'))
    item = SimpleNamespace(window_text=lambda: 'Debug',
        is_enabled=lambda: state != 'disabled_debug', invoke=lambda: events.append('invoke_debug'))
    def children(**criteria):
        assert criteria == {'control_type': 'MenuItem'}
        events.append('direct_menu_items')
        return [item, item] if state == 'duplicate_debug' else [item]
    def window(*, handle):
        assert handle == 30
        return SimpleNamespace(children=children,
            descendants=lambda **_: pytest.fail('Engineering providers must not be traversed'))
    monkeypatch.setitem(sys.modules, 'pywinauto', SimpleNamespace(
        Desktop=lambda **_: SimpleNamespace(window=window)))
    if state in ('missing_bar', 'multiple_bars', 'disabled_debug', 'duplicate_debug'):
        with pytest.raises(RuntimeError):
            ui._open_native_debug_menu(_Session(), NativeGXWorks2SimulationAutomation.DEBUG_MENU)
        assert 'invoke_debug' not in events
    else:
        ui._open_native_debug_menu(_Session(), NativeGXWorks2SimulationAutomation.DEBUG_MENU)
        assert events == (['foreground_confirmed'] if state == 'already_open' else
                          ['foreground_confirmed', 'direct_menu_items', 'invoke_debug'])


@pytest.mark.parametrize('fault', [None, 'post_rejected', 'process_id', 'window_handle',
    'process_started', 'executable', 'software_version', 'project_name'])
def test_simulation_toggle_posts_once_without_waiting_for_native_handler(monkeypatch, fault):
    from types import SimpleNamespace
    session = SimpleNamespace(process_id=1, window_handle=2, process_started='start',
        executable='GD2.exe', software_version='1.635.0.1', project_name='isolated.gxw')
    current = SimpleNamespace(**vars(session))
    if fault not in (None, 'post_rejected'):
        setattr(current, fault, 'changed')
    monkeypatch.setattr('gxworks2.finder.GXWorks2Finder.find_running', lambda *_, **__: current)
    posted = []
    def post(*args):
        posted.append(args)
        return fault != 'post_rejected'
    api = SimpleNamespace(PostMessageW=post,
        SendMessageTimeoutW=lambda *_: pytest.fail('Toggle waited for the native modal handler'))
    if fault:
        with pytest.raises(RuntimeError):
            NativeGXWorks2SimulationAutomation._post_command(api, session,
                dict(id=1677, text='Start/Stop Simulation'))
    else:
        receipt = NativeGXWorks2SimulationAutomation._post_command(api, session,
            dict(id=1677, text='Start/Stop Simulation'))
        assert receipt == dict(invoked=False, dispatched=True, confirmation='pending',
            command='Start/Stop Simulation', command_id=1677)
    assert posted == ([(2, 0x0111, 1677, 0)] if fault in (None, 'post_rejected') else [])


@pytest.mark.parametrize('startup', ['ready', 'transfer', 'failure', 'timeout'])
def test_queued_start_observes_dialog_and_cpu_immediately_without_replaying(monkeypatch, startup):
    events, clock = [], [0.0]
    monkeypatch.setattr('gxworks2.simulation.time.monotonic', lambda: clock[0])
    def sleep(seconds):
        events.append('wait')
        clock[0] += seconds
    monkeypatch.setattr('gxworks2.simulation.time.sleep', sleep)
    runtime = _Runtime()
    service, automation = _service(runtime)
    def post(_session):
        automation.invocations += 1
        events.append('post')
        return dict(invoked=False, dispatched=True, confirmation='pending')
    def dialogs(_session):
        events.append('dialog')
        if automation.invocations and startup == 'failure':
            return dict(pending=False, failure='native transfer failed')
        return dict(pending=bool(automation.invocations and startup == 'transfer' and clock[0] == 0), failure='')
    def probe():
        events.append('cpu')
        return dict(ready=bool(automation.invocations and startup != 'timeout'), connected=False)
    automation.start_stop_simulation = post
    automation.handle_startup_dialogs = dialogs
    runtime.probe_simulator = probe
    result = service.prepare(timeout=1)
    after_post = events[events.index('post') + 1:]
    assert after_post[0] == 'dialog'
    if startup != 'transfer' and startup != 'failure':
        assert after_post[1] == 'cpu'  # No fixed first-probe delay.
    if startup == 'transfer':
        assert after_post[:4] == ['dialog', 'dialog', 'wait', 'dialog']
    assert result.success is (startup in ('ready', 'transfer'))
    assert automation.invocations == 1
    assert result.stage == {'ready': 'ready', 'transfer': 'ready',
        'failure': 'start_simulator', 'timeout': 'start_timeout'}[startup]


@pytest.mark.parametrize('operation', ['prepare', 'stop'])
def test_unconfirmed_toggle_is_observed_without_a_second_dispatch(operation):
    from gxworks2.simulation import SimulationCommandUnconfirmed
    runtime = _Runtime(ready=operation == 'stop')
    service, automation = _service(runtime)
    def pending(_session):
        automation.invocations += 1
        runtime.ready = not runtime.ready
        raise SimulationCommandUnconfirmed({'id': 1677, 'text': 'Start/Stop Simulation'}, 1460)
    automation.start_stop_simulation = pending
    result = service.prepare(timeout=1) if operation == 'prepare' else service.stop_if_running(timeout=1)
    assert result.success and automation.invocations == 1
    assert result.details['command']['confirmation'] == 'pending'
    assert result.details['command']['invoked'] is False


@pytest.mark.parametrize('transfer', ['completed', 'unseen', 'failed_during_probe'])
def test_ready_cpu_cannot_bypass_transfer_completion_or_a_dialog_appearing_during_open(monkeypatch, transfer):
    events, clock = [], [0.0]
    monkeypatch.setattr('gxworks2.simulation.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('gxworks2.simulation.time.sleep', lambda seconds: clock.__setitem__(0, clock[0]+seconds))
    runtime = _Runtime()
    service, automation = _service(runtime)
    automation.requires_transfer_completion = transfer != 'failed_during_probe'
    def dialogs(_):
        if not automation.invocations:
            return {}
        if transfer == 'failed_during_probe' and 'read' in events:
            return {'failure': 'PLC transfer failed'}
        return {'pending': clock[0] == 0, 'transfer_complete': transfer == 'completed' and clock[0] > 0}
    def probe():
        if automation.invocations:
            events.append('read')
        return {'ready': bool(automation.invocations), 'connected': False}
    runtime.probe_simulator = probe
    automation.handle_startup_dialogs = dialogs
    automation.suspend_monitoring = lambda _: events.append('monitor_suspended')
    result = service.prepare(timeout=1)
    assert result.success is (transfer == 'completed')
    assert automation.invocations == 1
    assert ('monitor_suspended' in events) is (transfer == 'completed')
    if transfer == 'completed':
        assert result.details['transfer_complete'] is True and clock[0] > 0
    elif transfer == 'failed_during_probe':
        assert result.stage == 'start_simulator' and result.message == 'PLC transfer failed'
    else:
        assert result.stage == 'start_timeout'


def test_preparation_does_not_repeat_the_same_project_inspection_before_start():
    runtime = _Runtime()
    service, _ = _service(runtime)
    inspect = service.project_automation.inspect_project
    calls = []
    service.project_automation.inspect_project = lambda session: calls.append(session) or inspect(session)
    assert service.prepare(timeout=1).success
    assert len(calls) == 1


def test_connected_stopped_cpu_is_not_toggled_during_preparation():
    runtime = _Runtime()
    runtime.probe_simulator = lambda: {'ready': False, 'connected': True, 'cpu_run': False}
    service, automation = _service(runtime)
    result = service.prepare(timeout=1)
    assert not result.success and result.stage == 'cpu_not_running'
    assert automation.invocations == 0


def test_preparation_finishes_transfer_and_suspends_ide_monitor_before_returning_ready():
    events = []
    runtime = _Runtime(ready=True)
    service, automation = _service(runtime)
    states = iter([True, False])
    def dialogs(_session):
        pending = next(states)
        events.append('transfer_pending' if pending else 'transfer_closed')
        return dict(pending=pending, failure='')
    automation.handle_startup_dialogs = dialogs
    automation.suspend_monitoring = lambda _session: events.append('monitor_suspended') or {'status': 'native_command_received'}
    result = service.prepare(timeout=1)
    assert result.success and automation.invocations == 0
    assert events == ['transfer_pending', 'transfer_closed', 'monitor_suspended']
    assert result.details['native_monitoring']['status'] == 'native_command_received'


@pytest.mark.parametrize('code', ['<ES:010a4061>', 'ES:0x010a4061'])
def test_monitor_communication_error_is_recorded_and_never_silently_acknowledged(code):
    class UI:
        def _native_dialog_handles(self, _session): return [42]
        def _native_descendants(self, _handle): return [43]
        def _native_window_title(self, handle): return 'GX Works2' if handle == 42 else code
    result = NativeGXWorks2SimulationAutomation(UI()).acknowledge_monitor_reset_notice(_Session())
    assert result['handled'] == []
    assert result['errors'] == [{'handle': 42, 'code': 'ES:010a4061', 'message': 'GX Works2\n' + code}]

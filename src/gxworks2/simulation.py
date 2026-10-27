# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""High-level GX Works2 simulation preparation.

The public service exposes intent-level operations only.  The implementation
selects the named GX Works2 Debug menu command through the native menu model;
it never stores screen coordinates or exposes click/key primitives.
"""

from __future__ import annotations

import ctypes
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Optional

from .finder import GXWorks2Finder
from .ui_automation import (
    PywinautoGXWorks2UIAutomation,
    UnavailableGXWorks2UIAutomation,
)


class SimulationCommandUnconfirmed(RuntimeError):
    """A dispatched toggle must be reconciled through observations, never replayed."""
    def __init__(self, command, native_error):
        super().__init__('仿真命令等待原生窗口处理，需要核对实际状态。')
        self.receipt = dict(invoked=False, dispatched=True, confirmation='pending',
            command=command['text'], command_id=command['id'], native_error=native_error)


class GXWorks2SimulationAutomation(ABC):
    @abstractmethod
    def start_stop_simulation(self, session):
        """Invoke GX Works2 Debug -> Start/Stop Simulation by command name."""

    @abstractmethod
    def handle_startup_dialogs(self, session):
        """Accept only recognized simulation confirmations; report failures."""


class UnavailableGXWorks2SimulationAutomation(GXWorks2SimulationAutomation):
    reason = "当前环境没有可用的 GX Works2 仿真自动化驱动。"

    def start_stop_simulation(self, session):
        raise RuntimeError(self.reason)

    def handle_startup_dialogs(self, session):
        return {"handled": False, "failure": self.reason}


class NativeGXWorks2SimulationAutomation(GXWorks2SimulationAutomation):
    requires_transfer_completion = True
    """Invoke a native MFC menu command selected by its localized label."""

    DEBUG_MENU = re.compile(r"^(?:调试|Debug)(?:\s*\([^)]*\))?$", re.I)
    START_STOP_SIMULATION = re.compile(
        r"(?:模拟\s*开始\s*/\s*停止|开始\s*/\s*停止模拟|开始或停止模拟|Start\s*/\s*Stop\s+Simulation)",
        re.I,
    )
    SIMULATION_DIALOG = re.compile(r"模拟|仿真|simulation|GX\s*Simulator", re.I)
    PLC_WRITE_DIALOG = re.compile(r"PLC\s*写入|Write\s+to\s+PLC", re.I)
    PLC_WRITE_COMPLETE = re.compile(
        r"(?:PLC\s*写入\s*[:：]\s*(?:结束|完成)|"
        r"程序\s*\([^)]*\)\s*写入\s*[:：]\s*完成|"
        r"(?:PLC|program).*?(?:write|transfer).*?(?:complete|finished))",
        re.I | re.S,
    )
    CLOSE_BUTTON = re.compile(
        r"^(?:关闭|Close|完成|Finish)(?:\s*\(&?.*?\))?$",
        re.I,
    )
    DIALOG_BUTTON = re.compile(
        r"^(?:关闭|取消|Close|Cancel)(?:\s*\(&?.*?\))?$",
        re.I,
    )
    FAILURE_TEXT = re.compile(
        r"错误|失败|无法|不能|异常|不正确|error|failed|cannot",
        re.I,
    )

    def __init__(self, ui_automation=None):
        self.ui_automation = ui_automation or PywinautoGXWorks2UIAutomation()

    @staticmethod
    def available():
        return os.name == "nt" and PywinautoGXWorks2UIAutomation.available()

    @staticmethod
    def _menu_api():
        if os.name != "nt":
            raise RuntimeError("GX Works2 仿真菜单仅支持 Windows。")
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetMenu.argtypes = [wintypes.HWND]
        user32.GetMenu.restype = wintypes.HMENU
        user32.GetSubMenu.argtypes = [wintypes.HMENU, ctypes.c_int]
        user32.GetSubMenu.restype = wintypes.HMENU
        user32.GetMenuItemCount.argtypes = [wintypes.HMENU]
        user32.GetMenuItemCount.restype = ctypes.c_int
        user32.GetMenuItemID.argtypes = [wintypes.HMENU, ctypes.c_int]
        user32.GetMenuItemID.restype = ctypes.c_uint
        user32.GetMenuState.argtypes = [wintypes.HMENU, ctypes.c_uint, ctypes.c_uint]
        user32.GetMenuState.restype = ctypes.c_uint
        user32.GetMenuStringW.argtypes = [
            wintypes.HMENU,
            ctypes.c_uint,
            wintypes.LPWSTR,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.GetMenuStringW.restype = ctypes.c_int
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            ctypes.c_uint,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND,
            ctypes.c_uint,
            wintypes.WPARAM,
            wintypes.LPARAM,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        user32.SendMessageTimeoutW.restype = wintypes.LPARAM
        return user32

    @classmethod
    def _menu_text(cls, user32, menu, position):
        buffer = ctypes.create_unicode_buffer(512)
        user32.GetMenuStringW(menu, position, buffer, len(buffer), 0x0400)
        return buffer.value.replace('&&', '\0').replace('&', '').replace('\0', '&').strip()

    @classmethod
    def _find_command(cls, hwnd):
        user32 = cls._menu_api()
        root = user32.GetMenu(int(hwnd))
        if not root:
            raise RuntimeError("GX Works2 主窗口没有可读取的菜单。")
        return user32, cls._command_in_menu(user32, root)

    @classmethod
    def _command_in_menu(cls, user32, root):
        debug_menu = None
        for position in range(max(0, user32.GetMenuItemCount(root))):
            text = cls._menu_text(user32, root, position)
            if cls.DEBUG_MENU.search(text):
                debug_menu = user32.GetSubMenu(root, position)
                break
        if not debug_menu:
            raise RuntimeError("GX Works2 菜单中没有找到“调试”。")

        def search(menu, depth=0):
            if depth > 4:
                return None
            for position in range(max(0, user32.GetMenuItemCount(menu))):
                text = cls._menu_text(user32, menu, position)
                submenu = user32.GetSubMenu(menu, position)
                if cls.START_STOP_SIMULATION.search(text):
                    command_id = int(user32.GetMenuItemID(menu, position))
                    state = int(user32.GetMenuState(menu, position, 0x0400))
                    return {
                        "id": command_id,
                        "text": text,
                        "enabled": not bool(state & (0x0001 | 0x0002)),
                    }
                if submenu:
                    found = search(submenu, depth + 1)
                    if found:
                        return found
            return None

        command = search(debug_menu)
        if not command or command["id"] in {-1, 0xFFFFFFFF}:
            raise RuntimeError("GX Works2“调试”菜单中没有找到“开始/停止模拟”。")
        return command

    @classmethod
    def _resource_command(cls, session, visible_caption, *, path=None):
        """Read the command identity from this IDE's loaded menu resources.

        The caller separately observes that the live Codejock item is enabled.
        Resource state alone never grants dispatch permission.
        """
        if not getattr(session, 'executable', None):
            return None
        import win32api
        import win32gui
        from .native_locale import native_resource_path
        resource_path = native_resource_path(session.process_id, session.executable, 'GD2')
        module = win32api.LoadLibraryEx(str(resource_path), 0, 0x2 | 0x20)  # resource-only, no DLL code
        user32 = cls._menu_api()
        matches = []
        def caption(text):
            return text.replace('&', '').split('\t', 1)[0].strip()
        try:
            for name in win32api.EnumResourceNames(module, 4):  # RT_MENU
                menu = win32gui.LoadMenu(module, name)
                try:
                    try:
                        if path is None:
                            command = cls._command_in_menu(user32, menu)
                        else:
                            current = menu
                            for index, part in enumerate(path):
                                positions = [i for i in range(user32.GetMenuItemCount(current))
                                             if caption(cls._menu_text(user32, current, i)) == part]
                                if len(positions) != 1:
                                    raise RuntimeError('Named resource menu path is absent or ambiguous')
                                position, = positions
                                if index < len(path) - 1:
                                    current = user32.GetSubMenu(current, position)
                                    if not current:
                                        raise RuntimeError('Named resource submenu is missing')
                                else:
                                    command = dict(id=int(user32.GetMenuItemID(current, position)),
                                                   text=cls._menu_text(user32, current, position))
                    except RuntimeError:
                        continue
                    if caption(command['text']) == caption(visible_caption):
                        matches.append((name, command))
                finally:
                    win32gui.DestroyMenu(menu)
        finally:
            win32api.FreeLibrary(module)
        if not matches or len({command['id'] for _, command in matches}) != 1:
            raise RuntimeError('当前原生资源与已启用仿真菜单的命令身份不唯一。')
        return user32, {**matches[0][1], 'resource': str(resource_path), 'resource_menus': [name for name, _ in matches]}

    @staticmethod
    def _send_command(user32, session, command):
        # Idempotent monitoring commands need acknowledgement before a CPU
        # reset. Start/Stop must use _post_command and observe its outcome.
        message_result = ctypes.c_size_t()
        ctypes.windll.kernel32.SetLastError(0)
        if not user32.SendMessageTimeoutW(int(session.window_handle), 0x0111, int(command['id']), 0,
                                         0x0002, 5000, ctypes.byref(message_result)):
            error = ctypes.windll.kernel32.GetLastError()
            if error in (0, 1460):  # A modal transfer can keep the handler open.
                raise SimulationCommandUnconfirmed(command, error)
            raise RuntimeError('GX Works2 仿真命令派发失败：' + str(error))

    @staticmethod
    def _post_command(user32, session, command):
        """Queue one toggle, leaving this thread free to observe modal UI."""
        current = GXWorks2Finder().find_running(process_id=session.process_id)
        keys = ('process_id', 'window_handle', 'process_started', 'executable',
                'software_version', 'project_name')
        if (current is None or any(not getattr(session, key, None)
                or getattr(current, key) != getattr(session, key) for key in keys)):
            raise RuntimeError('仿真命令的 GX Works2 工程身份已经改变。')
        if not user32.PostMessageW(int(session.window_handle), 0x0111, int(command['id']), 0):
            raise RuntimeError('GX Works2 仿真命令未能进入目标窗口消息队列。')
        # Queueing is not an execution acknowledgement. The preparation loop
        # must prove the transfer outcome and selected CPU state separately.
        return dict(invoked=False, dispatched=True, confirmation='pending',
                    command=command['text'], command_id=command['id'])

    def _invoke_popup_command(self, session, native_error):
        """Use the visible MFC popup when the frame has no Win32 HMENU.

        Some GX Works2 builds draw the menu themselves, so ``GetMenu`` returns
        null even though UI Automation exposes the localized menu items. Open
        Debug on the bound frame, verify its direct item, then post the exact
        resource command once. No free access key reaches a source editor.
        """

        window = self.ui_automation._main_window(session)
        self.ui_automation._open_native_debug_menu(session, self.DEBUG_MENU)
        try:
            deadline = time.monotonic() + min(getattr(self.ui_automation, 'timeout', 1.0), 1.0)
            while True:
                command = self.ui_automation._find_menu_item(
                    window, self.START_STOP_SIMULATION, enabled=True)
                if command is not None:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"{native_error}；可见调试菜单中没有已启用的“模拟开始/停止”。")
                time.sleep(0.02)
            text = str(command.window_text() or "").strip()
            resolved = self._resource_command(session, text)
            if resolved is None:
                raise RuntimeError('仿真命令缺少已绑定的原生资源身份。')
        finally:
            self.ui_automation._cancel_native_menu(session)
        user32, native = resolved
        return {**self._post_command(user32, session, native),
                'method': 'visible_menu_resource_post', 'resource': native['resource'],
                'resource_menus': native['resource_menus']}

    def _click_dialog_button(self, hwnd, pattern):
        if os.name != "nt":
            return False
        user32 = ctypes.windll.user32
        for child in self.ui_automation._native_descendants(hwnd):
            class_buffer = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(child, class_buffer, len(class_buffer))
            if class_buffer.value.casefold() != "button":
                continue
            text = self.ui_automation._native_window_title(child)
            if pattern.search(text) and user32.IsWindowEnabled(child):
                import win32gui
                win32gui.PostMessage(hwnd, 0x0111, win32gui.GetDlgCtrlID(child), child)
                return True
        return False

    def suspend_monitoring(self, session):
        """Stop this IDE's monitoring before an external controlled CPU reset.

        This idempotent IDE command changes no PLC data or RUN state. Its
        identity is resolved from the actually loaded localized resource.
        """
        from .native_locale import inspect_native_language
        from gxw.native_locale import ui_text
        language = inspect_native_language(session.process_id, session.executable)
        path = tuple(ui_text(key, language) for key in
                     ('monitor_online_menu', 'monitor_submenu', 'monitor_stop_all'))
        user32, command = self._resource_command(session, path[-1], path=path)
        self.ui_automation._cancel_native_menu(session)
        self._send_command(user32, session, command)
        return dict(status='native_command_received', command=command['text'], command_id=command['id'],
                    resource=command['resource'], scope='all_windows_in_bound_IDE', plc_state_changed=False)

    def acknowledge_monitor_reset_notice(self, session):
        """Compatibility name: report monitor errors; never dismiss them.

        ES:010a4061 is a communication error. Closing it is not evidence that
        the monitoring session recovered. Preparation must stop native
        monitoring before the gateway resets a CPU.
        """
        errors = []
        for handle in self.ui_automation._native_dialog_handles(session):
            message = '\n'.join(self.ui_automation._native_window_title(child)
                                for child in (handle, *self.ui_automation._native_descendants(handle)))
            if not re.search(r'ES:(?:0x)?010a4061', message, re.I):
                continue
            errors.append({'handle': handle, 'code': 'ES:010a4061', 'message': message})
        return {'handled': [], 'errors': errors, 'other_errors_dismissed': False}

    def _plc_write_window_handles(self, session):
        """Find GX Works2's custom PLC-write panel, which is not ``#32770``."""

        if os.name != "nt":
            return []
        user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        candidates = []
        seen = set()

        def has_dialog_button(hwnd):
            for child in self.ui_automation._native_descendants(hwnd):
                class_buffer = ctypes.create_unicode_buffer(128)
                user32.GetClassNameW(child, class_buffer, len(class_buffer))
                if class_buffer.value.casefold() != "button":
                    continue
                if self.DIALOG_BUTTON.search(
                    self.ui_automation._native_window_title(child)
                ):
                    return True
            return False

        def inspect(hwnd, _lparam):
            process_id = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            if int(process_id.value) != int(session.process_id):
                return True
            title = self.ui_automation._native_window_title(hwnd)
            if not title or not self.PLC_WRITE_DIALOG.search(title):
                return True
            candidate = int(hwnd)
            while candidate and candidate != int(session.window_handle):
                if has_dialog_button(candidate):
                    if candidate not in seen:
                        seen.add(candidate)
                        candidates.append(candidate)
                    break
                candidate = int(user32.GetParent(candidate) or 0)
            return True

        user32.EnumWindows(callback_type(inspect), 0)
        user32.EnumChildWindows(
            int(session.window_handle),
            callback_type(inspect),
            0,
        )
        return candidates

    def start_stop_simulation(self, session):
        inspect = getattr(self.ui_automation, "inspect_simulation_project", self.ui_automation.inspect_project)
        state = inspect(session)
        if not state.get("project_open"):
            raise RuntimeError("请先在 GX Works2 中新建或打开工程。")
        if not state.get("program_ready"):
            raise RuntimeError("请先打开 GX Works2 当前工程中指定的程序编辑器。")
        try:
            user32, command = self._find_command(session.window_handle)
        except RuntimeError as native_error:
            return self._invoke_popup_command(session, native_error)
        if not command["enabled"]:
            raise RuntimeError("GX Works2 的“开始/停止模拟”当前不可用。")
        return {**self._post_command(user32, session, command),
                'method': 'native_menu_post'}

    def handle_startup_dialogs(self, session):
        handled = False
        write_pending = False
        transfer_complete = False
        handles = list(self.ui_automation._native_dialog_handles(session))
        handles.extend(self._plc_write_window_handles(session))
        for handle in dict.fromkeys(handles):
            text = self.ui_automation._native_dialog_text(handle)
            if not text:
                continue
            if self.PLC_WRITE_DIALOG.search(text):
                write_pending = True
                if self.FAILURE_TEXT.search(text):
                    return {"handled": handled, "pending": False, "failure": text}
                # GX Works2 renders the detailed completion log in a custom
                # control whose text is not exposed through Win32.  The action
                # button itself changes from ``取消`` to ``关闭`` only after
                # 100% completion, so it is the authoritative completion cue.
                if self._click_dialog_button(handle, self.CLOSE_BUTTON):
                    handled = True
                    transfer_complete = True
                continue
            if not self.SIMULATION_DIALOG.search(text):
                continue
            if self.FAILURE_TEXT.search(text):
                return {"handled": handled, "pending": False, "failure": text}
            if self.ui_automation._native_confirm_dialog(handle):
                handled = True
        return {"handled": handled, "pending": write_pending, "failure": "",
                "transfer_complete": transfer_complete}


@dataclass(frozen=True)
class SimulatorPreparationResult:
    success: bool
    stage: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


class GXSimulator2PreparationService:
    """Prepare or stop GX Simulator2 using observed state and named commands."""

    def __init__(
        self,
        *,
        finder=None,
        project_automation=None,
        simulation_automation=None,
        runtime=None,
    ):
        self.finder = finder or GXWorks2Finder()
        if project_automation is None:
            project_automation = (
                PywinautoGXWorks2UIAutomation()
                if PywinautoGXWorks2UIAutomation.available()
                else UnavailableGXWorks2UIAutomation()
            )
        self.project_automation = project_automation
        if simulation_automation is None:
            simulation_automation = (
                NativeGXWorks2SimulationAutomation(project_automation)
                if NativeGXWorks2SimulationAutomation.available()
                else UnavailableGXWorks2SimulationAutomation()
            )
        self.simulation_automation = simulation_automation
        if runtime is None:
            from simulator.runtime import get_simulator_gateway_runtime

            runtime = get_simulator_gateway_runtime()
        self.runtime = runtime

    @staticmethod
    def _emit(progress: Optional[Callable[[str, str], None]], stage, message):
        if progress:
            progress(stage, message)

    def _session(self):
        session = self.finder.find_running()
        if session is None:
            raise RuntimeError("未检测到正在运行的 GX Works2。")
        inspect = getattr(self.project_automation, "inspect_simulation_project", self.project_automation.inspect_project)
        state = inspect(session)
        if not state.get("automation_available", True):
            raise RuntimeError(state.get("message") or "无法检查 GX Works2 工程。")
        if not state.get("project_open"):
            raise RuntimeError("请先在 GX Works2 中新建或打开工程。")
        if not state.get("program_ready"):
            raise RuntimeError("请先打开 GX Works2 当前工程中指定的程序编辑器。")
        return session, state

    @staticmethod
    def _missing_component_result(state, environment):
        if environment.get("mx_component_installed"):
            message = (
                "已检测到 MX Component，但没有与当前仿真网关位数匹配的 "
                "ActProgType，无法从 PLC AI 读写 GX Simulator2。请安装或修复 "
                "32 位 MX Component ActProgType 后重启本程序。"
            )
        else:
            message = (
                "GX Simulator2 已安装，但本机没有安装或注册 MX Component "
                "ActProgType。GX Works2 可以单独运行仿真；PLC AI 的自动测试还需要 "
                "32 位 MX Component 作为官方外部读写接口。安装后请重启本程序。"
            )
        return SimulatorPreparationResult(
            False,
            "mx_component",
            message,
            {
                "environment": environment,
                "project": state,
                "required_component": "MX Component ActProgType",
                "required_architecture": "x86",
            },
        )

    def preflight(self, *, progress=None) -> SimulatorPreparationResult:
        """Validate all non-mutating prerequisites before import or startup."""

        try:
            self._emit(progress, "check_project", "正在检查 GX Works2 工程…")
            _session, state = self._session()
            return self._preflight_environment(state, progress=progress)
        except Exception as error:
            return SimulatorPreparationResult(False, "preflight", str(error), {})

    def _preflight_environment(self, state, *, progress=None):
        try:
            from simulator.gateway import detect_simulator_environment

            environment = detect_simulator_environment()
            if not environment.get("simulator_installed"):
                return SimulatorPreparationResult(
                    False,
                    "simulator_installation",
                    "未找到 GX Simulator2。请修复 GX Works2 的仿真组件安装后重试。",
                    {"environment": environment, "project": state},
                )
            if not environment.get("mx_component_installed"):
                return self._missing_component_result(state, environment)

            self._emit(progress, "gateway", "正在检查本地 Simulator2 网关…")
            health = self.runtime.ensure_gateway()
            if not health.get("mx_component_available"):
                result = self._missing_component_result(state, environment)
                details = dict(result.details)
                details["health"] = health
                return SimulatorPreparationResult(
                    result.success, result.stage, result.message, details
                )
            return SimulatorPreparationResult(
                True,
                "preflight",
                "GX Simulator2 自动测试环境检查通过。",
                {"health": health, "environment": environment, "project": state},
            )
        except Exception as error:
            return SimulatorPreparationResult(False, "preflight", str(error), {})

    def _suspend_monitoring(self, session):
        suspend = getattr(self.simulation_automation, 'suspend_monitoring', None)
        result = suspend(session) if callable(suspend) else None
        inspect = getattr(self.simulation_automation, 'acknowledge_monitor_reset_notice', None)
        errors = inspect(session).get('errors', []) if callable(inspect) else []
        if errors:
            raise RuntimeError('原生监视存在通信错误，尚未恢复：' + '\n'.join(error['message'] for error in errors))
        return result

    def prepare(self, *, progress=None, timeout=45.0) -> SimulatorPreparationResult:
        try:
            self._emit(progress, "check_project", "正在检查 GX Works2 工程…")
            session, state = self._session()
            checked = self._preflight_environment(state, progress=progress)
            if not checked.success:
                return checked
            health = (checked.details or {}).get("health") or self.runtime.ensure_gateway()
            startup_deadline = time.monotonic() + max(1.0, float(timeout))
            while True:
                dialog = self.simulation_automation.handle_startup_dialogs(session)
                if dialog.get('failure'):
                    return SimulatorPreparationResult(False, 'start_simulator', dialog['failure'], {'project': state})
                if not dialog.get('pending'):
                    break
                if time.monotonic() >= startup_deadline:
                    return SimulatorPreparationResult(False, 'startup_dialog_timeout',
                        '仿真传送窗口尚未完成关闭，未复位 CPU。', {'project': state})
                time.sleep(.1)
            probe = self.runtime.probe_simulator()
            if probe.get("ready"):
                monitoring = self._suspend_monitoring(session)
                return SimulatorPreparationResult(
                    True,
                    "ready",
                    "GX Simulator2 已就绪。",
                    {"already_running": True, "probe": probe, "project": state, 'native_monitoring': monitoring},
                )
            if probe.get('connected'):
                # Start/Stop is a toggle. A stopped/faulted CPU still owns a
                # live simulator; toggling here would close it and conceal the
                # actual fault. Controlled runners can explicitly reset it.
                monitoring = self._suspend_monitoring(session)
                return SimulatorPreparationResult(False, 'cpu_not_running',
                    '已连接 GX Simulator2，但 CPU 未运行；未切换仿真开关。',
                    {'already_running': True, 'probe': probe, 'project': state, 'native_monitoring': monitoring})

            self._emit(progress, "start_simulator", "正在启动 GX Simulator2…")
            try:
                invoked = self.simulation_automation.start_stop_simulation(session)
            except SimulationCommandUnconfirmed as command:
                invoked = command.receipt
            deadline = time.monotonic() + max(1.0, float(timeout))
            last_probe = probe
            from contextlib import nullcontext
            startup_probe = getattr(self.runtime, 'startup_probe', None)
            observation = (startup_probe() if callable(startup_probe)
                           else nullcontext(self.runtime.probe_simulator))
            transfer_complete = False
            requires_transfer = getattr(self.simulation_automation, 'requires_transfer_completion', False)
            with observation as observe_cpu:
                while time.monotonic() < deadline:
                    dialog = self.simulation_automation.handle_startup_dialogs(session)
                    if dialog.get("failure"):
                        return SimulatorPreparationResult(False, "start_simulator", dialog["failure"],
                            {"command": invoked, "probe": last_probe, "project": state})
                    transfer_complete |= bool(dialog.get('transfer_complete'))
                    # Open MX while GX transfers, but do not read a CPU that
                    # is being rewritten: MX reads can block the STA gateway
                    # until download completes. Keep the attested connection.
                    read_run = (not dialog.get('pending')
                                and (transfer_complete or not requires_transfer))
                    if read_run:
                        last_probe = observe_cpu()
                    elif callable(startup_probe) and not last_probe.get('connected'):
                        last_probe = observe_cpu(read_run=False)
                    # A transfer can appear during a blocking vendor Open.
                    # RUN alone must never accept the previous program.
                    dialog = self.simulation_automation.handle_startup_dialogs(session)
                    if dialog.get("failure"):
                        return SimulatorPreparationResult(False, "start_simulator", dialog["failure"],
                            {"command": invoked, "probe": last_probe, "project": state})
                    transfer_complete |= bool(dialog.get('transfer_complete'))
                    if (not dialog.get('pending') and last_probe.get("ready")
                            and (transfer_complete or not requires_transfer)):
                        monitoring = self._suspend_monitoring(session)
                        return SimulatorPreparationResult(
                            True,
                            "ready",
                            "GX Simulator2 已启动并完成专用路由校验。",
                            {
                                "already_running": False,
                                "command": invoked,
                                "probe": last_probe,
                                "project": state,
                                'transfer_complete': transfer_complete,
                                'native_monitoring': monitoring,
                            },
                        )
                    time.sleep(0.05)
            return SimulatorPreparationResult(
                False,
                "start_timeout",
                "GX Simulator2 尚未就绪：" + str(last_probe.get('error') or '启动超时'),
                {"command": invoked, "probe": last_probe, "project": state},
            )
        except Exception as error:
            return SimulatorPreparationResult(False, "prepare", str(error), {})

    def stop_if_running(self, *, progress=None, timeout=20.0) -> SimulatorPreparationResult:
        try:
            health = self.runtime.health()
            if health is None:
                # A fresh application process has not started the local gateway
                # yet. That says nothing about GX Simulator2: it may already be
                # running from an earlier test or a manual GX Works2 action.
                health = self.runtime.ensure_gateway()
            probe = self.runtime.probe_simulator()
            if not probe.get("ready") and not probe.get("connected"):
                from simulator.gateway import detect_simulator_environment

                environment = detect_simulator_environment()
                active_processes = [
                    name
                    for name in environment.get("simulator_processes", []) or []
                    if "simmanager" not in str(name).casefold()
                ]
                if active_processes:
                    return SimulatorPreparationResult(
                        False,
                        "stop_state_unverified",
                        "检测到 GX Simulator2 运行进程，但无法验证仿真会话已停止。",
                        {
                            "health": health,
                            "probe": probe,
                            "environment": environment,
                        },
                    )
                return SimulatorPreparationResult(
                    True,
                    "stopped",
                    "未检测到可连接的 GX Simulator2 会话。",
                    {"health": health, "probe": probe, "environment": environment},
                )
            self._emit(progress, "stop_simulator", "正在停止 GX Simulator2…")
            session, state = self._session()
            monitoring = self._suspend_monitoring(session)
            self.runtime.disconnect()
            try:
                invoked = self.simulation_automation.start_stop_simulation(session)
            except SimulationCommandUnconfirmed as command:
                invoked = command.receipt
            deadline = time.monotonic() + max(1.0, float(timeout))
            stopped_samples = 0
            last_probe = probe
            while time.monotonic() < deadline:
                last_probe = self.runtime.probe_simulator()
                if not last_probe.get("ready") and not last_probe.get("connected"):
                    stopped_samples += 1
                else:
                    stopped_samples = 0
                # One failed route probe can occur while Simulator2 is changing
                # state. Repeated observations prevent a transient MX Component
                # disconnect from being mistaken for a completed stop.
                if stopped_samples >= 3:
                    return SimulatorPreparationResult(
                        True,
                        "stopped",
                        "GX Simulator2 已停止。",
                        {
                            "command": invoked,
                            "project": state,
                            "probe": last_probe,
                            "verification_samples": stopped_samples,
                            'native_monitoring': monitoring,
                        },
                    )
                time.sleep(0.1)
            return SimulatorPreparationResult(
                False,
                "stop_timeout",
                "GX Simulator2 未在限定时间内停止。",
                {"command": invoked, "project": state, "probe": last_probe},
            )
        except Exception as error:
            return SimulatorPreparationResult(False, "stop", str(error), {})


__all__ = [
    "GXSimulator2PreparationService",
    "GXWorks2SimulationAutomation",
    "NativeGXWorks2SimulationAutomation",
    "SimulatorPreparationResult",
    "UnavailableGXWorks2SimulationAutomation",
]

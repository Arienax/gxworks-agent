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
        return buffer.value.strip()

    @classmethod
    def _find_command(cls, hwnd):
        user32 = cls._menu_api()
        root = user32.GetMenu(int(hwnd))
        if not root:
            raise RuntimeError("GX Works2 主窗口没有可读取的菜单。")
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
        return user32, command

    def _invoke_popup_command(self, session, native_error):
        """Use the visible MFC popup when the frame has no Win32 HMENU.

        Some GX Works2 builds draw the menu themselves, so ``GetMenu`` returns
        null even though UI Automation exposes the localized menu items.  Open
        Debug with its stable accelerator, verify the target by label, then
        send the accelerator declared by that exact menu item.
        """

        window = self.ui_automation._main_window(session)
        window.set_focus()
        window.type_keys("%b")
        time.sleep(0.12)
        command = self.ui_automation._find_menu_item(
            window,
            self.START_STOP_SIMULATION,
            enabled=True,
        )
        if command is None:
            window.type_keys("{ESC}")
            raise RuntimeError(
                f"{native_error}；可见调试菜单中也没有找到“模拟开始/停止”。"
            )
        text = str(command.window_text() or "").strip()

        # Invoke the exact menu item exposed by UI Automation.  Sending only
        # its access-key letter is timing/IME dependent and can leave the
        # popup open, which looks as if the user still has to click the item.
        # The Invoke pattern asks GX Works2 to execute the selected command
        # itself and is deterministic across Chinese input methods.
        try:
            command.invoke()
            return {
                "invoked": True,
                "command": text,
                "method": "visible_menu_invoke",
            }
        except Exception as invoke_error:
            accelerator = re.search(r"\(&?([A-Z])\)\s*$", text, re.I)
            if accelerator is None:
                window.type_keys("{ESC}")
                raise RuntimeError(
                    "GX Works2 模拟命令无法直接执行，且没有可识别的访问键。"
                ) from invoke_error
            try:
                from pywinauto.keyboard import send_keys

                send_keys(
                    accelerator.group(1).lower(),
                    pause=0,
                    vk_packet=False,
                )
            except Exception:
                window.type_keys("{ESC}")
                raise
            return {
                "invoked": True,
                "command": text,
                "method": "visible_menu_accelerator_fallback",
            }

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
            if pattern.search(text):
                user32.SendMessageW(child, 0x00F5, 0, 0)  # BM_CLICK
                return True
        return False

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
        state = self.ui_automation.inspect_project(session)
        if not state.get("project_open"):
            raise RuntimeError("请先在 GX Works2 中新建或打开工程。")
        if not state.get("program_ready"):
            raise RuntimeError("请先打开 GX Works2 的 MAIN 程序编辑器。")
        try:
            user32, command = self._find_command(session.window_handle)
        except RuntimeError as native_error:
            return self._invoke_popup_command(session, native_error)
        if not command["enabled"]:
            raise RuntimeError("GX Works2 的“开始/停止模拟”当前不可用。")
        message_result = ctypes.c_size_t()
        # WM_COMMAND is sent synchronously with a bounded timeout so a true
        # return means GX Works2's UI thread actually received the command;
        # PostMessage only proved that it entered the Windows message queue.
        if not user32.SendMessageTimeoutW(
            int(session.window_handle),
            0x0111,
            int(command["id"]),
            0,
            0x0002,  # SMTO_ABORTIFHUNG
            5000,
            ctypes.byref(message_result),
        ):
            raise RuntimeError("GX Works2 未接受仿真菜单命令。")
        return {
            "invoked": True,
            "command": command["text"],
            "method": "native_menu_send",
        }

    def handle_startup_dialogs(self, session):
        handled = False
        write_pending = False
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
                continue
            if not self.SIMULATION_DIALOG.search(text):
                continue
            if self.FAILURE_TEXT.search(text):
                return {"handled": handled, "pending": False, "failure": text}
            if self.ui_automation._native_confirm_dialog(handle):
                handled = True
        return {"handled": handled, "pending": write_pending, "failure": ""}


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
        state = self.project_automation.inspect_project(session)
        if not state.get("automation_available", True):
            raise RuntimeError(state.get("message") or "无法检查 GX Works2 工程。")
        if not state.get("project_open"):
            raise RuntimeError("请先在 GX Works2 中新建或打开工程。")
        if not state.get("program_ready"):
            raise RuntimeError("请先打开 GX Works2 的 MAIN 程序编辑器。")
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

    def prepare(self, *, progress=None, timeout=45.0) -> SimulatorPreparationResult:
        try:
            checked = self.preflight(progress=progress)
            if not checked.success:
                return checked
            session, state = self._session()
            health = (checked.details or {}).get("health") or self.runtime.ensure_gateway()
            probe = self.runtime.probe_simulator()
            if probe.get("ready"):
                return SimulatorPreparationResult(
                    True,
                    "ready",
                    "GX Simulator2 已就绪。",
                    {"already_running": True, "probe": probe, "project": state},
                )

            self._emit(progress, "start_simulator", "正在启动 GX Simulator2…")
            invoked = self.simulation_automation.start_stop_simulation(session)
            deadline = time.monotonic() + max(1.0, float(timeout))
            first_probe_at = time.monotonic() + 0.5
            last_probe = probe
            while time.monotonic() < deadline:
                dialog = self.simulation_automation.handle_startup_dialogs(session)
                if dialog.get("failure"):
                    return SimulatorPreparationResult(
                        False,
                        "start_simulator",
                        dialog["failure"],
                        {"command": invoked, "probe": last_probe, "project": state},
                    )
                if dialog.get("pending") or time.monotonic() < first_probe_at:
                    time.sleep(0.1)
                    continue
                last_probe = self.runtime.probe_simulator()
                if last_probe.get("ready"):
                    return SimulatorPreparationResult(
                        True,
                        "ready",
                        "GX Simulator2 已启动并完成专用路由校验。",
                        {
                            "already_running": False,
                            "command": invoked,
                            "probe": last_probe,
                            "project": state,
                        },
                    )
                time.sleep(0.25)
            return SimulatorPreparationResult(
                False,
                "start_timeout",
                "GX Simulator2 启动超时，请查看 GX Works2 中是否有未处理的编译错误。",
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
            self.runtime.disconnect()
            invoked = self.simulation_automation.start_stop_simulation(session)
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

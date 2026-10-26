from abc import ABC, abstractmethod
import ctypes
import os
from pathlib import Path
import re
import time

from .diagnostics import (
    GXAutomationError,
    classify_automation_error,
    describe_exception,
    exception_details,
)
from .models import GXSyncErrorCode
from .finder import GXWorks2Finder
from .project_profiles import GXWORKS2_NATIVE_VERSION, native_project_profile


class NativeEditorNotFound(RuntimeError):
    """An exact source path is absent, rather than a focus/activation failure."""


class GXWorks2UIAutomation(ABC):
    """Internal automation boundary; callers never receive click primitives."""

    @abstractmethod
    def inspect_project(self, session):
        """Return current project/program state without changing GX Works2."""

    def inspect_simulation_project(self, session):
        """Inspect a program for simulation without granting CSV capabilities."""
        return self.inspect_project(session)

    @abstractmethod
    def export_current_program(self, session, destination):
        """Export the program for an explicit read/export request."""

    @abstractmethod
    def import_program_csv(self, session, csv_path):
        """Run Edit -> Read from CSV File and return observed result state."""

    @abstractmethod
    def export_current_comments(self, session, destination):
        """Export device comments for an explicit read/export request."""

    @abstractmethod
    def import_comments_csv(self, session, csv_path):
        """Import global device comments and return observed result state."""

    def save_project(self, session):
        """Persist the open GX project when the driver supports safe saving."""

        return {
            "success": False,
            "save_required": True,
            "message": "请在GX Works2中保存当前工程。",
        }

    def create_simple_project(self, session, path, *, cpu, progress=None):
        raise RuntimeError("当前驱动没有经过验证的空工程创建接口。")

    def prepare_export_retry(self, session):
        """Safely reset transient read/export UI before a retry."""

        return {"dismissed_dialogs": [], "main_activated": False}


class UnavailableGXWorks2UIAutomation(GXWorks2UIAutomation):
    """Safe default until a supported Windows UIA driver is injected."""

    reason = (
        "未配置GX Works2 UI Automation驱动。为避免盲目按键或坐标点击，"
        "导入操作已停止。"
    )

    def inspect_project(self, session):
        return {
            "project_open": bool(session.project_open),
            "project_name": session.project_name,
            "program_ready": False,
            "automation_available": False,
            "message": self.reason,
        }

    def export_current_program(self, session, destination):
        raise RuntimeError(self.reason)

    def import_program_csv(self, session, csv_path):
        raise RuntimeError(self.reason)

    def export_current_comments(self, session, destination):
        raise RuntimeError(self.reason)

    def import_comments_csv(self, session, csv_path):
        raise RuntimeError(self.reason)

    def save_project(self, session):
        return {
            "success": False,
            "save_required": True,
            "message": self.reason,
        }


class PywinautoGXWorks2UIAutomation(GXWorks2UIAutomation):
    """CSV and project operations use native Win32 windows, not UIA searches."""

    EDIT_MENU = re.compile(r"^编辑(?:\([&]?E\))?$")
    READ_CSV_ITEM = re.compile(r"从\s*CSV\s*文件(?:中)?(?:读取|导入)|Read.*CSV", re.I)
    WRITE_CSV_ITEM = re.compile(r"(?:写入|导出|保存).*CSV\s*文件|Write.*CSV", re.I)
    SUCCESS_TEXT = re.compile(r"完成|成功|读取.*结束|导入.*结束", re.I)
    FAILURE_TEXT = re.compile(r"错误|失败|无法|不能|异常|不正确|error|failed", re.I)
    CONFIRMATION_TEXT = re.compile(
        r"(?:读取|写入).*?(?:指定的)?文件.*?确定|执行读取后.*?无法撤消|"
        r"read|write|confirm",
        re.I | re.S,
    )
    PROGRAM_EDITOR = re.compile(r"\[PRG\].*\bMAIN\b", re.I)
    PROGRAM_WRITE_EDITOR = re.compile(
        r"\[PRG\]\s*(?:写入|Write(?:\s+Mode)?)\s+MAIN\b",
        re.I,
    )
    COMMENT_EDITOR = re.compile(r"软元件注释\s*COMMENT", re.I)
    LOCAL_COMMENT_EDITOR = re.compile(r"软元件注释\s*MAIN\b", re.I)
    PROGRAM_READ_KEY = "j"
    PROGRAM_WRITE_KEY = "k"
    COMMENT_READ_KEY = "f"
    COMMENT_WRITE_KEY = "o"
    # GD2_RC_936 menu/accelerator resources, GX Works2 1.635.0.1.
    NEW_PROJECT_COMMAND = 0x3FF
    SAVE_PROJECT_COMMAND = 0x402
    READ_CSV_COMMAND = 0x755
    WRITE_CSV_COMMAND = 0x756
    WRITE_MODE_COMMAND = 0x200
    USER_AUTH_SAVE_NOTICE = (
        "用户认证模式设置为了启用，但未启用操作履历的参数设置。\n"
        "启用PLC参数-[PLC RAS设置]-[操作履历]。"
    )

    def __init__(self, timeout=12.0, *, program="MAIN"):
        self.timeout = float(timeout)
        if not isinstance(program, str) or not re.fullmatch(r"[^\x00-\x1f\x7f]{1,64}", program):
            raise ValueError("程序名必须是明确的原生程序标识。")
        self.read_program_name = program
        self.PROGRAM_EDITOR = re.compile(r"\[PRG\].*(?<!\w)" + re.escape(program) + r"(?!\w)", re.I)
        self.PROGRAM_WRITE_EDITOR = re.compile(r"\[PRG\]\s*(?:写入|Write(?:\s+Mode)?)\s+" + re.escape(program) + r"(?!\w)", re.I)
        self.LOCAL_COMMENT_EDITOR = re.compile(r"软元件注释\s*" + re.escape(program) + r"(?!\w)", re.I)
        self._progress_reporter = None
        self._application_binding = None

    def set_progress_reporter(self, reporter):
        self._progress_reporter = reporter

    def _report_progress(self, stage, message):
        if self._progress_reporter is not None:
            self._progress_reporter(stage, message)

    @staticmethod
    def available():
        try:
            import pywinauto  # noqa: F401
            return True
        except ImportError:
            return False

    def _application(self, session, backend="win32"):
        from pywinauto import Application

        identity = (session.process_id, session.window_handle, backend)
        if self._application_binding and self._application_binding[0] == identity:
            return self._application_binding[1]
        app = Application(backend=backend).connect(
            process=session.process_id,
            timeout=self.timeout,
        )
        self._application_binding = (identity, app)
        return app

    def _main_window(self, session):
        app = self._application(session)
        return app.window(handle=session.window_handle)

    def _foreground_native_session(self, session):
        """Activate the bound frame using native input queues, without a mouse."""
        import win32gui
        import win32process
        from .finder import GXWorks2Finder
        current = GXWorks2Finder().find_running(process_id=session.process_id)
        keys = ('process_id', 'window_handle', 'process_started', 'executable', 'software_version')
        if current is None or any(getattr(current, key) != getattr(session, key) for key in keys):
            raise RuntimeError('仿真菜单的 GX Works2 进程身份已经改变。')
        target = int(session.window_handle)
        if win32gui.GetForegroundWindow() == target:
            return
        thread = ctypes.windll.kernel32.GetCurrentThreadId()
        foreground = win32gui.GetForegroundWindow()
        others = {win32process.GetWindowThreadProcessId(target)[0]}
        if foreground:
            others.add(win32process.GetWindowThreadProcessId(foreground)[0])
        joined = []
        try:
            for other in others - {thread}:
                if ctypes.windll.user32.AttachThreadInput(thread, other, True):
                    joined.append(other)
            if win32gui.IsIconic(target):
                win32gui.ShowWindow(target, 9)
            win32gui.BringWindowToTop(target)
            # BringWindowToTop can already activate the frame. A redundant
            # SetForegroundWindow can report access denied even then; the
            # actual foreground HWND, checked below, is the acknowledgement.
            if win32gui.GetForegroundWindow() != target:
                ctypes.windll.user32.SetForegroundWindow(target)
            if win32gui.GetForegroundWindow() != target:
                try:
                    self._main_window(session).set_focus()
                except Exception:
                    pass
        finally:
            for other in reversed(joined):
                ctypes.windll.user32.AttachThreadInput(thread, other, False)
        if win32gui.GetForegroundWindow() != target:
            raise RuntimeError('尚未确认已绑定的 GX Works2 仿真菜单窗口位于前台。')

    def _open_native_debug_menu(self, session, pattern):
        self._foreground_native_session(session)
        window = self._main_window(session)
        popups = self._native_popup_menu_handles(window)
        if len(popups) == 1 and pattern.search(self._native_window_title(popups[0])):
            return
        import win32gui
        from pywinauto import Desktop

        # Codejock's MenuBar is a native child HWND. Query only its direct
        # menu items; no accelerator can leak into an editor or an IME.
        bars = [handle for handle in self._native_descendants(session.window_handle)
                if win32gui.IsWindowVisible(handle)
                and win32gui.GetClassName(handle) == 'XTPToolBar'
                and self._native_window_title(handle) == 'MenuBar']
        if len(bars) != 1:
            raise RuntimeError('当前工程的原生菜单栏不唯一或尚未显示。')
        items = Desktop(backend='uia').window(handle=bars[0]).children(control_type='MenuItem')
        matches = [item for item in items if pattern.search(item.window_text().strip())]
        if len(matches) != 1 or not matches[0].is_enabled():
            raise RuntimeError('当前原生菜单栏中没有唯一、已启用的调试菜单。')
        matches[0].invoke()

    def _cancel_native_menu(self, session):
        import win32gui
        win32gui.PostMessage(session.window_handle, 0x001F, 0, 0)  # WM_CANCELMODE

    @staticmethod
    def _native_popup_menu_handles(window):
        """Locate only visible Codejock popups owned by this exact frame.

        Enumerating native top-level HWNDs does not enter editor accessibility
        providers. The same native popup class is used by engineering menus.
        """
        import win32gui
        import win32process

        frame = int(window.handle)
        thread, process = win32process.GetWindowThreadProcessId(frame)
        handles = []

        def visit(handle, _):
            if (win32gui.IsWindowVisible(handle)
                    and win32gui.GetClassName(handle) == 'XTPPopupBar'
                    and tuple(win32process.GetWindowThreadProcessId(handle)) == (thread, process)
                    and win32gui.GetAncestor(handle, 3) == frame):  # GA_ROOTOWNER
                handles.append(handle)

        win32gui.EnumWindows(visit, None)
        return handles

    def _find_menu_item(self, window, pattern, enabled=None):
        # Query the popup's direct items, never the engineering frame's tree.
        # Editor providers can otherwise block every simulation language.
        handles = self._native_popup_menu_handles(window)
        if not handles:
            return None
        if len(handles) != 1:
            raise RuntimeError('当前工程的原生弹出菜单不唯一。')
        from pywinauto import Desktop

        popup = Desktop(backend="uia").window(handle=handles[0])
        matches = [control for control in popup.children(control_type="MenuItem")
                   if pattern.search(control.window_text().strip())]
        if len(matches) > 1:
            raise RuntimeError('当前原生弹出菜单中的目标命令不唯一。')
        if not matches:
            return None
        command, = matches
        if enabled is not None and bool(command.is_enabled()) != bool(enabled):
            return None
        return command

    def _open_edit_menu(self, window):
        # The native Alt+E accelerator opens the Codejock popup, which has no
        # HMENU. This does not query an accessibility provider.
        window.set_focus()
        window.type_keys("%e")
        # The legacy MFC popup is visibly open before its keyboard focus is
        # ready.  Sending the second access key at 40 ms is occasionally lost,
        # which forced the expensive full accessibility-tree fallback.
        time.sleep(0.12)

    def _send_edit_accelerator(self, window, accelerator):
        """Dispatch the observed command to the bound frame, without SendInput."""
        commands = {self.PROGRAM_READ_KEY: self.READ_CSV_COMMAND,
                    self.COMMENT_READ_KEY: self.READ_CSV_COMMAND,
                    self.PROGRAM_WRITE_KEY: self.WRITE_CSV_COMMAND,
                    self.COMMENT_WRITE_KEY: self.WRITE_CSV_COMMAND}
        self._post_window_command(window, commands[accelerator])

    def _post_window_command(self, window, command):
        import win32api
        import win32gui
        import win32process

        handle = int(window.handle)
        binding = self._application_binding
        if not binding:
            raise RuntimeError("GX Works2原生命令没有绑定工程会话。")
        process_id, bound_handle, backend = binding[0]
        if (backend != "win32" or bound_handle != handle or not win32gui.IsWindow(handle)
                or win32process.GetWindowThreadProcessId(handle)[1] != process_id):
            raise RuntimeError("GX Works2命令的目标窗口身份已经改变。")
        process = win32api.OpenProcess(0x0410, False, process_id)
        try:
            executable = win32process.GetModuleFileNameEx(process, 0)
            version = win32api.GetFileVersionInfo(executable, "\\")
        finally:
            process.Close()
        ms, ls = version["FileVersionMS"], version["FileVersionLS"]
        if (Path(executable).name.casefold() != "gd2.exe"
                or (ms >> 16, ms & 65535, ls >> 16, ls & 65535) != GXWORKS2_NATIVE_VERSION):
            raise RuntimeError("此GX Works2版本尚未验证原生命令编号。")
        win32gui.PostMessage(handle, 0x0111, int(command), 0)

    def _post_native_command(self, session, command):
        self._post_window_command(self._main_window(session), command)

    @staticmethod
    def _send_tree_enter(tree):
        # SysTreeView32 forwards VK_RETURN through its native notification
        # route. The operation targets this tree, not the foreground desktop.
        tree.send_message(0x0100, 0x0D, 0)  # WM_KEYDOWN / VK_RETURN
        tree.send_message(0x0101, 0x0D, 0)  # WM_KEYUP / VK_RETURN

    @staticmethod
    def _send_popup_access_key(accelerator):
        from pywinauto.keyboard import send_keys

        send_keys(str(accelerator).lower(), pause=0, vk_packet=False)

    def _csv_command(self, window, pattern):
        # GX's Codejock popup is not an HMENU. Retry its observed native
        # access key; never fall back to a UIA accessibility-tree traversal.
        is_comment = bool(self.COMMENT_EDITOR.search(window.window_text()) or self.LOCAL_COMMENT_EDITOR.search(window.window_text()))
        read = pattern is self.READ_CSV_ITEM
        key = (self.COMMENT_READ_KEY if read else self.COMMENT_WRITE_KEY) if is_comment else (
            self.PROGRAM_READ_KEY if read else self.PROGRAM_WRITE_KEY)

        class NativeCommand:
            def invoke(inner):
                self._send_edit_accelerator(window, key)

        return NativeCommand()

    def inspect_project(self, session):
        if session.project_state_known and not session.project_open:
            return {
                "automation_available": True,
                "project_open": False,
                "program_ready": False,
                "project_name": "",
                "read_csv_available": False,
            }
        try:
            window = self._main_window(session)
            titles = self._child_window_titles(session)
            program_ready = any(self.PROGRAM_EDITOR.search(title) for title in titles)
            if not program_ready:
                program_ready = bool(self.PROGRAM_EDITOR.search(window.window_text()))
            program_writable = any(
                self.PROGRAM_WRITE_EDITOR.search(title) for title in titles
            )
            if not program_writable:
                program_writable = bool(
                    self.PROGRAM_WRITE_EDITOR.search(window.window_text())
                )
        except Exception as error:
            classified = classify_automation_error(
                error,
                default_code=GXSyncErrorCode.GX_PROJECT_INSPECT_FAILED,
                stage="inspect_project",
                retryable=True,
                message=f"无法读取GX Works2当前程序：{describe_exception(error)}",
            )
            return {
                "automation_available": False,
                "project_open": bool(session.project_open),
                "program_ready": False,
                "project_name": session.project_name,
                "read_csv_available": False,
                "message": str(classified),
                "error_code": classified.code,
                "stage": classified.stage,
                "retryable": classified.retryable,
                **exception_details(classified),
            }
        project_open = bool(session.project_open or program_ready)
        return {
            "automation_available": True,
            "project_open": project_open,
            "program_ready": program_ready,
            "project_name": session.project_name or window.window_text(),
            "read_csv_available": program_ready,
            "program_writable": program_writable,
            "program_name": self.read_program_name,
        }

    def inspect_simulation_project(self, session):
        state = self.inspect_project(session)
        if (not state.get("automation_available") or not state.get("project_open")
                or state.get("program_ready")):
            return state
        from .native_locale import inspect_native_language, native_tree_path
        from gxw.native_locale import program_title
        language = inspect_native_language(session.process_id, session.executable)
        titles = [title.strip() for title in self._child_window_titles(session)]
        implementations = [kind for kind in ("st", "fbd")
                           if program_title(self.read_program_name, implementation=kind,
                                            language=language) in titles]
        if len(implementations) != 1:
            return state
        trees = [tree for tree in self._main_window(session).descendants(class_name="SysTreeView32")
                 if tree.control_id() == 12051]
        if len(trees) != 1:
            return state
        path = native_tree_path(("程序部件", "程序", self.read_program_name, "程序本体"), language)
        try:
            source = trees[0].get_item(path, exact=True)
        except (IndexError, RuntimeError):
            return state
        profile = self._current_native_project_profile(session)
        if source is None or profile is None or not profile.program_supported:
            return state
        return {**state, "program_ready": True, "read_csv_available": False,
                "program_language": implementations[0], "cpu_menu": profile.cpu,
                "identity_basis": "native_program_tree_editor_and_status_bar"}

    def inspect_read_identity(self, session):
        """Confirm an actual program-tree source and native status-bar CPU."""
        self._require_native_program_source(session)
        profile = self._current_native_project_profile(session)
        if profile is None or not profile.program_supported:
            raise RuntimeError("未能从原生状态栏确认当前程序 CPU。")
        window = self._main_window(session)
        return {"cpu_menu": profile.cpu, "program_name": self.read_program_name,
                "project_name": GXWorks2Finder._project_name_from_title(window.window_text()),
                "identity_basis": "native_program_tree_and_status_bar",
                "program_source_confirmed": True}

    @staticmethod
    def _child_window_titles(session):
        if os.name != "nt":
            return []
        user32 = ctypes.windll.user32
        values = []
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )

        def visit(hwnd, _lparam):
            length = int(user32.GetWindowTextLengthW(hwnd))
            if length > 0:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title = buffer.value.strip()
                if title:
                    values.append(title)
            return True

        user32.EnumChildWindows(
            int(session.window_handle),
            callback_type(visit),
            0,
        )
        return values

    def _activate_editor(self, session, editor_pattern, tree_pattern):
        window = self._main_window(session)
        # GX Works2 puts the active MDI document name in the frame title.  The
        # title alone does not mean the document owns keyboard focus, though;
        # mode shortcuts such as F2 would otherwise be delivered to the frame.
        if self._activate_native_document(session, editor_pattern):
            return window

        if editor_pattern.search(window.window_text()):
            return window

        # The native navigation tree (control 12051 in the observed simple
        # project) exposes TVM_GETITEM/TVM_SELECTITEM. Use exact project paths
        # rather than scanning every UIA TabItem/TreeItem, which can block in
        # GX's disabled legacy providers for tens of seconds.
        if editor_pattern is self.COMMENT_EDITOR:
            paths = (("全局软元件注释",), ("Global Device Comment",))
        elif editor_pattern is self.LOCAL_COMMENT_EDITOR:
            paths = (("程序部件", "局部软元件注释", "MAIN"),)
        else:
            paths = (("程序部件", "程序", "MAIN"), ("Program", "MAIN"))
        target = None
        for tree in window.descendants(class_name="SysTreeView32"):
            if not tree.is_visible() or not tree.is_enabled():
                continue
            for path in paths:
                try:
                    item = tree.get_item(path, exact=True)
                except (IndexError, RuntimeError):
                    continue
                if item is not None:
                    target = (tree, item)
                    break
            if target is not None:
                break
        if target is None:
            raise NativeEditorNotFound("GX Works2原生导航中没有找到对应编辑器")
        tree, item = target
        self._select_native_tree_item(tree, item)
        self._send_tree_enter(tree)

        deadline = time.monotonic() + min(self.timeout, 4.0)
        while time.monotonic() < deadline:
            if editor_pattern.search(window.window_text()):
                return window
            time.sleep(0.05)
        raise RuntimeError("GX Works2未切换到对应编辑器")

    @staticmethod
    def _activate_native_document(session, editor_pattern):
        if os.name != "nt":
            return False
        user32 = ctypes.windll.user32
        matches = []
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )

        def visit(hwnd, _lparam):
            length = int(user32.GetWindowTextLengthW(hwnd))
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if editor_pattern.search(buffer.value.strip()):
                matches.append(int(hwnd))
                return False
            return True

        user32.EnumChildWindows(
            int(session.window_handle),
            callback_type(visit),
            0,
        )
        if not matches:
            return False

        wm_mdiactivate = 0x0222
        parent = int(user32.GetParent(matches[0]))
        if parent:
            user32.SendMessageW(parent, wm_mdiactivate, matches[0], 0)
        # Focus the MDI document on its GUI thread. Focusing a child through
        # pywinauto.set_focus repeatedly moves the pointer and waits even when
        # the GX frame is already foreground; it also targets the wrong window
        # for SetForegroundWindow. Keep foreground and keyboard focus separate.
        user32.SetForegroundWindow(int(session.window_handle))
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        target_thread = user32.GetWindowThreadProcessId(matches[0], None)
        attached = current_thread != target_thread
        if attached and not user32.AttachThreadInput(current_thread, target_thread, True):
            raise RuntimeError("无法连接GX Works2编辑器的输入线程。")
        try:
            user32.SetFocus(matches[0])
            focused = int(user32.GetFocus())
            # A newly activated MDI document can temporarily expose no focus
            # while its GUI thread finishes activation. Retry that state only;
            # another focused window remains a hard failure.
            deadline = time.monotonic() + .25
            while not focused and time.monotonic() < deadline:
                time.sleep(.02)
                user32.SetFocus(matches[0])
                focused = int(user32.GetFocus())
            if focused != matches[0] and not user32.IsChild(matches[0], focused):
                raise RuntimeError(f"未确认GX Works2编辑器取得键盘焦点（document={matches[0]}, focus={focused}）。")
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, target_thread, False)
        return True

    @staticmethod
    def _select_native_tree_item(tree, item):
        # pywinauto's item.select() first moves the mouse to focus the tree.
        # TVM_SELECTITEM works in the observed GX navigation without SendInput
        # or an interactive desktop; check its acknowledgement before Enter.
        if tree.send_message(0x110B, 9, item.elem) != 1:  # TVM_SELECTITEM / TVGN_CARET
            raise RuntimeError("GX Works2原生导航未确认节点选择。")

    @staticmethod
    def _select_native_combo(control, value):
        # GX's remote-I/O CPU handler restores the previous CPU when the
        # wrapper synthesizes SELENDOK before SELCHANGE and then CLOSEUP.
        # Send the native selection/change sequence and verify the result.
        choices = control.item_texts()
        if choices.count(value) != 1 or not control.is_enabled():
            raise RuntimeError("GX Works2原生选项不可选择或不明确。")
        index = choices.index(value)
        if control.send_message(0x014E, index, 0) != index:  # CB_SETCURSEL
            raise RuntimeError("GX Works2原生选项未确认节点选择。")
        control.notify_parent(1)  # CBN_SELCHANGE
        control.notify_parent(9)  # CBN_SELENDOK
        if control.selected_text() != value:
            raise RuntimeError("GX Works2原生选项发生变化。")

    @staticmethod
    def _set_native_check(control, checked):
        # BM_SETCHECK alone changes the painted box but does not run GX's
        # label handler. Its language list and project options need BN_CLICKED.
        if not control.is_enabled():
            raise RuntimeError('GX Works2原生复选项当前不可修改。')
        (control.check if checked else control.uncheck)()
        control.notify_parent(0)  # BN_CLICKED
        if bool(control.get_check_state()) != bool(checked):
            raise RuntimeError('GX Works2原生复选项未保留所选状态。')

    def _require_native_program_source(self, session):
        # A remote-I/O parameter project can retain an old MDI MAIN and CPU
        # status text from the preceding project. Its current navigation has
        # only parameters. Never import into that stale program document.
        for tree in self._main_window(session).descendants(class_name="SysTreeView32"):
            control_id = getattr(tree, "control_id", None)
            if not callable(control_id) or control_id() != 12051:
                continue
            for path in (("程序部件", "程序", self.read_program_name), ("Program", self.read_program_name)):
                try:
                    if tree.get_item(path, exact=True) is not None:
                        return
                except (IndexError, RuntimeError):
                    continue
            raise NativeEditorNotFound(f"当前GX Works2工程的原生导航没有{self.read_program_name}程序源。")
        raise NativeEditorNotFound("未找到当前工程的原生程序导航。")

    def _activate_program_editor(self, session):
        self._require_native_program_source(session)
        return self._activate_editor(
            session,
            self.PROGRAM_EDITOR,
            re.compile(r"^" + re.escape(self.read_program_name) + "$", re.I),
        )

    def _program_editor_titles(self, session):
        titles = list(self._child_window_titles(session))
        try:
            titles.append(self._main_window(session).window_text())
        except Exception:
            pass
        return [str(title or "").strip() for title in titles if str(title or "").strip()]

    @staticmethod
    def _send_write_mode_shortcut():
        from pywinauto.keyboard import send_keys

        # Mitsubishi documents F2 as the current-window Write Mode shortcut.
        send_keys("{F2}", pause=0)

    def _ensure_program_writable(self, session):
        """Focus MAIN and leave it in Write Mode before a physical import."""

        window = self._activate_program_editor(session)
        if any(
            self.PROGRAM_WRITE_EDITOR.search(title)
            for title in self._program_editor_titles(session)
        ):
            return window

        # Re-activate the native MDI child even when the frame title already
        # names MAIN.  Live GX Works2 testing showed that F2 is ignored unless
        # this document, rather than the MFC frame, owns keyboard focus.
        if not self._activate_native_document(session, self.PROGRAM_EDITOR):
            window.set_focus()
        self._post_native_command(session, self.WRITE_MODE_COMMAND)

        deadline = time.monotonic() + min(self.timeout, 2.0)
        while time.monotonic() < deadline:
            if any(
                self.PROGRAM_WRITE_EDITOR.search(title)
                for title in self._program_editor_titles(session)
            ):
                return window
            time.sleep(0.05)
        raise RuntimeError(
            "GX Works2 MAIN程序仍为读取/只读模式，无法执行CSV导入；"
            "请确认工程允许写入。"
        )

    def _activate_comments_editor(self, session):
        try:
            return self._activate_editor(session, self.COMMENT_EDITOR, re.compile(r"^全局软元件注释$"))
        except NativeEditorNotFound:
            # Basic Q CPUs expose a native MAIN local-comment table instead
            # of a Global Device Comment source. Do not create an unsupported
            # global source or select a same-named task/program tree leaf.
            profile = self._current_native_project_profile(session)
            if profile is None or profile.cpu not in {"Q00J", "Q00", "Q01"}:
                raise
            return self._activate_editor(session, self.LOCAL_COMMENT_EDITOR, re.compile(r"^MAIN$"))

    def _current_native_project_profile(self, session):
        # Original MFC status bar 59393 exposes the actual CPU, including
        # merged/concrete display spellings. It is independent of the caller's
        # requested model and of an old cached project in this GX process.
        profiles = set()
        for bar in self._main_window(session).descendants(class_name="msctls_statusbar32"):
            if bar.control_id() != 59393:
                continue
            profiles.update(profile for value in bar.texts() if (profile := native_project_profile(value)) is not None)
        return next(iter(profiles)) if len(profiles) == 1 else None

    def _current_native_project_codepage_observation(self, session, native_ui_language):
        """Read the bound frame's current project-language status without caching.

        The observation retains the CP936/GB18030 ambiguity in the native
        status resources. A caller needs separate live evidence to resolve it.
        """
        import win32gui
        import win32process
        from gxw.native_locale import LANGUAGES, project_codepage_status

        if native_ui_language not in LANGUAGES:
            raise RuntimeError('GX Works2原生界面语言未确认，不能读取当前工程语言。')
        frame = self._main_window(session)
        frame_handle = int(frame.handle)
        if frame_handle != int(session.window_handle):
            raise RuntimeError('GX Works2当前工程语言的窗口不属于已绑定会话。')
        bars = [bar for bar in frame.descendants(class_name='msctls_statusbar32')
                if bar.control_id() == 59393]
        if len(bars) != 1:
            raise RuntimeError('GX Works2当前工程语言的原生状态栏缺失或不唯一。')
        bar = bars[0]
        bar_handle = int(bar.handle)

        def bound_statusbar():
            return (win32gui.IsWindow(frame_handle) and win32gui.IsWindow(bar_handle)
                and win32process.GetWindowThreadProcessId(frame_handle)[1] == session.process_id
                and win32process.GetWindowThreadProcessId(bar_handle)[1] == session.process_id
                and win32gui.IsChild(frame_handle, bar_handle)
                and win32gui.IsWindowVisible(bar_handle)
                and win32gui.GetClassName(bar_handle).casefold() == 'msctls_statusbar32'
                and win32gui.GetDlgCtrlID(bar_handle) == 59393)

        if not bound_statusbar():
            raise RuntimeError('GX Works2当前工程语言的状态栏身份已改变。')
        texts = bar.texts()
        if not bound_statusbar():
            raise RuntimeError('GX Works2读取当前工程语言时状态栏身份已改变。')
        return project_codepage_status(texts, language=native_ui_language)

    def _wait_dialog(self, session, title_pattern, timeout=None):
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            for window in self._dialog_candidates(session):
                title = window.window_text().strip()
                class_name = str(
                    getattr(window.element_info, "class_name", "") or ""
                )
                if class_name == "#32770" and title_pattern.search(title):
                    return window
            time.sleep(0.1)
        raise RuntimeError("未检测到GX Works2文件选择对话框")

    def _dialog_candidates(self, session):
        """Return process-owned top-level and nested dialogs.

        GX Works2 embeds some modal dialogs below its MFC main window instead
        of exposing them through ``Application.windows()``.  Searching the
        nested Window controls is therefore required for the confirmation
        shown before CSV export/import.
        """
        native_handles = self._native_dialog_handles(session)
        if os.name == "nt":
            app = self._application(session)
            return [app.window(handle=handle) for handle in native_handles]

        app = self._application(session)
        result = []
        seen = set()
        for top in app.windows():
            candidates = [top]
            try:
                candidates.extend(top.descendants(control_type="Window"))
            except Exception:
                pass
            for candidate in candidates:
                try:
                    handle = int(candidate.handle)
                except Exception:
                    handle = 0
                key = handle or tuple(
                    getattr(candidate.element_info, "runtime_id", ()) or ()
                ) or id(candidate)
                if key in seen:
                    continue
                seen.add(key)
                result.append(candidate)
        return result

    @staticmethod
    def _native_dialog_handles(session):
        if os.name != "nt":
            return []
        user32 = ctypes.windll.user32
        handles = []
        seen = set()
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )

        def add_if_dialog(hwnd, _lparam):
            process_id = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            if int(process_id.value) != int(session.process_id) or not user32.IsWindowVisible(hwnd):
                return True
            class_buffer = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
            if (
                class_buffer.value == "#32770"
                and user32.GetWindowTextLengthW(hwnd) > 0
            ):
                value = int(hwnd)
                if value not in seen:
                    seen.add(value)
                    handles.append(value)
            return True

        # File and confirmation dialogs are sometimes owned top-level MFC
        # windows and sometimes nested children, depending on GX Works2 build.
        user32.EnumWindows(callback_type(add_if_dialog), 0)
        user32.EnumChildWindows(
            int(session.window_handle),
            callback_type(add_if_dialog),
            0,
        )
        return handles

    @staticmethod
    def _native_window_title(hwnd):
        if os.name != "nt" or not hwnd:
            return ""
        user32 = ctypes.windll.user32
        length = int(user32.GetWindowTextLengthW(int(hwnd)))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(int(hwnd), buffer, length + 1)
        return buffer.value.strip()

    @staticmethod
    def _native_descendants(hwnd):
        if os.name != "nt" or not hwnd:
            return []
        user32 = ctypes.windll.user32
        handles = []
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )

        def visit(child, _lparam):
            handles.append(int(child))
            return True

        user32.EnumChildWindows(int(hwnd), callback_type(visit), 0)
        return handles

    @classmethod
    def _native_dialog_text(cls, hwnd):
        if os.name != "nt":
            return ""
        user32 = ctypes.windll.user32
        values = [cls._native_window_title(hwnd)]
        for child in cls._native_descendants(hwnd):
            length = int(user32.GetWindowTextLengthW(child))
            if length <= 0 or length > 4096:
                continue
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(child, buffer, length + 1)
            value = buffer.value.strip()
            if value:
                values.append(value)
        return " ".join(dict.fromkeys(value for value in values if value))

    @classmethod
    def _native_confirm_dialog(cls, hwnd):
        if os.name != "nt":
            return False
        user32 = ctypes.windll.user32
        for child in cls._native_descendants(hwnd):
            class_buffer = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(child, class_buffer, len(class_buffer))
            if class_buffer.value.casefold() != "button":
                continue
            text = cls._native_window_title(child)
            if re.search(r"^(?:是|确定|OK|Yes)(?:\(&?.*?\))?$", text, re.I):
                user32.SendMessageW(child, 0x00F5, 0, 0)  # BM_CLICK
                return True
        return False

    def _legacy_dialog(self, session, title_pattern):
        from pywinauto import Desktop

        for handle in self._native_dialog_handles(session):
            title = self._native_window_title(handle)
            if title_pattern.search(title):
                return Desktop(backend="win32").window(handle=handle)
        return None

    def _wait_legacy_dialog(
        self,
        session,
        title_pattern,
        timeout=None,
        *,
        failure_stage="",
    ):
        wait_seconds = self.timeout if timeout is None else float(timeout)
        started_at = time.monotonic()
        deadline = started_at + wait_seconds
        while time.monotonic() < deadline:
            dialog = self._legacy_dialog(session, title_pattern)
            if dialog is not None:
                return dialog
            time.sleep(0.05)
        if failure_stage:
            elapsed = max(0.0, time.monotonic() - started_at)
            timeout_error = TimeoutError()
            raise GXAutomationError(
                GXSyncErrorCode.GX_FILE_DIALOG_TIMEOUT,
                failure_stage,
                f"{elapsed:.1f}秒内未检测到GX Works2文件选择窗口。",
                retryable=True,
                original_error=timeout_error,
                details={
                    "timeout_seconds": wait_seconds,
                    "elapsed_seconds": round(elapsed, 3),
                },
            )
        raise RuntimeError("未检测到GX Works2文件选择对话框")

    @staticmethod
    def _set_legacy_file_name(dialog, path):
        edits = [control for control in dialog.children(class_name="Edit") if control.is_enabled()]
        if not edits:
            edits = [control for control in dialog.descendants(class_name="Edit") if control.is_enabled()]
        if not edits:
            raise RuntimeError("文件选择对话框中没有文件名输入框")
        target = edits[0]
        target.set_edit_text(str(Path(path).resolve()))

        # GX Works2 uses a legacy MFC common dialog. Depending on Windows/GX
        # build, the default Save/Open button may be a direct child or exposed
        # deeper in the Win32 wrapper. Search both before falling back to the
        # native IDOK command; merely pressing Enter in the edit box is not
        # reliable and was leaving comment exports waiting for a manual Save.
        buttons = []
        seen = set()
        for getter in (dialog.children, dialog.descendants):
            try:
                candidates = getter(class_name="Button")
            except Exception:
                candidates = []
            for control in candidates:
                try:
                    enabled = control.is_enabled()
                except Exception:
                    enabled = True
                if not enabled:
                    continue
                handle = int(getattr(control, "handle", 0) or 0)
                key = handle or id(control)
                if key in seen:
                    continue
                seen.add(key)
                buttons.append(control)
        for button in buttons:
            raw_id = getattr(button, "control_id", 0)
            try:
                control_id = int(raw_id() if callable(raw_id) else raw_id or 0)
            except Exception:
                control_id = 0
            if control_id != 1:
                continue
            handle = int(getattr(button, "handle", 0) or 0)
            if os.name == "nt" and handle:
                ctypes.windll.user32.SendMessageW(handle, 0x00F5, 0, 0)  # BM_CLICK
            else:
                click = getattr(button, "click", None)
                if callable(click):
                    click()
                else:
                    button.click_input()
            return

        dialog_handle = int(getattr(dialog, "handle", 0) or 0)
        if os.name == "nt" and dialog_handle:
            ctypes.windll.user32.SendMessageW(dialog_handle, 0x0111, 1, 0)  # WM_COMMAND/IDOK
            return
        target.type_keys("{ENTER}")

    def _wait_confirmation_and_accept(self, session, context, timeout=None):
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            for handle in self._native_dialog_handles(session):
                text = self._native_dialog_text(handle)
                if not text:
                    continue
                # "执行读取后，将无法撤消" is part of GX Works2's normal
                # confirmation prompt, not an automation failure.
                if self.CONFIRMATION_TEXT.search(text):
                    if self._native_confirm_dialog(handle):
                        return handle
                if self.FAILURE_TEXT.search(text):
                    raise RuntimeError(text)
            time.sleep(0.05)
        raise RuntimeError(f"未检测到GX Works2{context}确认对话框")

    @staticmethod
    def _window_text(window):
        texts = [window.window_text()]
        try:
            texts.extend(
                control.window_text()
                for control in window.descendants()
                if control.window_text()
            )
        except Exception:
            pass
        return " ".join(dict.fromkeys(text.strip() for text in texts if text.strip()))

    @staticmethod
    def _set_file_name(dialog, path):
        edits = [
            control
            for control in dialog.descendants(control_type="Edit")
            if control.is_enabled()
        ]
        if not edits:
            raise RuntimeError("文件选择对话框中没有文件名输入框")
        target = next(
            (
                control
                for control in edits
                if re.search(
                    r"文件名|File name",
                    " ".join(
                        filter(
                            None,
                            (
                                control.window_text(),
                                getattr(control.element_info, "name", ""),
                                getattr(control.element_info, "automation_id", ""),
                            ),
                        )
                    ),
                    re.I,
                )
            ),
            edits[-1],
        )
        target.set_edit_text(str(Path(path).resolve()))
        buttons = [
            control
            for control in dialog.descendants(control_type="Button")
            if control.is_enabled()
        ]
        # The legacy common dialog also exposes combo-box drop-down arrows as
        # buttons named "打开".  Only the dialog's default command button has
        # automation id 1; accepting any other "打开" button leaves the dialog
        # waiting forever.
        for button in buttons:
            automation_id = str(
                getattr(button.element_info, "automation_id", "") or ""
            )
            if automation_id == "1" and re.search(
                r"打开|保存|确定|Open|Save|OK",
                button.window_text(),
                re.I,
            ):
                button.invoke()
                return
        target.type_keys("{ENTER}")

    def _invoke_csv_command(
        self,
        session,
        pattern,
        path,
        *,
        accelerator,
        confirm_before_file=False,
        operation="",
    ):
        export_code = (
            GXSyncErrorCode.GX_COMMENT_EXPORT_FAILED
            if operation == "comment_export"
            else GXSyncErrorCode.GX_PROGRAM_EXPORT_FAILED
        )
        file_dialog_stage = (
            "wait_comment_file_dialog"
            if operation == "comment_export"
            else "wait_program_file_dialog"
        )
        submit_stage = (
            "submit_comment_export_path"
            if operation == "comment_export"
            else "submit_program_export_path"
        )
        operation_label = "注释" if operation == "comment_export" else "程序"
        try:
            window = self._main_window(session)
            if operation:
                self._report_progress(
                    "open_export_menu",
                    f"正在打开GX Works2{operation_label}CSV导出命令",
                )
            self._send_edit_accelerator(window, accelerator)
        except GXAutomationError:
            raise
        except Exception as error:
            if not operation:
                raise
            raise classify_automation_error(
                error,
                default_code=GXSyncErrorCode.GX_EXPORT_MENU_FAILED,
                stage="open_export_menu",
                retryable=True,
                message=(
                    f"无法打开GX Works2{operation_label}CSV导出菜单："
                    f"{describe_exception(error)}"
                ),
                details={"operation": operation},
            ) from error
        if confirm_before_file:
            try:
                self._wait_confirmation_and_accept(session, "写入", timeout=1.5)
            except RuntimeError as initial_error:
                try:
                    window.send_message(0x001F, 0, 0)  # WM_CANCELMODE
                    command = self._csv_command(window, pattern)
                    if command is None:
                        raise GXAutomationError(
                            GXSyncErrorCode.GX_EXPORT_MENU_FAILED,
                            "open_export_menu",
                            "当前编辑器没有可用的“写入至CSV文件”命令。",
                            retryable=True,
                            original_error=initial_error,
                            details={"operation": operation},
                        )
                    command.invoke()
                    self._wait_confirmation_and_accept(
                        session, "写入", timeout=min(self.timeout, 4.0)
                    )
                except GXAutomationError:
                    raise
                except Exception as error:
                    raise classify_automation_error(
                        error,
                        default_code=GXSyncErrorCode.GX_EXPORT_MENU_FAILED,
                        stage="open_export_menu",
                        retryable=True,
                        message=(
                            f"GX Works2未打开{operation_label}CSV导出命令："
                            f"{describe_exception(error)}"
                        ),
                        details={"operation": operation},
                    ) from error
            if operation:
                self._report_progress(
                    file_dialog_stage,
                    f"正在等待GX Works2{operation_label}文件选择窗口",
                )
            dialog = self._wait_legacy_dialog(
                session,
                re.compile(r"打开|保存|CSV|Open|Save", re.I),
                timeout=min(self.timeout, 5.0),
                failure_stage=file_dialog_stage if operation else "",
            )
        else:
            try:
                dialog = self._wait_legacy_dialog(
                    session,
                    re.compile(r"打开|保存|CSV|Open|Save", re.I),
                    timeout=1.5,
                )
            except RuntimeError:
                window.send_message(0x001F, 0, 0)  # WM_CANCELMODE
                command = self._csv_command(window, pattern)
                if command is None:
                    raise RuntimeError("当前编辑器没有对应CSV命令")
                command.invoke()
                dialog = self._wait_legacy_dialog(
                    session,
                    re.compile(r"打开|保存|CSV|Open|Save", re.I),
                    timeout=min(self.timeout, 5.0),
                )
        if operation:
            self._report_progress(
                submit_stage,
                f"正在提交GX Works2{operation_label}CSV导出路径",
            )
        try:
            self._set_legacy_file_name(dialog, path)
        except Exception as error:
            if not operation:
                raise
            raise classify_automation_error(
                error,
                default_code=export_code,
                stage=submit_stage,
                retryable=True,
                message=(
                    f"无法向GX Works2提交{operation_label}CSV导出路径："
                    f"{describe_exception(error)}"
                ),
                details={"operation": operation},
            ) from error
        if not confirm_before_file:
            self._wait_confirmation_and_accept(
                session, "读取", timeout=min(self.timeout, 4.0)
            )
        result_stage = (
            "wait_comment_export_file"
            if operation == "comment_export"
            else "wait_program_export_file"
        )
        if operation:
            self._report_progress(
                result_stage,
                f"正在等待GX Works2生成{operation_label}CSV",
            )
        try:
            return self._wait_operation_result(
                session,
                destination=path if confirm_before_file else None,
            )
        except Exception as error:
            if not operation:
                raise
            raise classify_automation_error(
                error,
                default_code=export_code,
                stage=result_stage,
                retryable=True,
                message=(
                    f"等待GX Works2生成{operation_label}CSV时失败："
                    f"{describe_exception(error)}"
                ),
                details={"operation": operation},
            ) from error

    def _wait_operation_result(self, session, destination=None):
        started_at = time.monotonic()
        deadline = time.monotonic() + self.timeout
        last_message = ""
        destination_signature = None
        destination_stable_since = None
        main_ready_since = None
        while time.monotonic() < deadline:
            # Export mode is determined only from the requested backup file.
            # Do not ask the legacy UIA provider for another main-window
            # wrapper here: some GX Works2 MDI states block that call for
            # minutes even though the file is already being flushed.  A short
            # stable-size window filters the create-before-write race; the
            # following CSV validation remains authoritative.
            if destination and Path(destination).is_file():
                try:
                    stat = Path(destination).stat()
                    signature = (stat.st_size, stat.st_mtime_ns)
                except OSError:
                    signature = None
                now = time.monotonic()
                if signature and signature[0] > 2:
                    if signature != destination_signature:
                        destination_signature = signature
                        destination_stable_since = now
                    elif (
                        destination_stable_since is not None
                        and now - destination_stable_since >= 0.10
                    ):
                        return {
                            "success": True,
                            "message": "GX Works2已生成CSV备份",
                        }
                else:
                    destination_signature = None
                    destination_stable_since = None

                time.sleep(0.02)
                continue

            # After Read from CSV, GX Works2 writes its authoritative summary
            # (for example "Error: 0, Warning: 0") to the output pane.  The
            # pane is a legacy Win32 Edit that UIA does not expose reliably,
            # so read the control text directly instead of waiting for the
            # full timeout.
            main = self._main_window(session)
            main_ready = main.exists() and main.is_enabled()
            summary = (
                self._read_output_summary(session)
                if not destination
                and main_ready
                and time.monotonic() - started_at >= 0.25
                else ""
            )
            match = re.search(
                r"Error\s*:\s*(\d+)\s*,?\s*Warning\s*:\s*(\d+)",
                summary,
                re.I,
            )
            if match:
                error_count = int(match.group(1))
                warning_count = int(match.group(2))
                return {
                    "success": error_count == 0,
                    "message": summary,
                    "error_count": error_count,
                    "warning_count": warning_count,
                }

            dialog_handles = self._native_dialog_handles(session)
            for handle in dialog_handles:
                message = self._native_dialog_text(handle)
                if not message:
                    continue
                if message:
                    last_message = message
                if self.CONFIRMATION_TEXT.search(message):
                    continue
                if self.FAILURE_TEXT.search(message):
                    return {"success": False, "message": message}
                if self.SUCCESS_TEXT.search(message):
                    self._native_confirm_dialog(handle)
                    return {"success": True, "message": message}

            # Some GX Works2 versions never expose a parseable completion text.
            # The old code intentionally accepted an enabled MAIN frame as the
            # fallback acknowledgement, but only after waiting the full 12 s.
            # Accept the same observable state once it is stable and no modal
            # dialog remains; this removes ~12 s from each program/comment read.
            now = time.monotonic()
            if (
                not destination
                and main_ready
                and not dialog_handles
                and now - started_at >= 0.60
            ):
                if main_ready_since is None:
                    main_ready_since = now
                elif now - main_ready_since >= 0.45:
                    return {
                        "success": True,
                        "message": last_message or "GX Works2已返回可编辑状态",
                    }
            else:
                main_ready_since = None
            time.sleep(0.10)
        # Some GX Works2 versions close a successful import without a success
        # dialog. Returning to the editable main window is the observable ack.
        main = self._main_window(session)
        if main.exists() and main.is_enabled():
            return {"success": True, "message": last_message or "GX Works2已返回可编辑状态"}
        return {"success": False, "message": last_message or "未检测到GX Works2导入完成状态"}

    @staticmethod
    def _read_output_summary(session):
        if os.name != "nt":
            return ""
        user32 = ctypes.windll.user32
        values = []
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )

        def visit(hwnd, _lparam):
            class_buffer = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
            if class_buffer.value.casefold() != "edit":
                return True
            length = int(user32.SendMessageW(hwnd, 0x000E, 0, 0))  # WM_GETTEXTLENGTH
            if length <= 0 or length > 2048:
                return True
            text_buffer = ctypes.create_unicode_buffer(length + 1)
            user32.SendMessageW(
                hwnd,
                0x000D,  # WM_GETTEXT
                length + 1,
                text_buffer,
            )
            value = text_buffer.value.strip()
            if re.search(r"Error\s*:\s*\d+.*Warning\s*:\s*\d+", value, re.I):
                values.append(value)
            return True

        user32.EnumChildWindows(
            int(session.window_handle),
            callback_type(visit),
            0,
        )
        return values[-1] if values else ""

    @staticmethod
    def _confirm_dialog(window):
        for button in window.descendants(control_type="Button"):
            if button.is_enabled() and re.search(r"是|确定|OK|Yes", button.window_text(), re.I):
                button.invoke()
                return True
        return False

    def prepare_export_retry(self, session):
        """Dismiss only CSV-related transient UI, then reactivate MAIN."""

        dismissed = []
        if os.name == "nt":
            user32 = ctypes.windll.user32
            transient_pattern = re.compile(
                r"CSV|写入|读取|导出|打开|保存|另存为|Open|Save|Write|Read",
                re.I,
            )
            for handle in self._native_dialog_handles(session):
                text = self._native_dialog_text(handle)
                if not (
                    transient_pattern.search(text)
                    or self.CONFIRMATION_TEXT.search(text)
                ):
                    continue
                title = self._native_window_title(handle)
                # WM_COMMAND/IDCANCEL is the native equivalent of pressing
                # Escape in a common file or confirmation dialog.  Never send
                # it to the GX Works2 frame itself.
                user32.PostMessageW(int(handle), 0x0111, 2, 0)
                dismissed.append(title or text[:120])

        if dismissed:
            time.sleep(0.05)

        try:
            window = self._main_window(session)
            window.send_message(0x001F, 0, 0)  # WM_CANCELMODE, no SendInput
            self._activate_program_editor(session)
        except GXAutomationError:
            raise
        except Exception as error:
            raise classify_automation_error(
                error,
                default_code=GXSyncErrorCode.GX_MAIN_ACTIVATE_FAILED,
                stage="activate_main",
                retryable=True,
                message=(
                    "清理导出界面后无法重新激活MAIN："
                    + describe_exception(error)
                ),
                details={"dismissed_dialogs": dismissed},
            ) from error
        return {
            "dismissed_dialogs": dismissed,
            "main_activated": True,
        }

    def export_current_program(self, session, destination):
        self._report_progress("activate_main", "正在激活GX Works2 MAIN程序")
        try:
            self._activate_program_editor(session)
        except GXAutomationError:
            raise
        except Exception as error:
            raise classify_automation_error(
                error,
                default_code=GXSyncErrorCode.GX_MAIN_ACTIVATE_FAILED,
                stage="activate_main",
                retryable=True,
                message="无法激活GX Works2 MAIN程序：" + describe_exception(error),
            ) from error
        try:
            result = self._invoke_csv_command(
                session,
                self.WRITE_CSV_ITEM,
                destination,
                accelerator=self.PROGRAM_WRITE_KEY,
                confirm_before_file=True,
                operation="program_export",
            )
        except GXAutomationError:
            raise
        except Exception as error:
            raise classify_automation_error(
                error,
                default_code=GXSyncErrorCode.GX_PROGRAM_EXPORT_FAILED,
                stage="export_program",
                retryable=True,
                message="GX Works2程序CSV导出失败：" + describe_exception(error),
            ) from error
        if not result.get("success"):
            raise GXAutomationError(
                GXSyncErrorCode.GX_PROGRAM_EXPORT_FAILED,
                "wait_program_export_file",
                result.get("message") or "GX Works2程序CSV写出失败。",
                retryable=True,
                details={"gxworks2_result": dict(result)},
            )
        if not Path(destination).is_file():
            raise GXAutomationError(
                GXSyncErrorCode.GX_PROGRAM_EXPORT_FAILED,
                "wait_program_export_file",
                "GX Works2未生成程序CSV。",
                retryable=True,
                details={"export_path": str(Path(destination).resolve())},
            )

    def import_program_csv(self, session, csv_path):
        self._ensure_program_writable(session)
        return self._invoke_csv_command(
            session,
            self.READ_CSV_ITEM,
            csv_path,
            accelerator=self.PROGRAM_READ_KEY,
        )

    def export_current_comments(self, session, destination):
        self._report_progress("activate_comments", "正在打开GX Works2软元件注释")
        try:
            self._activate_comments_editor(session)
        except GXAutomationError:
            raise
        except Exception as error:
            raise classify_automation_error(
                error,
                default_code=GXSyncErrorCode.GX_COMMENT_EXPORT_FAILED,
                stage="activate_comments",
                retryable=True,
                message="无法激活GX Works2软元件注释：" + describe_exception(error),
            ) from error
        try:
            result = self._invoke_csv_command(
                session,
                self.WRITE_CSV_ITEM,
                destination,
                accelerator=self.COMMENT_WRITE_KEY,
                confirm_before_file=True,
                operation="comment_export",
            )
        except GXAutomationError:
            raise
        except Exception as error:
            raise classify_automation_error(
                error,
                default_code=GXSyncErrorCode.GX_COMMENT_EXPORT_FAILED,
                stage="export_comments",
                retryable=True,
                message="GX Works2注释CSV导出失败：" + describe_exception(error),
            ) from error
        if not result.get("success"):
            raise GXAutomationError(
                GXSyncErrorCode.GX_COMMENT_EXPORT_FAILED,
                "wait_comment_export_file",
                result.get("message") or "GX Works2注释CSV写出失败。",
                retryable=True,
                details={"gxworks2_result": dict(result)},
            )
        if not Path(destination).is_file():
            raise GXAutomationError(
                GXSyncErrorCode.GX_COMMENT_EXPORT_FAILED,
                "wait_comment_export_file",
                "GX Works2未生成软元件注释CSV。",
                retryable=True,
                details={"export_path": str(Path(destination).resolve())},
            )

    def import_comments_csv(self, session, csv_path):
        window = self._activate_comments_editor(session)
        scope = "program" if self.LOCAL_COMMENT_EDITOR.search(window.window_text()) else "global"
        try:
            result = self._invoke_csv_command(
                session,
                self.READ_CSV_ITEM,
                csv_path,
                accelerator=self.COMMENT_READ_KEY,
            )
            return {**result, "comment_scope": scope, "comment_name": "MAIN" if scope == "program" else "COMMENT"}
        finally:
            # Leave the user on the program they asked to update.
            try:
                self._activate_program_editor(session)
            except Exception:
                pass

    def create_simple_project(self, session, path, *, cpu, progress=None):
        """Create/save an exact native menu target in an exclusive location.

        The caller supplies an exclusive managed destination after approval.
        Never replace an already-open project or acknowledge its save prompt.
        """
        import win32api
        from .native_locale import inspect_native_language, native_caption

        path = Path(path).resolve()
        profile = native_project_profile(cpu)
        if (profile is None or session.project_open or path.suffix.lower() != ".gxw"
                or path.exists() or len(path.stem) > 30 or not path.parent.is_dir()):
            raise RuntimeError("自动新建工程的型号、当前目标或独立保存路径无效。")
        version = win32api.GetFileVersionInfo(session.executable, "\\")
        ms, ls = version["FileVersionMS"], version["FileVersionLS"]
        if (ms >> 16, ms & 65535, ls >> 16, ls & 65535) != GXWORKS2_NATIVE_VERSION:
            raise RuntimeError("自动新建工程尚未验证此 GX Works2 版本。")
        if self._native_dialog_handles(session):
            raise RuntimeError("GX Works2有未处理的对话框，无法自动准备工程。")
        native_ui_language = inspect_native_language(session.process_id, session.executable)
        timings = {}
        started = time.monotonic()
        main = self._main_window(session)
        self._post_native_command(session, self.NEW_PROJECT_COMMAND)
        dialog = self._wait_legacy_dialog(session, re.compile(
            '^' + re.escape(native_caption('新建', native_ui_language)) + '$', re.I))
        # Original controls observed in GX Works2 1.635.0.1, not positions or
        # UIA ids. Read the actual menu values before selecting exact strings.
        series = dialog.child_window(control_id=10002, class_name="ComboBox")
        families = [text for text in series.item_texts() if text in profile.series]
        if len(families) != 1:
            raise RuntimeError("GX Works2新建窗口没有明确的CPU系列。")
        self._select_native_combo(series, families[0])
        model = dialog.child_window(control_id=10003, class_name="ComboBox")
        kind = dialog.child_window(control_id=10001, class_name="ComboBox")
        language = dialog.child_window(control_id=10070, class_name="ComboBox")
        kinds = [text for text in kind.item_texts() if text in {"简单工程", "Simple Project"}]
        if len(kinds) != 1:
            raise RuntimeError("GX Works2新建窗口没有明确的简单工程类型。")
        # Structured mode filters the FX CPU list. Restore Simple before
        # selecting the model, then recheck controls after its notifications.
        if kind.is_enabled():
            self._select_native_combo(kind, kinds[0])
        self._select_native_combo(model, profile.cpu)
        if kind.is_enabled():
            self._select_native_combo(kind, kinds[0])
        elif kind.selected_text() != kinds[0]:
            raise RuntimeError("此CPU没有可用的简单工程类型。")
        labels = dialog.child_window(control_id=10068, class_name="Button")
        if labels.is_enabled() and labels.get_check_state():
            self._set_native_check(labels, False)
        languages = [text for text in language.item_texts() if text in {"梯形图", "Ladder"}]
        if len(languages) != 1:
            raise RuntimeError("GX Works2新建窗口没有明确的梯形图语言。")
        if language.is_enabled():
            self._select_native_combo(language, languages[0])
        elif language.selected_text() != languages[0]:
            raise RuntimeError("此CPU没有可用的梯形图语言。")
        if (model.selected_text() != profile.cpu or series.selected_text() != families[0]
                or kind.selected_text() != kinds[0] or language.selected_text() != languages[0]):
            raise RuntimeError("GX Works2新建选项发生变化，未确认目标CPU和工程类型。")
        timings["new_dialog"] = round((time.monotonic() - started) * 1000, 3)
        if progress:
            progress("prepare_project", "正在等待GX Works2初始化新的MAIN编辑器…")
        started = time.monotonic()
        dialog.child_window(control_id=1, class_name="Button").click()
        self._wait_created_program(session, profile, main)
        timings["initialize_project"] = round((time.monotonic() - started) * 1000, 3)
        if progress:
            progress("prepare_project", "MAIN已新建，正在首次保存GX工程…")
        started = time.monotonic()
        self._post_native_command(session, self.SAVE_PROJECT_COMMAND)
        dialog = self._wait_legacy_dialog(session, re.compile(
            '^' + re.escape(native_caption('工程另存为', native_ui_language)) + '$', re.I))
        timings["open_save_dialog"] = round((time.monotonic() - started) * 1000, 3)
        started = time.monotonic()
        self._set_legacy_file_name(dialog, path)
        deadline = time.monotonic() + self.timeout
        notices = []
        while time.monotonic() < deadline:
            notices.extend(self._acknowledge_known_save_notices(session, native_ui_language=native_ui_language))
            if path.is_file() and path.stat().st_size > 0 and path.name.casefold() in main.window_text().casefold():
                if not self._native_dialog_handles(session):
                    timings["save_new_project"] = round((time.monotonic() - started) * 1000, 3)
                    return {"success": True, "cpu": profile.cpu, "program_supported": profile.program_supported,
                            "path": str(path), "timings_ms": timings, "notices": notices}
            time.sleep(.05)
        raise TimeoutError("未确认新建GX工程已经保存到独立目标。")

    def _wait_created_program(self, session, profile, main):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self._native_dialog_handles(session):
                raise RuntimeError("GX Works2新建工程出现额外提示，请检查原生结果。")
            if profile.program_supported:
                if any(self.PROGRAM_EDITOR.search(title) for title in self._child_window_titles(session)):
                    # The persisted GX preference can open a new MAIN in Read
                    # Mode. Establish the editor first, then use the existing
                    # exact-source activation and verified Write Mode path.
                    try:
                        self._ensure_program_writable(session)
                    except NativeEditorNotFound:
                        # The MDI title can appear before the new project's
                        # source tree. Keep verifying that exact source until
                        # initialization completes; never accept a stale title.
                        pass
                    else:
                        return
            elif re.search(r"工程未设置|project not set", main.window_text(), re.I):
                return
            time.sleep(.05)
        raise TimeoutError("未确认GX Works2已建立可编辑的MAIN。")

    def _acknowledge_known_save_notices(self, session, *, native_ui_language='zh-CN'):
        """Acknowledge only the observed one-button native parameter notice.

        No PLC parameter, authentication mode or operation-history setting is
        changed. Every other dialog is left for inspection and stops the save.
        """
        from gxw.native_locale import ui_text
        expected_notice = ui_text('auth_save_notice', native_ui_language)
        # This one-button notice uses Windows' message-button language even
        # when its body comes from another GX resource satellite. Its exact
        # body, sole ID2 button and ownership are still checked independently.
        expected_buttons = {ui_text('ok', language) for language in ('zh-CN', 'en', 'ja')}
        notices = []
        user32 = ctypes.windll.user32 if os.name == "nt" else None
        def owned_window(handle):
            process_id = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
            return int(process_id.value) == int(session.process_id)
        for handle in self._native_dialog_handles(session):
            if not user32.IsWindow(handle):
                continue
            class_buffer = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(handle, class_buffer, len(class_buffer))
            if (class_buffer.value != '#32770' or not user32.IsWindowVisible(handle)
                    or not user32.IsWindow(session.window_handle) or not owned_window(session.window_handle)
                    or not owned_window(handle)
                    or int(user32.GetAncestor(handle, 3) or 0) != int(session.window_handle)):
                raise RuntimeError('GX Works2保存提示不属于已绑定的原生窗口。')
            messages, buttons = [], []
            for child in self._native_descendants(handle):
                class_buffer = ctypes.create_unicode_buffer(128)
                user32.GetClassNameW(child, class_buffer, len(class_buffer))
                text = self._native_window_title(child).replace("\r\n", "\n").replace("\r", "\n")
                if class_buffer.value == "Static" and text:
                    messages.append(text)
                if class_buffer.value == "Button" and user32.IsWindowVisible(child):
                    buttons.append((child, user32.GetDlgCtrlID(child), text))
            # Modal notices can disappear between enumeration and the read.
            # Raw HWND reads avoid a stale pywinauto wrapper's multi-second
            # retry after the informational dialog has already closed.
            if not user32.IsWindow(handle):
                continue
            if (messages != [expected_notice] or len(buttons) != 1
                    or buttons[0][1] != 2 or buttons[0][2] not in expected_buttons):
                raise RuntimeError("GX Works2保存时出现未适配的提示：" + self._native_dialog_text(handle))
            button = buttons[0][0]
            if (not user32.IsWindow(button) or not user32.IsWindowEnabled(button)
                    or not user32.IsChild(handle, button) or not owned_window(handle) or not owned_window(button)
                    or int(user32.GetAncestor(handle, 3) or 0) != int(session.window_handle)):
                raise RuntimeError('GX Works2保存提示的确认按钮不属于已绑定的原生窗口。')
            notices.append(messages[0])
            user32.SendMessageW(button, 0x00F5, 0, 0)  # BM_CLICK
        return notices

    def save_project(self, session):
        project_name = str(getattr(session, "project_name", "") or "").strip().casefold()
        if project_name in {
            "(工程未设置)",
            "工程未设置",
            "(project not set)",
            "project not set",
        }:
            return {
                "success": False,
                "save_required": True,
                "message": "当前GX Works2工程尚未命名，请先在GX Works2中选择保存位置。",
            }
        try:
            window = self._main_window(session)
            self._post_native_command(session, self.SAVE_PROJECT_COMMAND)
            time.sleep(0.2)
            notices = self._acknowledge_known_save_notices(session)
            return {
                "success": True,
                "save_required": False,
                "message": "GX Works2工程已保存。",
                "notices": notices,
            }
        except Exception as error:
            return {
                "success": False,
                "save_required": True,
                "message": f"无法自动保存GX Works2工程：{error}",
            }

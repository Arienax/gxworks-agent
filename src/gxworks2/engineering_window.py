"""Read and control the window bound to one engineering session."""
from __future__ import annotations
from pathlib import Path
import re
import time
from gxw.native_locale import label_title
from .engineering_errors import NativeEngineeringDriverError

class EngineeringWindow:
    def __init__(self, desktop):
        self.desktop = desktop

    def _window(self):
        return self.desktop.ui._main_window(self.desktop._frame_identity())

    def _foreground(self, handle=None):
        import ctypes
        import win32gui
        import win32process
        self.desktop._frame_identity()
        target = self.desktop.session.window_handle if handle is None else handle
        if win32process.GetWindowThreadProcessId(target)[1] != self.desktop.session.process_id:
            raise NativeEngineeringDriverError('待激活的页面不属于已绑定的 GX Works2 进程。')
        window = self.desktop.ui._application(self.desktop.session).window(handle=target)
        if win32gui.GetForegroundWindow() == target:
            return
        # A controller process does not necessarily own the foreground input
        # queue. Temporarily join the actual queues, and always detach again.
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        foreground = win32gui.GetForegroundWindow()
        threads = {win32process.GetWindowThreadProcessId(target)[0]}
        if foreground:
            threads.add(win32process.GetWindowThreadProcessId(foreground)[0])
        attached = []
        try:
            for other in sorted(threads - {current_thread}):
                if ctypes.windll.user32.AttachThreadInput(current_thread, other, True):
                    attached.append(other)
            if win32gui.IsIconic(target):
                win32gui.ShowWindow(target, 9)
            win32gui.BringWindowToTop(target)
            ctypes.windll.user32.SetForegroundWindow(target)
            if win32gui.GetForegroundWindow() != target:
                try:
                    window.set_focus()
                except Exception:
                    pass
        finally:
            for other in reversed(attached):
                ctypes.windll.user32.AttachThreadInput(current_thread, other, False)
        if win32gui.GetForegroundWindow() != target:
            raise NativeEngineeringDriverError('没有确认 GX Works2 工程窗口实际位于前台。')

    def _dialogs(self):
        import win32gui
        import win32process
        handles = list(self.desktop.ui._native_dialog_handles(self.desktop.session))
        def visit(hwnd, _):
            if (win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == '#32770'
                    and win32process.GetWindowThreadProcessId(hwnd)[1] == self.desktop.session.process_id):
                if hwnd not in handles:
                    handles.append(hwnd)
        win32gui.EnumWindows(visit, None)
        return [self.desktop.ui._application(self.desktop.session).window(handle=h) for h in handles]

    @staticmethod
    def _dialog_text(dialog):
        return '\n'.join(w.window_text() for w in dialog.descendants(class_name='Static')).strip()

    def _idle(self):
        self.desktop.inspect()
        dialogs = self.desktop._dialogs()
        if dialogs:
            raise NativeEngineeringDriverError('GX Works2 有尚未完成的原生对话框。',
                observation={'dialogs': [{'title': d.window_text(), 'message': self.desktop._dialog_text(d)} for d in dialogs]})
        if not self.desktop._window().is_enabled():
            raise NativeEngineeringDriverError('GX Works2 工程窗口尚未返回可编辑状态。')

    def _command(self, command, *, allow_dialog=False, project=True):
        if project:
            self.desktop.inspect()
        else:
            self.desktop._frame_identity()
        if not allow_dialog and self.desktop._dialogs():
            raise NativeEngineeringDriverError('存在原生对话框，未发出新的工程命令。')
        if not allow_dialog:
            self.desktop._foreground()
        self.desktop.ui._post_native_command(self.desktop.session, command)

    def _wait(self, predicate, message, *, timeout=None):
        deadline = time.monotonic() + (self.desktop.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            self.desktop._frame_identity()
            value = predicate()
            if value:
                return value
            time.sleep(.05)
        raise NativeEngineeringDriverError(message,
            observation={'dialogs': [{'title': d.window_text(), 'message': self.desktop._dialog_text(d)} for d in self.desktop._dialogs()]})

    def _dialog(self, title):
        expected = {self.desktop._caption(title)}
        if title == '新建数据':
            expected.add(self.desktop._text('dialog_new_data_navigation'))
        def found():
            rows = [d for d in self.desktop._dialogs() if d.window_text() in expected and d.is_enabled()]
            return rows[0] if len(rows) == 1 else None
        return self.desktop._wait(found, '未确认原生对话框：' + title)

    def _click(self, dialog, control_id):
        import win32gui
        button = dialog.child_window(control_id=control_id, class_name='Button')
        if not dialog.is_enabled() or not button.is_enabled():
            raise NativeEngineeringDriverError('原生按钮当前不可用。')
        self.desktop._foreground(dialog.handle)
        # Validation can open a nested native modal dialog. Posting leaves the
        # controller able to observe it instead of blocking inside BM_CLICK.
        win32gui.PostMessage(button.handle, 0x00F5, 0, 0)

    def _confirm(self, fragment, control_id):
        fragment = self.desktop._caption(fragment)
        def found():
            rows = [d for d in self.desktop._dialogs() if d.is_enabled() and fragment in self.desktop._dialog_text(d)]
            return rows[0] if len(rows) == 1 else None
        dialog = self.desktop._wait(found, '未确认本次操作的原生提示。')
        text = self.desktop._dialog_text(dialog)
        self.desktop._click(dialog, control_id)
        return text

    def _file_name(self, dialog, path):
        path = str(Path(path).resolve())
        self.desktop._foreground(dialog.handle)
        edits = [e for e in dialog.children(class_name='Edit') if e.is_enabled()]
        if not edits:
            edits = [e for e in dialog.descendants(class_name='Edit') if e.is_enabled()]
        named = [e for e in edits if e.control_id() == 1152 or
                 e.control_id() == 1148 and e.parent().class_name() == 'ComboBox'
                 and e.parent().control_id() == 1148
                 and e.parent().parent().class_name() == 'ComboBoxEx32'
                 and e.parent().parent().control_id() == 1148]
        if len(named) == 1:
            editor = named[0]
        elif len(edits) == 1:
            editor = edits[0]
        else:
            raise NativeEngineeringDriverError('原生文件对话框的文件名字段不明确。')
        editor.set_focus()
        editor.set_edit_text(path)
        if editor.window_text() != path:
            raise NativeEngineeringDriverError('原生文件名字段没有保留完整目标路径。')
        dialog.child_window(control_id=1, class_name='Button').set_focus()
        self.desktop._click(dialog, 1)

    def _visible(self, page, *, dialog=None, **details):
        if dialog is None:
            self.desktop._idle()
            self.desktop._foreground()
        else:
            self.desktop.inspect()
            active = [d for d in self.desktop._dialogs() if d.is_enabled()]
            if len(active) != 1 or active[0].handle != dialog.handle:
                raise NativeEngineeringDriverError('待展示的原生设置对话框不明确。')
            self.desktop._foreground(dialog.handle)
        import win32gui
        win32gui.RedrawWindow(self.desktop.session.window_handle, None, None, 0x0185)
        time.sleep(.1)  # paint only; data completion is verified separately
        self.desktop._captures += 1
        receipt = {'completed': True, 'page': page, **details}
        if self.desktop.evidence_directory:
            path = self.desktop.evidence_directory / f'{self.desktop._captures:03d}.png'
            capture = self.desktop._window().capture_as_image()
            if capture is None:
                raise NativeEngineeringDriverError(
                    '原生页面已完成，但窗口截图未返回图像；请检查窗口尺寸、桌面可访问性及 Pillow。',
                    observation={'stage': 'visible_capture', 'page': page, 'operation_completed': True})
            capture.save(path)
            receipt['image'] = str(path)
        if self.desktop.progress:
            self.desktop.progress('visible_completion', page)
        return receipt

    def _active_document(self):
        import win32gui
        mdi = self.desktop._window().child_window(class_name='MDIClient').wrapper_object()
        handle = win32gui.SendMessage(mdi.handle, 0x0229, 0, 0)
        if not handle:
            raise NativeEngineeringDriverError('原生工程没有活动编辑页面。')
        return self.desktop.ui._application(self.desktop.session).window(handle=handle)

    def _clear_fb_instance_view(self, document, owner, expected_title):
        """Return one exact FB instance view to its definition, without Save."""
        import win32gui
        import win32process
        title = document.window_text().rstrip()
        body, fbd = self.desktop._text('nav_body'), self.desktop._text('language_fbd')
        instance = re.fullmatch(re.escape(owner) +
            r' \[([^\[\]\r\n]+\.[^\[\]\r\n]+)\] ' + re.escape(body) +
            r' \[(ST|' + re.escape(fbd) + r')\]', title)
        if instance is None or expected_title != owner + ' [FB] ' + body + ' [' + instance[2] + ']':
            raise NativeEngineeringDriverError('实例显示与明确指定的 FB 定义页面不同。')
        self.desktop._idle()
        handle, frame, pid = int(document.handle), self.desktop.session.window_handle, self.desktop.session.process_id
        def same_document():
            return (win32gui.IsWindow(handle) and win32gui.IsChild(frame, handle)
                and win32process.GetWindowThreadProcessId(handle)[1] == pid
                and self.desktop._active_document().handle == handle)
        if not same_document() or win32gui.GetWindowText(handle).rstrip() != title:
            raise NativeEngineeringDriverError('待解除的 FB 实例页面已改变。')
        # RT_MENU128/136 command0x4EF opens RT_DIALOG10000. Its Clear button
        #10001 calls native SetInstance with an empty path and ends the dialog.
        # Cache HWNDs before posting; pywinauto specs cannot resolve a destroyed
        # dialog after Clear has returned. Each message is sent once.
        self.desktop._command(0x4EF)
        dialog = self.desktop._dialog('FB实例选择')
        dialog_handle = int(dialog.handle)
        dialogs = self.desktop._dialogs()
        if (len(dialogs) != 1 or int(dialogs[0].handle) != dialog_handle
                or win32gui.GetClassName(dialog_handle) != '#32770'
                or win32process.GetWindowThreadProcessId(dialog_handle)[1] != pid
                or win32gui.GetAncestor(dialog_handle, 3) != frame or not dialog.is_enabled()):
            raise NativeEngineeringDriverError('FB 实例选择对话框不属于当前工程窗口。')
        expected = {10001: ('Button', '解除选择(&C)'), 10002: ('ListBox', None),
                    1: ('Button', '确定'), 2: ('Button', '取消')}
        controls = {}
        for identity, (class_name, caption) in expected.items():
            control = win32gui.GetDlgItem(dialog_handle, identity)
            if (not control or not win32gui.IsChild(dialog_handle, control)
                    or win32process.GetWindowThreadProcessId(control)[1] != pid
                    or win32gui.GetClassName(control) != class_name
                    or caption is not None and win32gui.GetWindowText(control) != self.desktop._caption(caption)):
                raise NativeEngineeringDriverError('FB 实例选择对话框的原生控件结构不同。')
            controls[identity] = int(control)
        clear = controls[10001]
        if (not win32gui.IsWindowVisible(clear) or not win32gui.IsWindowEnabled(clear)
                or not same_document() or win32gui.GetWindowText(handle).rstrip() != title):
            raise NativeEngineeringDriverError('当前 FB 实例页面或解除选择按钮不可用。')
        win32gui.PostMessage(dialog_handle, 0x0111, 10001, clear)  # BN_CLICKED, once
        def returned():
            if win32gui.IsWindow(dialog_handle) and win32gui.IsWindowVisible(dialog_handle):
                return None
            if self.desktop._dialogs():
                raise NativeEngineeringDriverError('解除 FB 实例显示后出现了未确认的原生提示。')
            if not same_document():
                raise NativeEngineeringDriverError('解除 FB 实例显示后活动文档已改变。')
            if win32gui.GetWindowText(handle).rstrip() != expected_title:
                return None
            return self.desktop._active_document()
        return self.desktop._wait(returned, '当前 FB 实例显示尚未返回同一文档的定义页面。')

    def _navigate(self, paths, title_pattern):
        self.desktop._idle()
        import win32gui
        import win32process
        result_windows = []
        def find_result(handle, _):
            if (win32gui.IsWindowVisible(handle)
                    and win32process.GetWindowThreadProcessId(handle)[1] == self.desktop.session.process_id
                    and win32gui.GetClassName(handle) == 'XTPDockingPaneMiniWnd'
                    and win32gui.GetWindowText(handle).strip() == self.desktop._caption('交叉参照')):
                result_windows.append(handle)
        win32gui.EnumWindows(find_result, None)
        for handle in result_windows:
            # Close through the docking manager. Hiding the HWND alone leaves
            # its internal open state out of step with the native View menu.
            win32gui.PostMessage(handle, 0x0010, 0, 0)
        if result_windows:
            self.desktop._wait(lambda: all(not win32gui.IsWindowVisible(h) for h in result_windows),
                       '交叉参照窗口尚未让出目标编辑页面。')
        window = self.desktop._window()
        trees = [t for t in window.descendants(class_name='SysTreeView32')
                 if t.control_id() == 12051 and t.is_visible() and t.is_enabled()]
        if len(trees) != 1:
            raise NativeEngineeringDriverError('原生工程导航树不明确。')
        def tree_item():
            matches = []
            for path in paths:
                try:
                    item = trees[0].get_item(self.desktop._tree_path(path), exact=True)
                except (IndexError, RuntimeError):
                    continue
                if item is not None:
                    matches.append(item)
            if len(matches) > 1:
                raise NativeEngineeringDriverError('原生工程对象路径不唯一。')
            return matches[0] if matches else None
        item = self.desktop._wait(tree_item, '原生工程对象路径尚未出现。')
        document = self.desktop._active_document()
        if re.search(title_pattern, document.window_text()):
            # A bound active editor can be read without stealing desktop
            # focus or reopening it (also while Simulator2 owns the desktop).
            return document
        self.desktop._foreground()
        self.desktop.ui._select_native_tree_item(trees[0], item)
        # Reading an already-open definition must not reopen its data object.
        # Native Enter can reload the RichEdit asynchronously even when the
        # caption and HWND are unchanged, invalidating the following read.
        if not self.desktop.ui._activate_native_document(self.desktop.session, re.compile(title_pattern)):
            self.desktop.ui._send_tree_enter(trees[0])
        fb_owner = None
        if (len(paths) == 1 and len(paths[0]) == 4 and tuple(paths[0][:2]) == ('程序部件', 'FB/FUN')
                and paths[0][3] == '程序本体'):
            owner = paths[0][2]
            definition_prefix = '^' + re.escape(owner + ' [FB] ' + self.desktop._text('nav_body') + ' [')
            if title_pattern.replace(r'\ ', ' ').startswith(definition_prefix.replace(r'\ ', ' ')):
                fb_owner = owner
        instance_clear_attempted = False
        def active():
            nonlocal instance_clear_attempted
            doc = self.desktop._active_document()
            if not re.search(title_pattern, doc.window_text()):
                # Prefer an already-open exact definition MDI. A native FB
                # instance can instead reuse that same MDI with a qualified
                # instance caption, so tree Enter alone cannot restore it.
                if self.desktop.ui._activate_native_document(self.desktop.session, re.compile(title_pattern)):
                    doc = self.desktop._active_document()
                elif fb_owner is not None and not instance_clear_attempted:
                    instance = re.fullmatch(re.escape(fb_owner) +
                        r' \[([^\[\]\r\n]+\.[^\[\]\r\n]+)\] ' + re.escape(self.desktop._text('nav_body')) +
                        r' \[(ST|' + re.escape(self.desktop._text('language_fbd')) + r')\]\s*', doc.window_text())
                    expected_title = fb_owner + ' [FB] ' + self.desktop._text('nav_body') + ' [' + instance[2] + ']' if instance else None
                    if expected_title and re.search(title_pattern, expected_title):
                        instance_clear_attempted = True
                        doc = self.desktop._clear_fb_instance_view(doc, fb_owner, expected_title)
            return doc if re.search(title_pattern, doc.window_text()) else None
        doc = self.desktop._wait(active, '原生页面没有切换到指定工程对象。')
        self.desktop.ui._activate_native_document(self.desktop.session, re.compile(title_pattern))
        return doc

    def _labels_page(self, name, scope):
        if scope == 'global':
            return self.desktop._navigate([('全局标签', name)], '^' + re.escape(self.desktop._text('title_global') + ' ' + name) + r'\s*$')
        if scope == 'structure':
            return self.desktop._navigate([('程序部件', '结构体', name)], '^' + re.escape(self.desktop._text('title_structure') + ' ' + name) + r'\s*$')
        owner = '程序' if scope == 'program' else 'FB/FUN'
        expected = label_title(name, scope, language=getattr(self.desktop, 'native_ui_language', 'zh-CN'))
        return self.desktop._navigate([('程序部件', owner, name, '局部标签')], '^' + re.escape(expected) + r'\s*$')

    def _program_page(self, name, kind='program', language='fbd'):
        owner = '程序' if kind == 'program' else 'FB/FUN'
        pattern = '^' + re.escape(self.desktop._program_title(name, kind, language)) + r'\s*$'
        document = self.desktop._navigate([('程序部件', owner, name, '程序本体')], pattern)
        if language != 'st':
            return document
        # GX exposes the MDI title before finishing the RichEdit reload. Take
        # the source observation only after that exact editor has settled.
        # read_st still rejects any subsequent source or identity change.
        stable = [None, None]
        def ready():
            current = self.desktop._active_document()
            if current.handle != document.handle or not re.search(pattern, current.window_text()):
                raise NativeEngineeringDriverError('ST 页面加载期间活动对象已改变。')
            controls = [control for control in document.descendants(class_name='RichEdit20W')
                        if control.control_id() == 100]
            if len(controls) != 1:
                stable[:] = [None, None]
                return False
            editor, = controls
            observation = editor.handle, editor.window_text()
            now = time.monotonic()
            if observation != stable[0]:
                stable[:] = [observation, now]
                return False
            return now - stable[1] >= .15
        self.desktop._wait(ready, '指定 ST 编辑器尚未完成原生文本加载。')
        return document

    def _owned_control_handles(self, class_name, control_id, *, visible=True):
        import win32gui
        import win32process
        roots = []
        def visit(handle, _):
            if ((not visible or win32gui.IsWindowVisible(handle))
                    and win32process.GetWindowThreadProcessId(handle)[1] == self.desktop.session.process_id
                    and win32gui.GetClassName(handle) in ('GXW2FrameWnd', 'XTPDockingPaneMiniWnd')):
                roots.append(handle)
        win32gui.EnumWindows(visit, None)
        controls = []
        def child(handle, _):
            if ((not visible or win32gui.IsWindowVisible(handle)) and win32gui.GetClassName(handle) == class_name
                    and win32gui.GetDlgCtrlID(handle) == control_id):
                controls.append(handle)
        for root in roots:
            if win32gui.IsWindow(root):
                win32gui.EnumChildWindows(root, child, None)
        return list(dict.fromkeys(controls))

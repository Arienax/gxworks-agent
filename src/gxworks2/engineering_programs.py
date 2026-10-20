"""Program pages, ST text edits and execution task binding."""
from __future__ import annotations
import re
from .engineering_errors import NativeEngineeringDriverError
from .engineering_files import _same_text
from .engineering_observations import _post_native_client_click

class ProgramEditor:
    def __init__(self, desktop):
        self.desktop = desktop

    def _new_object(self, name, kind, language=None):
        self.desktop._idle()
        self.desktop._command(0x408)
        dialog = self.desktop._dialog('新建数据')
        return self.desktop._complete_new_object(dialog, name, kind, language)

    def _complete_new_object(self, dialog, name, kind, language=None):
        """Complete the already-open owned New Data action, without dispatching it again."""
        self.desktop.inspect()
        if dialog.handle != self.desktop._dialog('新建数据').handle:
            raise NativeEngineeringDriverError('新建数据操作不属于本次已打开的原生对话框。')
        key = {'程序块': 'new_kind_program', '全局标签': 'new_kind_global',
               '结构体': 'new_kind_structure', 'FB': 'new_kind_fb', '函数': 'new_kind_function'}.get(kind)
        if key is None:
            raise NativeEngineeringDriverError('新建对象类型没有已核对的原生资源身份。')
        self.desktop.ui._select_native_combo(dialog.child_window(control_id=10001, class_name='ComboBox'), self.desktop._text(key))
        dialog.child_window(control_id=10006, class_name='Edit').set_edit_text(name)
        if language:
            control = dialog.child_window(control_id=10003, class_name='ListBox')
            choices = control.item_texts()
            language = self.desktop._caption(language)
            if choices.count(language) != 1:
                raise NativeEngineeringDriverError('新建对象没有明确的原生语言选项。')
            index = choices.index(language)
            if control.send_message(0x0186, index, 0) != index:
                raise NativeEngineeringDriverError('原生语言选择未确认。')
            control.notify_parent(1)
        self.desktop._click(dialog, 1)
        self.desktop._wait(lambda: not self.desktop._dialogs(), '新建工程对象没有完成。')

    def read_st(self, name, kind='program'):
        """Read the exact current RichEdit body, with no saved-project fallback."""
        from gxw.active_context import read_active_st_text
        from gxw.models import GXWFormatError
        if kind not in ('program', 'fb'):
            raise NativeEngineeringDriverError('读取 ST 需要明确的 program 或 fb 归属。')
        identity = self.desktop.inspect()
        document = self.desktop._program_page(name, kind, 'st')
        title = document.window_text()
        controls = [control for control in document.descendants(class_name='RichEdit20W')
                    if control.control_id() == 100]
        if len(controls) != 1:
            raise NativeEngineeringDriverError('ST 编辑器的原生文本控件不明确。')
        editor = controls[0]
        text = editor.window_text()
        self.desktop._verify_read_page(identity, document, title)
        if editor.window_text() != text:
            raise NativeEngineeringDriverError('ST 正文在读取期间发生变化。')
        try:
            return read_active_st_text(text, owner=name, kind=kind, identity=identity,
                document_handle=document.handle, document_title=title, editor_handle=editor.handle)
        except GXWFormatError as error:
            raise NativeEngineeringDriverError(str(error)) from error

    def _write_st(self, name, text, *, before=None, kind='fb'):
        import win32gui
        document = self.desktop._program_page(name, kind, 'st')
        controls = [w for w in document.descendants(class_name='RichEdit20W') if w.control_id() == 100]
        if len(controls) != 1:
            raise NativeEngineeringDriverError('ST 编辑器的原生文本控件不明确。')
        editor = controls[0]
        if before is not None and not _same_text(editor.window_text(), before):
            raise NativeEngineeringDriverError('活动 ST 正文与本步骤基础源码不同。')
        win32gui.SendMessage(editor.handle, 0x00B1, 0, -1)
        win32gui.SendMessage(editor.handle, 0x00C2, 1, text)
        self.desktop._wait(lambda: _same_text(editor.window_text(), text), '原生 ST 编辑器回读与请求正文不同。')
        return self.desktop._visible(name + ' ST 编辑器', native_text=editor.window_text())

    def _task_tree_item(self, tree, resource, task):
        root = tree.get_item((self.desktop._text('nav_settings'),), exact=True)
        buckets = {self.desktop._text(key) for key in ('nav_execution', 'nav_initial_program',
            'nav_scan_program', 'nav_standby_program', 'nav_fixed_scan_program', 'nav_no_execution_type')}
        matches = []
        for bucket in root.children():
            for current_resource in bucket.children():
                if current_resource.text() != resource:
                    continue
                for current_task in current_resource.children():
                    if current_task.text() == task:
                        matches.append((bucket.text(), current_task))
        if len(matches) != 1 or matches[0][0] not in buckets:
            raise NativeEngineeringDriverError('当前任务没有唯一且已识别的原生执行类型归属。')
        return matches[0][1]

    def _bind_program_task(self, plan, *, stage_save=True):
        """Append a Program through its actual native task registration page."""
        if type(stage_save) is not bool:
            raise NativeEngineeringDriverError('任务登记保存边界必须是明确的布尔值。')
        import win32gui
        import win32process
        from pywinauto.keyboard import send_keys
        from gxw.native_write import native_task_scope_context
        line = plan['line']
        if type(line) is not int or not 1 <= line <= 65535:
            raise NativeEngineeringDriverError('原生任务登录行号不在已验证的范围内。')
        self.desktop._idle()
        self.desktop._foreground()
        trees = [t for t in self.desktop._window().descendants(class_name='SysTreeView32')
                 if t.control_id() == 12051 and t.is_visible() and t.is_enabled()]
        if len(trees) != 1:
            raise NativeEngineeringDriverError('原生任务导航树不明确。')
        tree = trees[0]
        tree.set_focus()
        item = self.desktop._task_tree_item(tree, plan['resource'], plan['task'])
        item.ensure_visible()
        self.desktop.ui._select_native_tree_item(tree, item)
        if tree.send_message(0x110A, 9, 0) != item.elem:
            raise NativeEngineeringDriverError('当前选中任务与显式任务计划不同。')
        rect = item.client_rect()
        if rect is None:
            raise NativeEngineeringDriverError('目标任务当前没有可见的原生树节点。')
        # Native right-click updates GX's own task context as well as the
        # TreeView caret. Keyboard WM_CONTEXTMENU can retain another object.
        _post_native_client_click(tree.handle, rect.left + 3,
                                  (rect.top + rect.bottom) // 2, right=True)
        def popup():
            handles = []
            def found(handle, _):
                if (win32gui.IsWindowVisible(handle)
                        and win32process.GetWindowThreadProcessId(handle)[1] == self.desktop.session.process_id
                        and win32gui.GetClassName(handle) == 'XTPPopupBar'):
                    handles.append(handle)
            win32gui.EnumWindows(found, None)
            return handles[0] if len(handles) == 1 else None
        handle = self.desktop._wait(popup, '没有确认所选任务的原生上下文菜单。')
        if win32gui.GetForegroundWindow() not in (handle, self.desktop.session.window_handle):
            raise NativeEngineeringDriverError('原生任务菜单不再拥有前台焦点。')
        from .native_locale import native_accelerator
        send_keys(native_accelerator('task_open_registration',
            getattr(self.desktop, 'native_ui_language', 'zh-CN')), vk_packet=False)
        pattern = '^' + re.escape(self.desktop._text('title_task') + ' ' + plan['task']) + r'\s*$'
        def task_document():
            doc = self.desktop._active_document()
            return doc if re.search(pattern, doc.window_text()) and not popup() else None
        doc = self.desktop._wait(task_document, '原生任务登录页面没有打开。')
        self.desktop.ui._activate_native_document(self.desktop.session, re.compile(pattern))
        grids = [g for g in doc.descendants(class_name='SPR32AU70_SpreadSheet')
                 if g.is_visible() and g.is_enabled()]
        if len(grids) != 1:
            raise NativeEngineeringDriverError('当前任务的程序登录表不明确。')
        grid = grids[0]
        grid.set_focus()
        send_keys('^{HOME}' + ('{DOWN %d}' % (line - 1) if line > 1 else '') + '{F2}', vk_packet=False)
        def cell():
            editors = [e for e in grid.descendants(class_name='SPR32AU70EditHScroll')
                       if e.control_id() == 200 and e.is_visible()]
            return editors[0] if len(editors) == 1 else None
        editor = self.desktop._wait(cell, '任务登录表没有进入明确的程序名称编辑单元格。')
        if editor.window_text():
            raise NativeEngineeringDriverError('任务追加位置已有程序名称，未覆盖。')
        win32gui.SendMessage(editor.handle, 0x00B1, 0, -1)
        win32gui.SendMessage(editor.handle, 0x00C2, 1, plan['source_name'])
        if editor.window_text() != plan['source_name'] or win32gui.SendMessage(editor.handle, 0x00B8, 0, 0) != 1:
            raise NativeEngineeringDriverError('任务单元格没有确认待提交的完整程序名称。')
        editor.set_focus()
        send_keys('{TAB}', vk_packet=False)
        expected = [{**task, 'programs': task['programs'] + ([{'name': plan['source_name'], 'line': line}]
                    if (task['resource'], task['task']) == (plan['resource'], plan['task']) else [])}
                    for task in plan['tasks']]
        if not stage_save:
            # TAB commits the first editor. Re-enter the same actual cell;
            # reading the old Edit HWND would only prove its pending text.
            grid.set_focus()
            send_keys('^{HOME}' + ('{DOWN %d}' % (line - 1) if line > 1 else '') + '{F2}', vk_packet=False)
            committed = self.desktop._wait(cell, '任务登录表没有重新打开已提交的程序单元格。')
            if committed.window_text() != plan['source_name']:
                raise NativeEngineeringDriverError('任务登录单元格提交后实读与追加计划不同。')
            send_keys('{ESC}', vk_packet=False)
            self.desktop.inspect()
            visible = self.desktop._visible(plan['task'] + ' 任务登录', program=plan['source_name'], line=line)
            return {**visible, 'task_append_confirmation': dict(status='matched',
                origin='native_task_cell_committed_readback', resource=plan['resource'], task=plan['task'],
                program=plan['source_name'], line=line, save_performed=False), 'expected_tasks': expected}
        def bound(raw):
            current = native_task_scope_context(raw)
            if current['status'] != 'available' or current['tasks'] != expected:
                raise NativeEngineeringDriverError('保存后的原生任务成员与当前追加计划不同。')
        self.desktop._save_until(bound)
        return self.desktop._visible(plan['task'] + ' 任务登录', program=plan['source_name'], line=line)

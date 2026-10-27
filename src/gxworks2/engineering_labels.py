# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""LabelEditor operations on an explicitly bound native desktop."""
from __future__ import annotations
from contextlib import nullcontext
from pathlib import Path
import tempfile
import time
from .native_locale import standard_yes_no_captions
from .engineering_errors import (NativeEngineeringDriverError)
from .engineering_files import (_label_csv, _merge_label_csv)


class LabelEditor:
    def __init__(self, desktop):
        self.desktop = desktop

    def _export_label_csv(self, name, scope):
        """Observe the current table through native export, without saving it."""
        if scope not in ('global', 'program', 'fb', 'structure'):
            raise NativeEngineeringDriverError('读取标签需要明确的 global、program、fb 或 structure 归属。')
        identity = self.desktop.inspect()
        document = self.desktop._labels_page(name, scope)
        title = document.window_text()
        # GX starts reading after its confirmation dialog closes. Keep transfer
        # files for the entire run, including interrupted native imports.
        self.desktop._transfers += 1
        directory = (self.desktop.evidence_directory / f'labels-{self.desktop._transfers:03d}'
                     if self.desktop.evidence_directory else Path(tempfile.mkdtemp(prefix='gxw-visible-labels-')))
        directory.mkdir(parents=True, exist_ok=self.desktop.evidence_directory is None)
        exported = directory / 'before.csv'
        self.desktop._command(self.desktop.ui.WRITE_CSV_COMMAND)
        self.desktop._confirm('将当前显示的标签信息写入至CSV文件。', 6)
        self.desktop._file_name(self.desktop._dialog('写入至CSV文件'), exported)
        stable = [None, None]
        def ready():
            if not exported.is_file() or self.desktop._dialogs():
                return None
            stat = exported.stat()
            key = stat.st_size, stat.st_mtime_ns
            if key != stable[0]:
                stable[:] = [key, time.monotonic()]
                return None
            if time.monotonic() - stable[1] < .1:
                return None
            if not 0 < stat.st_size <= 8 * 1024 * 1024:
                raise NativeEngineeringDriverError('原生标签 CSV 导出大小超出范围。')
            raw = exported.read_bytes()
            _label_csv(raw)
            return raw
        raw = self.desktop._wait(ready, '原生标签 CSV 导出未完成。')
        self.desktop._verify_read_page(identity, document, title)
        return dict(raw_bytes=raw, identity=identity, document_handle=document.handle,
                    document_title=title, export_file=str(exported))

    def read_labels(self, name, scope, *, type_sources=None):
        """Read the actual current table; CSV export is an explicit operation."""
        from gxw.active_context import read_active_label_grid
        from gxw.models import GXWFormatError
        from .active_document import ActiveDocumentAdapter
        if not hasattr(self.desktop, '_label_reader'):
            self.desktop._label_reader = ActiveDocumentAdapter()
        observed = self.desktop._label_reader.read_label_table(self.desktop, owner=name, scope=scope)
        try:
            return read_active_label_grid(observed['table'], owner=name, scope=scope, identity=observed['identity'],
                document_handle=observed['document_handle'], document_title=observed['document_title'], type_sources=type_sources)
        except GXWFormatError as error:
            raise NativeEngineeringDriverError(str(error)) from error

    def _csv_labels(self, name, scope, rows):
        observed = self.desktop.read_labels(name, scope)
        replacement = _merge_label_csv(observed['raw_bytes'], rows, structure=scope == 'structure',
            native_ui_language=getattr(self.desktop, 'native_ui_language', 'zh-CN'))
        # The only transfer file is the requested import. Reading the current
        # table and checking the result never request a CSV export.
        self.desktop._transfers += 1
        transfer = (nullcontext(self.desktop.evidence_directory / f'labels-{self.desktop._transfers:03d}')
                    if self.desktop.evidence_directory is not None
                    else tempfile.TemporaryDirectory(prefix='gxw-visible-labels-'))
        with transfer as directory:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=self.desktop.evidence_directory is None)
            path = directory / 'change.csv'
            path.write_bytes(replacement)
            self.desktop._command(self.desktop.ui.READ_CSV_COMMAND)
            self.desktop._file_name(self.desktop._dialog('从CSV文件读取'), path)
            self.desktop._confirm('读取指定的文件内容。', 6)
            ready_since = [None]
            def ready():
                if self.desktop._dialogs() or not self.desktop._window().is_enabled():
                    ready_since[0] = None
                    return False
                if ready_since[0] is None:
                    ready_since[0] = time.monotonic()
                return time.monotonic() - ready_since[0] >= .4
            self.desktop._wait(ready, '原生标签导入仍有提示，未确认完整写入。')
            self.desktop._labels_page(name, scope)
            page = '全局标签表' if scope == 'global' else name + (' 结构体成员表' if scope == 'structure' else ' 局部标签表')
            return self.desktop._visible(page, transfer_file=str(path))

    def _delete_label(self, prepared):
        """Delete the one Core-approved cursor row through the native prompt."""
        import win32gui
        import win32process
        from pywinauto.keyboard import send_keys
        if prepared.get('kind') != 'active_label_delete' or prepared.get('scope') != 'program':
            raise NativeEngineeringDriverError('标签行删除需要当前 Program 的独立 Core 计划。')
        plan, before = prepared['plan'], prepared['before']
        observed = self.desktop.read_labels(plan['source_name'], 'program')
        if (observed['csv_columns'] != before['csv_columns'] or
                [row['raw_fields'] for row in observed['declarations']] !=
                [row['raw_fields'] for row in before['declarations']]):
            raise NativeEngineeringDriverError('删除前原生标签表已偏离本次完整基础表，未发出删除。')
        index = plan['csv_row']
        if (type(index) is not int or not 0 <= index < len(observed['declarations'])
                or observed['declarations'][index]['raw_fields'] != plan['before_fields']):
            raise NativeEngineeringDriverError('删除目标与本次 CSV 行身份不同，未发出删除。')
        document = self.desktop._labels_page(plan['source_name'], 'program')
        grids = [grid for grid in document.descendants(class_name='SPR32AU70_SpreadSheet')
                 if grid.is_visible() and grid.is_enabled()]
        if len(grids) != 1:
            raise NativeEngineeringDriverError('标签行删除没有唯一的活动原生表格。')
        grid, = grids
        self.desktop._foreground()
        grid.set_focus()
        send_keys('^{HOME}{RIGHT}' + ('{DOWN ' + str(index) + '}' if index else '') + '{F2}', vk_packet=False)
        editors = [editor for editor in grid.descendants(class_name='SPR32AU70EditHScroll')
                   if editor.control_id() == 200 and editor.is_visible() and editor.is_enabled()]
        if len(editors) != 1 or editors[0].window_text() != plan['name']:
            raise NativeEngineeringDriverError('原生光标没有落在本次指定的标签名称，未发出删除。')
        if (not win32gui.IsChild(document.handle, grid.handle)
                or not win32gui.IsChild(grid.handle, editors[0].handle)
                or win32process.GetWindowThreadProcessId(editors[0].handle)[1] != self.desktop.session.process_id):
            raise NativeEngineeringDriverError('原生名称单元格不属于本次工程页面，未发出删除。')
        self.desktop._command(0x692)  # Native label-table Delete Cursor Row, once.
        def confirmation():
            dialogs = self.desktop._dialogs()
            if not dialogs:
                return None
            if (len(dialogs) != 1 or dialogs[0].window_text() != self.desktop._caption('MELSOFT系列 GX Works2')
                    or self.desktop._dialog_text(dialogs[0]) != self.desktop._caption('删除光标行。确定吗？')):
                raise NativeEngineeringDriverError('本次删除没有出现已核实的光标行确认，未确认其他提示。')
            dialog = dialogs[0]
            buttons = [dialog.child_window(control_id=control_id, class_name='Button') for control_id in (6, 7)]
            if (not all(button.is_enabled() for button in buttons)
                    or not standard_yes_no_captions(*(button.window_text() for button in buttons))):
                raise NativeEngineeringDriverError('原生删除确认按钮与已核实的控件不同。')
            return dialog
        dialog = self.desktop._wait(confirmation, '尚未确认本次原生标签行删除提示。')
        self.desktop._click(dialog, 6)
        self.desktop._wait(lambda: not self.desktop._dialogs() and self.desktop._window().is_enabled(), '本次标签行删除尚未结束。')
        return dict(delete_dispatched=True, confirmation_sent=True, source_name=plan['source_name'],
                    name=plan['name'], save_performed=False)

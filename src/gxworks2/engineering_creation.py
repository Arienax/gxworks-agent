# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Create and initialize a project in the bound native window."""
from __future__ import annotations
import time
from .project_profiles import native_project_profile
from .engineering_errors import NativeEngineeringDriverError

class ProjectCreator:
    def __init__(self, desktop):
        self.desktop = desktop

    def _create_project(self, *, language='fbd'):
        self.desktop._command(self.desktop.ui.NEW_PROJECT_COMMAND, project=False)
        dialog = self.desktop._dialog('新建')
        profile = native_project_profile(self.desktop.cpu)
        series = dialog.child_window(control_id=10002, class_name='ComboBox')
        expected_series = self.desktop._text('series_q') if profile.series[0].startswith('QCPU') else profile.series[0]
        choices = [s for s in series.item_texts() if s == expected_series]
        if len(choices) != 1:
            raise NativeEngineeringDriverError('未确认新建工程的 CPU 系列。')
        self.desktop.ui._select_native_combo(series, choices[0])
        self.desktop.ui._select_native_combo(dialog.child_window(control_id=10003, class_name='ComboBox'), self.desktop.cpu)
        kind = dialog.child_window(control_id=10001, class_name='ComboBox')
        if kind.is_enabled():
            self.desktop.ui._select_native_combo(kind, self.desktop._text('project_structured'))
        elif kind.selected_text() != self.desktop._text('project_structured'):
            raise NativeEngineeringDriverError('此 CPU 当前没有已验证的结构化工程页面。')
        self.desktop.ui._select_native_combo(dialog.child_window(control_id=10070, class_name='ComboBox'),
                                     'ST' if language == 'st' else self.desktop._text('language_fbd'))
        self.desktop._click(dialog, 1)
        creation_dialog_handle = dialog.handle
        created_ready_since = [None]
        def created():
            dialogs = self.desktop._dialogs()
            if any(current.handle == creation_dialog_handle for current in dialogs):
                created_ready_since[0] = None
                return None  # The posted native New handler is still returning.
            if dialogs:
                created_ready_since[0] = None
                self.desktop.ui._acknowledge_known_save_notices(self.desktop.session,
                    native_ui_language=getattr(self.desktop, 'native_ui_language', 'zh-CN'))
                return None
            if 'POU_01' not in self.desktop._window().window_text() or not self.desktop._window().is_enabled():
                created_ready_since[0] = None
                return None
            if created_ready_since[0] is None:
                created_ready_since[0] = time.monotonic()
            return time.monotonic() - created_ready_since[0] >= .4
        self.desktop._wait(created, '未确认原生空白 FBD 工程。')
        self.desktop._command(self.desktop.ui.SAVE_PROJECT_COMMAND, project=False)
        dialog = self.desktop._dialog('工程另存为')
        if self.desktop.path.exists():
            raise NativeEngineeringDriverError('新工程目标文件已出现，未覆盖。')
        self.desktop._file_name(dialog, self.desktop.path)
        save_dialog_handle = dialog.handle
        def initially_saved():
            dialogs = self.desktop._dialogs()
            if any(current.handle == save_dialog_handle for current in dialogs):
                return None  # File acceptance was dispatched once; never replay it.
            if dialogs:
                self.desktop.ui._acknowledge_known_save_notices(self.desktop.session,
                    native_ui_language=getattr(self.desktop, 'native_ui_language', 'zh-CN'))
                return None
            return self.desktop.path.is_file() and self.desktop.path.name.casefold() in self.desktop._window().window_text().casefold()
        self.desktop._wait(initially_saved, '未确认新工程已经保存。')
        self.desktop.inspect()
        self.desktop._last_saved = self.desktop.path.read_bytes()
        if language == 'fbd':
            self.desktop._prepare_empty_fbd()
        self.desktop._initialize_new_allocation()
        self.desktop._visible('工程导航树', cpu=self.desktop.cpu, path=str(self.desktop.path))

    def _initialize_new_allocation(self):
        """Materialize a new GUI project's own defaults before its baseline."""
        if self.desktop.cpu != 'FX3U/FX3UC':
            return
        from gxw.project_metadata import project_gui_configuration
        from gxw.visible_engineering import source_projection
        initial = self.desktop.snapshot()
        if project_gui_configuration(initial)['status'] == 'matched':
            return
        before = source_projection(initial)
        self.desktop._configure({'ranges': []})
        def initialized(raw):
            if source_projection(raw) != before:
                raise NativeEngineeringDriverError('原生新工程初始化改变了既有源码或分配范围。')
            if project_gui_configuration(raw)['status'] != 'matched':
                raise NativeEngineeringDriverError('原生新工程的分配设置尚未完成初始化。')
        self.desktop._save_until(initialized)

    def _prepare_empty_fbd(self):
        """Remove only the original empty program's native left rail."""
        from gxw.object_model import read_project_context
        self.desktop._program_page('POU_01')
        raw = self.desktop.snapshot()
        model = read_project_context(raw, 'POU_01.Program.pou').object_model()
        if model['nodes'] or model['unknown_record_count']:
            raise NativeEngineeringDriverError('原生新建工程不是可确认的空白程序。')
        if not model['wires']:
            return
        if len(model['wires']) != 1 or model['wires'][0]['start'] != [1, 0]:
            raise NativeEngineeringDriverError('原生空白程序的母线形式尚未验证。')
        self.desktop._command(0x6AC)
        def no_rail(raw):
            observed = read_project_context(raw, 'POU_01.Program.pou').object_model()
            if observed['nodes'] or observed['wires'] or observed['unknown_record_count']:
                raise NativeEngineeringDriverError('空白 FBD 页面尚未回读为无母线形式。')
        raw = self.desktop._save_until(no_rail)
        actual = read_project_context(raw, 'POU_01.Program.pou').object_model()
        if actual['nodes'] or actual['wires'] or actual['unknown_record_count']:
            raise NativeEngineeringDriverError('没有确认空白程序已经采用无左母线的 FBD 形式。')

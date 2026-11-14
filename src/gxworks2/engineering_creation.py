# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Create and initialize a project in the bound native window."""
from __future__ import annotations
import time
from .project_profiles import native_project_profile
from .engineering_errors import NativeEngineeringDriverError

class ProjectCreator:
    """Native New Project creation for the verified simple and structured kinds."""

    # language -> (structured kind, UI text key, default program name)
    LANGUAGES = {
        'ladder': (False, 'language_ladder', 'MAIN'),
        'sfc': (False, 'language_sfc', 'MAIN'),
        'st': (True, 'st', 'POU_01'),
        'fbd': (True, 'language_fbd', 'POU_01'),
    }

    def __init__(self, desktop):
        self.desktop = desktop

    def _create_project(self, *, language='fbd'):
        structured, language_key, program = self.LANGUAGES.get(language, (None, None, None))
        if structured is None:
            raise NativeEngineeringDriverError('未覆盖的新建工程语言：' + str(language))
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
        kind_text = self.desktop._text('project_structured' if structured else 'project_simple')
        if kind.is_enabled():
            self.desktop.ui._select_native_combo(kind, kind_text)
        elif kind.selected_text() != kind_text:
            raise NativeEngineeringDriverError('此 CPU 当前没有已验证的工程页面类型。')
        labels = dialog.child_window(control_id=10068, class_name='Button')
        if labels.is_enabled() and labels.get_check_state() != int(structured):
            labels.click()
        if labels.get_check_state() != int(structured):
            raise NativeEngineeringDriverError('未确认新工程的标签选项。')
        self.desktop.ui._select_native_combo(dialog.child_window(control_id=10070, class_name='ComboBox'),
                                     'ST' if language == 'st' else self.desktop._text(language_key))
        self.desktop._click(dialog, 1)
        block_settings = []
        creation_dialog_handle = dialog.handle
        created_ready_since = [None]
        def created():
            dialogs = self.desktop._dialogs()
            if any(current.handle == creation_dialog_handle for current in dialogs):
                created_ready_since[0] = None
                return None  # The posted native New handler is still returning.
            if dialogs:
                created_ready_since[0] = None
                if language == 'sfc' and self._confirm_sfc_block_settings(block_settings):
                    return None
                self.desktop.ui._acknowledge_known_save_notices(self.desktop.session,
                    native_ui_language=getattr(self.desktop, 'native_ui_language', 'zh-CN'))
                return None
            title = self.desktop._window().window_text()
            confirmed = program in title if structured else self.desktop._text('project_not_set') in title
            if not confirmed or not self.desktop._window().is_enabled():
                created_ready_since[0] = None
                return None
            if created_ready_since[0] is None:
                created_ready_since[0] = time.monotonic()
            return time.monotonic() - created_ready_since[0] >= .4
        self.desktop._wait(created, '未确认原生空白工程。')
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
                if language == 'sfc' and self._confirm_sfc_block_settings(block_settings):
                    return None
                self.desktop.ui._acknowledge_known_save_notices(self.desktop.session,
                    native_ui_language=getattr(self.desktop, 'native_ui_language', 'zh-CN'))
                return None
            return self.desktop.path.is_file() and self.desktop.path.name.casefold() in self.desktop._window().window_text().casefold()
        self.desktop._wait(initially_saved, '未确认新工程已经保存。')
        self.desktop.inspect()
        self.desktop._last_saved = self.desktop.path.read_bytes()
        if language == 'fbd':
            self.desktop._prepare_empty_fbd()
        if structured and getattr(self.desktop, '_initialize_creation_allocation', True):
            self.desktop._initialize_new_allocation()
        self.desktop._visible('工程导航树', cpu=self.desktop.cpu, path=str(self.desktop.path))

    def _confirm_sfc_block_settings(self, collected):
        """Confirm the SFC block registration dialog using its own observed fields.

        The dialog belongs to SFC project initialization, not to a generic save
        notice: it declares the block's data name, title, number and type. Only
        the observed control layout is accepted, and its current values are
        checked after applying the requested values.
        """
        for window in self.desktop._dialogs():
            try:
                name = window.child_window(control_id=25927, class_name='Edit')
                kind = window.child_window(control_id=25926, class_name='ComboBox')
                number = window.child_window(control_id=25939, class_name='Edit')
                title = window.child_window(control_id=25929, class_name='Edit')
                execute = window.child_window(control_id=1, class_name='Button')
            except Exception:
                continue
            observed = {'dialog': window.window_text(), 'data_name': name.window_text(),
                        'title': title.window_text(), 'block_number': number.window_text(),
                        'block_type': kind.selected_text(), 'block_types': list(kind.item_texts())}
            requested = getattr(self.desktop, '_creation_block', {'data_name': 'Block', 'title': '', 'number': 0})
            name.set_edit_text(requested.get('data_name', 'Block'))
            title.set_edit_text(requested.get('title', ''))
            number.set_edit_text(str(requested.get('number', 0)))
            sfc_types = [item for item in kind.item_texts() if item.upper().startswith('SFC')]
            if len(sfc_types) != 1:
                raise NativeEngineeringDriverError('未确认 SFC 块类型。')
            kind.select(sfc_types[0])
            if (name.window_text() != requested.get('data_name', 'Block')
                    or title.window_text() != requested.get('title', '')
                    or number.window_text() != str(requested.get('number', 0))
                    or kind.selected_text() != sfc_types[0]):
                raise NativeEngineeringDriverError('SFC 块信息写入后不一致。')
            observed['requested'] = dict(requested)
            collected.append(observed)
            execute.click_input()
            return observed
        return None

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

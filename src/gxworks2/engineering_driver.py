"""Process-bound engineering session and operation dispatch.

Graph, labels, allocation, compilation and assignment inspectors own their native page workflows.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import threading
import time
from .finder import GXWorks2Finder
from .project_profiles import GXWORKS2_NATIVE_VERSION, native_project_profile
from .native_locale import inspect_native_language, native_caption, native_tree_path, NativeLanguageNotReadyError
from .ui_automation import PywinautoGXWorks2UIAutomation
from gxw.native_locale import label_title, program_title, ui_text
from .engineering_errors import (NativeEngineeringDriverError)
from .engineering_files import (_same_text, _NativeFileUse, _abbreviated_project_path)
from .engineering_observations import (_post_native_client_click)
from .engineering_graph import GraphEditor
from .engineering_labels import LabelEditor
from .engineering_allocation import AllocationEditor
from .engineering_compile import CompileInspector
from .engineering_assignments import AssignmentInspector
from .engineering_files import (_same_text, _NativeFileUse, _abbreviated_project_path, _label_csv, _merge_label_csv)
from .engineering_grid import (_grid_calibration)
from .engineering_observations import (_label_display_type, _post_native_client_click, _accessible_grid_rows, _allocation_accessible, _allocation_grid_rows, _native_focus, _allocation_cell, _allocation_cell_point, _compile_diagnostic_rows, _accessible_compile_diagnostic_rows, _compile_summary_counts)


from .engineering_window import EngineeringWindow
from .engineering_programs import ProgramEditor
from .engineering_creation import ProjectCreator
from .engineering_archive import ProjectArchive


class NativeEngineeringDriver:
    """One frame, one saved project, one owning thread; no implicit adoption."""

    VERSION = '.'.join(map(str, GXWORKS2_NATIVE_VERSION))

    def __init__(self, session, path, cpu, *, timeout=30, progress=None,
                 evidence_directory=None, finder=None, automation=None, owned=False):
        self.finder = finder or GXWorks2Finder()
        self.ui = automation or PywinautoGXWorks2UIAutomation(timeout=timeout)
        self.session = session
        self.path = Path(path).expanduser().resolve()
        profile = native_project_profile(cpu)
        if profile is None or not profile.program_supported:
            raise NativeEngineeringDriverError('可见工程会话需要明确的可编程 CPU。')
        self.cpu = profile.cpu
        self.timeout = float(timeout)
        self.progress = progress
        self.evidence_directory = Path(evidence_directory).resolve() if evidence_directory else None
        if self.evidence_directory:
            self.evidence_directory.mkdir(parents=True, exist_ok=True)
        self.owned = owned
        self._thread = threading.get_ident()
        self._binding = tuple(getattr(session, key) for key in (
            'process_id', 'window_handle', 'process_started', 'executable', 'software_version'))
        self._last_saved = self.path.read_bytes() if self.path.is_file() else None
        self._captures = 0
        self._transfers = 0
        self._closed = False
        self._file_use = None
        if not all(self._binding) or session.software_version != self.VERSION:
            raise NativeEngineeringDriverError('无法确认 GX Works2 进程启动身份和已验证版本。')
        try:
            self.native_ui_language = inspect_native_language(session.process_id, session.executable)
        except NativeLanguageNotReadyError:
            def language_ready():
                try:
                    return inspect_native_language(self.session.process_id, self.session.executable)
                except NativeLanguageNotReadyError:
                    return None
            # _wait rechecks PID/start/window/version on every observation.
            # Mixed/unknown resources still fail immediately; no IDE command
            # is sent while the new frame's satellites are still loading.
            self.native_ui_language = self._wait(language_ready, '原生界面语言资源尚未完成加载。')
        self._codepage_observation = None

    def _text(self, key):
        return ui_text(key, getattr(self, 'native_ui_language', 'zh-CN'))

    def _caption(self, text):
        return native_caption(text, getattr(self, 'native_ui_language', 'zh-CN'))

    def _tree_path(self, path):
        return native_tree_path(path, getattr(self, 'native_ui_language', 'zh-CN'))

    def _program_title(self, owner, kind='program', language='fbd'):
        return program_title(owner, kind, language,
                             language=getattr(self, 'native_ui_language', 'zh-CN'))

    @classmethod
    def _bind_started_frame(cls, session, path, cpu, **options):
        """Retain the exact created frame even if pre-command binding fails."""
        try:
            driver = cls(session, path, cpu, owned=True, **options)
        except Exception as error:
            from copy import deepcopy
            observation = deepcopy(getattr(error, 'observation', {}))
            observation.update(startup_session={key: getattr(session, key) for key in (
                'process_id', 'window_handle', 'process_started', 'executable', 'software_version')},
                requested_project=str(path), native_command_dispatched=False)
            raise NativeEngineeringDriverError(str(error), observation=observation) from error
        try:
            if driver._frame_identity().project_open:
                raise NativeEngineeringDriverError('新建的 GX Works2 窗口在绑定期间打开了另一个工程。')
        except Exception as error:
            bound = NativeEngineeringDriverError(str(error), observation={
                **getattr(error, 'observation', {}), 'native_command_dispatched': False})
            raise driver._startup_error(bound) from error
        return driver

    @classmethod
    def start_new(cls, path, cpu, *, timeout=30, progress=None, evidence_directory=None, language='fbd'):
        if os.name != 'nt':
            raise NativeEngineeringDriverError('可见工程会话需要 Windows 交互桌面。')
        if language not in ('fbd', 'st'):
            raise NativeEngineeringDriverError('新建可见结构化工程的语言必须是 ST 或 FBD。')
        path = Path(path).expanduser().resolve()
        if (path.exists() or path.suffix.lower() != '.gxw' or not path.parent.is_dir()
                or not 1 <= len(path.stem) <= 30):
            raise NativeEngineeringDriverError('新会话需要独立、尚不存在的 GXW 文件路径。')
        finder = GXWorks2Finder()
        if finder.start() is None:
            raise NativeEngineeringDriverError('未找到本机 GX Works2。')
        deadline = time.monotonic() + timeout
        session = None
        while time.monotonic() < deadline:
            session = finder.find_running(process_id=finder.started_process_id)
            if session:
                break
            time.sleep(.1)
        if session is None or session.project_open:
            raise NativeEngineeringDriverError('新建 GX Works2 进程没有明确的空白窗口。')
        driver = cls._bind_started_frame(session, path, cpu, timeout=timeout, progress=progress,
                                        evidence_directory=evidence_directory, finder=finder)
        import win32gui
        try:
            win32gui.ShowWindow(session.window_handle, 3)
            driver._create_project(language=language)
        except Exception as error:
            raise driver._startup_error(error) from error
        return driver

    @classmethod
    def start_from_saved(cls, path, cpu, *, timeout=30, progress=None, evidence_directory=None,
                         accept_library_update=False):
        """Open the caller's explicitly managed copy in a new native process."""
        if type(accept_library_update) is not bool:
            raise NativeEngineeringDriverError('库更新选择必须是明确布尔值。')
        if os.name != 'nt':
            raise NativeEngineeringDriverError('可见工程会话需要 Windows 交互桌面。')
        path = Path(path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != '.gxw':
            raise NativeEngineeringDriverError('会话需要已保存的独立 GXW 工程副本。')
        from gxw.visible_engineering import source_projection
        projection = source_projection(path.read_bytes())
        actual, expected = native_project_profile(projection['cpu']), native_project_profile(cpu)
        if actual is None or expected is None or actual.cpu != expected.cpu:
            raise NativeEngineeringDriverError('保存的工程 CPU 与本次可见会话不同。')
        finder = GXWorks2Finder()
        if finder.start() is None:
            raise NativeEngineeringDriverError('未找到本机 GX Works2。')
        deadline = time.monotonic() + timeout
        session = None
        while time.monotonic() < deadline:
            session = finder.find_running(process_id=finder.started_process_id)
            if session:
                break
            time.sleep(.1)
        if session is None or session.project_open:
            raise NativeEngineeringDriverError('新建 GX Works2 进程没有明确的空白窗口。')
        driver = cls._bind_started_frame(session, path, cpu, timeout=timeout, progress=progress,
                                        evidence_directory=evidence_directory, finder=finder)
        driver.open_updates = []
        import win32gui
        try:
            win32gui.ShowWindow(session.window_handle, 3)
            driver._command(0x400, project=False)
            driver._file_name(driver._dialog('打开工程'), path)
            def opened():
                dialogs = driver._dialogs()
                active = [dialog for dialog in dialogs if dialog.is_enabled()]
                if accept_library_update and len(active) == 1:
                    update = driver._accept_library_update(active[0])
                    if update is not None:
                        return False
                return (path.name.casefold() in driver._window().window_text().casefold()
                        and not dialogs and driver._window().is_enabled())
            driver._wait(opened, '托管工程副本没有在新进程中打开。')
            driver.inspect()
            driver._visible('工程导航树', path=str(path), opened_managed_copy=True)
        except Exception as error:
            raise driver._startup_error(error) from error
        return driver

    def _accept_library_update(self, dialog):
        """Select available library updates on the owned opening dialog only.

        The Common manual, section 4.2.2, shows two library checkboxes and an
        OK/Cancel pair. IDs are read from the actual controls, not guessed.
        Localized title/option words must agree with that page's structure.
        """
        title = dialog.window_text().strip()
        patterns = {
            'en': r'Library Update Confirmation',
            'zh-CN': r'(?:库(?:文件)?(?:的)?)?更新确认',
            'ja': r'(?:ライブラリ)?更新確認',
        }
        if not re.fullmatch(patterns.get(self.native_ui_language, r'(?!)'), title):
            return None
        if 'gxworks2' not in re.sub(r'\s+', '', self._dialog_text(dialog)).casefold():
            return None
        def caption(control):
            return re.sub(r'\(&[A-Za-z]\)', '', control.window_text()).replace('&', '').strip()
        buttons = [button for button in dialog.descendants(class_name='Button') if button.is_visible()]
        checks = [button for button in buttons if button.style() & 0xF in (2, 3)]
        push = [button for button in buttons if button.style() & 0xF in (0, 1)]
        names = {
            'en': ('instructions', 'Application functions'),
            'zh-CN': ('指令', '应用函数'),
            'ja': ('命令', '応用関数'),
        }
        instruction_word, functions = names[self.native_ui_language]
        instructions = [button for button in checks if 'PID' in caption(button)
                        and instruction_word in caption(button)]
        applications = [button for button in checks if caption(button) == functions]
        ok = [button for button in push if caption(button) == self._text('ok')]
        cancel = [button for button in push if caption(button) == self._text('cancel')]
        if (len(checks) != 2 or len(push) != 2 or len(instructions) != 1
                or len(applications) != 1 or len(ok) != 1 or len(cancel) != 1
                or len({button.control_id() for button in checks + push}) != 4
                or not dialog.is_enabled()):
            return None
        states = [(checkbox, checkbox.get_check_state(), checkbox.is_enabled()) for checkbox in checks]
        if any(state not in (0, 1) for _, state, _ in states):
            raise NativeEngineeringDriverError('库更新选项状态不可确认。')
        if inspect_native_language(self.session.process_id, self.session.executable) != self.native_ui_language:
            raise NativeEngineeringDriverError('原生界面语言已经改变，需要重新绑定工程会话。')
        options = []
        update = {'title': title, 'options': options, 'confirmation_dispatched': False, 'confirmed': False}
        self.open_updates.append(update)
        for checkbox, state, enabled in states:
            option = {'caption': caption(checkbox), 'control_id': checkbox.control_id(),
                      'enabled': enabled, 'selected': bool(state)}
            options.append(option)
            if enabled and state == 0:
                self._click(dialog, checkbox.control_id())
                option['selection_dispatched'] = True
                self._wait(lambda: checkbox.get_check_state() == 1, '库更新选项没有选中。')
            option['selected'] = bool(checkbox.get_check_state())
        handle = dialog.handle
        self._wait(lambda: dialog.is_enabled() and ok[0].is_enabled(), '库更新确认按钮尚未可用。')
        self._click(dialog, ok[0].control_id())
        update['confirmation_dispatched'] = True
        self._wait(lambda: not any(row.handle == handle for row in self._dialogs()),
                   '库更新确认尚未完成。')
        update['confirmed'] = True
        return update

    def _startup_error(self, error):
        from copy import deepcopy
        observation = deepcopy(getattr(error, 'observation', {}))
        observation['startup_session'] = dict(zip((
            'process_id', 'window_handle', 'process_started', 'executable', 'software_version'), self._binding))
        observation['requested_project'] = str(self.path)
        observation['library_updates'] = getattr(self, 'open_updates', [])
        try:
            current = self._frame_identity()
            observation['project_opened'] = current.project_open
            if self.evidence_directory:
                path = self.evidence_directory / 'startup-failure.png'
                self._window().capture_as_image().save(str(path))
                observation['image'] = str(path)
        except Exception as capture_error:
            observation['startup_observation_error'] = str(capture_error)
        finally:
            self.close()
        return NativeEngineeringDriverError(str(error), observation=observation)

    @classmethod
    def attach(cls, process_id, path, cpu, **options):
        finder = GXWorks2Finder()
        session = finder.find_running(process_id=process_id)
        if session is None:
            raise NativeEngineeringDriverError('明确指定的 GX Works2 进程不存在。')
        driver = cls(session, path, cpu, finder=finder, **options)
        driver.inspect()
        return driver

    def _frame_identity(self):
        if self._closed or threading.get_ident() != self._thread:
            raise NativeEngineeringDriverError('原生会话已关闭或切换了执行线程。')
        session = self.finder.find_running(process_id=self.session.process_id)
        actual = tuple(getattr(session, key, None) for key in (
            'process_id', 'window_handle', 'process_started', 'executable', 'software_version'))
        if actual != self._binding:
            raise NativeEngineeringDriverError('GX Works2 进程、窗口或软件版本已改变。')
        self.session = session
        return session

    def inspect(self):
        session = self._frame_identity()
        actual_language = inspect_native_language(session.process_id, session.executable)
        if actual_language != self.native_ui_language:
            raise NativeEngineeringDriverError('原生界面语言已经改变，需要重新绑定工程会话。')
        try:
            actual_path = Path(session.project_name).resolve()
        except (OSError, ValueError):
            actual_path = None
        matched = actual_path == self.path
        binding = 'native_full_caption'
        if not matched and _abbreviated_project_path(session.project_name, self.path):
            if self._file_use is None:
                self._file_use = _NativeFileUse(self.path)
            matched = self._file_use.owns(session.process_id, session.process_started)
            binding = 'native_file_owner_and_abbreviated_caption'
        if not session.project_open or not matched:
            raise NativeEngineeringDriverError('活动工程已经改变，未继续写入。')
        profile = self.ui._current_native_project_profile(session)
        if profile is None or profile.cpu != self.cpu:
            raise NativeEngineeringDriverError('原生状态栏 CPU 与会话目标不一致。')
        stat = self.path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        cached = self._codepage_observation
        if cached is None or cached[0] != key:
            from gxw.offline_project import read_offline_project
            codepage = read_offline_project(self.path.read_bytes()).context['codepage']
            self._codepage_observation = (key, codepage)
        project_language = self.ui._current_native_project_codepage_observation(session, actual_language)
        if self._codepage_observation[1] not in project_language['codepage_candidates']:
            raise NativeEngineeringDriverError('当前原生项目语言与已绑定的保存工程不同，需要重新绑定。',
                observation={'project_language': project_language,
                             'saved_project_codepage': self._codepage_observation[1]})
        return {**{key: getattr(session, key) for key in (
            'process_id', 'window_handle', 'process_started', 'executable', 'software_version')},
            'project_path': str(self.path), 'cpu_menu': profile.cpu, 'path_binding': binding,
            'native_ui_language': actual_language, 'project_codepage': self._codepage_observation[1],
            'project_language_observation': project_language}

    def _window(self):
        return EngineeringWindow(self)._window()

    def _foreground(self, handle=None):
        return EngineeringWindow(self)._foreground(handle)

    def _dialogs(self):
        return EngineeringWindow(self)._dialogs()

    @staticmethod
    def _dialog_text(dialog):
        return EngineeringWindow._dialog_text(dialog)

    def _idle(self):
        return EngineeringWindow(self)._idle()

    def verify_project_codepage(self):
        """Re-read the current Chinese GB option at an operation boundary.

        The language pane deliberately represents both 936 and 54936. A saved
        file or a previously read option cannot detect an unsaved switch.
        Opening Options is read-only here: only selection, BM_GETCHECK and
        Cancel are used; no OK, checkbox toggle or user-default action is sent.
        """
        self._idle()
        before = self.inspect()
        candidates = before['project_language_observation']['codepage_candidates']
        if len(candidates) == 1:
            return dict(status='matched', project_codepage=candidates[0],
                        origin='current_native_project_language_status')
        if set(candidates) != {936, 54936}:
            raise NativeEngineeringDriverError('当前原生项目编码不明确，未继续操作。')
        self._command(0x467)
        title = self._text('dialog_options') + ' - ' + self.path.stem
        def options():
            dialogs = [d for d in self._dialogs() if d.window_text() == title and d.is_enabled()]
            return dialogs[0] if len(dialogs) == 1 else None
        dialog = self._wait(options, '没有确认当前工程的原生选项窗口；未重复发送。')
        observation = None
        try:
            self._foreground(dialog.handle)
            trees = [tree for tree in dialog.descendants(class_name='SysTreeView32')
                     if tree.control_id() == 11000 and tree.is_visible() and tree.is_enabled()]
            if len(trees) != 1:
                raise NativeEngineeringDriverError('当前工程选项导航树不唯一。')
            tree, = trees
            path = [self._text('options_project'), self._text('options_common_setting')]
            tree.get_item(path[:1], exact=True).expand()
            tree.get_item(path, exact=True).select()
            def checkbox():
                controls = [control for control in dialog.descendants(class_name='Button')
                            if control.control_id() == 15317 and control.is_visible()]
                return controls[0] if len(controls) == 1 else None
            control = self._wait(checkbox, '当前工程的 GB18030 编码选项不可确认。')
            if (not control.is_enabled() or control.window_text() != self._text('options_gb18030')
                    or control.style() & 0xF != 3):  # BS_AUTOCHECKBOX
                raise NativeEngineeringDriverError('当前工程的 GB18030 复选框身份不一致。')
            state = int(control.send_message(0x00F0, 0, 0))  # BM_GETCHECK only
            if state not in (0, 1):
                raise NativeEngineeringDriverError('当前工程的 GB18030 复选状态不明确。')
            observation = dict(project_codepage=54936 if state else 936,
                origin='current_native_project_options_BM_GETCHECK',
                dialog_handle=dialog.handle, control_handle=control.handle,
                control_id=15317, checked=state, raw_text=control.window_text())
        finally:
            self._click(dialog, 2)
            self._wait(lambda: not any(d.handle == dialog.handle for d in self._dialogs()),
                       '原生选项取消尚未结束；未重复发送。')
        after = self.inspect()
        fields = (*('process_id', 'window_handle', 'process_started', 'executable', 'software_version'),
                  'project_path', 'cpu_menu', 'native_ui_language', 'project_codepage')
        if any(before[key] != after[key] for key in fields):
            raise NativeEngineeringDriverError('读取编码选项期间工程身份改变，需要重新绑定。')
        if observation['project_codepage'] != before['project_codepage']:
            raise NativeEngineeringDriverError('当前原生项目编码与已绑定的保存工程不同，需要重新绑定。',
                observation={**observation, 'saved_project_codepage': before['project_codepage']})
        return dict(status='matched', **observation)

    def _command(self, command, *, allow_dialog=False, project=True):
        return EngineeringWindow(self)._command(command, allow_dialog=allow_dialog, project=project)

    def _wait(self, predicate, message, *, timeout=None):
        return EngineeringWindow(self)._wait(predicate, message, timeout=timeout)

    def _dialog(self, title):
        return EngineeringWindow(self)._dialog(title)

    def _click(self, dialog, control_id):
        return EngineeringWindow(self)._click(dialog, control_id)

    def _confirm(self, fragment, control_id):
        return EngineeringWindow(self)._confirm(fragment, control_id)

    def _file_name(self, dialog, path):
        return EngineeringWindow(self)._file_name(dialog, path)

    def _visible(self, page, *, dialog=None, **details):
        return EngineeringWindow(self)._visible(page, dialog=dialog, **details)

    def _create_project(self, *, language='fbd'):
        return ProjectCreator(self)._create_project(language=language)

    def _initialize_new_allocation(self):
        return ProjectCreator(self)._initialize_new_allocation()

    def _prepare_empty_fbd(self):
        return ProjectCreator(self)._prepare_empty_fbd()

    def _active_document(self):
        return EngineeringWindow(self)._active_document()

    def _clear_fb_instance_view(self, document, owner, expected_title):
        return EngineeringWindow(self)._clear_fb_instance_view(document, owner, expected_title)

    def _navigate(self, paths, title_pattern):
        return EngineeringWindow(self)._navigate(paths, title_pattern)

    def _labels_page(self, name, scope):
        return EngineeringWindow(self)._labels_page(name, scope)

    def _program_page(self, name, kind='program', language='fbd'):
        return EngineeringWindow(self)._program_page(name, kind, language)

    def focus_graph_network(self, adapter, owner, kind, network):
        return GraphEditor(self).focus_graph_network(adapter, owner, kind, network)

    def snapshot(self, *, on_dispatch=None):
        return ProjectArchive(self).snapshot(on_dispatch=on_dispatch)

    def read_saved_project(self, *, acknowledge_notices=False):
        return ProjectArchive(self).read_saved_project(acknowledge_notices=acknowledge_notices)

    def _save_until(self, verify):
        return ProjectArchive(self)._save_until(verify)

    def verify_saved_source(self, raw):
        return ProjectArchive(self).verify_saved_source(raw)

    def _export_label_csv(self, name, scope):
        return LabelEditor(self)._export_label_csv(name, scope)

    def _verify_read_page(self, identity, document, title):
        self._idle()
        current = self._active_document()
        if (self.inspect() != identity or current.handle != document.handle
                or current.window_text() != title):
            raise NativeEngineeringDriverError('原生读取期间工程或活动页面已改变。')

    def read_labels(self, name, scope, *, type_sources=None):
        return LabelEditor(self).read_labels(name, scope, type_sources=type_sources)

    def _csv_labels(self, name, scope, rows):
        return LabelEditor(self)._csv_labels(name, scope, rows)

    def _delete_label(self, prepared):
        return LabelEditor(self)._delete_label(prepared)

    def _new_object(self, name, kind, language=None):
        return ProgramEditor(self)._new_object(name, kind, language)

    def _complete_new_object(self, dialog, name, kind, language=None):
        return ProgramEditor(self)._complete_new_object(dialog, name, kind, language)

    def read_st(self, name, kind='program'):
        return ProgramEditor(self).read_st(name, kind)

    def _write_st(self, name, text, *, before=None, kind='fb'):
        return ProgramEditor(self)._write_st(name, text, before=before, kind=kind)

    def _task_tree_item(self, tree, resource, task):
        return ProgramEditor(self)._task_tree_item(tree, resource, task)

    def _bind_program_task(self, plan, *, stage_save=True):
        return ProgramEditor(self)._bind_program_task(plan, stage_save=stage_save)

    def _choose_label(self, name, *, expected_type=None):
        return GraphEditor(self)._choose_label(name, expected_type=expected_type)

    def _graph_view(self, name):
        return GraphEditor(self)._graph_view(name)

    def _graph_grid(self, view, *, include_lattice=False):
        return GraphEditor(self)._graph_grid(view, include_lattice=include_lattice)

    def _graph_placement_point(self, view, grid, x, y):
        return GraphEditor(self)._graph_placement_point(view, grid, x, y)

    @staticmethod
    def _graph_point(grid, x, y):
        point = (round(grid['x'] + x * grid['pitch']), round(grid['y'] + y * grid['pitch']))
        if not 5 < point[0] < grid['width'] - 28 or not 5 < point[1] < grid['height'] - 28:
            raise NativeEngineeringDriverError('目标图形对象不在当前可见区域。')
        if 'calibrated_x_limit' in grid and point[0] >= grid['calibrated_x_limit']:
            raise NativeEngineeringDriverError('目标超出当前已校准的网格区域，需要重新定位视口。')
        return point

    def _graph_drag(self, view, grid, source, target):
        return GraphEditor(self)._graph_drag(view, grid, source, target)

    def _graph_context(self, view, grid, box):
        return GraphEditor(self)._graph_context(view, grid, box)

    def _select_graph_node(self, view, grid, box):
        return GraphEditor(self)._select_graph_node(view, grid, box)

    def _draw_graph_wire(self, view, grid, anchor):
        return GraphEditor(self)._draw_graph_wire(view, grid, anchor)

    def _edit_fbd_graph(self, command, prepared):
        return GraphEditor(self)._edit_fbd_graph(command, prepared)

    def _create_fbd_call(self, command, plan):
        return GraphEditor(self)._create_fbd_call(command, plan)

    def _edit_allocation_grid_field(self, dialog, field, value):
        return AllocationEditor(self)._edit_allocation_grid_field(dialog, field, value)

    def _configuration_fields(self, dialog):
        return AllocationEditor(self)._configuration_fields(dialog)

    def read_configuration(self):
        return AllocationEditor(self).read_configuration()

    def _finish_allocation_notices(self):
        return AllocationEditor(self)._finish_allocation_notices()

    def _configure(self, plan):
        return AllocationEditor(self)._configure(plan)

    def compile_current(self, plan):
        return CompileInspector(self).compile_current(plan)

    def _read_compile_diagnostics(self, summary, run):
        return CompileInspector(self)._read_compile_diagnostics(summary, run)

    def _completed_compile_receipt(self, run, summary, output, *, driver_saved=False):
        return CompileInspector(self)._completed_compile_receipt(run, summary, output, driver_saved=driver_saved)

    def _compile(self, plan, *, legacy_save=True):
        return CompileInspector(self)._compile(plan, legacy_save=legacy_save)

    def read_current_compile_result(self, *, revision):
        return CompileInspector(self).read_current_compile_result(revision=revision)

    def _owned_control_handles(self, class_name, control_id, *, visible=True):
        return EngineeringWindow(self)._owned_control_handles(class_name, control_id, visible=visible)

    def _cross_reference_panel(self, *, allow_missing=False):
        return AssignmentInspector(self)._cross_reference_panel(allow_missing=allow_missing)

    def _open_cross_reference(self):
        return AssignmentInspector(self)._open_cross_reference()

    def _choose_native_menu(self, entries, final_key):
        return AssignmentInspector(self)._choose_native_menu(entries, final_key)

    def _show_assignments(self, plan):
        return AssignmentInspector(self)._show_assignments(plan)

    def read_assignments(self, queries, *, revision, compilation=None, allocation_context=None):
        return AssignmentInspector(self).read_assignments(queries, revision=revision, compilation=compilation, allocation_context=allocation_context)

    def show_assignments_completion(self, observation):
        return AssignmentInspector(self).show_assignments_completion(observation)

    def reopen(self):
        return ProjectArchive(self).reopen()

    def close(self):
        # Closing the Python controller does not close an operator's IDE or
        # erase an incomplete native operation. The final page stays visible.
        if self._file_use is not None:
            self._file_use.close()
        self._closed = True

    def execute(self, command, prepared):
        self.verify_project_codepage()
        if self._last_saved is None or self.path.read_bytes() != self._last_saved:
            raise NativeEngineeringDriverError('工程文件在本步骤前已被其他写入改变。')
        operation = command['operation']
        plan = prepared['plan']
        baseline_raw = self._last_saved
        if operation in ('create_global_labels', 'edit_global_labels'):
            if operation == 'create_global_labels':
                self._new_object(plan['source_name'], '全局标签')
                self._visible('工程导航树', object=plan['source_name'])
            visible = self._csv_labels(plan['source_name'], 'global', plan['rows'])
        elif operation == 'edit_pou_labels':
            from gxw.fb_authoring import definition_snapshot
            definitions = definition_snapshot(self._last_saved, require_fbd=False)['definitions']
            scope = 'fb' if any(r['kind'] == 'function_block' and r['name'] == plan['source_name'] for r in definitions) else 'program'
            visible = self._csv_labels(plan['source_name'], scope, plan['rows'])
        elif operation in ('create_st_fb', 'edit_st_fb'):
            if operation == 'create_st_fb':
                self._new_object(plan['source_name'], 'FB', 'ST')
                self._visible('工程导航树', object=plan['source_name'])
            if plan['write_declarations']:
                self._csv_labels(plan['source_name'], 'fb', plan['rows'])
            before_text = None
            if operation == 'edit_st_fb':
                from gxw.fb_authoring import definition_snapshot
                before_text = next(r['st_text'] for r in definition_snapshot(self._last_saved, plan['source_name'], require_fbd=False)['definitions'])
            visible = self._write_st(plan['source_name'], plan['st_text'], before=before_text)
        elif operation in ('create_st_program', 'edit_st_program'):
            if operation == 'create_st_program':
                self._new_object(plan['source_name'], '程序块', 'ST')
                self._visible('工程导航树', object=plan['source_name'])
                self._bind_program_task(plan)
            before_text = None
            if operation == 'edit_st_program':
                from gxw.fb_authoring import st_program_snapshot
                before_text = st_program_snapshot(self._last_saved, plan['source_name'])['programs'][0]['st_text']
            if plan['write_declarations']:
                self._csv_labels(plan['source_name'], 'program', plan['rows'])
            visible = self._write_st(plan['source_name'], plan['st_text'], before=before_text, kind='program')
        elif operation == 'create_structure':
            self._new_object(plan['source_name'], '结构体')
            self._visible('工程导航树', object=plan['source_name'])
            visible = self._csv_labels(plan['source_name'], 'structure', plan['rows'])
        elif operation == 'create_fbd_call':
            visible = self._create_fbd_call(command, plan)
        elif operation == 'edit_fbd_graph':
            try:
                return self._edit_fbd_graph(command, prepared)
            except Exception as error:
                from copy import deepcopy
                raise NativeEngineeringDriverError(str(error), observation={
                    **getattr(error, 'observation', {}),
                    'graph_actions': deepcopy(getattr(self, '_graph_receipts', [])),
                    'source_status': 'requires_reconciliation'}) from error
        elif operation == 'configure_project':
            visible = self._configure(plan)
        elif operation == 'compile':
            return self._compile(plan)
        elif operation == 'show_assignments':
            return self._show_assignments(plan)
        elif operation == 'save':
            self.snapshot()
            visible = self._visible('原生工程保存', path=str(self.path))
        elif operation == 'reopen':
            self.reopen()
            visible = self._visible('保存工程冷重开', path=str(self.path))
        else:
            raise NativeEngineeringDriverError('没有已验证的前台工程操作：' + str(operation))
        if prepared['kind'] == 'authoring':
            from gxw.visible_engineering import verify_authoring_readback
            self._save_until(lambda raw: verify_authoring_readback(baseline_raw, command, raw, prepared))
            if operation != 'configure_project':
                details = {k: v for k, v in visible.items() if k not in ('completed', 'page', 'image')}
                visible = self._visible(visible['page'], verified_saved_source=True, **details)
        elif operation == 'create_fbd_call':
            from gxw.visible_engineering import verify_fbd_call
            self._save_until(lambda raw: verify_fbd_call(baseline_raw, command, raw, prepared))
            visible = self._visible(visible['page'], instance=command['instance'], verified_saved_source=True)
        if (prepared['kind'] == 'authoring' or operation == 'create_fbd_call') and self.evidence_directory:
            (self.evidence_directory / f'{self._captures:03d}-{operation}.gxw').write_bytes(self._last_saved)
        return {'visible_completion': visible}

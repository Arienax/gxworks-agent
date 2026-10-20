"""CompileInspector operations on an explicitly bound native desktop."""
from __future__ import annotations
from .engineering_errors import (NativeEngineeringDriverError)
from .engineering_observations import (_accessible_compile_diagnostic_rows, _compile_summary_counts)


class CompileInspector:
    def __init__(self, desktop):
        self.desktop = desktop

    def compile_current(self, plan):
        """Compile the live revision; GX itself may save, this driver does not."""
        self.desktop.verify_project_codepage()
        return self.desktop._compile(plan, legacy_save=False)

    def _read_compile_diagnostics(self, summary, run):
        from copy import deepcopy
        binding = dict(compile_run_id=run['compile_run_id'], revision=run['revision'],
            pid=run['identity']['process_id'], identity=deepcopy(run['identity']),
            origin='current_native_compile_diagnostic_grid')
        expected = _compile_summary_counts(run.get('output'),
            public_checks_requested=run.get('public_checks_requested'),
            native_ui_language=getattr(self.desktop, 'native_ui_language', 'zh-CN'))
        try:
            grids = summary.parent().descendants(class_name='VSFlexGrid8N')
            candidates, failures = [], []
            for grid in grids:
                try:
                    rows = _accessible_compile_diagnostic_rows(grid.handle,
                        native_ui_language=getattr(self.desktop, 'native_ui_language', 'zh-CN'))
                except Exception as error:
                    failures.append(type(error).__name__)
                    continue
                if rows is not None:
                    candidates.append(rows)
        except Exception as error:
            return dict(binding, status='unavailable', reason='native_diagnostic_grid_not_available',
                        error_type=type(error).__name__, rows=[])
        if len(candidates) != 1 or failures:
            return dict(binding, status='unavailable', rows=[],
                reason='native_diagnostic_grid_missing_ambiguous_or_unreadable',
                candidate_count=len(candidates), unreadable_count=len(failures))
        rows, = candidates
        counts = {name: sum(row['severity'] == name for row in rows)
                  for name in ('Error', 'Warning', 'CheckWarning', 'Information')}
        matched = expected is not None and all(counts[name] == (0 if value is None else value)
                                              for name, value in expected.items())
        return dict(binding, status='observed' if matched else 'unconfirmed', rows=rows,
            severity_counts=counts, expected_counts=expected,
            **({} if matched else {'reason': 'diagnostic_severity_counts_differ_from_current_summary'}))

    def _completed_compile_receipt(self, run, summary, output, *, driver_saved=False):
        from copy import deepcopy
        result = dict(command='rebuild_all', output=output, completed=True, output_kind='native_summary',
            source_unchanged=True if driver_saved else None, saved=True if driver_saved else None,
            driver_saved=driver_saved, native_save_behavior='compile_may_save',
            source_binding='saved_snapshot_required' if driver_saved else 'session_revision_required',
            compile_run_id=run['compile_run_id'], identity=deepcopy(run['identity']), summary_handle=summary.handle,
            pid=run['identity']['process_id'], revision=run['revision'],
            public_checks_requested=run['public_checks_requested'])
        result['diagnostics'] = self.desktop._read_compile_diagnostics(summary, result)
        return result

    def _compile(self, plan, *, legacy_save=True):
        from copy import deepcopy
        from uuid import uuid4
        if type(plan.get('revision')) is not int or plan['revision'] < 0 or type(legacy_save) is not bool:
            raise NativeEngineeringDriverError('原生编译需要明确的当前源码修订。')
        if 'public_checks_requested' in plan and type(plan['public_checks_requested']) is not bool:
            raise NativeEngineeringDriverError('原生编译后的程序检查设置必须是明确布尔值。')
        self.desktop._idle()
        if getattr(self.desktop, '_pending_compile_run', None) is not None:
            raise NativeEngineeringDriverError('此前原生编译尚待核对，未重新发送编译命令。')
        self.desktop._last_compile_run = None
        # Clear only the native output summary control before dispatch. A
        # previous successful build cannot satisfy this operation's completion.
        # The output can be an inactive page in a floating docking group.
        # Clear that actual control too; the compiler will activate its page.
        summaries = self.desktop._owned_control_handles('Edit', 10013, visible=False)
        if len(summaries) != 1:
            raise NativeEngineeringDriverError('原生编译输出摘要控件不明确。')
        summary = self.desktop.ui._application(self.desktop.session).window(handle=summaries[0])
        import win32gui
        win32gui.SendMessage(summary.handle, 0x000C, 0, '')
        if summary.window_text():
            raise NativeEngineeringDriverError('无法建立本次编译的新输出完成点。')
        identity = self.desktop.inspect()
        pending = dict(compile_run_id=uuid4().hex, identity=deepcopy(identity), revision=plan['revision'],
            summary_handle=summary.handle, fresh_output_reset=True, command_dispatched=False,
            confirmation_dispatched=False, confirmation_accepted=False, public_checks_requested=None)
        self.desktop._pending_compile_run = pending
        self.desktop._command(0x4A1)
        pending['command_dispatched'] = True
        def prompt():
            rows = [d for d in self.desktop._dialogs() if d.is_enabled() and self.desktop._text('compile_confirm') in self.desktop._dialog_text(d)]
            return rows[0] if len(rows) == 1 else None
        dialog = self.desktop._wait(prompt, '没有出现本次全部编译确认页。')
        pending['confirmation_handle'] = dialog.handle
        checks = dialog.child_window(control_id=12270, class_name='Button')
        check_state = checks.get_check_state()
        if check_state not in (0, 1):
            raise NativeEngineeringDriverError('原生编译后的程序检查选项状态不可确认。')
        expected_check = (1 if legacy_save else int(plan.get('public_checks_requested', bool(check_state))))
        if check_state != expected_check:
            self.desktop._click(dialog, 12270)
            self.desktop._wait(lambda: checks.get_check_state() == expected_check, '原生编译后的程序检查没有匹配工程设置。')
        if checks.get_check_state() != expected_check:
            raise NativeEngineeringDriverError('原生编译检查选项在确认前发生改变。')
        pending['public_checks_requested'] = bool(expected_check)
        self.desktop._click(dialog, 1)
        pending['confirmation_dispatched'] = True
        def completed():
            output = summary.window_text()
            dialogs = self.desktop._dialogs()
            if not any(d.handle == pending['confirmation_handle'] for d in dialogs):
                pending['confirmation_accepted'] = True
            if (win32gui.IsWindowVisible(summary.handle)
                    and _compile_summary_counts(output,
                        public_checks_requested=pending['public_checks_requested'],
                        native_ui_language=getattr(self.desktop, 'native_ui_language', 'zh-CN')) is not None
                    and self.desktop._window().is_enabled() and not dialogs and pending['confirmation_accepted']):
                return output
            return None
        output = self.desktop._wait(completed, '本次原生全部编译尚未完成。')
        if self.desktop.inspect() != identity:
            raise NativeEngineeringDriverError('原生编译期间工程身份已改变。')
        if legacy_save:
            self.desktop.snapshot()
        result = self.desktop._completed_compile_receipt(pending, summary, output, driver_saved=legacy_save)
        if (self.desktop.inspect() != identity or summary.window_text() != output
                or self.desktop._dialogs() or not self.desktop._window().is_enabled()):
            raise NativeEngineeringDriverError('核对本次编译诊断时摘要或工程状态发生改变。')
        self.desktop._last_compile_run = deepcopy(result)
        self.desktop._pending_compile_run = None
        result['visible_completion'] = self.desktop._visible('本次原生编译输出', output=output)
        self.desktop._last_compile_run = deepcopy(result)
        return result

    def read_current_compile_result(self, *, revision):
        """Recheck a run started by this driver, never adopt an old output pane."""
        from copy import deepcopy
        previous = getattr(self.desktop, '_last_compile_run', None)
        pending = getattr(self.desktop, '_pending_compile_run', None)
        missing = dict(status='unavailable', origin='active_native_compile', revision=revision,
                       driver_saved=False, source_binding='session_revision_required')
        recovering = not isinstance(previous, dict) and isinstance(pending, dict)
        run = pending if recovering else previous
        if not isinstance(run, dict) or type(revision) is not int or revision != run.get('revision'):
            return {**missing, 'reason': 'no_completed_compile_for_requested_revision'}
        identity = self.desktop.inspect()
        if identity != run.get('identity'):
            return {**missing, 'reason': 'native_project_identity_changed'}
        try:
            self.desktop._idle()
        except NativeEngineeringDriverError:
            return {**missing, 'reason': 'native_compile_or_modal_operation_not_finished'}
        controls = self.desktop._owned_control_handles('Edit', 10013, visible=False)
        if controls != [run.get('summary_handle')]:
            return {**missing, 'reason': 'compile_output_control_changed_or_ambiguous'}
        summary = self.desktop.ui._application(self.desktop.session).window(handle=controls[0])
        output = summary.window_text()
        if recovering:
            import win32gui
            if (run.get('fresh_output_reset') is not True or run.get('command_dispatched') is not True
                    or run.get('confirmation_dispatched') is not True
                    or type(run.get('public_checks_requested')) is not bool):
                return {**missing, 'reason': 'original_compile_confirmation_not_dispatched'}
            if (not win32gui.IsWindowVisible(summary.handle) or _compile_summary_counts(output,
                    public_checks_requested=run['public_checks_requested'],
                    native_ui_language=getattr(self.desktop, 'native_ui_language', 'zh-CN')) is None
                    or self.desktop._dialogs() or not self.desktop._window().is_enabled()):
                return {**missing, 'reason': 'original_native_compile_not_completed'}
            run['confirmation_accepted'] = True
            previous = self.desktop._completed_compile_receipt(run, summary, output)
            previous['recovered'] = True
        elif output != previous.get('output'):
            return {**missing, 'reason': 'compile_output_or_project_changed'}
        if (self.desktop.inspect() != identity or summary.window_text() != output):
            return {**missing, 'reason': 'compile_output_or_project_changed'}
        diagnostics = self.desktop._read_compile_diagnostics(summary, previous)
        old_diagnostics = previous.get('diagnostics', {})
        if (old_diagnostics.get('status') == 'observed' and (diagnostics.get('status') != 'observed'
                or diagnostics.get('rows') != old_diagnostics.get('rows'))):
            return {**missing, 'reason': 'compile_diagnostic_rows_changed_or_unavailable'}
        previous = {**deepcopy(previous), 'diagnostics': diagnostics}
        if self.desktop.inspect() != identity or summary.window_text() != output:
            return {**missing, 'reason': 'compile_output_or_project_changed'}
        self.desktop._last_compile_run = deepcopy(previous)
        if recovering:
            self.desktop._pending_compile_run = None
        return {**deepcopy(previous), 'status': 'observed', 'origin': 'active_native_compile',
                'read_driver_saved': False}

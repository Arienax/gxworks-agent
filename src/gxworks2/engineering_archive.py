# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Save, observe and reopen the bound project without replaying edits."""
from __future__ import annotations
import time
from .engineering_errors import NativeEngineeringDriverError

class ProjectArchive:
    def __init__(self, desktop):
        self.desktop = desktop

    def snapshot(self, *, on_dispatch=None):
        if on_dispatch is not None and not callable(on_dispatch):
            raise NativeEngineeringDriverError('保存命令的内部发送回执必须为可调用函数。')
        self.desktop.verify_project_codepage()
        self.desktop._foreground()
        self.desktop.inspect()
        import win32gui
        import pywintypes
        # Return from the actual Save handler before accepting an unchanged
        # file. A posted command can still be queued after the file looks idle.
        # A native modal notice is handled below after this bounded send ends.
        try:
            win32gui.SendMessageTimeout(self.desktop.session.window_handle, 0x0111,
                                       self.desktop.ui.SAVE_PROJECT_COMMAND, 0, 2, 1500)
        except pywintypes.error as error:
            code = getattr(error, 'winerror', error.args[0] if error.args else None)
            uncertain = code in (0, 1460)
            if on_dispatch is not None:
                on_dispatch(dict(send_attempted=True, save_dispatched=uncertain,
                    dispatch_status='timed_out' if uncertain else 'rejected', native_error_code=code))
            if not uncertain:
                raise
        else:
            if on_dispatch is not None:
                on_dispatch(dict(send_attempted=True, save_dispatched=True, dispatch_status='returned'))
        raw = self.desktop.read_saved_project(acknowledge_notices=True)
        self.desktop._last_saved = raw
        return raw

    def read_saved_project(self, *, acknowledge_notices=False):
        """Observe complete owned file bytes without sending a Save command."""
        self.desktop.inspect()
        stable = [None, None]
        def saved():
            if self.desktop._dialogs():
                if acknowledge_notices:
                    self.desktop.ui._acknowledge_known_save_notices(self.desktop.session,
                        native_ui_language=getattr(self.desktop, 'native_ui_language', 'zh-CN'))
                return None
            if not self.desktop.path.is_file() or not self.desktop._window().is_enabled():
                return None
            stat = self.desktop.path.stat()
            key = (stat.st_size, stat.st_mtime_ns)
            if key != stable[0]:
                stable[:] = [key, time.monotonic()]
                return None
            if time.monotonic() - stable[1] < .2:
                return None
            try:
                raw = self.desktop.path.read_bytes()
                after = self.desktop.path.stat()
                if (after.st_size, after.st_mtime_ns) != key:
                    stable[:] = [None, None]
                    return None
                from gxw.container_writer import validate_cfb_streams
                validate_cfb_streams(raw)
            except (OSError, ValueError):
                return None
            return raw
        raw = self.desktop._wait(saved, '没有取得当前受控工程的稳定完整文件。')
        self.desktop.inspect()
        return raw

    def _save_until(self, verify):
        """Re-observe an async UI edit; never replay the engineering mutation."""
        deadline = time.monotonic() + self.desktop.timeout
        error = None
        raw = self.desktop.snapshot()
        while True:
            try:
                verify(raw)
            except (ValueError, NativeEngineeringDriverError) as observed:
                error = observed
                if time.monotonic() >= deadline:
                    break
                time.sleep(.15)
                raw = self.desktop.read_saved_project()
                continue
            self.desktop._last_saved = raw
            return raw
        raise NativeEngineeringDriverError('原生修改尚未通过保存回读：' + str(error))

    def verify_saved_source(self, raw):
        """Match current pages to the observed file without flushing UI edits."""
        from .live_driver import LiveEngineeringDriver
        from gxw.live_context import verify_active_saved_context
        if self.desktop.path.read_bytes() != raw:
            raise NativeEngineeringDriverError('核对当前源码前工程文件已改变。')
        observer = LiveEngineeringDriver(self.desktop, raw, self.desktop.cpu)
        current = observer.read_context()
        verification = verify_active_saved_context(raw, current, raw)
        if self.desktop.path.read_bytes() != raw:
            raise NativeEngineeringDriverError('核对当前源码期间工程文件已改变。')
        return verification

    def reopen(self):
        self.desktop._idle()
        saved = self.desktop.read_saved_project()
        if self.desktop._last_saved is None or saved != self.desktop._last_saved:
            raise NativeEngineeringDriverError('重新打开需要当前已确认的保存工程；未发送保存或关闭。')
        self.desktop._command(0x401)
        self.desktop._wait(lambda: not self.desktop._frame_identity().project_open and not self.desktop._dialogs(), '工程未冷关闭，未丢弃任何修改。')
        self.desktop._command(0x400, project=False)
        self.desktop._file_name(self.desktop._dialog('打开工程'), self.desktop.path)
        self.desktop._wait(lambda: self.desktop.path.name.casefold() in self.desktop._window().window_text().casefold()
                   and not self.desktop._dialogs(), '保存的工程没有重新打开。')
        self.desktop.inspect()
        reopened = self.desktop.read_saved_project()
        if reopened != saved:
            raise NativeEngineeringDriverError('重新打开期间工程文件发生变化，需要重新核对。')
        from gxw.fb_authoring import st_program_snapshot
        # Caption readiness precedes loading the ordinary ST text. Observe
        # the saved bodies in their actual editors before returning a reopen
        # receipt; never repair or fall back to the file on a mismatch.
        from gxw.visible_engineering import normalized_st
        for program in st_program_snapshot(saved)['programs']:
            def loaded(program=program):
                try:
                    observed = self.desktop.read_st(program['name'])
                except NativeEngineeringDriverError as error:
                    if str(error) == 'ST 正文在读取期间发生变化。':
                        return False
                    raise
                return normalized_st(observed['st_text']) == normalized_st(program['st_text'])
            self.desktop._wait(loaded, '冷重开后的 ST 正文尚未与保存工程一致。')
        self.desktop._visible('保存工程冷重开', path=str(self.desktop.path), same_process=True)
        return reopened

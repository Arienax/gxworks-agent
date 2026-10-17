"""Visible, process-bound GX Works2 engineering operations.

The caller owns the desktop lock and COM apartment for the whole session.
Core prepares and verifies domain changes. This driver operates native pages;
it never replaces a live project with an offline Workspace export.
"""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path
import re
import tempfile
import threading
import time

from .finder import GXWorks2Finder
from .project_profiles import GXWORKS2_NATIVE_VERSION, native_project_profile
from .ui_automation import PywinautoGXWorks2UIAutomation


class NativeEngineeringDriverError(RuntimeError):
    def __init__(self, message, *, observation=None):
        super().__init__(message)
        self.observation = observation or {}


def _same_text(left, right):
    return left.replace('\r\n', '\n').replace('\r', '\n') == right.replace('\r\n', '\n').replace('\r', '\n')


def _post_native_client_click(handle, x, y, *, right=False):
    """Send target-client coordinates in the target window's DPI context.

    GX's tree item rectangles are in its DPI-unaware client units. Posting
    them from an aware Python thread otherwise causes Windows to scale them
    again and can select a different engineering object.
    """
    import ctypes
    from ctypes import wintypes
    import win32gui
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.GetWindowDpiAwarenessContext.argtypes = [wintypes.HWND]
    api.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
    api.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    api.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    context = api.GetWindowDpiAwarenessContext(handle)
    previous = api.SetThreadDpiAwarenessContext(context) if context else None
    if not previous:
        raise NativeEngineeringDriverError('无法确认原生坐标的 DPI 上下文。')
    try:
        position = (int(y) << 16) | int(x)
        win32gui.PostMessage(handle, 0x0204 if right else 0x0201, 2 if right else 1, position)
        win32gui.PostMessage(handle, 0x0205 if right else 0x0202, 0, position)
    finally:
        api.SetThreadDpiAwarenessContext(previous)


class _NativeFileUse:
    """Read Windows' exact-file owners when GX abbreviates its caption path."""

    def __init__(self, path):
        import ctypes
        from ctypes import wintypes as w
        class UniqueProcess(ctypes.Structure):
            _fields_ = [('pid', w.DWORD), ('started', w.FILETIME)]
        class ProcessInfo(ctypes.Structure):
            _fields_ = [('process', UniqueProcess), ('app', w.WCHAR * 256), ('service', w.WCHAR * 64),
                        ('kind', w.UINT), ('status', w.ULONG), ('session', w.DWORD), ('restartable', w.BOOL)]
        self.ctypes, self.types, self.ProcessInfo = ctypes, w, ProcessInfo
        self.api = ctypes.WinDLL('Rstrtmgr')
        self.handle = w.DWORD()
        self.api.RmStartSession.argtypes = [ctypes.POINTER(w.DWORD), w.DWORD, w.LPWSTR]
        self.api.RmRegisterResources.argtypes = [w.DWORD, w.UINT, ctypes.POINTER(w.LPCWSTR),
            w.UINT, ctypes.c_void_p, w.UINT, ctypes.c_void_p]
        self.api.RmGetList.argtypes = [w.DWORD, ctypes.POINTER(w.UINT), ctypes.POINTER(w.UINT),
            ctypes.POINTER(ProcessInfo), ctypes.POINTER(w.DWORD)]
        self.api.RmEndSession.argtypes = [w.DWORD]
        if self.api.RmStartSession(ctypes.byref(self.handle), 0, ctypes.create_unicode_buffer(33)):
            raise NativeEngineeringDriverError('无法建立原生工程文件占用查询。')
        if self.api.RmRegisterResources(self.handle, 1, (w.LPCWSTR * 1)(str(path)), 0, None, 0, None):
            self.close()
            raise NativeEngineeringDriverError('无法登记明确指定的原生工程文件。')

    def owns(self, process_id, process_started):
        ctypes, w = self.ctypes, self.types
        needed, count, reasons = w.UINT(), w.UINT(), w.DWORD()
        status = self.api.RmGetList(self.handle, ctypes.byref(needed), ctypes.byref(count),
                                    None, ctypes.byref(reasons))
        if status == 0 and needed.value == 0:
            return False
        for _ in range(3):
            if status != 234 or not 1 <= needed.value <= 1024:
                raise NativeEngineeringDriverError('原生工程文件占用状态不可确认。')
            rows = (self.ProcessInfo * needed.value)()
            count.value = needed.value
            status = self.api.RmGetList(self.handle, ctypes.byref(needed), ctypes.byref(count),
                                        rows, ctypes.byref(reasons))
            if status == 0:
                return any(row.process.pid == process_id and
                    row.process.started.dwLowDateTime + (row.process.started.dwHighDateTime << 32) == int(process_started)
                    for row in rows[:count.value])
        raise NativeEngineeringDriverError('原生工程文件占用在查询中持续变化。')

    def close(self):
        if self.handle.value:
            self.api.RmEndSession(self.handle)
            self.handle.value = 0


def _abbreviated_project_path(title_path, path):
    import ntpath
    return (isinstance(title_path, str) and title_path.startswith('...')
            and len(title_path) > 3
            and ntpath.normcase(str(path)).endswith(ntpath.normcase(title_path[3:]))
            and title_path.casefold().endswith(ntpath.basename(str(path)).casefold()))


def _label_csv(raw):
    if len(raw) > 8 * 1024 * 1024 or not raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        raise NativeEngineeringDriverError('原生标签 CSV 缺少已验证的 UTF-16 格式。')
    rows = list(csv.reader(io.StringIO(raw.decode('utf-16')), delimiter='\t', strict=True))
    if len(rows) < 2 or len(rows[0]) != 1 or len(set(rows[1])) != len(rows[1]):
        raise NativeEngineeringDriverError('原生标签 CSV 的表头不明确。')
    body = [r for r in rows[2:] if r and any(r)]
    if any(len(r) != len(rows[1]) for r in body):
        raise NativeEngineeringDriverError('原生标签 CSV 的列数不一致。')
    return rows[0], rows[1], body


def _merge_label_csv(raw, rows, *, structure=False):
    """Mechanical column transport; types/defaults have already passed Core."""
    title, header, previous = _label_csv(raw)
    known = {'类', '标签名', '数据类型', '常量', '软元件', '地址', '注释', '备注',
             '系统标签的关联', '系统标签名', '属性'}
    required = {'类', '标签名', '数据类型', '常量', '注释'}
    if structure:
        required.remove('类')
        known = required
    if not required <= set(header) or set(header) - known:
        raise NativeEngineeringDriverError('此标签表的原生 CSV 列尚未验证。')
    old = {}
    for row in previous:
        name = row[header.index('标签名')].casefold()
        if name in old:
            raise NativeEngineeringDriverError('原生标签表中存在重名行。')
        old[name] = dict(zip(header, row))
    result = []
    from gxw.declarations import CLASS_CODES
    classes = {code: name for name, code in CLASS_CODES.items()}
    for row in rows:
        entry = dict(old.get(row['name'].casefold(), {}))
        if row['class_code'] not in classes:
            raise NativeEngineeringDriverError('此原生标签类别尚未验证。')
        entry.update({'类': classes[row['class_code']], '标签名': row['name'],
                      '数据类型': row['data_type'], '注释': row['comment'],
                      '常量': '' if structure else row.get(
                          'csv_constant', row['initial_value'] if row['class_code'] in (2, 9) else '')})
        if row.get('devices'):
            raise NativeEngineeringDriverError('前台 CSV 尚未验证结构成员的显式地址。')
        for column, field in (('软元件', 'device'), ('地址', 'iec_address')):
            value = row.get(field, '')
            if value and column not in header:
                raise NativeEngineeringDriverError('此标签表没有请求的显式地址列。')
            if column in header:
                entry[column] = value
        result.append([entry.get(column, '') for column in header])
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter='\t', quoting=csv.QUOTE_ALL, lineterminator='\r\n')
    writer.writerow(title)
    writer.writerow(header)
    writer.writerows(result)
    return output.getvalue().encode('utf-16')


def _grid_calibration(pixels):
    """Observe the native dot lattice; never guess a cached editor rectangle."""
    from collections import defaultdict

    width, height = pixels.size
    rgb = pixels.convert('RGB')
    samples = rgb.load()
    dark = {(x, y) for y in range(3, min(height - 3, 140))
            for x in range(40, width - 24)
            if max(samples[x, y]) < 180
            and max(samples[x, y]) - min(samples[x, y]) < 12}
    dots = set()
    # Native dots are isolated one/two-pixel components. A short horizontal
    # slice through text or a port line is not independent grid evidence.
    while dark:
        first = dark.pop()
        pending, component = [first], [first]
        while pending:
            x, y = pending.pop()
            for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0),
                           (1, 0), (-1, 1), (0, 1), (1, 1)):
                point = (x + dx, y + dy)
                if point in dark:
                    dark.remove(point)
                    pending.append(point)
                    component.append(point)
        xs, ys = zip(*component)
        if max(xs) - min(xs) <= 1 and max(ys) - min(ys) <= 1:
            dots.add((min(xs), min(ys)))

    candidates = []
    for pitch in range(8, 49):
        phases = defaultdict(set)
        for x, y in dots:
            if (((x - pitch, y) in dots or (x + pitch, y) in dots)
                    and ((x, y - pitch) in dots or (x, y + pitch) in dots)):
                phases[x % pitch, y % pitch].add((x, y))
        for phase, points in phases.items():
            rows = defaultdict(set)
            for x, y in points:
                rows[y].add(x)
            if sum(len(xs) >= 3 for xs in rows.values()) < 3:
                continue
            support, proof = [], set()
            for y, xs in rows.items():
                shared = xs & rows.get(y + pitch, set())
                chains = []
                for x in shared:
                    if x - pitch not in shared:
                        count = 1
                        while x + count * pitch in shared:
                            count += 1
                        chains.append((x, count))
                # Require an observed two-row, eight-column rectangle. Other
                # same-phase paired runs may be partly hidden by native blocks.
                if any(count >= 8 for _, count in chains):
                    support.extend((x, y) for x, count in chains if count >= 3)
                    proof.update((x + column * pitch, row_y)
                                 for x, count in chains if count >= 8
                                 for column in range(count) for row_y in (y, y + pitch))
            if support:
                candidates.append({'pitch': pitch, 'phase': phase, 'points': points,
                                   'proof': proof,
                                   'x': min(x for x, _ in support) - pitch,
                                   'y': min(y for _, y in support) - pitch})
    # A complete lattice also supports integral multiples of its period. Only
    # discard such an alias when all its observed rectangles are explained by
    # the finer lattice; a second independent grid must remain ambiguous.
    candidates = [candidate for candidate in candidates if not any(
        candidate['pitch'] > other['pitch']
        and candidate['pitch'] % other['pitch'] == 0
        and candidate['phase'][0] % other['pitch'] == other['phase'][0]
        and candidate['phase'][1] % other['pitch'] == other['phase'][1]
        and candidate['proof'] <= other['points']
        for other in candidates)]
    if len(candidates) != 1:
        raise NativeEngineeringDriverError('无法唯一确认当前 FBD 页面的二维原生网格。')
    grid = candidates[0]
    return {'pitch': grid['pitch'], 'x': grid['x'], 'y': grid['y'],
            'width': width, 'height': height}


def _accessible_grid_rows(handle):
    """Read the native grid's public MSAA row/cell interface, without UIA."""
    import ctypes
    from comtypes.automation import IDispatch
    from comtypes.client.dynamic import Dispatch
    interface = ctypes.POINTER(IDispatch)()
    status = ctypes.windll.oleacc.AccessibleObjectFromWindow(
        handle, ctypes.c_uint32(0xFFFFFFFC), ctypes.byref(IDispatch._iid_), ctypes.byref(interface))
    if status or not interface:
        raise NativeEngineeringDriverError('原生交叉引用表没有提供可核对的行接口。')
    grid = Dispatch(interface)
    count = grid.accChildCount
    if not 1 <= count <= 1048:
        raise NativeEngineeringDriverError('原生交叉引用表的结果数量超出本次展示范围。')
    result = []
    for index in range(1, count + 1):
        row = grid.accChild[index]
        native_row = str(row.accValue[0])
        if row.accRole[0] != 28 or native_row in ('0', '1'):
            continue  # column headers and the native filter row
        if not native_row.isdecimal() or not 2 <= int(native_row) <= 4097:
            raise NativeEngineeringDriverError('原生交叉引用没有可区分的实际结果行号。')
        if not 1 <= row.accChildCount <= 64:
            raise NativeEngineeringDriverError('原生交叉引用列数尚未验证。')
        cells = []
        for column in range(1, row.accChildCount + 1):
            cell = row.accChild[column]
            cells.append((cell.accName[0], cell.accValue[0]))
        def unique(name):
            values = [value for title, value in cells if title == name]
            if len(values) != 1 or not isinstance(values[0], str):
                raise NativeEngineeringDriverError('原生交叉引用列不唯一：' + name)
            return values[0]
        result.append({'native_row': int(native_row), **{key: unique(title) for key, title in (
            ('label', '软元件/标签'), ('address', '软元件'), ('program', '程序文件名'),
            ('pou', '数据名'), ('location', '位置'), ('read_write', '读入/写入'))}})
    return result


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
        driver = cls(session, path, cpu, timeout=timeout, progress=progress,
                     evidence_directory=evidence_directory, finder=finder, owned=True)
        import win32gui
        try:
            win32gui.ShowWindow(session.window_handle, 3)
            driver._create_project(language=language)
        except Exception as error:
            raise driver._startup_error(error) from error
        return driver

    @classmethod
    def start_from_saved(cls, path, cpu, *, timeout=30, progress=None, evidence_directory=None):
        """Open the caller's explicitly managed copy in a new native process."""
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
        driver = cls(session, path, cpu, timeout=timeout, progress=progress,
                     evidence_directory=evidence_directory, finder=finder, owned=True)
        import win32gui
        try:
            win32gui.ShowWindow(session.window_handle, 3)
            driver._command(0x400, project=False)
            driver._file_name(driver._dialog('打开工程'), path)
            driver._wait(lambda: path.name.casefold() in driver._window().window_text().casefold()
                         and not driver._dialogs(), '托管工程副本没有在新进程中打开。')
            driver.inspect()
            driver._visible('工程导航树', path=str(path), opened_managed_copy=True)
        except Exception as error:
            raise driver._startup_error(error) from error
        return driver

    def _startup_error(self, error):
        from copy import deepcopy
        observation = deepcopy(getattr(error, 'observation', {}))
        observation['startup_session'] = dict(zip((
            'process_id', 'window_handle', 'process_started', 'executable', 'software_version'), self._binding))
        observation['requested_project'] = str(self.path)
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
        return {**{key: getattr(session, key) for key in (
            'process_id', 'window_handle', 'process_started', 'executable', 'software_version')},
            'project_path': str(self.path), 'cpu_menu': profile.cpu, 'path_binding': binding}

    def _window(self):
        return self.ui._main_window(self._frame_identity())

    def _foreground(self, handle=None):
        import ctypes
        import win32gui
        import win32process
        self._frame_identity()
        target = self.session.window_handle if handle is None else handle
        if win32process.GetWindowThreadProcessId(target)[1] != self.session.process_id:
            raise NativeEngineeringDriverError('待激活的页面不属于已绑定的 GX Works2 进程。')
        window = self.ui._application(self.session).window(handle=target)
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
        handles = list(self.ui._native_dialog_handles(self.session))
        def visit(hwnd, _):
            if (win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == '#32770'
                    and win32process.GetWindowThreadProcessId(hwnd)[1] == self.session.process_id):
                if hwnd not in handles:
                    handles.append(hwnd)
        win32gui.EnumWindows(visit, None)
        return [self.ui._application(self.session).window(handle=h) for h in handles]

    @staticmethod
    def _dialog_text(dialog):
        return '\n'.join(w.window_text() for w in dialog.descendants(class_name='Static')).strip()

    def _idle(self):
        self.inspect()
        dialogs = self._dialogs()
        if dialogs:
            raise NativeEngineeringDriverError('GX Works2 有尚未完成的原生对话框。',
                observation={'dialogs': [{'title': d.window_text(), 'message': self._dialog_text(d)} for d in dialogs]})
        if not self._window().is_enabled():
            raise NativeEngineeringDriverError('GX Works2 工程窗口尚未返回可编辑状态。')

    def _command(self, command, *, allow_dialog=False, project=True):
        if project:
            self.inspect()
        else:
            self._frame_identity()
        if not allow_dialog and self._dialogs():
            raise NativeEngineeringDriverError('存在原生对话框，未发出新的工程命令。')
        if not allow_dialog:
            self._foreground()
        self.ui._post_native_command(self.session, command)

    def _wait(self, predicate, message, *, timeout=None):
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            self._frame_identity()
            value = predicate()
            if value:
                return value
            time.sleep(.05)
        raise NativeEngineeringDriverError(message,
            observation={'dialogs': [{'title': d.window_text(), 'message': self._dialog_text(d)} for d in self._dialogs()]})

    def _dialog(self, title):
        def found():
            rows = [d for d in self._dialogs() if d.window_text() == title and d.is_enabled()]
            return rows[0] if len(rows) == 1 else None
        return self._wait(found, '未确认原生对话框：' + title)

    def _click(self, dialog, control_id):
        import win32gui
        button = dialog.child_window(control_id=control_id, class_name='Button')
        if not dialog.is_enabled() or not button.is_enabled():
            raise NativeEngineeringDriverError('原生按钮当前不可用。')
        self._foreground(dialog.handle)
        # Validation can open a nested native modal dialog. Posting leaves the
        # controller able to observe it instead of blocking inside BM_CLICK.
        win32gui.PostMessage(button.handle, 0x00F5, 0, 0)

    def _confirm(self, fragment, control_id):
        def found():
            rows = [d for d in self._dialogs() if d.is_enabled() and fragment in self._dialog_text(d)]
            return rows[0] if len(rows) == 1 else None
        dialog = self._wait(found, '未确认本次操作的原生提示。')
        text = self._dialog_text(dialog)
        self._click(dialog, control_id)
        return text

    def _file_name(self, dialog, path):
        path = str(Path(path).resolve())
        self._foreground(dialog.handle)
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
        self._click(dialog, 1)

    def _visible(self, page, *, dialog=None, **details):
        if dialog is None:
            self._idle()
            self._foreground()
        else:
            self.inspect()
            active = [d for d in self._dialogs() if d.is_enabled()]
            if len(active) != 1 or active[0].handle != dialog.handle:
                raise NativeEngineeringDriverError('待展示的原生设置对话框不明确。')
            self._foreground(dialog.handle)
        import win32gui
        win32gui.RedrawWindow(self.session.window_handle, None, None, 0x0185)
        time.sleep(.1)  # paint only; data completion is verified separately
        self._captures += 1
        receipt = {'completed': True, 'page': page, **details}
        if self.evidence_directory:
            path = self.evidence_directory / f'{self._captures:03d}.png'
            self._window().capture_as_image().save(path)
            receipt['image'] = str(path)
        if self.progress:
            self.progress('visible_completion', page)
        return receipt

    def _create_project(self, *, language='fbd'):
        self._command(self.ui.NEW_PROJECT_COMMAND, project=False)
        dialog = self._dialog('新建')
        profile = native_project_profile(self.cpu)
        series = dialog.child_window(control_id=10002, class_name='ComboBox')
        choices = [s for s in series.item_texts() if s in profile.series]
        if len(choices) != 1:
            raise NativeEngineeringDriverError('未确认新建工程的 CPU 系列。')
        self.ui._select_native_combo(series, choices[0])
        self.ui._select_native_combo(dialog.child_window(control_id=10003, class_name='ComboBox'), self.cpu)
        kind = dialog.child_window(control_id=10001, class_name='ComboBox')
        if kind.is_enabled():
            self.ui._select_native_combo(kind, '结构化工程')
        elif kind.selected_text() != '结构化工程':
            raise NativeEngineeringDriverError('此 CPU 当前没有已验证的结构化工程页面。')
        self.ui._select_native_combo(dialog.child_window(control_id=10070, class_name='ComboBox'),
                                     'ST' if language == 'st' else '结构化梯形图/FBD')
        self._click(dialog, 1)
        self._wait(lambda: 'POU_01' in self._window().window_text() and not self._dialogs(), '未确认原生空白 FBD 工程。')
        self._command(self.ui.SAVE_PROJECT_COMMAND, project=False)
        dialog = self._dialog('工程另存为')
        if self.path.exists():
            raise NativeEngineeringDriverError('新工程目标文件已出现，未覆盖。')
        self._file_name(dialog, self.path)
        self._wait(lambda: self.path.is_file() and self.path.name.casefold() in self._window().window_text().casefold()
                   and not self._dialogs(), '未确认新工程已经保存。')
        self.inspect()
        self._last_saved = self.path.read_bytes()
        if language == 'fbd':
            self._prepare_empty_fbd()
        self._initialize_new_allocation()
        self._visible('工程导航树', cpu=self.cpu, path=str(self.path))

    def _initialize_new_allocation(self):
        """Materialize a new GUI project's own defaults before its baseline."""
        if self.cpu != 'FX3U/FX3UC':
            return
        from gxw.project_metadata import project_gui_configuration
        from gxw.visible_engineering import source_projection
        initial = self.snapshot()
        if project_gui_configuration(initial)['status'] == 'matched':
            return
        before = source_projection(initial)
        self._configure({'ranges': []})
        def initialized(raw):
            if source_projection(raw) != before:
                raise NativeEngineeringDriverError('原生新工程初始化改变了既有源码或分配范围。')
            if project_gui_configuration(raw)['status'] != 'matched':
                raise NativeEngineeringDriverError('原生新工程的分配设置尚未完成初始化。')
        self._save_until(initialized)

    def _prepare_empty_fbd(self):
        """Remove only the original empty program's native left rail."""
        from gxw.object_model import read_project_context
        self._program_page('POU_01')
        raw = self.snapshot()
        model = read_project_context(raw, 'POU_01.Program.pou').object_model()
        if model['nodes'] or model['unknown_record_count']:
            raise NativeEngineeringDriverError('原生新建工程不是可确认的空白程序。')
        if not model['wires']:
            return
        if len(model['wires']) != 1 or model['wires'][0]['start'] != [1, 0]:
            raise NativeEngineeringDriverError('原生空白程序的母线形式尚未验证。')
        self._command(0x6AC)
        def no_rail(raw):
            observed = read_project_context(raw, 'POU_01.Program.pou').object_model()
            if observed['nodes'] or observed['wires'] or observed['unknown_record_count']:
                raise NativeEngineeringDriverError('空白 FBD 页面尚未回读为无母线形式。')
        raw = self._save_until(no_rail)
        actual = read_project_context(raw, 'POU_01.Program.pou').object_model()
        if actual['nodes'] or actual['wires'] or actual['unknown_record_count']:
            raise NativeEngineeringDriverError('没有确认空白程序已经采用无左母线的 FBD 形式。')

    def _active_document(self):
        import win32gui
        mdi = self._window().child_window(class_name='MDIClient').wrapper_object()
        handle = win32gui.SendMessage(mdi.handle, 0x0229, 0, 0)
        if not handle:
            raise NativeEngineeringDriverError('原生工程没有活动编辑页面。')
        return self.ui._application(self.session).window(handle=handle)

    def _navigate(self, paths, title_pattern):
        self._idle()
        self._foreground()
        import win32gui
        import win32process
        result_windows = []
        def find_result(handle, _):
            if (win32gui.IsWindowVisible(handle)
                    and win32process.GetWindowThreadProcessId(handle)[1] == self.session.process_id
                    and win32gui.GetClassName(handle) == 'XTPDockingPaneMiniWnd'
                    and win32gui.GetWindowText(handle).strip() == '交叉参照'):
                result_windows.append(handle)
        win32gui.EnumWindows(find_result, None)
        for handle in result_windows:
            # Close through the docking manager. Hiding the HWND alone leaves
            # its internal open state out of step with the native View menu.
            win32gui.PostMessage(handle, 0x0010, 0, 0)
        if result_windows:
            self._wait(lambda: all(not win32gui.IsWindowVisible(h) for h in result_windows),
                       '交叉参照窗口尚未让出目标编辑页面。')
        window = self._window()
        trees = [t for t in window.descendants(class_name='SysTreeView32')
                 if t.control_id() == 12051 and t.is_visible() and t.is_enabled()]
        if len(trees) != 1:
            raise NativeEngineeringDriverError('原生工程导航树不明确。')
        def tree_item():
            matches = []
            for path in paths:
                try:
                    item = trees[0].get_item(path, exact=True)
                except (IndexError, RuntimeError):
                    continue
                if item is not None:
                    matches.append(item)
            if len(matches) > 1:
                raise NativeEngineeringDriverError('原生工程对象路径不唯一。')
            return matches[0] if matches else None
        item = self._wait(tree_item, '原生工程对象路径尚未出现。')
        self.ui._select_native_tree_item(trees[0], item)
        self.ui._send_tree_enter(trees[0])
        def active():
            doc = self._active_document()
            return doc if re.search(title_pattern, doc.window_text()) else None
        doc = self._wait(active, '原生页面没有切换到指定工程对象。')
        self.ui._activate_native_document(self.session, re.compile(title_pattern))
        return doc

    def _labels_page(self, name, scope):
        if scope == 'global':
            return self._navigate([('全局标签', name)], '^全局标签设置 ' + re.escape(name) + r'\s*$')
        if scope == 'structure':
            return self._navigate([('程序部件', '结构体', name)], '^结构体设置 ' + re.escape(name) + r'\s*$')
        owner = '程序' if scope == 'program' else 'FB/FUN'
        prefix = '局部标签设置' if scope == 'program' else '函数/FB标签设置'
        return self._navigate([('程序部件', owner, name, '局部标签')], '^' + prefix + ' ' + re.escape(name) + r' \[')

    def _program_page(self, name, kind='program', language='fbd'):
        owner = '程序' if kind == 'program' else 'FB/FUN'
        suffix = r'\[ST\]' if language == 'st' else r'\[结构化梯形图/FBD\]'
        pattern = '^' + re.escape(name) + r' \[' + ('PRG' if kind == 'program' else 'FB') + r'\] 程序本体 ' + suffix
        return self._navigate([('程序部件', owner, name, '程序本体')], pattern)

    def snapshot(self):
        self._idle()
        self._foreground()
        import win32gui
        import pywintypes
        # Return from the actual Save handler before accepting an unchanged
        # file. A posted command can still be queued after the file looks idle.
        # A native modal notice is handled below after this bounded send ends.
        try:
            win32gui.SendMessageTimeout(self.session.window_handle, 0x0111,
                                       self.ui.SAVE_PROJECT_COMMAND, 0, 2, 1500)
        except pywintypes.error as error:
            if getattr(error, 'winerror', error.args[0]) not in (0, 1460):
                raise
        stable = [None, None]
        def saved():
            if self._dialogs():
                self.ui._acknowledge_known_save_notices(self.session)
                return None
            if not self.path.is_file() or not self._window().is_enabled():
                return None
            stat = self.path.stat()
            key = (stat.st_size, stat.st_mtime_ns)
            if key != stable[0]:
                stable[:] = [key, time.monotonic()]
                return None
            if time.monotonic() - stable[1] < .2:
                return None
            try:
                raw = self.path.read_bytes()
                from gxw.container_writer import validate_cfb_streams
                validate_cfb_streams(raw)
            except (OSError, ValueError):
                return None
            return raw
        raw = self._wait(saved, '没有取得本次原生保存后的完整工程。')
        self.inspect()
        self._last_saved = raw
        return raw

    def _save_until(self, verify):
        """Re-observe an async UI edit; never replay the engineering mutation."""
        deadline = time.monotonic() + self.timeout
        error = None
        while time.monotonic() < deadline:
            raw = self.snapshot()
            try:
                verify(raw)
            except (ValueError, NativeEngineeringDriverError) as observed:
                error = observed
                time.sleep(.15)
                continue
            return raw
        raise NativeEngineeringDriverError('原生修改尚未通过保存回读：' + str(error))

    def _csv_labels(self, name, scope, rows):
        self._labels_page(name, scope)
        # GX starts reading after its confirmation dialog closes. Keep transfer
        # files for the entire run, including interrupted native imports.
        self._transfers += 1
        directory = (self.evidence_directory / f'labels-{self._transfers:03d}'
                     if self.evidence_directory else Path(tempfile.mkdtemp(prefix='gxw-visible-labels-')))
        directory.mkdir(parents=True, exist_ok=self.evidence_directory is None)
        exported = directory / 'before.csv'
        self._command(self.ui.WRITE_CSV_COMMAND)
        self._confirm('将当前显示的标签信息写入至CSV文件。', 6)
        self._file_name(self._dialog('写入至CSV文件'), exported)
        self._wait(lambda: exported.is_file() and not self._dialogs(), '原生标签 CSV 导出未完成。')
        replacement = _merge_label_csv(exported.read_bytes(), rows, structure=scope == 'structure')
        path = directory / 'change.csv'
        path.write_bytes(replacement)
        self._command(self.ui.READ_CSV_COMMAND)
        self._file_name(self._dialog('从CSV文件读取'), path)
        self._confirm('读取指定的文件内容。', 6)
        ready_since = [None]
        def ready():
            if self._dialogs() or not self._window().is_enabled():
                ready_since[0] = None
                return False
            if ready_since[0] is None:
                ready_since[0] = time.monotonic()
            return time.monotonic() - ready_since[0] >= .4
        self._wait(ready, '原生标签导入仍有提示，未确认完整写入。')
        self._labels_page(name, scope)
        page = '全局标签表' if scope == 'global' else name + (' 结构体成员表' if scope == 'structure' else ' 局部标签表')
        return self._visible(page,
                             transfer_file=str(path))

    def _new_object(self, name, kind, language=None):
        self._idle()
        self._command(0x408)
        dialog = self._dialog('新建数据')
        self.ui._select_native_combo(dialog.child_window(control_id=10001, class_name='ComboBox'), kind)
        dialog.child_window(control_id=10006, class_name='Edit').set_edit_text(name)
        if language:
            control = dialog.child_window(control_id=10003, class_name='ListBox')
            choices = control.item_texts()
            if choices.count(language) != 1:
                raise NativeEngineeringDriverError('新建对象没有明确的原生语言选项。')
            index = choices.index(language)
            if control.send_message(0x0186, index, 0) != index:
                raise NativeEngineeringDriverError('原生语言选择未确认。')
            control.notify_parent(1)
        self._click(dialog, 1)
        self._wait(lambda: not self._dialogs(), '新建工程对象没有完成。')

    def _write_st(self, name, text, *, before=None, kind='fb'):
        import win32gui
        document = self._program_page(name, kind, 'st')
        controls = [w for w in document.descendants(class_name='RichEdit20W') if w.control_id() == 100]
        if len(controls) != 1:
            raise NativeEngineeringDriverError('ST 编辑器的原生文本控件不明确。')
        editor = controls[0]
        if before is not None and not _same_text(editor.window_text(), before):
            raise NativeEngineeringDriverError('活动 ST 正文与本步骤基础源码不同。')
        win32gui.SendMessage(editor.handle, 0x00B1, 0, -1)
        win32gui.SendMessage(editor.handle, 0x00C2, 1, text)
        self._wait(lambda: _same_text(editor.window_text(), text), '原生 ST 编辑器回读与请求正文不同。')
        return self._visible(name + ' ST 编辑器', native_text=editor.window_text())

    def _bind_program_task(self, plan):
        """Append a Program through its actual native task registration page."""
        import win32gui
        import win32process
        from pywinauto.keyboard import send_keys
        from gxw.native_write import native_task_scope_context
        line = plan['line']
        if type(line) is not int or not 1 <= line <= 65535:
            raise NativeEngineeringDriverError('原生任务登录行号不在已验证的范围内。')
        self._idle()
        self._foreground()
        trees = [t for t in self._window().descendants(class_name='SysTreeView32')
                 if t.control_id() == 12051 and t.is_visible() and t.is_enabled()]
        if len(trees) != 1:
            raise NativeEngineeringDriverError('原生任务导航树不明确。')
        tree = trees[0]
        tree.set_focus()
        item = tree.get_item(('程序设置', '执行程序', plan['resource'], plan['task']), exact=True)
        item.ensure_visible()
        self.ui._select_native_tree_item(tree, item)
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
                        and win32process.GetWindowThreadProcessId(handle)[1] == self.session.process_id
                        and win32gui.GetClassName(handle) == 'XTPPopupBar'):
                    handles.append(handle)
            win32gui.EnumWindows(found, None)
            return handles[0] if len(handles) == 1 else None
        handle = self._wait(popup, '没有确认所选任务的原生上下文菜单。')
        if win32gui.GetForegroundWindow() not in (handle, self.session.window_handle):
            raise NativeEngineeringDriverError('原生任务菜单不再拥有前台焦点。')
        send_keys('t', vk_packet=False)  # 打开任务登录(T), verified native task menu
        pattern = '^任务登录 ' + re.escape(plan['task']) + r'\s*$'
        def task_document():
            doc = self._active_document()
            return doc if re.search(pattern, doc.window_text()) and not popup() else None
        doc = self._wait(task_document, '原生任务登录页面没有打开。')
        self.ui._activate_native_document(self.session, re.compile(pattern))
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
        editor = self._wait(cell, '任务登录表没有进入明确的程序名称编辑单元格。')
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
        def bound(raw):
            current = native_task_scope_context(raw)
            if current['status'] != 'available' or current['tasks'] != expected:
                raise NativeEngineeringDriverError('保存后的原生任务成员与当前追加计划不同。')
        self._save_until(bound)
        return self._visible(plan['task'] + ' 任务登录', program=plan['source_name'], line=line)

    def _choose_label(self, name, *, expected_type=None):
        dialog = self._dialog('标签登录/选择')
        self.ui._select_native_combo(dialog.child_window(control_id=6651, class_name='ComboBox'), '<全部>')
        labels = dialog.child_window(control_id=6650, class_name='ListBox')
        choices = labels.item_texts()
        matches = [value for value in choices if value in (name, '+' + name)]
        if len(matches) != 1:
            raise NativeEngineeringDriverError('原生标签选择器没有唯一的已声明变量：' + name)
        index = choices.index(matches[0])
        if labels.send_message(0x0186, index, 0) != index:
            raise NativeEngineeringDriverError('原生标签列表没有确认选择。')
        labels.notify_parent(1)
        def selected():
            actual = dialog.child_window(control_id=6639, class_name='Edit').window_text()
            actual_type = dialog.child_window(control_id=6643, class_name='Edit').window_text()
            if (labels.send_message(0x0188, 0, 0) == index and actual == name
                    and actual_type and (expected_type is None or actual_type == expected_type)):
                return actual, actual_type
            return None
        actual, actual_type = self._wait(selected, '原生变量名称或实例类型与当前声明不同。')
        # Selecting an existing row must never move it into another scope.
        move = dialog.child_window(control_id=6654, class_name='Button')
        if move.get_check_state():
            raise NativeEngineeringDriverError('原生标签选择器启用了声明移动，未应用。')
        self._click(dialog, 1)
        ready_since = [None]
        def applied():
            remaining = [d for d in self._dialogs() if d.handle != dialog.handle and d.is_enabled()]
            if remaining:
                raise NativeEngineeringDriverError('原生变量绑定出现额外提示。', observation={
                    'dialogs': [{'title': d.window_text(), 'message': self._dialog_text(d)} for d in remaining]})
            if not dialog.is_enabled():
                ready_since[0] = None
                return False
            if ready_since[0] is None:
                ready_since[0] = time.monotonic()
            return time.monotonic() - ready_since[0] >= .25
        self._wait(applied, '原生标签应用没有返回可编辑状态。')
        self._click(dialog, 2)
        self._wait(lambda: not self._dialogs(), '原生变量绑定没有结束。')
        return {'name': actual, 'native_type': actual_type}

    def _graph_view(self, name):
        document = self._program_page(name)
        controls = [w for w in document.descendants(class_name='AfxFrameOrView70u') if w.control_id() == 59648]
        if len(controls) != 1:
            raise NativeEngineeringDriverError('原生 FBD 图形视图不明确。')
        return controls[0]

    def _graph_grid(self, view):
        import win32gui
        # These are the editor's actual scroll bars, not frame/status titles.
        for control in view.parent().descendants(class_name='ScrollBar'):
            if control.control_id() in (2, 3):
                info = win32gui.GetScrollInfo(control.handle, 2)
                if info[4] != 0:
                    raise NativeEngineeringDriverError('FBD 视图已滚动；尚未验证当前源码到画面的坐标映射。')
        return _grid_calibration(view.capture_as_image())

    @staticmethod
    def _graph_point(grid, x, y):
        point = (round(grid['x'] + x * grid['pitch']), round(grid['y'] + y * grid['pitch']))
        if not 5 < point[0] < grid['width'] - 28 or not 5 < point[1] < grid['height'] - 28:
            raise NativeEngineeringDriverError('目标图形对象不在当前可见区域。')
        return point

    def _graph_drag(self, view, grid, source, target):
        import win32process
        if (win32process.GetWindowThreadProcessId(view.handle)[1] != self.session.process_id
                or not view.is_visible() or not view.is_enabled()
                or any(not (5 < point[0] < grid['width'] - 28
                            and 5 < point[1] < grid['height'] - 28) for point in (source, target))):
            raise NativeEngineeringDriverError('图形拖拽没有明确绑定在当前原生编辑区域内。')
        self._idle()
        self._foreground()
        # pywinauto drag defaults to screen coordinates, unlike click_input.
        # Both points here come from this verified view's captured dot grid.
        view.drag_mouse_input(src=source, dst=target, absolute=False)

    def _graph_context(self, view, grid, box):
        import win32gui
        import win32process
        self._idle()
        self._foreground()
        if (win32process.GetWindowThreadProcessId(view.handle)[1] != self.session.process_id
                or not view.is_visible() or not view.is_enabled()):
            raise NativeEngineeringDriverError('待选择的图形视图没有绑定当前工程。')
        view.click_input(button='right', coords=self._graph_point(grid, box[0] + .5, box[1] + 1))
        def popup():
            handles = []
            def found(handle, _):
                if (win32gui.IsWindowVisible(handle)
                        and win32process.GetWindowThreadProcessId(handle)[1] == self.session.process_id
                        and win32gui.GetClassName(handle) == 'XTPPopupBar'):
                    handles.append(handle)
            win32gui.EnumWindows(found, None)
            return handles[0] if len(handles) == 1 else None
        handle = self._wait(popup, '没有确认当前图形对象的原生上下文菜单。')
        if (win32gui.GetForegroundWindow() not in (handle, self.session.window_handle)
                or popup() != handle):
            raise NativeEngineeringDriverError('当前图形对象菜单不再拥有前台焦点。')
        return popup

    def _select_graph_node(self, view, grid, box):
        import win32process
        self._idle()
        self._foreground()
        if (win32process.GetWindowThreadProcessId(view.handle)[1] != self.session.process_id
                or not view.is_visible() or not view.is_enabled()):
            raise NativeEngineeringDriverError('待选择的图形视图没有绑定当前工程。')
        view.click_input(coords=self._graph_point(grid, box[0] + .5, box[1] + 1))

    def _draw_graph_wire(self, view, grid, anchor):
        from pywinauto.keyboard import send_keys
        # Native Interconnect Mode draws one orthogonal segment per drag.
        # A vertical T requires its horizontal receiving leg to exist first.
        # Start at the receiving port and finish at the existing driver/tap.
        # Drag just inside the observed lattice cell's native hit area rather
        # than on its boundary; the saved branch witness verifies this offset.
        points = list(reversed([anchor['from']['point'], *anchor['via'], anchor['to']['point']]))
        self._command(0x644)
        for start, end in zip(points, points[1:]):
            source = self._graph_point(grid, *start)
            target = self._graph_point(grid, *end)
            self._graph_drag(view, grid, (source[0] + 2, source[1] + 2),
                             (target[0] + 2, target[1] + 2))
        send_keys('{ESC}', vk_packet=False)
        self._command(0x643)

    def _edit_fbd_graph(self, command, prepared):
        from gxw.models import GXWFormatError
        from gxw.visible_graph import graph_edit_step, verify_graph_edit_step, verify_graph_edit_group
        from pywinauto.keyboard import send_keys
        actions, index, receipts = prepared['plan']['steps'], 0, []
        self._graph_receipts = receipts
        while index < len(actions):
            # The Core rebinds complete saved records before every native edit;
            # offsets from earlier saves are never used as persistent IDs.
            anchor = graph_edit_step(self._last_saved, prepared, index)
            block = anchor.get('node', anchor.get('wire', anchor.get('from', {}))).get('block')
            if block != 0:
                raise NativeEngineeringDriverError('此原生视图尚未验证其它图形块的格点定位。')
            name = prepared['owner']
            view = self._graph_view(name)
            grid = self._graph_grid(view)
            self._command(0x643)
            action = anchor['action']
            if action in ('rebind_terminal', 'delete_terminal', 'move_node'):
                box = anchor['node']['bbox']
                point = self._graph_point(grid, box[0] + .5, box[1] + 1)
                if action == 'rebind_terminal':
                    view.double_click_input(coords=point)
                    dialogs = self._dialogs()
                    if not dialogs:
                        self._command(0x60F)
                    elif len(dialogs) != 1 or dialogs[0].window_text() != '标签登录/选择':
                        raise NativeEngineeringDriverError('原生端子编辑出现了未确认的对话框。')
                    self._choose_label(anchor['symbol'])
                else:
                    if action == 'delete_terminal':
                        # Delete on the label clears its text; the verified
                        # context Cut removes precisely this terminal graphic
                        # and leaves existing wires for Core to check.
                        popup = self._graph_context(view, grid, box)
                        send_keys('t', vk_packet=False)
                        self._wait(lambda: not popup(), '原生端子剪切没有结束。')
                    else:
                        self._select_graph_node(view, grid, box)
                        self._graph_drag(view, grid, point,
                            self._graph_point(grid, anchor['x'] + .5, anchor['y'] + 1))
                        # Native text extents can overlap even when the saved
                        # node boxes only touch. Continue this one observed
                        # layout notice, then require the exact Core checkpoint.
                        ready_since = [None]
                        def moved():
                            dialogs = self._dialogs()
                            if dialogs:
                                if (len(dialogs) != 1 or self._dialog_text(dialogs[0]) !=
                                        '梯形图图形重叠。是否继续处理？'):
                                    raise NativeEngineeringDriverError('图形移动出现了未确认的原生提示。')
                                self._visible(name + ' 图形布局提示', dialog=dialogs[0],
                                              action=action, index=index)
                                self._click(dialogs[0], 6)
                                ready_since[0] = None
                                return False
                            if not self._window().is_enabled():
                                ready_since[0] = None
                                return False
                            if ready_since[0] is None:
                                ready_since[0] = time.monotonic()
                            return time.monotonic() - ready_since[0] >= .25
                        self._wait(moved, '原生图形移动尚未返回编辑状态。')
            elif action == 'delete_wire':
                for segment in anchor['wire']['segments']:
                    view.click_input(coords=self._graph_point(grid, *segment['point']))
                    self._command(0x18CD)
            elif action == 'add_wire':
                self._draw_graph_wire(view, grid, anchor)
            else:
                raise NativeEngineeringDriverError('没有明确的原生图形编辑动作。')
            checked = []
            def complete(raw):
                try:
                    verification = verify_graph_edit_step(raw, prepared, index)
                    length = 1
                except GXWFormatError:
                    if action != 'move_node' or index + 1 >= len(actions):
                        raise
                    verification = verify_graph_edit_group(raw, prepared, index, index + 1)
                    length = 2
                checked[:] = [verification, length]
            saved = self._save_until(complete)
            if self.evidence_directory:
                (self.evidence_directory / f'graph-{index:03d}.gxw').write_bytes(saved)
            # A coupled move is accepted only at the complete checkpoint of
            # the explicitly requested adjacent connection, never by guessing.
            verified, consumed = checked
            visible = self._visible(name + ' FBD 编辑器', action=action,
                indices=list(range(index, index + consumed)), verified_saved_source=True)
            receipts.append({**verified, 'visible_completion': visible})
            index += consumed
        return {'visible_completion': visible, 'graph_actions': receipts}

    def _create_fbd_call(self, command, plan):
        from gxw.object_model import read_project_context
        caller = command['caller']
        context = read_project_context(self._last_saved, caller)
        name = context.declarations[context.sources.local_table].owner_name
        view = self._graph_view(name)
        grid = self._graph_grid(view)
        model = context.object_model()
        occupied = model['nodes']
        # Native insertion creates its own source records and adjacent argument
        # terminals. Only the empty visible space is chosen here; Core verifies
        # the resulting instance, interface and connection graph afterwards.
        # A saved native block does not accept insertions in the white area
        # below its dot grid. Use the visible empty columns beside its current
        # objects; inserting rows through them would move unrelated objects.
        next_x = max((n['x'] + n['width'] + 10 for n in occupied), default=5)
        placement = self._graph_point(grid, next_x, 1)
        self._command(0x6AA)
        dialog = self._dialog('指令输入')
        combo = dialog.child_window(control_id=6145, class_name='ComboBox')
        self.ui._select_native_combo(combo, 'FB')
        editor = dialog.child_window(control_id=6144, class_name='Edit')
        editor.set_focus()
        import win32gui
        win32gui.SendMessage(editor.handle, 0x00B1, 0, -1)
        editor.type_keys(command['definition'], pause=.01)
        if editor.window_text() != command['definition']:
            raise NativeEngineeringDriverError('原生指令输入中的 FB 类型回读不同。')
        self._click(dialog, 1)
        self._wait(lambda: not self._dialogs(), '原生指令输入没有接受当前 FB 定义。')
        self.inspect()
        view.click_input(coords=placement)
        self._choose_label(command['instance'], expected_type=command['definition'])
        self._visible(name + ' FBD 实例', instance=command['instance'])
        def instance_saved(raw):
            nodes = read_project_context(raw, caller).object_model()['nodes']
            matches = [n for n in nodes if n['symbol'] == command['instance']
                       and n['template'] == 'function_block:' + command['definition']]
            if len(matches) != 1:
                raise NativeEngineeringDriverError('新建 FBD 实例尚未完成保存。')
        self._save_until(instance_saved)
        bindings = []
        for formal, symbol in command['arguments'].items():
            current = read_project_context(self._last_saved, caller).object_model()
            blocks = [n for n in current['nodes'] if n['symbol'] == command['instance']
                      and n['template'] == 'function_block:' + command['definition']]
            if len(blocks) != 1:
                raise NativeEngineeringDriverError('新建 FBD 实例回读不唯一。')
            block = blocks[0]
            ports = [p for p in block['ports'] if p['name'] == formal]
            if len(ports) != 1:
                raise NativeEngineeringDriverError('原生 FB 端口与本步骤接口不同：' + formal)
            port = ports[0]
            pin = (block['x'] + port['x'], block['y'] + port['y'])
            kind = 'input' if port['side'] == 'in' else 'output'
            terminals = [n for n in current['nodes'] if n['template'] == kind and any(
                (n['x'] + p['x'], n['y'] + p['y']) == pin for p in n['ports'])]
            if len(terminals) != 1:
                raise NativeEngineeringDriverError('没有唯一的原生参数连接端。')
            terminal = terminals[0]
            view = self._graph_view(name)
            grid = self._graph_grid(view)
            x = terminal['x'] + .5
            point = self._graph_point(grid, x, terminal['y'] + 1)
            view.click_input(coords=point)
            self._command(0x60F)
            binding = self._choose_label(symbol)
            bindings.append({'formal': formal, **binding})
            def terminal_saved(raw):
                nodes = read_project_context(raw, caller).object_model()['nodes']
                terminals = [n for n in nodes if n['template'] == kind and n['symbol'] == symbol
                             and any((n['x'] + p['x'], n['y'] + p['y']) == pin for p in n['ports'])]
                if len(terminals) != 1:
                    raise NativeEngineeringDriverError('本次 FBD 参数连接尚未完成保存。')
            self._save_until(terminal_saved)
            self._visible(name + ' FBD 参数连接', formal=formal, symbol=symbol)
        return self._visible(name + ' FBD 编辑器', instance=command['instance'], bindings=bindings)

    def _configure(self, plan):
        # CPU-specific controls below have independent native FX3U observations.
        # Other CPU menus are deliberately left unverified, even though they
        # share the offline Core configuration planner.
        if self.cpu != 'FX3U/FX3UC' or 'parameter_after' in plan:
            raise NativeEngineeringDriverError('此前台分配驱动只验证了 FX3U/FX3UC 范围设置。')
        controls = {'D': (14069, 14072), 'M': (14096, 14099)}
        if any(row['device'] not in controls or any(v < 0 for v in row['after']) for row in plan['ranges']):
            raise NativeEngineeringDriverError('此前台范围字段或禁用方式尚未验证。')
        self._command(0x685)
        dialog = self._dialog('自动分配软元件设置')
        for row in plan['ranges']:
            for control_id, expected, after in zip(controls[row['device']], row['before'], row['after']):
                editor = dialog.child_window(control_id=control_id, class_name='Edit')
                if editor.window_text() != str(expected):
                    raise NativeEngineeringDriverError('活动自动分配范围与当前 Core 配置不同。')
                editor.set_edit_text(str(after))
                if editor.window_text() != str(after):
                    raise NativeEngineeringDriverError('原生自动分配字段回读不同。')
        self._click(dialog, 1)
        notices = []
        deadline = time.monotonic() + self.timeout
        while self._dialogs() and time.monotonic() < deadline:
            dialogs = [d for d in self._dialogs() if d.is_enabled()]
            handled = False
            for current in dialogs:
                text = self._dialog_text(current)
                if ('设置与PLC参数中的锁存设置范围重复。' in text
                        or text.startswith('自动分配软元件设置被更改，因此需要全部编译工程。')):
                    notices.append(text)
                    self._click(current, 6)
                    handled = True
                    break
            if not handled:
                time.sleep(.05)
        self._wait(lambda: not self._dialogs(), '自动分配范围没有完成，保留原生提示。')
        self._command(0x685)
        dialog = self._dialog('自动分配软元件设置')
        for row in plan['ranges']:
            for control_id, expected in zip(controls[row['device']], row['after']):
                if dialog.child_window(control_id=control_id, class_name='Edit').window_text() != str(expected):
                    raise NativeEngineeringDriverError('重新打开的原生分配范围与本步设置不同。')
        visible = self._visible('自动分配软元件设置', dialog=dialog,
                                ranges=plan['ranges'], notices=notices)
        self._click(dialog, 2)
        self._wait(lambda: not self._dialogs(), '已展示的分配设置页尚未关闭。')
        return visible

    def _compile(self, plan):
        self._idle()
        # Clear only the native output summary control before dispatch. A
        # previous successful build cannot satisfy this operation's completion.
        # The output can be an inactive page in a floating docking group.
        # Clear that actual control too; the compiler will activate its page.
        summaries = self._owned_control_handles('Edit', 10013, visible=False)
        if len(summaries) != 1:
            raise NativeEngineeringDriverError('原生编译输出摘要控件不明确。')
        summary = self.ui._application(self.session).window(handle=summaries[0])
        import win32gui
        win32gui.SendMessage(summary.handle, 0x000C, 0, '')
        if summary.window_text():
            raise NativeEngineeringDriverError('无法建立本次编译的新输出完成点。')
        self._command(0x4A1)
        def prompt():
            rows = [d for d in self._dialogs() if d.is_enabled() and '是否执行全部编译？' in self._dialog_text(d)]
            return rows[0] if len(rows) == 1 else None
        dialog = self._wait(prompt, '没有出现本次全部编译确认页。')
        checks = dialog.child_window(control_id=12270, class_name='Button')
        if not checks.get_check_state():
            self._click(dialog, 12270)
        if checks.get_check_state() != 1:
            raise NativeEngineeringDriverError('原生编译后的程序检查没有启用。')
        self._click(dialog, 1)
        def completed():
            output = summary.window_text()
            if (win32gui.IsWindowVisible(summary.handle)
                    and '已完成全部编译' in output and re.search(r'\bError\s*:\s*\d+', output)
                    and self._window().is_enabled() and not self._dialogs()):
                return output
            return None
        output = self._wait(completed, '本次原生全部编译尚未完成。')
        self.snapshot()
        return {'command': 'rebuild_all', 'output': output, 'completed': True,
                'source_unchanged': True, 'saved': True, 'pid': self.session.process_id,
                'revision': plan['revision'], 'public_checks_requested': True,
                'visible_completion': self._visible('本次原生编译输出', output=output)}

    def _owned_control_handles(self, class_name, control_id, *, visible=True):
        import win32gui
        import win32process
        roots = []
        def visit(handle, _):
            if ((not visible or win32gui.IsWindowVisible(handle))
                    and win32process.GetWindowThreadProcessId(handle)[1] == self.session.process_id
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

    def _cross_reference_panel(self, *, allow_missing=False):
        import win32gui
        controls = self._owned_control_handles('ComboBox', 20102)
        if not controls and allow_missing:
            return None
        if len(controls) != 1:
            raise NativeEngineeringDriverError('本工程的交叉引用搜索页面不明确。')
        return self.ui._application(self.session).window(handle=win32gui.GetParent(controls[0]))

    def _open_cross_reference(self):
        panel = self._cross_reference_panel(allow_missing=True)
        if panel is not None:
            return panel
        # Codejock routes this menu through its active command bar. A direct
        # frame WM_COMMAND can return without opening the search pane.
        self._choose_native_menu([('%f', '搜索/替换(F)')], 's')
        return self._wait(lambda: self._cross_reference_panel(allow_missing=True),
                          '原生交叉参照窗口没有打开。')

    def _choose_native_menu(self, entries, final_key):
        import win32gui
        import win32process
        from pywinauto.keyboard import send_keys
        self._foreground()
        for key, caption in entries:
            send_keys(key, pause=0, vk_packet=False)
            def popup():
                handles = []
                def visit(handle, _):
                    if (win32gui.IsWindowVisible(handle)
                            and win32process.GetWindowThreadProcessId(handle)[1] == self.session.process_id
                            and win32gui.GetClassName(handle) == 'XTPPopupBar'
                            and win32gui.GetWindowText(handle) == caption):
                        handles.append(handle)
                win32gui.EnumWindows(visit, None)
                if len(handles) > 1:
                    raise NativeEngineeringDriverError('原生菜单不唯一。')
                return handles[0] if handles else None
            handle = self._wait(popup, '原生菜单没有打开：' + caption)
            if win32gui.GetForegroundWindow() != self.session.window_handle:
                raise NativeEngineeringDriverError('菜单打开期间前台工程发生变化。')
            if not win32gui.IsWindowVisible(handle):
                raise NativeEngineeringDriverError('原生菜单在选择前已经关闭。')
        send_keys(final_key, pause=0, vk_packet=False)

    def _show_assignments(self, plan):
        import win32gui
        from copy import deepcopy
        queries = plan.get('queries')
        if (not isinstance(queries, list) or not 1 <= len(queries) <= 32
                or any(q.get('scope') != 'global' for q in queries)):
            raise NativeEngineeringDriverError('前台地址展示需要 1 至 32 个明确的全局标签查询。')
        panel = self._open_cross_reference()
        # Use the native docking caption to float the real result pane, then
        # resize it so the address row is actually visible beside the graph.
        container = panel
        while container.handle != self.session.window_handle and container.class_name() not in (
                'XTPDockingPaneTabbedContainer', 'XTPDockingPaneMiniWnd'):
            container = container.parent()
        if container.class_name() == 'XTPDockingPaneTabbedContainer':
            win32gui.SendMessage(container.handle, 0x0203, 1, (16 << 16) | 100)
            panel = self._cross_reference_panel()
        floating = panel
        while floating.handle != self.session.window_handle and floating.class_name() != 'XTPDockingPaneMiniWnd':
            floating = floating.parent()
        if floating.class_name() != 'XTPDockingPaneMiniWnd':
            raise NativeEngineeringDriverError('原生地址结果页面没有形成可见窗口。')
        frame = self._window().rectangle()
        width, height = min(1450, frame.width() - 80), min(570, frame.height() - 250)
        win32gui.SetWindowPos(floating.handle, 0, frame.left + 40,
                             frame.bottom - height - 60, width, height, 0x0014)
        results = []
        for query in queries:
            panel = self._cross_reference_panel()
            self._foreground(floating.handle)
            search = panel.child_window(control_id=20102, class_name='ComboBox')
            summary = panel.child_window(control_id=20304, class_name='Edit')
            win32gui.SendMessage(summary.handle, 0x000C, 0, '')
            editor = search.child_window(class_name='Edit')
            editor.set_focus()
            editor.set_edit_text(query['name'])
            search.notify_parent(5)  # CBN_EDITCHANGE, like editing the native search box
            panel.child_window(control_id=20101, class_name='Button').set_focus()
            panel.child_window(control_id=20101, class_name='Button').click_input()
            text = self._wait(lambda: summary.window_text() if ('"' + query['name'] + '"')
                              in summary.window_text() else None, '本次交叉引用搜索没有完成。')
            grids = [c for c in panel.descendants(class_name='VSFlexGrid8N') if c.is_visible()]
            if len(grids) != 1 or grids[0].rectangle().height() < 120:
                raise NativeEngineeringDriverError('原生地址结果行没有足够的可见区域。')
            rows = _accessible_grid_rows(grids[0].handle)
            completion = self._visible('本次编译的交叉引用', query=query['name'], rows=rows)
            results.append({'query': deepcopy(query), 'search_text': search.window_text(),
                            'summary': text, 'rows': rows, 'visible_completion': completion})
        return {'cross_references': results, 'pid': self.session.process_id,
                'revision': plan['revision'], 'visible_completion': completion}

    def reopen(self):
        saved = self.snapshot()
        self._command(0x401)
        self._wait(lambda: not self._frame_identity().project_open and not self._dialogs(), '工程未冷关闭，未丢弃任何修改。')
        self._command(0x400, project=False)
        self._file_name(self._dialog('打开工程'), self.path)
        self._wait(lambda: self.path.name.casefold() in self._window().window_text().casefold()
                   and not self._dialogs(), '保存的工程没有重新打开。')
        self.inspect()
        reopened = self.snapshot()
        self._visible('保存工程冷重开', path=str(self.path), same_process=True)
        return reopened

    def close(self):
        # Closing the Python controller does not close an operator's IDE or
        # erase an incomplete native operation. The final page stays visible.
        if self._file_use is not None:
            self._file_use.close()
        self._closed = True

    def execute(self, command, prepared):
        self._idle()
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

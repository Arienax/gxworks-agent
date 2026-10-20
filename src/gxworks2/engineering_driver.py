"""Visible, process-bound GX Works2 engineering operations.

The caller owns the desktop lock and COM apartment for the whole session.
Core prepares and verifies domain changes. This driver operates native pages;
it never replaces a live project with an offline Workspace export.
"""
from __future__ import annotations

import csv
from contextlib import nullcontext
import io
import os
from pathlib import Path
import re
import tempfile
import threading
import time

from .finder import GXWorks2Finder
from .project_profiles import GXWORKS2_NATIVE_VERSION, native_project_profile
from .native_locale import (inspect_native_language, native_caption, native_menu_selection,
                            native_tree_path, standard_yes_no_captions, NativeLanguageNotReadyError)
from .ui_automation import PywinautoGXWorks2UIAutomation
from gxw.native_locale import (canonical_label_columns, label_title, native_language,
                               program_title, rebuild_completion_markers, strict_project_text, ui_text)


class NativeEngineeringDriverError(RuntimeError):
    def __init__(self, message, *, observation=None):
        super().__init__(message)
        self.observation = observation or {}


def _same_text(left, right):
    return left.replace('\r\n', '\n').replace('\r', '\n') == right.replace('\r\n', '\n').replace('\r', '\n')


def _label_display_type(data_type):
    # Native chooser display strings witnessed independently of CSV spelling.
    return {'BOOL': 'Bit', 'INT': 'Word[Signed]'}.get(data_type, data_type)


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
    from gxw.active_context import parse_active_label_csv
    from gxw.models import GXWFormatError
    try:
        return parse_active_label_csv(raw)
    except GXWFormatError as error:
        raise NativeEngineeringDriverError(str(error)) from error


def _merge_label_csv(raw, rows, *, structure=False, native_ui_language='zh-CN'):
    """Mechanical column transport; types/defaults have already passed Core."""
    title, header, previous = _label_csv(raw)
    native_header = header
    header = canonical_label_columns(header, language=native_ui_language)
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
    writer.writerow(native_header)
    writer.writerows(result)
    return output.getvalue().encode('utf-16')


def _grid_calibration(pixels, *, include_lattice=False):
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
    # Text can hide the vertical neighbour of a boundary dot. Keep that dot
    # as evidence for alias checking only when an observed immediate neighbour
    # already belongs to the fine lattice. Do not extend the lattice repeatedly:
    # a separate, compatible coarse grid must remain ambiguous.
    for candidate in candidates:
        pitch, points = candidate['pitch'], candidate['points']
        candidate['alias_points'] = points | {
            (x, y) for x, y in dots
            if (x % pitch, y % pitch) == candidate['phase'] and any(
                point in points for point in ((x-pitch, y), (x+pitch, y),
                                               (x, y-pitch), (x, y+pitch)))}
    # A complete lattice also supports integral multiples of its period. Only
    # discard such an alias when all its observed rectangles are explained by
    # the finer lattice; a second independent grid must remain ambiguous.
    candidates = [candidate for candidate in candidates if not any(
        candidate['pitch'] > other['pitch']
        and candidate['pitch'] % other['pitch'] == 0
        and candidate['phase'][0] % other['pitch'] == other['phase'][0]
        and candidate['phase'][1] % other['pitch'] == other['phase'][1]
        and candidate['proof'] <= other['alias_points']
        for other in candidates)]
    horizontal_limit = None
    grid = candidates[0] if len(candidates) == 1 else None
    if len(candidates) == 2:
        left, right = sorted(candidates, key=lambda item: min(x for x, _ in item['proof']))
        pitch = left['pitch']
        phase_distance = min((left['phase'][0] - right['phase'][0]) % pitch,
                             (right['phase'][0] - left['phase'][0]) % pitch)
        if (right['pitch'] == pitch and left['phase'][1] == right['phase'][1]
                and left['y'] == right['y'] and phase_distance == 1):
            sides = []
            for candidate in (left, right):
                rows = defaultdict(set)
                for x, y in candidate['proof']:
                    rows[y].add(x)
                sides.append(rows)
            shared = sorted(sides[0].keys() & sides[1].keys())
            seams = [(max(sides[0][y]), min(sides[1][y])) for y in shared]
            # Wide native captures can shift one horizontal raster pixel at a
            # narrow vertical boundary. Prove adjacent, equal-period rows on
            # both sides; unrelated or overlapping lattices remain ambiguous.
            if (len(seams) >= 3 and all(pitch - 1 <= end - start <= 2 * pitch + 1
                                      for start, end in seams)
                    and max(end for _, end in seams) - min(end for _, end in seams) <= pitch):
                limit = min(x for x, _ in right['points']) - pitch
                stable_rows = [xs for y, xs in sides[0].items() if any(
                    all(x + column * pitch in xs and x + column * pitch < limit for column in range(8))
                    for x in xs)]
                if len(stable_rows) >= 3:
                    grid, horizontal_limit = left, limit
    if grid is None:
        raise NativeEngineeringDriverError('无法唯一确认当前 FBD 页面的二维原生网格。')
    result = {'pitch': grid['pitch'], 'x': grid['x'], 'y': grid['y'],
              'width': width, 'height': height}
    if horizontal_limit is not None:
        # Only the independently observed left area has this coordinate map.
        # Never extrapolate it into the raster transition or the other phase.
        result['calibrated_x_limit'] = horizontal_limit
    if include_lattice:
        # Observe only the phase established above; another network can have
        # its own vertical origin. A white view below a network is not cells.
        def dot(x, y):
            if max(samples[x, y]) >= 180:
                return False
            seen, pending = {(x, y)}, [(x, y)]
            while pending:
                px, py = pending.pop()
                for dx, dy in ((-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1)):
                    point = (px+dx, py+dy)
                    if point in seen or not (0 <= point[0] < width and 0 <= point[1] < height):
                        continue
                    if max(samples[point[0], point[1]]) < 180:
                        seen.add(point)
                        if (len(seen) > 4 or max(a for a,b in seen)-min(a for a,b in seen)>1
                                or max(b for a,b in seen)-min(b for a,b in seen)>1):
                            return False
                        pending.append(point)
            return min(a for a,b in seen) == x and min(b for a,b in seen) == y
        points, rows = set(), []
        for y in range(grid['y']+grid['pitch'], height-3, grid['pitch']):
            row = {x for x in range(grid['x']+grid['pitch'], width-24, grid['pitch'])
                   if x >= 3 and y >= 3 and dot(x,y)}
            if not any(all(x+i*grid['pitch'] in row for i in range(8)) for x in row):
                break
            rows.append(y)
            points.update((x,y) for x in row)
        if len(rows) < 2:
            raise NativeEngineeringDriverError('首个网络没有可确认的连续原生点阵。')
        result['first_lattice_band'] = [rows[0], rows[-1]]
        result['lattice_points'] = sorted([x,y] for x,y in points)
    return result


def _accessible_grid_rows(handle, *, native_ui_language='zh-CN'):
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
        result.append({'native_row': int(native_row), **{key: unique(ui_text(title, native_ui_language)) for key, title in (
            ('label', 'xref_label'), ('address', 'column_device'), ('program', 'xref_program'),
            ('pou', 'xref_data'), ('location', 'xref_position'), ('read_write', 'xref_rw'))}})
    return result


def _allocation_accessible(handle):
    """Obtain a fresh public MSAA object; native grid objects can be snapshots."""
    import ctypes
    from comtypes.automation import IDispatch
    from comtypes.client.dynamic import Dispatch
    interface = ctypes.POINTER(IDispatch)()
    api = ctypes.windll.oleacc.AccessibleObjectFromWindow
    api.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
    api.restype = ctypes.c_long
    status = api(handle, ctypes.c_uint32(0xFFFFFFFC), ctypes.byref(IDispatch._iid_),
                 ctypes.byref(interface))
    if status or not interface:
        raise NativeEngineeringDriverError('原生分配表没有提供可核对的行接口。')
    return Dispatch(interface)


def _allocation_grid_rows(handle):
    grid = _allocation_accessible(handle)
    if grid.accRole[0] != 24 or not 3 <= grid.accChildCount <= 544:
        raise NativeEngineeringDriverError('原生分配表的身份或行数尚未验证。')
    rows = []
    for index in range(1, grid.accChildCount + 1):
        row = grid.accChild[index]
        if row.accRole[0] == 25:
            continue  # MSAA also exposes one object per column header.
        value = row.accValue[0]
        if (row.accRole[0] != 28 or not isinstance(value, str) or not value.isdecimal()
                or int(value) != len(rows) or not 3 <= row.accChildCount <= 16):
            raise NativeEngineeringDriverError('原生分配表没有连续且唯一的实际行列。')
        cells = []
        for column in range(1, row.accChildCount + 1):
            cell = row.accChild[column]
            name, text, state = cell.accName[0], cell.accValue[0], cell.accState[0]
            if (cell.accRole[0] != 29 or not isinstance(name, str) or not isinstance(text, str)
                    or type(state) is not int):
                raise NativeEngineeringDriverError('原生分配单元格的列归属或文本不明确。')
            cells.append(dict(name=name, value=text, state=state))
        rows.append(dict(native_row=int(value), cells=cells))
    return rows


def _native_focus(handle):
    """Read the owning GUI thread, which can differ from the main frame thread."""
    import ctypes
    from ctypes import wintypes as w
    import win32process
    class GuiThreadInfo(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('flags', w.DWORD), ('active', w.HWND),
                    ('focus', w.HWND), ('capture', w.HWND), ('menu', w.HWND),
                    ('move', w.HWND), ('caret', w.HWND), ('caret_rect', w.RECT)]
    value = GuiThreadInfo()
    value.size = ctypes.sizeof(value)
    if not ctypes.windll.user32.GetGUIThreadInfo(win32process.GetWindowThreadProcessId(handle)[0],
                                                ctypes.byref(value)):
        raise NativeEngineeringDriverError('无法确认原生编辑器的实际线程焦点。')
    return value.focus


def _allocation_cell(handle, native_row, native_column):
    """Resolve a cell again after every focus/edit operation, never by heap address."""
    grid = _allocation_accessible(handle)
    if grid.accRole[0] != 24 or not 3 <= grid.accChildCount <= 544:
        raise NativeEngineeringDriverError('原生分配表的身份或行数已改变。')
    matches = []
    for index in range(1, grid.accChildCount + 1):
        row = grid.accChild[index]
        if row.accRole[0] == 28 and row.accValue[0] == str(native_row):
            matches.append(row)
    if len(matches) != 1 or matches[0].accChildCount != 8 or not 0 <= native_column < 8:
        raise NativeEngineeringDriverError('原生分配字段没有唯一的当前单元格。')
    cell = matches[0].accChild[native_column + 1]
    if cell.accRole[0] != 29:
        raise NativeEngineeringDriverError('原生分配字段的可访问角色已改变。')
    return grid, cell


def _allocation_cell_point(frame, handle, native_row, native_column):
    import ctypes
    from ctypes import wintypes as w
    from comtypes.client import GetModule
    import win32gui
    GetModule('oleacc.dll')
    from comtypes.gen.Accessibility import IAccessible
    grid, cell = _allocation_cell(handle, native_row, native_column)
    root_box = grid._comobj.QueryInterface(IAccessible).accLocation(0)
    cell_box = cell._comobj.QueryInterface(IAccessible).accLocation(0)
    api = ctypes.windll.user32.LogicalToPhysicalPointForPerMonitorDPI
    api.argtypes = [w.HWND, ctypes.POINTER(w.POINT)]
    api.restype = w.BOOL
    def convert(x, y):
        point = w.POINT(x, y)
        # The top-level frame is the independently observed conversion anchor.
        if not api(frame, ctypes.byref(point)):
            raise NativeEngineeringDriverError('无法确认分配表格的原生 DPI 坐标。')
        return point.x, point.y
    x, y, width, height = root_box
    physical = (*convert(x, y), *convert(x + width, y + height))
    actual = win32gui.GetWindowRect(handle)
    if any(abs(a - b) > tolerance for a, b, tolerance in zip(physical, actual, (2, 2, 8, 8))):
        raise NativeEngineeringDriverError('分配表的可访问坐标与实际窗口不同。')
    x, y, width, height = cell_box
    left, top = convert(x, y)
    right, bottom = convert(x + width, y + height)
    if not (actual[0] <= left < right <= actual[2] and actual[1] <= top < bottom <= actual[3]):
        raise NativeEngineeringDriverError('请求的原生分配单元格当前不可见。')
    point = ((left + right) // 2, (top + bottom) // 2)
    hit = win32gui.WindowFromPoint(point)
    if hit != handle and not win32gui.IsChild(handle, hit):
        raise NativeEngineeringDriverError('请求的分配单元格被其他窗口覆盖。')
    return point


def _compile_diagnostic_rows(rows, *, native_ui_language='zh-CN'):
    """Project the witnessed diagnostic columns, not their dynamic control ID."""
    from gxw.native_locale import native_diagnostic_severity
    from gxw.models import GXWFormatError
    titles = ('No.', *(ui_text('diagnostic_' + key, native_ui_language)
                       for key in ('result', 'data', 'class', 'content', 'code')))
    canonical_titles = ('No.', '结果', '数据名', '分类', '内容', '错误代码')
    actual = [row for row in rows if row.get('role') == 28]
    headers = [row for row in actual if row.get('value') == '0']
    if len(headers) != 1:
        return None
    header = headers[0].get('cells', [])
    selection_column = bool(header and header[0].get('name') == '.' and header[0].get('value') == '.')
    if selection_column:
        # GX Works2's floating ST output grid exposes a leading selection
        # column. It is not a diagnostic field; all semantic columns still
        # have to match the witnessed localized header exactly.
        if header[0].get('role') != 29:
            return None
        header = header[1:]
    names = [cell.get('name') or '' for cell in header]
    if tuple(name for name in names if name) != titles:
        return None
    if any(cell.get('role') != 29 or (cell.get('value') or '') != name
           for cell, name in zip(header, names)):
        raise NativeEngineeringDriverError('本次编译诊断表头的实际名称与值不同。')
    result = []
    for row in actual:
        value = row.get('value')
        if value == '0':
            continue
        if (not isinstance(value, str) or not value.isdecimal()
                or int(value) != len(result) + 1 or int(value) > 4096):
            raise NativeEngineeringDriverError('本次编译诊断没有连续且唯一的实际行号。')
        cells = row.get('cells', [])
        if selection_column:
            if (not cells or cells[0].get('name') != '.' or cells[0].get('role') != 29
                    or cells[0].get('value') is not None and not isinstance(cells[0].get('value'), str)):
                raise NativeEngineeringDriverError('本次编译诊断的选择列归属已改变。')
            cells = cells[1:]
        if len(cells) != len(header):
            raise NativeEngineeringDriverError('本次编译诊断的实际列数已改变。')
        fields = {}
        for cell, name in zip(cells, names):
            text = cell.get('value')
            if (cell.get('role') != 29 or (cell.get('name') or '') != name
                    or text is not None and not isinstance(text, str)
                    or not name and text not in ('', None)):
                raise NativeEngineeringDriverError('本次编译诊断的列归属或文本不可确认。')
            if name:
                fields[canonical_titles[titles.index(name)]] = text or ''
        try:
            severity = native_diagnostic_severity(fields['结果'], language=native_ui_language)
        except GXWFormatError as error:
            raise NativeEngineeringDriverError('本次编译诊断的结果类别与原生界面语言不同。') from error
        if fields['No.'] != value:
            raise NativeEngineeringDriverError('本次编译诊断的编号或结果类别尚未验证。')
        result.append(dict(native_row=int(value), no=fields['No.'], severity=severity, raw_severity=fields['结果'],
            data_name=fields['数据名'], category=fields['分类'], message=fields['内容'], code=fields['错误代码']))
    return result


def _accessible_compile_diagnostic_rows(handle, *, native_ui_language='zh-CN'):
    """Read MSAA rows: native row 0 is the header and row 1 is actual data."""
    import ctypes
    from comtypes.automation import IDispatch
    from comtypes.client.dynamic import Dispatch
    interface = ctypes.POINTER(IDispatch)()
    status = ctypes.windll.oleacc.AccessibleObjectFromWindow(
        handle, ctypes.c_uint32(0xFFFFFFFC), ctypes.byref(IDispatch._iid_), ctypes.byref(interface))
    if status or not interface:
        raise NativeEngineeringDriverError('原生编译诊断没有提供可核对的行接口。')
    grid = Dispatch(interface)
    if not 1 <= grid.accChildCount <= 4104:
        raise NativeEngineeringDriverError('原生编译诊断数量超出本次回读范围。')
    rows = []
    for index in range(1, grid.accChildCount + 1):
        row = grid.accChild[index]
        if row.accRole[0] != 28:
            continue  # The surrounding Role25 objects describe columns.
        if not 1 <= row.accChildCount <= 64:
            raise NativeEngineeringDriverError('原生编译诊断列数尚未验证。')
        cells = []
        for column in range(1, row.accChildCount + 1):
            cell = row.accChild[column]
            cells.append(dict(name=cell.accName[0], value=cell.accValue[0], role=cell.accRole[0]))
        rows.append(dict(role=28, value=str(row.accValue[0]), cells=cells))
    return _compile_diagnostic_rows(rows, native_ui_language=native_ui_language)


def _compile_summary_counts(output, *, public_checks_requested=None, native_ui_language=None):
    if not isinstance(output, str) or not any(marker in output for marker in rebuild_completion_markers(native_ui_language)):
        return None
    counts = {}
    for name in ('Error', 'Warning', 'CheckWarning'):
        matches = re.findall(r'(?<![A-Za-z])' + name + r'\s*:\s*([0-9]+)', output, re.IGNORECASE)
        if not matches:
            if name == 'CheckWarning' and public_checks_requested is False:
                counts[name] = None  # GX omits this field when public checks were not requested.
                continue
            return None
        counts[name] = int(matches[-1])
    return counts


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
        driver = cls._bind_started_frame(session, path, cpu, timeout=timeout, progress=progress,
                                        evidence_directory=evidence_directory, finder=finder)
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
            from gxw.fb_authoring import _context
            codepage = _context(self.path.read_bytes(), require_fbd=False)[0]['codepage']
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
        expected = {self._caption(title)}
        if title == '新建数据':
            expected.add(self._text('dialog_new_data_navigation'))
        def found():
            rows = [d for d in self._dialogs() if d.window_text() in expected and d.is_enabled()]
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
        fragment = self._caption(fragment)
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
        expected_series = self._text('series_q') if profile.series[0].startswith('QCPU') else profile.series[0]
        choices = [s for s in series.item_texts() if s == expected_series]
        if len(choices) != 1:
            raise NativeEngineeringDriverError('未确认新建工程的 CPU 系列。')
        self.ui._select_native_combo(series, choices[0])
        self.ui._select_native_combo(dialog.child_window(control_id=10003, class_name='ComboBox'), self.cpu)
        kind = dialog.child_window(control_id=10001, class_name='ComboBox')
        if kind.is_enabled():
            self.ui._select_native_combo(kind, self._text('project_structured'))
        elif kind.selected_text() != self._text('project_structured'):
            raise NativeEngineeringDriverError('此 CPU 当前没有已验证的结构化工程页面。')
        self.ui._select_native_combo(dialog.child_window(control_id=10070, class_name='ComboBox'),
                                     'ST' if language == 'st' else self._text('language_fbd'))
        self._click(dialog, 1)
        creation_dialog_handle = dialog.handle
        created_ready_since = [None]
        def created():
            dialogs = self._dialogs()
            if any(current.handle == creation_dialog_handle for current in dialogs):
                created_ready_since[0] = None
                return None  # The posted native New handler is still returning.
            if dialogs:
                created_ready_since[0] = None
                self.ui._acknowledge_known_save_notices(self.session,
                    native_ui_language=getattr(self, 'native_ui_language', 'zh-CN'))
                return None
            if 'POU_01' not in self._window().window_text() or not self._window().is_enabled():
                created_ready_since[0] = None
                return None
            if created_ready_since[0] is None:
                created_ready_since[0] = time.monotonic()
            return time.monotonic() - created_ready_since[0] >= .4
        self._wait(created, '未确认原生空白 FBD 工程。')
        self._command(self.ui.SAVE_PROJECT_COMMAND, project=False)
        dialog = self._dialog('工程另存为')
        if self.path.exists():
            raise NativeEngineeringDriverError('新工程目标文件已出现，未覆盖。')
        self._file_name(dialog, self.path)
        save_dialog_handle = dialog.handle
        def initially_saved():
            dialogs = self._dialogs()
            if any(current.handle == save_dialog_handle for current in dialogs):
                return None  # File acceptance was dispatched once; never replay it.
            if dialogs:
                self.ui._acknowledge_known_save_notices(self.session,
                    native_ui_language=getattr(self, 'native_ui_language', 'zh-CN'))
                return None
            return self.path.is_file() and self.path.name.casefold() in self._window().window_text().casefold()
        self._wait(initially_saved, '未确认新工程已经保存。')
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

    def _clear_fb_instance_view(self, document, owner, expected_title):
        """Return one exact FB instance view to its definition, without Save."""
        import win32gui
        import win32process
        title = document.window_text().rstrip()
        body, fbd = self._text('nav_body'), self._text('language_fbd')
        instance = re.fullmatch(re.escape(owner) +
            r' \[([^\[\]\r\n]+\.[^\[\]\r\n]+)\] ' + re.escape(body) +
            r' \[(ST|' + re.escape(fbd) + r')\]', title)
        if instance is None or expected_title != owner + ' [FB] ' + body + ' [' + instance[2] + ']':
            raise NativeEngineeringDriverError('实例显示与明确指定的 FB 定义页面不同。')
        self._idle()
        handle, frame, pid = int(document.handle), self.session.window_handle, self.session.process_id
        def same_document():
            return (win32gui.IsWindow(handle) and win32gui.IsChild(frame, handle)
                and win32process.GetWindowThreadProcessId(handle)[1] == pid
                and self._active_document().handle == handle)
        if not same_document() or win32gui.GetWindowText(handle).rstrip() != title:
            raise NativeEngineeringDriverError('待解除的 FB 实例页面已改变。')
        # RT_MENU128/136 command0x4EF opens RT_DIALOG10000. Its Clear button
        #10001 calls native SetInstance with an empty path and ends the dialog.
        # Cache HWNDs before posting; pywinauto specs cannot resolve a destroyed
        # dialog after Clear has returned. Each message is sent once.
        self._command(0x4EF)
        dialog = self._dialog('FB实例选择')
        dialog_handle = int(dialog.handle)
        dialogs = self._dialogs()
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
                    or caption is not None and win32gui.GetWindowText(control) != self._caption(caption)):
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
            if self._dialogs():
                raise NativeEngineeringDriverError('解除 FB 实例显示后出现了未确认的原生提示。')
            if not same_document():
                raise NativeEngineeringDriverError('解除 FB 实例显示后活动文档已改变。')
            if win32gui.GetWindowText(handle).rstrip() != expected_title:
                return None
            return self._active_document()
        return self._wait(returned, '当前 FB 实例显示尚未返回同一文档的定义页面。')

    def _navigate(self, paths, title_pattern):
        self._idle()
        import win32gui
        import win32process
        result_windows = []
        def find_result(handle, _):
            if (win32gui.IsWindowVisible(handle)
                    and win32process.GetWindowThreadProcessId(handle)[1] == self.session.process_id
                    and win32gui.GetClassName(handle) == 'XTPDockingPaneMiniWnd'
                    and win32gui.GetWindowText(handle).strip() == self._caption('交叉参照')):
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
                    item = trees[0].get_item(self._tree_path(path), exact=True)
                except (IndexError, RuntimeError):
                    continue
                if item is not None:
                    matches.append(item)
            if len(matches) > 1:
                raise NativeEngineeringDriverError('原生工程对象路径不唯一。')
            return matches[0] if matches else None
        item = self._wait(tree_item, '原生工程对象路径尚未出现。')
        document = self._active_document()
        if re.search(title_pattern, document.window_text()):
            # A bound active editor can be read without stealing desktop
            # focus or reopening it (also while Simulator2 owns the desktop).
            return document
        self._foreground()
        self.ui._select_native_tree_item(trees[0], item)
        # Reading an already-open definition must not reopen its data object.
        # Native Enter can reload the RichEdit asynchronously even when the
        # caption and HWND are unchanged, invalidating the following read.
        if not self.ui._activate_native_document(self.session, re.compile(title_pattern)):
            self.ui._send_tree_enter(trees[0])
        fb_owner = None
        if (len(paths) == 1 and len(paths[0]) == 4 and tuple(paths[0][:2]) == ('程序部件', 'FB/FUN')
                and paths[0][3] == '程序本体'):
            owner = paths[0][2]
            definition_prefix = '^' + re.escape(owner + ' [FB] ' + self._text('nav_body') + ' [')
            if title_pattern.replace(r'\ ', ' ').startswith(definition_prefix.replace(r'\ ', ' ')):
                fb_owner = owner
        instance_clear_attempted = False
        def active():
            nonlocal instance_clear_attempted
            doc = self._active_document()
            if not re.search(title_pattern, doc.window_text()):
                # Prefer an already-open exact definition MDI. A native FB
                # instance can instead reuse that same MDI with a qualified
                # instance caption, so tree Enter alone cannot restore it.
                if self.ui._activate_native_document(self.session, re.compile(title_pattern)):
                    doc = self._active_document()
                elif fb_owner is not None and not instance_clear_attempted:
                    instance = re.fullmatch(re.escape(fb_owner) +
                        r' \[([^\[\]\r\n]+\.[^\[\]\r\n]+)\] ' + re.escape(self._text('nav_body')) +
                        r' \[(ST|' + re.escape(self._text('language_fbd')) + r')\]\s*', doc.window_text())
                    expected_title = fb_owner + ' [FB] ' + self._text('nav_body') + ' [' + instance[2] + ']' if instance else None
                    if expected_title and re.search(title_pattern, expected_title):
                        instance_clear_attempted = True
                        doc = self._clear_fb_instance_view(doc, fb_owner, expected_title)
            return doc if re.search(title_pattern, doc.window_text()) else None
        doc = self._wait(active, '原生页面没有切换到指定工程对象。')
        self.ui._activate_native_document(self.session, re.compile(title_pattern))
        return doc

    def _labels_page(self, name, scope):
        if scope == 'global':
            return self._navigate([('全局标签', name)], '^' + re.escape(self._text('title_global') + ' ' + name) + r'\s*$')
        if scope == 'structure':
            return self._navigate([('程序部件', '结构体', name)], '^' + re.escape(self._text('title_structure') + ' ' + name) + r'\s*$')
        owner = '程序' if scope == 'program' else 'FB/FUN'
        expected = label_title(name, scope, language=getattr(self, 'native_ui_language', 'zh-CN'))
        return self._navigate([('程序部件', owner, name, '局部标签')], '^' + re.escape(expected) + r'\s*$')

    def _program_page(self, name, kind='program', language='fbd'):
        owner = '程序' if kind == 'program' else 'FB/FUN'
        pattern = '^' + re.escape(self._program_title(name, kind, language)) + r'\s*$'
        document = self._navigate([('程序部件', owner, name, '程序本体')], pattern)
        if language != 'st':
            return document
        # GX exposes the MDI title before finishing the RichEdit reload. Take
        # the source observation only after that exact editor has settled.
        # read_st still rejects any subsequent source or identity change.
        stable = [None, None]
        def ready():
            current = self._active_document()
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
        self._wait(ready, '指定 ST 编辑器尚未完成原生文本加载。')
        return document

    def focus_graph_network(self, adapter, owner, kind, network):
        """Jump once within one currently observed native graph, without Save."""
        from gxw.active_graph import graph_source_state
        if (not isinstance(owner, str) or not owner or any(c in owner for c in '\0\r\n')
                or kind not in ('program', 'fb') or type(network) is not int or not 0 <= network < 512):
            raise NativeEngineeringDriverError('图形网络定位需要明确的 Program/FB 归属及网络编号。')
        # A retained child may still own the UI-thread critical section. No
        # GUI query or navigation is safe until that same child has returned.
        adapter._require_ready()
        sent = False
        try:
            import win32gui
            import win32process
            self._idle()
            title = self._program_title(owner, kind, 'fbd')
            if self._active_document().window_text().rstrip() != title:
                self._program_page(owner, kind, 'fbd')
            before = adapter.read_graph(self, owner=owner, kind=kind)
            source = graph_source_state(before)
            if network >= len(before['networks']):
                raise NativeEngineeringDriverError('请求的网络不在当前原生图形范围内；未发送跳转命令。')
            binding_keys = ('identity', 'data_id', 'document_handle', 'view_handle', 'owner', 'owner_kind')
            binding = {key: before.get(key) for key in binding_keys}
            if (binding['owner'] != owner or binding['owner_kind'] != kind
                    or binding['identity'] != self.inspect()
                    or not isinstance(binding['data_id'], str) or not binding['data_id']
                    or any(type(binding[key]) is not int or binding[key] <= 0
                           for key in ('document_handle', 'view_handle'))):
                raise NativeEngineeringDriverError('当前图形工程及页面绑定不完整。')
            frame, pid = self.session.window_handle, self.session.process_id

            def same_page():
                document, view = binding['document_handle'], binding['view_handle']
                return (win32gui.IsWindow(document) and win32gui.IsWindow(view)
                    and win32gui.IsChild(frame, document) and win32gui.IsChild(document, view)
                    and win32process.GetWindowThreadProcessId(document)[1] == pid
                    and win32process.GetWindowThreadProcessId(view)[1] == pid
                    and self._active_document().handle == document
                    and win32gui.GetWindowText(document).rstrip() == title)

            def checked(current):
                if ({key: current.get(key) for key in binding_keys} != binding or not same_page()
                        or graph_source_state(current) != source):
                    raise NativeEngineeringDriverError('网络定位期间工程、页面或图形源码已改变。')
                state = current.get('native_edit_state')
                if (not isinstance(state, dict) or type(state.get('inline_editor')) is not int
                        or state['inline_editor'] != 0 or type(state.get('network_clipboard_active')) is not int
                        or state['network_clipboard_active'] != 0
                        or type(current.get('active_network')) is not int
                        or not 0 <= current['active_network'] < len(current['networks'])):
                    raise NativeEngineeringDriverError('当前网络存在未结束的原生编辑或网络状态不明确。')
                return current

            checked(before)
            if before['active_network'] == network:
                return before
            current = checked(adapter.read_graph(self, owner=owner, kind=kind, data_id=binding['data_id']))
            if current['active_network'] == network:
                return current
            # BasicLib RT_DIALOG10012 is shared by the native FBD Jump command.
            # Its editable ComboBox owns Edit1001; ComboWrapper has no setter.
            sent = True
            self._command(0x606)
            def jump_dialog():
                rows = self._dialogs()
                if not rows:
                    return None
                if len(rows) != 1 or rows[0].window_text() != self._caption('跳转'):
                    raise NativeEngineeringDriverError('网络定位出现了非本次跳转的原生对话框。')
                return rows[0]
            dialog = self._wait(jump_dialog, '没有确认本次原生网络跳转对话框。')
            dialog_handle = int(dialog.handle)
            if (win32gui.GetClassName(dialog_handle) != '#32770'
                    or win32process.GetWindowThreadProcessId(dialog_handle)[1] != pid
                    or win32gui.GetAncestor(dialog_handle, 3) != frame
                    or not dialog.is_enabled() or not same_page()):
                raise NativeEngineeringDriverError('网络跳转对话框不属于当前绑定页面。')
            controls = {}
            for control_id, (class_name, caption) in {
                    10013: ('ComboBox', None), 10014: ('Button', '确定'),
                    10015: ('Button', '取消'), 10016: ('Static', '梯形图块号')}.items():
                handle = win32gui.GetDlgItem(dialog_handle, control_id)
                if (not handle or win32gui.GetParent(handle) != dialog_handle
                        or win32process.GetWindowThreadProcessId(handle)[1] != pid
                        or win32gui.GetClassName(handle) != class_name
                        or caption is not None and win32gui.GetWindowText(handle) != self._caption(caption)):
                    raise NativeEngineeringDriverError('网络跳转对话框的原生控件结构不同。')
                controls[control_id] = int(handle)
            combo = dialog.child_window(control_id=10013, class_name='ComboBox').wrapper_object()
            editors = combo.children(class_name='Edit')
            if (int(combo.handle) != controls[10013] or len(editors) != 1
                    or editors[0].control_id() != 1001):
                raise NativeEngineeringDriverError('网络跳转没有唯一的原生编号输入框。')
            editor = editors[0]
            edit_handle = int(editor.handle)
            if (win32gui.GetParent(edit_handle) != controls[10013]
                    or win32process.GetWindowThreadProcessId(edit_handle)[1] != pid
                    or not all(win32gui.IsWindowVisible(handle) and win32gui.IsWindowEnabled(handle)
                               for handle in (controls[10013], controls[10014], edit_handle))):
                raise NativeEngineeringDriverError('网络跳转编号输入框不可用或归属不明确。')
            requested = str(network + 1)
            editor.set_edit_text(requested)
            def input_matches():
                dialogs = self._dialogs()
                if (not same_page() or not win32gui.IsWindow(dialog_handle)
                        or [int(row.handle) for row in dialogs] != [dialog_handle]):
                    raise NativeEngineeringDriverError('填写网络编号期间绑定页面或对话框已改变。')
                return editor.window_text() == requested and combo.window_text() == requested
            self._wait(input_matches, '原生网络跳转编号没有回读为请求值。')
            if win32gui.GetDlgItem(dialog_handle, 10014) != controls[10014]:
                raise NativeEngineeringDriverError('网络跳转确认按钮已改变。')
            self._click(dialog, 10014)
            # Keep the integer HWND: resolving a WindowSpecification after its
            # dialog has closed can wait for a different future dialog.
            self._wait(lambda: not win32gui.IsWindow(dialog_handle), '本次原生网络跳转对话框尚未结束。')
            def arrived():
                self._idle()
                after = checked(adapter.read_graph(self, owner=owner, kind=kind, data_id=binding['data_id']))
                return after if after['active_network'] == network else None
            return self._wait(arrived, '原生网络跳转没有回读到请求的网络；未重复发送。')
        except Exception as error:
            observation = getattr(error, 'observation', {})
            raise NativeEngineeringDriverError(str(error), observation={**observation,
                'status': 'unconfirmed' if sent else 'rejected', 'network_navigation_started': sent,
                'requested_network': network, 'source_mutation_requested': False, 'save_performed': False}) from error

    def snapshot(self, *, on_dispatch=None):
        if on_dispatch is not None and not callable(on_dispatch):
            raise NativeEngineeringDriverError('保存命令的内部发送回执必须为可调用函数。')
        self.verify_project_codepage()
        self._foreground()
        self.inspect()
        import win32gui
        import pywintypes
        # Return from the actual Save handler before accepting an unchanged
        # file. A posted command can still be queued after the file looks idle.
        # A native modal notice is handled below after this bounded send ends.
        try:
            win32gui.SendMessageTimeout(self.session.window_handle, 0x0111,
                                       self.ui.SAVE_PROJECT_COMMAND, 0, 2, 1500)
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
        raw = self.read_saved_project(acknowledge_notices=True)
        self._last_saved = raw
        return raw

    def read_saved_project(self, *, acknowledge_notices=False):
        """Observe complete owned file bytes without sending a Save command."""
        self.inspect()
        stable = [None, None]
        def saved():
            if self._dialogs():
                if acknowledge_notices:
                    self.ui._acknowledge_known_save_notices(self.session,
                        native_ui_language=getattr(self, 'native_ui_language', 'zh-CN'))
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
                after = self.path.stat()
                if (after.st_size, after.st_mtime_ns) != key:
                    stable[:] = [None, None]
                    return None
                from gxw.container_writer import validate_cfb_streams
                validate_cfb_streams(raw)
            except (OSError, ValueError):
                return None
            return raw
        raw = self._wait(saved, '没有取得当前受控工程的稳定完整文件。')
        self.inspect()
        return raw

    def _save_until(self, verify):
        """Re-observe an async UI edit; never replay the engineering mutation."""
        deadline = time.monotonic() + self.timeout
        error = None
        raw = self.snapshot()
        while True:
            try:
                verify(raw)
            except (ValueError, NativeEngineeringDriverError) as observed:
                error = observed
                if time.monotonic() >= deadline:
                    break
                time.sleep(.15)
                raw = self.read_saved_project()
                continue
            self._last_saved = raw
            return raw
        raise NativeEngineeringDriverError('原生修改尚未通过保存回读：' + str(error))

    def verify_saved_source(self, raw):
        """Match current pages to the observed file without flushing UI edits."""
        from .live_driver import LiveEngineeringDriver
        from gxw.live_context import verify_active_saved_context
        if self.path.read_bytes() != raw:
            raise NativeEngineeringDriverError('核对当前源码前工程文件已改变。')
        observer = LiveEngineeringDriver(self, raw, self.cpu)
        current = observer.read_context()
        verification = verify_active_saved_context(raw, current, raw)
        if self.path.read_bytes() != raw:
            raise NativeEngineeringDriverError('核对当前源码期间工程文件已改变。')
        return verification

    def _export_label_csv(self, name, scope):
        """Observe the current table through native export, without saving it."""
        if scope not in ('global', 'program', 'fb', 'structure'):
            raise NativeEngineeringDriverError('读取标签需要明确的 global、program、fb 或 structure 归属。')
        identity = self.inspect()
        document = self._labels_page(name, scope)
        title = document.window_text()
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
        stable = [None, None]
        def ready():
            if not exported.is_file() or self._dialogs():
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
        raw = self._wait(ready, '原生标签 CSV 导出未完成。')
        self._verify_read_page(identity, document, title)
        return dict(raw_bytes=raw, identity=identity, document_handle=document.handle,
                    document_title=title, export_file=str(exported))

    def _verify_read_page(self, identity, document, title):
        self._idle()
        current = self._active_document()
        if (self.inspect() != identity or current.handle != document.handle
                or current.window_text() != title):
            raise NativeEngineeringDriverError('原生读取期间工程或活动页面已改变。')

    def read_labels(self, name, scope, *, type_sources=None):
        """Read the actual current table; CSV export is an explicit operation."""
        from gxw.active_context import read_active_label_grid
        from gxw.models import GXWFormatError
        from .active_document import ActiveDocumentAdapter
        if not hasattr(self, '_label_reader'):
            self._label_reader = ActiveDocumentAdapter()
        observed = self._label_reader.read_label_table(self, owner=name, scope=scope)
        try:
            return read_active_label_grid(observed['table'], owner=name, scope=scope, identity=observed['identity'],
                document_handle=observed['document_handle'], document_title=observed['document_title'], type_sources=type_sources)
        except GXWFormatError as error:
            raise NativeEngineeringDriverError(str(error)) from error

    def _csv_labels(self, name, scope, rows):
        observed = self.read_labels(name, scope)
        replacement = _merge_label_csv(observed['raw_bytes'], rows, structure=scope == 'structure',
            native_ui_language=getattr(self, 'native_ui_language', 'zh-CN'))
        # The only transfer file is the requested import. Reading the current
        # table and checking the result never request a CSV export.
        self._transfers += 1
        transfer = (nullcontext(self.evidence_directory / f'labels-{self._transfers:03d}')
                    if self.evidence_directory is not None
                    else tempfile.TemporaryDirectory(prefix='gxw-visible-labels-'))
        with transfer as directory:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=self.evidence_directory is None)
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
            return self._visible(page, transfer_file=str(path))

    def _delete_label(self, prepared):
        """Delete the one Core-approved cursor row through the native prompt."""
        import win32gui
        import win32process
        from pywinauto.keyboard import send_keys
        if prepared.get('kind') != 'active_label_delete' or prepared.get('scope') != 'program':
            raise NativeEngineeringDriverError('标签行删除需要当前 Program 的独立 Core 计划。')
        plan, before = prepared['plan'], prepared['before']
        observed = self.read_labels(plan['source_name'], 'program')
        if (observed['csv_columns'] != before['csv_columns'] or
                [row['raw_fields'] for row in observed['declarations']] !=
                [row['raw_fields'] for row in before['declarations']]):
            raise NativeEngineeringDriverError('删除前原生标签表已偏离本次完整基础表，未发出删除。')
        index = plan['csv_row']
        if (type(index) is not int or not 0 <= index < len(observed['declarations'])
                or observed['declarations'][index]['raw_fields'] != plan['before_fields']):
            raise NativeEngineeringDriverError('删除目标与本次 CSV 行身份不同，未发出删除。')
        document = self._labels_page(plan['source_name'], 'program')
        grids = [grid for grid in document.descendants(class_name='SPR32AU70_SpreadSheet')
                 if grid.is_visible() and grid.is_enabled()]
        if len(grids) != 1:
            raise NativeEngineeringDriverError('标签行删除没有唯一的活动原生表格。')
        grid, = grids
        self._foreground()
        grid.set_focus()
        send_keys('^{HOME}{RIGHT}' + ('{DOWN ' + str(index) + '}' if index else '') + '{F2}', vk_packet=False)
        editors = [editor for editor in grid.descendants(class_name='SPR32AU70EditHScroll')
                   if editor.control_id() == 200 and editor.is_visible() and editor.is_enabled()]
        if len(editors) != 1 or editors[0].window_text() != plan['name']:
            raise NativeEngineeringDriverError('原生光标没有落在本次指定的标签名称，未发出删除。')
        if (not win32gui.IsChild(document.handle, grid.handle)
                or not win32gui.IsChild(grid.handle, editors[0].handle)
                or win32process.GetWindowThreadProcessId(editors[0].handle)[1] != self.session.process_id):
            raise NativeEngineeringDriverError('原生名称单元格不属于本次工程页面，未发出删除。')
        self._command(0x692)  # Native label-table Delete Cursor Row, once.
        def confirmation():
            dialogs = self._dialogs()
            if not dialogs:
                return None
            if (len(dialogs) != 1 or dialogs[0].window_text() != self._caption('MELSOFT系列 GX Works2')
                    or self._dialog_text(dialogs[0]) != self._caption('删除光标行。确定吗？')):
                raise NativeEngineeringDriverError('本次删除没有出现已核实的光标行确认，未确认其他提示。')
            dialog = dialogs[0]
            buttons = [dialog.child_window(control_id=control_id, class_name='Button') for control_id in (6, 7)]
            if (not all(button.is_enabled() for button in buttons)
                    or not standard_yes_no_captions(*(button.window_text() for button in buttons))):
                raise NativeEngineeringDriverError('原生删除确认按钮与已核实的控件不同。')
            return dialog
        dialog = self._wait(confirmation, '尚未确认本次原生标签行删除提示。')
        self._click(dialog, 6)
        self._wait(lambda: not self._dialogs() and self._window().is_enabled(), '本次标签行删除尚未结束。')
        return dict(delete_dispatched=True, confirmation_sent=True, source_name=plan['source_name'],
                    name=plan['name'], save_performed=False)

    def _new_object(self, name, kind, language=None):
        self._idle()
        self._command(0x408)
        dialog = self._dialog('新建数据')
        return self._complete_new_object(dialog, name, kind, language)

    def _complete_new_object(self, dialog, name, kind, language=None):
        """Complete the already-open owned New Data action, without dispatching it again."""
        self.inspect()
        if dialog.handle != self._dialog('新建数据').handle:
            raise NativeEngineeringDriverError('新建数据操作不属于本次已打开的原生对话框。')
        key = {'程序块': 'new_kind_program', '全局标签': 'new_kind_global',
               '结构体': 'new_kind_structure', 'FB': 'new_kind_fb', '函数': 'new_kind_function'}.get(kind)
        if key is None:
            raise NativeEngineeringDriverError('新建对象类型没有已核对的原生资源身份。')
        self.ui._select_native_combo(dialog.child_window(control_id=10001, class_name='ComboBox'), self._text(key))
        dialog.child_window(control_id=10006, class_name='Edit').set_edit_text(name)
        if language:
            control = dialog.child_window(control_id=10003, class_name='ListBox')
            choices = control.item_texts()
            language = self._caption(language)
            if choices.count(language) != 1:
                raise NativeEngineeringDriverError('新建对象没有明确的原生语言选项。')
            index = choices.index(language)
            if control.send_message(0x0186, index, 0) != index:
                raise NativeEngineeringDriverError('原生语言选择未确认。')
            control.notify_parent(1)
        self._click(dialog, 1)
        self._wait(lambda: not self._dialogs(), '新建工程对象没有完成。')

    def read_st(self, name, kind='program'):
        """Read the exact current RichEdit body, with no saved-project fallback."""
        from gxw.active_context import read_active_st_text
        from gxw.models import GXWFormatError
        if kind not in ('program', 'fb'):
            raise NativeEngineeringDriverError('读取 ST 需要明确的 program 或 fb 归属。')
        identity = self.inspect()
        document = self._program_page(name, kind, 'st')
        title = document.window_text()
        controls = [control for control in document.descendants(class_name='RichEdit20W')
                    if control.control_id() == 100]
        if len(controls) != 1:
            raise NativeEngineeringDriverError('ST 编辑器的原生文本控件不明确。')
        editor = controls[0]
        text = editor.window_text()
        self._verify_read_page(identity, document, title)
        if editor.window_text() != text:
            raise NativeEngineeringDriverError('ST 正文在读取期间发生变化。')
        try:
            return read_active_st_text(text, owner=name, kind=kind, identity=identity,
                document_handle=document.handle, document_title=title, editor_handle=editor.handle)
        except GXWFormatError as error:
            raise NativeEngineeringDriverError(str(error)) from error

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

    def _task_tree_item(self, tree, resource, task):
        root = tree.get_item((self._text('nav_settings'),), exact=True)
        buckets = {self._text(key) for key in ('nav_execution', 'nav_initial_program',
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
        self._idle()
        self._foreground()
        trees = [t for t in self._window().descendants(class_name='SysTreeView32')
                 if t.control_id() == 12051 and t.is_visible() and t.is_enabled()]
        if len(trees) != 1:
            raise NativeEngineeringDriverError('原生任务导航树不明确。')
        tree = trees[0]
        tree.set_focus()
        item = self._task_tree_item(tree, plan['resource'], plan['task'])
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
        from .native_locale import native_accelerator
        send_keys(native_accelerator('task_open_registration',
            getattr(self, 'native_ui_language', 'zh-CN')), vk_packet=False)
        pattern = '^' + re.escape(self._text('title_task') + ' ' + plan['task']) + r'\s*$'
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
        if not stage_save:
            # TAB commits the first editor. Re-enter the same actual cell;
            # reading the old Edit HWND would only prove its pending text.
            grid.set_focus()
            send_keys('^{HOME}' + ('{DOWN %d}' % (line - 1) if line > 1 else '') + '{F2}', vk_packet=False)
            committed = self._wait(cell, '任务登录表没有重新打开已提交的程序单元格。')
            if committed.window_text() != plan['source_name']:
                raise NativeEngineeringDriverError('任务登录单元格提交后实读与追加计划不同。')
            send_keys('{ESC}', vk_packet=False)
            self.inspect()
            visible = self._visible(plan['task'] + ' 任务登录', program=plan['source_name'], line=line)
            return {**visible, 'task_append_confirmation': dict(status='matched',
                origin='native_task_cell_committed_readback', resource=plan['resource'], task=plan['task'],
                program=plan['source_name'], line=line, save_performed=False), 'expected_tasks': expected}
        def bound(raw):
            current = native_task_scope_context(raw)
            if current['status'] != 'available' or current['tasks'] != expected:
                raise NativeEngineeringDriverError('保存后的原生任务成员与当前追加计划不同。')
        self._save_until(bound)
        return self._visible(plan['task'] + ' 任务登录', program=plan['source_name'], line=line)

    def _choose_label(self, name, *, expected_type=None):
        dialog = self._dialog('标签登录/选择')
        self.ui._select_native_combo(dialog.child_window(control_id=6651, class_name='ComboBox'), self._text('chooser_all'))
        type_filter = dialog.child_window(control_id=6660, class_name='ComboBox')
        if type_filter.is_enabled() and 'ANY' in type_filter.item_texts():
            # Placeholder terminals retain the previous chooser's Bit filter.
            # This is the search filter, not the declaration's data-type field.
            self.ui._select_native_combo(type_filter, 'ANY')
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

    def _graph_grid(self, view, *, include_lattice=False):
        import win32gui
        # These are the editor's actual scroll bars, not frame/status titles.
        for control in view.parent().descendants(class_name='ScrollBar'):
            if control.control_id() in (2, 3):
                info = win32gui.GetScrollInfo(control.handle, 2)
                if info[4] != 0:
                    raise NativeEngineeringDriverError('FBD 视图已滚动；尚未验证当前源码到画面的坐标映射。')
        return _grid_calibration(view.capture_as_image(), include_lattice=include_lattice)

    def _graph_placement_point(self, view, grid, x, y):
        """Require all empty input cells on the actual first network lattice."""
        current = self._graph_grid(view, include_lattice=True)
        if any(current[key] != grid[key] for key in ('pitch', 'x', 'y', 'width', 'height')):
            raise NativeEngineeringDriverError('新增前实际图形网格或视口发生变化，未发出插入。')
        points = {tuple(point) for point in current['lattice_points']}
        for column, row in ((x+1,y+1),(x+2,y+1),(x+1,y+2),(x+2,y+2)):
            if self._graph_point(current, column, row) not in points:
                raise NativeEngineeringDriverError('新增区域不在已观察到的首个网络点阵中，未发出插入。')
        return self._graph_point(current, x+2, y+1)

    @staticmethod
    def _graph_point(grid, x, y):
        point = (round(grid['x'] + x * grid['pitch']), round(grid['y'] + y * grid['pitch']))
        if not 5 < point[0] < grid['width'] - 28 or not 5 < point[1] < grid['height'] - 28:
            raise NativeEngineeringDriverError('目标图形对象不在当前可见区域。')
        if 'calibrated_x_limit' in grid and point[0] >= grid['calibrated_x_limit']:
            raise NativeEngineeringDriverError('目标超出当前已校准的网格区域，需要重新定位视口。')
        return point

    def _graph_drag(self, view, grid, source, target):
        import win32process
        if (win32process.GetWindowThreadProcessId(view.handle)[1] != self.session.process_id
                or not view.is_visible() or not view.is_enabled()
                or any(not (5 < point[0] < grid['width'] - 28
                            and 5 < point[1] < grid['height'] - 28
                            and point[0] < grid.get('calibrated_x_limit', grid['width']))
                       for point in (source, target))):
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
                    elif len(dialogs) != 1 or dialogs[0].window_text() != self._caption('标签登录/选择'):
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
                                        self._caption('梯形图图形重叠。是否继续处理？')):
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

    def _edit_allocation_grid_field(self, dialog, field, value):
        import win32gui
        from pywinauto.keyboard import send_keys
        from pywinauto.mouse import click
        if (field.get('grid_role') != 'allocation' or field.get('native_column') not in (5, 6)
                or field.get('state', 0) & (1 | 0x40) or not re.fullmatch(r'[0-9]+', str(value))):
            raise NativeEngineeringDriverError('请求的原生分配表格字段不可编辑。')
        grids = [grid for grid in dialog.descendants(class_name='VSFlexGrid8N')
                 if grid.is_visible() and grid.is_enabled()]
        candidates = [grid for grid in grids if {len(row['cells']) for row in _allocation_grid_rows(grid.handle)} == {8}]
        if len(candidates) != 1:
            raise NativeEngineeringDriverError('待编辑的原生分配表归属不唯一。')
        grid = candidates[0]
        if any(child.is_visible() and child.control_id() == 666 for child in grid.descendants()):
            raise NativeEngineeringDriverError('分配表已有未完成的原生单元格编辑。')
        self._foreground(dialog.handle)
        point = _allocation_cell_point(self.session.window_handle, grid.handle,
                                       field['native_row'], field['native_column'])
        _, cell = _allocation_cell(grid.handle, field['native_row'], field['native_column'])
        if cell.accValue[0] != field['text']:
            raise NativeEngineeringDriverError('分配单元格已偏离本次核对的值。')
        click(coords=point)
        # MSAA child objects are snapshots: reacquire after the native click.
        def selected():
            _, current = _allocation_cell(grid.handle, field['native_row'], field['native_column'])
            return (current.accValue[0] == field['text'] and current.accState[0] & 2
                    and _native_focus(grid.handle) == grid.handle)
        self._wait(selected, '原生分配单元格的选择与实际焦点未确认。')
        send_keys('{F2}', pause=0, vk_packet=False)
        def editor_ready():
            matches = [child for child in grid.descendants() if child.is_visible() and child.is_enabled()
                and child.control_id() == 666 and re.fullmatch(r'ATL:[0-9A-Fa-f]+', child.class_name())
                and win32gui.GetParent(child.handle) == grid.handle]
            if len(matches) != 1:
                return None
            editor = matches[0]
            if (editor.window_text() != field['text'] or _native_focus(editor.handle) != editor.handle
                    or win32gui.GetWindowLong(editor.handle, -16) & 0xffffffff != 0x500001c6):
                return None
            return editor
        editor = self._wait(editor_ready, '原生分配单元格没有形成已确认的内嵌编辑器。')
        self.inspect()
        if (not dialog.is_enabled() or _native_focus(editor.handle) != editor.handle
                or win32gui.GetParent(editor.handle) != grid.handle):
            raise NativeEngineeringDriverError('原生分配编辑器归属或焦点已改变。')
        if not win32gui.SendMessage(editor.handle, 0x000C, 0, str(value)) or editor.window_text() != str(value):
            raise NativeEngineeringDriverError('原生分配编辑器未保留请求值，未提交单元格。')
        if _native_focus(editor.handle) != editor.handle:
            raise NativeEngineeringDriverError('提交前原生分配编辑器焦点已改变。')
        send_keys('{TAB}', pause=0, vk_packet=False)

    def _configuration_fields(self, dialog):
        """Identify actual controls; resource IDs never stand in for grid HWNDs."""
        from gxw.project_metadata import project_allocation_profile, read_native_allocation_grids
        if not self.cpu.startswith('FX'):
            grids = [grid for grid in dialog.descendants(class_name='VSFlexGrid8N')
                     if grid.is_visible() and grid.is_enabled()]
            if len(grids) != 2:
                raise NativeEngineeringDriverError('当前型号没有两个明确的原生分配表格。')
            by_role = {}
            for grid in grids:
                rows = _allocation_grid_rows(grid.handle)
                columns = {len(row['cells']) for row in rows}
                role = 'allocation' if columns == {8} else 'parameter' if columns == {3} else None
                if role is None or role in by_role:
                    raise NativeEngineeringDriverError('当前型号分配表的列结构或归属不唯一。')
                by_role[role] = rows
            fields = read_native_allocation_grids(by_role,
                native_ui_language=getattr(self, 'native_ui_language', 'zh-CN'))
            return {**fields, 'allocation_profile': project_allocation_profile(self.cpu)['allocation_profile']}
        fields, ranges = {}, {}
        for device, controls in {'D': (14069, 14072), 'M': (14096, 14099)}.items():
            values = []
            for control_id in controls:
                editor = dialog.child_window(control_id=control_id, class_name='Edit')
                text = editor.window_text()
                values.append(dict(control_id=control_id, text=text, enabled=bool(editor.is_enabled())))
            fields[device] = values
            ranges[device] = ([int(value['text']) for value in values]
                              if all(re.fullmatch(r'[0-9]+', value['text']) for value in values) else None)
        result = dict(raw_fields=fields, ranges=ranges, coverage='verified_D_M_controls_only')
        if self.cpu != 'FX3U/FX3UC':
            result.update(control_profile='device_range_fx', coverage='observed_D_M_controls',
                allocation_profile=project_allocation_profile(self.cpu)['allocation_profile'])
        return result

    def read_configuration(self):
        """Observe the current native range dialog and cancel it without Save."""
        if native_project_profile(self.cpu) is None or not native_project_profile(self.cpu).program_supported:
            return dict(status='unavailable', origin='active_native_configuration',
                        reason='native_configuration_controls_not_verified_for_cpu', cpu=self.cpu,
                        driver_saved=False)
        identity = self.inspect()
        self._command(0x685)
        dialog = self._dialog('自动分配软元件设置')
        close_dialog = True
        try:
            fields = self._configuration_fields(dialog)
            if self.inspect() != identity:
                raise NativeEngineeringDriverError('读取分配设置期间原生工程身份已改变。')
            visible = self._visible('自动分配软元件设置', dialog=dialog, **fields)
            from gxw.project_metadata import native_allocation_observation_state
            if native_allocation_observation_state(self._configuration_fields(dialog)) != native_allocation_observation_state(fields):
                close_dialog = False
                raise NativeEngineeringDriverError('分配设置字段在读取期间已改变，保留当前对话框。',
                    observation={'source_status': 'requires_reconciliation'})
            details = dict(fields)
            coverage = details.pop('coverage', 'verified_D_M_controls_only')
            return dict(status='available' if all(value is not None for value in fields['ranges'].values()) else 'partial',
                origin='active_native_configuration', identity=identity, cpu=self.cpu,
                dialog_handle=dialog.handle, **details, driver_saved=False,
                coverage=coverage, visible_completion=visible)
        finally:
            # Only cancel the same owned dialog while its project identity is
            # still intact. A changed target remains for explicit reconciliation.
            if self.inspect() != identity:
                raise NativeEngineeringDriverError('读取分配设置期间原生工程身份已改变。',
                    observation={'source_status': 'requires_reconciliation'})
            if close_dialog:
                self._click(dialog, 2)
                self._wait(lambda: not self._dialogs(), '读取后的原生分配设置页尚未关闭。')

    def _finish_allocation_notices(self):
        """Observe confirmations for an already posted allocation commit."""
        notices, acknowledged = [], set()
        deadline = time.monotonic() + self.timeout
        while self._dialogs() and time.monotonic() < deadline:
            dialogs = [d for d in self._dialogs() if d.is_enabled()]
            handled = False
            for current in dialogs:
                if current.handle in acknowledged:
                    continue
                text = self._dialog_text(current)
                normalized = text.replace('\r\n', '\n').replace('\r', '\n')
                overlap = re.escape(self._text('allocation_overlap_format')).replace(
                    re.escape('%1!s!'), r'[^\r\n]{1,80}')
                if (re.fullmatch(overlap, normalized)
                        or _same_text(text, self._text('allocation_rebuild_notice'))):
                    notices.append(text)
                    acknowledged.add(current.handle)
                    self._click(current, 6)
                    handled = True
                    break
            if not handled:
                time.sleep(.05)
        self._wait(lambda: not self._dialogs(), '自动分配范围没有完成，保留原生提示。')
        return notices

    def _configure(self, plan):
        from copy import deepcopy
        from gxw.models import GXWFormatError
        from gxw.project_metadata import native_allocation_observation_state
        if 'parameter_after' in plan:
            raise NativeEngineeringDriverError('此前台驱动尚未验证 PLC 参数容量编辑。')
        if any(row['device'] not in ('D', 'M') or any(v < 0 for v in row['after']) for row in plan['ranges']):
            raise NativeEngineeringDriverError('此前台范围字段或禁用方式尚未验证。')
        self._command(0x685)
        dialog = self._dialog('自动分配软元件设置')
        before = self._configuration_fields(dialog)
        for key in ('allocation_profile', 'control_profile'):
            if key in plan and plan[key] != before.get(key):
                raise NativeEngineeringDriverError('当前分配设置页面与 Core 计划的型号或控件配置不同。')
        expected = deepcopy(before)
        for row in plan['ranges']:
            for field, previous, after in zip(expected['raw_fields'][row['device']], row['before'], row['after']):
                if field['text'] != str(previous) or field.get('enabled') is False:
                    raise NativeEngineeringDriverError('活动自动分配范围与当前 Core 配置不同。')
                field['text'] = str(after)
            expected['ranges'][row['device']] = list(row['after'])
            if before.get('control_profile') == 'device_range_grid':
                fields = expected['raw_fields'][row['device']]
                cells = expected['grid_rows']['allocation'][fields[0]['native_row']]['cells']
                for field, value in zip(fields, row['after']):
                    cells[field['native_column']]['value'] = str(value)
                cells[7]['value'] = str(row['after'][1] - row['after'][0] + 1)
        self._last_compile_run = None
        if before.get('control_profile') == 'device_range_grid':
            rolling = deepcopy(before)
            for row in plan['ranges']:
                # Keep the interval valid after each native Tab commit.
                order = (1, 0) if row['after'][0] > row['before'][1] else (0, 1)
                for index in order:
                    field = rolling['raw_fields'][row['device']][index]
                    after = row['after'][index]
                    if field['text'] == str(after):
                        continue
                    target = deepcopy(rolling)
                    target['raw_fields'][row['device']][index]['text'] = str(after)
                    target['ranges'][row['device']][index] = after
                    cells = target['grid_rows']['allocation'][field['native_row']]['cells']
                    cells[field['native_column']]['value'] = str(after)
                    low, high = target['ranges'][row['device']]
                    cells[7]['value'] = str(high - low + 1)
                    self._edit_allocation_grid_field(dialog, field, after)
                    def committed():
                        try:
                            current = self._configuration_fields(dialog)
                        except GXWFormatError:
                            return None  # The native total can update after its range cell.
                        return current if native_allocation_observation_state(current) == native_allocation_observation_state(target) else None
                    rolling = self._wait(committed, '原生分配单元格提交后未通过整表保留核对。')
        else:
            for row in plan['ranges']:
                for field, after in zip(before['raw_fields'][row['device']], row['after']):
                    editor = dialog.child_window(control_id=field['control_id'], class_name='Edit')
                    if not editor.is_enabled() or editor.window_text() != field['text']:
                        raise NativeEngineeringDriverError('原生自动分配字段状态在修改前已改变。')
                    editor.set_edit_text(str(after))
                    if editor.window_text() != str(after):
                        raise NativeEngineeringDriverError('原生自动分配字段回读不同。')
        if native_allocation_observation_state(self._configuration_fields(dialog)) != native_allocation_observation_state(expected):
            raise NativeEngineeringDriverError('应用前原生分配设置未通过请求字段及保留字段核对。')
        self._click(dialog, 1)
        notices = self._finish_allocation_notices()
        current = self.read_configuration()
        if native_allocation_observation_state(current) != native_allocation_observation_state(expected):
            raise NativeEngineeringDriverError('重新打开的原生分配范围与本步设置不同，或未请求字段已改变。')
        return {**current['visible_completion'], 'configuration': current, 'notices': notices}

    def compile_current(self, plan):
        """Compile the live revision; GX itself may save, this driver does not."""
        self.verify_project_codepage()
        return self._compile(plan, legacy_save=False)

    def _read_compile_diagnostics(self, summary, run):
        from copy import deepcopy
        binding = dict(compile_run_id=run['compile_run_id'], revision=run['revision'],
            pid=run['identity']['process_id'], identity=deepcopy(run['identity']),
            origin='current_native_compile_diagnostic_grid')
        expected = _compile_summary_counts(run.get('output'),
            public_checks_requested=run.get('public_checks_requested'),
            native_ui_language=getattr(self, 'native_ui_language', 'zh-CN'))
        try:
            grids = summary.parent().descendants(class_name='VSFlexGrid8N')
            candidates, failures = [], []
            for grid in grids:
                try:
                    rows = _accessible_compile_diagnostic_rows(grid.handle,
                        native_ui_language=getattr(self, 'native_ui_language', 'zh-CN'))
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
        result['diagnostics'] = self._read_compile_diagnostics(summary, result)
        return result

    def _compile(self, plan, *, legacy_save=True):
        from copy import deepcopy
        from uuid import uuid4
        if type(plan.get('revision')) is not int or plan['revision'] < 0 or type(legacy_save) is not bool:
            raise NativeEngineeringDriverError('原生编译需要明确的当前源码修订。')
        if 'public_checks_requested' in plan and type(plan['public_checks_requested']) is not bool:
            raise NativeEngineeringDriverError('原生编译后的程序检查设置必须是明确布尔值。')
        self._idle()
        if getattr(self, '_pending_compile_run', None) is not None:
            raise NativeEngineeringDriverError('此前原生编译尚待核对，未重新发送编译命令。')
        self._last_compile_run = None
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
        identity = self.inspect()
        pending = dict(compile_run_id=uuid4().hex, identity=deepcopy(identity), revision=plan['revision'],
            summary_handle=summary.handle, fresh_output_reset=True, command_dispatched=False,
            confirmation_dispatched=False, confirmation_accepted=False, public_checks_requested=None)
        self._pending_compile_run = pending
        self._command(0x4A1)
        pending['command_dispatched'] = True
        def prompt():
            rows = [d for d in self._dialogs() if d.is_enabled() and self._text('compile_confirm') in self._dialog_text(d)]
            return rows[0] if len(rows) == 1 else None
        dialog = self._wait(prompt, '没有出现本次全部编译确认页。')
        pending['confirmation_handle'] = dialog.handle
        checks = dialog.child_window(control_id=12270, class_name='Button')
        check_state = checks.get_check_state()
        if check_state not in (0, 1):
            raise NativeEngineeringDriverError('原生编译后的程序检查选项状态不可确认。')
        expected_check = (1 if legacy_save else int(plan.get('public_checks_requested', bool(check_state))))
        if check_state != expected_check:
            self._click(dialog, 12270)
            self._wait(lambda: checks.get_check_state() == expected_check, '原生编译后的程序检查没有匹配工程设置。')
        if checks.get_check_state() != expected_check:
            raise NativeEngineeringDriverError('原生编译检查选项在确认前发生改变。')
        pending['public_checks_requested'] = bool(expected_check)
        self._click(dialog, 1)
        pending['confirmation_dispatched'] = True
        def completed():
            output = summary.window_text()
            dialogs = self._dialogs()
            if not any(d.handle == pending['confirmation_handle'] for d in dialogs):
                pending['confirmation_accepted'] = True
            if (win32gui.IsWindowVisible(summary.handle)
                    and _compile_summary_counts(output,
                        public_checks_requested=pending['public_checks_requested'],
                        native_ui_language=getattr(self, 'native_ui_language', 'zh-CN')) is not None
                    and self._window().is_enabled() and not dialogs and pending['confirmation_accepted']):
                return output
            return None
        output = self._wait(completed, '本次原生全部编译尚未完成。')
        if self.inspect() != identity:
            raise NativeEngineeringDriverError('原生编译期间工程身份已改变。')
        if legacy_save:
            self.snapshot()
        result = self._completed_compile_receipt(pending, summary, output, driver_saved=legacy_save)
        if (self.inspect() != identity or summary.window_text() != output
                or self._dialogs() or not self._window().is_enabled()):
            raise NativeEngineeringDriverError('核对本次编译诊断时摘要或工程状态发生改变。')
        self._last_compile_run = deepcopy(result)
        self._pending_compile_run = None
        result['visible_completion'] = self._visible('本次原生编译输出', output=output)
        self._last_compile_run = deepcopy(result)
        return result

    def read_current_compile_result(self, *, revision):
        """Recheck a run started by this driver, never adopt an old output pane."""
        from copy import deepcopy
        previous = getattr(self, '_last_compile_run', None)
        pending = getattr(self, '_pending_compile_run', None)
        missing = dict(status='unavailable', origin='active_native_compile', revision=revision,
                       driver_saved=False, source_binding='session_revision_required')
        recovering = not isinstance(previous, dict) and isinstance(pending, dict)
        run = pending if recovering else previous
        if not isinstance(run, dict) or type(revision) is not int or revision != run.get('revision'):
            return {**missing, 'reason': 'no_completed_compile_for_requested_revision'}
        identity = self.inspect()
        if identity != run.get('identity'):
            return {**missing, 'reason': 'native_project_identity_changed'}
        try:
            self._idle()
        except NativeEngineeringDriverError:
            return {**missing, 'reason': 'native_compile_or_modal_operation_not_finished'}
        controls = self._owned_control_handles('Edit', 10013, visible=False)
        if controls != [run.get('summary_handle')]:
            return {**missing, 'reason': 'compile_output_control_changed_or_ambiguous'}
        summary = self.ui._application(self.session).window(handle=controls[0])
        output = summary.window_text()
        if recovering:
            import win32gui
            if (run.get('fresh_output_reset') is not True or run.get('command_dispatched') is not True
                    or run.get('confirmation_dispatched') is not True
                    or type(run.get('public_checks_requested')) is not bool):
                return {**missing, 'reason': 'original_compile_confirmation_not_dispatched'}
            if (not win32gui.IsWindowVisible(summary.handle) or _compile_summary_counts(output,
                    public_checks_requested=run['public_checks_requested'],
                    native_ui_language=getattr(self, 'native_ui_language', 'zh-CN')) is None
                    or self._dialogs() or not self._window().is_enabled()):
                return {**missing, 'reason': 'original_native_compile_not_completed'}
            run['confirmation_accepted'] = True
            previous = self._completed_compile_receipt(run, summary, output)
            previous['recovered'] = True
        elif output != previous.get('output'):
            return {**missing, 'reason': 'compile_output_or_project_changed'}
        if (self.inspect() != identity or summary.window_text() != output):
            return {**missing, 'reason': 'compile_output_or_project_changed'}
        diagnostics = self._read_compile_diagnostics(summary, previous)
        old_diagnostics = previous.get('diagnostics', {})
        if (old_diagnostics.get('status') == 'observed' and (diagnostics.get('status') != 'observed'
                or diagnostics.get('rows') != old_diagnostics.get('rows'))):
            return {**missing, 'reason': 'compile_diagnostic_rows_changed_or_unavailable'}
        previous = {**deepcopy(previous), 'diagnostics': diagnostics}
        if self.inspect() != identity or summary.window_text() != output:
            return {**missing, 'reason': 'compile_output_or_project_changed'}
        self._last_compile_run = deepcopy(previous)
        if recovering:
            self._pending_compile_run = None
        return {**deepcopy(previous), 'status': 'observed', 'origin': 'active_native_compile',
                'read_driver_saved': False}

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
        entries, final_key = native_menu_selection('menu_find', 'menu_cross_reference',
            getattr(self, 'native_ui_language', 'zh-CN'))
        self._choose_native_menu(entries, final_key)
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
        from gxw.live_assignments import live_assignment_queries
        try:
            queries = live_assignment_queries(plan.get('queries'))
        except ValueError as error:
            raise NativeEngineeringDriverError(str(error)) from error
        context = plan.get('allocation_context')
        searches = []
        for query in queries:
            entries = [entry for entry in (context or {}).get('queries', []) if entry.get('query') == query]
            if context is not None and (len(entries) != 1 or entries[0].get('status') != 'prepared'):
                raise NativeEngineeringDriverError('地址展示没有该变量的当前编译来源。')
            searches.append(entries[0]['search_text'] if entries else query['name'])
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
        for query, search_text in zip(queries, searches):
            panel = self._cross_reference_panel()
            self._foreground(floating.handle)
            search = panel.child_window(control_id=20102, class_name='ComboBox')
            summary = panel.child_window(control_id=20304, class_name='Edit')
            win32gui.SendMessage(summary.handle, 0x000C, 0, '')
            editor = search.child_window(class_name='Edit')
            editor.set_focus()
            editor.set_edit_text(search_text)
            search.notify_parent(5)  # CBN_EDITCHANGE, like editing the native search box
            panel.child_window(control_id=20101, class_name='Button').set_focus()
            panel.child_window(control_id=20101, class_name='Button').click_input()
            from gxw.native_locale import cross_reference_summary_count
            def completed_search():
                current = summary.window_text()
                return current if cross_reference_summary_count(current, search_text,
                    language=getattr(self, 'native_ui_language', 'zh-CN')) is not None else None
            text = self._wait(completed_search, '本次交叉引用搜索没有完成。')
            grids = [c for c in panel.descendants(class_name='VSFlexGrid8N') if c.is_visible()]
            if len(grids) != 1 or grids[0].rectangle().height() < 120:
                raise NativeEngineeringDriverError('原生地址结果行没有足够的可见区域。')
            rows = _accessible_grid_rows(grids[0].handle,
                native_ui_language=getattr(self, 'native_ui_language', 'zh-CN'))
            completion = self._visible('本次编译的交叉引用', query=query['name'], source=query['source'],
                                       scope=query.get('scope', 'local'), rows=rows)
            results.append({'query': deepcopy(query), 'search_text': search.window_text(),
                            'summary': text, 'rows': rows, 'visible_completion': completion})
        return {'cross_references': results, 'pid': self.session.process_id,
                'revision': plan['revision'], 'visible_completion': completion}

    def read_assignments(self, queries, *, revision, compilation=None, allocation_context=None):
        """Join current native rows to Core's acknowledged same-build allocations.

        The owning session supplies its fresh compile-save projection. This
        driver never reads an old GXW/snapshot or runs an offline compilation.
        """
        from copy import deepcopy
        from gxw.live_assignments import live_assignment_queries, verify_live_assignments
        from gxw.live_context import _identity
        if type(revision) is not int or revision < 0:
            raise NativeEngineeringDriverError('活动地址读取需要 1 至 32 个明确查询。')
        try:
            queries = live_assignment_queries(queries)
        except ValueError as error:
            raise NativeEngineeringDriverError(str(error)) from error
        self._last_current_assignments = None
        result = dict(status='unavailable', origin='current_native_cross_reference', revision=revision,
            allocation_status='unverified', owner_binding='source_and_compiled_component', driver_saved=False,
            execution='not_run', results=[])
        def unavailable(reason):
            result['results'] = [dict(query=deepcopy(query), status='unavailable', reason=reason) for query in queries]
            return result
        if not isinstance(allocation_context, dict) or allocation_context.get('kind') != 'current_compile_allocations':
            return unavailable('acknowledged_current_compile_save_allocations_required')
        entries = allocation_context.get('queries')
        if (not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries)
                or [entry.get('query') for entry in entries] != queries
                or allocation_context.get('revision') != revision):
            raise NativeEngineeringDriverError('当前编译分配来源与本次查询或修订不一致。')
        current = self.read_current_compile_result(revision=revision)
        if current.get('status') != 'observed':
            reason = current.get('reason', 'current_compile_unavailable')
        elif (not isinstance(compilation, dict) or compilation.get('status') != 'passed'
                or type(compilation.get('revision')) is not int or compilation['revision'] != revision
                or compilation.get('compile_run_id') != current.get('compile_run_id')):
            reason = 'core_verified_current_compile_required'
        else:
            reason = None
        if reason:
            return unavailable(reason)
        identity = _identity(current.get('identity'))
        if (allocation_context.get('compile_run_id') != current['compile_run_id']
                or allocation_context.get('identity') != identity):
            return unavailable('allocation_context_belongs_to_another_compile_or_project')
        requested = [entry['query'] for entry in entries if entry.get('status') == 'prepared']
        if not requested:
            return verify_live_assignments(allocation_context, dict(revision=revision,
                compile_run_id=current['compile_run_id'], identity=identity, cross_references=[]))
        observed = self._show_assignments({'queries': requested, 'revision': revision,
                                          'allocation_context': allocation_context})
        fresh = self.read_current_compile_result(revision=revision)
        if (fresh.get('status') != 'observed' or fresh.get('compile_run_id') != current['compile_run_id']
                or fresh.get('identity') != current.get('identity')):
            unavailable('compile_changed_during_assignment_read')
            result['unverified_observation'] = deepcopy(observed)
            return result
        records = observed.get('cross_references')
        if (not isinstance(records, list) or len(records) != len(requested)
                or any(not isinstance(row, dict) or row.get('query') != query
                       for row, query in zip(records, requested))):
            raise NativeEngineeringDriverError('原生交叉引用读回与本次查询不一致。')
        if observed.get('revision') != revision or observed.get('pid') != identity['process_id']:
            raise NativeEngineeringDriverError('原生交叉引用读回属于其它进程或修订。')
        verified = verify_live_assignments(allocation_context, {**observed,
            'compile_run_id': current['compile_run_id'], 'identity': deepcopy(identity)})
        result = {**verified, 'cross_references': deepcopy(records),
                  'visible_completion': deepcopy(observed.get('visible_completion'))}
        confirmed = [entry['query'] for entry in verified['results'] if entry['status'] in ('current', 'partial')]
        last = next((record for record in reversed(records) if record['query'] in confirmed), None)
        if last is not None:
            self._last_current_assignments = dict(result=deepcopy(result), record=deepcopy(last),
                allocation_context=deepcopy(allocation_context))
        return result

    def show_assignments_completion(self, observation):
        """Reopen one confirmed native query after full-context page readback."""
        from copy import deepcopy
        from gxw.live_context import _identity
        cached = getattr(self, '_last_current_assignments', None)
        if (not isinstance(cached, dict) or not isinstance(observation, dict)
                or any(observation.get(key) != cached['result'].get(key)
                       for key in ('revision', 'compile_run_id', 'identity', 'cross_references'))):
            raise NativeEngineeringDriverError('地址展示缺少本次已核对查询的原始回执。')
        expected = cached['result']
        def current():
            run = self.read_current_compile_result(revision=expected['revision'])
            if (run.get('status') != 'observed' or run.get('compile_run_id') != expected['compile_run_id']
                    or _identity(run.get('identity')) != expected['identity']):
                raise NativeEngineeringDriverError('地址完成页面已不属于本次编译或工程。')
        current()
        record = cached['record']
        observed = self._show_assignments(dict(queries=[deepcopy(record['query'])], revision=expected['revision'],
                                              allocation_context=cached['allocation_context']))
        current()
        records = observed.get('cross_references')
        if (observed.get('revision') != expected['revision']
                or observed.get('pid') != expected['identity']['process_id']
                or not isinstance(records, list) or len(records) != 1
                or not isinstance(records[0], dict)
                or any(records[0].get(key) != record.get(key) for key in ('query', 'search_text', 'summary', 'rows'))):
            raise NativeEngineeringDriverError('重新展示的原生地址行已改变，原查询不能证明当前结果。',
                                                observation={'status': 'unconfirmed', 'cross_references': records})
        completion = records[0].get('visible_completion')
        if not isinstance(completion, dict) or completion.get('completed') is not True:
            raise NativeEngineeringDriverError('本次地址结果没有形成实际可见的完成页面。')
        return deepcopy(completion)

    def reopen(self):
        self._idle()
        saved = self.read_saved_project()
        if self._last_saved is None or saved != self._last_saved:
            raise NativeEngineeringDriverError('重新打开需要当前已确认的保存工程；未发送保存或关闭。')
        self._command(0x401)
        self._wait(lambda: not self._frame_identity().project_open and not self._dialogs(), '工程未冷关闭，未丢弃任何修改。')
        self._command(0x400, project=False)
        self._file_name(self._dialog('打开工程'), self.path)
        self._wait(lambda: self.path.name.casefold() in self._window().window_text().casefold()
                   and not self._dialogs(), '保存的工程没有重新打开。')
        self.inspect()
        reopened = self.read_saved_project()
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
                    observed = self.read_st(program['name'])
                except NativeEngineeringDriverError as error:
                    if str(error) == 'ST 正文在读取期间发生变化。':
                        return False
                    raise
                return normalized_st(observed['st_text']) == normalized_st(program['st_text'])
            self._wait(loaded, '冷重开后的 ST 正文尚未与保存工程一致。')
        self._visible('保存工程冷重开', path=str(self.path), same_process=True)
        return reopened

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

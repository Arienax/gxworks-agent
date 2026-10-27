# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Native engineering observations helpers."""
from __future__ import annotations
import re
from gxw.native_locale import rebuild_completion_markers, ui_text
from .engineering_errors import (NativeEngineeringDriverError)


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

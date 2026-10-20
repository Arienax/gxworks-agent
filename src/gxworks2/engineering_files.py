"""Native engineering files helpers."""
from __future__ import annotations
import csv
import io
from gxw.native_locale import canonical_label_columns
from .engineering_errors import (NativeEngineeringDriverError)


def _same_text(left, right):
    return left.replace('\r\n', '\n').replace('\r', '\n') == right.replace('\r\n', '\n').replace('\r', '\n')


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

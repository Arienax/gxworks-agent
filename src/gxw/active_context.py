# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Core observations of unsaved native label tables and ST editor contents.

The native driver supplies a checked project/document identity. Observed row
order is retained; it is not a native graphic port number.
"""
from __future__ import annotations

import csv
import io
import re
from copy import deepcopy
from types import SimpleNamespace

from .callable_sources import CLASS, interface, type_mask
from .declarations import CLASS_CODES, _type_parts
from .fb_authoring import _st_implementation
from .models import GXWFormatError
from .native_locale import (canonical_label_columns, label_title, native_language,
                            program_title, strict_project_text)


_LABEL_COLUMNS = frozenset({'类', '标签名', '数据类型', '常量', '软元件', '地址', '注释', '备注',
                            '系统标签的关联', '系统标签名', '属性'})
_IDENTITY_KEYS = ('process_id', 'window_handle', 'process_started', 'executable',
                  'software_version', 'project_path', 'cpu_menu')


def parse_active_label_csv(raw):
    """Decode the native tab-separated UTF-16 framing without discarding fields."""
    if (not isinstance(raw, bytes) or len(raw) > 8 * 1024 * 1024
            or not raw.startswith((b'\xff\xfe', b'\xfe\xff'))):
        raise GXWFormatError('原生标签 CSV 缺少已验证的 UTF-16 格式。')
    try:
        text = raw.decode('utf-16')
        if '\0' in text:
            raise ValueError('embedded NUL')
        rows = list(csv.reader(io.StringIO(text, newline=''), delimiter='\t', strict=True))
    except (UnicodeError, csv.Error, ValueError) as error:
        raise GXWFormatError('原生标签 CSV 的 UTF-16 或字段格式不完整。') from error
    if (len(rows) < 2 or len(rows[0]) != 1 or not rows[0][0] or not rows[1]
            or any(not value for value in rows[1]) or len(set(rows[1])) != len(rows[1])):
        raise GXWFormatError('原生标签 CSV 的表头不明确。')
    body = [row for row in rows[2:] if row and any(row)]
    if len(body) > 8192 or any(len(row) != len(rows[1]) for row in body):
        raise GXWFormatError('原生标签 CSV 的列数或记录数不一致。')
    return rows[0], rows[1], body


def _binding(owner, kind, identity, document_handle, document_title, *, st=False):
    if (kind not in (('program', 'fb') if st else ('global', 'program', 'fb', 'structure'))
            or not isinstance(owner, str) or not owner or '\0' in owner
            or not isinstance(identity, dict) or not set(_IDENTITY_KEYS) <= identity.keys()
            or any(type(identity[key]) is not int or identity[key] <= 0
                   for key in ('process_id', 'window_handle'))
            or any(not isinstance(identity[key], str) or not identity[key] for key in _IDENTITY_KEYS[2:])
            or identity['software_version'] != '1.635.0.1'
            or type(document_handle) is not int or document_handle <= 0
            or not isinstance(document_title, str)):
        raise GXWFormatError('当前原生工程或文档归属不完整。')
    language = native_language(identity)
    if st:
        expected = program_title(owner, kind, 'st', language=language)
        matched = document_title.rstrip() == expected
    else:
        matched = document_title.rstrip() == label_title(owner, kind, language=language)
    if not matched:
        raise GXWFormatError('当前原生页面与指定声明或 ST owner 不同。')
    return dict(identity=deepcopy(identity), owner=owner, owner_kind=kind,
                document_handle=document_handle, document_title=document_title)


def _type_observation(text, type_sources):
    try:
        base, bounds = _type_parts(text)
        mask = type_mask('ARRAY [' + ','.join(f'{low}..{high}' for low, high in bounds)
                         + '] OF ' + base if bounds else base)
        return dict(status='resolved', base_type=base, bounds=bounds, type_mask=mask, kind='variable')
    except GXWFormatError as error:
        diagnostic = str(error)
    try:
        base, bounds = _type_parts(text)
    except GXWFormatError:
        return dict(status='unresolved', diagnostic=diagnostic)
    matches = [source for source in type_sources if source['owner'].casefold() == base.casefold()]
    if len(matches) != 1:
        return dict(status='unresolved', base_type=base, bounds=bounds, diagnostic=diagnostic)
    source = matches[0]
    return dict(status='source_bound', base_type=base, bounds=bounds, type_mask=None,
                kind='function_block' if source['owner_kind'] == 'fb' else 'structure',
                source_binding=deepcopy(source['source_binding']))


def read_active_labels_table(table, *, owner, scope, identity, document_handle, document_title, type_sources=None):
    """Project an independently observed native table using in-memory TSV framing.

    The adapter must supply the actual current table and its checked binding;
    serialization does not establish a native observation or perform an export.
    """
    if (not isinstance(table, dict) or set(table) != {'title', 'columns', 'rows'}
            or not isinstance(table['title'], str) or not isinstance(table['columns'], list)
            or not isinstance(table['rows'], list) or len(table['rows']) > 8192
            or any(not isinstance(row, list) or len(row) != len(table['columns']) for row in table['rows'])):
        raise GXWFormatError('当前原生标签表的标题、列或记录形状不完整。')
    records = [[table['title']], table['columns'], *table['rows']]
    characters = 0
    for record in records:
        characters += len(record) + 1
        for field in record:
            if not isinstance(field, str) or '\0' in field:
                raise GXWFormatError('当前原生标签表字段必须是不含 NUL 的文本。')
            characters += len(field)
            if characters > 4 * 1024 * 1024:
                raise GXWFormatError('当前原生标签表大小超出范围。')
    output = io.StringIO(newline='')
    csv.writer(output, delimiter='\t', quoting=csv.QUOTE_ALL, lineterminator='\r\n').writerows(records)
    try:
        raw = output.getvalue().encode('utf-16')
    except UnicodeError as error:
        raise GXWFormatError('当前原生标签表字段不是完整的 Unicode 文本。') from error
    result = read_active_labels_csv(raw, owner=owner, scope=scope, identity=identity,
        document_handle=document_handle, document_title=document_title, type_sources=type_sources)
    return {**result, 'read_transport': 'native_label_table'}


_NATIVE_LABEL_TYPE_ALIASES = {
    # Frozen decl_input.csv/dtypes.gxw and declarations-native-types.png bind
    # these display forms to declarations. GD2DataMng RT_STRING 0x2778..0x2790
    # supplies the exact localized spellings, not additional supported types.
    'zh-CN': {'Bit': 'BOOL', 'Word[Signed]': 'INT', 'Double Word[Signed]': 'DINT',
              'FLOAT (Single Precision)': 'REAL', 'Time': 'TIME',
              'Word[Unsigned]/Bit STRING[16-bit]': 'WORD',
              'Double Word[Unsigned]/Bit STRING[32-bit]': 'DWORD'},
    'en': {'Bit': 'BOOL', 'Word[Signed]': 'INT', 'Double Word[Signed]': 'DINT',
           'FLOAT (Single Precision)': 'REAL', 'Time': 'TIME', 'String': 'STRING',
           'Word[Unsigned]/Bit String[16-bit]': 'WORD',
           'Double Word[Unsigned]/Bit String[32-bit]': 'DWORD'},
    'ja': {'ビット': 'BOOL', 'ワード[符号付き]': 'INT', 'ダブルワード[符号付き]': 'DINT',
           '単精度実数': 'REAL', '時間': 'TIME', '文字列': 'STRING',
           'ワード[符号なし]/ビット列[16ビット]': 'WORD',
           'ダブルワード[符号なし]/ビット列[32ビット]': 'DWORD'},
}


def _native_label_scalar_type(text, aliases, language):
    prefix = {'en': 'String', 'ja': '文字列'}.get(language)
    sized = re.fullmatch(re.escape(prefix) + r'\(([1-9][0-9]{0,2})\)', text) if prefix else None
    if sized:
        # RT_STRING 0x2782 corresponds to CN STRING[%d]. The same captured
        # STRING[8] source and the existing Core length bound supply semantics.
        converted = 'STRING[' + sized[1] + ']'
        try:
            _type_parts(converted)
        except GXWFormatError:
            return text, None
        return converted, prefix
    converted = aliases.get(text, text)
    return converted, text if converted != text else None


def _native_label_type(text, aliases, *, language):
    array = re.fullmatch(r'(.+)\(([^()]*)\)', text)
    if array and '..' in array[2]:
        display, dimensions = array.groups()
        base, display_alias = _native_label_scalar_type(display, aliases, language)
        try:
            _, bounds = _type_parts('ARRAY [' + dimensions + '] OF ' + base)
        except GXWFormatError:
            return text, None
        converted = 'ARRAY [' + ','.join(f'{low}..{high}' for low, high in bounds) + '] OF ' + base
        return converted, display_alias
    return _native_label_scalar_type(text, aliases, language)


def read_active_label_grid(table, *, owner, scope, identity, document_handle, document_title, type_sources=None):
    """Project checked native grid cells without exporting or guessing types.

    Retain the original cells for re-projection after all current named types
    are observed. A display alias colliding with such a name stays unresolved.
    """
    if not isinstance(table, dict) or set(table) != {'columns', 'rows'}:
        raise GXWFormatError('当前原生标签网格必须包含明确的列和记录。')
    native_grid = deepcopy(table)
    arguments = dict(owner=owner, scope=scope, identity=identity,
        document_handle=document_handle, document_title=document_title)
    language = native_language(_binding(owner, scope, identity, document_handle, document_title)['identity'])
    # Reuse the existing framing, text, size, binding and type-source checks
    # before interpreting any grid-only decoration or display spelling.
    checked = read_active_labels_table(dict(title=owner, **native_grid), **arguments, type_sources=type_sources)
    _, columns, records = parse_active_label_csv(checked['raw_bytes'])
    canonical = canonical_label_columns(columns, language=language)
    type_column = canonical.index('数据类型')
    buttons = [index for index, column in enumerate(columns) if column in (' ', '\u3000')]
    if (buttons != [type_column + 1]
            or any(record[buttons[0]] != '' for record in native_grid['rows'])):
        raise GXWFormatError('当前原生标签网格没有唯一且为空的数据类型按钮列。')
    button, = buttons
    columns = columns[:button] + columns[button + 1:]
    aliases = _NATIVE_LABEL_TYPE_ALIASES[language]
    sources = [] if type_sources is None else type_sources
    named = {source['owner'].casefold() for source in sources}
    rows, ambiguous, blocked = [], [], set()
    for index, record in enumerate(records):
        row = record[:button] + record[button + 1:]
        converted, display_alias = _native_label_type(row[type_column], aliases, language=language)
        if display_alias is not None and display_alias.casefold() in named:
            blocked.add(display_alias.casefold())
            ambiguous.append(dict(code='ambiguous_native_type_alias', csv_row=index,
                display_type=row[type_column], candidates=[converted, row[type_column]]))
        else:
            row[type_column] = converted
        rows.append(row)
    available = [source for source in sources if source['owner'].casefold() not in blocked]
    result = read_active_labels_table(dict(title=owner, columns=columns, rows=rows),
        **arguments, type_sources=available)
    if ambiguous:
        result['issues'].extend(ambiguous)
        result['status'] = 'partial'
    return {**result, 'native_grid': native_grid}


def read_active_labels_csv(raw, *, owner, scope, identity, document_handle, document_title, type_sources=None):
    """Read named declarations from the active table, retaining every CSV field.

    A named compound type requires another current active source observation
    with the same project identity. Saved-project snapshots are not accepted.
    The ordinary-variable initializer is absent from this native CSV format.
    """
    binding = _binding(owner, scope, identity, document_handle, document_title)
    sources = [] if type_sources is None else type_sources
    if not isinstance(sources, (list, tuple)):
        raise GXWFormatError('当前类型来源必须是活动声明观察。')
    for source in sources:
        if (not isinstance(source, dict) or source.get('origin') != 'active_native_labels'
                or source.get('owner_kind') not in ('fb', 'structure')
                or not isinstance(source.get('owner'), str)
                or not isinstance(source.get('source_binding'), dict)
                or source['source_binding'].get('owner') != source['owner']
                or source['source_binding'].get('owner_kind') != source['owner_kind']
                or source['source_binding'].get('identity') != identity):
            raise GXWFormatError('命名类型来源不属于同一当前原生工程。')
    title, header, records = parse_active_label_csv(raw)
    canonical_header = canonical_label_columns(header, language=native_language(identity))
    required = {'标签名', '数据类型', '常量', '注释'} | (set() if scope == 'structure' else {'类'})
    if not required <= set(canonical_header):
        raise GXWFormatError('原生标签 CSV 缺少声明的必需列。')
    issues = [dict(code='unknown_column', column=column) for column, canonical in zip(header, canonical_header)
              if canonical not in _LABEL_COLUMNS]
    classes = {code: name for name, code in CLASS_CODES.items()}
    declarations, names = [], set()
    for index, values in enumerate(records):
        fields = dict(zip(canonical_header, values, strict=True))
        name = fields['标签名']
        if not name or name.casefold() in names:
            raise GXWFormatError('原生标签 CSV 存在缺失或重名声明。')
        names.add(name.casefold())
        if 'project_codepage' in identity:
            strict_project_text(name, identity['project_codepage'])
            strict_project_text(fields['注释'], identity['project_codepage'])
        class_code = None if scope == 'structure' else {**CLASS, **CLASS_CODES}.get(fields['类'])
        expected = (8, 9) if scope == 'global' else ((1, 2) if scope == 'program' else tuple(CLASS.values()))
        class_status = ('member' if scope == 'structure' else
                        'resolved' if class_code in expected else 'unresolved')
        type_info = _type_observation(fields['数据类型'], sources)
        if class_status == 'unresolved':
            issues.append(dict(code='unknown_or_foreign_class', csv_row=index, class_name=fields['类']))
        if type_info['status'] == 'unresolved':
            issues.append(dict(code='unresolved_type', csv_row=index, data_type=fields['数据类型']))
        constant = class_code in (2, 9, 11)
        declarations.append(dict(name=name, data_type=fields['数据类型'], csv_row=index,
            class_name=classes.get(class_code), class_code=class_code, class_status=class_status,
            kind=type_info.get('kind', 'unresolved'), type_status=type_info['status'], type=type_info,
            csv_constant=fields['常量'], initial_value=fields['常量'] if constant else None,
            initial_value_status='observed_constant' if constant else 'unavailable_in_csv',
            comment=fields['注释'], device=fields.get('软元件'), iec_address=fields.get('地址'),
            raw_fields=dict(zip(header, values, strict=True))))
    result = dict(status='partial' if issues else 'available', origin='active_native_labels',
        owner=owner, owner_kind=scope, scope='global' if scope == 'global' else
            'structure' if scope == 'structure' else 'local',
        source_binding=binding, declarations=declarations, issues=issues,
        csv_title=title[0], csv_columns=list(header), canonical_csv_columns=canonical_header, raw_bytes=raw,
        compilation='not_run', execution='not_run', save_performed=False)
    if scope == 'fb':
        rows = [SimpleNamespace(name=row['name'], class_code=row['class_code'], data_type=row['data_type'],
                                record_id=row['csv_row'], offset=None) for row in declarations]
        definition = dict(name=owner, source_kind='project_labels',
                          declarations=SimpleNamespace(rows=rows), return_type_raw=None)
        masks = {row['data_type']: row['type'].get('type_mask') for row in declarations}
        try:
            inputs, outputs = interface(definition, resolve_type=lambda value: masks[value])
        except GXWFormatError as error:
            result.update(status='partial', interface_status='unavailable', interface_diagnostic=str(error))
        else:
            declared = {row['name']: row for row in declarations}
            for direction, ports in (('input', inputs), ('output', outputs)):
                for port in ports:
                    row = declared[port['name']]
                    port.update(csv_row=row['csv_row'], type_status=row['type_status'],
                                direction='in_out' if port['class_code'] == 5 else direction)
            result.update(interface_status='partial' if issues else 'available',
                interface=dict(inputs=inputs, outputs=outputs, graphic_port_order='unverified',
                               declaration_order='csv_observation', source_binding=deepcopy(binding)))
    return result


def read_active_st_text(text, *, owner, kind, identity, document_handle, document_title, editor_handle):
    """Keep the exact RichEdit text; no normalization or saved-file fallback."""
    binding = _binding(owner, kind, identity, document_handle, document_title, st=True)
    if type(editor_handle) is not int or editor_handle <= 0:
        raise GXWFormatError('当前 ST 原生文本控件归属不完整。')
    if 'project_codepage' in identity:
        strict_project_text(text, identity['project_codepage'])
    return dict(status='available', origin='active_native_st', owner=owner, owner_kind=kind,
                source_binding={**binding, 'editor_handle': editor_handle}, st_text=_st_implementation(text),
                line_endings='native_control_exact', compilation='not_run', execution='not_run', save_performed=False)

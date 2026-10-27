# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Normalize vendor UI observations at the GX Works2 adapter boundary.

Raw captions, control IDs and MSAA states are retained for replay; callers
consume named ranges and declaration fields. This module performs no UI actions.
"""
from __future__ import annotations

from copy import deepcopy
import re

from gxw.models import GXWFormatError


def native_allocation_controls(control_profile: str) -> dict:
    """Static three-language resource schemas, not CPU dispatch evidence.

    A driver selects a schema only after inspecting the actual owned dialog.
    Grid handles and control IDs are dynamic. Its field bindings come from
    the current row observations, never template control IDs 14252/14253.
    """
    schemas = {
        'device_range_classic': {'dialog_id': 10002,
            'devices': {'D': {'selector_id': 14014, 'start_id': 14017, 'end_id': 14020},
                        'M': {'selector_id': 14043, 'start_id': 14044, 'end_id': 14047}}},
        'device_range_fx': {'dialog_id': 10003,
            'devices': {'D': {'selector_id': 14066, 'start_id': 14069, 'end_id': 14072},
                        'M': {'selector_id': 14094, 'start_id': 14096, 'end_id': 14099}}},
        'device_range_grid': {'dialog_id': 10004, 'runtime_control_ids': 'dynamic',
            'grid_roles': {'allocation': {'columns': 8}, 'parameter': {'columns': 3}},
            'field_columns': {'start': 5, 'end': 6, 'total': 7}},
    }
    if not isinstance(control_profile, str) or control_profile not in schemas:
        raise GXWFormatError('native allocation control profile is unverified')
    return {'control_profile': control_profile, 'status': 'static_resource_observed',
            'software_version': '1.635.0.1', **schemas[control_profile]}


def _bounded_allocation_grid_rows(rows, columns):
    """Copy only the explicit bounded MSAA transport, rejecting unknown shapes."""
    if not isinstance(rows, list) or not 2 <= len(rows) <= 256:
        raise GXWFormatError('native allocation grid row count is outside the observed bound')
    for index, row in enumerate(rows):
        if (not isinstance(row, dict) or set(row) != {'native_row', 'cells'}
                or type(row['native_row']) is not int or row['native_row'] != index
                or not isinstance(row['cells'], list) or len(row['cells']) != columns):
            raise GXWFormatError('native allocation grid rows or columns are incomplete or unordered')
        for cell in row['cells']:
            if (not isinstance(cell, dict) or set(cell) != {'name', 'value', 'state'}
                    or any(not isinstance(cell[key], str) or '\0' in cell[key]
                           or len(cell[key]) > 4096 for key in ('name', 'value'))
                    or type(cell['state']) is not int or not 0 <= cell['state'] <= 0xffffffff):
                raise GXWFormatError('native allocation grid cell text or accessibility state is invalid')
            try:
                (cell['name'] + cell['value']).encode('utf-16le')
            except UnicodeError as error:
                raise GXWFormatError('native allocation grid has invalid UTF-16 cell text') from error
    return deepcopy(rows)


def _allocation_decimal_pair(first, last, *, total=None):
    if (not re.fullmatch('[0-9]+', first) or not re.fullmatch('[0-9]+', last)
            or not 0 <= int(first) <= int(last) < 2**31
            or total is not None and (not re.fullmatch('[0-9]+', total)
                                      or int(total) != int(last) - int(first) + 1)):
        raise GXWFormatError('native allocation range or total is not a consistent decimal interval')
    return [int(first), int(last)]


def read_native_allocation_grid(rows, *, native_ui_language='zh-CN') -> dict:
    """Resolve the current grid's ordinary D/M rows without checkbox inference.

    Chinese Q03UDV provides the native row witness. Other languages use exact
    corresponding resource captions and retain their separate runtime status.
    Uninterpreted cell values and state bits remain in ``grid_rows``.
    """
    from gxw.native_locale import ui_text
    raw = _bounded_allocation_grid_rows(rows, 8)
    text = lambda key: ui_text('allocation_grid_' + key, native_ui_language)
    header = ['', '', text('device'), text('digit'), text('assign'), text('range'), text('range'), text('total')]
    second = ['', '', text('device'), text('digit'), text('selection'), text('start'), text('end'), text('total')]
    for row, values in zip(raw[:2], (header, second)):
        if [cell['name'] for cell in row['cells']] != header or [cell['value'] for cell in row['cells']] != values:
            raise GXWFormatError('native allocation grid has no complete ordered localized headers')
    group_order = ('word', 'bit', 'pointer', 'timer', 'retentive', 'counter')
    group_names = {text(key): key for key in group_order}
    devices = {
        'word': {'var': {'D', 'W', 'R'}, 'retain': {text(key) for key in ('d_latch', 'w_latch', 'zr_latch')}},
        'bit': {'var': {'M', 'B'}, 'retain': {text(key) for key in ('l_latch', 'b_latch')}},
        'pointer': {'var': {'P'}, 'retain': set()},
        'timer': {'var': {'T'}, 'retain': {text('t_latch')}},
        'retentive': {'var': {'ST'}, 'retain': {text('st_latch')}},
        'counter': {'var': {'C'}, 'retain': {text('c_latch')}},
    }
    hexadecimal = {'W', 'B', text('w_latch'), text('b_latch')}
    group, scope, groups, ranges, fields = None, None, [], {}, {}
    for row in raw[2:]:
        cells = row['cells']
        values = [cell['value'] for cell in cells]
        if [cell['name'] for cell in cells] != header:
            raise GXWFormatError('native allocation cell captions belong to another column or language')
        if values[0] in group_names:
            group = group_names[values[0]]
            if (len(groups) >= len(group_order) or groups != list(group_order[:len(groups)])
                    or group != group_order[len(groups)] or any(values[1:])):
                raise GXWFormatError('native allocation device groups changed order or ownership')
            groups.append(group)
            scope = None
            continue
        if group is None:
            raise GXWFormatError('native allocation device row has no current group')
        if values[0] == text('var'):
            scope = 'var'
        elif values[0] == text('var_retain'):
            scope = 'retain'
        elif values[0]:
            raise GXWFormatError('native allocation row has an unknown declaration range group')
        if (scope is None or values[2] not in devices[group][scope]
                or values[3] != ('16' if values[2] in hexadecimal else '10')
                or scope == 'var' and values[1]
                or scope == 'retain' and values[1] not in ('', text('latch1'), text('latch2'))):
            raise GXWFormatError('native allocation row has a foreign device, radix or VAR group')
        device = values[2]
        if device in ('D', 'M'):
            if scope != 'var' or values[0] != text('var') or device in fields:
                raise GXWFormatError('native allocation has no unique ordinary D/M declaration row')
            ranges[device] = _allocation_decimal_pair(values[5], values[6], total=values[7])
            fields[device] = [dict(grid_role='allocation', native_row=row['native_row'], native_column=column,
                                  text=cells[column]['value'], state=cells[column]['state'] & ~0x6)
                              for column in (5, 6)]
    if groups != list(group_order) or set(ranges) != {'D', 'M'}:
        raise GXWFormatError('native allocation grid is incomplete or has no unique ordinary D/M ranges')
    return {'control_profile': 'device_range_grid', 'coverage': 'observed_D_M_grid',
            'ranges': ranges, 'raw_fields': fields, 'grid_rows': raw,
            'selection_status': 'not_observed_in_msaa'}


def read_native_allocation_grids(grid_rows, *, native_ui_language='zh-CN') -> dict:
    """Bind the two current native grid roles and retain every observed cell."""
    from gxw.native_locale import ui_text
    if not isinstance(grid_rows, dict) or set(grid_rows) != {'allocation', 'parameter'}:
        raise GXWFormatError('native allocation requires both current allocation and PLC-parameter grids')
    result = read_native_allocation_grid(grid_rows['allocation'], native_ui_language=native_ui_language)
    parameter = _bounded_allocation_grid_rows(grid_rows['parameter'], 3)
    if len(parameter) != len(result['grid_rows']):
        raise GXWFormatError('native allocation and parameter grid row counts differ')
    caption = ui_text('allocation_grid_parameter', native_ui_language)
    setting = ui_text('allocation_grid_parameter_range', native_ui_language)
    for row in parameter:
        if [cell['name'] for cell in row['cells']] != [caption] * 3:
            raise GXWFormatError('native parameter grid column captions belong to another language')
    if ([cell['value'] for cell in parameter[0]['cells']] != [caption] * 3
            or [cell['value'] for cell in parameter[1]['cells']] != [setting] * 3):
        raise GXWFormatError('native allocation parameter grid has no complete localized headers')
    parameter_ranges = {}
    for device, fields in result['raw_fields'].items():
        row = parameter[fields[0]['native_row']]
        values = [cell['value'] for cell in row['cells']]
        if values[1] != ui_text('allocation_grid_separator', native_ui_language):
            raise GXWFormatError('native allocation parameter range separator is unverified')
        bounds = _allocation_decimal_pair(values[0], values[2])
        current = result['ranges'][device]
        if not bounds[0] <= current[0] <= current[1] <= bounds[1]:
            raise GXWFormatError('native allocation range exceeds its current PLC-parameter interval')
        parameter_ranges[device] = bounds
    result['grid_rows'] = {'allocation': result['grid_rows'], 'parameter': parameter}
    result['parameter_ranges'] = parameter_ranges
    return result


def native_allocation_grid_state(grid_rows) -> dict:
    """Ignore only transient selected/focused MSAA state, retaining other bits."""
    if not isinstance(grid_rows, dict) or set(grid_rows) != {'allocation', 'parameter'}:
        raise GXWFormatError('native allocation grid state requires both observed grid roles')
    result = {role: _bounded_allocation_grid_rows(grid_rows[role], columns)
              for role, columns in (('allocation', 8), ('parameter', 3))}
    for rows in result.values():
        for row in rows:
            for cell in row['cells']:
                cell['state'] &= ~0x6
    return result


def native_allocation_observation_state(observation) -> dict:
    """Compare observed setting values without transient native handles.

    The caller separately binds project/process identity. This projection is
    for the repeated field reads around a dialog or its visible completion.
    Original raw grid rows remain unchanged on the supplied observation.
    """
    if not isinstance(observation, dict):
        raise GXWFormatError('native allocation observation requires explicit field data')
    keys = ('allocation_profile', 'control_profile', 'coverage', 'ranges', 'raw_fields',
            'grid_rows', 'parameter_ranges', 'selection_status')
    result = {key: deepcopy(observation[key]) for key in keys if key in observation}
    if 'grid_rows' in result:
        result['grid_rows'] = native_allocation_grid_state(result['grid_rows'])
    return result


def label_column_identities(table):
    from gxw.native_locale import canonical_label_columns, native_language
    columns = table['csv_columns']
    canonical = canonical_label_columns(columns, language=native_language(table['source_binding']['identity']))
    if table.get('canonical_csv_columns', canonical) != canonical:
        raise GXWFormatError('Saved label comparison has inconsistent localized column metadata')
    identities = {'类': 'class', '标签名': 'name', '数据类型': 'data_type', '常量': 'constant',
                  '注释': 'comment', '软元件': 'device', '地址': 'iec_address', '备注': 'remark',
                  '关系': 'relation', '系统标签名': 'system_name', '属性': 'attribute'}
    return {column: identities.get(key, key) for column, key in zip(columns, canonical, strict=True)}


def allocation_fields_editable(observation, devices):
    grid = observation.get('control_profile') == 'device_range_grid'
    return not any((field['state'] & 0x41 if grid else not field.get('enabled'))
                   for device in devices for field in observation.get('raw_fields', {}).get(device, []))


def observed_pointer_range(configuration, identity):
    from gxw.native_locale import native_language, ui_text
    language = native_language(identity)
    observed = read_native_allocation_grids(configuration.get('grid_rows'), native_ui_language=language)
    pointers = [row['cells'] for row in observed['grid_rows']['allocation'] if row['cells'][2]['value'] == 'P']
    if (len(pointers) != 1 or pointers[0][0]['value'] != ui_text('allocation_grid_var', language)
            or any(not cell['value'].isdecimal() for cell in pointers[0][5:8])
            or any(cell['state'] & ~0x6 for cell in pointers[0][5:8])):
        return None
    values = [cell['value'] for cell in pointers[0][5:8]]
    low, high, total = map(int, values)
    if values != [str(low), str(high), str(total)]:
        return None
    return [low, high] if 0 <= low <= high < 0xffffffff and total == high - low + 1 else None


def validate_allocation_observation(configuration):
    from gxw.native_locale import native_language
    controls = native_allocation_controls(configuration['control_profile'])
    if configuration['control_profile'] == 'device_range_grid':
        if configuration.get('coverage') != 'observed_D_M_grid' or not isinstance(configuration.get('identity'), dict):
            raise GXWFormatError('Live grid allocation requires its current bound native observation')
        parsed = read_native_allocation_grids(configuration.get('grid_rows'),
            native_ui_language=native_language(configuration['identity']))
        if (configuration.get('raw_fields') != parsed['raw_fields'] or configuration.get('ranges') != parsed['ranges']
                or configuration.get('parameter_ranges', parsed['parameter_ranges']) != parsed['parameter_ranges']):
            raise GXWFormatError('Live allocation field bindings differ from the actual native grid rows')
        return
    if configuration.get('coverage') != 'observed_D_M_controls':
        raise GXWFormatError('Live allocation control schema has no current native observation')
    fields, ranges = configuration.get('raw_fields'), configuration.get('ranges')
    if not isinstance(fields, dict) or set(fields) != {'D', 'M'} or not isinstance(ranges, dict) or set(ranges) != set(fields):
        raise GXWFormatError('Live allocation requires exactly the observed D/M range fields')
    for device, expected in controls['devices'].items():
        rows = fields[device]
        if (not isinstance(rows, list) or len(rows) != 2 or any(not isinstance(row, dict) for row in rows)
                or [row.get('control_id') for row in rows] != [expected['start_id'], expected['end_id']]
                or any(type(row.get('enabled')) is not bool or not isinstance(row.get('text'), str) for row in rows)):
            raise GXWFormatError('Live allocation field IDs differ from the observed native resource schema')
        numeric = [int(row['text']) for row in rows] if all(re.fullmatch('[0-9]+', row['text']) for row in rows) else None
        if ranges[device] != numeric:
            raise GXWFormatError('Live allocation numeric range differs from actual native field text')
        if configuration.get('status') == 'available' and (numeric is None or not 0 <= numeric[0] <= numeric[1] < 2**31):
            raise GXWFormatError('Available native allocation fields require ordered integer range values')

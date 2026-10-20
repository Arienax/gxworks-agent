"""AllocationEditor operations on an explicitly bound native desktop."""
from __future__ import annotations
import re
import time
from .project_profiles import native_project_profile
from .engineering_errors import (NativeEngineeringDriverError)
from .engineering_files import (_same_text)
from .engineering_observations import (_allocation_grid_rows, _native_focus, _allocation_cell, _allocation_cell_point)


class AllocationEditor:
    def __init__(self, desktop):
        self.desktop = desktop

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
        self.desktop._foreground(dialog.handle)
        point = _allocation_cell_point(self.desktop.session.window_handle, grid.handle,
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
        self.desktop._wait(selected, '原生分配单元格的选择与实际焦点未确认。')
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
        editor = self.desktop._wait(editor_ready, '原生分配单元格没有形成已确认的内嵌编辑器。')
        self.desktop.inspect()
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
        if not self.desktop.cpu.startswith('FX'):
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
                native_ui_language=getattr(self.desktop, 'native_ui_language', 'zh-CN'))
            return {**fields, 'allocation_profile': project_allocation_profile(self.desktop.cpu)['allocation_profile']}
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
        if self.desktop.cpu != 'FX3U/FX3UC':
            result.update(control_profile='device_range_fx', coverage='observed_D_M_controls',
                allocation_profile=project_allocation_profile(self.desktop.cpu)['allocation_profile'])
        return result

    def read_configuration(self):
        """Observe the current native range dialog and cancel it without Save."""
        if native_project_profile(self.desktop.cpu) is None or not native_project_profile(self.desktop.cpu).program_supported:
            return dict(status='unavailable', origin='active_native_configuration',
                        reason='native_configuration_controls_not_verified_for_cpu', cpu=self.desktop.cpu,
                        driver_saved=False)
        identity = self.desktop.inspect()
        self.desktop._command(0x685)
        dialog = self.desktop._dialog('自动分配软元件设置')
        close_dialog = True
        try:
            fields = self.desktop._configuration_fields(dialog)
            if self.desktop.inspect() != identity:
                raise NativeEngineeringDriverError('读取分配设置期间原生工程身份已改变。')
            visible = self.desktop._visible('自动分配软元件设置', dialog=dialog, **fields)
            from gxw.project_metadata import native_allocation_observation_state
            if native_allocation_observation_state(self.desktop._configuration_fields(dialog)) != native_allocation_observation_state(fields):
                close_dialog = False
                raise NativeEngineeringDriverError('分配设置字段在读取期间已改变，保留当前对话框。',
                    observation={'source_status': 'requires_reconciliation'})
            details = dict(fields)
            coverage = details.pop('coverage', 'verified_D_M_controls_only')
            return dict(status='available' if all(value is not None for value in fields['ranges'].values()) else 'partial',
                origin='active_native_configuration', identity=identity, cpu=self.desktop.cpu,
                dialog_handle=dialog.handle, **details, driver_saved=False,
                coverage=coverage, visible_completion=visible)
        finally:
            # Only cancel the same owned dialog while its project identity is
            # still intact. A changed target remains for explicit reconciliation.
            if self.desktop.inspect() != identity:
                raise NativeEngineeringDriverError('读取分配设置期间原生工程身份已改变。',
                    observation={'source_status': 'requires_reconciliation'})
            if close_dialog:
                self.desktop._click(dialog, 2)
                self.desktop._wait(lambda: not self.desktop._dialogs(), '读取后的原生分配设置页尚未关闭。')

    def _finish_allocation_notices(self):
        """Observe confirmations for an already posted allocation commit."""
        notices, acknowledged = [], set()
        deadline = time.monotonic() + self.desktop.timeout
        while self.desktop._dialogs() and time.monotonic() < deadline:
            dialogs = [d for d in self.desktop._dialogs() if d.is_enabled()]
            handled = False
            for current in dialogs:
                if current.handle in acknowledged:
                    continue
                text = self.desktop._dialog_text(current)
                normalized = text.replace('\r\n', '\n').replace('\r', '\n')
                overlap = re.escape(self.desktop._text('allocation_overlap_format')).replace(
                    re.escape('%1!s!'), r'[^\r\n]{1,80}')
                if (re.fullmatch(overlap, normalized)
                        or _same_text(text, self.desktop._text('allocation_rebuild_notice'))):
                    notices.append(text)
                    acknowledged.add(current.handle)
                    self.desktop._click(current, 6)
                    handled = True
                    break
            if not handled:
                time.sleep(.05)
        self.desktop._wait(lambda: not self.desktop._dialogs(), '自动分配范围没有完成，保留原生提示。')
        return notices

    def _configure(self, plan):
        from copy import deepcopy
        from gxw.models import GXWFormatError
        from gxw.project_metadata import native_allocation_observation_state
        if 'parameter_after' in plan:
            raise NativeEngineeringDriverError('此前台驱动尚未验证 PLC 参数容量编辑。')
        if any(row['device'] not in ('D', 'M') or any(v < 0 for v in row['after']) for row in plan['ranges']):
            raise NativeEngineeringDriverError('此前台范围字段或禁用方式尚未验证。')
        self.desktop._command(0x685)
        dialog = self.desktop._dialog('自动分配软元件设置')
        before = self.desktop._configuration_fields(dialog)
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
        self.desktop._last_compile_run = None
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
                    self.desktop._edit_allocation_grid_field(dialog, field, after)
                    def committed():
                        try:
                            current = self.desktop._configuration_fields(dialog)
                        except GXWFormatError:
                            return None  # The native total can update after its range cell.
                        return current if native_allocation_observation_state(current) == native_allocation_observation_state(target) else None
                    rolling = self.desktop._wait(committed, '原生分配单元格提交后未通过整表保留核对。')
        else:
            for row in plan['ranges']:
                for field, after in zip(before['raw_fields'][row['device']], row['after']):
                    editor = dialog.child_window(control_id=field['control_id'], class_name='Edit')
                    if not editor.is_enabled() or editor.window_text() != field['text']:
                        raise NativeEngineeringDriverError('原生自动分配字段状态在修改前已改变。')
                    editor.set_edit_text(str(after))
                    if editor.window_text() != str(after):
                        raise NativeEngineeringDriverError('原生自动分配字段回读不同。')
        if native_allocation_observation_state(self.desktop._configuration_fields(dialog)) != native_allocation_observation_state(expected):
            raise NativeEngineeringDriverError('应用前原生分配设置未通过请求字段及保留字段核对。')
        self.desktop._click(dialog, 1)
        notices = self.desktop._finish_allocation_notices()
        current = self.desktop.read_configuration()
        if native_allocation_observation_state(current) != native_allocation_observation_state(expected):
            raise NativeEngineeringDriverError('重新打开的原生分配范围与本步设置不同，或未请求字段已改变。')
        return {**current['visible_completion'], 'configuration': current, 'notices': notices}

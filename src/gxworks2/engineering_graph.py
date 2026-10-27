# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""GraphEditor operations on an explicitly bound native desktop."""
from __future__ import annotations
import time
from .engineering_errors import (NativeEngineeringDriverError)
from .engineering_grid import (_grid_calibration)


class GraphEditor:
    def __init__(self, desktop):
        self.desktop = desktop

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
            self.desktop._idle()
            title = self.desktop._program_title(owner, kind, 'fbd')
            if self.desktop._active_document().window_text().rstrip() != title:
                self.desktop._program_page(owner, kind, 'fbd')
            before = adapter.read_graph(self.desktop, owner=owner, kind=kind)
            source = graph_source_state(before)
            if network >= len(before['networks']):
                raise NativeEngineeringDriverError('请求的网络不在当前原生图形范围内；未发送跳转命令。')
            binding_keys = ('identity', 'data_id', 'document_handle', 'view_handle', 'owner', 'owner_kind')
            binding = {key: before.get(key) for key in binding_keys}
            if (binding['owner'] != owner or binding['owner_kind'] != kind
                    or binding['identity'] != self.desktop.inspect()
                    or not isinstance(binding['data_id'], str) or not binding['data_id']
                    or any(type(binding[key]) is not int or binding[key] <= 0
                           for key in ('document_handle', 'view_handle'))):
                raise NativeEngineeringDriverError('当前图形工程及页面绑定不完整。')
            frame, pid = self.desktop.session.window_handle, self.desktop.session.process_id

            def same_page():
                document, view = binding['document_handle'], binding['view_handle']
                return (win32gui.IsWindow(document) and win32gui.IsWindow(view)
                    and win32gui.IsChild(frame, document) and win32gui.IsChild(document, view)
                    and win32process.GetWindowThreadProcessId(document)[1] == pid
                    and win32process.GetWindowThreadProcessId(view)[1] == pid
                    and self.desktop._active_document().handle == document
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
            current = checked(adapter.read_graph(self.desktop, owner=owner, kind=kind, data_id=binding['data_id']))
            if current['active_network'] == network:
                return current
            # BasicLib RT_DIALOG10012 is shared by the native FBD Jump command.
            # Its editable ComboBox owns Edit1001; ComboWrapper has no setter.
            sent = True
            self.desktop._command(0x606)
            def jump_dialog():
                rows = self.desktop._dialogs()
                if not rows:
                    return None
                if len(rows) != 1 or rows[0].window_text() != self.desktop._caption('跳转'):
                    raise NativeEngineeringDriverError('网络定位出现了非本次跳转的原生对话框。')
                return rows[0]
            dialog = self.desktop._wait(jump_dialog, '没有确认本次原生网络跳转对话框。')
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
                        or caption is not None and win32gui.GetWindowText(handle) != self.desktop._caption(caption)):
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
                dialogs = self.desktop._dialogs()
                if (not same_page() or not win32gui.IsWindow(dialog_handle)
                        or [int(row.handle) for row in dialogs] != [dialog_handle]):
                    raise NativeEngineeringDriverError('填写网络编号期间绑定页面或对话框已改变。')
                return editor.window_text() == requested and combo.window_text() == requested
            self.desktop._wait(input_matches, '原生网络跳转编号没有回读为请求值。')
            if win32gui.GetDlgItem(dialog_handle, 10014) != controls[10014]:
                raise NativeEngineeringDriverError('网络跳转确认按钮已改变。')
            self.desktop._click(dialog, 10014)
            # Keep the integer HWND: resolving a WindowSpecification after its
            # dialog has closed can wait for a different future dialog.
            self.desktop._wait(lambda: not win32gui.IsWindow(dialog_handle), '本次原生网络跳转对话框尚未结束。')
            def arrived():
                self.desktop._idle()
                after = checked(adapter.read_graph(self.desktop, owner=owner, kind=kind, data_id=binding['data_id']))
                return after if after['active_network'] == network else None
            return self.desktop._wait(arrived, '原生网络跳转没有回读到请求的网络；未重复发送。')
        except Exception as error:
            observation = getattr(error, 'observation', {})
            raise NativeEngineeringDriverError(str(error), observation={**observation,
                'status': 'unconfirmed' if sent else 'rejected', 'network_navigation_started': sent,
                'requested_network': network, 'source_mutation_requested': False, 'save_performed': False}) from error

    def _choose_label(self, name, *, expected_type=None):
        dialog = self.desktop._dialog('标签登录/选择')
        self.desktop.ui._select_native_combo(dialog.child_window(control_id=6651, class_name='ComboBox'), self.desktop._text('chooser_all'))
        type_filter = dialog.child_window(control_id=6660, class_name='ComboBox')
        if type_filter.is_enabled() and 'ANY' in type_filter.item_texts():
            # Placeholder terminals retain the previous chooser's Bit filter.
            # This is the search filter, not the declaration's data-type field.
            self.desktop.ui._select_native_combo(type_filter, 'ANY')
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
        actual, actual_type = self.desktop._wait(selected, '原生变量名称或实例类型与当前声明不同。')
        # Selecting an existing row must never move it into another scope.
        move = dialog.child_window(control_id=6654, class_name='Button')
        if move.get_check_state():
            raise NativeEngineeringDriverError('原生标签选择器启用了声明移动，未应用。')
        self.desktop._click(dialog, 1)
        ready_since = [None]
        def applied():
            remaining = [d for d in self.desktop._dialogs() if d.handle != dialog.handle and d.is_enabled()]
            if remaining:
                raise NativeEngineeringDriverError('原生变量绑定出现额外提示。', observation={
                    'dialogs': [{'title': d.window_text(), 'message': self.desktop._dialog_text(d)} for d in remaining]})
            if not dialog.is_enabled():
                ready_since[0] = None
                return False
            if ready_since[0] is None:
                ready_since[0] = time.monotonic()
            return time.monotonic() - ready_since[0] >= .25
        self.desktop._wait(applied, '原生标签应用没有返回可编辑状态。')
        self.desktop._click(dialog, 2)
        self.desktop._wait(lambda: not self.desktop._dialogs(), '原生变量绑定没有结束。')
        return {'name': actual, 'native_type': actual_type}

    def _graph_view(self, name):
        document = self.desktop._program_page(name)
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
        current = self.desktop._graph_grid(view, include_lattice=True)
        if any(current[key] != grid[key] for key in ('pitch', 'x', 'y', 'width', 'height')):
            raise NativeEngineeringDriverError('新增前实际图形网格或视口发生变化，未发出插入。')
        points = {tuple(point) for point in current['lattice_points']}
        for column, row in ((x+1,y+1),(x+2,y+1),(x+1,y+2),(x+2,y+2)):
            if self.desktop._graph_point(current, column, row) not in points:
                raise NativeEngineeringDriverError('新增区域不在已观察到的首个网络点阵中，未发出插入。')
        return self.desktop._graph_point(current, x+2, y+1)

    def _graph_drag(self, view, grid, source, target):
        import win32process
        if (win32process.GetWindowThreadProcessId(view.handle)[1] != self.desktop.session.process_id
                or not view.is_visible() or not view.is_enabled()
                or any(not (5 < point[0] < grid['width'] - 28
                            and 5 < point[1] < grid['height'] - 28
                            and point[0] < grid.get('calibrated_x_limit', grid['width']))
                       for point in (source, target))):
            raise NativeEngineeringDriverError('图形拖拽没有明确绑定在当前原生编辑区域内。')
        self.desktop._idle()
        self.desktop._foreground()
        # pywinauto drag defaults to screen coordinates, unlike click_input.
        # Both points here come from this verified view's captured dot grid.
        view.drag_mouse_input(src=source, dst=target, absolute=False)

    def _graph_context(self, view, grid, box):
        import win32gui
        import win32process
        self.desktop._idle()
        self.desktop._foreground()
        if (win32process.GetWindowThreadProcessId(view.handle)[1] != self.desktop.session.process_id
                or not view.is_visible() or not view.is_enabled()):
            raise NativeEngineeringDriverError('待选择的图形视图没有绑定当前工程。')
        view.click_input(button='right', coords=self.desktop._graph_point(grid, box[0] + .5, box[1] + 1))
        def popup():
            handles = []
            def found(handle, _):
                if (win32gui.IsWindowVisible(handle)
                        and win32process.GetWindowThreadProcessId(handle)[1] == self.desktop.session.process_id
                        and win32gui.GetClassName(handle) == 'XTPPopupBar'):
                    handles.append(handle)
            win32gui.EnumWindows(found, None)
            return handles[0] if len(handles) == 1 else None
        handle = self.desktop._wait(popup, '没有确认当前图形对象的原生上下文菜单。')
        if (win32gui.GetForegroundWindow() not in (handle, self.desktop.session.window_handle)
                or popup() != handle):
            raise NativeEngineeringDriverError('当前图形对象菜单不再拥有前台焦点。')
        return popup

    def _select_graph_node(self, view, grid, box):
        import win32process
        self.desktop._idle()
        self.desktop._foreground()
        if (win32process.GetWindowThreadProcessId(view.handle)[1] != self.desktop.session.process_id
                or not view.is_visible() or not view.is_enabled()):
            raise NativeEngineeringDriverError('待选择的图形视图没有绑定当前工程。')
        view.click_input(coords=self.desktop._graph_point(grid, box[0] + .5, box[1] + 1))

    def _draw_graph_wire(self, view, grid, anchor):
        from pywinauto.keyboard import send_keys
        # Native Interconnect Mode draws one orthogonal segment per drag.
        # A vertical T requires its horizontal receiving leg to exist first.
        # Start at the receiving port and finish at the existing driver/tap.
        # Drag just inside the observed lattice cell's native hit area rather
        # than on its boundary; the saved branch witness verifies this offset.
        points = list(reversed([anchor['from']['point'], *anchor['via'], anchor['to']['point']]))
        self.desktop._command(0x644)
        for start, end in zip(points, points[1:]):
            source = self.desktop._graph_point(grid, *start)
            target = self.desktop._graph_point(grid, *end)
            self.desktop._graph_drag(view, grid, (source[0] + 2, source[1] + 2),
                             (target[0] + 2, target[1] + 2))
        send_keys('{ESC}', vk_packet=False)
        self.desktop._command(0x643)

    def _edit_fbd_graph(self, command, prepared):
        from gxw.models import GXWFormatError
        from gxw.visible_graph import graph_edit_step, verify_graph_edit_step, verify_graph_edit_group
        from pywinauto.keyboard import send_keys
        actions, index, receipts = prepared['plan']['steps'], 0, []
        self.desktop._graph_receipts = receipts
        while index < len(actions):
            # The Core rebinds complete saved records before every native edit;
            # offsets from earlier saves are never used as persistent IDs.
            anchor = graph_edit_step(self.desktop._last_saved, prepared, index)
            block = anchor.get('node', anchor.get('wire', anchor.get('from', {}))).get('block')
            if block != 0:
                raise NativeEngineeringDriverError('此原生视图尚未验证其它图形块的格点定位。')
            name = prepared['owner']
            view = self.desktop._graph_view(name)
            grid = self.desktop._graph_grid(view)
            self.desktop._command(0x643)
            action = anchor['action']
            if action in ('rebind_terminal', 'delete_terminal', 'move_node'):
                box = anchor['node']['bbox']
                point = self.desktop._graph_point(grid, box[0] + .5, box[1] + 1)
                if action == 'rebind_terminal':
                    view.double_click_input(coords=point)
                    dialogs = self.desktop._dialogs()
                    if not dialogs:
                        self.desktop._command(0x60F)
                    elif len(dialogs) != 1 or dialogs[0].window_text() != self.desktop._caption('标签登录/选择'):
                        raise NativeEngineeringDriverError('原生端子编辑出现了未确认的对话框。')
                    self.desktop._choose_label(anchor['symbol'])
                else:
                    if action == 'delete_terminal':
                        # Delete on the label clears its text; the verified
                        # context Cut removes precisely this terminal graphic
                        # and leaves existing wires for Core to check.
                        popup = self.desktop._graph_context(view, grid, box)
                        send_keys('t', vk_packet=False)
                        self.desktop._wait(lambda: not popup(), '原生端子剪切没有结束。')
                    else:
                        self.desktop._select_graph_node(view, grid, box)
                        self.desktop._graph_drag(view, grid, point,
                            self.desktop._graph_point(grid, anchor['x'] + .5, anchor['y'] + 1))
                        # Native text extents can overlap even when the saved
                        # node boxes only touch. Continue this one observed
                        # layout notice, then require the exact Core checkpoint.
                        ready_since = [None]
                        def moved():
                            dialogs = self.desktop._dialogs()
                            if dialogs:
                                if (len(dialogs) != 1 or self.desktop._dialog_text(dialogs[0]) !=
                                        self.desktop._caption('梯形图图形重叠。是否继续处理？')):
                                    raise NativeEngineeringDriverError('图形移动出现了未确认的原生提示。')
                                self.desktop._visible(name + ' 图形布局提示', dialog=dialogs[0],
                                              action=action, index=index)
                                self.desktop._click(dialogs[0], 6)
                                ready_since[0] = None
                                return False
                            if not self.desktop._window().is_enabled():
                                ready_since[0] = None
                                return False
                            if ready_since[0] is None:
                                ready_since[0] = time.monotonic()
                            return time.monotonic() - ready_since[0] >= .25
                        self.desktop._wait(moved, '原生图形移动尚未返回编辑状态。')
            elif action == 'delete_wire':
                for segment in anchor['wire']['segments']:
                    view.click_input(coords=self.desktop._graph_point(grid, *segment['point']))
                    self.desktop._command(0x18CD)
            elif action == 'add_wire':
                self.desktop._draw_graph_wire(view, grid, anchor)
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
            saved = self.desktop._save_until(complete)
            if self.desktop.evidence_directory:
                (self.desktop.evidence_directory / f'graph-{index:03d}.gxw').write_bytes(saved)
            # A coupled move is accepted only at the complete checkpoint of
            # the explicitly requested adjacent connection, never by guessing.
            verified, consumed = checked
            visible = self.desktop._visible(name + ' FBD 编辑器', action=action,
                indices=list(range(index, index + consumed)), verified_saved_source=True)
            receipts.append({**verified, 'visible_completion': visible})
            index += consumed
        return {'visible_completion': visible, 'graph_actions': receipts}

    def _create_fbd_call(self, command, plan):
        from gxw.object_model import read_project_context
        caller = command['caller']
        context = read_project_context(self.desktop._last_saved, caller)
        name = context.declarations[context.sources.local_table].owner_name
        view = self.desktop._graph_view(name)
        grid = self.desktop._graph_grid(view)
        model = context.object_model()
        occupied = model['nodes']
        # Native insertion creates its own source records and adjacent argument
        # terminals. Only the empty visible space is chosen here; Core verifies
        # the resulting instance, interface and connection graph afterwards.
        # A saved native block does not accept insertions in the white area
        # below its dot grid. Use the visible empty columns beside its current
        # objects; inserting rows through them would move unrelated objects.
        next_x = max((n['x'] + n['width'] + 10 for n in occupied), default=5)
        placement = self.desktop._graph_point(grid, next_x, 1)
        self.desktop._command(0x6AA)
        dialog = self.desktop._dialog('指令输入')
        combo = dialog.child_window(control_id=6145, class_name='ComboBox')
        self.desktop.ui._select_native_combo(combo, 'FB')
        editor = dialog.child_window(control_id=6144, class_name='Edit')
        editor.set_focus()
        import win32gui
        win32gui.SendMessage(editor.handle, 0x00B1, 0, -1)
        editor.type_keys(command['definition'], pause=.01)
        if editor.window_text() != command['definition']:
            raise NativeEngineeringDriverError('原生指令输入中的 FB 类型回读不同。')
        self.desktop._click(dialog, 1)
        self.desktop._wait(lambda: not self.desktop._dialogs(), '原生指令输入没有接受当前 FB 定义。')
        self.desktop.inspect()
        view.click_input(coords=placement)
        self.desktop._choose_label(command['instance'], expected_type=command['definition'])
        self.desktop._visible(name + ' FBD 实例', instance=command['instance'])
        def instance_saved(raw):
            nodes = read_project_context(raw, caller).object_model()['nodes']
            matches = [n for n in nodes if n['symbol'] == command['instance']
                       and n['template'] == 'function_block:' + command['definition']]
            if len(matches) != 1:
                raise NativeEngineeringDriverError('新建 FBD 实例尚未完成保存。')
        self.desktop._save_until(instance_saved)
        bindings = []
        for formal, symbol in command['arguments'].items():
            current = read_project_context(self.desktop._last_saved, caller).object_model()
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
            view = self.desktop._graph_view(name)
            grid = self.desktop._graph_grid(view)
            x = terminal['x'] + .5
            point = self.desktop._graph_point(grid, x, terminal['y'] + 1)
            view.click_input(coords=point)
            self.desktop._command(0x60F)
            binding = self.desktop._choose_label(symbol)
            bindings.append({'formal': formal, **binding})
            def terminal_saved(raw):
                nodes = read_project_context(raw, caller).object_model()['nodes']
                terminals = [n for n in nodes if n['template'] == kind and n['symbol'] == symbol
                             and any((n['x'] + p['x'], n['y'] + p['y']) == pin for p in n['ports'])]
                if len(terminals) != 1:
                    raise NativeEngineeringDriverError('本次 FBD 参数连接尚未完成保存。')
            self.desktop._save_until(terminal_saved)
            self.desktop._visible(name + ' FBD 参数连接', formal=formal, symbol=symbol)
        return self.desktop._visible(name + ' FBD 编辑器', instance=command['instance'], bindings=bindings)

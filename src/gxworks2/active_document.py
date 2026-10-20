"""Version-bound active-document transport, independent of offline Workspace."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import uuid

from shared.paths import bundled_executable
from .engineering_driver import NativeEngineeringDriverError
from gxw.native_locale import label_title, native_language, program_title


class ActiveDocumentAdapter:
    def __init__(self, executable=None, *, timeout=8):
        default = Path(os.environ.get('LOCALAPPDATA', '')) / 'PLC AI Studio/native-active/PlcAi.ActiveDocument.exe'
        self.executable = Path(executable or os.environ.get('GX_ACTIVE_DOCUMENT_ADAPTER_EXE')
                               or bundled_executable('native-active', 'PlcAi.ActiveDocument.exe') or default)
        self.timeout = timeout
        self._pending = {}

    def _require_ready(self):
        if self._pending:
            raise NativeEngineeringDriverError('上一原生操作状态尚未确认，未发送新的修改。',
                observation={'status': 'unconfirmed', 'native_task_ids': list(self._pending)})

    def read_graph(self, driver, *, owner, kind='program', data_id=None):
        """Resolve the visible owner afresh; never reuse a previous heap address."""
        self._require_ready()
        if kind not in ('program', 'fb'):
            raise ValueError('The active graph owner must be a Program or FB')
        identity = driver.inspect()
        driver._idle()
        document = driver._active_document()
        expected = program_title(owner, kind, 'fbd', language=native_language(identity))
        if document.window_text().rstrip() != expected:
            raise NativeEngineeringDriverError('当前活动图形页与指定 Program/FB 归属不同。')
        views = [v for v in document.descendants(class_name='AfxFrameOrView70u') if v.control_id() == 59648]
        if len(views) != 1:
            raise NativeEngineeringDriverError('当前活动图形页没有唯一的原生视图。')
        request = {'protocol_version': 1, 'operation': 'read_active_graph',
                   **{key: identity[key] for key in ('process_id', 'window_handle', 'process_started', 'executable')},
                   'document_handle': document.handle, 'document_title': expected, 'view_handle': views[0].handle}
        if data_id is not None:
            request['data_id'] = data_id
        result = self._call(request)
        if driver.inspect() != identity:
            raise NativeEngineeringDriverError('活动图形读取期间工程身份已改变。')
        return {**result, 'identity': identity, 'owner': owner, 'owner_kind': kind}

    def read_label_table(self, driver, *, owner, scope):
        """Read the current label control without exporting or saving a file."""
        self._require_ready()
        if scope not in ('global', 'program', 'fb', 'structure'):
            raise ValueError('The native label table requires an explicit owner scope')
        identity = driver.inspect()
        document = driver._labels_page(owner, scope)
        driver._idle()
        title = document.window_text()
        if title.rstrip() != label_title(owner, scope, language=native_language(identity)):
            raise NativeEngineeringDriverError('当前标签页与指定声明归属不同。')
        grids = [grid for grid in document.descendants(class_name='SPR32AU70_SpreadSheet')
                 if grid.is_visible() and grid.is_enabled()]
        if len(grids) != 1:
            raise NativeEngineeringDriverError('当前标签页没有唯一的原生表格。')
        request = {'protocol_version': 1, 'operation': 'read_active_labels',
                   **{key: identity[key] for key in ('process_id', 'window_handle', 'process_started', 'executable')},
                   'document_handle': document.handle, 'document_title': title, 'grid_handle': grids[0].handle}
        result = self._call(request)
        driver._verify_read_page(identity, document, title)
        if (result.get('document_handle') != document.handle or result.get('document_title') != title
                or result.get('grid_handle') != grids[0].handle or result.get('control_version') != '7.0.0.52'
                or result.get('save_performed') is not False or not isinstance(result.get('table'), dict)):
            raise NativeEngineeringDriverError('当前标签表读取归属或结果不完整。')
        return {**result, 'identity': identity}

    def select_node(self, driver, anchor):
        from gxw.active_graph import resolve_graph_target
        return self._select_element(driver, anchor, resolve_graph_target, 'node')

    def select_wire(self, driver, anchor):
        from gxw.active_graph import resolve_graph_wire_target
        return self._select_element(driver, anchor, resolve_graph_wire_target, 'wire')

    def _select_element(self, driver, anchor, resolve, kind):
        from gxw.active_graph import graph_source_state
        before = self.read_graph(driver, owner=anchor['owner'], kind=anchor['owner_kind'])
        target = resolve(before, anchor)
        if target['network'] != before['active_network']:
            raise NativeEngineeringDriverError('目标网络当前未激活；尚未执行选择或修改。')
        request = {'protocol_version': 2, 'operation': 'select_graph_element',
                   **{key: before['identity'][key] for key in ('process_id', 'window_handle', 'process_started', 'executable')},
                   'document_handle': before['document_handle'], 'document_title': driver._active_document().window_text(),
                   'view_handle': before['view_handle'], 'data_id': before['data_id'], 'target': target}
        selected = self._call(request)
        after = self.read_graph(driver, owner=anchor['owner'], kind=anchor['owner_kind'], data_id=before['data_id'])
        if graph_source_state(after) != graph_source_state(before):
            raise NativeEngineeringDriverError('选择期间活动图形内容已改变，未继续工程修改。')
        current = resolve(after, anchor)
        selected_key, other_key = ('nodes', 'wires') if kind == 'node' else ('wires', 'nodes')
        chosen = [(network['index'], node['index']) for network in after['networks'] if network['index'] == current['network']
                  for node in network[selected_key] if node['selected']]
        if (chosen != [(current['network'], current['index'])]
                or any(wire['selected'] for network in after['networks'] if network['index'] == current['network']
                       for wire in network[other_key])
                or not selected.get('selection_applied')):
            raise NativeEngineeringDriverError('原生目标选择未确认，未继续工程修改。')
        return {**after, 'selection_confirmation': {key: selected[key] for key in
            ('selection_applied', 'selection_writes', 'target_memory_written', 'own_ui_suspend_balanced')}}

    def apply_graph_action(self, driver, observation, prepared):
        """Execute one Core plan, returning only fresh native evidence.

        The caller verifies the requested semantic effect in Core. A native
        mode command is asynchronous: drawing cannot start until its actual
        SCLDED base mode has been observed, and leaving that mode also needs a
        readback. This method neither saves nor opens a saved project copy.
        """
        from gxw.active_graph import _action_state, active_graph_terminal_target
        if (not isinstance(prepared, dict) or prepared.get('kind') != 'active_graph_action'
                or prepared.get('schema_version') != 1):
            raise NativeEngineeringDriverError('需要当前活动图形的一次 Core 动作计划。')
        self._require_ready()
        if _action_state(observation) != prepared['observation_state']:
            raise NativeEngineeringDriverError('动作计划与传入的原生图形观察不同，未开始修改。')
        action = prepared['action']
        kind = action['action']
        if kind not in ('rebind_terminal', 'delete_terminal', 'move_node', 'delete_wire', 'add_wire', 'add_terminal'):
            raise NativeEngineeringDriverError('此活动图形动作尚未接入原生编辑。')
        if kind in ('move_node', 'add_wire') and (prepared['owner_kind'] != 'program' or prepared['network'] != 0):
            raise NativeEngineeringDriverError('坐标编辑目前仅验证了 Program 网络零，未发送原生命令。')
        started, selection = False, None

        def read(*, unchanged=False, allow_inline=False):
            current = self.read_graph(driver, owner=prepared['owner'], kind=prepared['owner_kind'],
                                      data_id=observation['data_id'])
            if any(current.get(key) != observation.get(key)
                   for key in ('identity', 'data_id', 'document_handle', 'view_handle')):
                raise NativeEngineeringDriverError('原生图形工程或活动页面绑定已改变。')
            if current['active_network'] != prepared['network']:
                raise NativeEngineeringDriverError('目标网络当前未激活，未继续图形修改。')
            state = current.get('native_edit_state')
            if (not isinstance(state, dict) or type(state.get('inline_editor')) is not int
                    or state['inline_editor'] < 0 or not allow_inline and state['inline_editor'] != 0
                    or type(state.get('network_clipboard_active')) is not int or state['network_clipboard_active'] != 0):
                raise NativeEngineeringDriverError('存在原生行内编辑或整网络剪贴板状态，未继续图形修改。')
            if unchanged and _action_state(current) != prepared['observation_state']:
                raise NativeEngineeringDriverError('原生图形已偏离本次动作的当前上下文，未继续修改。')
            return current

        def mode(value, *, unchanged=False):
            def observed():
                current = read(unchanged=unchanged)
                return current if current['native_edit_state'].get('base_mode') == value else None
            return driver._wait(observed, '原生图形编辑模式没有在当前页面确认。')

        def auto_connect(value, *, unchanged=False):
            def observed():
                current = read(unchanged=unchanged)
                return current if current['native_edit_state'].get('auto_connect') == value else None
            return driver._wait(observed, '原生自动连接划线设置尚未确认。')

        def view_grid(current):
            # _graph_view locates Program pages. FB pages are already bound by
            # read_graph, so resolve that exact active view without switching
            # to an identically named Program.
            if prepared['owner_kind'] == 'program':
                view = driver._graph_view(prepared['owner'])
            else:
                views = [v for v in driver._active_document().descendants(class_name='AfxFrameOrView70u')
                         if v.control_id() == 59648 and v.handle == current['view_handle']]
                if len(views) != 1:
                    raise NativeEngineeringDriverError('当前 FB 图形页没有唯一的绑定视图。')
                view = views[0]
            if view.handle != current['view_handle']:
                raise NativeEngineeringDriverError('当前图形视图与原生观察不同。')
            return view, driver._graph_grid(view)

        try:
            current = read(unchanged=True)
            if kind in ('move_node', 'add_wire'):
                state = current['native_edit_state']
                if (type(state.get('auto_connect')) is not int or state['auto_connect'] not in (0, 1)
                        or state.get('guided_override') != 0
                        or state.get('connection_stage') != 0 or state.get('pending_connection_network') != -1):
                    raise NativeEngineeringDriverError('划线设置或待完成连接状态未确认，未发送新的图形命令。')
            driver._command(0x643)
            current = mode(0x643, unchanged=True)
            if kind not in ('add_wire', 'add_terminal'):
                selected = (self.select_wire if kind == 'delete_wire' else self.select_node)(driver, prepared['target'])
                selection = selected.get('selection_confirmation')
                current = read(unchanged=True)
            if kind == 'rebind_terminal':
                from .engineering_driver import _label_display_type
                driver._command(0x60F)
                started = True
                driver._choose_label(action['symbol'], expected_type=_label_display_type(prepared['expected_data_type']))
            elif kind in ('delete_terminal', 'delete_wire'):
                started = True
                driver._command(0x18CD)
            elif kind == 'move_node':
                automatic = current['native_edit_state']['auto_connect']
                if automatic:
                    # Native moves can extend a formerly touching port with a
                    # new wire. Core's move action preserves all wire geometry,
                    # so observe manual mode before delivering the drag.
                    driver._command(0x645)
                    current = auto_connect(0, unchanged=True)
                view, grid = view_grid(current)
                current = read(unchanged=True)
                if current['native_edit_state'].get('auto_connect') != 0:
                    raise NativeEngineeringDriverError('移动前原生自动连接设置已改变，未拖拽。')
                bounds = prepared['target']['semantic']
                source = driver._graph_point(grid, bounds['x']+.5, bounds['y']+1)
                target = driver._graph_point(grid, action['x']+.5, action['y']+1)
                started = True
                driver._graph_drag(view, grid, source, target)
                if automatic:
                    current = read()
                    if current['native_edit_state'].get('auto_connect') != 0:
                        raise NativeEngineeringDriverError('移动后原生自动连接设置已改变，未重复切换。')
                    driver._command(0x645)
                    auto_connect(automatic)
            elif kind == 'add_wire':
                automatic = current['native_edit_state']['auto_connect']
                if automatic:
                    # AutoConnect uses a two-click candidate-port state machine.
                    # Explicit geometry uses the witnessed manual drag branch.
                    driver._command(0x645)
                    current = auto_connect(0, unchanged=True)
                driver._command(0x644)
                current = mode(0x644, unchanged=True)
                points = list(reversed([prepared['endpoints']['from']['point'],
                                       *action['via'], prepared['endpoints']['to']['point']]))
                segments = list(zip(points, points[1:]))
                # Native vertical strokes require their horizontal receiving
                # legs to exist, including both legs of a U-shaped route.
                segments.sort(key=lambda segment: segment[0][1] != segment[1][1])
                for start, end in segments:
                    current = mode(0x644, unchanged=not started)
                    state = current['native_edit_state']
                    if (state.get('auto_connect') != 0 or state.get('guided_override') != 0
                            or state.get('connection_stage') != 0 or state.get('pending_connection_network') != -1):
                        raise NativeEngineeringDriverError('本次手动划线设置已改变或前一线段尚未完成。')
                    view, grid = view_grid(current)
                    # Native hit areas lie just inside the current observed
                    # lattice cell. Pixel locations are recalibrated per drag.
                    source = driver._graph_point(grid, *start)
                    target = driver._graph_point(grid, *end)
                    started = True
                    driver._graph_drag(view, grid, (source[0]+2, source[1]+2), (target[0]+2, target[1]+2))
                driver._command(0x643)
                current = mode(0x643)
                if (current['native_edit_state'].get('connection_stage') != 0
                        or current['native_edit_state'].get('pending_connection_network') != -1):
                    raise NativeEngineeringDriverError('本次原生连线尚有待完成状态。')
                if automatic:
                    driver._command(0x645)
                    auto_connect(automatic)
            else:
                import win32gui
                import win32process
                from .engineering_driver import _label_display_type
                if (action.get('template') != 'input' or prepared.get('expected_data_type', '').upper() != 'BOOL'
                        or prepared['network'] != 0 or prepared['owner_kind'] != 'program'):
                    raise NativeEngineeringDriverError('此原生新建路径仅支持 Program 当前网络零的命名 BOOL 输入端子。')
                current = read(unchanged=True)
                view, grid = view_grid(current)
                point = driver._graph_placement_point(view, grid, action['x'], action['y'])
                read(unchanged=True)
                # The witnessed input placement command must finish before
                # its sole click; PostMessage alone can race with that click.
                started = True
                win32gui.SendMessageTimeout(current['identity']['window_handle'], 0x0111, 0x655, 0, 2, 1500)
                view.click_input(coords=point)
                def placed():
                    observed = read(allow_inline=True)
                    if _action_state(observed) == prepared['observation_state']:
                        return None
                    anchor = active_graph_terminal_target(observed, prepared, placeholder=True)
                    inline = observed['native_edit_state']['inline_editor']
                    return (observed, anchor, inline) if inline else None
                placed_graph, anchor, inline = driver._wait(placed, '没有读到本次唯一新建输入及其待完成编辑器。')
                if (not win32gui.IsWindow(inline) or not win32gui.IsWindowVisible(inline)
                        or not win32gui.IsChild(placed_graph['view_handle'], inline)
                        or win32process.GetWindowThreadProcessId(inline)[1] != placed_graph['identity']['process_id']):
                    raise NativeEngineeringDriverError('本次输入的待完成编辑器不属于当前原生视图。')
                win32gui.PostMessage(inline, 0x0100, 13, 0x001C0001)
                win32gui.PostMessage(inline, 0x0101, 13, 0xC01C0001)
                driver._wait(lambda: not win32gui.IsWindow(inline) or not win32gui.IsWindowVisible(inline),
                             '本次输入的原生行内编辑尚未结束。')
                current = read()
                anchor = active_graph_terminal_target(current, prepared, placeholder=True)
                selected = self.select_node(driver, anchor)
                selection = selected.get('selection_confirmation')
                current = read()
                active_graph_terminal_target(current, prepared, placeholder=True)
                driver._command(0x60F)
                driver._choose_label(action['symbol'], expected_type=_label_display_type(prepared['expected_data_type']))
            def changed():
                current = read()
                if kind == 'add_terminal':
                    active_graph_terminal_target(current, prepared)
                return current if _action_state(current) != prepared['observation_state'] else None
            after = driver._wait(changed, '原生图形操作后尚未读到实际内容变化。')
            return {**after, 'native_graph_action': {'action': kind, 'network': prepared['network'],
                    'status': 'observed', 'save_performed': False},
                    **({'selection_confirmation': selection} if selection else {})}
        except Exception as error:
            evidence = error.observation if isinstance(error, NativeEngineeringDriverError) else {}
            raise NativeEngineeringDriverError(str(error), observation={**evidence,
                'status': 'unconfirmed' if started or evidence.get('status') == 'unconfirmed' else 'rejected',
                'action': kind, 'mutation_may_have_started': started or evidence.get('status') == 'unconfirmed',
                'save_performed': False}) from error

    def _call(self, request):
        self._require_ready()
        if os.name != 'nt' or not self.executable.is_file():
            raise NativeEngineeringDriverError('活动文档读取需要已构建的本机原生适配器。')
        child = subprocess.Popen([str(self.executable)], stdin=subprocess.PIPE,
                encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            stdout, _ = child.communicate(json.dumps(request, ensure_ascii=False), timeout=self.timeout)
        except subprocess.TimeoutExpired as error:
            if request.get('protocol_version') == 2 and request['operation'] == 'select_graph_element':
                # V2 requires a kernel lease or an independent guardian. Stop
                # the writer before recovery; never replay a partial selection.
                child.kill()
                child.communicate()
                raise NativeEngineeringDriverError('活动文档选择超时，执行进程已停止；需重新读取目标状态。',
                    observation={'status': 'unconfirmed', 'operation': request['operation'],
                                 'child_process_id': child.pid, 'child_terminated': True,
                                 'native_mutation_may_have_started': True}) from error
            # The child may be inside a bounded critical section that suspends
            # the owned GX UI. Killing it would bypass its finally/ResumeThread.
            # Keep its live handle and reader threads; no mutation is replayed.
            task_id = uuid.uuid4().hex
            self._pending[task_id] = (child, request)
            raise NativeEngineeringDriverError('活动文档操作响应超时；必须先读取目标状态。',
                observation={'status': 'unconfirmed', 'operation': request['operation'],
                             'native_task_id': task_id, 'child_process_id': child.pid,
                             'child_terminated': False}) from error
        return self._result(child.returncode, stdout, request)

    def pending_operations(self):
        """Query only existing child handles; completion still needs state readback."""
        result = []
        for task_id, (child, request) in list(self._pending.items()):
            if child.poll() is None:
                result.append({'native_task_id': task_id, 'status': 'running', 'operation': request['operation']})
                continue
            stdout, _ = child.communicate()
            row = {'native_task_id': task_id, 'status': 'ready_to_reconcile', 'operation': request['operation']}
            try:
                row['native_observation'] = self._result(child.returncode, stdout, request)
            except NativeEngineeringDriverError as error:
                row.update(status='unconfirmed', message=str(error), observation=error.observation)
            result.append(row)
            del self._pending[task_id]
        return result

    @staticmethod
    def _result(returncode, stdout, request):
        try:
            result = json.loads(stdout.lstrip('\ufeff'))
        except (ValueError, TypeError):
            raise NativeEngineeringDriverError('活动文档适配器未返回完整结果。', observation={
                'status': 'unconfirmed' if request.get('operation') == 'select_graph_element' else 'failed',
                'native_mutation_may_have_started': request.get('operation') == 'select_graph_element'}) from None
        if not isinstance(result, dict):
            raise NativeEngineeringDriverError('活动文档适配器返回了无效结果类型。')
        if (returncode or result.get('status') != 'observed'
                or result.get('protocol_version') != 1 or result.get('native_version') != '1.635.0.1'
                or result.get('process_id') != request['process_id']):
            raise NativeEngineeringDriverError('当前活动文档未确认：' + str(result.get('message', 'invalid_observation')),
                                               observation=result)
        return result

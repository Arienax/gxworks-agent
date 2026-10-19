"""Persistent page operations composed from the bound native engineering driver.

Reading and applying operate on current native pages. Only stage_save issues a
Save, once. An acknowledged native compiler save has its own explicit boundary.
"""
from __future__ import annotations

from copy import deepcopy
import re
import time

from gxw.live_context import (_identity, _same_native_cpu, live_context_state, verify_active_labels,
                              verify_active_saved_context, verify_active_st)
from .engineering_driver import NativeEngineeringDriverError
from .native_locale import native_tree_path
from gxw.native_locale import native_language, program_title, ui_text


class LiveEngineeringDriver:
    def __init__(self, driver, baseline, expected_cpu, adapter=None):
        from gxw.fb_authoring import _context
        from gxw.native_write import native_task_scope_context
        from gxworks2.project_profiles import native_project_profile
        from .active_document import ActiveDocumentAdapter
        if not isinstance(baseline, bytes) or not baseline:
            raise NativeEngineeringDriverError('持续原生编辑需要完整且已核验的保存工程。')
        config = _context(baseline, require_fbd=False)[0]
        profile = native_project_profile(expected_cpu)
        self.driver, self.baseline, self.path = driver, baseline, driver.path
        self.adapter = (adapter if adapter is not None else
                        getattr(driver, '_label_reader', None) or ActiveDocumentAdapter())
        # Graph and label reads share timeout recovery. A retained label child
        # must be polled by the same pending/rebind boundary as graph work.
        driver._label_reader = self.adapter
        self.identity = _identity(driver.inspect())
        if (profile is None or not profile.program_supported or not _same_native_cpu(profile.cpu, config['cpu'])
                or not _same_native_cpu(self.identity['cpu_menu'], config['cpu'])
                or self.identity['software_version'] != '1.635.0.1'):
            raise NativeEngineeringDriverError('持续编辑的 CPU、版本或保存来源不一致。')
        tasks = native_task_scope_context(baseline)
        if tasks['status'] != 'available':
            raise NativeEngineeringDriverError('保存工程没有完整且唯一的资源/任务归属。')
        self._tasks = {**tasks, 'origin': 'validated_saved_baseline_and_confirmed_native_appends',
                       'confirmed_appends': []}
        self._saved_bytes, self._closed, self._last_context = baseline, False, None
        self._completion_dialog = None
        self._last_target = None
        if self.path.read_bytes() != baseline:
            raise NativeEngineeringDriverError('持续编辑开始时工程文件已偏离明确的保存基线。')

    def capabilities(self):
        from plc.target_support import DEFAULT_TARGET_SUPPORT
        identity = self.inspect()
        return DEFAULT_TARGET_SUPPORT.live_capabilities(identity['cpu_menu'],
            software_version=identity['software_version'], native_ui_language=native_language(identity))

    def inspect(self):
        if self._closed:
            raise NativeEngineeringDriverError('持续工程控制器已关闭。')
        actual = self.driver.inspect()
        if _identity(actual) != self.identity:
            raise NativeEngineeringDriverError('持续工程的进程、启动身份、窗口或工程已改变。')
        return deepcopy(actual)

    def _ready(self):
        pending = getattr(self.adapter, '_pending', {})
        if pending:
            raise NativeEngineeringDriverError('已有原生操作未确认，未发送新的页面操作。',
                observation={'status': 'unconfirmed', 'native_task_ids': list(pending)})
        self.inspect()
        self.driver.verify_project_codepage()

    def _inventory_tree(self):
        self.driver._idle()
        trees = [tree for tree in self.driver._window().descendants(class_name='SysTreeView32')
                 if tree.control_id() == 12051 and tree.is_visible() and tree.is_enabled()]
        if len(trees) != 1:
            raise NativeEngineeringDriverError('当前工程没有唯一的可见导航树。')
        tree = trees[0]
        def children(path, *, optional=False):
            try:
                item = tree.get_item(native_tree_path(path, native_language(self.identity)), exact=True)
            except (IndexError, RuntimeError):
                if optional:
                    return []
                raise NativeEngineeringDriverError('当前工程导航树缺少明确目录：' + '/'.join(path)) from None
            names = [child.text() for child in item.children()]
            if any(not isinstance(name, str) or not name or '\0' in name for name in names) or len(set(names)) != len(names):
                raise NativeEngineeringDriverError('当前工程导航目录的对象名称不明确。')
            return sorted(names)
        return dict(globals=children(('全局标签',)),
            pous=[dict(owner=name, owner_kind=kind) for kind, folder in (('program', '程序'), ('fb', 'FB/FUN'))
                  for name in children(('程序部件', folder))],
            structures=children(('程序部件', '结构体'), optional=True))

    def read_context(self):
        from gxw.active_context import read_active_label_grid, read_active_labels_csv
        self.finish_completion()
        self._ready()
        inventory = self._inventory_tree()
        before_tree = deepcopy(inventory)
        tables, st, graphs = [], [], []
        for scope, names in (('global', inventory['globals']), ('structure', inventory['structures'])):
            tables.extend(self.driver.read_labels(name, scope) for name in names)
        for row in inventory['pous']:
            owner, kind = row['owner'], row['owner_kind']
            tables.append(self.driver.read_labels(owner, kind))
            folder, marker = ('程序', 'PRG') if kind == 'program' else ('FB/FUN', 'FB')
            ui_language = native_language(self.identity)
            pattern = '^' + re.escape(owner + ' [' + marker + '] ' + ui_text('nav_body', ui_language) + ' [')
            document = self.driver._navigate([('程序部件', folder, owner, '程序本体')], pattern)
            title = document.window_text().rstrip()
            if title == program_title(owner, kind, 'st', language=ui_language):
                row['language'] = 'st'
                st.append(self.driver.read_st(owner, kind))
            elif title == program_title(owner, kind, 'fbd', language=ui_language):
                row['language'] = 'fbd'
                graphs.append(self.adapter.read_graph(self.driver, owner=owner, kind=kind))
            else:
                raise NativeEngineeringDriverError('当前原生 POU 语言尚无完整持续读取能力。')
        # Resolve named project types using observations from this same read;
        # never borrow stale declarations from the saved baseline.
        type_sources = [table for table in tables if table['owner_kind'] in ('fb', 'structure')]
        resolved = []
        for table in tables:
            binding = table['source_binding']
            reader, value = ((read_active_label_grid, table['native_grid']) if 'native_grid' in table
                             else (read_active_labels_csv, table['raw_bytes']))
            result = reader(value, owner=table['owner'], scope=table['owner_kind'],
                identity=binding['identity'], document_handle=binding['document_handle'],
                document_title=binding['document_title'], type_sources=type_sources)
            if 'export_file' in table:
                result['export_file'] = table['export_file']
            if 'read_transport' in table:
                result['read_transport'] = table['read_transport']
            resolved.append(result)
        configuration = self.driver.read_configuration()
        if self._inventory_tree() != before_tree:
            raise NativeEngineeringDriverError('读取当前页面期间工程导航对象发生变化，需重新核对。')
        result = dict(identity=self.inspect(), inventory=inventory, tables=resolved, st=st, graphs=graphs,
                      configuration=configuration, tasks=deepcopy(self._tasks))
        try:
            live_context_state(result)
        except ValueError as error:
            raise NativeEngineeringDriverError(str(error), observation={'status': 'unconfirmed'}) from error
        self._last_context = deepcopy(result)
        return result

    def read_target_context(self, context, command):
        """Refresh actual dependencies; unread sources await the full phase check."""
        from gxw.active_context import read_active_label_grid, read_active_labels_csv
        from gxw.live_context import current_graph, live_read_scope
        self.finish_completion()
        self._ready()
        expected_tree = {**context['inventory'], 'pous': [
            {key: row[key] for key in ('owner', 'owner_kind')} for row in context['inventory']['pous']]}
        if _identity(context['identity']) != self.identity or self._inventory_tree() != expected_tree:
            raise NativeEngineeringDriverError('定向读取期间工程身份或对象清单发生变化，需要完整重新读取。')
        result = deepcopy(context)
        if command['operation'] in ('save', 'compile', 'show_assignments', 'reopen'):
            return self.read_context()
        scope_command = command
        if command.get('caller') is not None:
            # Saved role names can differ from the visible owner. Keep their
            # resolution in Core, as in the actual graph planner.
            graph = current_graph(self.baseline, context, command['caller'])
            scope_command = {**command, 'caller': graph['owner']}
        fresh = {}
        while True:
            scope = live_read_scope(result, scope_command, list(fresh.values()))
            pending = [key for key in scope['tables'] if tuple(key) not in fresh]
            if not pending:
                break
            for kind, owner in pending:
                fresh[kind, owner] = self.driver.read_labels(owner, kind)
        tables = [fresh.get((row['owner_kind'], row['owner']), row) for row in result['tables']]
        types = [row for row in tables if row['owner_kind'] in ('fb', 'structure')]
        resolved = []
        for row in tables:
            if (row['owner_kind'], row['owner']) not in fresh:
                resolved.append(row)
                continue
            binding = row['source_binding']
            reader, value = ((read_active_label_grid, row['native_grid']) if 'native_grid' in row
                             else (read_active_labels_csv, row['raw_bytes']))
            resolved.append(reader(value, owner=row['owner'], scope=row['owner_kind'], identity=binding['identity'],
                document_handle=binding['document_handle'], document_title=binding['document_title'], type_sources=types))
        result['tables'] = resolved
        refreshed = {}
        for kind, owner in scope['pous']:
            matches = [row for row in context['inventory']['pous'] if (row['owner_kind'], row['owner']) == (kind, owner)]
            if len(matches) != 1:
                raise NativeEngineeringDriverError('定向读取没有唯一的当前 POU 归属。')
            language = matches[0]['language']
            if language == 'st':
                refreshed['st', kind, owner] = self.driver.read_st(owner, kind)
            else:
                self.driver._program_page(owner, kind, 'fbd')
                refreshed['graphs', kind, owner] = self.adapter.read_graph(self.driver, owner=owner, kind=kind)
        for field in ('st', 'graphs'):
            result[field] = [refreshed.get((field, row['owner_kind'], row['owner']), row) for row in result[field]]
        if scope['configuration']:
            result['configuration'] = self.driver.read_configuration()
        if _identity(self.inspect()) != self.identity or self._inventory_tree() != expected_tree:
            raise NativeEngineeringDriverError('定向读取期间工程身份或对象清单发生变化。')
        result['_observation_scope'] = dict(kind='dependencies', **scope)
        live_context_state(result)
        self._last_context = deepcopy(result)
        return result

    def focus_target(self, command, context):
        """Resolve a requested network in the current owner before Core preparation."""
        from gxw.live_context import current_graph, verify_live_observations
        operation = command.get('operation')
        if operation not in ('edit_fbd_graph', 'create_fbd_call'):
            return context
        if operation == 'edit_fbd_graph':
            actions = command.get('actions')
            if not isinstance(actions, list) or len(actions) != 1:
                raise NativeEngineeringDriverError('网络定位要求单个明确的图形动作。')
            network = actions[0].get('network')
        else:
            network = command.get('network', 0)
        graph = current_graph(self.baseline, context, command.get('caller'))
        if type(network) is not int or not 0 <= network < len(graph['networks']):
            raise NativeEngineeringDriverError('请求的网络不在当前图形范围内；未发送页面命令。')
        self.finish_completion()
        self._ready()
        owner, kind = graph['owner'], graph['owner_kind']
        self.driver._program_page(owner, kind, 'fbd')
        observed = self.adapter.read_graph(self.driver, owner=owner, kind=kind)
        def updated(value):
            result = deepcopy(context)
            result['graphs'] = [deepcopy(value) if (row['owner'], row['owner_kind']) == (owner, kind)
                                else row for row in result['graphs']]
            verify_live_observations(context, result)
            return result
        # Even an already active network must belong to this same fresh source.
        result = updated(observed)
        if observed.get('active_network') != network:
            observed = self.driver.focus_graph_network(self.adapter, owner, kind, network)
            result = updated(observed)
        if observed.get('active_network') != network:
            raise NativeEngineeringDriverError('页面没有切换到请求的图形网络。')
        self._last_context = deepcopy(result)
        return result

    def apply(self, command, prepared):
        self.finish_completion()
        self._ready()
        if self.read_disk_change() is not None:
            raise NativeEngineeringDriverError('工程文件在实际页面修改前已被其他保存改变。')
        operation, plan = command['operation'], prepared['plan']
        if plan.get('operation') != operation:
            raise NativeEngineeringDriverError('实际原生操作与已准备的 Core 操作不同。')
        if operation not in ('create_global_labels', 'edit_global_labels', 'edit_pou_labels',
                             'create_st_fb', 'edit_st_fb', 'create_st_program', 'edit_st_program', 'configure_project',
                             'edit_fbd_graph', 'create_fbd_call', 'delete_label'):
            raise NativeEngineeringDriverError('该操作尚未接入持续原生编辑驱动。')
        self.driver._last_compile_run = None
        if prepared['kind'] == 'active_fbd_call' and operation == 'create_fbd_call':
            return self._create_active_fbd_call(command, prepared)
        if prepared['kind'] == 'active_graph' and operation == 'edit_fbd_graph':
            from gxw.active_graph import _action_state, prepare_active_graph_action, verify_active_graph_action
            from gxw.live_context import active_callable_sources
            if len(command.get('actions', [])) != 1 or self._last_context is None:
                raise NativeEngineeringDriverError('每个持续图形操作边界必须只有一个动作并具有当前完整上下文。')
            owner, owner_kind = plan['owner'], plan['owner_kind']
            self.driver._program_page(owner, owner_kind, 'fbd')
            fresh = self.adapter.read_graph(self.driver, owner=owner, kind=owner_kind)
            sources = active_callable_sources(self.baseline, self._last_context['tables'], owner=owner,
                kind=owner_kind, identity=self.identity)
            original = prepared['graph_action']
            if _action_state(fresh, sources=sources) != original['before_state']:
                raise NativeEngineeringDriverError('图形源码或已观察端口已偏离本次 Core 动作，未修改。')
            action = deepcopy(original['action'])
            kind = action['action']
            if kind in ('rebind_terminal', 'delete_terminal', 'move_node'):
                action['node'] = deepcopy(original['target'])
            elif kind == 'delete_wire':
                action['wire'] = deepcopy(original['target'])
            elif kind == 'add_wire':
                for name in ('from', 'to'):
                    action[name]['node'] = deepcopy(original['endpoints'][name]['node'])
            current_plan = prepare_active_graph_action(fresh, action, sources=sources,
                                                       device_profile=prepared['device_profile'])
            actual = self.adapter.apply_graph_action(self.driver, fresh, current_plan)
            verification = verify_active_graph_action(fresh, actual, current_plan, sources=sources)
            if verification['status'] != 'verified':
                raise NativeEngineeringDriverError('实际原生图形效果未通过 Core 核对。',
                    observation={'status': 'unconfirmed', 'verification': verification, 'save_performed': False})
            verification['status'] = 'matched'
            visible = self.driver._visible(owner + ' FBD 编辑器')
        elif prepared['kind'] == 'active_label_delete' and operation == 'delete_label':
            from gxw.live_context import verify_active_label_delete
            self.driver._delete_label(prepared)
            labels = self.driver.read_labels(plan['source_name'], prepared['scope'])
            verification = verify_active_label_delete(prepared, labels)
            visible = self.driver._visible(plan['source_name'] + ' 局部标签表', deleted_label=plan['name'])
        elif prepared['kind'] == 'active_labels':
            if operation == 'create_global_labels':
                self.driver._new_object(plan['source_name'], '全局标签')
                self.driver._visible('工程导航树', object=plan['source_name'])
            visible = self.driver._csv_labels(plan['source_name'], prepared['scope'], plan['rows'])
            observed = self.driver.read_labels(plan['source_name'], prepared['scope'])
            verification = verify_active_labels(prepared, observed)
        elif prepared['kind'] == 'active_st':
            if prepared['create']:
                self.driver._new_object(plan['source_name'], 'FB' if prepared['scope'] == 'fb' else '程序块', 'ST')
                self.driver._visible('工程导航树', object=plan['source_name'])
                if prepared['scope'] == 'program':
                    result = self.driver._bind_program_task(plan, stage_save=False)
                    confirmation = result.get('task_append_confirmation', {})
                    expected = deepcopy(self._tasks['tasks'])
                    selected = [task for task in expected if (task['resource'], task['task']) == (plan['resource'], plan['task'])]
                    if len(selected) != 1 or confirmation != dict(status='matched',
                            origin='native_task_cell_committed_readback', resource=plan['resource'], task=plan['task'],
                            program=plan['source_name'], line=plan['line'], save_performed=False):
                        raise NativeEngineeringDriverError('新 Program 的实际任务追加尚未确认。')
                    selected[0]['programs'].append(dict(name=plan['source_name'], line=plan['line']))
                    if result.get('expected_tasks') != expected:
                        raise NativeEngineeringDriverError('新 Program 的任务结果与持续会话当前任务不同。')
                    self._tasks['tasks'] = expected
                    self._tasks['confirmed_appends'].append(deepcopy(confirmation))
            if plan['write_declarations']:
                self.driver._csv_labels(plan['source_name'], prepared['scope'], plan['rows'])
            before_st = (prepared.get('before_st') or {}).get('st_text')
            visible = self.driver._write_st(plan['source_name'], plan['st_text'], before=before_st, kind=prepared['scope'])
            labels = self.driver.read_labels(plan['source_name'], prepared['scope'])
            verification = verify_active_st(prepared, self.driver.read_st(plan['source_name'], prepared['scope']), labels)
        elif prepared['kind'] == 'active_configuration' and operation == 'configure_project':
            visible = self.driver._configure(plan)
            verification = dict(status='matched', origin='active_native_configuration', save_performed=False)
        else:
            raise NativeEngineeringDriverError('持续原生操作与已准备的 Core 契约不匹配。')
        self.inspect()
        self._last_target = (deepcopy(command), deepcopy(prepared))
        return dict(status='matched', verification=verification, visible_completion=visible, save_performed=False)

    def _create_active_fbd_call(self, command, prepared):
        """Use native insertion and re-resolve each actual formal before binding."""
        from gxw.active_graph import (active_graph_projection, prepare_active_graph_action,
                                      verify_active_graph_action)
        from gxw.live_context import _graph_source, active_callable_sources
        from gxw.live_fbd_call import (active_fbd_call_binding_steps, prepare_active_fbd_call,
                                      verify_active_fbd_call)
        import win32gui
        import win32process
        if self._last_context is None:
            raise NativeEngineeringDriverError('新增调用需要刚刚读取的完整活动工程上下文。')
        before = self.read_target_context(self._last_context, command)
        current_plan = prepare_active_fbd_call(self.baseline, before, command,
                                               device_profile=prepared['device_profile'])
        if any(current_plan[key] != prepared[key] for key in ('plan', 'before_graphs', 'protected_context')):
            raise NativeEngineeringDriverError('新增调用前实际接口或图形已经变化，未开始插入。')
        plan = prepared['plan']
        owner, kind = plan['owner'], plan['owner_kind']
        self.driver._program_page(owner, kind, 'fbd')
        observed = self.adapter.read_graph(self.driver, owner=owner, kind=kind)
        sources = active_callable_sources(self.baseline, before['tables'], owner=owner, kind=kind, identity=self.identity)
        graph = active_graph_projection(observed, sources=sources)
        next_x = max((row['x']+row['width']+10 for row in graph['networks'][plan['network']]['nodes']), default=5)
        views = [view for view in self.driver._active_document().descendants(class_name='AfxFrameOrView70u')
                 if view.control_id() == 59648 and view.handle == observed['view_handle']]
        if len(views) != 1:
            raise NativeEngineeringDriverError('当前调用页面没有唯一的绑定原生图形视图。')
        view, = views
        grid = self.driver._graph_grid(view)
        point = self.driver._graph_point(grid, next_x, 1)
        self.driver._command(0x6AA)
        dialog = self.driver._dialog('指令输入')
        self.driver.ui._select_native_combo(dialog.child_window(control_id=6145, class_name='ComboBox'), 'FB')
        editor = dialog.child_window(control_id=6144, class_name='Edit')
        editor.set_focus()
        win32gui.SendMessage(editor.handle, 0x00B1, 0, -1)
        win32gui.SendMessage(editor.handle, 0x00C2, 1, command['definition'])
        if editor.window_text() != command['definition']:
            raise NativeEngineeringDriverError('原生指令输入的功能块定义与当前接口不同。')
        self.driver._click(dialog, 1)
        self.driver._wait(lambda: not self.driver._dialogs(), '原生指令输入尚未接受当前功能块。')
        self.inspect()
        view.click_input(coords=point)
        self.driver._choose_label(command['instance'], expected_type=command['definition'])
        self.driver._visible(owner + ' FBD 实例', instance=command['instance'])
        inserted = self.adapter.read_graph(self.driver, owner=owner, kind=kind)
        inline = inserted['native_edit_state']['inline_editor']
        if inline:
            if (not win32gui.IsChild(inserted['view_handle'], inline)
                    or win32process.GetWindowThreadProcessId(inline)[1] != self.identity['process_id']):
                raise NativeEngineeringDriverError('新增调用的行内编辑器没有绑定当前原生页面。')
            # The chooser has already bound this instance. Finish that exact
            # pending editor, never repeat placement to discover its state.
            win32gui.PostMessage(inline, 0x100, 0x0D, 0x001C0001)
            win32gui.PostMessage(inline, 0x101, 0x0D, 0xC01C0001)
            self.driver._wait(lambda: not win32gui.IsWindow(inline) or not win32gui.IsWindowVisible(inline),
                              '新增调用的原生行内编辑尚未结束。')
            finished = self.adapter.read_graph(self.driver, owner=owner, kind=kind)
            if _graph_source(inserted) != _graph_source(finished):
                raise NativeEngineeringDriverError('结束新增调用的行内编辑时实际图形发生未请求改变。')
        bindings = []
        for _ in range(len(plan['interface']['inputs']) + len(plan['interface']['outputs']) + 1):
            current = self.read_target_context(before, command)
            steps = active_fbd_call_binding_steps(current, prepared)
            pending = [step for step in steps if step['status'] == 'pending']
            if not pending:
                verification = verify_active_fbd_call(before, current, command, prepared)
                self.driver._program_page(owner, kind, 'fbd')
                visible = self.driver._visible(owner + ' FBD 编辑器', instance=command['instance'], bindings=bindings)
                self._last_target = (deepcopy(command), deepcopy(prepared))
                return dict(status='matched', verification=verification, visible_completion=visible,
                            save_performed=False)
            step = pending[0]
            self.driver._program_page(owner, kind, 'fbd')
            observed = self.adapter.read_graph(self.driver, owner=owner, kind=kind)
            sources = active_callable_sources(self.baseline, current['tables'], owner=owner, kind=kind, identity=self.identity)
            action = dict(action='rebind_terminal', network=plan['network'], node=step['target'], symbol=step['symbol'])
            binding = prepare_active_graph_action(observed, action, sources=sources,
                                                 device_profile=prepared['device_profile'])
            changed = self.adapter.apply_graph_action(self.driver, observed, binding)
            if verify_active_graph_action(observed, changed, binding, sources=sources)['status'] != 'verified':
                raise NativeEngineeringDriverError('功能块参数的本次原生重绑未通过当前 Core 核对。')
            bindings.append(dict(formal=step['formal'], symbol=step['symbol']))
            self.driver._visible(owner + ' FBD 参数连接', formal=step['formal'], symbol=step['symbol'])
        raise NativeEngineeringDriverError('新增功能块调用的参数连接没有在明确步骤内完成。')

    def read_disk_change(self):
        """Read a complete owned GXW without issuing Save or adopting its state."""
        from gxw.container_writer import validate_cfb_streams
        self.inspect()
        stable = [None, None]
        def ready():
            before = self.path.stat()
            raw = self.path.read_bytes()
            after = self.path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                stable[:] = [None, None]
                return None
            if raw == self._saved_bytes:
                return ('unchanged', raw)
            key = after.st_size, after.st_mtime_ns
            if stable[0] != key:
                stable[:] = [key, time.monotonic()]
                return None
            if time.monotonic() - stable[1] < .2:
                return None
            try:
                validate_cfb_streams(raw)
            except ValueError:
                return None
            return ('changed', raw)
        state, raw = self.driver._wait(ready, '当前工程文件仍在写入，未取得稳定的原生保存字节。')
        self.inspect()
        return raw if state == 'changed' else None

    def acknowledge_native_save(self, raw, expected_context, *, compilation=None):
        """Adopt only a verified native-owned save explicitly reported by the caller."""
        self._ready()
        if self.path.read_bytes() != raw:
            raise NativeEngineeringDriverError('原生保存字节不再对应当前受控工程文件。')
        validation = verify_active_saved_context(raw, expected_context, self.baseline, compilation=compilation)
        if self.path.read_bytes() != raw:
            raise NativeEngineeringDriverError('核对原生保存时工程文件又被改变。')
        self._saved_bytes = self.baseline = raw
        if hasattr(self.driver, '_last_saved'):
            self.driver._last_saved = raw
        return dict(status='matched', validation=validation, driver_saved=False)

    def stage_save_status(self, operation_id):
        """Read only this controller's latest Save dispatch; never touch native UI."""
        progress = getattr(self, '_stage_save_progress', None)
        if not isinstance(progress, dict) or progress.get('operation_id') != operation_id:
            return dict(operation_id=operation_id, status='unavailable', save_dispatched=False,
                        send_attempted=False, snapshot_returned=False)
        return deepcopy(progress)

    def stage_save(self, expected_context, *, operation_id=None, compilation=None, _current_before=None):
        from uuid import uuid4
        operation_id = uuid4().hex if operation_id is None else operation_id
        if not isinstance(operation_id, str) or not re.fullmatch(r'[a-f0-9]{32}', operation_id):
            raise NativeEngineeringDriverError('阶段保存需要本次控制器生成的操作标识。')
        progress = dict(operation_id=operation_id, status='checking', save_dispatched=False,
                        send_attempted=False, snapshot_returned=False)
        self._stage_save_progress = progress  # Reset before every pre-Save gate, including _ready.
        def dispatched(event):
            if (not isinstance(event, dict) or event.get('send_attempted') is not True
                    or type(event.get('save_dispatched')) is not bool
                    or event.get('dispatch_status') not in ('returned', 'timed_out', 'rejected')):
                raise NativeEngineeringDriverError('原生保存命令的发送回执不可确认。')
            progress.update(send_attempted=True, save_dispatched=event['save_dispatched'],
                            dispatch_status=event['dispatch_status'])
            if type(event.get('native_error_code')) is int:
                progress['native_error_code'] = event['native_error_code']
            progress['status'] = 'dispatched' if progress['save_dispatched'] else 'rejected'
        try:
            self._ready()
            if _current_before is None:
                before = self.read_context()
            else:
                from gxw.live_context import verify_live_observations
                if self._last_context is None or '_observation_scope' in _current_before:
                    raise NativeEngineeringDriverError('阶段保存需要本次完整读取的边界上下文。')
                verify_live_observations(self._last_context, _current_before)
                before = deepcopy(_current_before)
            from gxw.live_context import verify_live_observations
            try:
                verify_live_observations(expected_context, before)
            except ValueError as error:
                raise NativeEngineeringDriverError('阶段保存前当前原生源码已偏离最后确认的修订。') from error
            if self.read_disk_change() is not None:
                raise NativeEngineeringDriverError('阶段保存前磁盘工程发生未确认改变，未覆盖。')
            raw = self.driver.snapshot(on_dispatch=dispatched)  # Exactly one explicit Save.
            progress['snapshot_returned'] = True
            if progress['save_dispatched'] is not True:
                raise NativeEngineeringDriverError('阶段保存没有本次原生命令的发送记录。')
            try:
                validation = verify_active_saved_context(raw, expected_context, self.baseline, compilation=compilation)
            except ValueError as error:
                raise NativeEngineeringDriverError(str(error),
                    observation={'status': 'unconfirmed', 'save_performed': True}) from error
            try:
                after = self.read_context()
                if '_revision' in expected_context:
                    after['_revision'] = expected_context['_revision']
                verify_live_observations(before, after)
            except ValueError as error:
                raise NativeEngineeringDriverError('阶段保存期间活动源码发生改变，需要重新核对。') from error
            if self.path.read_bytes() != raw:
                # A timed-out native Save can finish while the page readback
                # is running. Preserve its one dispatch for rebind; do not
                # acknowledge the earlier snapshot as the final saved file.
                raise NativeEngineeringDriverError('阶段保存仍有后续磁盘写入，需要重新核对原保存。')
            self._saved_bytes = self.baseline = raw
            progress['status'] = 'verified'
            return dict(data=raw, validation=validation, operation_id=operation_id, save_dispatched=True,
                        _stage_context=deepcopy(after), _stage_compile=deepcopy(compilation))
        except Exception as error:
            progress.update(status='unconfirmed' if progress['save_dispatched'] else 'rejected',
                            error_type=type(error).__name__)
            if isinstance(error, NativeEngineeringDriverError):
                error.observation.update(stage_save_operation_id=operation_id,
                    save_dispatched=progress['save_dispatched'], snapshot_returned=progress['snapshot_returned'])
            raise

    def pending_operations(self):
        return self.adapter.pending_operations()

    def show_completion(self, command, prepared, observation=None):
        """Return to the actual target page after the full-context readback."""
        self.finish_completion()
        self._ready()
        operation, plan = command['operation'], (prepared or {}).get('plan', {})
        if operation not in ('save', 'compile', 'show_assignments', 'reopen'):
            self._last_target = (deepcopy(command), deepcopy(prepared))
        if operation in ('create_global_labels', 'edit_global_labels', 'edit_pou_labels', 'delete_label'):
            scope = prepared['scope']
            self.driver._labels_page(plan['source_name'], scope)
            return self.driver._visible('全局标签表' if scope == 'global' else plan['source_name'] + ' 局部标签表')
        if operation in ('create_st_fb', 'edit_st_fb', 'create_st_program', 'edit_st_program'):
            self.driver._program_page(plan['source_name'], prepared['scope'], 'st')
            return self.driver._visible(plan['source_name'] + ' ST 编辑器')
        if operation in ('create_fbd_call', 'edit_fbd_graph'):
            owner = prepared.get('owner') or plan.get('owner')
            kind = prepared.get('owner_kind') or plan.get('owner_kind')
            selector = command.get('caller')
            if owner is None:
                matches = [row for row in (self._last_context or {}).get('inventory', {}).get('pous', [])
                           if selector in (row['owner'], row['owner'] + '.Program.pou', row['owner'] + '.Body.pou')]
                if len(matches) != 1:
                    raise NativeEngineeringDriverError('图形完成点没有明确的当前 Program/FB 归属。')
                owner, kind = matches[0]['owner'], matches[0]['owner_kind']
            self.driver._program_page(owner, kind or 'program', 'fbd')
            return self.driver._visible(owner + ' FBD 编辑器')
        if operation == 'configure_project':
            from gxw.project_metadata import native_allocation_observation_state
            expected = (self._last_context or {}).get('configuration')
            if not isinstance(expected, dict) or expected.get('status') != 'available':
                raise NativeEngineeringDriverError('分配设置完成点缺少刚刚核对的实际字段。')
            self.driver._command(0x685)
            dialog = self.driver._dialog('自动分配软元件设置')
            fields = self.driver._configuration_fields(dialog)
            if native_allocation_observation_state(fields) != native_allocation_observation_state(expected):
                raise NativeEngineeringDriverError('展示分配设置时字段已改变，保留原生页面。')
            self._completion_dialog = dict(dialog=dialog, fields=deepcopy(fields), identity=self.inspect())
            return self.driver._visible('自动分配软元件设置', dialog=dialog, **fields)
        if operation == 'compile':
            current = observation or getattr(self.driver, '_last_compile_run', None)
            handles = self.driver._owned_control_handles('Edit', 10013, visible=True)
            if not isinstance(current, dict) or not current.get('completed') or len(handles) != 1:
                raise NativeEngineeringDriverError('本次编译输出没有可见且已确认的原生摘要。')
            control = self.driver.ui._application(self.driver.session).window(handle=handles[0])
            if control.window_text() != current.get('output'):
                raise NativeEngineeringDriverError('可见的原生编译摘要已不属于本次结果。')
            return self.driver._visible('本次原生编译输出', output=current['output'])
        if operation == 'show_assignments':
            if not isinstance(observation, dict):
                raise NativeEngineeringDriverError('地址展示缺少刚刚核对的原生查询结果。')
            if observation.get('status') == 'unavailable':
                return dict(completed=False, status='unavailable',
                            reason='native_assignment_results_unavailable')
            return self.driver.show_assignments_completion(observation)
        if operation == 'reopen':
            if self._last_target is not None:
                return {**self.show_completion(*self._last_target), 'reopen': 'same_process'}
            return self.driver._visible('保存工程冷重开', same_process=True)
        if operation == 'save':
            if self._last_target is not None:
                return self.show_completion(*self._last_target)
            return self.driver._visible('原生工程保存')
        raise NativeEngineeringDriverError('该步骤没有已验证的原生可见完成点。')

    def finish_completion(self):
        from gxw.project_metadata import native_allocation_observation_state
        pending = self._completion_dialog
        if pending is None:
            return
        self.inspect()
        dialog = pending['dialog']
        if (_identity(self.inspect()) != _identity(pending['identity']) or not dialog.is_enabled()
                or native_allocation_observation_state(self.driver._configuration_fields(dialog))
                    != native_allocation_observation_state(pending['fields'])):
            raise NativeEngineeringDriverError('展示中的原生设置已被改变，未关闭或覆盖人工编辑。',
                                                observation={'status': 'unconfirmed'})
        self.driver._click(dialog, 2)
        self.driver._wait(lambda: not self.driver._dialogs(), '展示后的原生设置页面尚未关闭。')
        self._completion_dialog = None

    def rebind(self, expected_identity):
        actual = _identity(self.driver.inspect())
        if actual != self.identity or _identity(expected_identity) != self.identity:
            raise NativeEngineeringDriverError('重新绑定必须指向原来的进程启动身份、窗口与工程。')
        self._closed = False
        return self.read_context()

    def close(self):
        self._closed = True  # Leave the IDE and every unsaved native edit visible.

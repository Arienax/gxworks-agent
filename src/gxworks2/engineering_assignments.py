# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""AssignmentInspector operations on an explicitly bound native desktop."""
from __future__ import annotations
from .native_locale import native_menu_selection
from .engineering_errors import (NativeEngineeringDriverError)
from .engineering_observations import (_accessible_grid_rows)


class AssignmentInspector:
    def __init__(self, desktop):
        self.desktop = desktop

    def _cross_reference_panel(self, *, allow_missing=False):
        import win32gui
        controls = self.desktop._owned_control_handles('ComboBox', 20102)
        if not controls and allow_missing:
            return None
        if len(controls) != 1:
            raise NativeEngineeringDriverError('本工程的交叉引用搜索页面不明确。')
        return self.desktop.ui._application(self.desktop.session).window(handle=win32gui.GetParent(controls[0]))

    def _open_cross_reference(self):
        panel = self.desktop._cross_reference_panel(allow_missing=True)
        if panel is not None:
            return panel
        # Codejock routes this menu through its active command bar. A direct
        # frame WM_COMMAND can return without opening the search pane.
        entries, final_key = native_menu_selection('menu_find', 'menu_cross_reference',
            getattr(self.desktop, 'native_ui_language', 'zh-CN'))
        self.desktop._choose_native_menu(entries, final_key)
        return self.desktop._wait(lambda: self.desktop._cross_reference_panel(allow_missing=True),
                          '原生交叉参照窗口没有打开。')

    def _choose_native_menu(self, entries, final_key):
        import win32gui
        import win32process
        from pywinauto.keyboard import send_keys
        self.desktop._foreground()
        for key, caption in entries:
            send_keys(key, pause=0, vk_packet=False)
            def popup():
                handles = []
                def visit(handle, _):
                    if (win32gui.IsWindowVisible(handle)
                            and win32process.GetWindowThreadProcessId(handle)[1] == self.desktop.session.process_id
                            and win32gui.GetClassName(handle) == 'XTPPopupBar'
                            and win32gui.GetWindowText(handle) == caption):
                        handles.append(handle)
                win32gui.EnumWindows(visit, None)
                if len(handles) > 1:
                    raise NativeEngineeringDriverError('原生菜单不唯一。')
                return handles[0] if handles else None
            handle = self.desktop._wait(popup, '原生菜单没有打开：' + caption)
            if win32gui.GetForegroundWindow() != self.desktop.session.window_handle:
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
        panel = self.desktop._open_cross_reference()
        # Use the native docking caption to float the real result pane, then
        # resize it so the address row is actually visible beside the graph.
        container = panel
        while container.handle != self.desktop.session.window_handle and container.class_name() not in (
                'XTPDockingPaneTabbedContainer', 'XTPDockingPaneMiniWnd'):
            container = container.parent()
        if container.class_name() == 'XTPDockingPaneTabbedContainer':
            win32gui.SendMessage(container.handle, 0x0203, 1, (16 << 16) | 100)
            panel = self.desktop._cross_reference_panel()
        floating = panel
        while floating.handle != self.desktop.session.window_handle and floating.class_name() != 'XTPDockingPaneMiniWnd':
            floating = floating.parent()
        if floating.class_name() != 'XTPDockingPaneMiniWnd':
            raise NativeEngineeringDriverError('原生地址结果页面没有形成可见窗口。')
        frame = self.desktop._window().rectangle()
        width, height = min(1450, frame.width() - 80), min(570, frame.height() - 250)
        win32gui.SetWindowPos(floating.handle, 0, frame.left + 40,
                             frame.bottom - height - 60, width, height, 0x0014)
        results = []
        for query, search_text in zip(queries, searches):
            panel = self.desktop._cross_reference_panel()
            self.desktop._foreground(floating.handle)
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
                    language=getattr(self.desktop, 'native_ui_language', 'zh-CN')) is not None else None
            text = self.desktop._wait(completed_search, '本次交叉引用搜索没有完成。')
            grids = [c for c in panel.descendants(class_name='VSFlexGrid8N') if c.is_visible()]
            if len(grids) != 1 or grids[0].rectangle().height() < 120:
                raise NativeEngineeringDriverError('原生地址结果行没有足够的可见区域。')
            rows = _accessible_grid_rows(grids[0].handle,
                native_ui_language=getattr(self.desktop, 'native_ui_language', 'zh-CN'))
            completion = self.desktop._visible('本次编译的交叉引用', query=query['name'], source=query['source'],
                                       scope=query.get('scope', 'local'), rows=rows)
            results.append({'query': deepcopy(query), 'search_text': search.window_text(),
                            'summary': text, 'rows': rows, 'visible_completion': completion})
        return {'cross_references': results, 'pid': self.desktop.session.process_id,
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
        self.desktop._last_current_assignments = None
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
        current = self.desktop.read_current_compile_result(revision=revision)
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
        observed = self.desktop._show_assignments({'queries': requested, 'revision': revision,
                                          'allocation_context': allocation_context})
        fresh = self.desktop.read_current_compile_result(revision=revision)
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
            self.desktop._last_current_assignments = dict(result=deepcopy(result), record=deepcopy(last),
                allocation_context=deepcopy(allocation_context))
        return result

    def show_assignments_completion(self, observation):
        """Reopen one confirmed native query after full-context page readback."""
        from copy import deepcopy
        from gxw.live_context import _identity
        cached = getattr(self.desktop, '_last_current_assignments', None)
        if (not isinstance(cached, dict) or not isinstance(observation, dict)
                or any(observation.get(key) != cached['result'].get(key)
                       for key in ('revision', 'compile_run_id', 'identity', 'cross_references'))):
            raise NativeEngineeringDriverError('地址展示缺少本次已核对查询的原始回执。')
        expected = cached['result']
        def current():
            run = self.desktop.read_current_compile_result(revision=expected['revision'])
            if (run.get('status') != 'observed' or run.get('compile_run_id') != expected['compile_run_id']
                    or _identity(run.get('identity')) != expected['identity']):
                raise NativeEngineeringDriverError('地址完成页面已不属于本次编译或工程。')
        current()
        record = cached['record']
        observed = self.desktop._show_assignments(dict(queries=[deepcopy(record['query'])], revision=expected['revision'],
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

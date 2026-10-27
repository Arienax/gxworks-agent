import os

import pytest

from gxworks2.finder import GXWorks2Finder
from gxworks2.ui_automation import PywinautoGXWorks2UIAutomation


def _fb_definition_navigation_double(monkeypatch, *, title='Logic [MAIN.Instance] 程序本体 [ST]',
                                     existing=False, behavior='success'):
    """Replay only navigation messages; destroyed dialog specs reject access."""
    import sys
    from types import SimpleNamespace
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError
    state = SimpleNamespace(current=10, title=title, existing=existing, dialog_live=False,
                            commands=[], clicks=[], activations=[], selected=[], dialog_handle_reads=0)
    definition = 'Logic [FB] 程序本体 [ST]'
    captions = {200:'FB实例选择', 201:'解除选择(&C)', 202:'', 203:'确定', 204:'取消'}
    classes = {200:'#32770', 201:'Button', 202:'ListBox', 203:'Button', 204:'Button'}
    controls = {10001:201, 10002:202, 1:203, 2:204}
    if behavior == 'missing_clear':
        controls.pop(10001)
    elif behavior == 'wrong_clear_caption':
        captions[201] = '另一个操作'
    elif behavior == 'wrong_ok_caption':
        captions[203] = '删除'
    elif behavior == 'wrong_list_class':
        classes[202] = 'Edit'
    elif behavior == 'wrong_dialog_class':
        classes[200] = 'OtherDialog'

    class Document:
        def __init__(self, handle):
            self.handle = handle
        def window_text(self):
            return state.title if self.handle == 10 else definition
    class Dialog:
        @property
        def handle(self):
            state.dialog_handle_reads += 1
            assert state.dialog_live, 'Destroyed dialog HWND must have been cached before Clear'
            return 200
        def window_text(self):
            return captions[200]
        def is_enabled(self):
            return True
    dialog = Dialog()
    documents = {10:Document(10), 11:Document(11)}
    tree = SimpleNamespace(control_id=lambda:12051, is_visible=lambda:True, is_enabled=lambda:True,
                           get_item=lambda path, exact: SimpleNamespace(elem=777))
    driver = object.__new__(NativeEngineeringDriver)
    driver.session = SimpleNamespace(process_id=1234, window_handle=100)
    driver._idle = lambda: None
    driver._foreground = lambda *args: None
    driver._window = lambda: SimpleNamespace(descendants=lambda **kwargs:[tree])
    driver._active_document = lambda: documents[state.current]
    driver._dialogs = lambda: [dialog] if state.dialog_live else []
    def wait(predicate, message, **kwargs):
        for _ in range(5):
            result = predicate()
            if result:
                return result
        raise NativeEngineeringDriverError(message)
    driver._wait = wait
    def activate(session, pattern):
        state.activations.append(pattern.pattern)
        if state.existing:
            state.current = 11
            return True
        return False
    driver.ui = SimpleNamespace(_activate_native_document=activate,
        _select_native_tree_item=lambda tree,item:state.selected.append(item.elem), _send_tree_enter=lambda tree:None)
    def command(value):
        assert value == 0x4EF, 'Navigation must not save, compile, close, reopen or edit source'
        state.commands.append(value)
        state.dialog_live = behavior != 'missing_dialog'
    driver._command = command
    def post(handle, message, identity, control):
        assert (handle,message,identity,control) == (200,0x111,10001,201)
        state.clicks.append((handle,message,identity,control))
        if behavior != 'clear_timeout':
            state.dialog_live = False
            state.title = ('Logic [FB] 程序本体 [结构化梯形图/FBD]' if title.endswith('[结构化梯形图/FBD]')
                           else definition)
        if behavior == 'wrong_return_title':
            state.title = 'Other [FB] 程序本体 [ST]'
        if behavior == 'changed_document':
            state.current = 11
    def text(handle):
        return documents[handle].window_text() if handle in documents else captions.get(handle, '')
    monkeypatch.setitem(sys.modules, 'win32gui', SimpleNamespace(EnumWindows=lambda callback, data:None,
        IsWindowVisible=lambda handle:state.dialog_live if handle==200 else True,
        IsWindowEnabled=lambda handle:not (handle==201 and behavior=='disabled_clear'),
        IsWindow=lambda handle:state.dialog_live if handle==200 else handle in documents or handle in captions,
        IsChild=lambda parent,child:(parent==100 and child in documents) or (parent==200 and child in captions and child!=200),
        GetWindowText=text, GetClassName=lambda handle:classes.get(handle,'MdiChild'),
        GetAncestor=lambda handle,flag:999 if behavior=='foreign_dialog_owner' else 100,
        GetDlgItem=lambda handle,identity:controls.get(identity,0), PostMessage=post))
    monkeypatch.setitem(sys.modules, 'win32process', SimpleNamespace(GetWindowThreadProcessId=lambda handle:
        (77,5678 if handle==200 and behavior=='foreign_dialog_pid' else 1234)))
    return driver, state


@pytest.mark.parametrize('language', ['ST', '结构化梯形图/FBD'])
def test_fb_definition_navigation_clears_only_the_same_qualified_instance_once(monkeypatch, language):
    driver,state = _fb_definition_navigation_double(monkeypatch, title=f'Logic [MAIN.Instance] 程序本体 [{language}]')
    document = driver._navigate([('程序部件','FB/FUN','Logic','程序本体')], r'^Logic \[FB\] 程序本体 \[')
    assert document.handle == 10
    assert document.window_text() == f'Logic [FB] 程序本体 [{language}]'
    assert state.commands == [0x4EF] and state.clicks == [(200,0x111,10001,201)]
    assert state.activations and state.selected == [777]
    assert not state.dialog_live


def test_fb_definition_navigation_prefers_an_existing_exact_definition_mdi(monkeypatch):
    driver,state = _fb_definition_navigation_double(monkeypatch, existing=True)
    document = driver._navigate([('程序部件','FB/FUN','Logic','程序本体')], r'^Logic \[FB\] 程序本体 \[')
    assert document.handle == 11 and state.activations
    assert state.commands == state.clicks == []


@pytest.mark.parametrize(('title','folder','leaf','pattern'), [
    ('Other [MAIN.Instance] 程序本体 [ST]','FB/FUN','程序本体',r'^Logic \[FB\] 程序本体 \['),
    ('Logic [PRG] 程序本体 [ST]','FB/FUN','程序本体',r'^Logic \[FB\] 程序本体 \['),
    ('Other [PRG] 程序本体 [ST]','FB/FUN','程序本体',r'^Logic \[FB\] 程序本体 \['),
    ('Logic [MAIN.Instance] 程序本体 [ST]','程序','程序本体',r'^Logic \[PRG\] 程序本体 \['),
    ('Logic [MAIN.Instance] 程序本体 [ST]','FB/FUN','局部标签',r'^Logic \[FB\] 程序本体 \['),
    ('Logic [MAIN.Instance] 程序本体 [ST]','FB/FUN','程序本体',r'Logic \[FB\]'),
    ('Logic [MAIN.Instance] 程序本体 [ST]','FB/FUN','程序本体',r'^.* \[FB\] 程序本体 \['),
    ('Logic [Live_Inst] 程序本体 [ST]','FB/FUN','程序本体',r'^Logic \[FB\] 程序本体 \['),
    ('Logic [MAIN.Instance] 程序本体 [结构化梯形图/FBD]','FB/FUN','程序本体',r'^Logic \[FB\] 程序本体 \[ST\]')])
def test_fb_definition_navigation_refuses_other_owners_programs_or_inexact_targets(monkeypatch,title,folder,leaf,pattern):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver,state = _fb_definition_navigation_double(monkeypatch, title=title)
    with pytest.raises(NativeEngineeringDriverError, match='没有切换'):
        driver._navigate([('程序部件',folder,'Logic',leaf)], pattern)
    assert state.commands == state.clicks == []


@pytest.mark.parametrize('behavior', ['missing_clear','wrong_clear_caption','wrong_ok_caption','wrong_list_class',
    'wrong_dialog_class','foreign_dialog_owner','foreign_dialog_pid','disabled_clear','missing_dialog'])
def test_fb_definition_navigation_checks_owned_dialog_structure_before_clear(monkeypatch,behavior):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver,state = _fb_definition_navigation_double(monkeypatch, behavior=behavior)
    with pytest.raises(NativeEngineeringDriverError):
        driver._navigate([('程序部件','FB/FUN','Logic','程序本体')], r'^Logic \[FB\] 程序本体 \[')
    assert state.commands == [0x4EF] and state.clicks == []


@pytest.mark.parametrize('behavior', ['clear_timeout','wrong_return_title','changed_document'])
def test_fb_definition_navigation_never_replays_clear_after_timeout_or_changed_document(monkeypatch,behavior):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver,state = _fb_definition_navigation_double(monkeypatch, behavior=behavior)
    with pytest.raises(NativeEngineeringDriverError):
        driver._navigate([('程序部件','FB/FUN','Logic','程序本体')], r'^Logic \[FB\] 程序本体 \[')
    assert state.commands == [0x4EF] and state.clicks == [(200,0x111,10001,201)]


def _graph_network_focus_double(monkeypatch, *, kind='program', behavior='success', active=0):
    """Frozen two-network content plus the witnessed native Jump controls."""
    import sys
    from copy import deepcopy
    from types import SimpleNamespace
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError
    from tests.test_gxw_active_graph import current_native_terminals
    owner = 'POU_01' if kind == 'program' else 'Live_FBD_FB'
    title = owner + (' [PRG]' if kind == 'program' else ' [FB]') + ' 程序本体 [结构化梯形图/FBD]'
    graph = current_native_terminals()
    identity = dict(process_id=1234, window_handle=100, process_started='123',
                    executable='GX/GD2.exe', software_version='1.635.0.1',
                    project_path='isolated.gxw', cpu_menu='FX3U/FX3UC')
    graph.update(owner=owner, owner_kind=kind, active_network=active, identity=deepcopy(identity),
                 data_id='actual-data-id', document_handle=10, view_handle=11,
                 native_edit_state=dict(inline_editor=0, network_clipboard_active=0, base_mode=0x643))
    graph['networks'].append(dict(index=1, nodes=[], wires=[], junctions=[]))
    state = SimpleNamespace(graph=graph, dialog_live=False, commands=[], clicks=[], reads=[],
        writes=[], navigations=[], idle_calls=0, ready_calls=0, title=title, current_document=10,
        editor_text='1', combo_text='1', dialog_handle_reads=0)
    controls = {10013:201, 10014:202, 10015:203, 10016:204}
    classes = {200:'#32770', 201:'ComboBox', 202:'Button', 203:'Button', 204:'Static', 205:'Edit'}
    captions = {200:'跳转', 202:'确定', 203:'取消', 204:'梯形图块号'}
    if behavior == 'missing_cancel':
        controls.pop(10015)
    if behavior == 'wrong_static_caption':
        captions[204] = '行号'
    if behavior == 'wrong_ok_caption':
        captions[202] = '删除'
    if behavior == 'wrong_dialog_class':
        classes[200] = 'OtherDialog'
    if behavior == 'wrong_combo_class':
        classes[201] = 'Edit'
    if behavior == 'wrong_dialog_title':
        captions[200] = '其它窗口'
    if behavior == 'wrong_active_owner':
        state.title = 'Other [PRG] 程序本体 [结构化梯形图/FBD]'
    if behavior == 'inline_pending':
        graph['native_edit_state']['inline_editor'] = 999
    if behavior == 'clipboard_pending':
        graph['native_edit_state']['network_clipboard_active'] = 1

    class Editor:
        handle = 205
        def control_id(self):
            return 1002 if behavior == 'wrong_edit_id' else 1001
        def set_edit_text(self, value):
            state.writes.append(value)
            state.editor_text = value
            if behavior != 'combo_text_mismatch':
                state.combo_text = value
        def window_text(self):
            return state.editor_text
    editor = Editor()
    class Combo:
        handle = 201
        def wrapper_object(self):
            return self
        def children(self, **kwargs):
            assert kwargs == {'class_name':'Edit'}
            return [] if behavior == 'missing_edit' else [editor, editor] if behavior == 'duplicate_edit' else [editor]
        def window_text(self):
            return state.combo_text
        # Deliberately no set_edit_text: only the owned Edit supports it.
    combo = Combo()
    class Dialog:
        @property
        def handle(self):
            assert state.dialog_live, 'Destroyed Jump dialog must not be resolved again'
            state.dialog_handle_reads += 1
            return 200
        def window_text(self):
            return captions[200]
        def is_enabled(self):
            return behavior != 'disabled_dialog'
        def child_window(self, **kwargs):
            assert kwargs == {'control_id':10013, 'class_name':'ComboBox'}
            return combo
    dialog = Dialog()
    driver = object.__new__(NativeEngineeringDriver)
    driver.session = SimpleNamespace(process_id=1234, window_handle=100)
    driver.inspect = lambda: deepcopy(identity)
    driver._active_document = lambda: SimpleNamespace(handle=state.current_document, window_text=lambda:state.title)
    def idle():
        state.idle_calls += 1
        if state.dialog_live:
            raise NativeEngineeringDriverError('Pending native modal')
    driver._idle = idle
    driver._dialogs = lambda:[dialog] if state.dialog_live else []
    def navigate(name, owner_kind, language):
        state.navigations.append((name, owner_kind, language))
        state.title = title
    driver._program_page = navigate
    def wait(predicate, message, **kwargs):
        for _ in range(5):
            result = predicate()
            if result:
                return result
        raise NativeEngineeringDriverError(message)
    driver._wait = wait
    def command(value):
        assert value == 0x606, 'Focus must not save, compile, insert or edit source'
        state.commands.append(value)
        state.dialog_live = behavior != 'missing_dialog'
    driver._command = command
    def click(target, control_id):
        assert target is dialog and control_id == 10014
        assert state.editor_text == state.combo_text
        state.clicks.append((int(target.handle), control_id))
        if behavior == 'confirm_timeout':
            return
        state.dialog_live = False
        if behavior != 'wrong_active_network':
            state.graph['active_network'] = int(state.editor_text) - 1
        if behavior == 'changed_document':
            state.current_document = 12
        for key, value in (('data_id','other-data-id'), ('view_handle',12), ('document_handle',12)):
            if behavior == 'changed_' + key:
                state.graph[key] = value
        if behavior == 'changed_identity':
            state.graph['identity']['process_started'] = 'different'
        if behavior == 'changed_source':
            state.graph['networks'][0]['nodes'][0]['name'] = 'Probe_B002'
        if behavior.startswith('changed_port_'):
            key = behavior.removeprefix('changed_port_')
            state.graph['networks'][0]['nodes'][0]['connections']['ports'][0][key] = {
                'name':'unexpected', 'local_x':3, 'flags':3, 'native_type_mask':4}[key]
        if behavior == 'viewport_and_selection':
            state.graph['viewport'] = [100, 200]
            state.graph['networks'][0]['nodes'][0].update(selected=False, address=0x220000, record='new heap bytes')
            state.graph['networks'][0]['wires'][0]['selected'] = True
    driver._click = click
    class Adapter:
        def _require_ready(self):
            state.ready_calls += 1
            if behavior == 'pending_child':
                raise NativeEngineeringDriverError('Pending same live native child')
        def read_graph(self, target, *, owner, kind, data_id=None):
            assert target is driver
            assert owner == state.graph['owner'] and kind == state.graph['owner_kind']
            state.reads.append(data_id)
            if len(state.reads) == 2 and behavior == 'source_changed_before_dispatch':
                state.graph['networks'][0]['nodes'][0]['name'] = 'Probe_B002'
            return deepcopy(state.graph)
    def text(handle):
        if handle == 10:
            return state.title
        if handle == 205:
            return state.editor_text
        if handle == 201:
            return state.combo_text
        return captions.get(handle, '')
    monkeypatch.setitem(sys.modules, 'win32gui', SimpleNamespace(
        IsWindow=lambda handle:state.dialog_live if handle == 200 else handle in (10,11,201,202,203,204,205),
        IsChild=lambda parent,child:(parent,child) in ((100,10),(10,11)),
        IsWindowVisible=lambda handle:behavior != 'hidden_edit' or handle != 205,
        IsWindowEnabled=lambda handle:behavior != 'disabled_edit' or handle != 205,
        GetWindowText=text, GetClassName=lambda handle:classes.get(handle, 'MdiChild'),
        GetAncestor=lambda handle,flag:999 if behavior == 'foreign_dialog_owner' else 100,
        GetParent=lambda handle:999 if behavior == 'foreign_edit_parent' and handle == 205 else
            201 if handle == 205 else 200,
        GetDlgItem=lambda handle,control_id:controls.get(control_id,0)))
    monkeypatch.setitem(sys.modules, 'win32process', SimpleNamespace(GetWindowThreadProcessId=lambda handle:
        (77,5678 if (behavior == 'foreign_dialog_pid' and handle == 200
                   or behavior == 'foreign_edit_pid' and handle == 205) else 1234)))
    return driver, Adapter(), state, owner


@pytest.mark.parametrize('kind', ['program','fb'])
@pytest.mark.parametrize(('active','requested'), [(0,1),(1,0)])
def test_graph_network_focus_uses_one_owned_jump_edit_and_one_confirmation(monkeypatch,kind,active,requested):
    from gxw.active_graph import graph_source_state
    driver,adapter,state,owner = _graph_network_focus_double(monkeypatch,kind=kind,active=active)
    before = graph_source_state(state.graph)
    result = driver.focus_graph_network(adapter,owner,kind,requested)
    assert result['active_network'] == requested
    assert graph_source_state(result) == before
    assert state.commands == [0x606] and state.clicks == [(200,10014)]
    assert state.writes == [str(requested+1)] and state.reads == [None,'actual-data-id','actual-data-id']
    assert not state.dialog_live


def test_graph_network_focus_current_network_returns_fresh_read_without_command(monkeypatch):
    driver,adapter,state,owner = _graph_network_focus_double(monkeypatch)
    result = driver.focus_graph_network(adapter,owner,'program',0)
    assert result is not state.graph and result['active_network'] == 0
    assert state.commands == state.clicks == state.writes == [] and state.reads == [None]


def test_graph_network_focus_navigates_to_the_exact_owner_before_reading(monkeypatch):
    driver,adapter,state,owner = _graph_network_focus_double(monkeypatch,kind='fb',behavior='wrong_active_owner')
    result = driver.focus_graph_network(adapter,owner,'fb',1)
    assert result['owner'] == owner and result['owner_kind'] == 'fb'
    assert state.navigations == [(owner,'fb','fbd')]
    assert state.commands == [0x606]


@pytest.mark.parametrize('network', [-1,True,'1',512,1.0,2])
def test_graph_network_focus_rejects_invalid_or_nonexistent_network_before_jump(monkeypatch,network):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver,adapter,state,owner = _graph_network_focus_double(monkeypatch)
    with pytest.raises(NativeEngineeringDriverError):
        driver.focus_graph_network(adapter,owner,'program',network)
    assert state.commands == state.clicks == state.writes == []


@pytest.mark.parametrize('behavior', ['pending_child','inline_pending','clipboard_pending','source_changed_before_dispatch'])
def test_graph_network_focus_refuses_pending_native_work_or_stale_source_before_jump(monkeypatch,behavior):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver,adapter,state,owner = _graph_network_focus_double(monkeypatch,behavior=behavior)
    with pytest.raises(NativeEngineeringDriverError):
        driver.focus_graph_network(adapter,owner,'program',1)
    assert state.commands == state.clicks == state.writes == []
    if behavior == 'pending_child':
        assert state.idle_calls == 0 and state.reads == []


@pytest.mark.parametrize('behavior', ['missing_cancel','wrong_static_caption','wrong_ok_caption',
    'wrong_dialog_class','wrong_combo_class','wrong_dialog_title','disabled_dialog',
    'foreign_dialog_owner','foreign_dialog_pid','wrong_edit_id','missing_edit','duplicate_edit',
    'hidden_edit','disabled_edit','foreign_edit_parent','foreign_edit_pid','missing_dialog'])
def test_graph_network_focus_checks_the_owned_complete_native_jump_dialog(monkeypatch,behavior):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver,adapter,state,owner = _graph_network_focus_double(monkeypatch,behavior=behavior)
    with pytest.raises(NativeEngineeringDriverError) as error:
        driver.focus_graph_network(adapter,owner,'program',1)
    assert state.commands == [0x606] and state.clicks == state.writes == []
    assert error.value.observation['status'] == 'unconfirmed'
    assert error.value.observation['source_mutation_requested'] is False


def test_graph_network_focus_never_confirms_a_combo_edit_text_mismatch(monkeypatch):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver,adapter,state,owner = _graph_network_focus_double(monkeypatch,behavior='combo_text_mismatch')
    with pytest.raises(NativeEngineeringDriverError):
        driver.focus_graph_network(adapter,owner,'program',1)
    assert state.commands == [0x606] and state.writes == ['2'] and state.clicks == []


@pytest.mark.parametrize('behavior', ['confirm_timeout','wrong_active_network','changed_document','changed_identity',
    'changed_data_id','changed_view_handle','changed_document_handle','changed_source','changed_port_name',
    'changed_port_local_x','changed_port_flags','changed_port_native_type_mask'])
def test_graph_network_focus_requires_same_binding_source_and_actual_network_without_retry(monkeypatch,behavior):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver,adapter,state,owner = _graph_network_focus_double(monkeypatch,behavior=behavior)
    with pytest.raises(NativeEngineeringDriverError) as error:
        driver.focus_graph_network(adapter,owner,'program',1)
    assert state.commands == [0x606] and state.writes == ['2'] and state.clicks == [(200,10014)]
    assert error.value.observation['status'] == 'unconfirmed'
    assert error.value.observation['save_performed'] is False


def test_graph_network_focus_allows_viewport_selection_and_rebuilt_pointers(monkeypatch):
    from gxw.active_graph import graph_source_state
    driver,adapter,state,owner = _graph_network_focus_double(monkeypatch,behavior='viewport_and_selection')
    before = graph_source_state(state.graph)
    result = driver.focus_graph_network(adapter,owner,'program',1)
    assert result['active_network'] == 1 and result['viewport'] == [100,200]
    assert graph_source_state(result) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows-only GX Works2 integration")
def test_running_gxworks2_without_project_has_no_csv_import_command():
    candidates = GXWorks2Finder().list_running()
    if len(candidates) != 1:
        pytest.skip('Live boundary requires exactly one explicitly isolated GX Works2 target')
    session = candidates[0]
    if session is None:
        pytest.skip("GX Works2 is not running")
    if not session.project_state_known or session.project_open:
        pytest.skip("GX Works2 currently has a project open")

    state = PywinautoGXWorks2UIAutomation(timeout=4).inspect_project(session)
    assert state["automation_available"]
    assert not state["project_open"]
    assert not state["program_ready"]
    assert not state["read_csv_available"]


@pytest.mark.skipif(os.name != "nt", reason="Windows-only GX Works2 integration")
def test_running_gxworks2_output_summary_is_machine_readable():
    candidates = GXWorks2Finder().list_running()
    if len(candidates) != 1:
        pytest.skip('Live boundary requires exactly one explicitly isolated GX Works2 target')
    session = candidates[0]
    if session is None or not session.project_open:
        pytest.skip("GX Works2 does not currently have an open program")

    summary = PywinautoGXWorks2UIAutomation._read_output_summary(session)
    if not summary:
        pytest.skip("GX Works2 has no current operation summary")
    assert "Error:" in summary
    assert "Warning:" in summary


class _VisibleDriver:
    """Saved native witnesses replayed without opening or controlling an IDE."""
    def __init__(self, raw, transitions=(), *, mutate_identity=None, receipt=None):
        self.raw = raw
        self.transitions = list(transitions)
        self.calls = []
        self.closed = False
        self.mutate_identity = mutate_identity
        self.receipt = receipt
        self.identity = dict(process_id=701, window_handle=9001, process_started='123456',
            executable=r'D:\GXWORKS2\GPPW2\GD2.exe', software_version='1.635.0.1',
            project_path=r'D:\isolated\visible.gxw', cpu_menu='FX3U/FX3UC')

    def inspect(self):
        return dict(self.identity)

    def snapshot(self):
        return self.raw

    def read_saved_project(self):
        # This fixture has no native UI; snapshot is just its byte source.
        return self.snapshot()

    def execute(self, command, prepared):
        import copy
        self.calls.append((copy.deepcopy(command), copy.deepcopy(prepared)))
        if self.transitions:
            expected, saved = self.transitions.pop(0)
            assert self.raw == expected
            self.raw = saved
        if self.mutate_identity:
            self.identity.update(self.mutate_identity)
        if self.receipt is not None:
            return copy.deepcopy(self.receipt)
        result = {'visible_completion': {'completed': True, 'page': command['operation']}}
        if command['operation'] == 'compile':
            result.update(command='rebuild_all', output='已完成全部编译。Error: 0, Warning: 1, CheckWarning: 0',
                completed=True, saved=True, source_unchanged=True, pid=self.identity['process_id'],
                revision=prepared['plan']['revision'])
        elif command['operation'] == 'show_assignments':
            result['cross_references'] = [_native_cross_reference_record(row['query'], row['assignment']['address'])
                                          for row in prepared['plan']['queried_assignments']]
        return result

    def reopen(self):
        self.calls.append(({'operation': 'reopen'}, {}))
        return self.raw

    def close(self):
        self.closed = True


@pytest.mark.parametrize('version', ['v0002', 'v0005', 'v0006', 'v0008', 'v0010'])
def test_visible_authoring_checks_frozen_saved_source_without_offline_call_logs(version):
    from application.native_engineering import NativeEngineeringSession
    from tests.test_gxw_object_model import authored_fb_witness
    before, after, command, _ = authored_fb_witness(version)
    # RichEdit changes line endings; every other source character is checked.
    if 'st_text' in command:
        command = {**command, 'st_text': command['st_text'].replace('\r\n', '\n')}
    driver = _VisibleDriver(before, [(before, after)])
    result = NativeEngineeringSession(driver, 'FX3U').run([command], baseline=before)
    assert result['status'] == 'completed', result.get('error')
    assert result['steps'][0]['verification']['status'] == 'matched'
    assert result['data'] == after and driver.closed
    assert 'calls' not in result['steps'][0]['observation']
    assert result['compile']['status'] == 'not_run'


def test_visible_session_rebuilds_each_plan_from_its_previous_saved_native_state():
    from application.native_engineering import NativeEngineeringSession
    from tests.test_gxw_object_model import authored_fb_witness
    before, middle, create, _ = authored_fb_witness('v0005')
    edit_before, after, edit, _ = authored_fb_witness('v0006')
    assert middle == edit_before
    progress = []
    driver = _VisibleDriver(before, [(before, middle), (middle, after)])
    session = NativeEngineeringSession(driver, 'FX3U/FX3UC', progress=progress.append)
    result = session.run([create, edit, {'operation': 'save'}, {'operation': 'reopen'}], baseline=before)
    assert result['passed'], result.get('error')
    assert result['revision'] == 2
    assert driver.calls[1][1]['before']['st_text'] == create['st_text']
    assert result['save']['status'] == result['reopen']['status'] == 'passed'
    assert result['data'] == after
    assert [s['status'] for s in progress] == ['executing', 'completed'] * 4
    with pytest.raises(RuntimeError, match='replayed'):
        session.run([{'operation': 'save'}])


@pytest.mark.parametrize('field,value', [
    ('process_id', 702), ('window_handle', 9002), ('process_started', 'another'),
    ('executable', r'C:\other\GD2.exe'), ('software_version', '1.0.0.0'),
    ('project_path', r'D:\isolated\other.gxw'), ('cpu_menu', 'L02'),
])
def test_visible_target_change_aborts_after_first_effect_and_does_not_replay(field, value):
    from application.native_engineering import NativeEngineeringSession
    from gxw.project_templates import blank_fbd_baseline
    driver = _VisibleDriver(blank_fbd_baseline('FX3U'), mutate_identity={field: value})
    result = NativeEngineeringSession(driver, 'FX3U').run([{'operation': 'save'}, {'operation': 'compile'}])
    assert result['status'] == 'interrupted' and not result['passed']
    assert len(driver.calls) == 1 and driver.closed
    assert result['compile']['status'] == 'not_run'


def test_visible_claim_or_offline_call_log_cannot_replace_source_readback():
    from application.native_engineering import NativeEngineeringSession
    from tests.test_gxw_object_model import authored_fb_witness
    before, _, command, report = authored_fb_witness('v0005')
    driver = _VisibleDriver(before, receipt={**report['native_workspace'],
        'visible_completion': {'completed': True, 'page': 'ST editor'}})
    result = NativeEngineeringSession(driver, 'FX3U').run([command, {'operation': 'save'}])
    assert result['status'] == 'interrupted' and not result['passed']
    assert len(driver.calls) == 1
    assert 'readback' in result['error']['message'] or 'missing' in result['error']['message']


def test_visible_label_plan_owns_default_and_constant_csv_values():
    from gxw.visible_engineering import prepare_authoring
    from gxw.models import GXWFormatError
    from tests.test_gxw_object_model import authored_fb_witness
    before, _, command, _ = authored_fb_witness('v0005')
    command = {**command, 'declarations': [
        {'name': 'Input', 'data_type': 'INT', 'class_name': 'VAR_INPUT'},
        {'name': 'Gain', 'data_type': 'INT', 'class_name': 'VAR_CONSTANT', 'initial_value': '2'},
        {'name': 'Value', 'data_type': 'INT', 'class_name': 'VAR_OUTPUT'}]}
    plan = prepare_authoring(before, command)['plan']
    assert [(row['name'], row['initial_value'], row['csv_constant']) for row in plan['rows']] == [
        ('Input', '0', ''), ('Gain', '2', '2'), ('Value', '0', '')]
    command['declarations'][0]['initial_value'] = '2'
    with pytest.raises(GXWFormatError, match='nondefault'):
        prepare_authoring(before, command)


@pytest.mark.parametrize('change, expected', [
    ({}, 'passed'), ({'completed': False}, 'unverified'), ({'saved': False}, 'unverified'),
    ({'source_unchanged': False}, 'unverified'), ({'pid': 702}, 'unverified'),
    ({'revision': 2}, 'unverified'), ({'command': 'compile'}, 'unverified'),
    ({'output': '已完成全部编译。Error: 2, Warning: 0, CheckWarning: 0'}, 'failed'),
    ({'output': 'command sent'}, 'unverified'),
])
def test_visible_compile_requires_fresh_completed_native_summary(change, expected):
    from application.native_engineering import compile_observation
    observation = dict(command='rebuild_all', completed=True, saved=True, source_unchanged=True,
        pid=701, revision=1, output='已完成全部编译。Error: 0, Warning: 3, CheckWarning: 2')
    observation.update(change)
    result = compile_observation(observation, process_id=701, revision=1)
    assert result['status'] == expected
    if expected == 'passed':
        assert result['warnings'] == 3 and result['check_warnings'] == 2
    assert result['execution'] == 'not_run'


def test_visible_compile_summary_keeps_all_generated_counts_independent():
    from hypothesis import example, given, settings, strategies as st
    from application.native_engineering import compile_observation

    @settings(max_examples=40, deadline=None, derandomize=True)
    @example(errors=0, warnings=0, check_warnings=1, order=('CheckWarning', 'Warning', 'Error'))
    @given(errors=st.integers(0, 1000000), warnings=st.integers(0, 1000000),
           check_warnings=st.integers(0, 1000000),
           order=st.permutations(('Error', 'Warning', 'CheckWarning')))
    def check(errors, warnings, check_warnings, order):
        supplied = {'Error': errors, 'Warning': warnings, 'CheckWarning': check_warnings}
        observation = dict(command='rebuild_all', completed=True, saved=True, source_unchanged=True,
            pid=701, revision=1, output='已完成全部编译。' + ', '.join(f'{key}: {supplied[key]}' for key in order))
        result = compile_observation(observation, process_id=701, revision=1)
        assert (result['errors'], result['warnings'], result['check_warnings']) == (errors, warnings, check_warnings)
        assert result['status'] == ('passed' if errors == 0 else 'failed')
    check()


def _visible_cross_reference_witness():
    from tools.offline_inputs import private_path
    return private_path('research/experiments/visible-session-20261017/lab-compiled.gxw').read_bytes()


def _native_cross_reference_record(query, address='D2999'):
    import copy
    return {'query': copy.deepcopy(query), 'search_text': query['name'],
        'summary': '1件：软元件/标签"' + query['name'] + '"的交叉参照信息',
        'rows': [{'label': query['name'], 'address': address, 'program': 'MAIN',
                  'pou': 'POU_01', 'location': '1,...', 'read_write': ''}],
        'visible_completion': {'completed': True, 'page': '交叉引用'}}


def test_visible_addresses_come_from_the_same_gui_compilation_saved_snapshot():
    from application.native_engineering import NativeEngineeringSession
    from gxw.visible_engineering import saved_assignments
    before = after = _visible_cross_reference_witness()
    queries = [{'scope': 'global', 'source': 'Global1.gh', 'name': 'Result'}]
    driver = _VisibleDriver(before, [(before, after)])
    result = NativeEngineeringSession(driver, 'FX3U').run([
        {'operation': 'compile'}, {'operation': 'show_assignments', 'queries': queries},
        {'operation': 'save'}, {'operation': 'reopen'}])
    assert result['passed'], result.get('error')
    assert result['compile']['status'] == 'passed'
    assert result['assignments']['status'] == 'current'
    assert result['assignments']['rows'] == saved_assignments(after)['rows']
    assert result['assignments']['rows']
    assert result['assignments']['source_usage'] == 'unverified'
    references = result['assignments']['native_cross_references']
    assert references['status'] == 'matched'
    assert references['queries'][0]['assignment']['address'] == 'D2999'
    assert references['queries'][0]['source_usage'] == 'queried_native_cross_reference'
    assert result['assignments']['execution_correspondence'] == 'unverified'
    uncompiled = _VisibleDriver(after)
    rejected = NativeEngineeringSession(uncompiled, 'FX3U').run([{'operation': 'show_assignments'}])
    assert not rejected['passed'] and not uncompiled.calls


@pytest.mark.parametrize('damage', ['missing', 'changed_address'])
def test_visible_cold_reopen_rechecks_the_same_verified_compiler_mapping(monkeypatch, damage):
    import copy
    import application.native_engineering as session_module
    from gxw.visible_engineering import saved_assignments
    raw = _visible_cross_reference_witness()
    driver = _VisibleDriver(raw)
    reopened = False
    original_reopen = driver.reopen
    def reopen():
        nonlocal reopened
        reopened = True
        return original_reopen()
    driver.reopen = reopen
    def assignments(data):
        result = saved_assignments(data)
        if reopened:
            if damage == 'missing':
                return {'status': 'unavailable', 'rows': []}
            result = copy.deepcopy(result)
            next(row for row in result['rows'] if row['name'] == 'Result')['address'] = 'D2998'
        return result
    monkeypatch.setattr(session_module, 'saved_assignments', assignments)
    result = session_module.NativeEngineeringSession(driver, 'FX3U').run([
        {'operation': 'compile'}, {'operation': 'show_assignments',
            'queries': [{'scope': 'global', 'source': 'Global1.gh', 'name': 'Result'}]},
        {'operation': 'save'}, {'operation': 'reopen'}])
    assert not result['passed'] and result['status'] == 'interrupted'
    assert result['assignments']['status'] == 'unverified'
    assert 'compiler address mapping' in result['error']['message']
    assert result['reopen']['status'] == 'not_run'


@pytest.mark.parametrize('queries', [None, [], [{}],
    [{'scope': 'local', 'source': 'POU_01', 'name': 'Result'}],
    [{'scope': 'global', 'source': 'Global1.gh', 'name': 'Result'}] * 2,
    [{'scope': 'global', 'source': 'Global1.gh', 'name': 'Result'}] * 33])
def test_visible_cross_reference_display_requires_bounded_explicit_unique_global_queries(queries):
    from gxw.models import GXWFormatError
    from gxw.visible_engineering import prepare_assignment_cross_references
    with pytest.raises(GXWFormatError):
        prepare_assignment_cross_references(_visible_cross_reference_witness(), queries)


def test_visible_cross_reference_query_uses_the_global_declaration_and_one_compiler_candidate():
    import copy
    from gxw.models import GXWFormatError
    from gxw.visible_engineering import prepare_assignment_cross_references, saved_assignments
    raw = _visible_cross_reference_witness()
    queries = [{'scope': 'global', 'source': 'Global1.gh', 'name': 'Result'}]
    assignments = saved_assignments(raw)
    chosen = prepare_assignment_cross_references(raw, queries, assignments=assignments)
    assert chosen[0]['assignment']['source'] == '@GLOBALS'
    assert chosen[0]['assignment']['address'] == 'D2999'
    local = {**chosen[0]['assignment'], 'source': 'POU_01', 'address': 'D1000'}
    assignments['rows'].append(local)
    assert prepare_assignment_cross_references(raw, queries, assignments=assignments) == chosen
    assignments['rows'].append(copy.deepcopy(chosen[0]['assignment']))
    with pytest.raises(GXWFormatError, match='unique'):
        prepare_assignment_cross_references(raw, queries, assignments=assignments)


@pytest.mark.parametrize('damage', [None, 'duplicate_id', 'gap', 'reordered', 'missing_id', 'bool_id', 'foreign_address'])
def test_visible_cross_reference_keeps_distinct_native_rows_for_repeated_st_references(damage):
    from copy import deepcopy
    from gxw.models import GXWFormatError
    from gxw.visible_engineering import verify_native_cross_references
    raw = _visible_cross_reference_witness()
    query = {'scope': 'global', 'source': 'Global1.gh', 'name': 'Result'}
    record = _native_cross_reference_record(query)
    row = record['rows'][0]
    record['rows'] = [{**deepcopy(row), 'native_row': number} for number in (2, 3)]
    record['summary'] = record['summary'].replace('1件', '2件')
    if damage == 'duplicate_id':
        record['rows'][1]['native_row'] = 2
    elif damage == 'gap':
        record['rows'][1]['native_row'] = 4
    elif damage == 'reordered':
        record['rows'].reverse()
    elif damage == 'missing_id':
        del record['rows'][1]['native_row']
    elif damage == 'bool_id':
        record['rows'][0]['native_row'] = True
    elif damage == 'foreign_address':
        record['rows'][1]['address'] = 'D2998'
    if damage:
        with pytest.raises(GXWFormatError):
            verify_native_cross_references(raw, [query], {'cross_references': [record]})
    else:
        result = verify_native_cross_references(raw, [query], {'cross_references': [record]})
        assert result['status'] == 'matched'
        assert [r['native']['native_row'] for r in result['queries'][0]['rows']] == [2, 3]


@pytest.mark.parametrize('damage', [None, 'foreign_process', 'outside_view', 'hidden_view'])
def test_native_graph_drag_is_confined_to_the_bound_editor_client(damage, monkeypatch):
    from types import SimpleNamespace
    import win32process
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError
    driver = object.__new__(NativeEngineeringDriver)
    driver.session = SimpleNamespace(process_id=321)
    driver._idle = lambda: None
    driver._foreground = lambda: None
    calls = []
    view = SimpleNamespace(handle=456, is_visible=lambda: damage != 'hidden_view', is_enabled=lambda: True,
        drag_mouse_input=lambda **options: calls.append(options))
    monkeypatch.setattr(win32process, 'GetWindowThreadProcessId',
                        lambda handle: (1, 987 if damage == 'foreign_process' else 321))
    source = (2, 70) if damage == 'outside_view' else (100, 70)
    target = (180, 90)
    grid = {'width': 300, 'height': 200}
    if damage:
        with pytest.raises(NativeEngineeringDriverError):
            driver._graph_drag(view, grid, source, target)
        assert not calls
    else:
        driver._graph_drag(view, grid, source, target)
        assert calls == [{'src': source, 'dst': target, 'absolute': False}]


@pytest.mark.parametrize('damage', ['empty_results', 'duplicate_results', 'query', 'search', 'summary_count',
    'summary_name', 'empty_rows', 'duplicate_row', 'label', 'address', 'empty_address',
    'missing_pou', 'empty_location', 'invisible'])
def test_visible_cross_reference_rejects_stale_incomplete_or_wrong_native_grid_rows(damage):
    import copy
    from gxw.models import GXWFormatError
    from gxw.visible_engineering import verify_native_cross_references
    raw = _visible_cross_reference_witness()
    query = {'scope': 'global', 'source': 'Global1.gh', 'name': 'Result'}
    record = _native_cross_reference_record(query)
    observation = {'cross_references': [record]}
    if damage == 'empty_results':
        observation['cross_references'] = []
    elif damage == 'duplicate_results':
        observation['cross_references'].append(copy.deepcopy(record))
    elif damage == 'query':
        record['query']['source'] = 'Another.gh'
    elif damage == 'search':
        record['search_text'] = 'Start'
    elif damage == 'summary_count':
        record['summary'] = record['summary'].replace('1件', '2件')
    elif damage == 'summary_name':
        record['summary'] = record['summary'].replace('Result', 'Start')
    elif damage == 'empty_rows':
        record['rows'] = []
    elif damage == 'duplicate_row':
        record['rows'].append(copy.deepcopy(record['rows'][0]))
        record['summary'] = record['summary'].replace('1件', '2件')
    elif damage == 'label':
        record['rows'][0]['label'] = 'Start'
    elif damage == 'address':
        record['rows'][0]['address'] = 'D2998'
    elif damage == 'empty_address':
        record['rows'][0]['address'] = ''
    elif damage == 'missing_pou':
        record['rows'][0]['pou'] = 'NotInCurrentSource'
    elif damage == 'empty_location':
        record['rows'][0]['location'] = ''
    else:
        record['visible_completion']['completed'] = False
    with pytest.raises(GXWFormatError):
        verify_native_cross_references(raw, [query], observation)


@pytest.mark.parametrize('queries', [None, []])
def test_visible_session_rejects_cross_reference_without_explicit_queries_before_showing(queries):
    from application.native_engineering import NativeEngineeringSession
    driver = _VisibleDriver(_visible_cross_reference_witness())
    command = {'operation': 'show_assignments'}
    if queries is not None:
        command['queries'] = queries
    result = NativeEngineeringSession(driver, 'FX3U').run([{'operation': 'compile'}, command])
    assert not result['passed']
    assert [item['operation'] for item, _ in driver.calls] == ['compile']
    assert result['compile']['status'] == 'passed'
    assert result['assignments']['status'] == 'not_run'


def test_visible_session_does_not_upgrade_allocations_when_native_cross_reference_is_wrong():
    from application.native_engineering import NativeEngineeringSession
    driver = _VisibleDriver(_visible_cross_reference_witness())
    execute = driver.execute
    def wrong_cross_reference(command, prepared):
        result = execute(command, prepared)
        if command['operation'] == 'show_assignments':
            result['cross_references'][0]['rows'][0]['address'] = 'D2998'
        return result
    driver.execute = wrong_cross_reference
    query = {'scope': 'global', 'source': 'Global1.gh', 'name': 'Result'}
    result = NativeEngineeringSession(driver, 'FX3U').run([
        {'operation': 'compile'}, {'operation': 'show_assignments', 'queries': [query]}, {'operation': 'save'}])
    assert not result['passed'] and result['compile']['status'] == 'passed'
    assert result['assignments']['status'] == 'not_run'
    assert len(driver.calls) == 2
    assert result['data_status'] == 'last_verified_source'


def test_visible_compile_failure_retains_diagnostics_and_never_executes_next_step():
    from application.native_engineering import NativeEngineeringSession
    from gxw.fb_authoring import _context
    before = _visible_cross_reference_witness()
    # A harmless GUI-file newline makes the observed saved bytes distinct;
    # only the receipt's native error status is under test in this mock run.
    gui = _context(before)[1]['Gppw2.gpj'].decode('utf-16le')
    after = _replace_visible_logical_stream(before, 'Gppw2.gpj', (gui.rstrip('\0') + '\n\0').encode('utf-16le'))
    driver = _VisibleDriver(before, [(before, after)], receipt={
        'visible_completion': {'completed': True, 'page': 'compile output'},
        'command': 'rebuild_all', 'completed': True, 'saved': True, 'source_unchanged': True,
        'pid': 701, 'revision': 0, 'output': '已完成全部编译。Error: 2, Warning: 0, CheckWarning: 0',
        'diagnostics': [{'code': 'C9172', 'message': 'Native diagnostic retained'}]})
    result = NativeEngineeringSession(driver, 'FX3U').run([{'operation': 'compile'}, {'operation': 'save'}])
    assert not result['passed'] and result['compile']['status'] == 'failed'
    assert result['steps'][0]['observation']['diagnostics'][0]['code'] == 'C9172'
    assert len(driver.calls) == 1
    assert result['data'] == after and result['data_status'] == 'last_verified_source'
    assert 'unverified_data' not in result


def test_visible_source_edit_invalidates_the_previous_gui_compile_and_assignments():
    from application.native_engineering import NativeEngineeringSession
    from gxw.fb_authoring import _context, definition_snapshot
    from gxw.text_pou import parse_st_pou, serialize_st_pou
    before = _visible_cross_reference_witness()
    definition, = definition_snapshot(before, 'ScaleValue')['definitions']
    command = {'operation': 'edit_st_fb', 'source_name': 'ScaleValue',
               'st_text': definition['st_text'] + '(* source changed *)\r\n'}
    source = _context(before)[1][definition['program']]
    after = _replace_visible_logical_stream(before, definition['program'], serialize_st_pou(parse_st_pou(source), command['st_text']))
    driver = _VisibleDriver(before)
    original = driver.execute

    def execute(command, prepared):
        if command['operation'] == 'edit_st_fb':
            driver.raw = after
        return original(command, prepared)

    driver.execute = execute
    result = NativeEngineeringSession(driver, 'FX3U').run([
        {'operation': 'compile'}, command, {'operation': 'show_assignments'}, {'operation': 'save'}])
    assert result['status'] == 'interrupted' and result['compile']['status'] == 'stale'
    assert [command['operation'] for command, _ in driver.calls] == ['compile', 'edit_st_fb']
    assert result['assignments']['status'] == 'not_run'


@pytest.fixture(scope='module')
def multiple_revision_native_result():
    """Real Core source/readback checks over a controlled native driver replay."""
    from application.native_engineering import NativeEngineeringSession, normalize_native_plan
    from gxw.fb_authoring import _context, definition_snapshot
    from gxw.text_pou import parse_st_pou, serialize_st_pou

    before = _visible_cross_reference_witness()
    definition, = definition_snapshot(before, 'ScaleValue')['definitions']
    source = parse_st_pou(_context(before)[1][definition['program']])
    commands, outputs = [], []
    query = {'scope': 'global', 'source': 'Global1.gh', 'name': 'Result'}
    for number in (1, 2):
        text = definition['st_text'] + f'(* source revision {number} *)\r\n'
        commands.extend([
            {'operation': 'edit_st_fb', 'source_name': 'ScaleValue', 'st_text': text},
            {'operation': 'compile'}, {'operation': 'show_assignments', 'queries': [dict(query)]}])
        outputs.append(_replace_visible_logical_stream(before, definition['program'], serialize_st_pou(source, text)))
    commands.append({'operation': 'save'})
    plan = normalize_native_plan({'schema_version': 1, 'commands': commands}, plc_model='FX3U')
    driver = _VisibleDriver(before)
    original = driver.execute

    def execute(command, prepared):
        if command['operation'] == 'edit_st_fb':
            driver.raw = outputs.pop(0)
        return original(command, prepared)

    driver.execute = execute
    result = NativeEngineeringSession(driver, 'FX3U').run(plan['commands'], baseline=before)
    assert result['passed'], result.get('error')
    return result, plan


def test_visible_multiround_compile_queries_keep_each_observed_revision(multiple_revision_native_result):
    from application.native_engineering import verified_native_data
    result, plan = multiple_revision_native_result
    assert [step['revision'] for step in result['steps'] if step['operation'] == 'show_assignments'] == [1, 2]
    assert [step['observation']['revision'] for step in result['steps'] if step['operation'] == 'compile'] == [1, 2]
    assert result['revision'] == result['assignments']['revision'] == result['save']['revision'] == 2
    assert verified_native_data(result, plan) == result['data']


@pytest.mark.parametrize('damage', ['historical_query', 'historical_revision', 'historical_unmatched',
    'historical_compile_revision', 'missing_historical_compile', 'final_layer_revision',
    'final_query_revision', 'final_query_result', 'incomplete_step', 'observed_data'])
def test_visible_multiround_delivery_rejects_wrong_history_or_stale_final_layers(multiple_revision_native_result, damage):
    import copy
    from application.native_engineering import verified_native_data
    result, plan = copy.deepcopy(multiple_revision_native_result)
    if damage == 'historical_query':
        result['steps'][2]['verification']['native_cross_references']['queries'][0]['query']['name'] = 'Foreign'
    elif damage == 'historical_revision':
        result['steps'][2]['revision'] = 2
    elif damage == 'historical_unmatched':
        result['steps'][2]['verification']['native_cross_references']['status'] = 'unverified'
    elif damage == 'historical_compile_revision':
        result['steps'][1]['revision'] = 0
    elif damage == 'missing_historical_compile':
        del result['steps'][1]
        del plan['commands'][1]
    elif damage == 'final_layer_revision':
        result['assignments']['revision'] = 1
    elif damage == 'final_query_revision':
        result['steps'][5]['revision'] = 1
    elif damage == 'final_query_result':
        result['assignments']['native_cross_references']['queries'][0]['query']['name'] = 'Foreign'
    elif damage == 'incomplete_step':
        result['steps'][0]['status'] = 'interrupted'
    else:
        result['data_status'] = 'observed_source_not_verified'
    with pytest.raises(ValueError):
        verified_native_data(result, plan)


def test_native_plan_freezes_generated_compile_query_rounds_without_resolving_future_objects():
    from hypothesis import given, settings, strategies as st
    from application.native_engineering import normalize_native_plan

    @settings(max_examples=24, deadline=None, derandomize=True)
    @given(rounds=st.integers(1, 8), final=st.sampled_from([(), ('save',), ('save', 'reopen')]))
    def check(rounds, final):
        commands = []
        for number in range(rounds):
            commands.extend([
                {'operation': 'edit_fbd_graph', 'caller': f'Future{number}.Program.pou',
                 'actions': [{'action': 'rebind_terminal', 'node': f'n{number}', 'symbol': f'NewSymbol{number}'}]},
                {'operation': 'compile'}, {'operation': 'show_assignments', 'queries': [
                    {'scope': 'global', 'source': f'Table{number}.gh', 'name': f'NewSymbol{number}'}]}])
        commands.extend({'operation': operation} for operation in final)
        plan = {'schema_version': 1, 'commands': commands}
        frozen = normalize_native_plan(plan, plc_model='FX3U')
        assert frozen == plan and frozen is not plan
        frozen['commands'][0]['actions'][0]['symbol'] = 'ChangedAfterFreeze'
        assert plan['commands'][0]['actions'][0]['symbol'] == 'NewSymbol0'
    check()


@pytest.mark.parametrize('missing_compile,missing_final_layer', [(True, None), (False, 'show_assignments'),
    (False, 'compile'), (False, 'save'), (False, 'reopen')])
def test_native_plan_keeps_current_compile_and_final_layer_requirements(missing_compile, missing_final_layer):
    from application.native_engineering import normalize_native_plan
    edit = {'operation': 'edit_fbd_graph', 'caller': 'Future.Program.pou',
            'actions': [{'action': 'delete_terminal', 'node': 'n1'}]}
    show = {'operation': 'show_assignments', 'queries': [
        {'scope': 'global', 'source': 'Global1.gh', 'name': 'Result'}]}
    if missing_compile:
        commands = [{'operation': 'compile'}, show, edit, show]
    else:
        commands = [edit, {'operation': 'compile'}, show, {'operation': 'save'}, {'operation': 'reopen'}, edit]
        commands.extend({'operation': op} if op != 'show_assignments' else show
                        for op in ('compile', 'show_assignments', 'save', 'reopen') if op != missing_final_layer)
    with pytest.raises(ValueError):
        normalize_native_plan({'schema_version': 1, 'commands': commands}, plc_model='FX3U')


@pytest.mark.parametrize('operation', [None, [], {}, True, 'unverified_operation'])
def test_native_plan_rejects_nonstring_or_unknown_operations_before_execution(operation):
    from application.native_engineering import normalize_native_plan
    with pytest.raises(ValueError, match='operation'):
        normalize_native_plan({'schema_version': 1, 'commands': [{'operation': operation}]}, plc_model='FX3U')


@pytest.mark.parametrize('actions', [
    [{'action': 'move_nod', 'node': 'n1', 'x': 1, 'y': 1}],
    [{'action': 'delete_terminal'}], [{'action': 'delete_wire', 'wire': 'w1', 'extra': 1}],
    [{'action': 'rebind_terminal', 'node': 'n1', 'symbol': '\0'}],
    [{'action': 'move_node', 'node': 'n1', 'x': True, 'y': 1}],
    [{'action': 'add_wire', 'id': 'path', 'from': {'node': 'n1', 'port': 0},
      'to': {'node': 'n2', 'port': 'in'}, 'via': []}],
    [{'action': 'add_wire', 'id': 'path', 'from': {'node': 'n1', 'port': 'out'},
      'to': {'wire': 'w1', 'point': [1, True]}, 'via': []}],
    [{'action': 'add_wire', 'id': 'path', 'from': {'node': 'n1', 'port': 'out'},
      'to': {'node': 'n2', 'port': 'in'}, 'via': [[1, 1], [2, 2]]}],
    [{'action': 'add_wire', 'id': 'path', 'from': {'node': 'n1', 'port': 'out'},
      'to': {'node': 'n2', 'port': 'in'}, 'via': [[1, 1], [1, 1]]}],
    [{'action': 'add_wire', 'id': 'path', 'from': {'node': 'n1', 'port': 'out'},
      'to': {'node': 'n2', 'port': 'in'}, 'via': [[1, 4097]]}],
])
def test_native_graph_syntax_is_checked_before_source_reads_and_earlier_effects(monkeypatch, actions):
    import gxw.visible_graph as graph
    from application.native_engineering import normalize_native_plan
    from gxw.models import GXWFormatError

    def unexpected_source_read(*args, **kwargs):
        pytest.fail('A malformed graph action reached source-dependent planning')

    monkeypatch.setattr(graph, 'read_project_context', unexpected_source_read)
    command = {'operation': 'edit_fbd_graph', 'caller': 'Future.Program.pou', 'actions': actions}
    with pytest.raises(GXWFormatError):
        graph.prepare_graph_edit(b'not read', command)
    with pytest.raises(ValueError):
        normalize_native_plan({'schema_version': 1, 'commands': [
            {'operation': 'create_st_fb', 'source_name': 'Future', 'declarations': [], 'st_text': ';'}, command]},
            plc_model='FX3U')


def test_native_graph_move_syntax_bounds_are_independent_of_unresolved_node_geometry():
    from hypothesis import given, settings, strategies as st
    from application.native_engineering import normalize_native_plan

    @settings(max_examples=30, deadline=None, derandomize=True)
    @given(x=st.integers(0, 4096), y=st.integers(0, 4096),
           invalid=st.one_of(st.integers(max_value=-1), st.integers(min_value=4097),
                             st.booleans(), st.none(), st.floats(), st.text()))
    def check(x, y, invalid):
        command = {'operation': 'edit_fbd_graph', 'caller': 'Future.Program.pou',
                   'actions': [{'action': 'move_node', 'node': 'FutureNode', 'x': x, 'y': y}]}
        plan = {'schema_version': 1, 'commands': [command]}
        assert normalize_native_plan(plan, plc_model='FX3U') == plan
        command['actions'][0]['x'] = invalid
        with pytest.raises(ValueError):
            normalize_native_plan(plan, plc_model='FX3U')
    check()


def test_visible_baseline_mismatch_never_returns_an_unverified_final_artifact():
    from application.native_engineering import NativeEngineeringSession
    from tests.test_gxw_object_model import authored_fb_witness
    before, after, _, _ = authored_fb_witness('v0005')
    driver = _VisibleDriver(after)
    result = NativeEngineeringSession(driver, 'FX3U').run([{'operation': 'save'}], baseline=before)
    assert result['status'] == 'interrupted' and not driver.calls
    assert 'data' not in result and 'data_status' not in result
    assert result['unverified_data'] == after
    assert result['unverified_data_status'] == 'observed_source_not_verified'


@pytest.mark.parametrize('stage', ['before_step', 'authoring_readback', 'snapshot_parse', 'cold_reopen'])
def test_visible_failed_source_validation_keeps_the_last_verified_source_separate(stage):
    from application.native_engineering import NativeEngineeringSession
    from tests.test_gxw_object_model import authored_fb_witness
    before, after, command, _ = authored_fb_witness('v0005')
    _, wrong_body, _, _ = authored_fb_witness('v0006')
    driver = _VisibleDriver(before)
    commands = [{'operation': 'save'}]
    unverified = after
    if stage == 'before_step':
        reads = []
        def snapshot():
            reads.append(True)
            if len(reads) == 2:
                driver.raw = after
            return driver.raw
        driver.snapshot = snapshot
    elif stage == 'authoring_readback':
        driver.transitions = [(before, wrong_body)]
        commands = [command]
        unverified = wrong_body
    elif stage == 'snapshot_parse':
        unverified = b'not a native GXW document'
        driver.transitions = [(before, unverified)]
    else:
        def reopen():
            driver.raw = after
            return after
        driver.reopen = reopen
        commands.append({'operation': 'reopen'})
    result = NativeEngineeringSession(driver, 'FX3U').run(commands, baseline=before)
    assert result['status'] == 'interrupted' and not result['passed']
    assert result['data'] == before and result['data_status'] == 'last_verified_source'
    assert result['unverified_data'] == unverified
    assert result['unverified_data_status'] == 'observed_source_not_verified'
    assert driver.closed


def _replace_visible_logical_stream(raw, logical, replacement):
    from gxw.container_writer import replace_project_stream, validate_cfb_streams
    from gxw.project_metadata import logical_mapping
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested, _ = replace_project_stream(outer['_hdb'], mapping[logical], replacement)
    saved, _ = replace_project_stream(raw, '_hdb', nested)
    return saved


@pytest.mark.parametrize('damage', ['range', 'selector', 'retained_inactive_range'])
def test_visible_configuration_requires_the_saved_gui_allocation_mirror(damage):
    from application.native_engineering import NativeEngineeringSession
    from gxw.fb_authoring import _context
    from gxw.project_metadata import write_gui_system_variables
    from gxw.visible_engineering import verify_authoring_readback
    before = after = _visible_cross_reference_witness()
    metadata, = [name for name in _context(before)[1] if name.endswith('.prj')]
    command = {'operation': 'configure_project', 'source_name': metadata,
               'options': {'system_variable_ranges': {'D': [2000, 2999]}}}
    gui = _context(after)[1]['Gppw2.gpj']
    if damage == 'range':
        changed = write_gui_system_variables(gui, {'D': (2001, 7999)})
    elif damage == 'retained_inactive_range':
        changed = write_gui_system_variables(gui, {'R': (513, 7999)})
    else:
        old = '\t\tWordDevice\tTYPE_WORD\t0'
        text = gui.decode('utf-16le')
        assert old in text
        changed = text.replace(old, '\t\tWordDevice\tTYPE_WORD\t1').encode('utf-16le')
    damaged = _replace_visible_logical_stream(after, 'Gppw2.gpj', changed)
    # The existing authoring helper checks .prj alone. The controller must also
    # bind the independent GUI record that the next native compile will load.
    assert verify_authoring_readback(before, command, damaged)['status'] == 'matched'
    driver = _VisibleDriver(before, [(before, damaged)])
    result = NativeEngineeringSession(driver, 'FX3U').run([command])
    assert not result['passed'] and 'Gppw2.gpj' in result['error']['message']
    assert result['data'] == before and result['unverified_data'] == damaged


def test_visible_snapshot_projection_keeps_resource_task_settings_in_the_same_context():
    from application.native_engineering import NativeEngineeringSession, _project_projection
    from gxw.fb_authoring import _context
    from gxw.project_metadata import read_task_source
    from gxw.visible_engineering import source_projection
    from tests.test_gxw_object_model import motion_compilation_witness
    _, before, _ = motion_compilation_witness()
    streams = _context(before)[1]
    logical = next(name for name in streams if name.endswith('.tsk'))
    task = bytearray(streams[logical])
    task[read_task_source(task)['count_offset'] - 8] ^= 1
    damaged = _replace_visible_logical_stream(before, logical, bytes(task))
    assert source_projection(before) == source_projection(damaged)
    context = _project_projection(before)
    assert context['task_scopes']['resources'] and context['task_scopes']['tasks']
    assert context['task_scopes'] != _project_projection(damaged)['task_scopes']
    driver = _VisibleDriver(before, [(before, damaged)])
    result = NativeEngineeringSession(driver, 'FX3U').run([{'operation': 'save'}])
    assert not result['passed']
    assert result['data'] == before and result['unverified_data'] == damaged


def test_visible_fx_gui_allocation_uses_the_observed_100ms_timer_and_selected_devices():
    from gxw.fb_authoring import _context
    from gxw.project_metadata import project_gui_configuration, read_gui_system_variables
    raw = _visible_cross_reference_witness()
    gui = _context(raw)[1]['Gppw2.gpj']
    stored = read_gui_system_variables(gui)
    effective = read_gui_system_variables(gui, cpu='FX3U/FX3UC')
    assert stored['ranges']['T'] == (0xffffffff, 0xffffffff)
    assert stored['stored_ranges']['100T'] == (100, 199)
    assert effective['ranges']['T'] == (100, 199)
    assert effective['stored_ranges']['R'] == (512, 7999)
    assert effective['ranges']['R'] == effective['ranges']['W'] == (0xffffffff, 0xffffffff)
    assert effective['ranges']['M'] == (3840, 7679)
    assert effective['retained_fields']['100TMin'] == {'storage_type': 'DWORD', 'value': 100}
    current = project_gui_configuration(raw)
    assert current['status'] == 'matched'
    assert current['effective_ranges']['D'] == [2000, 2999]
    assert current['stored_ranges']['R'] == [512, 7999]


def test_visible_fx_gui_allocation_requires_100t_and_does_not_use_generic_timer_fallback():
    from gxw.fb_authoring import _context
    from gxw.project_metadata import project_gui_configuration
    raw = _visible_cross_reference_witness()
    text = _context(raw)[1]['Gppw2.gpj'].decode('utf-16le')
    changed = '\n'.join(line for line in text.split('\n') if not line.startswith(('\t\t100TMin\t', '\t\t100TMax\t')))
    damaged = _replace_visible_logical_stream(raw, 'Gppw2.gpj', changed.encode('utf-16le'))
    result = project_gui_configuration(damaged)
    assert result['status'] == 'missing' and 'T' in result['missing']
    assert 'T' not in result['effective_ranges']
    assert result['stored_ranges']['T'] == [0xffffffff, 0xffffffff]


def test_visible_fx_gui_unknown_selection_does_not_infer_a_device_profile():
    from gxw.fb_authoring import _context
    from gxw.project_metadata import project_gui_configuration
    raw = _visible_cross_reference_witness()
    text = _context(raw)[1]['Gppw2.gpj'].decode('utf-16le')
    changed = text.replace('\t\tWordDevice\tTYPE_WORD\t0', '\t\tWordDevice\tTYPE_WORD\t1')
    result = project_gui_configuration(_replace_visible_logical_stream(raw, 'Gppw2.gpj', changed.encode('utf-16le')))
    assert result['status'] in ('missing', 'not_verified')
    assert result['unverified_selectors'] == ['WordDevice']
    assert 'D' not in result['effective_ranges'] and 'R' not in result['effective_ranges']


def test_visible_inactive_gui_backup_changes_are_detected_during_plain_save():
    from application.native_engineering import NativeEngineeringSession
    from gxw.fb_authoring import _context
    from gxw.project_metadata import project_gui_configuration, write_gui_system_variables
    raw = _visible_cross_reference_witness()
    gui = _context(raw)[1]['Gppw2.gpj']
    damaged = _replace_visible_logical_stream(raw, 'Gppw2.gpj', write_gui_system_variables(gui, {'R': (513, 7999)}))
    before, after = project_gui_configuration(raw), project_gui_configuration(damaged)
    assert before['status'] == after['status'] == 'matched'
    assert before['effective_ranges'] == after['effective_ranges']
    assert before['retained_fields'] != after['retained_fields']
    driver = _VisibleDriver(raw, [(raw, damaged)])
    result = NativeEngineeringSession(driver, 'FX3U').run([{'operation': 'save'}])
    assert not result['passed'] and result['data'] == raw and result['unverified_data'] == damaged


@pytest.mark.parametrize('damage', [None, 'swap_inputs', 'wrong_output', 'disconnect_input'])
def test_visible_fbd_call_verifies_actual_formal_connectivity_in_frozen_native_geometry(damage):
    import copy
    import zipfile
    from tools.offline_inputs import private_path
    from gxw.models import GXWFormatError
    from gxw.object_model import generate_object_project, read_project_context
    from gxw.visible_engineering import verify_fbd_call
    with zipfile.ZipFile(private_path('research/' + 'evidence/gxw-fb-authoring-mcp-20261015.zip')) as archive:
        after = archive.read('versions/v0007/program.gxw')
    context = read_project_context(after, 'FBD_MATRIX.Program.pou')
    model = context.object_model()
    before_model = copy.deepcopy(model)
    # The expected Input/Delta/Value nets come from the untouched native sample.
    # Remove only the second call to construct the pre-insertion source.
    before_model['nodes'] = [node for node in before_model['nodes'] if node['y'] < 10]
    before_model['wires'] = [wire for wire in before_model['wires'] if wire['start'][1] < 10]
    before = generate_object_project(before_model, baseline=after).data
    command = {'operation': 'create_fbd_call', 'caller': model['program'],
        'instance': 'Offset_Main', 'definition': 'WordOffset',
        'arguments': {'Input': 'D10', 'Delta': 'D11', 'Value': 'D12'}}
    if damage == 'swap_inputs':
        command['arguments'].update(Input='D11', Delta='D10')
    elif damage == 'wrong_output':
        command['arguments']['Value'] = 'D11'
    elif damage == 'disconnect_input':
        # Keep exactly the same nodes and terminals; alter only a real net.
        model['wires'] = [wire for wire in model['wires'] if wire['start'] != [2, 13]]
        after = generate_object_project(model, baseline=after).data
    if damage is None:
        checked = verify_fbd_call(before, command, after, {'caller': model['program']})
        assert [(row['formal'], row['symbol']) for row in checked['connections']] == [
            ('Input', 'D10'), ('Delta', 'D11'), ('Value', 'D12')]
    else:
        with pytest.raises(GXWFormatError, match='not connected'):
            verify_fbd_call(before, command, after, {'caller': model['program']})


def _visible_acceptance_sample():
    import runpy
    from tools.offline_inputs import private_path

    script = private_path('research/validate_visible_engineering_session.py')
    saved = private_path('research/experiments/visible-session-20261017/acceptance-05/last-verified.gxw')
    if not script.is_file() or not saved.is_file():
        pytest.skip('private visible acceptance script/native saved sample is unavailable')
    return runpy.run_path(str(script)), saved.read_bytes()


def test_visible_acceptance_sample_query_uses_the_real_global_declaration_logical_source():
    from gxw.models import GXWFormatError
    from gxw.native_diagnostics import native_inspection_queries
    from gxw.visible_engineering import prepare_assignment_cross_references

    sample, raw = _visible_acceptance_sample()
    plan = sample['sample_plan'](raw)
    command, = [row for row in plan['commands'] if row['operation'] == 'show_assignments']
    assert plan['cpu'] == 'FX3U'
    assert command['queries'] == [{'source': 'Global1.gh', 'name': 'Result', 'scope': 'global'}]
    checked = native_inspection_queries(raw, {'assignments': command['queries'], 'locations': [], 'saved_code': False})
    assert checked['assignments'] == command['queries']
    selected, = prepare_assignment_cross_references(raw, command['queries'])
    assert selected['assignment']['source'] == '@GLOBALS'
    assert selected['assignment']['address'] == 'D7999'
    with pytest.raises(GXWFormatError, match='global declaration owner'):
        prepare_assignment_cross_references(raw, [{'source': 'Global1', 'name': 'Result', 'scope': 'global'}])


@pytest.mark.parametrize('damage', ['missing_query_evidence', 'source_alias', 'wrong_address', 'wrong_saved_rows',
                                   'stale_revision', 'missing_step_verification'])
def test_visible_acceptance_rechecks_complete_native_query_evidence_before_passing(tmp_path, damage):
    import copy
    from gxw.visible_engineering import saved_assignments, verify_native_cross_references

    sample, raw = _visible_acceptance_sample()
    path = tmp_path / 'saved-native-sample.gxw'
    path.write_bytes(raw)
    query = {'source': 'Global1.gh', 'name': 'Result', 'scope': 'global'}
    # This in-memory synthetic receipt exercises the acceptance verifier. It is
    # never saved as native evidence and does not represent a desktop execution.
    observed = {'cross_references': [_native_cross_reference_record(query, address='D7999')]}
    native = verify_native_cross_references(raw, [query], observed)
    steps = [{'index': i, 'operation': operation, 'status': 'completed',
              'verification': {'status': 'matched'}} for i, operation in enumerate(sample['OPERATIONS'])]
    steps[3]['verification']['caller'] = 'POU_01.Program.pou'
    steps[6].update(observation=observed, verification={'status': 'matched', 'native_cross_references': copy.deepcopy(native)})
    receipt = {'status': 'completed', 'passed': True, 'revision': 5, 'steps': steps,
        'compile': {'status': 'passed', 'revision': 5},
        'assignments': {**saved_assignments(raw), 'status': 'current', 'revision': 5,
            'origin': 'current_gui_compile_saved_snapshot', 'native_cross_references': copy.deepcopy(native)},
        'save': {'status': 'passed', 'revision': 5}, 'reopen': {'status': 'passed', 'revision': 5},
        'snapshot': {'path': str(path)}, 'execution': 'not_run'}
    assert sample['validate_receipt'](receipt)['passed']
    damaged = copy.deepcopy(receipt)
    if damage == 'missing_query_evidence':
        damaged['assignments']['native_cross_references']['queries'] = []
    elif damage == 'source_alias':
        damaged['steps'][6]['observation']['cross_references'][0]['query']['source'] = 'Global1'
    elif damage == 'wrong_address':
        damaged['steps'][6]['observation']['cross_references'][0]['rows'][0]['address'] = 'D7998'
    elif damage == 'stale_revision':
        damaged['assignments']['revision'] = 4
    elif damage == 'wrong_saved_rows':
        next(row for row in damaged['assignments']['rows'] if row['name'] == 'Result')['address'] = 'D7998'
    else:
        del damaged['steps'][6]['verification']['native_cross_references']
    result = sample['validate_receipt'](damaged)
    assert not result['passed'] and result['failures']


def _visible_structure_witness():
    import json
    from tools.offline_inputs import private_path

    directory = private_path('research/experiments/visible-session-20261017')
    paths = [directory / name for name in ('structure-probe/before.gxw', 'structure-probe/after.gxw',
        'structure-probe/command.json', 'structure-defaults/final.gxw', 'structure-defaults/receipt.json')]
    if any(not path.is_file() for path in paths):
        pytest.skip('private native structure before/after witnesses are unavailable')
    return (paths[0].read_bytes(), paths[1].read_bytes(), json.loads(paths[2].read_text(encoding='utf-8')),
            paths[3].read_bytes(), json.loads(paths[4].read_text(encoding='utf-8')))


def test_visible_structure_default_members_match_native_save_and_cold_reopen_witness():
    from application.native_engineering import NativeEngineeringSession
    from gxw.fb_authoring import definition_snapshot
    from gxw.visible_engineering import prepare_authoring, verify_authoring_readback

    _, before, _, after, recorded = _visible_structure_witness()
    command = {'operation': 'create_structure', 'source_name': 'DemoDefaults', 'members': [
        {'name': 'Factor', 'data_type': 'INT', 'comment': 'Multiplier'},
        {'name': 'Ready', 'data_type': 'BOOL', 'comment': 'Readiness'}]}
    expected = [{'name': 'Factor', 'data_type': 'INT', 'initial_value': '0', 'comment': 'Multiplier'},
                {'name': 'Ready', 'data_type': 'BOOL', 'initial_value': 'FALSE', 'comment': 'Readiness'}]
    assert not [row for row in definition_snapshot(before)['definitions'] if row['name'] == 'DemoDefaults']
    prepared = prepare_authoring(before, command)
    assert [(row['initial_value'], row['csv_constant']) for row in prepared['plan']['rows']] == [('0', ''), ('FALSE', '')]
    checked = verify_authoring_readback(before, command, after, prepared)
    assert checked['definition']['members'] == expected
    assert definition_snapshot(before, 'DemoConfig')['definitions'] == definition_snapshot(after, 'DemoConfig')['definitions']
    driver = _VisibleDriver(before, [(before, after)])
    result = NativeEngineeringSession(driver, 'FX3U').run([command, {'operation': 'save'}, {'operation': 'reopen'}], baseline=before)
    assert result['passed'], result.get('error')
    assert result['data'] == after
    assert result['save']['status'] == result['reopen']['status'] == 'passed'
    assert result['compile']['status'] == recorded['compile']['status'] == 'not_run'
    assert recorded['identity']['process_id'] == 203064
    assert recorded['identity']['software_version'] == '1.635.0.1'
    assert recorded['identity']['cpu_menu'] == 'FX3U/FX3UC'
    assert [(step['operation'], step['status']) for step in recorded['steps']] == [
        ('create_structure', 'completed'), ('save', 'completed'), ('reopen', 'completed')]


@pytest.mark.parametrize('member,value', [('Factor', '3'), ('Ready', 'TRUE')])
def test_visible_structure_nondefault_initializer_is_rejected_before_native_edit(member, value):
    from gxw.models import GXWFormatError
    from gxw.visible_engineering import prepare_authoring

    before, _, _, _, _ = _visible_structure_witness()
    command = {'operation': 'create_structure', 'source_name': 'InitializerRejected', 'members': [
        {'name': 'Factor', 'data_type': 'INT', 'initial_value': '0', 'comment': 'Multiplier'},
        {'name': 'Ready', 'data_type': 'BOOL', 'initial_value': 'FALSE', 'comment': 'Readiness'}]}
    next(row for row in command['members'] if row['name'] == member)['initial_value'] = value
    with pytest.raises(GXWFormatError, match='nondefault'):
        prepare_authoring(before, command)


def test_visible_structure_original_native_csv_ignored_nondefault_initializer_is_not_accepted_as_source_proof():
    import csv
    import io
    from gxw.fb_authoring import authoring_plan, definition_snapshot
    from gxw.models import GXWFormatError
    from gxw.visible_engineering import verify_authoring_readback
    from tools.offline_inputs import private_path

    before, after, command, _, _ = _visible_structure_witness()
    csv_path = private_path('research/experiments/visible-session-20261017/structure-probe/labels-001/change.csv')
    rows = list(csv.reader(io.StringIO(csv_path.read_bytes().decode('utf-16'), newline=''), delimiter='\t'))
    assert rows[1] == ['标签名', '数据类型', '常量', '注释']
    assert [(row[0], row[2]) for row in rows[2:]] == [('Factor', '3'), ('Ready', 'TRUE')]
    actual, = definition_snapshot(after, 'DemoConfig')['definitions']
    assert [(row['name'], row['initial_value']) for row in command['members']] == [('Factor', '3'), ('Ready', 'TRUE')]
    assert [(row['name'], row['initial_value']) for row in actual['members']] == [('Factor', '0'), ('Ready', 'FALSE')]
    # Retain the original request as the independent expectation, bypassing
    # only the newly added preflight guard to replay this historical failure.
    plan, previous, expected, caller = authoring_plan(before, command)
    with pytest.raises(GXWFormatError, match='structure members differ'):
        verify_authoring_readback(before, command, after,
            {'kind': 'authoring', 'plan': plan, 'before': previous, 'expected': expected, 'caller': caller})


def _visible_native_path_driver(monkeypatch, path, caption, *, owner=(701, '123456'), unavailable=None):
    from types import SimpleNamespace
    from gxworks2 import engineering_driver as native
    from gxworks2.project_profiles import native_project_profile
    from gxw.project_templates import blank_fbd_baseline

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blank_fbd_baseline('FX3U'))

    session = SimpleNamespace(process_id=701, window_handle=9001, process_started='123456',
        executable=r'D:\GXWORKS2\GPPW2\GD2.exe', software_version='1.635.0.1',
        project_name=caption, project_open=True)
    state = SimpleNamespace(registrations=[], queries=[], profile_reads=[], closed=0, finder_calls=[],
        project_language=dict(status='observed', codepage_candidates=[936,54936], status_text='简体中文'))

    class FileUse:
        def __init__(self, registered):
            state.registrations.append(registered)
            if unavailable == 'register':
                raise native.NativeEngineeringDriverError('file owner unavailable during registration')

        def owns(self, process_id, process_started):
            state.queries.append((process_id, process_started))
            if unavailable == 'query':
                raise native.NativeEngineeringDriverError('file owner unavailable during query')
            return owner == (process_id, process_started)

        def close(self):
            state.closed += 1

    def find_running(*, process_id):
        state.finder_calls.append(process_id)
        return session

    def profile(current):
        state.profile_reads.append(current)
        return native_project_profile('FX3U')

    monkeypatch.setattr(native, '_NativeFileUse', FileUse)
    monkeypatch.setattr(native, 'inspect_native_language', lambda process_id, executable: 'zh-CN')
    driver = native.NativeEngineeringDriver(session, path, 'FX3U',
        finder=SimpleNamespace(find_running=find_running),
        automation=SimpleNamespace(_current_native_project_profile=profile,
            _current_native_project_codepage_observation=lambda *a:state.project_language.copy()))
    return driver, state, session


@pytest.mark.parametrize('suffix,owner,passed', [
    ('managed/Visible.gxw', (701, '123456'), True),
    ('managed/Visible.gxw', (702, '123456'), False),
    ('managed/Visible.gxw', (701, '123457'), False),
    ('managed/Visible.gxw', None, False),
    ('other/Visible.gxw', (701, '123456'), False),
    ('managed/Another.gxw', (701, '123456'), False),
])
def test_visible_native_driver_abbreviated_path_requires_matching_suffix_and_process_creation(
        monkeypatch, tmp_path, suffix, owner, passed):
    from gxworks2.engineering_driver import NativeEngineeringDriverError

    path = tmp_path / 'managed' / 'Visible.gxw'
    caption = '.../' + suffix
    driver, state, session = _visible_native_path_driver(monkeypatch, path, caption, owner=owner)
    if passed:
        identity = driver.inspect()
        assert identity['project_path'] == str(path.resolve())
        assert identity['path_binding'] == 'native_file_owner_and_abbreviated_caption'
        assert identity['process_id'] == 701 and identity['process_started'] == '123456'
        assert identity['cpu_menu'] == 'FX3U/FX3UC'
        assert state.profile_reads == [session]
    else:
        with pytest.raises(NativeEngineeringDriverError, match='活动工程已经改变'):
            driver.inspect()
        assert state.profile_reads == []
    if suffix == 'managed/Visible.gxw':
        assert state.registrations == [path.resolve()]
        assert state.queries == [(701, '123456')]
    else:
        # Even a true exact-file owner cannot repair a different visible suffix.
        assert state.registrations == state.queries == []
    assert state.finder_calls == [701]
    driver.close()
    assert state.closed == int(suffix == 'managed/Visible.gxw')


@pytest.mark.parametrize('unavailable', ['register', 'query'])
def test_visible_native_driver_cannot_confirm_abbreviated_path_when_file_owner_is_unavailable(
        monkeypatch, tmp_path, unavailable):
    from gxworks2.engineering_driver import NativeEngineeringDriverError

    path = tmp_path / 'managed' / 'Visible.gxw'
    driver, state, _ = _visible_native_path_driver(monkeypatch, path, '.../managed/Visible.gxw',
        unavailable=unavailable)
    with pytest.raises(NativeEngineeringDriverError, match='file owner unavailable'):
        driver.inspect()
    assert state.profile_reads == []
    assert state.registrations == [path.resolve()]
    assert state.queries == ([(701, '123456')] if unavailable == 'query' else [])
    driver.close()
    assert state.closed == int(unavailable == 'query')


@pytest.mark.parametrize('caption_matches', [True, False])
def test_visible_native_driver_full_caption_requires_exact_path_without_owner_override(
        monkeypatch, tmp_path, caption_matches):
    from gxworks2.engineering_driver import NativeEngineeringDriverError

    path = tmp_path / 'managed' / 'Visible.gxw'
    caption = path if caption_matches else tmp_path / 'another' / 'Visible.gxw'
    driver, state, _ = _visible_native_path_driver(monkeypatch, path, str(caption.resolve()))
    if caption_matches:
        assert driver.inspect()['path_binding'] == 'native_full_caption'
    else:
        with pytest.raises(NativeEngineeringDriverError, match='活动工程已经改变'):
            driver.inspect()
        assert state.profile_reads == []
    # Full captions never fall back to file ownership, including the same basename.
    assert state.registrations == state.queries == []
    driver.close()
    assert state.closed == 0


def test_visible_native_driver_reuses_exact_file_query_and_releases_it_on_close(monkeypatch, tmp_path):
    from gxworks2.engineering_driver import NativeEngineeringDriverError

    path = tmp_path / 'managed' / 'Visible.gxw'
    driver, state, _ = _visible_native_path_driver(monkeypatch, path, '.../managed/Visible.gxw')
    assert driver.inspect() == driver.inspect()
    assert state.registrations == [path.resolve()]
    assert state.queries == [(701, '123456')] * 2
    driver.close()
    assert state.closed == 1
    with pytest.raises(NativeEngineeringDriverError, match='已关闭'):
        driver.inspect()
    assert state.queries == [(701, '123456')] * 2


@pytest.mark.parametrize('candidates,text', [([932], '日文'), ([1252], '英语'), ([54936], 'unresolved live GB option')])
def test_native_driver_invalidates_unsaved_project_language_change_without_waiting_for_disk(monkeypatch, tmp_path, candidates, text):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    path = tmp_path / 'Visible.gxw'
    driver, state, _ = _visible_native_path_driver(monkeypatch, path, str(path.resolve()))
    before = driver.inspect()
    assert before['project_codepage'] == 936
    assert before['project_language_observation']['codepage_candidates'] == [936,54936]
    signature = (path.stat().st_mtime_ns, path.read_bytes())
    state.project_language = dict(status='observed', codepage_candidates=candidates, status_text=text)
    with pytest.raises(NativeEngineeringDriverError, match='项目语言.*重新绑定'):
        driver.inspect()
    assert (path.stat().st_mtime_ns, path.read_bytes()) == signature
    driver.close()
    assert state.closed == 0


def _current_project_options_driver(tmp_path, language, *, checked=0, damage=None):
    from types import SimpleNamespace
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError
    from gxw.native_locale import ui_text

    driver = object.__new__(NativeEngineeringDriver)
    driver.path, driver.native_ui_language = tmp_path / 'Owned.gxw', language
    state = SimpleNamespace(open=False, checked=checked, events=[])
    identity = dict(process_id=41, window_handle=42, process_started='43', executable='GX/GD2.exe',
        software_version='1.635.0.1', project_path=str(driver.path), cpu_menu='Q03UDV',
        native_ui_language=language, project_codepage=936,
        project_language_observation=dict(codepage_candidates=[936, 54936]))
    driver.inspect = lambda: identity.copy()
    driver._idle = lambda: state.events.append(('idle',))
    def command(value):
        assert value == 0x467 and not state.open
        state.events.append(('command', value))
        state.open = True
    driver._command = command
    driver._foreground = lambda hwnd: state.events.append(('foreground', hwnd))
    def wait(predicate, message):
        value = predicate()
        if value:
            return value
        raise NativeEngineeringDriverError(message)
    driver._wait = wait
    def node(path, *, exact):
        assert exact is True
        assert path in ([ui_text('options_project', language)],
                        [ui_text('options_project', language), ui_text('options_common_setting', language)])
        return SimpleNamespace(expand=lambda: state.events.append(('expand', path)),
            select=lambda: state.events.append(('select', path)))
    tree = SimpleNamespace(control_id=lambda: 11000, is_visible=lambda: True,
        is_enabled=lambda: True, get_item=node)
    def get_check(message, wparam, lparam):
        assert (message, wparam, lparam) == (0xF0, 0, 0)
        state.events.append(('BM_GETCHECK', state.checked))
        return state.checked
    control = SimpleNamespace(handle=52, control_id=lambda: 15317,
        is_visible=lambda: True, is_enabled=lambda: damage != 'disabled',
        window_text=lambda: 'Wrong option' if damage == 'caption' else ui_text('options_gb18030', language),
        style=lambda: 0x50010002 if damage == 'style' else 0x50010003,
        send_message=get_check)
    dialog = SimpleNamespace(handle=51, window_text=lambda: ui_text('dialog_options', language)+' - Owned',
        is_enabled=lambda: True, descendants=lambda class_name:
            [tree] if class_name == 'SysTreeView32' else ([control, control] if damage == 'duplicate' else [control]))
    driver._dialogs = lambda: [dialog] if state.open else []
    def cancel(actual, button):
        assert actual is dialog and button == 2
        state.events.append(('Cancel', 2))
        state.open = False
    driver._click = cancel
    return driver, state, identity


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
def test_current_project_encoding_reads_fresh_option_and_cancels_after_unsaved_gb_switch(tmp_path, language):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver, state, identity = _current_project_options_driver(tmp_path, language)
    observed = driver.verify_project_codepage()
    assert observed['project_codepage'] == 936 and observed['checked'] == 0
    assert observed['origin'] == 'current_native_project_options_BM_GETCHECK'
    # The status and saved identity deliberately do not change. A fresh Options
    # read must detect the GB18030 switch before another engineering operation.
    state.checked = 1
    with pytest.raises(NativeEngineeringDriverError, match='项目编码.*重新绑定') as failure:
        driver.verify_project_codepage()
    assert failure.value.observation['project_codepage'] == 54936
    assert identity['project_codepage'] == 936
    assert [row for row in state.events if row[0] == 'BM_GETCHECK'] == [('BM_GETCHECK', 0), ('BM_GETCHECK', 1)]
    assert state.events.count(('command', 0x467)) == state.events.count(('Cancel', 2)) == 2
    assert state.open is False


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('damage', ['disabled', 'caption', 'style', 'duplicate', 'indeterminate'])
def test_current_project_encoding_rejects_unknown_control_and_always_cancels_owned_options(tmp_path, language, damage):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver, state, _ = _current_project_options_driver(tmp_path, language,
        checked=2 if damage == 'indeterminate' else 0, damage=damage)
    with pytest.raises(NativeEngineeringDriverError, match='GB18030'):
        driver.verify_project_codepage()
    assert state.events.count(('command', 0x467)) == state.events.count(('Cancel', 2)) == 1
    assert state.open is False
    if damage != 'indeterminate':
        assert not any(row[0] == 'BM_GETCHECK' for row in state.events)


@pytest.mark.parametrize('codepage', [932, 1252, 949])
def test_current_project_encoding_uses_unambiguous_status_without_options(tmp_path, codepage):
    driver, state, identity = _current_project_options_driver(tmp_path, 'en')
    identity.update(project_codepage=codepage, project_language_observation=dict(codepage_candidates=[codepage]))
    assert driver.verify_project_codepage() == dict(status='matched', project_codepage=codepage,
        origin='current_native_project_language_status')
    assert state.events == [('idle',)]


@pytest.mark.skipif(os.name != 'nt', reason='Windows Restart Manager own-file API witness')
def test_visible_native_file_use_matches_own_temporary_file_pid_and_creation_time(tmp_path):
    import ctypes
    from ctypes import wintypes as w
    from gxworks2.engineering_driver import _NativeFileUse

    path = tmp_path / 'own-process-only.gxw'
    path.write_bytes(b'isolated file ownership API witness, not a GX project')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                  w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    kernel.GetProcessTimes.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    # Hold our own ordinary file open. No GX process, window or project is queried.
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    query = None
    try:
        created, exited, used_kernel, used_user = [w.FILETIME() for _ in range(4)]
        assert kernel.GetProcessTimes(kernel.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(exited),
            ctypes.byref(used_kernel), ctypes.byref(used_user)), ctypes.get_last_error()
        started = created.dwLowDateTime + (created.dwHighDateTime << 32)
        query = _NativeFileUse(path)
        assert query.owns(os.getpid(), str(started))
        assert not query.owns(os.getpid() + 1, str(started))
        assert not query.owns(os.getpid(), str(started + 1))
        query.close()
        assert query.handle.value == 0
        query.close()
        assert query.handle.value == 0
    finally:
        if query is not None:
            query.close()
        assert kernel.CloseHandle(handle), ctypes.get_last_error()


def _visible_cross_reference_menu_driver(monkeypatch, *, scenario='ready', existing=False):
    import sys
    from types import SimpleNamespace
    from gxworks2 import engineering_driver as native
    state = SimpleNamespace(keys=[], events=[], waits=[], panel_reads=0, visibility_reads=0)
    panel = object()
    driver = object.__new__(native.NativeEngineeringDriver)
    driver.session = SimpleNamespace(process_id=701, window_handle=9001)
    def lookup(*, allow_missing=False):
        assert allow_missing is True
        state.panel_reads += 1
        state.events.append('panel')
        return panel if existing or state.panel_reads >= 3 else None
    def wait(predicate, message):
        state.waits.append(message)
        for _ in range(3):
            value = predicate()
            if value:
                return value
        raise native.NativeEngineeringDriverError(message)
    def send_keys(value, **options):
        assert options == {'pause': 0, 'vk_packet': False}
        state.keys.append(value)
        state.events.append(value)
    def visible(_handle):
        state.visibility_reads += 1
        return scenario != 'menu_closed' or state.visibility_reads == 1
    win32gui = SimpleNamespace(
        EnumWindows=lambda callback, context: None if scenario == 'missing_menu' else callback(710, context),
        IsWindowVisible=visible,
        GetClassName=lambda handle: 'XTPPopupBar',
        GetWindowText=lambda handle: '其他菜单' if scenario == 'wrong_menu_title' else '搜索/替换(F)',
        GetForegroundWindow=lambda: 9002 if scenario == 'foreground_changed' else 9001)
    monkeypatch.setitem(sys.modules, 'win32gui', win32gui)
    monkeypatch.setitem(sys.modules, 'win32process', SimpleNamespace(
        GetWindowThreadProcessId=lambda handle: (1, 702 if scenario == 'foreign_menu' else 701)))
    monkeypatch.setitem(sys.modules, 'pywinauto.keyboard', SimpleNamespace(send_keys=send_keys))
    driver._cross_reference_panel = lookup
    driver._foreground = lambda: state.events.append('activate_bound_frame')
    driver._wait = wait
    return driver, state, panel


def test_visible_cross_reference_existing_panel_does_not_send_menu_keys(monkeypatch):
    driver, state, panel = _visible_cross_reference_menu_driver(monkeypatch, existing=True)
    assert driver._open_cross_reference() is panel
    assert state.keys == [] and state.events == ['panel'] and state.waits == []


@pytest.mark.parametrize('scenario', ['missing_menu', 'foreign_menu', 'wrong_menu_title',
                                     'foreground_changed', 'menu_closed'])
def test_visible_cross_reference_unconfirmed_menu_cannot_send_text_into_source(monkeypatch, scenario):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver, state, _panel = _visible_cross_reference_menu_driver(monkeypatch, scenario=scenario)
    with pytest.raises(NativeEngineeringDriverError):
        driver._open_cross_reference()
    assert state.keys == ['%f']
    assert state.events[:3] == ['panel', 'activate_bound_frame', '%f']
    assert state.panel_reads == 1  # No later lookup can turn an unconfirmed menu into success.


def test_visible_cross_reference_confirmed_native_menu_waits_for_actual_panel(monkeypatch):
    driver, state, panel = _visible_cross_reference_menu_driver(monkeypatch)
    assert driver._open_cross_reference() is panel
    assert state.keys == ['%f', 's']
    assert state.events[:3] == ['panel', 'activate_bound_frame', '%f']
    assert state.events.index('s') < len(state.events) - 2
    assert state.panel_reads == 3 and len(state.waits) == 2


def test_visible_cross_reference_panel_scan_skips_popup_roots_and_handles_absence(monkeypatch):
    import sys
    from types import SimpleNamespace
    from gxworks2 import engineering_driver as native
    driver = object.__new__(native.NativeEngineeringDriver)
    driver.session = SimpleNamespace(process_id=701)
    scanned, opened = [], []
    visible = {9001, 710, 720, 730, 740}
    classes = {9001: 'GXW2FrameWnd', 710: 'XTPPopupBar', 720: 'GXW2FrameWnd',
               730: 'XTPDockingPaneMiniWnd', 740: 'ComboBox'}
    def children(handle, callback, context):
        assert handle in {9001, 730}, 'A transient menu or foreign frame must not be scanned'
        scanned.append(handle)
        if handle == 730:
            callback(740, context)
    expected = object()
    def window(*, handle):
        opened.append(handle)
        return expected
    driver.ui = SimpleNamespace(_application=lambda session: SimpleNamespace(window=window))
    monkeypatch.setitem(sys.modules, 'win32gui', SimpleNamespace(
        EnumWindows=lambda callback, context: [callback(handle, context) for handle in (9001, 710, 720, 730)],
        IsWindowVisible=lambda handle: handle in visible, GetClassName=classes.__getitem__,
        IsWindow=lambda handle: True, EnumChildWindows=children,
        GetDlgCtrlID=lambda handle: 20102, GetParent=lambda handle: 750))
    monkeypatch.setitem(sys.modules, 'win32process', SimpleNamespace(
        GetWindowThreadProcessId=lambda handle: (1, 702 if handle == 720 else 701)))
    assert driver._cross_reference_panel(allow_missing=True) is expected
    assert scanned == [9001, 730] and opened == [750]
    visible.remove(740)
    assert driver._cross_reference_panel(allow_missing=True) is None
    with pytest.raises(native.NativeEngineeringDriverError, match='不明确'):
        driver._cross_reference_panel()
    visible.remove(730)
    assert driver._owned_control_handles('ComboBox', 20102) == []
    assert driver._owned_control_handles('ComboBox', 20102, visible=False) == [740]


@pytest.mark.parametrize('controls', [[111], [], [111, 222]])
def test_visible_compile_output_requires_unique_real_control_before_first_clear(
        monkeypatch, controls):
    import sys
    from types import SimpleNamespace
    from gxworks2 import engineering_driver as native
    class FirstOutputClear(Exception):
        """Stop before any compilation, dialog handling or native text mutation."""
    driver = object.__new__(native.NativeEngineeringDriver)
    driver.session = SimpleNamespace(process_id=701, window_handle=9001)
    state = SimpleNamespace(cleared=[], opened=[])
    def lookup(class_name, control_id, *, visible=True):
        assert (class_name, control_id, visible) == ('Edit', 10013, False)
        return controls
    def window(*, handle):
        state.opened.append(handle)
        return SimpleNamespace(handle=handle)
    def clear(handle, message, wparam, text):
        assert (handle, message, wparam, text) == (111, 0x000C, 0, '')
        state.cleared.append(handle)
        raise FirstOutputClear
    driver.ui = SimpleNamespace(_application=lambda session: SimpleNamespace(window=window))
    driver._idle = lambda: None
    driver._owned_control_handles = lookup
    driver._command = lambda *_args, **_options: pytest.fail('Test must stop before compilation dispatch')
    driver._choose_native_menu = lambda *_args, **_options: pytest.fail('Output lookup must not send menu keys')
    monkeypatch.setitem(sys.modules, 'win32gui', SimpleNamespace(
        IsWindowVisible=lambda handle: False, SendMessage=clear))
    if len(controls) != 1:
        with pytest.raises(native.NativeEngineeringDriverError, match='摘要控件不明确'):
            driver._compile({'revision': 0})
        assert state.cleared == state.opened == []
    else:
        with pytest.raises(FirstOutputClear):
            driver._compile({'revision': 0})
        assert state.opened == state.cleared == [111]


@pytest.mark.parametrize('visible', [False, True])
@pytest.mark.parametrize('live', [False, True])
@pytest.mark.parametrize('checked', [0, 1])
@pytest.mark.parametrize('requested', [None, False, True])
def test_visible_compile_completion_requires_the_fresh_output_control_to_be_visible(monkeypatch, visible, live, checked, requested):
    import sys
    from types import SimpleNamespace
    from gxworks2 import engineering_driver as native
    wanted = 1 if not live else (checked if requested is None else int(requested))
    output = '已完成全部编译。Error: 0, Warning: 0' + (', CheckWarning: 0' if wanted else '')
    state = SimpleNamespace(text='old successful build', phase='idle', commands=[], snapshots=0,
                            checked=checked, check_clicks=0)
    driver = object.__new__(native.NativeEngineeringDriver)
    driver.session = SimpleNamespace(process_id=701)
    summary = SimpleNamespace(handle=111, window_text=lambda: state.text)
    checks = SimpleNamespace(get_check_state=lambda: state.checked)
    dialog = SimpleNamespace(handle=222, is_enabled=lambda: True, child_window=lambda **options: checks)
    driver.ui = SimpleNamespace(_application=lambda session: SimpleNamespace(window=lambda **options: summary))
    driver._idle = lambda: None
    driver.verify_project_codepage = lambda: dict(status='matched', project_codepage=936)
    driver.inspect = lambda: {**_active_read_identity(), 'process_id': 701}
    driver._owned_control_handles = lambda *args, **options: [111]
    def clear(handle, message, wparam, text):
        assert (handle, message, wparam, text) == (111, 0x000C, 0, '')
        state.text = ''
    def command(code):
        assert state.text == ''
        state.commands.append(code)
        state.phase = 'prompt'
    def click(_dialog, control_id):
        if control_id == 12270:
            state.checked, state.check_clicks = state.checked ^ 1, state.check_clicks + 1
        else:
            assert control_id == 1
            state.phase, state.text = 'completed', output
    def wait(predicate, message):
        for _ in range(3):
            value = predicate()
            if value:
                return value
        raise native.NativeEngineeringDriverError(message)
    def snapshot():
        state.snapshots += 1
    driver._command, driver._click, driver._wait, driver.snapshot = command, click, wait, snapshot
    driver._dialogs = lambda: [dialog] if state.phase == 'prompt' else []
    driver._dialog_text = lambda _dialog: '是否执行全部编译？'
    driver._window = lambda: SimpleNamespace(is_enabled=lambda: True)
    driver._visible = lambda page, **options: {'completed': True, 'page': page}
    monkeypatch.setitem(sys.modules, 'win32gui', SimpleNamespace(
        SendMessage=clear, IsWindowVisible=lambda handle: visible))
    if visible:
        plan = {'revision': 3, **({} if requested is None else {'public_checks_requested': requested})}
        result = driver.compile_current(plan) if live else driver._compile(plan)
        assert result['completed'] is True and result['output'] == output and result['revision'] == 3
        assert state.snapshots == (0 if live else 1)
        assert result['driver_saved'] is (not live) and result['native_save_behavior'] == 'compile_may_save'
        assert result['output_kind'] == 'native_summary' and result['diagnostics']['status'] == 'unavailable'
        assert result['saved'] is (None if live else True)
        assert result['source_unchanged'] is (None if live else True)
        assert result['public_checks_requested'] is bool(wanted)
        current = driver.read_current_compile_result(revision=3)
        assert current['status'] == 'observed' and current['compile_run_id'] == result['compile_run_id']
    else:
        with pytest.raises(native.NativeEngineeringDriverError, match='本次原生全部编译尚未完成'):
            plan = {'revision': 3, **({} if requested is None else {'public_checks_requested': requested})}
            driver.compile_current(plan) if live else driver._compile(plan)
        assert state.snapshots == 0
        assert driver.read_current_compile_result(revision=3)['status'] == 'unavailable'
    assert state.commands == [0x4A1]
    assert state.checked == wanted
    assert state.check_clicks == int(checked != wanted)


def _native_compile_diagnostic_table(entries=(), *, language='zh-CN'):
    from gxw.native_locale import ui_text
    titles = ('', 'No.', *(ui_text('diagnostic_' + key, language)
               for key in ('result', 'data', 'class', 'content', 'code')), '')
    def row(number, values):
        return dict(role=28, value=str(number), cells=[dict(name=name, value=value, role=29)
            for name, value in zip(titles, values)])
    result = [dict(role=25, value='', cells=[]) for _ in titles]
    result.append(row(0, titles))
    for index, (severity, owner, category, message, code) in enumerate(entries, 1):
        result.append(row(index, ('', str(index), severity, owner, category, message, code, '')))
    return result


@pytest.mark.parametrize('language,severity', [('zh-CN', 'Error'), ('en', 'ERROR'), ('ja', 'Error')])
def test_localized_compile_grid_preserves_native_severity_and_core_error(language, severity):
    from gxworks2.engineering_driver import _compile_diagnostic_rows
    raw = _native_compile_diagnostic_table([(severity, 'Globals', 'Global label check',
        'An independently supplied duplicate declaration diagnostic.', 'F1055')], language=language)
    rows = _compile_diagnostic_rows(raw, native_ui_language=language)
    assert rows == [dict(native_row=1, no='1', severity='Error', raw_severity=severity,
        data_name='Globals', category='Global label check',
        message='An independently supplied duplicate declaration diagnostic.', code='F1055')]


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
def test_st_compile_grid_retains_complete_warning_with_dot_selection_column(language):
    from copy import deepcopy
    from gxw.native_locale import ui_text
    from gxworks2.engineering_driver import _compile_diagnostic_rows, NativeEngineeringDriverError
    message = 'A complete synthetic operator warning with its input domain and evaluation formula.'
    severity = ui_text('diagnostic_warning', language)
    raw = _native_compile_diagnostic_table([(severity, 'MAIN', 'Compile', message, 'C9146')], language=language)
    for row in raw:
        if row['cells']:
            row['cells'][0].update(name='.', value='.')
    frozen = deepcopy(raw)
    rows = _compile_diagnostic_rows(raw, native_ui_language=language)
    assert raw == frozen
    assert rows == [dict(native_row=1, no='1', severity='Warning', raw_severity=severity,
        data_name='MAIN', category='Compile', message=message, code='C9146')]
    raw[-1]['cells'][5]['name'] = 'Unrelated text'
    with pytest.raises(NativeEngineeringDriverError):
        _compile_diagnostic_rows(raw, native_ui_language=language)


def test_actual_english_q_diagnostics_retain_both_native_duplicate_scope_errors():
    import json
    from copy import deepcopy
    from tools.offline_inputs import private_path
    from gxworks2.engineering_driver import _compile_diagnostic_rows
    path = private_path('research/experiments/native-live-multitarget-20261018/'
        'q03udv-en-diagnostic-grid-observe-01/native-grids.json')
    if not path.is_file():
        pytest.skip('private actual English Q03UDV compile diagnostic grid unavailable')
    raw = json.loads(path.read_text(encoding='utf-8'))[0]['raw_rows']
    frozen = deepcopy(raw)
    rows = _compile_diagnostic_rows(raw, native_ui_language='en')
    assert raw == frozen
    assert [(row['severity'], row['raw_severity'], row['data_name'], row['code']) for row in rows] == [
        ('Error', 'ERROR', 'LiveGlobals', 'F1055'), ('Error', 'ERROR', 'LiveGlobals', 'F1055')]
    assert "'Enable'" in rows[0]['message'] and "'Value'" in rows[1]['message']


@pytest.mark.parametrize('entries', [[], [('Error', 'Live_ST_FB', '检查局部标签', 'Invalid label.', 'C5054')],
    [('Warning', 'POU_01', '编译程序', 'Independent graphs.', 'C2034')]
    + [('Information', '-', '-', 'Current allocation information.', f'F{1301 + index}') for index in range(7)]])
def test_compile_diagnostic_msaa_reads_row_one_and_skips_column_objects(monkeypatch, entries):
    import sys
    from types import SimpleNamespace
    from gxworks2 import engineering_driver as native
    raw = _native_compile_diagnostic_table(entries)
    children = {}
    for index, row in enumerate(raw, 1):
        if row['role'] == 25:
            children[index] = SimpleNamespace(accRole={0: 25})  # No row/value properties exist here.
        else:
            cells = {column: SimpleNamespace(accRole={0: cell['role']}, accName={0: cell['name']},
                accValue={0: cell['value']}) for column, cell in enumerate(row['cells'], 1)}
            children[index] = SimpleNamespace(accRole={0: 28}, accValue={0: row['value']},
                accChildCount=len(cells), accChild=cells)
    grid = SimpleNamespace(accChildCount=len(children), accChild=children)
    sentinel = object()
    monkeypatch.setitem(sys.modules, 'ctypes', SimpleNamespace(POINTER=lambda _: lambda: sentinel,
        c_uint32=lambda value: value, byref=lambda value: value,
        windll=SimpleNamespace(oleacc=SimpleNamespace(AccessibleObjectFromWindow=lambda *args: 0))))
    monkeypatch.setitem(sys.modules, 'comtypes.automation', SimpleNamespace(IDispatch=SimpleNamespace(_iid_=object())))
    monkeypatch.setitem(sys.modules, 'comtypes.client.dynamic', SimpleNamespace(Dispatch=lambda _: grid))
    rows = native._accessible_compile_diagnostic_rows(928374)
    assert rows == [dict(native_row=index, no=str(index), severity=entry[0], raw_severity=entry[0], data_name=entry[1],
        category=entry[2], message=entry[3], code=entry[4]) for index, entry in enumerate(entries, 1)]
    assert all('location' not in row for row in rows)


@pytest.mark.parametrize('damage', ['number', 'column', 'cell_role', 'severity', 'duplicate_header'])
def test_compile_diagnostic_table_rejects_unconfirmed_native_rows(damage):
    from copy import deepcopy
    from gxworks2.engineering_driver import _compile_diagnostic_rows, NativeEngineeringDriverError
    raw = _native_compile_diagnostic_table([('Error', 'Owner', 'Check', 'Invalid label.', 'C5054')])
    if damage == 'number': raw[-1]['value'] = '2'
    elif damage == 'column': raw[-1]['cells'][3]['name'] = 'Foreign data'
    elif damage == 'cell_role': raw[-1]['cells'][3]['role'] = 25
    elif damage == 'severity': raw[-1]['cells'][2]['value'] = 'Unknown'
    else: raw.append(deepcopy(raw[-2]))
    if damage == 'duplicate_header':
        assert _compile_diagnostic_rows(raw) is None
    else:
        with pytest.raises(NativeEngineeringDriverError):
            _compile_diagnostic_rows(raw)


@pytest.mark.parametrize('damage', [None, 'missing', 'ambiguous', 'unreadable', 'counts',
    'missing_check', 'missing_check_requested', 'missing_check_undeclared', 'unexpected_check'])
def test_current_compile_diagnostics_bind_unique_header_grid_and_severity_counts(monkeypatch, damage):
    from copy import deepcopy
    from types import SimpleNamespace
    from gxworks2 import engineering_driver as native
    driver = object.__new__(native.NativeEngineeringDriver)
    entries = [('Warning', 'Owner', 'Compile', 'Independent graphs.', 'C2034')]
    entries += [('Information', '-', '-', 'Allocation information.', f'F{1301 + index}') for index in range(7)]
    if damage == 'unexpected_check':
        entries.append(('CheckWarning', 'Owner', 'Program check', 'Unexpected check diagnostic.', 'C9999'))
    rows = native._compile_diagnostic_rows(_native_compile_diagnostic_table(entries))
    ids = [] if damage == 'missing' else [4432, 5811] if damage == 'ambiguous' else [4432]
    summary = SimpleNamespace(parent=lambda: SimpleNamespace(descendants=lambda **kwargs:
        [SimpleNamespace(handle=value) for value in ids]))
    def read(handle, *, native_ui_language='zh-CN'):
        assert native_ui_language == 'zh-CN'
        if damage == 'unreadable': raise RuntimeError('MSAA not available')
        return deepcopy(rows)
    monkeypatch.setattr('gxworks2.engineering_compile._accessible_compile_diagnostic_rows', read)
    missing_check = damage in ('missing_check', 'missing_check_requested', 'missing_check_undeclared', 'unexpected_check')
    run = dict(compile_run_id='same-run', revision=7, identity=_active_read_identity(),
        output=f'已完成全部编译。Error: 0, Warning: {2 if damage == "counts" else 1}'
        + ('' if missing_check else ', CheckWarning: 0'))
    if damage in ('missing_check', 'unexpected_check'):
        run['public_checks_requested'] = False
    elif damage == 'missing_check_requested':
        run['public_checks_requested'] = True
    observed = driver._read_compile_diagnostics(summary, run)
    assert observed['compile_run_id'] == 'same-run' and observed['revision'] == 7 and observed['pid'] == 42
    assert observed['identity'] == run['identity']
    if damage in (None, 'missing_check'):
        assert observed['status'] == 'observed' and len(observed['rows']) == 8
        assert observed['severity_counts'] == dict(Error=0, Warning=1, CheckWarning=0, Information=7)
        assert observed['expected_counts'] == dict(Error=0, Warning=1,
            CheckWarning=None if damage == 'missing_check' else 0)
    elif damage in ('counts', 'missing_check_requested', 'missing_check_undeclared', 'unexpected_check'):
        assert observed['status'] == 'unconfirmed' and len(observed['rows']) == (9 if damage == 'unexpected_check' else 8)
    else:
        assert observed['status'] == 'unavailable' and observed['rows'] == []


def _pending_compile_witness(monkeypatch):
    from copy import deepcopy
    import sys
    from types import SimpleNamespace
    from gxworks2 import engineering_driver as native
    driver = object.__new__(native.NativeEngineeringDriver)
    state = SimpleNamespace(text='old build', phase='idle', commands=[], saves=0, visible=True,
        controls=[111], modal=False, enabled=True, identity=_active_read_identity())
    driver.session = SimpleNamespace(process_id=42)
    header = native._compile_diagnostic_rows(_native_compile_diagnostic_table())
    grid = SimpleNamespace(handle=982742)
    summary = SimpleNamespace(handle=111, window_text=lambda: state.text,
        parent=lambda: SimpleNamespace(descendants=lambda **kwargs: [grid]))
    checks = SimpleNamespace(get_check_state=lambda: 0)
    dialog = SimpleNamespace(handle=222, is_enabled=lambda: True, child_window=lambda **kwargs: checks)
    driver.ui = SimpleNamespace(_application=lambda session: SimpleNamespace(window=lambda **kwargs: summary))
    driver._idle = lambda: None
    driver.verify_project_codepage = lambda: dict(status='matched', project_codepage=936)
    driver.inspect = lambda: deepcopy(state.identity)
    driver._owned_control_handles = lambda *args, **kwargs: list(state.controls)
    driver._dialogs = lambda: [dialog] if state.phase == 'prompt' or state.modal else []
    driver._dialog_text = lambda dialog: '是否执行全部编译？'
    driver._window = lambda: SimpleNamespace(is_enabled=lambda: state.enabled)
    driver._visible = lambda page, **kwargs: dict(completed=True, page=page)
    def clear(handle, message, wparam, text):
        assert state.phase == 'idle'
        state.text = text
    def command(code):
        run = driver._pending_compile_run
        assert run['fresh_output_reset'] is True and run['compile_run_id'] and state.text == ''
        assert run['revision'] == 3 and run['identity'] == state.identity and run['summary_handle'] == 111
        state.commands.append(code)
        state.phase = 'prompt'
    def click(dialog, control_id):
        assert control_id == 1  # Live compilation preserves the unchecked option.
        state.phase, state.text = 'compiling', 'Compiling current project.'
    def wait(predicate, message):
        value = predicate()
        if value:
            return value
        raise native.NativeEngineeringDriverError(message)
    def snapshot():
        state.saves += 1
        pytest.fail('Compile recovery must not save')
    driver._command, driver._click, driver._wait, driver.snapshot = command, click, wait, snapshot
    monkeypatch.setattr('gxworks2.engineering_compile._accessible_compile_diagnostic_rows', lambda handle, **kwargs: deepcopy(header))
    monkeypatch.setitem(sys.modules, 'win32gui', SimpleNamespace(
        SendMessage=clear, IsWindowVisible=lambda handle: state.visible))
    with pytest.raises(native.NativeEngineeringDriverError, match='全部编译尚未完成'):
        driver.compile_current({'revision': 3})
    return driver, state


@pytest.mark.parametrize('damage', [None, 'revision', 'identity', 'controls', 'reset', 'confirmation',
    'output', 'hidden', 'modal', 'disabled'])
def test_timed_out_compile_recovers_only_its_original_completed_native_run_without_replay(monkeypatch, damage):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver, state = _pending_compile_witness(monkeypatch)
    token = driver._pending_compile_run['compile_run_id']
    assert driver._pending_compile_run['confirmation_accepted'] is True
    with pytest.raises(NativeEngineeringDriverError, match='未重新发送编译'):
        driver.compile_current({'revision': 3})
    state.text = '已完成全部编译。Error: 0, Warning: 0'  # The actual unchecked profile omits CheckWarning.
    if damage == 'identity': state.identity['process_started'] = 'another process start'
    elif damage == 'controls': state.controls = [112]
    elif damage == 'reset': driver._pending_compile_run['fresh_output_reset'] = False
    elif damage == 'confirmation': driver._pending_compile_run['confirmation_dispatched'] = False
    elif damage == 'output': state.text = 'Old result: Error: 0, Warning: 0, CheckWarning: 0'
    elif damage == 'hidden': state.visible = False
    elif damage == 'modal': state.modal = True
    elif damage == 'disabled': state.enabled = False
    observed = driver.read_current_compile_result(revision=4 if damage == 'revision' else 3)
    assert observed['status'] == ('unavailable' if damage else 'observed')
    assert state.commands == [0x4A1] and state.saves == 0
    if damage is None:
        assert observed['compile_run_id'] == token and observed['recovered'] and observed['completed']
        assert observed['public_checks_requested'] is False
        assert observed['driver_saved'] is False and observed['saved'] is None
        assert observed['diagnostics']['status'] == 'observed' and observed['diagnostics']['rows'] == []
        assert driver._pending_compile_run is None
        assert driver.read_current_compile_result(revision=3)['compile_run_id'] == token
    else:
        assert driver._pending_compile_run['compile_run_id'] == token


@pytest.mark.parametrize('damage', ['changed_rows', 'unreadable_rows'])
def test_completed_compile_reader_keeps_its_observed_diagnostic_rows_bound_to_the_run(monkeypatch, damage):
    from gxworks2 import engineering_driver as native
    driver, state = _pending_compile_witness(monkeypatch)
    state.text = '已完成全部编译。Error: 0, Warning: 0'
    first = driver.read_current_compile_result(revision=3)
    assert first['status'] == 'observed' and first['diagnostics']['rows'] == []
    if damage == 'changed_rows':
        changed = native._compile_diagnostic_rows(_native_compile_diagnostic_table([
            ('Information', '-', '-', 'Another allocation result.', 'F1301')]))
        monkeypatch.setattr('gxworks2.engineering_compile._accessible_compile_diagnostic_rows', lambda handle, **kwargs: changed)
    else:
        def unavailable(handle, *, native_ui_language='zh-CN'):
            assert native_ui_language == 'zh-CN'
            raise RuntimeError('Native rows became unreadable')
        monkeypatch.setattr('gxworks2.engineering_compile._accessible_compile_diagnostic_rows', unavailable)
    result = driver.read_current_compile_result(revision=3)
    assert result['status'] == 'unavailable'
    assert result['reason'] == 'compile_diagnostic_rows_changed_or_unavailable'
    assert driver._last_compile_run['compile_run_id'] == first['compile_run_id']
    assert driver._last_compile_run['diagnostics']['rows'] == []
    assert state.commands == [0x4A1] and state.saves == 0


@pytest.mark.parametrize('damage', [None, 'foreign_resource'])
def test_native_st_cross_reference_resource_binding_rejects_a_foreign_program(damage):
    import json
    from copy import deepcopy
    from zipfile import ZipFile
    from tools.offline_inputs import private_path
    from gxw.models import GXWFormatError
    from gxw.visible_engineering import verify_native_cross_references
    fixture = private_path('tests/fixtures/gxw_visible_st_program_graph_native.json')
    if not fixture.is_file():
        pytest.skip('private frozen ST Program/graph fixture is unavailable')
    index = json.loads(fixture.read_text(encoding='utf-8'))
    archive = private_path(index['archive'])
    if not archive.is_file():
        pytest.skip('private frozen ST Program/graph archive is unavailable')
    case = index['cases']['st_cross_references_049']
    with ZipFile(archive) as saved:
        receipt, final = json.loads(saved.read(case['receipt'])), saved.read(case['after'])
    assert receipt['passed'] is True and receipt['identity']['software_version'] == '1.635.0.1'
    assert receipt['identity']['cpu_menu'] == 'FX3U/FX3UC'
    source = next(step for step in receipt['steps'] if step['operation'] == 'show_assignments')
    observation = deepcopy(source['observation'])
    record, = observation['cross_references']
    rows = record['rows']
    assert [row['native_row'] for row in rows] == [2, 3, 4, 5, 6]
    assert {key: value for key, value in rows[2].items() if key != 'native_row'} == {
        key: value for key, value in rows[3].items() if key != 'native_row'}
    assert all(row['program'] == 'MAIN' for row in rows)
    if damage:
        for row in rows:
            row['program'] = 'FOREIGN_RESOURCE'
        with pytest.raises(GXWFormatError, match='current resource/task binding'):
            verify_native_cross_references(final, [record['query']], observation)
    else:
        checked = verify_native_cross_references(final, [record['query']], observation)
        assert checked['status'] == 'matched'
        assert len(checked['queries'][0]['rows']) == 5


def _visible_graph_context_driver(monkeypatch, scenario):
    import sys
    from types import SimpleNamespace
    from gxworks2 import engineering_driver as native

    state = SimpleNamespace(keys=[], clicks=[], menu_open=True, waits=[])
    driver = object.__new__(native.NativeEngineeringDriver)
    driver.session = SimpleNamespace(process_id=701, window_handle=9001)
    owners = {9001: 701, 9002: 701, 9003: 702, 810: 701, 710: 701, 711: 701, 730: 702}
    classes = {9001: 'GXW2FrameWnd', 710: 'XTPPopupBar', 711: 'XTPPopupBar',
               720: 'OtherMenu', 730: 'XTPPopupBar', 740: 'XTPPopupBar'}
    if scenario == 'foreign_view':
        owners[810] = 702
    if scenario == 'foreign_menu':
        owners[710] = 702
    if scenario == 'wrong_menu_class':
        classes[710] = 'OtherMenu'
    handles = [9001, 710, 720, 730, 740] + ([711] if scenario == 'duplicate_menus' else [])

    def visible(handle):
        return handle != 740 and not (handle in (710, 711) and (
            not state.menu_open or scenario in ('missing_menu', 'hidden_menu')))

    def wait(predicate, message):
        state.waits.append(message)
        for _ in range(2):
            value = predicate()
            if value:
                return value
        raise native.NativeEngineeringDriverError(message)

    def send_keys(value, **options):
        assert options == {'vk_packet': False}
        state.keys.append(value)

    def foreground():
        if scenario == 'menu_closed':
            state.menu_open = False
        return {'menu': 710, 'same_process_other_frame': 9002, 'foreign_foreground': 9003}.get(scenario, 9001)
    monkeypatch.setitem(sys.modules, 'win32gui', SimpleNamespace(
        EnumWindows=lambda callback, context: [callback(handle, context) for handle in handles],
        IsWindowVisible=visible, GetClassName=classes.__getitem__, GetForegroundWindow=foreground))
    monkeypatch.setitem(sys.modules, 'win32process', SimpleNamespace(
        GetWindowThreadProcessId=lambda handle: (1, owners.get(handle, 701))))
    monkeypatch.setitem(sys.modules, 'pywinauto.keyboard', SimpleNamespace(send_keys=send_keys))
    driver._idle = lambda: None
    driver._foreground = lambda: None
    driver._wait = wait
    driver._command = lambda *_args, **_options: pytest.fail('Graph context must not mutate the engineering project')
    view = SimpleNamespace(handle=810, is_visible=lambda: scenario != 'hidden_view',
        is_enabled=lambda: scenario != 'disabled_view', click_input=lambda **options: state.clicks.append(options))
    grid = {'x': 10, 'y': 15, 'pitch': 16, 'width': 400, 'height': 300}
    return driver, view, grid, state


@pytest.mark.parametrize('scenario', ['foreign_view', 'hidden_view', 'disabled_view',
    'missing_menu', 'hidden_menu', 'foreign_menu', 'wrong_menu_class', 'duplicate_menus',
    'same_process_other_frame', 'foreign_foreground', 'menu_closed'])
def test_visible_graph_context_rejects_unbound_view_or_menu_before_any_shortcut(monkeypatch, scenario):
    from gxworks2.engineering_driver import NativeEngineeringDriverError

    driver, view, grid, state = _visible_graph_context_driver(monkeypatch, scenario)
    with pytest.raises(NativeEngineeringDriverError):
        driver._graph_context(view, grid, [4, 2, 7, 4])
    assert state.keys == []
    if scenario in ('foreign_view', 'hidden_view', 'disabled_view'):
        assert state.clicks == state.waits == []
    else:
        assert state.clicks == [{'button': 'right', 'coords': (82, 63)}]


@pytest.mark.parametrize('foreground', ['frame', 'menu'])
def test_visible_graph_context_returns_only_the_unique_bound_popup_for_exact_cut(monkeypatch, foreground):
    driver, view, grid, state = _visible_graph_context_driver(monkeypatch, foreground)
    popup = driver._graph_context(view, grid, [4, 2, 7, 4])
    # Foreign, hidden and non-popup windows cannot create false ambiguity.
    assert state.clicks == [{'button': 'right', 'coords': (82, 63)}]
    assert callable(popup) and popup() == 710
    assert state.keys == [] and state.menu_open
    assert len(state.waits) == 1
    state.menu_open = False
    assert popup() is None


@pytest.mark.parametrize('capture', ['captured', 'capture_none', 'capture_failed', 'write_failed', 'no_evidence', 'identity_failed'])
def test_visible_startup_error_preserves_original_failure_and_bound_identity_when_capture_fails(tmp_path, capture):
    from copy import deepcopy
    from types import SimpleNamespace
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError

    driver = object.__new__(NativeEngineeringDriver)
    driver.path = (tmp_path / 'managed' / 'Visible.gxw').resolve()
    driver.evidence_directory = None if capture == 'no_evidence' else tmp_path / 'private-evidence'
    driver._binding = (701, 9001, '123456', r'C:\MELSOFT\GX Works2\GXW2.exe', '1.635.0.1')
    session = dict(zip(('process_id', 'window_handle', 'process_started', 'executable', 'software_version'), driver._binding))
    driver.session = SimpleNamespace(**session, project_open=False)
    if capture == 'identity_failed':
        driver.session = SimpleNamespace(**{**session, 'process_id': 702, 'window_handle': 9002}, project_open=False)
    events = []
    driver._closed = False
    driver._file_use = SimpleNamespace(close=lambda: events.append('release_file_query'))

    def identity():
        events.append('identity')
        if capture == 'identity_failed':
            raise RuntimeError('frame identity unavailable')
        return driver.session

    def save(path):
        events.append(('image', path))
        if capture == 'write_failed':
            raise OSError('screenshot write failed')

    def image():
        events.append('capture')
        if capture == 'capture_failed':
            raise RuntimeError('screenshot capture failed')
        if capture == 'capture_none':
            return None
        return SimpleNamespace(save=save)

    driver._frame_identity = identity
    driver._window = lambda: SimpleNamespace(capture_as_image=image)
    original_observation = {'dialogs': [{'title': 'GX Works2', 'message': '无法打开工程。'}],
                            'hint': '检查已保存的工程副本。'}
    original = NativeEngineeringDriverError('原生工程打开失败。', observation=deepcopy(original_observation))
    failure = driver._startup_error(original)
    assert isinstance(failure, NativeEngineeringDriverError) and str(failure) == str(original)
    assert failure.observation['startup_session'] == session
    assert failure.observation['requested_project'] == str(driver.path)
    assert failure.observation['dialogs'] == original_observation['dialogs']
    assert failure.observation['hint'] == original_observation['hint']
    assert failure.observation['dialogs'] is not original.observation['dialogs']
    assert original.observation == original_observation
    assert driver._closed and events.count('release_file_query') == 1
    assert events[-1] == 'release_file_query'
    if capture != 'identity_failed':
        assert failure.observation['project_opened'] is False
    if capture in ('capture_none', 'capture_failed', 'write_failed', 'identity_failed'):
        assert failure.observation['startup_observation_error'] == {
            'capture_none': '启动失败页面的窗口截图未返回图像；请检查窗口尺寸、桌面可访问性及 Pillow。',
            'capture_failed': 'screenshot capture failed', 'write_failed': 'screenshot write failed',
            'identity_failed': 'frame identity unavailable',
        }[capture]
        assert 'image' not in failure.observation
    else:
        assert 'startup_observation_error' not in failure.observation
        if capture == 'captured':
            assert failure.observation['image'] == str(driver.evidence_directory / 'startup-failure.png')
        else:
            assert 'capture' not in events and 'image' not in failure.observation


@pytest.mark.parametrize('scenario', ['ready', 'foreign_view', 'hidden_view', 'disabled_view',
                                     'left_edge', 'right_edge', 'top_edge', 'bottom_edge'])
def test_visible_graph_node_selection_is_a_bound_client_click_without_menu_keys(monkeypatch, scenario):
    from gxworks2.engineering_driver import NativeEngineeringDriverError

    driver, view, grid, state = _visible_graph_context_driver(monkeypatch, scenario)
    driver._graph_context = lambda *_args: pytest.fail('Node selection must not open a context menu')
    box = [4, 2, 7, 4]
    if scenario == 'left_edge':
        grid['x'], box[0] = -3, 0  # x == 5, outside the strict client boundary.
    elif scenario == 'right_edge':
        grid['width'] = 110  # x == width - 28.
    elif scenario == 'top_edge':
        grid['y'], box[1] = -11, 0  # y == 5.
    elif scenario == 'bottom_edge':
        grid['height'] = 91  # y == height - 28.
    if scenario == 'ready':
        driver._select_graph_node(view, grid, box)
        assert len(state.clicks) == 1
        assert state.clicks[0]['coords'] == (82, 63)
        assert state.clicks[0].get('button', 'left') == 'left'
    else:
        with pytest.raises(NativeEngineeringDriverError):
            driver._select_graph_node(view, grid, box)
        assert state.clicks == []
    assert state.keys == state.waits == []


@pytest.mark.parametrize('stage', ['before_step', 'save', 'recompile', 'reopen_returned', 'reopen_snapshot'])
@pytest.mark.parametrize('damage', ['changed_address', 'cleared_assignment'])
def test_visible_session_later_saved_snapshots_cannot_reuse_a_different_compiler_mapping(stage, damage):
    from application.native_engineering import NativeEngineeringSession, _project_projection, verified_native_data
    from gxw.fb_authoring import _context
    from gxw.compiler_tables import parse_compiler_tables
    from gxw.compiler_symbols import compiler_symbols
    from gxw.visible_engineering import saved_assignments

    before = _visible_cross_reference_witness()
    compiler = bytearray(_context(before, require_fbd=False)[1]['CGTable.dat'])
    tables = parse_compiler_tables(compiler)
    symbol, = [row for row in compiler_symbols(tables) if row.component.name_bytes == b'Result'
               and tables.pou_at(row.pou_offset).name_bytes == b'@GLOBALS']
    assert symbol.assignment.operand == 'D2999'
    record = symbol.component.record
    allocation = record.offset + len(record.raw) - len(symbol.assignment.raw)
    # Synthetic counterexamples change only the frozen compiler allocation.
    # They are not native acceptance evidence or new source-address bindings.
    if damage == 'changed_address':
        compiler[allocation + 2:allocation + 5] = (2998).to_bytes(3, 'little', signed=True)
    else:
        compiler[allocation:allocation + 26] = bytes(26)
    after = _replace_visible_logical_stream(before, 'CGTable.dat', bytes(compiler))
    assert _project_projection(after) == _project_projection(before)
    observed, = [row for row in saved_assignments(after)['rows'] if row['name'] == 'Result']
    assert observed['address'] == ('D2998' if damage == 'changed_address' else None)
    driver = _VisibleDriver(before)
    original_execute, original_snapshot, original_reopen = driver.execute, driver.snapshot, driver.reopen
    commands = [{'operation': 'compile'}, {'operation': 'show_assignments',
        'queries': [{'scope': 'global', 'source': 'Global1.gh', 'name': 'Result'}]}]
    if stage == 'before_step':
        shifted = False
        def snapshot():
            nonlocal shifted
            value = original_snapshot()
            if not shifted and driver.calls and driver.calls[-1][0]['operation'] == 'show_assignments':
                shifted, driver.raw = True, after
            return value
        driver.snapshot = snapshot
        commands.append({'operation': 'save'})
    elif stage in ('save', 'recompile'):
        final_operation = 'save' if stage == 'save' else 'compile'
        def execute(command, prepared):
            value = original_execute(command, prepared)
            if command['operation'] == final_operation and len(driver.calls) == 3:
                driver.raw = after
            return value
        driver.execute = execute
        commands.append({'operation': final_operation})
    else:
        def reopen():
            value = original_reopen()
            if stage == 'reopen_snapshot':
                driver.raw = after
            return after if stage == 'reopen_returned' else value
        driver.reopen = reopen
        commands.append({'operation': 'reopen'})
    result = NativeEngineeringSession(driver, 'FX3U').run(commands, baseline=before)
    assert not result['passed'] and result['status'] == 'interrupted'
    assert result['assignments']['status'] == 'unverified' and result['assignments']['rows'] == []
    assert 'compiler address mapping' in result['error']['message']
    assert result['data'] == before and result['data_status'] == 'last_verified_source'
    assert result['unverified_data'] == after and driver.closed
    assert len(driver.calls) == (2 if stage == 'before_step' else 3)
    with pytest.raises(ValueError, match='did not complete'):
        verified_native_data(result, {'schema_version': 1, 'commands': commands})


@pytest.mark.parametrize('case_name', ['grid_calibration_113', 'grid_calibration_093', 'grid_calibration_118'])
@pytest.mark.parametrize('blurred', [False, True])
def test_visible_grid_calibration_uses_frozen_native_two_dimensional_points(case_name, blurred):
    import json
    from io import BytesIO
    from zipfile import ZipFile
    from PIL import Image, ImageFilter
    from tools.offline_inputs import private_path
    from gxworks2.engineering_driver import NativeEngineeringDriverError, _grid_calibration

    fixture = private_path('tests/fixtures/gxw_visible_st_program_graph_native.json')
    if not fixture.is_file():
        pytest.skip('private frozen ST Program/graph fixture is unavailable')
    index = json.loads(fixture.read_text(encoding='utf-8'))
    archive = private_path(index['archive'])
    if not archive.is_file() or case_name not in index['cases']:
        pytest.skip('private frozen native grid capture is unavailable')
    case = index['cases'][case_name]
    with ZipFile(archive) as saved, Image.open(BytesIO(saved.read(case['image']))) as capture:
        assert capture.size == (1832, 1296)
        # The unmodified captures retain independently observed 24/123/3
        # coordinates; they do not establish completion of a native session.
        assert case['expected'] == {'pitch': 24, 'x': 123, 'y': 3}
        if blurred:
            with pytest.raises(NativeEngineeringDriverError, match='网格'):
                _grid_calibration(capture.filter(ImageFilter.GaussianBlur(1.25)))
        else:
            actual = _grid_calibration(capture)
            assert {key: actual[key] for key in case['expected']} == case['expected']
            assert (actual['width'], actual['height']) == capture.size


@pytest.mark.parametrize('shape', ['blank', 'horizontal_dots', 'vertical_dots', 'unequal_axes',
                                 'short_columns', 'competing_phase', 'competing_pitch',
                                 'competing_harmonic'])
def test_visible_grid_calibration_rejects_insufficient_or_ambiguous_periodic_evidence(shape):
    from PIL import Image, ImageDraw
    from gxworks2.engineering_driver import NativeEngineeringDriverError, _grid_calibration

    capture = Image.new('RGB', (1100, 180), 'white')
    drawing = ImageDraw.Draw(capture)

    def lattice(first_x, first_y, *, x_pitch=24, y_pitch=24, columns=16, rows=4):
        for column in range(columns):
            for row in range(rows):
                drawing.point((first_x + column * x_pitch, first_y + row * y_pitch), fill='black')

    if shape == 'horizontal_dots':
        lattice(147, 27, rows=1)
    elif shape == 'vertical_dots':
        lattice(147, 27, columns=1)
    elif shape == 'unequal_axes':
        lattice(147, 27, y_pitch=20)
    elif shape == 'short_columns':
        lattice(147, 27, columns=7)
    elif shape in ('competing_phase', 'competing_pitch', 'competing_harmonic'):
        lattice(80, 27)
        if shape == 'competing_phase':
            lattice(563, 27)
        elif shape == 'competing_pitch':
            lattice(563, 25, x_pitch=20, y_pitch=20)
        else:
            # The periods have compatible phases, but the coarse right-hand
            # lattice is not an alias of the separate fine left-hand lattice.
            lattice(560, 27, x_pitch=48, y_pitch=48, columns=10, rows=3)
    with pytest.raises(NativeEngineeringDriverError, match='网格'):
        _grid_calibration(capture)


@pytest.mark.parametrize('hidden_x', [147, 627])
def test_visible_grid_calibration_explains_harmonics_despite_a_hidden_fine_neighbor(hidden_x):
    from PIL import Image, ImageDraw
    from gxworks2.engineering_driver import _grid_calibration

    capture = Image.new('RGB', (1200, 180), 'white')
    drawing = ImageDraw.Draw(capture)
    for column in range(1, 41):
        for row in range(1, 5):
            drawing.point((123 + column * 24, 3 + row * 24), fill='black')
    # Block and input text obscure different rows. The bottom dotted boundary
    # retains (147, 123): its 48px neighbor is visible, its 24px neighbor hidden.
    drawing.rectangle((241, 49, 509, 101), fill='white')
    drawing.rectangle((145, 97, 221, 101), fill='white')
    # A Q03UDV/en output caption also hides a fine vertical neighbour at the
    # right edge. Its boundary dot has only a horizontal fine neighbour; the
    # same visible 24px lattice still explains the apparent 48px rectangle.
    drawing.rectangle((hidden_x-2, 97, hidden_x+2, 101), fill='white')
    for x in range(123, 1110, 3):
        drawing.point((x, 123), fill='black')
    assert _grid_calibration(capture) == {
        'pitch': 24, 'x': 123, 'y': 3, 'width': 1200, 'height': 180}


def test_visible_grid_calibration_bounds_the_actual_wide_native_raster_transition():
    from PIL import Image
    from tools.offline_inputs import private_path
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError, _grid_calibration
    path = private_path('research/experiments/native-live-multitarget-20261018/q-en5-grid-observation-02/native-view.png')
    if not path.is_file():
        pytest.skip('private frozen wide native grid capture is unavailable')
    with Image.open(path) as capture:
        assert capture.size == (3111, 1580)
        grid = _grid_calibration(capture, include_lattice=True)
    assert {key: grid[key] for key in ('pitch', 'x', 'y', 'width', 'height', 'calibrated_x_limit')} == {
        'pitch': 24, 'x': 123, 'y': 3, 'width': 3111, 'height': 1580, 'calibrated_x_limit': 2092}
    assert NativeEngineeringDriver._graph_point(grid, 5, 1) == (243, 27)
    assert NativeEngineeringDriver._graph_point(grid, 18, 5) == (555, 123)
    with pytest.raises(NativeEngineeringDriverError, match='已校准的网格区域'):
        NativeEngineeringDriver._graph_point(grid, 84, 1)


def test_visible_grid_calibration_keeps_generated_operations_inside_the_proven_phase():
    from hypothesis import example, given, settings, strategies as st
    from PIL import Image, ImageDraw
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError, _grid_calibration

    @settings(max_examples=40, deadline=None)
    @example(pitch=24, origin_x=123, columns=82, shift=1)
    @given(pitch=st.integers(12, 32), origin_x=st.integers(80, 140),
           columns=st.integers(12, 42), shift=st.sampled_from([-1, 1]))
    def check(pitch, origin_x, columns, shift):
        origin_y = 3
        boundary = origin_x + (columns + 1) * pitch + shift
        capture = Image.new('RGB', (boundary + 14 * pitch, 180), 'white')
        drawing = ImageDraw.Draw(capture)
        for row in range(1, 5):
            for column in range(1, columns + 1):
                drawing.point((origin_x + column * pitch, origin_y + row * pitch), fill='black')
            for column in range(12):
                drawing.point((boundary + column * pitch, origin_y + row * pitch), fill='black')
        grid = _grid_calibration(capture)
        assert (grid['pitch'], grid['x'], grid['y']) == (pitch, origin_x, origin_y)
        assert grid['calibrated_x_limit'] == boundary - pitch
        assert NativeEngineeringDriver._graph_point(grid, 3.5, 2) == (
            round(origin_x + 3.5 * pitch), origin_y + 2 * pitch)
        with pytest.raises(NativeEngineeringDriverError, match='已校准的网格区域'):
            NativeEngineeringDriver._graph_point(grid, columns + 2, 2)
    check()


@pytest.mark.parametrize('damage', ['phase', 'vertical_phase', 'pitch', 'gap', 'overlap', 'short_left',
                                   'unshared_intrusion'])
def test_visible_grid_calibration_does_not_merge_unproven_phase_boundaries(damage):
    from PIL import Image, ImageDraw
    from gxworks2.engineering_driver import NativeEngineeringDriverError, _grid_calibration
    capture = Image.new('RGB', (1400, 180), 'white')
    drawing = ImageDraw.Draw(capture)
    left_columns = 8 if damage == 'short_left' else 16
    for row in range(1, 4 if damage == 'unshared_intrusion' else 5):
        for column in range(1, left_columns + 1):
            drawing.point((123 + column * 24, 3 + row * 24), fill='black')
    first = 123 + (left_columns + 1) * 24 + 1
    if damage == 'phase':
        first += 1
    elif damage == 'gap':
        first += 3 * 24
    elif damage == 'overlap':
        first = 123 + 8 * 24 + 2
    elif damage == 'short_left':
        first -= 2  # The safe side then contains only seven complete columns.
    for row in range(1, 5):
        for column in range(12):
            period = 20 if damage == 'pitch' else 24
            drawing.point((first + column * period,
                           3 + row * period + int(damage == 'vertical_phase')), fill='black')
    if damage == 'unshared_intrusion':
        for row in (4, 5):
            for column in range(1, 13):
                drawing.point((124 + column * 24, 3 + row * 24), fill='black')
    with pytest.raises(NativeEngineeringDriverError, match='网格'):
        _grid_calibration(capture)


@pytest.mark.parametrize('source,target', [((243, 27), (2092, 27)), ((2093, 27), (243, 27)),
                                         ((243, 27), (2091 + 2, 27 + 2))])
def test_native_graph_drag_rejects_crossing_the_calibrated_phase_before_pointer_delivery(monkeypatch, source, target):
    import sys
    from types import SimpleNamespace
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError
    monkeypatch.setitem(sys.modules, 'win32process', SimpleNamespace(GetWindowThreadProcessId=lambda handle: (11, 41)))
    driver = object.__new__(NativeEngineeringDriver)
    driver.session = SimpleNamespace(process_id=41)
    events = []
    driver._idle = driver._foreground = lambda: events.append('native_input_preparation')
    view = SimpleNamespace(handle=42, is_visible=lambda: True, is_enabled=lambda: True,
                           drag_mouse_input=lambda **kwargs: events.append('pointer_delivery'))
    grid = dict(pitch=24, x=123, y=3, width=3111, height=1580, calibrated_x_limit=2092)
    with pytest.raises(NativeEngineeringDriverError, match='拖拽'):
        driver._graph_drag(view, grid, source, target)
    assert events == []


def test_visible_grid_calibration_reads_generated_lattice_coordinates_through_native_style_occlusion():
    from hypothesis import example, given, settings, strategies as st
    from PIL import Image, ImageDraw
    from gxworks2.engineering_driver import _grid_calibration

    @settings(max_examples=40, deadline=None)
    @example(pitch=24, origin_x=123, origin_y=3, shape='corner', grey=0)
    @given(pitch=st.integers(8, 32), origin_x=st.integers(80, 140),
           origin_y=st.integers(3, 8), shape=st.sampled_from(['point', 'corner', 'square']),
           grey=st.integers(0, 160))
    def check(pitch, origin_x, origin_y, shape, grey):
        capture = Image.new('RGB', (900, 180), 'white')
        drawing = ImageDraw.Draw(capture)
        colour = (grey, grey, grey)
        for column in range(1, 17):
            for row in range(1, 5):
                x, y = origin_x + column * pitch, origin_y + row * pitch
                drawing.point((x, y), fill=colour)
                if shape != 'point':
                    drawing.point((x + 1, y), fill=colour)
                    drawing.point((x, y + 1), fill=colour)
                if shape == 'square':
                    drawing.point((x + 1, y + 1), fill=colour)
        # An opaque block hides different portions of adjacent grid rows.
        # Coordinates and obstruction are generated independently of the reader.
        drawing.rectangle((origin_x + 5 * pitch - 2, origin_y + 2 * pitch - 2,
                           origin_x + 7 * pitch + 2, origin_y + 3 * pitch + 2), fill='white')
        drawing.point((42, 12), fill='black')
        drawing.point((49, 18), fill='black')
        for x in range(45, 85, 3):
            drawing.point((x, 125), fill='black')
        actual = _grid_calibration(capture)
        assert actual == {'pitch': pitch, 'x': origin_x, 'y': origin_y,
                          'width': 900, 'height': 180}

    check()


@pytest.mark.parametrize('scenario', ['delayed_ready', 'wrong_index', 'wrong_name', 'wrong_type', 'empty_type'])
def test_visible_label_selection_waits_for_current_list_row_name_and_type_before_apply(scenario):
    from types import SimpleNamespace
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError

    driver = object.__new__(NativeEngineeringDriver)
    state = SimpleNamespace(index=0, name='OtherInst', data_type='OldFB', polls=0)
    events, applications = [], []
    target = (1, 'ScaleInstB', 'ScaleValue')
    final = {'delayed_ready': target, 'wrong_index': (0, 'ScaleInstB', 'ScaleValue'),
             'wrong_name': (1, 'OtherInst', 'ScaleValue'),
             'wrong_type': (1, 'ScaleInstB', 'OtherFB'),
             'empty_type': (1, 'ScaleInstB', '')}[scenario]

    def send(message, index, _unused):
        if message == 0x0186:  # LB_SETCURSEL schedules native selection updates.
            events.append(('select', index))
            return index
        assert message == 0x0188  # LB_GETCURSEL is read independently of the Edit fields.
        return state.index

    combo = object()
    labels = SimpleNamespace(item_texts=lambda: ['OtherInst', '+ScaleInstB'], send_message=send,
        notify_parent=lambda code: events.append(('notify', code)))
    controls = {6651: combo, 6650: labels,
                6660: SimpleNamespace(is_enabled=lambda: True, item_texts=lambda: ['ANY', 'Bit']),
                6639: SimpleNamespace(window_text=lambda: state.name),
                6643: SimpleNamespace(window_text=lambda: state.data_type),
                6654: SimpleNamespace(get_check_state=lambda: 0)}
    dialog = SimpleNamespace(child_window=lambda control_id, class_name: controls[control_id])
    driver._dialog = lambda title: dialog
    driver.ui = SimpleNamespace(_select_native_combo=lambda control, value:
        events.append(('filter_all', control is combo, value)))

    def wait(condition, message):
        for row in [(0, 'OtherInst', 'OldFB'), (1, 'ScaleInstB', ''), final]:
            state.index, state.name, state.data_type = row
            state.polls += 1
            observed = condition()
            if observed:
                return observed
        raise NativeEngineeringDriverError(message)

    driver._wait = wait

    class ApplyReached(Exception):
        pass

    def click(actual_dialog, control_id):
        assert actual_dialog is dialog and control_id == 1
        applications.append((state.index, state.name, state.data_type))
        # Stop at the external Apply boundary; no native controls are operated.
        raise ApplyReached

    driver._click = click
    if scenario == 'delayed_ready':
        with pytest.raises(ApplyReached):
            driver._choose_label('ScaleInstB', expected_type='ScaleValue')
        assert applications == [target]
    else:
        with pytest.raises(NativeEngineeringDriverError, match='名称或实例类型'):
            driver._choose_label('ScaleInstB', expected_type='ScaleValue')
        assert applications == []
    assert state.polls == 3
    assert events == [('filter_all', True, '<全部>'), ('filter_all', False, 'ANY'), ('select', 1), ('notify', 1)]


def _active_read_identity():
    return dict(process_id=42, window_handle=100, process_started='1234567', executable='C:/GX/GD2.exe',
                software_version='1.635.0.1', project_path='C:/Lab/Current.gxw', cpu_menu='FX3U/FX3UC')


@pytest.mark.parametrize('text', ['', 'Y0:=X0;\r\nD0:=1;\r', '(*中文\t🚀*)\nY0 := NOT X0;\n'])
def test_active_st_core_keeps_exact_current_control_text_and_source_binding(text):
    from gxw.active_context import read_active_st_text
    result = read_active_st_text(text, owner='Main', kind='program', identity=_active_read_identity(),
        document_handle=200, document_title='Main [PRG] 程序本体 [ST]', editor_handle=300)
    assert result['st_text'] == text and result['line_endings'] == 'native_control_exact'
    assert result['source_binding']['identity'] == _active_read_identity()
    assert result['source_binding']['editor_handle'] == 300 and not result['save_performed']
    assert result['compilation'] == result['execution'] == 'not_run'


@pytest.mark.parametrize('damage', ['version', 'project', 'owner', 'kind', 'editor', 'nul', 'surrogate'])
def test_active_st_core_rejects_wrong_document_identity_or_unreadable_text(damage):
    from gxw.active_context import read_active_st_text
    from gxw.models import GXWFormatError
    arguments = dict(text='Y0:=X0;', owner='Main', kind='program', identity=_active_read_identity(),
        document_handle=200, document_title='Main [PRG] 程序本体 [ST]', editor_handle=300)
    if damage == 'version': arguments['identity']['software_version'] = '1.0.0.0'
    elif damage == 'project': del arguments['identity']['project_path']
    elif damage == 'owner': arguments['owner'] = 'Another'
    elif damage == 'kind': arguments['kind'] = 'fb'
    elif damage == 'editor': arguments['editor_handle'] = True
    elif damage == 'nul': arguments['text'] = 'Y0\0:=X0;'
    elif damage == 'surrogate': arguments['text'] = '\ud800'
    with pytest.raises(GXWFormatError):
        read_active_st_text(**arguments)


@pytest.mark.parametrize('damage', [None, 'project', 'document'])
def test_explicit_label_export_retains_current_page_binding(tmp_path, monkeypatch, damage):
    import csv
    import io
    from pathlib import Path
    from types import SimpleNamespace
    from gxworks2 import engineering_driver as native
    buffer = io.StringIO(newline='')
    csv.writer(buffer, delimiter='\t', quoting=csv.QUOTE_ALL).writerows([
        ['Project'], ['类', '标签名', '数据类型', '常量', '软元件', '地址', '注释'],
        ['VAR_GLOBAL', 'Flag', 'BOOL', '', 'M100', '%MX0.100', 'Current table']])
    raw = buffer.getvalue().encode('utf-16')
    driver = object.__new__(native.NativeEngineeringDriver)
    identity = _active_read_identity()
    calls, inspections = [], []
    document = SimpleNamespace(handle=200, window_text=lambda: '全局标签设置 ' + ('Other' if damage == 'owner' else 'Global1'))
    def inspect():
        inspections.append(1)
        return {**identity, 'project_path': 'C:/Lab/Other.gxw'} if damage == 'project' and len(inspections) > 1 else identity
    driver.inspect = inspect
    driver._labels_page = lambda name, scope: document
    driver._active_document = lambda: SimpleNamespace(handle=201, window_text=document.window_text) if damage == 'document' else document
    driver._idle = lambda: None
    driver._transfers, driver.evidence_directory = 0, tmp_path
    driver.ui = SimpleNamespace(WRITE_CSV_COMMAND=101, READ_CSV_COMMAND=102, SAVE_PROJECT_COMMAND=103)
    driver._command = calls.append
    driver._confirm = lambda message, button: calls.append(('confirm', message, button))
    driver._dialog = lambda title: title
    def export(dialog, path):
        assert dialog == '写入至CSV文件'
        path.write_bytes(raw)
    driver._file_name = export
    driver._dialogs = lambda: []
    clock = iter(range(30))
    monkeypatch.setattr('gxworks2.engineering_labels.time', SimpleNamespace(monotonic=lambda: next(clock) / 5))
    def wait(predicate, message):
        for _ in range(10):
            value = predicate()
            if value:
                return value
        raise native.NativeEngineeringDriverError(message)
    driver._wait = wait
    driver.snapshot = lambda: pytest.fail('Reading a current label table must not save the project')
    if damage:
        with pytest.raises(native.NativeEngineeringDriverError):
            driver._export_label_csv('Global1', 'global')
    else:
        result = driver._export_label_csv('Global1', 'global')
        assert Path(result['export_file']).read_bytes() == raw
        assert result['identity'] == identity and result['document_handle'] == document.handle
    assert [value for value in calls if isinstance(value, int)] == [101]


@pytest.mark.parametrize('damage', [None, 'project', 'document', 'owner', 'controls', 'changed_text'])
def test_active_st_driver_reads_unsaved_body_without_replacing_or_saving_text(monkeypatch, damage):
    from types import SimpleNamespace
    from gxworks2 import engineering_driver as native
    driver = object.__new__(native.NativeEngineeringDriver)
    identity, inspections, reads = _active_read_identity(), [], []
    text = 'Result := InputValue;\r\n(*未保存 🚀*)\r'
    def inspect():
        inspections.append(1)
        return {**identity, 'process_started': 'new_process'} if damage == 'project' and len(inspections) > 1 else identity
    def current_text():
        reads.append(1)
        return text + 'Changed' if damage == 'changed_text' and len(reads) > 1 else text
    editor = SimpleNamespace(handle=300, control_id=lambda: 100, window_text=current_text)
    document = SimpleNamespace(handle=200,
        window_text=lambda: ('Another' if damage == 'owner' else 'Scale') + ' [FB] 程序本体 [ST]',
        descendants=lambda class_name: [editor, editor] if damage == 'controls' else [editor])
    driver.inspect = inspect
    driver._program_page = lambda name, kind, language: document
    driver._active_document = lambda: SimpleNamespace(handle=201, window_text=document.window_text) if damage == 'document' else document
    driver._idle = lambda: None
    driver.snapshot = lambda: pytest.fail('Current ST reads must not save')
    driver._command = lambda *args, **kwargs: pytest.fail('Current ST reads must not send an engineering command')
    if damage:
        with pytest.raises(native.NativeEngineeringDriverError):
            driver.read_st('Scale', kind='fb')
    else:
        result = driver.read_st('Scale', kind='fb')
        assert result['st_text'] == text and result['owner_kind'] == 'fb'
        assert result['source_binding']['editor_handle'] == 300
        assert not result['save_performed'] and len(reads) == 2


@pytest.mark.parametrize('change_owner', [False, True])
def test_st_page_waits_for_the_exact_editor_to_finish_loading(monkeypatch, change_owner):
    from gxworks2 import engineering_window as owner
    from types import SimpleNamespace
    from gxworks2 import engineering_driver as native
    driver = object.__new__(native.NativeEngineeringDriver)
    state = dict(reads=0, clock=0.0)
    complete = 'D0 := 123;'
    def text():
        state['reads'] += 1
        return complete[:-1] if state['reads'] < 3 else complete
    editor = SimpleNamespace(handle=300, control_id=lambda: 100, window_text=text)
    title = 'Main [PRG] Program Body [ST]'
    document = SimpleNamespace(handle=200, window_text=lambda: title,
        descendants=lambda **kwargs: [editor])
    driver._program_title = lambda *args: title
    driver._navigate = lambda *args: document
    driver._active_document = lambda: (SimpleNamespace(handle=201) if change_owner else document)
    monkeypatch.setattr(owner, 'time', SimpleNamespace(monotonic=lambda: state['clock']))
    def wait(predicate, message):
        for _ in range(12):
            value = predicate()
            if value:
                return value
            state['clock'] += .05
        raise native.NativeEngineeringDriverError(message)
    driver._wait = wait
    if change_owner:
        with pytest.raises(native.NativeEngineeringDriverError, match='活动对象已改变'):
            driver._program_page('Main', 'program', 'st')
    else:
        assert driver._program_page('Main', 'program', 'st') is document
        assert state['reads'] >= 6 and text() == complete


def _configuration_driver_witness(*, changed_m=False, changed_during_read=False):
    from types import SimpleNamespace
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError
    driver = object.__new__(NativeEngineeringDriver)
    state = SimpleNamespace(values={14069: '512', 14072: '7999', 14096: '3840', 14099: '7679'},
                            open=False, commands=[], clicks=[], edits=[])
    def child_window(*, control_id, class_name):
        assert class_name == 'Edit'
        def write(value):
            state.edits.append((control_id, value))
            state.values[control_id] = value
        return SimpleNamespace(window_text=lambda: state.values[control_id],
                               is_enabled=lambda: True, set_edit_text=write)
    dialog = SimpleNamespace(handle=222, child_window=child_window, is_enabled=lambda: True)
    driver.cpu, driver.timeout = 'FX3U/FX3UC', 1
    driver.inspect = _active_read_identity
    def command(value):
        assert value == 0x685
        state.commands.append(value)
        state.open = True
    driver._command = command
    driver._dialog = lambda title: dialog
    driver._dialogs = lambda: [dialog] if state.open else []
    def click(actual, control_id):
        assert actual is dialog and control_id in (1, 2)
        state.clicks.append(control_id)
        state.open = False
        if control_id == 1 and changed_m:
            state.values[14096] = '3841'
    driver._click = click
    def visible(page, **options):
        if changed_during_read:
            state.values[14069] = '777'
        return {'completed': True, 'page': page}
    driver._visible = visible
    def wait(predicate, message):
        value = predicate()
        if value:
            return value
        raise NativeEngineeringDriverError(message)
    driver._wait = wait
    driver.snapshot = lambda: pytest.fail('Configuration reads and edits must not save each step')
    driver._last_compile_run = {'compile_run_id': 'previous'}
    return driver, state


@pytest.mark.parametrize('raw', ['512', '-1', 'unknown'])
def test_active_configuration_driver_reads_actual_fields_and_cancels_without_save(raw):
    driver, state = _configuration_driver_witness()
    state.values[14069] = raw
    observed = driver.read_configuration()
    assert observed['status'] == ('available' if raw == '512' else 'partial')
    assert observed['raw_fields']['D'][0] == {'control_id': 14069, 'text': raw, 'enabled': True}
    assert observed['ranges']['D'] == ([512, 7999] if raw == '512' else None)
    assert observed['ranges']['M'] == [3840, 7679]
    assert observed['identity'] == _active_read_identity() and not observed['driver_saved']
    assert state.commands == [0x685] and state.clicks == [2] and not state.edits and not state.open
    assert driver._last_compile_run == {'compile_run_id': 'previous'}


@pytest.mark.parametrize('damage', ['field_changed', 'identity_changed', 'unsupported_cpu'])
def test_active_configuration_driver_does_not_guess_or_close_a_changed_target(damage):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver, state = _configuration_driver_witness(changed_during_read=damage == 'field_changed')
    if damage == 'unsupported_cpu':
        driver.cpu = 'LJ72GF15-T2'
        assert driver.read_configuration()['status'] == 'unavailable'
        assert not state.commands
    else:
        if damage == 'identity_changed':
            calls = []
            def inspect():
                calls.append(1)
                return {**_active_read_identity(), 'project_path': 'C:/Lab/Other.gxw'} if len(calls) > 1 else _active_read_identity()
            driver.inspect = inspect
        with pytest.raises(NativeEngineeringDriverError):
            driver.read_configuration()
        assert not state.clicks and state.open and not state.edits


@pytest.mark.parametrize('damage', [None, 'before_mismatch', 'unrequested_range_changed'])
def test_active_configuration_driver_preserves_other_fields_and_invalidates_old_compile(damage):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver, state = _configuration_driver_witness(changed_m=damage == 'unrequested_range_changed')
    plan = {'ranges': [{'device': 'D', 'before': [512, 7999], 'after': [600, 900]}]}
    if damage == 'before_mismatch':
        plan['ranges'][0]['before'][1] = 1234
    if damage:
        with pytest.raises(NativeEngineeringDriverError):
            driver._configure(plan)
        if damage == 'before_mismatch':
            assert not state.edits and not state.clicks
        else:
            assert state.edits == [(14069, '600'), (14072, '900')]
            assert driver._last_compile_run is None
    else:
        observed = driver._configure(plan)
        assert observed['completed'] and observed['configuration']['ranges'] == {'D': [600, 900], 'M': [3840, 7679]}
        assert state.edits == [(14069, '600'), (14072, '900')] and state.clicks == [1, 2]
        assert driver._last_compile_run is None and not state.open


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
@pytest.mark.parametrize('notice_kind', ['overlap', 'rebuild'])
@pytest.mark.parametrize('damage', [None, 'foreign_language', 'extra_message', 'changed_message'])
def test_configuration_notices_preserve_raw_text_and_accept_only_complete_localized_messages(
        language, newline, notice_kind, damage):
    from types import SimpleNamespace
    from gxw.native_locale import ui_text
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    driver, state = _configuration_driver_witness()
    driver.native_ui_language, driver.timeout = language, .01
    source_language = ('en' if language != 'en' else 'zh-CN') if damage == 'foreign_language' else language
    text = ui_text('allocation_' + ('overlap_format' if notice_kind == 'overlap' else 'rebuild_notice'),
                   source_language).replace('%1!s!', 'Counter')
    if damage == 'extra_message': text += '\nUnrelated native warning'
    if damage == 'changed_message': text = text[:-1] + '!'
    text = text.replace('\n', newline)
    notice = SimpleNamespace(handle=333, is_enabled=lambda: True)
    pending = dict(visible=False, clicks=0)
    original_click, original_dialogs = driver._click, driver._dialogs
    def click(dialog, control_id):
        if dialog is notice:
            assert pending['visible'] and control_id == 6
            pending['clicks'] += 1
            pending['visible'] = False
        else:
            original_click(dialog, control_id)
            if control_id == 1: pending['visible'] = True
    driver._click = click
    driver._dialogs = lambda: [notice] if pending['visible'] else original_dialogs()
    driver._dialog_text = lambda dialog: text if dialog is notice else ''
    plan = {'ranges': [{'device': 'D', 'before': [512, 7999], 'after': [600, 900]}]}
    if damage:
        with pytest.raises(NativeEngineeringDriverError, match='保留原生提示'):
            driver._configure(plan)
        assert pending == dict(visible=True, clicks=0)
        assert state.commands == [0x685] and state.clicks == [1]
    else:
        result = driver._configure(plan)
        assert result['notices'] == [text]
        assert pending == dict(visible=False, clicks=1)
        assert state.commands == [0x685, 0x685] and state.clicks == [1, 2]
        assert result['configuration']['ranges'] == {'D': [600, 900], 'M': [3840, 7679]}


@pytest.mark.parametrize('damage', [None, 'missing_run', 'revision', 'project', 'controls', 'output', 'text_changes'])
def test_current_compile_reader_requires_this_driver_run_and_unchanged_native_output(damage):
    from types import SimpleNamespace
    from gxworks2.engineering_driver import NativeEngineeringDriver
    driver = object.__new__(NativeEngineeringDriver)
    identity, output = _active_read_identity(), '已完成全部编译。Error: 0, Warning: 0, CheckWarning: 0'
    driver._last_compile_run = dict(identity=identity, output=output, revision=3,
        compile_run_id='run-a', summary_handle=111, completed=True)
    if damage == 'missing_run': driver._last_compile_run = None
    driver.inspect = lambda: {**identity, 'process_started': 'different'} if damage == 'project' else identity
    driver._idle = lambda: None
    driver._owned_control_handles = lambda *args, **kwargs: [111, 112] if damage == 'controls' else [111]
    reads = []
    def text():
        reads.append(1)
        return 'Historical output' if damage == 'output' or damage == 'text_changes' and len(reads) > 1 else output
    driver.ui = SimpleNamespace(_application=lambda session: SimpleNamespace(
        window=lambda **options: SimpleNamespace(window_text=text)))
    driver.session = object()
    driver.snapshot = lambda: pytest.fail('Compile-result inspection must not save')
    driver._command = lambda *args, **kwargs: pytest.fail('Inspection must not start or clear compilation')
    observed = driver.read_current_compile_result(revision=4 if damage == 'revision' else 3)
    assert observed['status'] == ('unavailable' if damage else 'observed')
    if not damage:
        assert observed['compile_run_id'] == 'run-a' and observed['output'] == output
        observed['identity']['project_path'] = 'mutated by consumer'
        assert driver._last_compile_run['identity'] == identity


@pytest.mark.parametrize('damage', [None, 'missing_verdict', 'failed', 'wrong_run', 'wrong_revision',
    'no_current_compile', 'stale_after_query', 'wrong_query', 'empty_rows', 'mixed_local', 'local_only',
    'instance_only', 'unsupported_local', 'missing_allocation_context', 'old_allocation_run', 'foreign_allocation_identity'])
def test_active_assignment_reader_requires_a_current_passed_compile_and_never_uses_saved_allocations(damage):
    from copy import deepcopy
    from types import SimpleNamespace
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError
    from gxw.live_assignments import prepare_live_assignments
    from tests.test_gxw_compiler import live_assignment_native_witness
    driver = object.__new__(NativeEngineeringDriver)
    global_ = dict(source='Global1.gh', name='Result', scope='global')
    local = dict(source='ScaleValue', name='OutValue', scope='local')
    instance = dict(source='POU_01', name='ScaleInst.OutValue', scope='instance')
    queries = [local] if damage == 'local_only' else [instance] if damage == 'instance_only' else (
        [global_, local] if damage == 'mixed_local' else [global_, {**local, 'name': 'Gain'}]
        if damage == 'unsupported_local' else [global_])
    current = dict(status='observed', revision=3, compile_run_id='run-a', identity=_active_read_identity())
    raw, _, _ = live_assignment_native_witness()
    context = prepare_live_assignments(raw, queries, revision=3, compile_run_id='run-a', identity=current['identity'])
    if damage == 'missing_allocation_context': context = None
    elif damage == 'old_allocation_run': context['compile_run_id'] = 'an-old-compile'
    elif damage == 'foreign_allocation_identity': context['identity']['process_id'] = 44
    current['identity']['path_binding'] = 'native_full_caption'  # Not part of the semantic identity.
    calls, reads = [], []
    def read(*, revision):
        reads.append(revision)
        if damage == 'no_current_compile':
            return dict(status='unavailable', reason='no_completed_compile_for_requested_revision')
        return {**current, 'compile_run_id': 'run-b'} if damage == 'stale_after_query' and len(reads) > 1 else deepcopy(current)
    driver.read_current_compile_result = read
    def cross_reference(plan):
        calls.append(deepcopy(plan))
        records = []
        for query in plan['queries']:
            entry = next(item for item in plan['allocation_context']['queries'] if item['query'] == query)
            name = entry['search_text']
            records.append(dict(query={**query, 'name': 'Foreign'} if damage == 'wrong_query' else deepcopy(query),
                search_text=name, summary='1件：软元件/标签"' + name + '"的交叉参照信息',
                visible_completion=dict(completed=True, page='交叉参照'), rows=[] if damage == 'empty_rows' else [
                dict(label=query['name'], address='D7999', program='MAIN', pou='POU_01', location='ST line 1', read_write='')]))
        return dict(cross_references=records, pid=42, revision=3)
    driver._show_assignments = cross_reference
    driver.path = SimpleNamespace(read_bytes=lambda: pytest.fail('Current assignments must not read saved GXW'))
    driver._last_saved = object()
    driver.snapshot = lambda: pytest.fail('Current assignment reads must not save')
    verdict = dict(status='passed', revision=3, compile_run_id='run-a')
    if damage == 'missing_verdict': verdict = None
    elif damage == 'failed': verdict['status'] = 'failed'
    elif damage == 'wrong_run': verdict['compile_run_id'] = 'another'
    elif damage == 'wrong_revision': verdict['revision'] = 2
    if damage == 'wrong_query':
        with pytest.raises(NativeEngineeringDriverError, match='本次查询不一致'):
            driver.read_assignments(queries, revision=3, compilation=verdict, allocation_context=context)
        return
    observed = driver.read_assignments(queries, revision=3, compilation=verdict, allocation_context=context)
    expected = ('current' if damage in (None, 'mixed_local', 'local_only', 'instance_only') else
                'partial' if damage == 'unsupported_local' else 'unavailable')
    assert observed['status'] == expected
    assert observed['allocation_status'] == ('current' if expected == 'current' else 'unverified')
    assert observed['owner_binding'] == 'source_and_compiled_component' and not observed['driver_saved']
    assert [row['query'] for row in observed['results']] == queries
    if damage in ('missing_verdict', 'failed', 'wrong_run', 'wrong_revision', 'no_current_compile',
                  'missing_allocation_context', 'old_allocation_run', 'foreign_allocation_identity'):
        assert not calls
    if damage == 'missing_allocation_context': assert not reads
    if damage == 'stale_after_query':
        assert 'cross_references' not in observed and observed['unverified_observation']['cross_references']
    if expected in ('current', 'partial'):
        assert calls == [{'queries': [entry['query'] for entry in context['queries'] if entry['status'] == 'prepared'],
                          'revision': 3, 'allocation_context': context}]
        assert observed['cross_references'][0]['rows'][0]['address'] == 'D7999'


@pytest.mark.parametrize('queries,revision', [([], 3), ([dict(source='Global1.gh', name='X', scope='fb')], 3),
    ([dict(source='Global1.gh', name='Bad\nName', scope='global')], 3),
    ([dict(source='Global1.gh', name=True, scope='global')], 3),
    ([dict(source='Global1.gh', name='Result', scope='global')] * 2, 3),
    ([dict(source='Global1.gh', name='Result', scope='global')], True),
    ([dict(source='Global1.gh', name='Result', scope='global')], -1)])
def test_active_assignment_reader_rejects_invalid_queries_before_any_native_read(queries, revision):
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError
    driver = object.__new__(NativeEngineeringDriver)
    driver.read_current_compile_result = lambda **kwargs: pytest.fail('Invalid queries must not inspect native UI')
    driver._show_assignments = lambda plan: pytest.fail('Invalid queries must not search native UI')
    with pytest.raises(NativeEngineeringDriverError):
        driver.read_assignments(queries, revision=revision)


@pytest.mark.parametrize('damage', [None, 'foreign_receipt', 'different_run', 'different_identity',
    'different_rows', 'different_summary', 'different_pid', 'different_revision', 'unconfirmed_page'])
def test_current_assignment_completion_reopens_only_confirmed_rows_without_changing_the_original_receipt(damage):
    from copy import deepcopy
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError
    from gxw.live_assignments import prepare_live_assignments
    from tests.test_gxw_compiler import live_assignment_native_witness
    raw, identity, native = live_assignment_native_witness()
    queries = [native['cross_references'][0]['query']]
    prepared = prepare_live_assignments(raw, queries, revision=3, compile_run_id='isolated-test-run', identity=identity)
    driver, reads, searches = object.__new__(NativeEngineeringDriver), [], []
    current = dict(status='observed', revision=3, compile_run_id='isolated-test-run',
                   identity={**identity, 'path_binding': 'native_full_caption'})
    def read(*, revision):
        reads.append(revision)
        value = deepcopy(current)
        if len(reads) > 2:
            if damage == 'different_run': value['compile_run_id'] = 'another-run'
            elif damage == 'different_identity': value['identity']['project_path'] = 'C:/different.gxw'
        return value
    def search(plan):
        searches.append(deepcopy(plan))
        value = deepcopy(native)
        if len(searches) > 1:
            record = value['cross_references'][0]
            if damage == 'different_rows': record['rows'][0]['address'] = 'D7998'
            elif damage == 'different_summary': record['summary'] += ' changed'
            elif damage == 'different_pid': value['pid'] += 1
            elif damage == 'different_revision': value['revision'] += 1
            elif damage == 'unconfirmed_page': record['visible_completion']['completed'] = False
        return value
    driver.read_current_compile_result, driver._show_assignments = read, search
    observation = driver.read_assignments(queries, revision=3,
        compilation=dict(status='passed', revision=3, compile_run_id='isolated-test-run'), allocation_context=prepared)
    original = deepcopy(observation)
    if damage == 'foreign_receipt': observation['cross_references'][0]['query']['name'] = 'foreign'
    if damage:
        with pytest.raises(NativeEngineeringDriverError): driver.show_assignments_completion(observation)
    else:
        completion = driver.show_assignments_completion(observation)
        assert completion['completed'] and searches[-1]['queries'] == queries
        assert len(searches) == 2 and reads == [3, 3, 3, 3]
        assert observation == original
    if damage in ('foreign_receipt', 'different_run', 'different_identity'):
        assert len(searches) == 1


@pytest.mark.parametrize('index', [0, 1, 2])
def test_current_assignment_reader_and_completion_preserve_qualified_native_queries(index):
    from copy import deepcopy
    from gxw.live_assignments import prepare_live_assignments
    from gxworks2.engineering_driver import NativeEngineeringDriver
    from tests.test_gxw_compiler import qualified_live_assignment_native_witness
    raw, compilation, original = qualified_live_assignment_native_witness()
    native = {**deepcopy(original), 'cross_references': [deepcopy(original['cross_references'][index])]}
    queries = [native['cross_references'][0]['query']]
    prepared = prepare_live_assignments(raw, queries, revision=compilation['revision'],
        compile_run_id=compilation['compile_run_id'], identity=compilation['identity'])
    driver, searches, reads = object.__new__(NativeEngineeringDriver), [], []
    def read(*, revision):
        reads.append(revision)
        return dict(status='observed', revision=compilation['revision'],
                    compile_run_id=compilation['compile_run_id'], identity=deepcopy(compilation['identity']))
    def search(plan):
        searches.append(deepcopy(plan))
        assert plan['allocation_context']['queries'][0]['search_text'] == native['cross_references'][0]['search_text']
        return deepcopy(native)
    driver.read_current_compile_result, driver._show_assignments = read, search
    driver.snapshot = lambda: pytest.fail('Qualified address reads and completion must not save')
    observation = driver.read_assignments(queries, revision=compilation['revision'],
        compilation=compilation, allocation_context=prepared)
    assert observation['status'] == 'current'
    assert observation['cross_references'] == native['cross_references']
    completion = driver.show_assignments_completion(observation)
    assert completion['completed'] and completion['source'] == queries[0]['source']
    assert len(searches) == 2 and reads == [compilation['revision']] * 4
    assert searches[1]['queries'] == queries and searches[1]['allocation_context'] == prepared


@pytest.mark.parametrize('index', [0, 1, 2])
def test_current_assignment_panel_sends_core_qualified_search_without_source_selector_or_save(monkeypatch, index):
    import sys
    from copy import deepcopy
    from types import SimpleNamespace
    from gxw.live_assignments import prepare_live_assignments
    from gxworks2 import engineering_driver as native
    from tests.test_gxw_compiler import qualified_live_assignment_native_witness
    raw, compilation, observed = qualified_live_assignment_native_witness()
    oracle = observed['cross_references'][index]
    query = oracle['query']
    prepared = prepare_live_assignments(raw, [query], revision=compilation['revision'],
        compile_run_id=compilation['compile_run_id'], identity=compilation['identity'])
    state = SimpleNamespace(search='', summary='', edits=[], clicks=[], positions=[])
    editor = SimpleNamespace(set_focus=lambda: None,
        set_edit_text=lambda text: (state.edits.append(text), setattr(state, 'search', text)))
    search = SimpleNamespace(handle=301, child_window=lambda **options: editor,
        window_text=lambda: state.search, notify_parent=lambda code: state.clicks.append(('notify', code)))
    summary = SimpleNamespace(handle=302, window_text=lambda: state.summary)
    def click():
        assert state.search == oracle['search_text']
        state.clicks.append(('search', state.search))
        state.summary = oracle['summary']
    button = SimpleNamespace(set_focus=lambda: None, click_input=click)
    def control(*, control_id, class_name):
        expected = {20102:('ComboBox',search),20304:('Edit',summary),20101:('Button',button)}
        assert control_id in expected, 'Qualified search must not manipulate the disabled source selector'
        expected_class, result = expected[control_id]
        assert class_name == expected_class
        return result
    floating = SimpleNamespace(handle=202, class_name=lambda:'XTPDockingPaneMiniWnd')
    grid = SimpleNamespace(handle=401, is_visible=lambda:True,
                           rectangle=lambda:SimpleNamespace(height=lambda:300))
    panel = SimpleNamespace(handle=201, class_name=lambda:'CrossReferencePanel', parent=lambda:floating,
                            child_window=control, descendants=lambda **options:[grid])
    driver = object.__new__(native.NativeEngineeringDriver)
    driver.session = SimpleNamespace(process_id=observed['pid'], window_handle=100)
    driver._open_cross_reference = driver._cross_reference_panel = lambda: panel
    driver._foreground = lambda handle: None
    driver._window = lambda: SimpleNamespace(rectangle=lambda:SimpleNamespace(
        left=0, bottom=1000, width=lambda:1600, height=lambda:1000))
    driver._visible = lambda page,**values: dict(completed=True,page=page,**deepcopy(values))
    def wait(predicate, message):
        value = predicate()
        assert value, message
        return value
    driver._wait = wait
    driver.snapshot = lambda: pytest.fail('An assignment panel query must not save')
    def send_message(handle, message, unused, value):
        assert (handle, message, value) == (summary.handle, 0x000C, '')
        state.summary = ''
    monkeypatch.setitem(sys.modules,'win32gui',SimpleNamespace(SendMessage=send_message,
        SetWindowPos=lambda *args:state.positions.append(args)))
    monkeypatch.setattr('gxworks2.engineering_assignments._accessible_grid_rows',lambda handle, **kwargs:deepcopy(oracle['rows']) if handle==grid.handle else pytest.fail('Foreign grid'))
    result = driver._show_assignments(dict(queries=[query],revision=compilation['revision'],allocation_context=prepared))
    record, = result['cross_references']
    assert state.edits == [oracle['search_text']]
    assert state.clicks == [('notify',5),('search',oracle['search_text'])]
    assert record['search_text'] == oracle['search_text'] and record['summary'] == oracle['summary']
    assert record['rows'] == oracle['rows'] and record['visible_completion']['completed']
    assert result['pid'] == observed['pid'] and result['revision'] == compilation['revision']
    assert len(state.positions) == 1


class _LiveNativePages:
    """Current-page double: every save/reopen is counted, no Windows calls."""
    def __init__(self, path, raw):
        from copy import deepcopy
        from tests.test_gxw_object_model import live_native_context_witness
        self.path = path
        self.path.write_bytes(raw)
        self.context = live_native_context_witness(raw)
        self.context['identity']['project_path'] = str(path)
        identity = self.context['identity']
        for row in self.context['tables'] + self.context['st']:
            row['source_binding']['identity'] = deepcopy(identity)
        for row in self.context['graphs']:
            row['identity'] = deepcopy(identity)
        self.context['configuration']['identity'] = deepcopy(identity)
        self.saved_output = raw
        self.saves, self.reopens, self.closes = 0, 0, 0
        self.calls, self._last_compile_run = [], {'revision': 1}

    def inspect(self):
        from copy import deepcopy
        return deepcopy(self.context['identity'])

    def _idle(self):
        pass

    def verify_project_codepage(self):
        self.calls.append(('verify_project_codepage',))
        return dict(status='matched', project_codepage=936)

    def _window(self):
        from types import SimpleNamespace
        state = self.context['inventory']
        def item(path, **kwargs):
            choices = {('全局标签',):state['globals'],('程序部件','结构体'):state['structures'],
                       ('程序部件','程序'):[row['owner'] for row in state['pous'] if row['owner_kind']=='program'],
                       ('程序部件','FB/FUN'):[row['owner'] for row in state['pous'] if row['owner_kind']=='fb']}
            return SimpleNamespace(children=lambda:[SimpleNamespace(text=lambda name=name:name) for name in choices[tuple(path)]])
        tree=SimpleNamespace(control_id=lambda:12051,is_visible=lambda:True,is_enabled=lambda:True,get_item=item)
        return SimpleNamespace(descendants=lambda **kwargs:[tree])

    def _navigate(self, paths, pattern):
        from types import SimpleNamespace
        owner=paths[0][2]
        row=next(row for row in self.context['inventory']['pous'] if row['owner']==owner)
        title=owner+(' [PRG]' if row['owner_kind']=='program' else ' [FB]')+' 程序本体 '+(
            '[ST]' if row['language']=='st' else '[结构化梯形图/FBD]')
        return SimpleNamespace(window_text=lambda:title)

    def read_labels(self,name,scope,**kwargs):
        from copy import deepcopy
        self.calls.append(('read_labels',name,scope))
        return deepcopy(next(row for row in self.context['tables'] if (row['owner'],row['owner_kind'])==(name,scope)))

    def read_st(self,name,kind='program'):
        from copy import deepcopy
        self.calls.append(('read_st',name,kind))
        return deepcopy(next(row for row in self.context['st'] if (row['owner'],row['owner_kind'])==(name,kind)))

    def read_configuration(self):
        from copy import deepcopy
        self.calls.append(('read_configuration',))
        return deepcopy(self.context['configuration'])

    def _wait(self,predicate,message):
        import time
        until=time.monotonic()+2
        while time.monotonic()<until:
            result=predicate()
            if result:
                return result
            time.sleep(.01)
        raise AssertionError(message)

    def snapshot(self, *, on_dispatch=None):
        self.saves+=1
        if on_dispatch is not None:
            on_dispatch(dict(send_attempted=True, save_dispatched=True, dispatch_status='returned'))
        self.path.write_bytes(self.saved_output)
        return self.saved_output

    def _visible(self,page,**kwargs):
        self.calls.append(('visible',page))
        return dict(completed=True,page=page)

    def _labels_page(self,name,scope):
        self.calls.append(('labels_page',name,scope))

    def _program_page(self,name,kind='program',language='fbd'):
        self.calls.append(('program_page',name,kind,language))

    def _csv_labels(self,name,scope,rows):
        import csv,io
        from gxw.active_context import read_active_labels_csv
        from gxw.declarations import CLASS_CODES
        before=self.read_labels(name,scope)
        classes={value:key for key,value in CLASS_CODES.items()}
        buffer=io.StringIO(newline='')
        writer=csv.writer(buffer,delimiter='\t',lineterminator='\r\n')
        writer.writerow([before['csv_title']]);writer.writerow(before['csv_columns'])
        for row in rows:
            fields={'类':classes[row['class_code']],'标签名':row['name'],'数据类型':row['data_type'],
                '常量':row.get('csv_constant',''),'注释':row['comment'],
                '软元件':row.get('device',''),'地址':row.get('iec_address','')}
            writer.writerow([fields[column] for column in before['csv_columns']])
        binding=before['source_binding']
        updated=read_active_labels_csv(buffer.getvalue().encode('utf-16'),owner=name,scope=scope,
            identity=self.inspect(),document_handle=binding['document_handle'],document_title=binding['document_title'])
        self.context['tables']=[updated if (row['owner'],row['owner_kind'])==(name,scope) else row for row in self.context['tables']]
        return self._visible(name+' 标签表')

    def close(self):
        self.closes+=1


def _live_pages_wrapper(tmp_path):
    from copy import deepcopy
    from gxworks2.live_driver import LiveEngineeringDriver
    from gxw.project_templates import blank_fbd_baseline
    from types import SimpleNamespace
    raw=blank_fbd_baseline('FX3U/FX3UC')
    pages=_LiveNativePages(tmp_path/'controlled.gxw',raw)
    adapter=SimpleNamespace(_pending={},pending_operations=lambda:[],
        read_graph=lambda driver,owner,kind:deepcopy(next(row for row in pages.context['graphs']
            if (row['owner'],row['owner_kind'])==(owner,kind))))
    return raw,pages,LiveEngineeringDriver(pages,raw,'FX3U',adapter)


@pytest.mark.parametrize('explicit', [False, True])
def test_live_label_reads_share_pending_recovery_without_replaying_the_import(tmp_path, explicit):
    import json
    from types import SimpleNamespace
    from gxw.project_templates import blank_fbd_baseline
    from gxworks2.active_document import ActiveDocumentAdapter
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    from gxworks2.live_driver import LiveEngineeringDriver
    raw = blank_fbd_baseline('FX3U')
    pages = _LiveNativePages(tmp_path / 'pending-label-read.gxw', raw)
    adapter = ActiveDocumentAdapter('never-launched.exe')
    pages._label_reader = ActiveDocumentAdapter('superseded.exe') if explicit else adapter
    live = LiveEngineeringDriver(pages, raw, 'FX3U', adapter=adapter if explicit else None)
    assert live.adapter is pages._label_reader is adapter
    state = SimpleNamespace(finished=False)
    response = dict(status='observed', protocol_version=1, native_version='1.635.0.1',
                    process_id=pages.inspect()['process_id'])
    child = SimpleNamespace(returncode=0, poll=lambda: 0 if state.finished else None,
                            communicate=lambda: (json.dumps(response), ''))
    adapter._pending['label-read'] = (child, dict(operation='read_active_labels', process_id=response['process_id']))
    with pytest.raises(NativeEngineeringDriverError, match='未确认'):
        live._ready()
    assert pages.calls == []
    assert live.pending_operations() == [dict(native_task_id='label-read', status='running', operation='read_active_labels')]
    state.finished = True
    assert live.pending_operations() == [dict(native_task_id='label-read', status='ready_to_reconcile',
        operation='read_active_labels', native_observation=response)]
    assert adapter._pending == {}
    live._ready()
    assert pages.calls == [('verify_project_codepage',)]
    assert pages.saves == pages.reopens == 0 and pages.path.read_bytes() == raw


@pytest.mark.parametrize('selected,saved,observed,accepted', [
    ('L02', 'L02', 'L02/L02-P', True),
    ('L02/L02-P', 'L02', 'L02/L02-P', True),
    ('FX3U', 'FX3U/FX3UC', 'FX3U/FX3UC', True),
    ('L02/L02-P', 'L02', 'L06/L06-P', False),
    ('Q03UDV', 'L02', 'L02/L02-P', False),
])
def test_live_driver_binds_saved_cpu_to_its_actual_merged_menu_without_rewriting_source(
        tmp_path, monkeypatch, selected, saved, observed, accepted):
    from types import SimpleNamespace
    from gxworks2.live_driver import LiveEngineeringDriver
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    import gxw.fb_authoring
    import gxw.native_write
    raw = b'isolated constructor binding witness'
    path = tmp_path / 'owned.gxw'
    path.write_bytes(raw)
    identity = dict(process_id=11, window_handle=12, process_started='13', executable='GD2.exe',
        software_version='1.635.0.1', project_path=str(path), cpu_menu=observed)
    monkeypatch.setattr(gxw.fb_authoring, '_context', lambda value, require_fbd: ({'cpu': saved},))
    monkeypatch.setattr(gxw.native_write, 'native_task_scope_context', lambda value: {'status': 'available'})
    driver = SimpleNamespace(path=path, inspect=lambda: dict(identity))
    if accepted:
        live = LiveEngineeringDriver(driver, raw, selected, adapter=object())
        assert live.identity['cpu_menu'] == observed
        assert live.baseline == raw
    else:
        with pytest.raises(NativeEngineeringDriverError, match='CPU'):
            LiveEngineeringDriver(driver, raw, selected, adapter=object())
    assert path.read_bytes() == raw


def test_live_driver_reads_actual_tree_and_pages_without_save_reopen_or_saved_source_fallback(tmp_path):
    from gxw.live_context import live_context_state
    raw,pages,live=_live_pages_wrapper(tmp_path)
    context=live.read_context()
    assert live_context_state(context)==live_context_state(pages.context)
    assert pages.saves==pages.reopens==pages.closes==0 and pages.path.read_bytes()==raw
    assert ('read_configuration',) in pages.calls
    live.close()
    assert pages.closes==0
    with pytest.raises(RuntimeError,match='已关闭'):
        live.read_context()
    assert live.rebind(context['identity'])['identity']==context['identity']
    pages.context['identity']['process_started']='different-start'
    with pytest.raises(RuntimeError,match='重新绑定'):
        live.rebind(context['identity'])


def test_live_context_reprojects_original_native_grid_with_all_current_named_types(tmp_path):
    from gxw.active_context import read_active_label_grid
    raw, pages, live = _live_pages_wrapper(tmp_path)
    table = next(row for row in pages.context['tables'] if row['owner_kind'] == 'program')
    binding = table['source_binding']
    grid = dict(columns=['类','标签名','数据类型','\u3000','常量','注释'],
                rows=[['VAR','Flag','Bit','','','Current native cells']])
    current = read_active_label_grid(grid, owner=table['owner'], scope='program',
        identity=binding['identity'], document_handle=binding['document_handle'],
        document_title=binding['document_title'])
    # Converted framing is derived data; the second pass must start from
    # the native cells so named-type collisions cannot be lost.
    current['raw_bytes'] = b'not an observation'
    pages.context['tables'] = [current if row is table else row for row in pages.context['tables']]
    context = live.read_context()
    actual = next(row for row in context['tables'] if row['owner_kind'] == 'program')
    assert actual['native_grid'] == grid and actual['declarations'][0]['data_type'] == 'BOOL'
    assert actual['read_transport'] == 'native_label_table'
    assert pages.saves == pages.reopens == 0 and pages.path.read_bytes() == raw


def test_live_driver_applies_current_labels_and_saves_exactly_once_at_explicit_phase(tmp_path):
    from gxw.live_context import prepare_live_command,verify_live_change
    from gxw.fb_authoring import _context
    from gxw.declarations import edit_declarations,serialize_declarations
    from tests.test_gxw_object_model import replay_source_records
    raw,pages,live=_live_pages_wrapper(tmp_path)
    before=live.read_context()
    name=before['inventory']['globals'][0]
    command=dict(operation='edit_global_labels',source_name=name,declarations=[dict(name='LiveFlag',data_type='BOOL',device='M50')])
    prepared=prepare_live_command(raw,before,command,device_profile='FX3U')
    applied=live.apply(command,prepared)
    after=live.read_context()
    assert verify_live_change(before,after,command,prepared)['status']=='matched'
    assert applied['status']=='matched' and pages._last_compile_run is None
    assert pages.saves==0 and pages.path.read_bytes()==raw
    doc=_context(raw)[3].declarations[name+'.gh']
    rows=[{key:row[key] for key in ('name','data_type','device','iec_address')} for row in prepared['plan']['rows']]
    pages.saved_output=replay_source_records(raw,{name+'.gh':serialize_declarations(edit_declarations(doc,upserts=rows))})
    saved=live.stage_save(after)
    assert pages.saves==1 and saved['data']==pages.saved_output and saved['validation']['status']=='matched'
    assert saved['_stage_context']['tables'] and not live.read_disk_change()
    live.show_completion(command,prepared)
    assert pages.calls[-2:]==[('labels_page',name,'global'),('visible','全局标签表')]
    live.show_completion({'operation':'save'}, {})
    assert pages.calls[-2:]==[('labels_page',name,'global'),('visible','全局标签表')]


@pytest.mark.parametrize('damage',['external_source','disk','pending','identity'])
def test_live_stage_save_refuses_unconfirmed_changes_before_dispatch(tmp_path,damage):
    from tests.test_gxw_object_model import replay_source_records
    from gxw.fb_authoring import _context
    raw,pages,live=_live_pages_wrapper(tmp_path)
    before=live.read_context()
    if damage=='external_source':
        wires=pages.context['graphs'][0]['networks'][0]['wires']
        wires.append(dict(index=len(wires),start=[2,3],end=[8,3],selected=False))
    elif damage=='disk':
        streams=_context(raw)[1];parameter=bytearray(streams['Param.wpa']);parameter[-1]^=1
        pages.path.write_bytes(replay_source_records(raw,{'Param.wpa':parameter}))
    elif damage=='pending':
        live.adapter._pending['actual-child-handle']=object()
    else:
        pages.context['identity']['process_started']='changed'
    with pytest.raises(RuntimeError):
        live.stage_save(before)
    assert pages.saves==0


@pytest.mark.parametrize('failure', ['idle', 'foreground', 'target', 'denied', 'timeout', 'after_send'])
def test_snapshot_dispatch_callback_records_actual_send_after_target_checks_without_claiming_completion(
        monkeypatch, failure):
    import sys
    from types import SimpleNamespace
    from gxworks2.engineering_driver import NativeEngineeringDriver
    class Stopped(RuntimeError):
        pass
    class NativeSendError(Exception):
        def __init__(self, code):
            super().__init__(code, 'Save send failed')
            self.winerror = code
    driver = object.__new__(NativeEngineeringDriver)
    driver.session = SimpleNamespace(window_handle=9001)
    driver.ui = SimpleNamespace(SAVE_PROJECT_COMMAND=51)
    events, receipts = [], []
    def check(name):
        events.append(name)
        if failure == name:
            raise Stopped(name)
    driver._idle = lambda: check('idle')
    driver.verify_project_codepage = lambda: driver._idle()
    driver._foreground = lambda: check('foreground')
    driver.inspect = lambda: check('target')
    def send(*args):
        assert args == (9001, 0x0111, 51, 0, 2, 1500)
        events.append('send')
        if failure in ('denied', 'timeout'):
            raise NativeSendError(5 if failure == 'denied' else 1460)
    def dispatched(value):
        events.append('callback')
        receipts.append(value)
    def wait(*args):
        events.append('wait')
        raise Stopped('before a saved-byte receipt')
    driver._wait = wait
    monkeypatch.setitem(sys.modules, 'win32gui', SimpleNamespace(SendMessageTimeout=send))
    monkeypatch.setitem(sys.modules, 'pywintypes', SimpleNamespace(error=NativeSendError))
    with pytest.raises((Stopped, NativeSendError)):
        driver.snapshot(on_dispatch=dispatched)
    if failure in ('idle', 'foreground', 'target'):
        assert not receipts and 'send' not in events
    else:
        assert events[:5] == ['idle', 'foreground', 'target', 'send', 'callback']
        assert len(receipts) == 1 and receipts[0]['send_attempted'] is True
        assert receipts[0]['save_dispatched'] is (failure != 'denied')
        assert receipts[0]['dispatch_status'] == dict(denied='rejected', timeout='timed_out', after_send='returned')[failure]
        assert 'completed' not in receipts[0] and not hasattr(driver, '_last_saved')


def test_saved_file_observation_does_not_save_or_adopt_bytes(tmp_path, monkeypatch):
    from gxworks2 import engineering_archive as owner
    from itertools import count
    from types import SimpleNamespace
    from gxw.project_templates import blank_fbd_baseline
    from gxworks2 import engineering_driver as native
    driver = object.__new__(native.NativeEngineeringDriver)
    raw = blank_fbd_baseline('FX3U')
    driver.path = tmp_path / 'observed.gxw'
    driver.path.write_bytes(raw)
    driver._last_saved = b'previous confirmed source'
    driver.inspect = lambda: None
    driver._dialogs = lambda: []
    driver._window = lambda: SimpleNamespace(is_enabled=lambda: True)
    driver.snapshot = lambda *a, **k: pytest.fail('Observation must not save')
    driver._command = lambda *a, **k: pytest.fail('Observation must not issue UI commands')
    ticks = count()
    monkeypatch.setattr(owner, 'time', SimpleNamespace(monotonic=lambda: next(ticks) / 4))
    def wait(predicate, message):
        for _ in range(8):
            result = predicate()
            if result:
                return result
        pytest.fail(message)
    driver._wait = wait
    assert driver.read_saved_project() == raw
    assert driver._last_saved == b'previous confirmed source'


def test_save_verification_retry_reads_late_bytes_without_another_save(monkeypatch):
    from gxworks2 import engineering_driver as native
    driver = object.__new__(native.NativeEngineeringDriver)
    driver.timeout = 2
    events = []
    driver.snapshot = lambda: events.append('save') or b'initial response'
    driver.read_saved_project = lambda: events.append('read') or b'late native completion'
    def verify(raw):
        events.append(('verify', raw))
        if raw != b'late native completion':
            raise ValueError('original Save still finishing')
    monkeypatch.setattr(native.time, 'sleep', lambda *args: None)
    assert driver._save_until(verify) == b'late native completion'
    assert events == ['save', ('verify', b'initial response'), 'read', ('verify', b'late native completion')]


def test_batch_explicit_save_is_dispatched_only_by_the_driver_once(tmp_path):
    from gxw.project_templates import blank_fbd_baseline
    from gxworks2.engineering_driver import NativeEngineeringDriver
    from application.native_engineering import NativeEngineeringSession
    raw = blank_fbd_baseline('FX3U')
    driver = object.__new__(NativeEngineeringDriver)
    driver.path = tmp_path / 'single-save.gxw'
    driver.path.write_bytes(raw)
    driver._last_saved = raw
    driver.inspect = lambda: _VisibleDriver(raw).inspect()
    driver.verify_project_codepage = lambda: None
    reads, saves = [], []
    driver.read_saved_project = lambda: reads.append(True) or raw
    driver.snapshot = lambda **kwargs: saves.append(True) or raw
    driver.verify_saved_source = lambda current: None
    driver._visible = lambda page, **kwargs: dict(completed=True, page=page)
    driver.close = lambda: None
    result = NativeEngineeringSession(driver, 'FX3U').run([{'operation': 'save'}], baseline=raw)
    assert result['passed'], result.get('error')
    assert saves == [True] and len(reads) == 3


def test_batch_current_source_guard_rejects_unsaved_foreign_edits_without_saving(tmp_path):
    from gxworks2.engineering_driver import NativeEngineeringDriver
    raw, pages, live = _live_pages_wrapper(tmp_path)
    pages.cpu = 'FX3U'
    assert NativeEngineeringDriver.verify_saved_source(pages, raw)['status'] == 'matched'
    wires = pages.context['graphs'][0]['networks'][0]['wires']
    wires.append(dict(index=len(wires), start=[2, 3], end=[8, 3], selected=False))
    with pytest.raises(ValueError, match='graph|source|Graph'):
        NativeEngineeringDriver.verify_saved_source(pages, raw)
    assert pages.saves == pages.reopens == 0 and pages.path.read_bytes() == raw


@pytest.mark.parametrize('damage', [None, 'unconfirmed', 'external_file', 'reopened_file', 'st_source'])
def test_reopen_only_reads_the_confirmed_file_and_never_saves(tmp_path, monkeypatch, damage):
    from types import SimpleNamespace
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError
    driver = object.__new__(NativeEngineeringDriver)
    driver.path = tmp_path / 'owned.gxw'
    raw, changed = b'confirmed owned source', b'unconfirmed changed source'
    driver._last_saved = None if damage == 'unconfirmed' else raw
    state = dict(open=True, reads=0)
    commands = []
    driver._idle = driver.inspect = lambda: None
    def read():
        state['reads'] += 1
        return changed if damage == 'external_file' or damage == 'reopened_file' and state['reads'] > 1 else raw
    driver.read_saved_project = read
    driver.snapshot = lambda **kwargs: pytest.fail('Reopen must not dispatch Save')
    def command(value, **kwargs):
        commands.append((value, kwargs))
        state['open'] = value != 0x401
    driver._command = command
    driver._frame_identity = lambda: SimpleNamespace(project_open=state['open'])
    driver._dialogs = lambda: []
    driver._dialog = lambda name: name
    driver._file_name = lambda dialog, path: None
    driver._window = lambda: SimpleNamespace(window_text=lambda: str(driver.path))
    def wait(predicate, message):
        if not predicate():
            raise NativeEngineeringDriverError(message)
        return True
    driver._wait = wait
    driver._visible = lambda *args, **kwargs: dict(completed=True)
    def saved_programs(value):
        assert value == raw
        return {'programs': [{'name': 'Original', 'st_text': 'D0:=1;'}]}
    monkeypatch.setattr('gxw.fb_authoring.st_program_snapshot', saved_programs)
    driver.read_st = lambda name: {'st_text': 'D0:=2;' if damage == 'st_source' else 'D0:=1;'}
    if damage:
        with pytest.raises(NativeEngineeringDriverError, match='重新打开|冷重开'):
            driver.reopen()
    else:
        assert driver.reopen() == raw
    dispatched = damage not in ('unconfirmed', 'external_file')
    assert commands == ([(0x401, {}), (0x400, {'project': False})] if dispatched else [])
    assert state['reads'] == (2 if dispatched else 1)


def test_live_stage_save_status_is_read_only_and_cannot_promote_bytes_without_native_dispatch(tmp_path, monkeypatch):
    from gxworks2.engineering_driver import NativeEngineeringDriverError
    raw, pages, live = _live_pages_wrapper(tmp_path)
    before = live.read_context()
    monkeypatch.setattr(pages, 'snapshot', lambda **kwargs: raw)
    operation_id = 'c' * 32
    with pytest.raises(NativeEngineeringDriverError, match='发送记录'):
        live.stage_save(before, operation_id=operation_id)
    monkeypatch.setattr(live, '_ready', lambda: pytest.fail('Save status must not inspect native state'))
    monkeypatch.setattr(live, 'inspect', lambda: pytest.fail('Save status must not inspect native state'))
    progress = live.stage_save_status(operation_id)
    assert progress['save_dispatched'] is False and progress['snapshot_returned'] is True
    assert progress['status'] == 'rejected' and pages.saves == 0
    progress['save_dispatched'] = True
    assert live.stage_save_status(operation_id)['save_dispatched'] is False
    assert live.stage_save_status('d' * 32)['status'] == 'unavailable'


@pytest.mark.parametrize('stage_save,commit',[(False,True),(False,False),(True,True)])
def test_live_task_append_reopens_committed_cell_without_implicit_save(monkeypatch,stage_save,commit):
    from gxworks2 import engineering_programs as owner
    from types import SimpleNamespace
    import sys
    import gxworks2.engineering_driver as module
    from gxworks2.engineering_driver import NativeEngineeringDriver,NativeEngineeringDriverError
    state=SimpleNamespace(edit='',committed='',editing=False,keys=[],saves=0,popup=True)
    editor=SimpleNamespace(handle=200,control_id=lambda:200,is_visible=lambda:state.editing,
        window_text=lambda:state.edit,set_focus=lambda:None)
    grid=SimpleNamespace(is_visible=lambda:True,is_enabled=lambda:True,set_focus=lambda:None,
        descendants=lambda **kwargs:[editor])
    document=SimpleNamespace(window_text=lambda:'任务登录 Task_01',descendants=lambda **kwargs:[grid])
    tree_item=SimpleNamespace(elem=41,ensure_visible=lambda:None,
        text=lambda:'Task_01',children=lambda:[],
        client_rect=lambda:SimpleNamespace(left=2,top=3,bottom=25))
    resource_item=SimpleNamespace(text=lambda:'MAIN',children=lambda:[tree_item])
    bucket_item=SimpleNamespace(text=lambda:'执行程序',children=lambda:[resource_item])
    task_root=SimpleNamespace(children=lambda:[bucket_item])
    tree=SimpleNamespace(handle=12051,control_id=lambda:12051,is_visible=lambda:True,is_enabled=lambda:True,
        set_focus=lambda:None,get_item=lambda *a,**k:task_root,send_message=lambda *a:41)
    window=SimpleNamespace(descendants=lambda **kwargs:[tree])
    def keys(value,**kwargs):
        state.keys.append(value)
        if value=='t':
            state.popup=False
        elif value.endswith('{F2}'):
            state.editing=True;state.edit=state.committed
        elif value=='{TAB}':
            state.committed=state.edit if commit else '';state.editing=False
        elif value=='{ESC}':
            state.editing=False
    def send(handle,message,wparam,lparam):
        if message==0x00C2:state.edit=lparam
        return 1
    native=SimpleNamespace(IsWindowVisible=lambda handle:state.popup if handle==81 else True,GetClassName=lambda handle:'XTPPopupBar',
        EnumWindows=lambda callback,unused:callback(81,None),GetForegroundWindow=lambda:81,SendMessage=send)
    monkeypatch.setitem(sys.modules,'win32gui',native)
    monkeypatch.setitem(sys.modules,'win32process',SimpleNamespace(GetWindowThreadProcessId=lambda handle:(22,701)))
    monkeypatch.setitem(sys.modules,'pywinauto.keyboard',SimpleNamespace(send_keys=keys))
    monkeypatch.setattr(owner,'_post_native_client_click',lambda *a,**k:None)
    driver=object.__new__(NativeEngineeringDriver)
    driver.session=SimpleNamespace(process_id=701,window_handle=9001)
    driver.ui=SimpleNamespace(_select_native_tree_item=lambda *a:None,_activate_native_document=lambda *a:None)
    driver._window=lambda:window;driver._active_document=lambda:document
    driver._idle=driver._foreground=lambda:None;driver.inspect=_active_read_identity
    driver._wait=lambda predicate,message:predicate() or (_ for _ in ()).throw(AssertionError(message))
    driver._visible=lambda page,**kwargs:dict(completed=True,page=page)
    def save_until(verify):state.saves+=1
    driver._save_until=save_until
    plan=dict(source_name='LiveProgram',resource='MAIN',task='Task_01',line=2,
        tasks=[dict(resource='MAIN',task='Task_01',programs=[dict(name='POU_01',line=1)])])
    if not commit:
        with pytest.raises(NativeEngineeringDriverError,match='提交后实读'):
            driver._bind_program_task(plan,stage_save=stage_save)
        assert state.saves==0
    else:
        result=driver._bind_program_task(plan,stage_save=stage_save)
        assert state.committed=='LiveProgram' and state.saves==int(stage_save)
        if not stage_save:
            assert sum(key.endswith('{F2}') for key in state.keys)==2
            assert result['task_append_confirmation']['origin']=='native_task_cell_committed_readback'
            assert result['expected_tasks'][0]['programs'][-1]==dict(name='LiveProgram',line=2)


def test_live_graph_driver_reprepares_semantic_target_after_native_reconstruction_without_save(tmp_path):
    from copy import deepcopy
    from types import SimpleNamespace
    from tests.test_gxw_object_model import live_graph_context_witness
    from gxworks2.live_driver import LiveEngineeringDriver
    from gxw.live_context import prepare_live_command,verify_live_change
    raw,current=live_graph_context_witness()
    pages=_LiveNativePages(tmp_path/'graph.gxw',raw)
    pages.context['graphs']=deepcopy(current['graphs'])
    pages.context['graphs'][0]['identity']=pages.inspect()
    state=SimpleNamespace(rebuild=False,mutations=[])
    def read_graph(driver,owner,kind):
        actual=pages.context['graphs'][0]
        if state.rebuild:
            actual['networks'][0]['nodes'].reverse()
            for index,node in enumerate(actual['networks'][0]['nodes']):
                node.update(index=index,address=0x80000+200*index,selected=False)
            actual.update(data_id=456,document_handle=300)
            state.rebuild=False
        return deepcopy(actual)
    def apply_graph_action(driver,observed,prepared):
        target=prepared['target']['semantic']
        assert target['symbol']=='X0' and prepared['action']['node']=='b0:n1'
        node=pages.context['graphs'][0]['networks'][0]['nodes'][1]
        assert node['name']=='X0'
        node['name']='X1'
        state.mutations.append(prepared['action']['action'])
        return read_graph(driver,prepared['owner'],prepared['owner_kind'])
    adapter=SimpleNamespace(_pending={},read_graph=read_graph,apply_graph_action=apply_graph_action,pending_operations=lambda:[])
    live=LiveEngineeringDriver(pages,raw,'FX3U',adapter)
    before=live.read_context()
    command=dict(operation='edit_fbd_graph',caller=before['graphs'][0]['owner']+'.Program.pou',
        actions=[dict(action='rebind_terminal',network=0,node='b0:n0',symbol='X1')])
    prepared=prepare_live_command(raw,before,command,device_profile='FX3U')
    state.rebuild=True
    assert live.apply(command,prepared)['status']=='matched'
    after=live.read_context()
    assert verify_live_change(before,after,command,prepared)['status']=='matched'
    assert state.mutations==['rebind_terminal'] and pages.saves==pages.reopens==0
    live.show_completion(command,prepared)
    assert pages.calls[-2:]==[('program_page',prepared['owner'],'program','fbd'),('visible',prepared['owner']+' FBD 编辑器')]


@pytest.mark.parametrize('damage',[None,'fields','identity'])
def test_live_configuration_completion_closes_only_the_verified_unchanged_dialog(tmp_path,damage):
    from copy import deepcopy
    from types import SimpleNamespace
    _,pages,live=_live_pages_wrapper(tmp_path)
    live.read_context()
    state=SimpleNamespace(open=False,cancel=0)
    dialog=SimpleNamespace(handle=902,is_enabled=lambda:state.open)
    def command(code):
        assert code==0x685;state.open=True
    def fields(doc):
        return {key:deepcopy(pages.context['configuration'][key]) for key in ('raw_fields','ranges','coverage')}
    def click(doc,control):
        assert doc is dialog and control==2
        state.open=False;state.cancel+=1
    pages._command=command;pages._dialog=lambda title:dialog;pages._configuration_fields=fields
    pages._click=click;pages._dialogs=lambda:[dialog] if state.open else []
    completion=live.show_completion({'operation':'configure_project'},{'plan':{}})
    assert completion['page']=='自动分配软元件设置' and state.open
    if damage=='fields':pages.context['configuration']['raw_fields']['D'][0]['text']='17'
    if damage=='identity':pages.context['identity']['process_started']='foreign'
    if damage:
        with pytest.raises(RuntimeError):live.finish_completion()
        assert state.open and state.cancel==0
    else:
        live.read_context()
        assert not state.open and state.cancel==1
        live.finish_completion()
        assert state.cancel==1 and pages.saves==0


def test_live_stage_save_uses_latest_saved_baseline_for_unobserved_initializers(tmp_path):
    from gxw.live_context import prepare_live_command
    from gxw.fb_authoring import _context
    from gxw.declarations import edit_declarations,serialize_declarations
    from tests.test_gxw_object_model import replay_source_records
    raw,pages,live=_live_pages_wrapper(tmp_path)
    before=live.read_context();name=before['inventory']['globals'][0]
    command=dict(operation='edit_global_labels',source_name=name,declarations=[dict(name='LiveFlag',data_type='BOOL')])
    prepared=prepare_live_command(raw,before,command,device_profile='FX3U')
    live.apply(command,prepared);after=live.read_context()
    document=_context(raw)[3].declarations[name+'.gh']
    saved=replay_source_records(raw,{name+'.gh':serialize_declarations(edit_declarations(document,upserts=command['declarations']))})
    pages.path.write_bytes(saved)
    assert live.read_disk_change()==saved
    live.acknowledge_native_save(saved,after)
    assert live.read_disk_change() is None and pages.saves==0
    doc=_context(saved)[3].declarations[name+'.gh']
    changed=edit_declarations(doc,upserts=[dict(name='LiveFlag',data_type='BOOL',initial_value='TRUE')])
    pages.saved_output=replay_source_records(saved,{name+'.gh':serialize_declarations(changed)})
    with pytest.raises(RuntimeError,match='ordinary initializer') as caught:
        live.stage_save(after)
    assert pages.saves==1 and caught.value.observation['save_performed'] is True
@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('button_language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('damage', [None, 'table', 'cursor', 'owner', 'dialog', 'button', 'mixed_buttons', 'timeout'])
def test_unused_label_delete_dispatches_one_verified_cursor_row_and_one_exact_confirmation(
        monkeypatch, damage, language, button_language):
    import copy
    import sys
    from types import SimpleNamespace
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError
    from gxw.native_locale import ui_text
    state = SimpleNamespace(dialog=False, commands=[], clicks=[], keys=[])
    fields = {'类': 'VAR', '标签名': 'Unused', '数据类型': 'INT'}
    table = {'csv_columns': list(fields), 'declarations': [{'raw_fields': fields}]}
    prepared = {'kind': 'active_label_delete', 'scope': 'program', 'before': copy.deepcopy(table),
        'plan': {'source_name': 'Main', 'name': 'Unused', 'csv_row': 0, 'before_fields': copy.deepcopy(fields)}}
    if damage == 'table':
        table['declarations'][0]['raw_fields']['数据类型'] = 'BOOL'
    editor = SimpleNamespace(handle=210, control_id=lambda:200, is_visible=lambda:True, is_enabled=lambda:True,
        window_text=lambda:'Other' if damage == 'cursor' else 'Unused')
    grid = SimpleNamespace(handle=200, is_visible=lambda:True, is_enabled=lambda:True, set_focus=lambda:None,
        descendants=lambda **kw:[editor])
    document = SimpleNamespace(handle=100, descendants=lambda **kw:[grid])
    class Dialog:
        def window_text(self): return ui_text('application',language)
        def child_window(self, control_id, class_name):
            assert class_name == 'Button'
            def caption():
                if damage=='button': return 'Other'
                actual = ('en' if control_id==6 else 'zh-CN') if damage=='mixed_buttons' else button_language
                return ui_text('yes' if control_id==6 else 'no',actual)
            return SimpleNamespace(is_enabled=lambda:True, window_text=caption)
    dialog = Dialog()
    driver = object.__new__(NativeEngineeringDriver)
    driver.session = SimpleNamespace(process_id=7)
    driver.native_ui_language = language
    driver.read_labels = lambda name, scope: copy.deepcopy(table)
    driver._labels_page = lambda name, scope: document
    driver._foreground = lambda *args: None
    driver._dialogs = lambda:[dialog] if state.dialog else []
    driver._dialog_text = lambda _: 'Other prompt' if damage == 'dialog' else ui_text('delete_cursor_row',language)
    driver._window = lambda:SimpleNamespace(is_enabled=lambda:True)
    def command(value):
        state.commands.append(value)
        state.dialog = damage != 'timeout'
    def click(actual, control_id):
        assert actual is dialog
        state.clicks.append(control_id)
        state.dialog = False
    def wait(predicate, message):
        for _ in range(4):
            value = predicate()
            if value: return value
        raise NativeEngineeringDriverError(message)
    driver._command, driver._click, driver._wait = command, click, wait
    driver.snapshot = lambda:pytest.fail('A label deletion must not Save')
    monkeypatch.setitem(sys.modules, 'win32gui', SimpleNamespace(IsChild=lambda parent, child:damage != 'owner'))
    monkeypatch.setitem(sys.modules, 'win32process', SimpleNamespace(GetWindowThreadProcessId=lambda handle:(3,7)))
    monkeypatch.setitem(sys.modules, 'pywinauto.keyboard', SimpleNamespace(send_keys=lambda keys, **kw:state.keys.append(keys)))
    if damage:
        with pytest.raises(NativeEngineeringDriverError):
            driver._delete_label(prepared)
        assert state.commands == ([] if damage in ('table','cursor','owner') else [0x692])
        assert state.clicks == []
    else:
        result = driver._delete_label(prepared)
        assert result['delete_dispatched'] and result['confirmation_sent'] and not result['save_performed']
        assert state.commands == [0x692] and state.clicks == [6]
    assert not any('{ESC}' in keys for keys in state.keys)

def test_input_placement_requires_painted_cells_in_first_network_not_just_visible_view():
    from types import SimpleNamespace
    from PIL import Image
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError, _grid_calibration
    pixels = Image.new('RGB', (600,420), 'white')
    for row in [*range(1,10), *range(12,22)]:
        for column in range(1,34):
            pixels.putpixel((32+16*column,4+16*row),(110,110,110))
    view = SimpleNamespace(capture_as_image=lambda:pixels,
        parent=lambda:SimpleNamespace(descendants=lambda **kw:[]))
    driver = object.__new__(NativeEngineeringDriver)
    grid = _grid_calibration(pixels)
    assert driver._graph_placement_point(view,grid,3,7) == (112,132)
    for row in (9,11,12):
        # These coordinates are visible, including another painted network.
        driver._graph_point(grid,5,row+1)
        with pytest.raises(NativeEngineeringDriverError,match='首个网络点阵'):
            driver._graph_placement_point(view,grid,3,row)
    pixels.putpixel((112,132),(255,255,255))
    with pytest.raises(NativeEngineeringDriverError,match='首个网络点阵'):
        driver._graph_placement_point(view,grid,3,7)


# Localized native observation boundaries. These are independent static text
# examples and controlled CSV values, never English/Japanese runtime witnesses.
_LOCALIZED_NATIVE_CASES = {
    'zh-CN': dict(codepage=936, columns=['类', '标签名', '数据类型', '常量', '软元件', '地址', '注释',
        '备注', '系统标签的关联', '系统标签名', '属性'], titles={
        'global': '全局标签设置 Globals', 'program': '局部标签设置 Main [PRG]',
        'fb': '函数/FB标签设置 Logic [FB]', 'structure': '结构体设置 Config'},
        body='程序本体', fbd='结构化梯形图/FBD', comment='温度'),
    'en': dict(codepage=1252, columns=['Class', 'Label Name', 'Data Type', 'Constant', 'Device', 'Address', 'Comment',
        'Remark', 'Relation with System Label', 'System Label Name', 'Attribute'], titles={
        'global': 'Global Label Setting Globals', 'program': 'Local Label Setting Main [PRG]',
        'fb': 'Function/FB Label Setting Logic [FB]', 'structure': 'Structure Setting Config'},
        body='Program', fbd='Structured Ladder/FBD', comment='Température'),
    'ja': dict(codepage=932, columns=['クラス', 'ラベル名', 'データ型', '定数値', 'デバイス', 'アドレス', 'コメント',
        '備考', 'システムラベルの関連', 'システムラベル名', '属性'], titles={
        'global': 'グローバルラベル設定 Globals', 'program': 'ローカルラベル設定 Main [PRG]',
        'fb': 'ファンクション/FBラベル設定 Logic [FB]', 'structure': '構造体設定 Config'},
        body='プログラム本体', fbd='構造化ラダー/FBD', comment='温度'),
}


def _localized_native_identity(language):
    return {**_active_read_identity(), 'native_ui_language': language,
            'project_codepage': _LOCALIZED_NATIVE_CASES[language]['codepage']}


def _localized_native_csv(columns, records, *, title='Controlled native table framing'):
    import csv
    import io
    output = io.StringIO(newline='')
    csv.writer(output, delimiter='\t', quoting=csv.QUOTE_ALL, lineterminator='\r\n').writerows([
        [title], columns, *records])
    return output.getvalue().encode('utf-16')


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('scope,owner,category', [
    ('global', 'Globals', 'VAR_GLOBAL'), ('program', 'Main', 'VAR'),
    ('fb', 'Logic', 'VAR_INPUT'), ('structure', 'Config', None),
])
def test_localized_label_observation_maps_independent_headers_and_preserves_exact_raw_fields(language, scope, owner, category):
    from gxw.active_context import read_active_labels_csv
    case = _LOCALIZED_NATIVE_CASES[language]
    indices = [1, 2, 3, 6] if scope == 'structure' else list(range(7))
    columns = [case['columns'][index] for index in indices]
    values = [category, 'Signal', 'BOOL', '', 'M51' if scope == 'global' else '',
              '%MX0.51' if scope == 'global' else '', case['comment']]
    record = [values[index] for index in indices]
    raw = _localized_native_csv(columns, [record])
    result = read_active_labels_csv(raw, owner=owner, scope=scope, identity=_localized_native_identity(language),
        document_handle=200, document_title=case['titles'][scope])
    declaration, = result['declarations']
    assert result['status'] == 'available' and result['raw_bytes'] == raw
    assert declaration['name'] == 'Signal' and declaration['comment'] == case['comment']
    assert declaration['raw_fields'] == dict(zip(columns, record))
    assert result['canonical_csv_columns'] == [_LOCALIZED_NATIVE_CASES['zh-CN']['columns'][index] for index in indices]
    assert result['source_binding']['identity'] == _localized_native_identity(language)
    assert result['save_performed'] is False and result['compilation'] == result['execution'] == 'not_run'
    if scope == 'fb':
        assert result['interface_status'] == 'available'
        assert result['interface']['inputs'][0]['name'] == 'Signal'
        assert result['interface']['graphic_port_order'] == 'unverified'


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('kind,owner', [('program', 'Main'), ('fb', 'Logic')])
def test_localized_st_observation_uses_independent_native_titles_and_exact_control_text(language, kind, owner):
    from gxw.active_context import read_active_st_text
    case = _LOCALIZED_NATIVE_CASES[language]
    text = '(* ' + case['comment'] + ' *)\r\nCount := Count + 1;\r'
    marker = '[PRG]' if kind == 'program' else '[FB]'
    title = owner + ' ' + marker + ' ' + case['body'] + ' [ST]'
    observed = read_active_st_text(text, owner=owner, kind=kind, identity=_localized_native_identity(language),
        document_handle=200, document_title=title, editor_handle=300)
    assert observed['st_text'] == text and observed['source_binding']['document_title'] == title
    assert observed['source_binding']['identity'] == _localized_native_identity(language)
    assert not observed['save_performed']


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('damage', ['title_language', 'header_language', 'owner', 'kind', 'partial_locale', 'codepage'])
def test_localized_label_observation_rejects_mismatched_page_or_language_metadata(language, damage):
    from gxw.active_context import read_active_labels_csv
    from gxw.models import GXWFormatError
    case = _LOCALIZED_NATIVE_CASES[language]
    other = 'en' if language != 'en' else 'ja'
    identity = _localized_native_identity(language)
    columns = case['columns'][:7]
    title = case['titles']['global']
    owner, scope, comment = 'Globals', 'global', case['comment']
    if damage == 'title_language': title = _LOCALIZED_NATIVE_CASES[other]['titles']['global']
    elif damage == 'header_language': columns = _LOCALIZED_NATIVE_CASES[other]['columns'][:7]
    elif damage == 'owner': owner = 'Another'
    elif damage == 'kind': scope = 'program'
    elif damage == 'partial_locale': identity.pop('project_codepage')
    else:
        identity['project_codepage'] = 1252
        comment = '温度'
    raw = _localized_native_csv(columns, [['VAR_GLOBAL', 'Signal', 'BOOL', '', 'M51', '%MX0.51', comment]])
    with pytest.raises(GXWFormatError):
        read_active_labels_csv(raw, owner=owner, scope=scope, identity=identity,
            document_handle=200, document_title=title)


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('other', ['zh-CN', 'en', 'ja'])
def test_localized_st_title_cannot_be_rebound_to_another_ui_language(language, other):
    from gxw.active_context import read_active_st_text
    from gxw.models import GXWFormatError
    title = 'Main [PRG] ' + _LOCALIZED_NATIVE_CASES[other]['body'] + ' [ST]'
    arguments = dict(text='Count := 1;', owner='Main', kind='program', identity=_localized_native_identity(language),
        document_handle=200, document_title=title, editor_handle=300)
    if language == other:
        assert read_active_st_text(**arguments)['status'] == 'available'
    else:
        with pytest.raises(GXWFormatError):
            read_active_st_text(**arguments)


@pytest.mark.parametrize('language,expected', [
    ('zh-CN', '已完成全部编译。'), ('en', 'Rebuild All Completed.'), ('ja', '全コンパイルを完了しました。'),
])
def test_localized_completion_marker_has_the_exact_static_resource_framing(language, expected):
    from gxw.native_locale import rebuild_completion_markers
    assert rebuild_completion_markers(language) == (expected,)
    assert expected in rebuild_completion_markers()


@pytest.mark.parametrize('language,expected', [
    ('zh-CN', ([('%f', '搜索/替换(F)')], 's')),
    ('en', ([('%f', 'Find/Replace')], 's')),
    ('ja', ([('%f', '検索/置換(F)')], 's')),
])
def test_native_menu_uses_its_resource_accelerators_and_exact_popup_caption(language, expected):
    from gxworks2.native_locale import native_menu_selection
    assert native_menu_selection('menu_find', 'menu_cross_reference', language) == expected


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
def test_task_registration_shortcut_comes_from_its_native_resource(language):
    from gxworks2.native_locale import native_accelerator
    assert native_accelerator('task_open_registration', language) == 't'


@pytest.mark.parametrize('language,prefix,kind', [
    ('zh-CN', '读取指定的文件内容。', '程序块'),
    ('en', 'Reading of the specified file will start.', 'Program Block'),
    ('ja', '指定されたファイルの内容を読み出します。', 'プログラムブロック'),
])
def test_native_label_import_and_new_program_use_their_own_resource_context(language, prefix, kind):
    from gxw.native_locale import ui_text
    assert ui_text('csv_read_confirm', language).splitlines()[0] == prefix
    assert ui_text('new_kind_program', language) == kind


_TASK_BUCKET_CAPTIONS = {
    'zh-CN': ('程序设置', ('执行程序', '初始程序', '扫描程序', '待机程序', '恒定周期程序', '无执行类型指定')),
    'en': ('Program Setting', ('Execution Program', 'Initial Program', 'Scan Program', 'Standby Program',
                               'Fixed Scan Program', 'No Execution Type')),
    'ja': ('プログラム設定', ('実行プログラム', '初期プログラム', 'スキャンプログラム', '待機プログラム',
                         '定周期プログラム', '実行タイプ指定なし')),
}


@pytest.mark.parametrize('language', ('zh-CN', 'en', 'ja'))
@pytest.mark.parametrize('bucket_index', range(6))
@pytest.mark.parametrize('damage', (None, 'duplicate', 'unknown_bucket', 'wrong_resource'))
def test_task_page_resolution_binds_actual_execution_bucket_and_exact_resource_task(language, bucket_index, damage):
    from types import SimpleNamespace
    from gxworks2.engineering_driver import NativeEngineeringDriver, NativeEngineeringDriverError
    def node(text, children=()):
        return SimpleNamespace(text=lambda:text, children=lambda:list(children))
    task = node('执行程序')  # User-owned names must remain untranslated.
    resource = node('程序', [task])
    root_caption, buckets = _TASK_BUCKET_CAPTIONS[language]
    bucket = node('Unregistered execution bucket' if damage == 'unknown_bucket' else buckets[bucket_index], [resource])
    children = [bucket, node('unrelated empty bucket')]
    if damage == 'duplicate':
        children.append(node(buckets[(bucket_index + 1) % 6], [resource]))
    root = node(root_caption, children)
    calls = []
    def get_item(path, *, exact):
        calls.append((path, exact))
        assert path == (root_caption,) and exact is True
        return root
    driver = object.__new__(NativeEngineeringDriver)
    driver.native_ui_language = language
    tree = SimpleNamespace(get_item=get_item)
    if damage:
        with pytest.raises(NativeEngineeringDriverError, match='执行类型归属'):
            driver._task_tree_item(tree, 'another resource' if damage == 'wrong_resource' else '程序', '执行程序')
    else:
        assert driver._task_tree_item(tree, '程序', '执行程序') is task
    assert len(calls) == 1


@pytest.mark.parametrize('language,expected', [
    ('zh-CN', ('程序部件', '程序', '程序本体', '局部标签')),
    ('en', ('POU', 'Program', '程序本体', 'Local Label')),
    ('ja', ('プログラム部品', 'プログラム', '程序本体', 'ローカルラベル')),
])
def test_native_tree_path_translates_structural_positions_without_translating_same_named_objects(language, expected):
    from gxworks2.native_locale import native_tree_path
    path = ['程序部件', '程序', '程序本体', '局部标签']
    assert native_tree_path(path, language) == expected
    assert path == ['程序部件', '程序', '程序本体', '局部标签']


def test_native_tree_user_object_names_survive_all_localized_structural_paths():
    from hypothesis import given, settings, strategies as st
    from gxworks2.native_locale import native_tree_path
    expectations = {
        'zh-CN': (('全局标签',), ('程序部件', 'FB/FUN'), ('程序设置', '执行程序')),
        'en': (('Global Label',), ('POU', 'FB/FUN'), ('Program Setting', 'Execution Program')),
        'ja': (('グローバルラベル',), ('プログラム部品', 'FB/FUN'), ('プログラム設定', '実行プログラム')),
    }
    @settings(max_examples=65, deadline=None, derandomize=True)
    @given(language=st.sampled_from(list(expectations)), name=st.text(alphabet='Ab_012程序部件本体構造ラベル./[] ', min_size=1, max_size=35))
    def check(language, name):
        for source, prefix in zip((('全局标签',), ('程序部件', 'FB/FUN'), ('程序设置', '执行程序')), expectations[language]):
            path = [*source, name]
            assert native_tree_path(path, language) == (*prefix, name)
            assert path == [*source, name]
    check()


@pytest.mark.parametrize('path', [('OtherRoot',), ('程序部件', 'OtherFolder'),
    ('程序部件', '程序', 'Main', 'OtherPage'), ('程序设置', 'OtherFolder')])
def test_native_tree_path_rejects_unknown_structural_navigation(path):
    from gxworks2.native_locale import native_tree_path
    from gxw.models import GXWFormatError
    with pytest.raises(GXWFormatError):
        native_tree_path(path, 'en')


@pytest.mark.parametrize('language,expected', [('zh-CN', '新建'), ('en', 'New Project'), ('ja', '新規作成')])
def test_native_operational_caption_uses_verified_text_identity_and_preserves_literal_tokens(language, expected):
    from gxworks2.native_locale import native_caption
    from gxw.models import GXWFormatError
    assert native_caption('新建', language) == expected
    assert native_caption('MAIN_1', language) == 'MAIN_1'
    if language == 'zh-CN':
        assert native_caption('未登记的操作', language) == '未登记的操作'
    else:
        with pytest.raises(GXWFormatError, match='no verified localized resource identity'):
            native_caption('未登记的操作', language)


@pytest.mark.parametrize('codepage,language', [(1252, 'en'), (932, 'ja'), (936, 'zh-CN')])
def test_loaded_native_language_requires_all_same_directory_satellites_and_keeps_project_encoding_separate(tmp_path, codepage, language):
    from gxworks2.native_locale import language_from_satellites
    root = tmp_path / 'GX'
    paths = [root / (name + '_RC_' + str(codepage) + '.dll') for name in ('GD2', 'GD2MainFrmNavigation', 'GD2Shared')]
    paths += [tmp_path / 'OtherGX' / 'GD2_RC_932.dll', root / ('ed_fbd_RC_' + str(codepage) + '.dll')]
    assert language_from_satellites(paths, root / 'GD2.exe') == language


@pytest.mark.parametrize('damage', ['missing', 'mixed', 'duplicate_language', 'unknown_language', 'other_directory', 'wrong_name'])
def test_loaded_native_language_rejects_missing_mixed_or_foreign_resource_identity(tmp_path, damage):
    from gxworks2.native_locale import language_from_satellites
    from gxw.models import GXWFormatError
    root = tmp_path / 'GX'
    paths = [root / (name + '_RC_1252.dll') for name in ('GD2', 'GD2MainFrmNavigation', 'GD2Shared')]
    if damage == 'missing': paths.pop()
    elif damage == 'mixed': paths[2] = root / 'GD2Shared_RC_932.dll'
    elif damage == 'duplicate_language': paths.append(root / 'GD2_RC_936.dll')
    elif damage == 'unknown_language': paths[0] = root / 'GD2_RC_949.dll'
    elif damage == 'other_directory': paths[1] = tmp_path / 'OtherGX' / 'GD2MainFrmNavigation_RC_1252.dll'
    else: paths[0] = root / 'GD2_RC_1252.dll.backup'
    with pytest.raises(GXWFormatError):
        language_from_satellites(paths, root / 'GD2.exe')


@pytest.mark.parametrize('codepage', [1252, 932, 936])
@pytest.mark.parametrize('present_mask', range(7))
def test_only_incomplete_coherent_native_language_snapshots_are_startup_pending(tmp_path, codepage, present_mask):
    from gxworks2.native_locale import language_from_satellites, NativeLanguageNotReadyError
    root = tmp_path / 'GX'
    names = ('GD2', 'GD2MainFrmNavigation', 'GD2Shared')
    paths = [root / f'{name}_RC_{codepage}.dll' for i,name in enumerate(names) if present_mask & 1 << i]
    with pytest.raises(NativeLanguageNotReadyError):
        language_from_satellites(paths, root / 'GD2.exe')


def test_mixed_native_resources_never_become_pending_when_another_required_module_is_missing(tmp_path):
    from hypothesis import given, settings, strategies as st
    from gxworks2.native_locale import language_from_satellites, NativeLanguageNotReadyError
    from gxw.models import GXWFormatError
    root = tmp_path / 'GX'
    pairs = [(1252,932),(1252,936),(932,1252),(932,936),(936,1252),(936,932)]
    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(pair=st.sampled_from(pairs), missing=st.integers(0,2), order=st.permutations((0,1,2)))
    def check(pair, missing, order):
        names = ('GD2', 'GD2MainFrmNavigation', 'GD2Shared')
        paths = [root / f'{name}_RC_{pair[0]}.dll' for i,name in enumerate(names) if i != missing]
        paths.append(root / f'GD2Ctrl_RC_{pair[1]}.dll')
        with pytest.raises(GXWFormatError) as error:
            language_from_satellites([paths[i] for i in order], root / 'GD2.exe')
        assert not isinstance(error.value, NativeLanguageNotReadyError)
    check()


@pytest.mark.parametrize('damage', [None, 'mixed_initial', 'mixed_late', 'unknown_initial', 'unknown_late',
    'process_started', 'window_handle', 'executable', 'software_version', 'missing_frame', 'timeout'])
def test_native_driver_language_readiness_waits_only_on_the_original_frame_without_commands(monkeypatch, tmp_path, damage):
    from gxworks2 import engineering_window as owner
    from types import SimpleNamespace
    import gxworks2.engineering_driver as native
    from gxworks2.native_locale import NativeLanguageNotReadyError
    from gxw.models import GXWFormatError
    session = SimpleNamespace(process_id=701, window_handle=9001, process_started='123456',
                              executable='C:/GX/GD2.exe', software_version='1.635.0.1')
    state = SimpleNamespace(reads=[], finder_calls=[], clock=0)
    def language(process_id, executable):
        state.reads.append((process_id,executable))
        if damage in ('mixed_initial','unknown_initial') or (
                damage in ('mixed_late','unknown_late') and len(state.reads)>1):
            raise GXWFormatError('mixed resources' if damage.startswith('mixed') else 'unknown resource language')
        if damage=='timeout' or len(state.reads)<3:
            raise NativeLanguageNotReadyError('Required native UI satellites are not loaded yet')
        return 'en'
    def find_running(*, process_id):
        state.finder_calls.append(process_id)
        if damage=='missing_frame':return None
        if damage in ('process_started','window_handle','executable','software_version'):
            changed=dict(vars(session));changed[damage]=9002 if damage=='window_handle' else 'changed'
            return SimpleNamespace(**changed)
        return session
    def sleep(duration):state.clock+=duration
    monkeypatch.setattr(native,'inspect_native_language',language)
    monkeypatch.setattr(owner,'time',SimpleNamespace(monotonic=lambda:state.clock,sleep=sleep))
    monkeypatch.setattr(native.NativeEngineeringDriver,'_dialogs',lambda self:[])
    monkeypatch.setattr(native.NativeEngineeringDriver,'_command',lambda *a,**k:pytest.fail('Readiness must not send an IDE command'))
    options=dict(timeout=.11, finder=SimpleNamespace(find_running=find_running), automation=SimpleNamespace())
    if damage:
        with pytest.raises((GXWFormatError,native.NativeEngineeringDriverError)):
            native.NativeEngineeringDriver(session,tmp_path/'managed.gxw','FX3U',**options)
        if damage.endswith('_initial'):
            assert len(state.reads)==1 and not state.finder_calls
        elif damage in ('process_started','window_handle','executable','software_version','missing_frame'):
            assert len(state.reads)==1 and state.finder_calls==[701]
        elif damage.endswith('_late'):
            assert len(state.reads)==2 and state.finder_calls==[701]
        else:
            assert state.clock>=.11 and all(pid==701 for pid in state.finder_calls)
    else:
        driver=native.NativeEngineeringDriver(session,tmp_path/'managed.gxw','FX3U',**options)
        assert driver.native_ui_language=='en' and len(state.reads)==3
        assert state.finder_calls==[701,701] and state.clock==.05
    assert all(value==(701,'C:/GX/GD2.exe') for value in state.reads)


def test_loaded_native_language_consensus_is_independent_of_module_order_and_identical_duplicates(tmp_path):
    from hypothesis import given, settings, strategies as st
    from gxworks2.native_locale import language_from_satellites
    root = tmp_path / 'GX'
    base = [root / (name + '_RC_932.dll') for name in ('GD2', 'GD2MainFrmNavigation', 'GD2Shared')]
    @settings(max_examples=20, deadline=None, derandomize=True)
    @given(order=st.permutations(base), repeated=st.lists(st.sampled_from(base), max_size=8))
    def check(order, repeated):
        assert language_from_satellites([*order, *repeated], root / 'GD2.exe') == 'ja'
    check()


@pytest.mark.parametrize('failure', ['language_timeout', 'mixed_language', 'opened_project'])
def test_new_frame_binding_failure_preserves_ownership_and_never_dispatches_or_restarts(monkeypatch, tmp_path, failure):
    from types import SimpleNamespace
    import gxworks2.engineering_driver as native
    from gxw.models import GXWFormatError
    session = SimpleNamespace(process_id=701, window_handle=9001, process_started='123456',
                              executable='C:/GX/GD2.exe', software_version='1.635.0.1', project_open=False)
    calls=[]
    if failure!='opened_project':
        def fail_init(*args,**kwargs):
            calls.append('construct')
            if failure=='mixed_language':raise GXWFormatError('mixed resources')
            raise native.NativeEngineeringDriverError('language timeout')
        monkeypatch.setattr(native.NativeEngineeringDriver,'__init__',fail_init)
    else:
        monkeypatch.setattr(native,'inspect_native_language',lambda *args:'en')
    finder=SimpleNamespace(find_running=lambda **kwargs:SimpleNamespace(**{**vars(session),'project_open':True}),
        start=lambda:pytest.fail('An already created frame must never be restarted'))
    monkeypatch.setattr(native.NativeEngineeringDriver,'_command',lambda *args,**kwargs:pytest.fail('No IDE command is authorized'))
    path=tmp_path/'saved.gxw'
    with pytest.raises(native.NativeEngineeringDriverError) as caught:
        native.NativeEngineeringDriver._bind_started_frame(session,path,'FX3U',finder=finder,automation=SimpleNamespace())
    observation=caught.value.observation
    assert observation['native_command_dispatched'] is False
    assert observation['requested_project']==str(path)
    assert observation['startup_session']=={k:getattr(session,k) for k in (
        'process_id','window_handle','process_started','executable','software_version')}
    assert calls==([] if failure=='opened_project' else ['construct'])
    if failure=='opened_project':assert observation['project_opened'] is True


@pytest.mark.parametrize('module', ['GD2ComUIDevRange', 'GD2ComUIData', 'GD2WndOprHeader',
    'GD2MainFrmCompileResult', 'GD2MainFrmCrossRef', 'ed_st', 'ed_fbd', 'basiclib'])
@pytest.mark.parametrize('codepage', [932, 936])
def test_loaded_native_language_rejects_lazily_loaded_pages_from_another_language(tmp_path, module, codepage):
    from gxworks2.native_locale import language_from_satellites
    from gxw.models import GXWFormatError
    root = tmp_path / 'GX'
    paths = [root / (name + '_RC_1252.dll') for name in ('GD2', 'GD2MainFrmNavigation', 'GD2Shared')]
    paths.append(root / (module + '_RC_' + str(codepage) + '.dll'))
    with pytest.raises(GXWFormatError, match='mixed|Multiple'):
        language_from_satellites(paths, root / 'GD2.exe')


@pytest.mark.parametrize('codepage,text', [
    (932, '～温度_1'), (936, '启用 温度'), (1252, 'Température_é'), (949, '온도'),
])
def test_strict_native_text_preserves_representable_codepage_roundtrips(codepage, text):
    from gxw.native_locale import strict_project_text
    assert strict_project_text(text, codepage) == text
    assert text.encode('cp' + str(codepage)).decode('cp' + str(codepage)) == text


@pytest.mark.parametrize('codepage,text', [(932, '〜'), (932, '−'), (932, '🚀'),
    (936, '🚀'), (1252, '温度'), (1252, '🚀'), (949, '\ud800')])
def test_strict_native_text_rejects_unencodable_or_noninvertible_codepage_text(codepage, text):
    from gxw.native_locale import strict_project_text
    from gxw.models import GXWFormatError
    with pytest.raises(GXWFormatError):
        strict_project_text(text, codepage)


def test_strict_native_text_never_changes_safe_generated_text_or_replaces_unrepresentable_suffixes():
    from hypothesis import given, settings, strategies as st
    from gxw.native_locale import strict_project_text
    from gxw.models import GXWFormatError
    @settings(max_examples=50, deadline=None, derandomize=True)
    @given(codepage=st.sampled_from([932, 936, 949, 1252]), text=st.text(alphabet='AZaz_019 \t\r\n"', max_size=60))
    def check(codepage, text):
        assert strict_project_text(text, codepage) == text
        with pytest.raises(GXWFormatError):
            strict_project_text(text + '🚀', codepage)
    check()


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
def test_localized_csv_merge_keeps_native_headers_title_metadata_and_explicit_addresses(language):
    from gxworks2.engineering_driver import _merge_label_csv
    from gxw.active_context import parse_active_label_csv
    case = _LOCALIZED_NATIVE_CASES[language]
    values = ['VAR_GLOBAL', 'Signal', 'BOOL', '', 'M51', '%MX0.51', case['comment'],
              'keep remark', 'keep relation', 'keep system', 'keep attribute']
    original = _localized_native_csv(case['columns'], [values], title='Original native export title')
    changed = _merge_label_csv(original, [dict(name='Signal', data_type='BOOL', class_code=8, comment='Updated',
        initial_value='', device='M51', iec_address='%MX0.51')], native_ui_language=language)
    title, columns, rows = parse_active_label_csv(changed)
    assert title == ['Original native export title'] and columns == case['columns']
    assert rows == [[*values[:6], 'Updated', *values[7:]]]
    assert parse_active_label_csv(original)[2] == [values]


def test_localized_csv_merge_preserves_uninterpreted_columns_over_generated_quoted_text():
    from hypothesis import given, settings, strategies as st
    from gxworks2.engineering_driver import _merge_label_csv
    from gxw.active_context import parse_active_label_csv
    @settings(max_examples=70, deadline=None, derandomize=True)
    @given(language=st.sampled_from(['zh-CN', 'en', 'ja']),
           retained=st.lists(st.text(alphabet='Az_09 \t\n\r"', max_size=25), min_size=4, max_size=4))
    def check(language, retained):
        case = _LOCALIZED_NATIVE_CASES[language]
        values = ['VAR_GLOBAL', 'Signal', 'BOOL', '', 'M51', '%MX0.51', 'Comment', *retained]
        raw = _localized_native_csv(case['columns'], [values])
        requested = [dict(name='Signal', data_type='BOOL', class_code=8, comment='New comment',
                          initial_value='', device='M51', iec_address='%MX0.51')]
        merged = _merge_label_csv(raw, requested, native_ui_language=language)
        title, columns, rows = parse_active_label_csv(merged)
        assert title == ['Controlled native table framing'] and columns == case['columns']
        assert rows == [[*values[:6], 'New comment', *retained]]
        assert _merge_label_csv(merged, requested, native_ui_language=language) == merged
    check()


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
def test_unknown_localized_csv_fields_are_retained_in_readback_and_block_unverified_writes(language):
    from hypothesis import given, settings, strategies as st
    from gxw.active_context import read_active_labels_csv
    from gxworks2.engineering_driver import _merge_label_csv, NativeEngineeringDriverError
    case = _LOCALIZED_NATIVE_CASES[language]
    @settings(max_examples=20, deadline=None, derandomize=True)
    @given(value=st.text(alphabet='AZ_019 温度ラベル\t\n"', max_size=35))
    def check(value):
        columns = [*case['columns'][:7], 'VendorExtension']
        values = ['VAR_GLOBAL', 'Signal', 'BOOL', '', '', '', 'Comment', value]
        raw = _localized_native_csv(columns, [values])
        observed = read_active_labels_csv(raw, owner='Globals', scope='global', identity=_localized_native_identity(language),
            document_handle=200, document_title=case['titles']['global'])
        assert observed['status'] == 'partial'
        assert observed['declarations'][0]['raw_fields']['VendorExtension'] == value
        assert observed['raw_bytes'] == raw
        with pytest.raises(NativeEngineeringDriverError):
            _merge_label_csv(raw, [dict(name='Signal', data_type='BOOL', class_code=8,
                comment='Comment', initial_value='')], native_ui_language=language)
    check()


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
def test_localized_saved_label_comparison_uses_semantics_and_rejects_forged_header_metadata(language):
    from gxw.active_context import read_active_labels_csv
    from gxw.declarations import edit_declarations, serialize_declarations
    from gxw.fb_authoring import _context
    from gxw.live_context import _verify_saved_tables
    from gxw.models import GXWFormatError
    from gxw.project_templates import blank_fbd_baseline
    from tests.test_gxw_object_model import replay_source_records
    raw = blank_fbd_baseline('FX3U')
    _, _, groups, sources, caller = _context(raw)
    document = sources.declarations[groups[caller]['labels']]
    changed = edit_declarations(document, upserts=[dict(name='Count', data_type='INT', class_name='VAR', comment='Temperature')],
                                native_fbd_locals=True)
    saved = replay_source_records(raw, {document.logical_name: serialize_declarations(changed)})
    case = _LOCALIZED_NATIVE_CASES[language]
    columns = [case['columns'][index] for index in (0, 1, 2, 3, 6)]
    csv_data = _localized_native_csv(columns, [['VAR', 'Count', 'INT', '', 'Temperature']])
    identity = {**_active_read_identity(), 'native_ui_language': language, 'project_codepage': 936}
    body_marker = '[PRG]'
    title = case['titles']['program'].replace('Main ' + body_marker, document.owner_name + ' ' + body_marker)
    observed = read_active_labels_csv(csv_data, owner=document.owner_name, scope='program', identity=identity,
        document_handle=200, document_title=title)
    assert _verify_saved_tables(_context(saved), [observed]) == 1
    observed['canonical_csv_columns'][0] = '标签名'
    with pytest.raises(GXWFormatError, match='inconsistent localized column metadata'):
        _verify_saved_tables(_context(saved), [observed])


def test_visible_native_driver_requires_rebind_after_loaded_ui_language_changes(monkeypatch, tmp_path):
    from gxworks2 import engineering_driver as native
    path = tmp_path / 'managed' / 'Visible.gxw'
    driver, state, _ = _visible_native_path_driver(monkeypatch, path, str(path.resolve()))
    assert driver.inspect()['native_ui_language'] == 'zh-CN'
    reads = len(state.profile_reads)
    monkeypatch.setattr(native, 'inspect_native_language', lambda process_id, executable: 'en')
    with pytest.raises(native.NativeEngineeringDriverError, match='界面语言已经改变'):
        driver.inspect()
    assert len(state.profile_reads) == reads
    driver.close()


def _localized_current_page_observations(context, language):
    """Reframe controlled source observations; this does not run a native UI."""
    from copy import deepcopy
    from gxw.active_context import read_active_labels_csv, read_active_st_text
    result = deepcopy(context)
    identity = {**context['identity'], 'native_ui_language': language, 'project_codepage': 936}
    result['identity'] = identity
    case = _LOCALIZED_NATIVE_CASES[language]
    chinese = _LOCALIZED_NATIVE_CASES['zh-CN']
    owners = {'global': 'Globals', 'program': 'Main', 'fb': 'Logic', 'structure': 'Config'}
    inputs, tables = [], []
    for table in context['tables']:
        scope, owner = table['owner_kind'], table['owner']
        header = [case['columns'][chinese['columns'].index(column)] for column in table['csv_columns']]
        rows = [[row['raw_fields'][column] for column in table['csv_columns']] for row in table['declarations']]
        raw = _localized_native_csv(header, rows, title=table['csv_title'])
        title = case['titles'][scope].replace(owners[scope], owner)
        arguments = dict(raw=raw, owner=owner, scope=scope, identity=identity, document_handle=200+len(inputs), document_title=title)
        inputs.append(arguments)
        tables.append(read_active_labels_csv(**arguments))
    types = [table for table in tables if table['owner_kind'] in ('fb', 'structure')]
    result['tables'] = [read_active_labels_csv(**arguments, type_sources=types) for arguments in inputs]
    result['st'] = [read_active_st_text(row['st_text'], owner=row['owner'], kind=row['owner_kind'], identity=identity,
        document_handle=400+index, document_title=row['owner'] + (' [PRG] ' if row['owner_kind'] == 'program' else ' [FB] ')
        + case['body'] + ' [ST]', editor_handle=500+index) for index, row in enumerate(context['st'])]
    for graph in result['graphs']:
        graph['identity'] = deepcopy(identity)
    result['configuration']['identity'] = deepcopy(identity)
    return result


@pytest.mark.parametrize('language', ['en', 'ja'])
@pytest.mark.parametrize('operation', ['create_global_labels', 'edit_global_labels', 'edit_pou_labels'])
def test_localized_declaration_full_prepare_merge_and_readback_preserve_original_raw_metadata(language, operation):
    from copy import deepcopy
    from gxw.active_context import read_active_labels_csv
    from gxw.live_context import prepare_live_command, verify_live_change
    from gxw.project_templates import blank_fbd_baseline
    from gxworks2.engineering_driver import _merge_label_csv
    from tests.test_gxw_object_model import live_native_context_witness
    raw = blank_fbd_baseline('FX3U')
    before = _localized_current_page_observations(live_native_context_witness(raw), language)
    case = _LOCALIZED_NATIVE_CASES[language]
    scope = 'program' if operation == 'edit_pou_labels' else 'global'
    owner = before['inventory']['pous'][0]['owner'] if scope == 'program' else 'Globals'
    title = (case['titles']['program'].replace('Main', owner) if scope == 'program' else case['titles']['global'])
    initial_records = [] if operation == 'create_global_labels' else [[
        'VAR' if scope == 'program' else 'VAR_GLOBAL', 'Keep', 'BOOL', '', '', '', 'Original',
        'retain remark', '', '', '']]
    initial = _localized_native_csv(case['columns'], initial_records)
    if operation != 'create_global_labels':
        table = read_active_labels_csv(initial, owner=owner, scope=scope, identity=before['identity'],
            document_handle=900, document_title=title)
        if scope == 'program':
            before['tables'] = [table if row['owner_kind'] == scope and row['owner'] == owner else row for row in before['tables']]
        else:
            before['tables'].append(table)
            before['inventory']['globals'].append(owner)
    command = dict(operation=operation, source_name=owner,
        declarations=[dict(name='Count', data_type='INT', comment='Updated temperature')])
    prepared = prepare_live_command(raw, before, command, device_profile='FX3U')
    modified = _merge_label_csv(initial, prepared['plan']['rows'], native_ui_language=language)
    actual = read_active_labels_csv(modified, owner=owner, scope=scope, identity=before['identity'],
        document_handle=901, document_title=title)
    after = deepcopy(before)
    if operation == 'create_global_labels':
        after['inventory']['globals'].append(owner)
        after['tables'].append(actual)
    else:
        after['tables'] = [actual if row['owner_kind'] == scope and row['owner'] == owner else row for row in before['tables']]
    result = verify_live_change(before, after, command, prepared)
    assert result['status'] == 'matched' and result['save_performed'] is False
    assert actual['csv_columns'] == case['columns']
    count, = [row for row in actual['declarations'] if row['name'] == 'Count']
    assert count['comment'] == 'Updated temperature'
    if operation != 'create_global_labels':
        keep, = [row for row in actual['declarations'] if row['name'] == 'Keep']
        assert keep['raw_fields'][case['columns'][7]] == 'retain remark'
    forged = deepcopy(actual)
    forged['canonical_csv_columns'][0] = '标签名'
    from gxw.models import GXWFormatError
    from gxw.live_context import verify_active_labels
    with pytest.raises(GXWFormatError, match='inconsistent localized column metadata'):
        verify_active_labels(prepared, forged)


@pytest.mark.parametrize('language', ['en', 'ja'])
@pytest.mark.parametrize('damage', [None, 'raw_device', 'raw_name', 'foreign_semantic_metadata'])
def test_localized_unused_label_delete_keeps_native_raw_fields_and_existing_fx_cpu_boundary(language, damage):
    from gxw.live_context import prepare_live_command, verify_live_change
    from gxw.models import GXWFormatError
    from tests.test_gxw_object_model import live_label_delete_native_witness
    raw, previous, saved, command = live_label_delete_native_witness()
    before = _localized_current_page_observations(previous, language)
    after = _localized_current_page_observations(saved, language)
    if damage:
        table = next(row for row in before['tables'] if row['owner'] == command['source_name'])
        target = next(row for row in table['declarations'] if row['name'] == command['name'])
        if damage == 'raw_device': target['raw_fields'][_LOCALIZED_NATIVE_CASES[language]['columns'][4]] = 'M51'
        elif damage == 'raw_name': target['raw_fields'][_LOCALIZED_NATIVE_CASES[language]['columns'][1]] = 'Another'
        else: table['canonical_csv_columns'][0] = '标签名'
        with pytest.raises(GXWFormatError):
            prepare_live_command(raw, before, command, device_profile='FX3U')
        return
    prepared = prepare_live_command(raw, before, command, device_profile='FX3U')
    assert prepared['plan']['before_fields'] == prepared['target']['raw_fields']
    assert prepared['plan']['cpu'] == 'FX3U/FX3UC'
    result = verify_live_change(before, after, command, prepared)
    assert result['status'] == 'matched' and result['save_performed'] is False


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('scope,owner,category', [
    ('global', 'Globals', 'VAR_GLOBAL'), ('program', 'Main', 'VAR'),
    ('fb', 'Logic', 'VAR_INPUT'), ('structure', 'Config', None),
])
@pytest.mark.parametrize('empty', [False, True])
def test_native_label_table_projects_observed_fields_without_changing_source_metadata(language, scope, owner, category, empty):
    import csv
    import io
    from copy import deepcopy
    from gxw.active_context import read_active_labels_csv, read_active_labels_table
    case = _LOCALIZED_NATIVE_CASES[language]
    indices = [1, 2, 3, 6] if scope == 'structure' else list(range(11))
    columns = [case['columns'][index] for index in indices]
    values = [category, 'Signal', 'BOOL', '', 'M51' if scope == 'global' else '',
              '%MX0.51' if scope == 'global' else '', case['comment'],
              'Retained remark', '', '', 'Retained attribute']
    records = [] if empty else [[values[index] for index in indices]]
    table = dict(title='Observed current table', columns=columns, rows=records)
    original = deepcopy(table)
    arguments = dict(owner=owner, scope=scope, identity=_localized_native_identity(language),
        document_handle=200, document_title=case['titles'][scope])
    result = read_active_labels_table(table, **arguments)
    assert table == original
    assert list(csv.reader(io.StringIO(result['raw_bytes'].decode('utf-16'), newline=''), delimiter='\t')) == [
        [table['title']], columns, *records]
    assert result['read_transport'] == 'native_label_table' and 'export_file' not in result
    assert result['origin'] == 'active_native_labels' and result['status'] == 'available'
    assert result['csv_title'] == table['title'] and result['csv_columns'] == columns
    assert result['source_binding'] == dict(identity=arguments['identity'], owner=owner, owner_kind=scope,
        document_handle=200, document_title=arguments['document_title'])
    assert result['save_performed'] is False and result['compilation'] == result['execution'] == 'not_run'
    assert [row['raw_fields'] for row in result['declarations']] == [dict(zip(columns, row)) for row in records]
    if not empty:
        assert result['declarations'][0]['comment'] == case['comment']
        assert result['declarations'][0]['initial_value_status'] == 'unavailable_in_csv'
    if scope == 'fb':
        assert result['interface']['graphic_port_order'] == 'unverified'
        assert [port['name'] for port in result['interface']['inputs']] == ([] if empty else ['Signal'])
    legacy = read_active_labels_csv(result['raw_bytes'], **arguments)
    assert 'read_transport' not in legacy
    assert {key: value for key, value in result.items() if key != 'read_transport'} == legacy


def test_native_label_table_preserves_generated_unicode_quotes_and_line_endings_in_independent_csv_readback():
    import csv
    import io
    from hypothesis import example, given, settings, strategies as st
    from gxw.active_context import read_active_labels_table
    text = st.text(alphabet=st.characters(blacklist_categories=('Cs',), blacklist_characters='\0'), max_size=60)
    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(title=st.text(alphabet='Az09\t\r\n"温度🚀', min_size=1, max_size=35),
           values=st.lists(st.tuples(text, text, text), min_size=1, max_size=4))
    @example(title='"\t\r\n🚀', values=[('A\r\nB', '\t"', '\r\n')])
    def check(title, values):
        columns = ['类', '标签名', '数据类型', '常量', '软元件', '地址', '注释', '备注', 'VendorExtension']
        rows = [['VAR_GLOBAL', f'Signal{index}', 'BOOL', '', '', '', comment, remark, extension]
                for index, (comment, remark, extension) in enumerate(values)]
        result = read_active_labels_table(dict(title=title, columns=columns, rows=rows),
            owner='Globals', scope='global', identity=_active_read_identity(),
            document_handle=200, document_title='全局标签设置 Globals')
        decoded = list(csv.reader(io.StringIO(result['raw_bytes'].decode('utf-16'), newline=''), delimiter='\t', strict=True))
        assert decoded == [[title], columns, *rows]
        assert result['status'] == 'partial' and result['issues'] == [dict(code='unknown_column', column='VendorExtension')]
        assert [row['raw_fields'] for row in result['declarations']] == [dict(zip(columns, row)) for row in rows]
        assert [row['comment'] for row in result['declarations']] == [value[0] for value in values]
    check()


def test_native_label_table_resolves_named_types_only_from_the_supplied_current_native_sources():
    from copy import deepcopy
    from gxw.active_context import read_active_labels_table
    from gxw.models import GXWFormatError
    columns = ['类', '标签名', '数据类型', '常量', '注释']
    source = read_active_labels_table(dict(title='Logic table', columns=columns,
        rows=[['VAR_INPUT', 'Enabled', 'BOOL', '', '']]), owner='Logic', scope='fb',
        identity=_active_read_identity(), document_handle=201, document_title='函数/FB标签设置 Logic [FB]')
    table = dict(title='Main table', columns=columns, rows=[['VAR', 'LogicInst', 'Logic', '', '']])
    arguments = dict(owner='Main', scope='program', identity=_active_read_identity(),
        document_handle=200, document_title='局部标签设置 Main [PRG]')
    unresolved = read_active_labels_table(table, **arguments)
    assert unresolved['declarations'][0]['type_status'] == 'unresolved'
    resolved = read_active_labels_table(table, **arguments, type_sources=[source])
    declaration, = resolved['declarations']
    assert declaration['type_status'] == 'source_bound' and declaration['kind'] == 'function_block'
    assert declaration['type']['source_binding'] == source['source_binding']
    foreign = deepcopy(source)
    foreign['source_binding']['identity']['project_path'] = 'C:/Lab/Other.gxw'
    with pytest.raises(GXWFormatError, match='同一当前原生工程'):
        read_active_labels_table(table, **arguments, type_sources=[foreign])


@pytest.mark.parametrize('damage', [
    'not_mapping', 'missing_key', 'extra_key', 'title_type', 'columns_type', 'rows_type', 'row_type',
    'column_type', 'cell_type', 'short_row', 'long_row', 'empty_row', 'duplicate_column', 'empty_title',
    'empty_columns', 'title_nul', 'column_nul', 'cell_nul', 'title_surrogate', 'column_surrogate',
    'cell_surrogate', 'row_limit', 'text_limit', 'serialized_limit',
])
def test_native_label_table_rejects_unreadable_shapes_fields_and_oversized_serialization(damage):
    from gxw.active_context import read_active_labels_table
    from gxw.models import GXWFormatError
    table = dict(title='Native table', columns=['类', '标签名', '数据类型', '常量', '注释'],
        rows=[['VAR_GLOBAL', 'Signal', 'BOOL', '', 'Observed comment']])
    if damage == 'not_mapping': table = []
    elif damage == 'missing_key': del table['title']
    elif damage == 'extra_key': table['unverified'] = True
    elif damage == 'title_type': table['title'] = 1
    elif damage == 'columns_type': table['columns'] = tuple(table['columns'])
    elif damage == 'rows_type': table['rows'] = tuple(table['rows'])
    elif damage == 'row_type': table['rows'][0] = tuple(table['rows'][0])
    elif damage == 'column_type': table['columns'][0] = None
    elif damage == 'cell_type': table['rows'][0][0] = b'VAR_GLOBAL'
    elif damage == 'short_row': table['rows'][0].pop()
    elif damage == 'long_row': table['rows'][0].append('unexpected')
    elif damage == 'empty_row': table['rows'] = [[]]
    elif damage == 'duplicate_column': table['columns'][0] = table['columns'][1]
    elif damage == 'empty_title': table['title'] = ''
    elif damage == 'empty_columns': table['columns'], table['rows'] = [], []
    elif damage == 'row_limit': table['rows'] = [[''] * len(table['columns'])] * 8193
    elif damage == 'text_limit': table['title'] = 'A' * (4 * 1024 * 1024)
    elif damage == 'serialized_limit': table['title'] = '"' * (2 * 1024 * 1024)
    else:
        value = '\0' if damage.endswith('_nul') else '\ud800'
        if damage.startswith('title_'): table['title'] = value
        elif damage.startswith('column_'): table['columns'][0] = value
        else: table['rows'][0][-1] = value
    with pytest.raises(GXWFormatError):
        read_active_labels_table(table, owner='Globals', scope='global', identity=_active_read_identity(),
            document_handle=200, document_title='全局标签设置 Globals')


@pytest.mark.parametrize('damage', ['version', 'project', 'owner', 'scope', 'document', 'language', 'codepage'])
def test_native_label_table_retains_current_project_document_and_encoding_boundaries(damage):
    from gxw.active_context import read_active_labels_table
    from gxw.models import GXWFormatError
    table = dict(title='Native table', columns=['类', '标签名', '数据类型', '常量', '注释'],
        rows=[['VAR_GLOBAL', 'Signal', 'BOOL', '', '温度']])
    arguments = dict(owner='Globals', scope='global', identity=_localized_native_identity('zh-CN'),
        document_handle=200, document_title='全局标签设置 Globals')
    if damage == 'version': arguments['identity']['software_version'] = '1.635.0.2'
    elif damage == 'project': del arguments['identity']['project_path']
    elif damage == 'owner': arguments['owner'] = 'Other'
    elif damage == 'scope': arguments['scope'] = 'other'
    elif damage == 'document': arguments['document_handle'] = False
    elif damage == 'language': arguments['identity'] = _localized_native_identity('en')
    else: arguments['identity']['project_codepage'] = 1252
    with pytest.raises(GXWFormatError):
        read_active_labels_table(table, **arguments)


_NATIVE_GRID_TYPE_WITNESSES = {
    'zh-CN': [('Bit', 'BOOL'), ('Word[Signed]', 'INT'), ('Double Word[Signed]', 'DINT'),
              ('FLOAT (Single Precision)', 'REAL'), ('Word[Unsigned]/Bit STRING[16-bit]', 'WORD'),
              ('Double Word[Unsigned]/Bit STRING[32-bit]', 'DWORD'), ('Time', 'TIME'), ('STRING', 'STRING')],
    'en': [('Bit', 'BOOL'), ('Word[Signed]', 'INT'), ('Double Word[Signed]', 'DINT'),
           ('FLOAT (Single Precision)', 'REAL'), ('Word[Unsigned]/Bit String[16-bit]', 'WORD'),
           ('Double Word[Unsigned]/Bit String[32-bit]', 'DWORD'), ('Time', 'TIME'), ('String', 'STRING')],
    'ja': [('ビット', 'BOOL'), ('ワード[符号付き]', 'INT'), ('ダブルワード[符号付き]', 'DINT'),
           ('単精度実数', 'REAL'), ('ワード[符号なし]/ビット列[16ビット]', 'WORD'),
           ('ダブルワード[符号なし]/ビット列[32ビット]', 'DWORD'), ('時間', 'TIME'), ('文字列', 'STRING')],
}


def _native_label_grid_witness(language, scope, types):
    case = _LOCALIZED_NATIVE_CASES[language]
    indices = [1, 2, 3, 6] if scope == 'structure' else list(range(11))
    type_column = indices.index(2)
    columns = [case['columns'][index] for index in indices]
    columns.insert(type_column + 1, ' ')
    rows = []
    for index, data_type in enumerate(types):
        fields = ['VAR_GLOBAL' if scope == 'global' else 'VAR', f'Value{index}', data_type, '',
                  'D100', '%MW0.100', case['comment'], 'Retained remark', 'Current relation', 'SystemValue', 'Keep']
        row = [fields[index] for index in indices]
        row.insert(type_column + 1, '')
        rows.append(row)
    return dict(columns=columns, rows=rows)


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('scope,owner', [('global', 'Globals'), ('program', 'Main'), ('fb', 'Logic'), ('structure', 'Config')])
def test_native_label_grid_uses_frozen_type_spellings_and_retains_all_non_type_fields(language, scope, owner):
    import csv
    import io
    from copy import deepcopy
    from gxw.active_context import read_active_label_grid
    witnesses = _NATIVE_GRID_TYPE_WITNESSES[language]
    table = _native_label_grid_witness(language, scope, [display for display, _ in witnesses])
    original = deepcopy(table)
    case = _LOCALIZED_NATIVE_CASES[language]
    result = read_active_label_grid(table, owner=owner, scope=scope, identity=_localized_native_identity(language),
        document_handle=200, document_title=case['titles'][scope])
    assert table == original and result['native_grid'] == original and result['native_grid'] is not table
    assert result['read_transport'] == 'native_label_table' and result['origin'] == 'active_native_labels'
    assert result['status'] == 'available' and result['csv_title'] == owner
    assert result['save_performed'] is False and result['compilation'] == result['execution'] == 'not_run'
    button = original['columns'].index(' ')
    columns = original['columns'][:button] + original['columns'][button + 1:]
    expected = []
    for values, (_, canonical) in zip(original['rows'], witnesses, strict=True):
        row = dict(zip(columns, values[:button] + values[button + 1:]))
        row[case['columns'][2]] = canonical
        expected.append(row)
    assert result['csv_columns'] == columns and [row['raw_fields'] for row in result['declarations']] == expected
    decoded = list(csv.reader(io.StringIO(result['raw_bytes'].decode('utf-16'), newline=''), delimiter='\t'))
    assert decoded == [[owner], columns, *[[row[column] for column in columns] for row in expected]]
    table['rows'][0][-1] = 'Changed caller copy'
    assert result['native_grid'] == original
    result['native_grid']['rows'][0][-1] = 'Changed retained copy'
    assert table['rows'][0][-1] == 'Changed caller copy'
    assert result['declarations'][0]['raw_fields'] == expected[0]


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
def test_native_label_grid_reuses_current_named_types_and_core_array_bounds_without_guessing_unmeasured_aliases(language):
    from gxw.active_context import read_active_label_grid, read_active_labels_table
    case = _LOCALIZED_NATIVE_CASES[language]
    identity = _localized_native_identity(language)
    sources = []
    for scope, owner, indices, values in [
        ('fb', 'ScaleValue', [0, 1, 2, 3, 6], ['VAR_INPUT', 'Enabled', 'BOOL', '', '']),
        ('structure', 'ConfigType', [1, 2, 3, 6], ['Enabled', 'BOOL', '', '']),
    ]:
        title = case['titles'][scope].replace('Logic' if scope == 'fb' else 'Config', owner)
        sources.append(read_active_labels_table(dict(title=owner, columns=[case['columns'][index] for index in indices], rows=[values]),
            owner=owner, scope=scope, identity=identity, document_handle=201 + len(sources), document_title=title))
    native_bool, native_int = (row[0] for row in _NATIVE_GRID_TYPE_WITNESSES[language][:2])
    types = [native_bool + '(0..3)', native_int + '(1..2,3..5)', 'ScaleValue', 'ConfigType(0..2)',
             'STRING[32]', 'UnexpectedString(32)', 'FLOAT (Double Precision)', native_bool + '(3..0)', 'Mystery display(0..3)']
    table = _native_label_grid_witness(language, 'program', types)
    result = read_active_label_grid(table, owner='Main', scope='program', identity=identity,
        document_handle=200, document_title=case['titles']['program'], type_sources=sources)
    declarations = result['declarations']
    assert [row['data_type'] for row in declarations] == [
        'ARRAY [0..3] OF BOOL', 'ARRAY [1..2,3..5] OF INT', 'ScaleValue', 'ARRAY [0..2] OF ConfigType',
        'STRING[32]', 'UnexpectedString(32)', 'FLOAT (Double Precision)', native_bool + '(3..0)', 'Mystery display(0..3)']
    assert [row['type_status'] for row in declarations] == [
        'resolved', 'resolved', 'source_bound', 'source_bound', 'resolved', 'unresolved', 'unresolved', 'unresolved', 'unresolved']
    assert declarations[2]['type']['source_binding'] == sources[0]['source_binding']
    assert declarations[3]['type']['source_binding'] == sources[1]['source_binding']
    assert declarations[0]['type']['bounds'] == [(0, 3)] and declarations[1]['type']['bounds'] == [(1, 2), (3, 5)]


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('array', [False, True])
def test_native_label_grid_reprojects_original_cells_and_keeps_a_named_type_collision_unresolved(language, array):
    from gxw.active_context import read_active_label_grid, read_active_labels_table
    case = _LOCALIZED_NATIVE_CASES[language]
    identity = _localized_native_identity(language)
    alias = _NATIVE_GRID_TYPE_WITNESSES[language][0][0]
    source = read_active_labels_table(dict(title=alias, columns=[case['columns'][index] for index in (0, 1, 2, 3, 6)],
        rows=[['VAR_INPUT', 'Enabled', 'BOOL', '', '']]), owner=alias, scope='fb', identity=identity,
        document_handle=201, document_title=case['titles']['fb'].replace('Logic', alias))
    display = alias + '(0..3)' if array else alias
    table = _native_label_grid_witness(language, 'fb', [display])
    table['rows'][0][0] = 'VAR_INPUT'
    arguments = dict(owner='Logic', scope='fb', identity=identity,
        document_handle=200, document_title=case['titles']['fb'])
    first = read_active_label_grid(table, **arguments)
    assert first['declarations'][0]['data_type'] == ('ARRAY [0..3] OF BOOL' if array else 'BOOL')
    actual = read_active_label_grid(first['native_grid'], **arguments, type_sources=[source])
    declaration, = actual['declarations']
    assert actual['status'] == 'partial' and declaration['data_type'] == display
    assert declaration['type_status'] == 'unresolved' and 'source_binding' not in declaration['type']
    assert actual['interface_status'] == 'partial'
    assert actual['interface']['inputs'][0]['type_status'] == 'unresolved'
    assert actual['interface']['inputs'][0]['type_code'] is None
    assert any(issue['code'] == 'ambiguous_native_type_alias' for issue in actual['issues'])
    assert actual['native_grid'] == table and actual['native_grid'] is not first['native_grid']


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'mixed_duplicate', 'wrong_position', 'nonempty', 'whitespace_value', 'wrong_header'])
def test_native_label_grid_rejects_a_missing_ambiguous_or_nonempty_type_button_column(damage):
    from gxw.active_context import read_active_label_grid
    from gxw.models import GXWFormatError
    table = _native_label_grid_witness('en', 'program', ['Bit'])
    button = table['columns'].index(' ')
    if damage == 'missing':
        table['columns'].pop(button)
        table['rows'][0].pop(button)
    elif damage == 'duplicate':
        table['columns'].append(' ')
        table['rows'][0].append('')
    elif damage == 'mixed_duplicate':
        table['columns'].append('\u3000')
        table['rows'][0].append('')
    elif damage == 'wrong_position':
        table['columns'].insert(0, table['columns'].pop(button))
        table['rows'][0].insert(0, table['rows'][0].pop(button))
    elif damage == 'nonempty': table['rows'][0][button] = 'Actual native value'
    elif damage == 'whitespace_value': table['rows'][0][button] = ' '
    else: table['columns'][button] = '  '
    with pytest.raises(GXWFormatError):
        read_active_label_grid(table, owner='Main', scope='program', identity=_localized_native_identity('en'),
            document_handle=200, document_title='Local Label Setting Main [PRG]')


def test_native_label_grid_preserves_generated_unknown_fields_and_raw_native_cells_across_column_order():
    from copy import deepcopy
    from hypothesis import given, settings, strategies as st
    from gxw.active_context import read_active_label_grid
    @settings(max_examples=70, deadline=None, derandomize=True)
    @given(language=st.sampled_from(['zh-CN', 'en', 'ja']), order=st.permutations(range(12)),
           retained=st.lists(st.text(alphabet='Az_09 \t\n\r"', max_size=25), min_size=4, max_size=4),
           type_index=st.integers(0, 7))
    def check(language, order, retained, type_index):
        case = _LOCALIZED_NATIVE_CASES[language]
        display, expected_type = _NATIVE_GRID_TYPE_WITNESSES[language][type_index]
        original_columns = [*case['columns'], 'VendorExtension']
        original_values = ['VAR_GLOBAL', 'Signal', display, '', 'M52', '%MX0.52', 'Captured comment',
                           retained[0], retained[1], retained[2], 'Keep', retained[3]]
        columns = [original_columns[index] for index in order]
        values = [original_values[index] for index in order]
        button = columns.index(case['columns'][2]) + 1
        columns.insert(button, ' ')
        values.insert(button, '')
        table = dict(columns=columns, rows=[values])
        original = deepcopy(table)
        result = read_active_label_grid(table, owner='Globals', scope='global', identity=_localized_native_identity(language),
            document_handle=200, document_title=case['titles']['global'])
        expected = dict(zip(original_columns, original_values))
        expected[case['columns'][2]] = expected_type
        assert result['declarations'][0]['raw_fields'] == expected
        assert result['status'] == 'partial'
        assert result['issues'] == [dict(code='unknown_column', column='VendorExtension')]
        assert table == original and result['native_grid'] == original
    check()


def test_native_label_grid_array_projection_preserves_generated_core_bounds_and_independent_base_types():
    from hypothesis import given, settings, strategies as st
    from gxw.active_context import read_active_label_grid
    @settings(max_examples=55, deadline=None, derandomize=True)
    @given(language=st.sampled_from(['zh-CN', 'en', 'ja']), type_index=st.integers(0, 7),
           dimensions=st.lists(st.tuples(st.integers(-20, 20), st.integers(0, 9)), min_size=1, max_size=4))
    def check(language, type_index, dimensions):
        display, expected_type = _NATIVE_GRID_TYPE_WITNESSES[language][type_index]
        text = ','.join(f'{low}..{low + span}' for low, span in dimensions)
        table = _native_label_grid_witness(language, 'program', [display + '(' + text + ')'])
        result = read_active_label_grid(table, owner='Main', scope='program', identity=_localized_native_identity(language),
            document_handle=200, document_title=_LOCALIZED_NATIVE_CASES[language]['titles']['program'])
        declaration, = result['declarations']
        assert declaration['data_type'] == f'ARRAY [{text}] OF {expected_type}'
        assert declaration['type']['bounds'] == [(low, low + span) for low, span in dimensions]
        assert declaration['type']['base_type'] == expected_type and declaration['type_status'] == 'resolved'
    check()


@pytest.mark.parametrize('damage', ['not_mapping', 'missing_rows', 'extra_key', 'ragged', 'field_type', 'nul', 'surrogate'])
def test_native_label_grid_reuses_table_shape_and_text_rejection(damage):
    from gxw.active_context import read_active_label_grid
    from gxw.models import GXWFormatError
    table = _native_label_grid_witness('en', 'program', ['Bit'])
    if damage == 'not_mapping': table = []
    elif damage == 'missing_rows': del table['rows']
    elif damage == 'extra_key': table['title'] = 'Unbound title'
    elif damage == 'ragged': table['rows'][0].pop()
    elif damage == 'field_type': table['rows'][0][-1] = None
    elif damage == 'nul': table['rows'][0][-1] = '\0'
    else: table['rows'][0][-1] = '\ud800'
    with pytest.raises(GXWFormatError):
        read_active_label_grid(table, owner='Main', scope='program', identity=_localized_native_identity('en'),
            document_handle=200, document_title='Local Label Setting Main [PRG]')


@pytest.mark.parametrize('language', ['zh-CN', 'en', 'ja'])
@pytest.mark.parametrize('scope,owner', [('global', 'Globals'), ('program', 'Main'), ('fb', 'Logic'), ('structure', 'Config')])
def test_native_label_grid_keeps_an_observed_empty_table_available(language, scope, owner):
    from gxw.active_context import read_active_label_grid
    table = _native_label_grid_witness(language, scope, [])
    result = read_active_label_grid(table, owner=owner, scope=scope, identity=_localized_native_identity(language),
        document_handle=200, document_title=_LOCALIZED_NATIVE_CASES[language]['titles'][scope])
    assert result['status'] == 'available' and result['declarations'] == [] and result['issues'] == []
    assert ' ' not in result['csv_columns'] and result['native_grid'] == table


@pytest.mark.parametrize('caption', ['软 元件', '注 释'])
def test_native_label_grid_preserves_unrecognized_device_headers_and_rejects_a_missing_required_comment(caption):
    from copy import deepcopy
    from gxw.active_context import read_active_label_grid
    from gxw.models import GXWFormatError
    table = _native_label_grid_witness('zh-CN', 'program', ['Bit'])
    table['columns'][table['columns'].index('软元件' if caption == '软 元件' else '注释')] = caption
    table['columns'][table['columns'].index(' ')] = '\u3000'
    original = deepcopy(table)
    arguments = dict(owner='Main', scope='program', identity=_localized_native_identity('zh-CN'),
        document_handle=200, document_title=_LOCALIZED_NATIVE_CASES['zh-CN']['titles']['program'])
    if caption == '注 释':
        with pytest.raises(GXWFormatError, match='必需列'):
            read_active_label_grid(table, **arguments)
        assert table == original
        return
    result = read_active_label_grid(table, **arguments)
    assert result['status'] == 'partial' and result['issues'] == [dict(code='unknown_column', column=caption)]
    assert caption in result['csv_columns'] and '软元件' not in result['csv_columns']
    assert result['declarations'][0]['raw_fields'][caption] == 'D100'
    assert result['declarations'][0]['device'] is None
    assert table == original and result['native_grid'] == original


@pytest.mark.parametrize('scope,owner,columns,values,expected_columns', [
    ('fb', 'Logic', ['类', '标签名', '数据类型', '\u3000', '常量', '注释'],
     ['VAR_INPUT', 'Enabled', 'Bit', '', '', 'Captured comment'], ['类', '标签名', '数据类型', '常量', '注释']),
    ('structure', 'Config', ['标签名', '数据类型', '\u3000', '常量', '注释'],
     ['Enabled', 'Bit', '', '', 'Captured comment'], ['标签名', '数据类型', '常量', '注释']),
])
def test_native_label_grid_accepts_the_observed_chinese_fb_and_structure_layouts(
        scope, owner, columns, values, expected_columns):
    from copy import deepcopy
    from gxw.active_context import read_active_label_grid
    table = dict(columns=columns, rows=[values])
    original = deepcopy(table)
    result = read_active_label_grid(table, owner=owner, scope=scope, identity=_localized_native_identity('zh-CN'),
        document_handle=200, document_title=_LOCALIZED_NATIVE_CASES['zh-CN']['titles'][scope])
    assert result['status'] == 'available' and result['csv_columns'] == expected_columns
    assert result['declarations'][0]['comment'] == 'Captured comment'
    assert result['declarations'][0]['data_type'] == 'BOOL' and result['native_grid'] == original


@pytest.mark.parametrize('scope', ['global', 'program', 'fb', 'structure'])
def test_native_label_grid_ambiguity_cannot_be_reinferred_from_the_current_catalog_during_write_planning(scope):
    from gxw.active_context import read_active_label_grid, read_active_labels_table
    from gxw.live_context import prepare_active_labels
    from gxw.models import GXWFormatError
    from gxw.project_templates import blank_fbd_baseline
    from tests.test_gxw_object_model import live_native_context_witness
    baseline = blank_fbd_baseline('FX3U')
    context = live_native_context_witness(baseline)
    identity = context['identity']
    program = context['inventory']['pous'][0]['owner']
    source = read_active_labels_table(dict(title='Bit', columns=['类', '标签名', '数据类型', '常量', '注释'],
        rows=[['VAR_INPUT', 'Enabled', 'BOOL', '', '']]), owner='Bit', scope='fb', identity=identity,
        document_handle=201, document_title='函数/FB标签设置 Bit [FB]')
    standard_owner = {'global': 'Globals', 'program': 'Main', 'fb': 'Logic', 'structure': 'Config'}[scope]
    owner = program if scope == 'program' else context['inventory']['globals'][0] if scope == 'global' else standard_owner
    title = _LOCALIZED_NATIVE_CASES['zh-CN']['titles'][scope].replace(standard_owner, owner)
    observed = read_active_label_grid(_native_label_grid_witness('zh-CN', scope, ['Bit']),
        owner=owner, scope=scope, identity=identity, document_handle=200, document_title=title, type_sources=[source])
    assert observed['declarations'][0]['type_status'] == 'unresolved'
    tables = [table for table in context['tables'] if (table['owner_kind'], table['owner']) != (scope, owner)] + [observed, source]
    command = dict(operation='edit_pou_labels', source_name=program, declarations=[dict(name='Other', data_type='INT')])
    with pytest.raises(GXWFormatError, match='type alias is ambiguous'):
        prepare_active_labels(baseline, tables, command, owner=program, identity=identity, device_profile='FX3U')


@pytest.mark.parametrize('language,prefix', [('en', 'String'), ('ja', '文字列')])
@pytest.mark.parametrize('length', [1, 8, 32, 255])
@pytest.mark.parametrize('array', [False, True])
def test_native_label_grid_maps_localized_string_length_formats_using_the_existing_core_limit(
        language, prefix, length, array):
    from gxw.active_context import read_active_label_grid
    display = f'{prefix}({length})' + ('(1..2)' if array else '')
    table = _native_label_grid_witness(language, 'program', [display])
    table['columns'][table['columns'].index(' ')] = '\u3000'
    result = read_active_label_grid(table, owner='Main', scope='program', identity=_localized_native_identity(language),
        document_handle=200, document_title=_LOCALIZED_NATIVE_CASES[language]['titles']['program'])
    declaration, = result['declarations']
    assert result['status'] == 'available'
    assert declaration['data_type'] == (f'ARRAY [1..2] OF STRING[{length}]' if array else f'STRING[{length}]')
    assert declaration['type_status'] == 'resolved' and declaration['type']['base_type'] == 'STRING'
    assert declaration['type']['bounds'] == ([(1, 2)] if array else [])
    assert result['native_grid'] == table


@pytest.mark.parametrize('language,prefix', [('en', 'String'), ('ja', '文字列')])
@pytest.mark.parametrize('length', ['0', '256', '-1', '01', '8.0', ' 8 ', '8x', '', '9' * 4500])
@pytest.mark.parametrize('array', [False, True])
def test_native_label_grid_does_not_guess_noncanonical_or_out_of_range_localized_string_lengths(language, prefix, length, array):
    from gxw.active_context import read_active_label_grid
    display = f'{prefix}({length})' + ('(1..2)' if array else '')
    table = _native_label_grid_witness(language, 'program', [display])
    result = read_active_label_grid(table, owner='Main', scope='program', identity=_localized_native_identity(language),
        document_handle=200, document_title=_LOCALIZED_NATIVE_CASES[language]['titles']['program'])
    declaration, = result['declarations']
    assert result['status'] == 'partial' and declaration['data_type'] == display
    assert declaration['type_status'] == 'unresolved' and result['native_grid'] == table


def test_native_label_grid_localized_string_lengths_and_array_bounds_preserve_independent_generated_values():
    from hypothesis import given, settings, strategies as st
    from gxw.active_context import read_active_label_grid
    @settings(max_examples=75, deadline=None, derandomize=True)
    @given(language=st.sampled_from(['en', 'ja']), length=st.integers(1, 255),
           dimensions=st.lists(st.tuples(st.integers(-20, 20), st.integers(0, 9)), max_size=3))
    def check(language, length, dimensions):
        prefix = 'String' if language == 'en' else '文字列'
        bounds = ','.join(f'{low}..{low + span}' for low, span in dimensions)
        display = f'{prefix}({length})' + (f'({bounds})' if dimensions else '')
        table = _native_label_grid_witness(language, 'program', [display])
        result = read_active_label_grid(table, owner='Main', scope='program', identity=_localized_native_identity(language),
            document_handle=200, document_title=_LOCALIZED_NATIVE_CASES[language]['titles']['program'])
        declaration, = result['declarations']
        assert declaration['data_type'] == (f'ARRAY [{bounds}] OF STRING[{length}]' if dimensions else f'STRING[{length}]')
        assert declaration['type_status'] == 'resolved'
        assert declaration['type']['bounds'] == [(low, low + span) for low, span in dimensions]
        assert result['native_grid'] == table
    check()


@pytest.mark.parametrize('language,prefix', [('en', 'String'), ('ja', '文字列')])
@pytest.mark.parametrize('array', [False, True])
def test_native_label_grid_sized_string_alias_keeps_the_current_named_type_collision_unresolved(language, prefix, array):
    from gxw.active_context import read_active_label_grid, read_active_labels_table
    case = _LOCALIZED_NATIVE_CASES[language]
    identity = _localized_native_identity(language)
    source = read_active_labels_table(dict(title=prefix, columns=[case['columns'][index] for index in (0, 1, 2, 3, 6)],
        rows=[['VAR_INPUT', 'Enabled', 'BOOL', '', '']]), owner=prefix, scope='fb', identity=identity,
        document_handle=201, document_title=case['titles']['fb'].replace('Logic', prefix))
    display = f'{prefix}(8)' + ('(1..2)' if array else '')
    table = _native_label_grid_witness(language, 'program', [display])
    result = read_active_label_grid(table, owner='Main', scope='program', identity=identity,
        document_handle=200, document_title=case['titles']['program'], type_sources=[source])
    declaration, = result['declarations']
    assert result['status'] == 'partial' and declaration['data_type'] == display
    assert declaration['type_status'] == 'unresolved'
    assert any(issue['code'] == 'ambiguous_native_type_alias' for issue in result['issues'])

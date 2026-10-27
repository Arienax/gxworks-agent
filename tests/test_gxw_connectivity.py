
from tools.offline_inputs import private_path
import base64
import json
from pathlib import Path
from functools import lru_cache
import pytest

from src.gxw.connectivity import build_connectivity_graph
from src.gxw.models import Point
from src.gxw.structured_pou import parse_structured_pou


_FIXTURE_PATH = private_path('tests/fixtures/gxw_structured_54_58.json')


def _program(sample: int):
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))[str(sample)]
    data = base64.b64decode(fixture["program_pou_base64"])
    return parse_structured_pou(data, logical_name=fixture["logical_name"])


def _node(program, symbol):
    matches = [node for node in program.nodes if node.symbol == symbol]
    assert len(matches) == 1
    return matches[0]


def test_sample_54_confirms_coincident_ports_connect_without_wire_record():
    program = _program(54)
    graph = build_connectivity_graph(program)
    mov = _node(program, "MOV")
    y1 = _node(program, "Y1")

    assert mov.ports[2].port_kind_code == 0
    assert mov.port_point(2) == Point(29, 2)
    assert y1.port_point(0) == Point(29, 2)
    assert all(
        wire.start != Point(29, 2) and wire.end != Point(29, 2)
        for wire in program.wires
    )
    assert graph.ports_connected(mov.offset, 2, y1.offset, 0)


def test_sample_55_spaced_mov_io_creates_explicit_wires():
    program = _program(55)
    graph = build_connectivity_graph(program)
    mov = _node(program, "MOV")
    value = _node(program, "10")
    d1 = _node(program, "D1")

    assert value.port_point(0) == Point(11, 3)
    assert mov.port_point(1) == Point(22, 3)
    assert mov.port_point(3) == Point(29, 3)
    assert d1.port_point(0) == Point(50, 3)
    assert [
        (wire.start.x, wire.start.y, wire.end.x, wire.end.y)
        for wire in program.wires[-2:]
    ] == [(11, 3, 22, 3), (29, 3, 50, 3)]
    assert graph.ports_connected(value.offset, 0, mov.offset, 1)
    assert graph.ports_connected(mov.offset, 3, d1.offset, 0)


def test_sample_56_t_junction_uses_endpoint_on_segment_geometry():
    program = _program(56)
    graph = build_connectivity_graph(program)
    x1 = _node(program, "X1")
    x2 = _node(program, "X2")
    y1 = _node(program, "Y1")

    # X1's right port lands on the branch point (10,7). The two vertical
    # segments then connect to the upper/lower horizontal branch wires.
    assert x1.port_point(1) == Point(10, 7)
    assert graph.ports_connected(x1.offset, 1, x2.offset, 0)
    assert graph.ports_connected(x1.offset, 1, y1.offset, 0)
    assert not graph.ports_connected(x1.offset, 0, x1.offset, 1)


def test_sample_57_interior_crossing_does_not_merge_wire_nets():
    program = _program(57)
    graph = build_connectivity_graph(program)
    vertical = next(
        wire for wire in program.wires
        if (wire.start.x, wire.start.y, wire.end.x, wire.end.y) == (73, 4, 73, 7)
    )
    horizontal = next(
        wire for wire in program.wires
        if (wire.start.x, wire.start.y, wire.end.x, wire.end.y) == (71, 5, 75, 5)
    )

    assert graph.net_for_wire(vertical.offset).index != graph.net_for_wire(horizontal.offset).index


def test_sample_58_endpoint_on_wire_interior_merges_wire_nets():
    program = _program(58)
    graph = build_connectivity_graph(program)
    horizontal = next(
        wire for wire in program.wires
        if (wire.start.x, wire.start.y, wire.end.x, wire.end.y) == (71, 5, 75, 5)
    )
    vertical = next(
        wire for wire in program.wires
        if (wire.start.x, wire.start.y, wire.end.x, wire.end.y) == (73, 5, 73, 8)
    )

    assert graph.net_for_wire(vertical.offset).index == graph.net_for_wire(horizontal.offset).index


@lru_cache(maxsize=1)
def _visible_native_call_source():
    import zipfile
    import pytest
    path = private_path('research/' + 'evidence/gxworks2-visible-engineering-session-20261017.zip')
    if not path.is_file():
        pytest.skip('private native visible FBD/interface witness is unavailable')
    with zipfile.ZipFile(path) as archive:
        return archive.read('experiments/acceptance-06/final.gxw')


@lru_cache(maxsize=24)
def _visible_native_graph_member(case, field):
    """Use the immutable module archive, without a loose experiment fallback."""
    from zipfile import ZipFile
    fixture = private_path('tests/fixtures/gxw_visible_st_program_graph_native.json')
    if not fixture.is_file():
        pytest.skip('private frozen ST Program/graph fixture is unavailable')
    index = json.loads(fixture.read_text(encoding='utf-8'))
    archive = private_path(index['archive'])
    if not archive.is_file():
        pytest.skip('private frozen ST Program/graph archive is unavailable')
    with ZipFile(archive) as saved:
        return saved.read(index['cases'][case][field])


@lru_cache(maxsize=1)
def _visible_rewire_source():
    """Native FB/interface witness with an explicit independent three-call layout.

    The added test layout is synthetic; it is not claimed as a native save.
    """
    import struct
    from dataclasses import replace
    from src.gxw.models import UnknownRecord
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.project_writer import build_gxw_project

    raw = _visible_native_call_source()
    context = read_project_context(raw, 'POU_01.Program.pou')
    model = context.object_model()
    call = next(n for n in model['nodes'] if n['template'] == 'function_block:ScaleValue')
    positions = {'ScaleInst': (10, 2), 'Enable': (2, 3), 'InputValue': (2, 4), 'Result': (26, 3)}
    for row in model['nodes']:
        row['x'], row['y'] = positions[row['symbol']]
    models = {row['symbol']: row for row in model['nodes']}
    model['wires'] = [
        {'from': models['Enable']['id'] + '.OUT', 'to': call['id'] + '.Enabled'},
        {'from': models['InputValue']['id'] + '.OUT', 'to': call['id'] + '.InValue'},
        {'from': call['id'] + '.OutValue', 'to': models['Result']['id'] + '.IN'}]
    for name, y in (('SecondInst', 12), ('ThirdInst', 24)):
        model['nodes'].extend([
            {'id': name, 'prototype_offset': call['source_offset'], 'template': call['template'], 'symbol': name, 'x': 40, 'y': y},
            {'id': name + '_en', 'template': 'input', 'symbol': 'Enable', 'x': 33, 'y': y + 1},
            {'id': name + '_value', 'template': 'input', 'symbol': 'InputValue', 'x': 33, 'y': y + 2},
            {'id': name + '_result', 'template': 'output', 'symbol': 'Result', 'x': 58, 'y': y + 1}])
        model['wires'].extend([
            {'from': name + '_en.OUT', 'to': name + '.Enabled'},
            {'from': name + '_value.OUT', 'to': name + '.InValue'},
            {'from': name + '.OutValue', 'to': name + '_result.IN'}])
    model['canvas_height'] = 40
    raw = generate_object_project(model, baseline=raw).data
    context = read_project_context(raw, 'POU_01.Program.pou')
    offset = max(r.offset for r in context.program.iter_records()) + 1
    opaque = UnknownRecord(offset, 12, 0xface, struct.pack('<III', 12, 0xface, 0x12345678))
    program = replace(context.program, unknown_records=context.program.unknown_records + (opaque,))
    return build_gxw_project(raw, program).data


def _rewire_handles(raw):
    from src.gxw.object_model import read_project_context
    from src.gxw.models import NodeKind
    context = read_project_context(raw, 'POU_01.Program.pou')
    calls = {n.symbol: 'n' + str(n.offset) for n in context.program.nodes if n.kind == NodeKind.FUNCTION_BLOCK}
    terminals = {(n.symbol, n.bbox.left, n.bbox.top): 'n' + str(n.offset) for n in context.program.nodes
                 if n.kind in (NodeKind.INPUT, NodeKind.OUTPUT)}
    wires = {(w.start.x, w.start.y, w.end.x, w.end.y): 'w' + str(w.offset) for w in context.program.wires}
    return context, calls, terminals, wires


def _rewire_command(raw, *, third=True):
    _, calls, _, wires = _rewire_handles(raw)
    actions = [
        {'action': 'delete_wire', 'wire': wires[(35, 15, 40, 15)]},
        {'action': 'add_wire', 'id': 'second', 'from': {'wire': wires[(22, 4, 26, 4)], 'point': [24, 4]},
         'to': {'node': calls['SecondInst'], 'port': 'InValue'}, 'via': [[24, 18], [37, 18], [37, 15]]}]
    if third:
        actions += [
            {'action': 'delete_wire', 'wire': wires[(35, 27, 40, 27)]},
            {'action': 'add_wire', 'id': 'third', 'from': {'wire': 'second:1', 'point': [28, 18]},
             'to': {'node': calls['ThirdInst'], 'port': 'InValue'}, 'via': [[28, 30], [37, 30], [37, 27]]}]
    return {'operation': 'edit_fbd_graph', 'caller': 'POU_01.Program.pou', 'actions': actions}


def _rewire_partition(raw):
    from src.gxw.object_model import read_project_context
    from src.gxw.models import NodeKind
    context = read_project_context(raw, 'POU_01.Program.pou')
    nodes = {n.offset: n for n in context.program.nodes}
    def endpoint(ref):
        node = nodes[ref.node_offset]
        if node.kind == NodeKind.FUNCTION_BLOCK:
            return node.symbol + '.' + context.sources.callable(node)['ports'][ref.port_index]['name']
        role = 'OUT' if node.kind == NodeKind.INPUT else 'IN'
        return f'{node.symbol}@{node.bbox.left},{node.bbox.top}.{role}'
    return {frozenset(endpoint(port) for port in net.ports)
            for net in build_connectivity_graph(context.program).nets if net.ports}


def _independent_rewire_partition(*, third=True, second=True, input_symbol='InputValue'):
    """Logical endpoint oracle: no routing/writer/planner generated expectation."""
    groups = [{f'Enable@2,3.OUT', 'ScaleInst.Enabled'},
        {f'{input_symbol}@2,4.OUT', 'ScaleInst.InValue'},
        {'ScaleInst.OutValue', 'Result@26,3.IN'},
        {'Enable@33,13.OUT', 'SecondInst.Enabled'}, {'SecondInst.OutValue', 'Result@58,13.IN'},
        {'Enable@33,25.OUT', 'ThirdInst.Enabled'}, {'ThirdInst.OutValue', 'Result@58,25.IN'}]
    if second:
        groups[2].add('SecondInst.InValue')
        groups.append({'InputValue@33,14.OUT'})
    else:
        groups.append({'InputValue@33,14.OUT', 'SecondInst.InValue'})
    if third:
        groups[2].add('ThirdInst.InValue')
        groups.append({'InputValue@33,26.OUT'})
    else:
        groups.append({'InputValue@33,26.OUT', 'ThirdInst.InValue'})
    return {frozenset(group) for group in groups}


def test_visible_existing_calls_rewire_into_a_real_fanout_and_t_branch_with_exact_full_partition():
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit
    raw = _visible_rewire_source()
    command = _rewire_command(raw)
    before = read_project_context(raw, command['caller'])
    prepared = prepare_graph_edit(raw, command)
    saved = generate_object_project(prepared['model'], baseline=raw).data
    assert _rewire_partition(saved) == _independent_rewire_partition()
    assert verify_graph_edit(raw, command, saved, prepared)['topology'] == 'complete_port_partition'
    after = read_project_context(saved, command['caller'])
    assert [n.raw for n in before.program.nodes] == [n.raw for n in after.program.nodes]
    assert [r.raw for r in before.program.unknown_records] == [r.raw for r in after.program.unknown_records]
    assert len(before.program.wires) != len(after.program.wires)


def test_visible_wire_edit_rejects_a_legal_same_type_but_wrong_call_connection():
    import copy
    import pytest
    from src.gxw.models import GXWFormatError
    from src.gxw.object_model import generate_object_project
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit
    raw = _visible_rewire_source()
    command = _rewire_command(raw, third=False)
    prepared = prepare_graph_edit(raw, command)
    wrong = copy.deepcopy(prepared['model'])
    # Same node and wire counts and compatible INT types; only the chosen
    # terminal net changes. A count/geometry-size check cannot detect this.
    next(n for n in wrong['nodes'] if n['symbol'] == 'InputValue' and n['x'] == 2)['symbol'] = 'Result'
    saved = generate_object_project(wrong, baseline=raw).data
    with pytest.raises(GXWFormatError, match='node missing'):
        verify_graph_edit(raw, command, saved, prepared)


def test_visible_graph_native_offset_reordering_rebinds_the_next_action_from_whole_records():
    from copy import deepcopy
    from dataclasses import replace
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.project_writer import build_gxw_project
    from src.gxw.visible_graph import graph_edit_step, prepare_graph_edit, verify_graph_edit_step
    raw = _visible_rewire_source()
    _, _, terminals, _ = _rewire_handles(raw)
    target = terminals[('InputValue', 2, 4)]
    command = _rewire_command(raw, third=False)
    command['actions'].insert(0, {'action': 'rebind_terminal', 'node': target, 'symbol': 'Result'})
    prepared = prepare_graph_edit(raw, command)
    model = deepcopy(_rewire_handles(raw)[0].object_model())
    next(n for n in model['nodes'] if n['id'] == target)['symbol'] = 'Result'
    # Reversing serialized node order changes every current offset while
    # leaving all records, grid points and endpoint relationships unchanged.
    saved = generate_object_project(model, baseline=raw).data
    current = read_project_context(saved, command['caller'])
    next_key = max(r.offset for r in current.program.iter_records()) + 1
    reordered = replace(current.program, nodes=tuple(replace(n, offset=next_key + i)
                         for i, n in enumerate(reversed(current.program.nodes))))
    saved = build_gxw_project(saved, reordered).data
    assert verify_graph_edit_step(saved, prepared, 0)['status'] == 'matched'
    anchor = graph_edit_step(saved, prepared, 1)
    current = read_project_context(saved, command['caller'])
    coordinates = next(w for w in current.program.wires if (w.start.x,w.start.y,w.end.x,w.end.y) == (35,15,40,15))
    assert anchor['wire']['segments'][0]['source_offset'] == coordinates.offset
    assert coordinates.offset != int(command['actions'][1]['wire'][1:])


def test_visible_graph_cannot_approve_an_unrelated_wire_opaque_field_change():
    from dataclasses import replace
    import pytest
    from src.gxw.models import GXWFormatError
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.project_writer import build_gxw_project
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit
    raw = _visible_rewire_source()
    command = _rewire_command(raw, third=False)
    prepared = prepare_graph_edit(raw, command)
    saved = generate_object_project(prepared['model'], baseline=raw).data
    context = read_project_context(saved, command['caller'])
    untouched = next(w for w in context.program.wires if w.start.y == w.end.y == 26)
    changed = replace(untouched, prefix_fields=(0x1234, *untouched.prefix_fields[1:]))
    program = replace(context.program, wires=tuple(changed if w.offset == untouched.offset else w for w in context.program.wires))
    damaged = build_gxw_project(saved, program).data
    assert _rewire_partition(saved) == _rewire_partition(damaged)
    with pytest.raises(GXWFormatError, match='unrelated wire'):
        verify_graph_edit(raw, command, damaged, prepared)


def test_visible_graph_requires_the_previous_complete_checkpoint_before_exposing_next_anchor():
    import pytest
    from src.gxw.models import GXWFormatError
    from src.gxw.visible_graph import graph_edit_step, prepare_graph_edit
    raw = _visible_rewire_source()
    prepared = prepare_graph_edit(raw, _rewire_command(raw))
    assert graph_edit_step(raw, prepared, 0)['action'] == 'delete_wire'
    with pytest.raises(GXWFormatError, match='topology'):
        graph_edit_step(raw, prepared, 1)


def test_visible_graph_rejects_multiple_drivers_type_changes_and_wrong_port_direction():
    import copy
    import pytest
    from src.gxw.models import GXWFormatError
    from src.gxw.visible_graph import prepare_graph_edit
    raw = _visible_rewire_source()
    command = _rewire_command(raw, third=False)
    cases = []
    # Leave the original InputValue producer attached before adding A's OUT.
    cases.append(({'operation': command['operation'], 'caller': command['caller'], 'actions': command['actions'][1:]}, 'multiple port writers'))
    wrong = copy.deepcopy(command)
    wrong['actions'][1]['to']['port'] = 'OutValue'
    cases.append((wrong, 'direction'))
    _, _, terminals, _ = _rewire_handles(raw)
    cases.append(({'operation': command['operation'], 'caller': command['caller'], 'actions': [
        {'action': 'rebind_terminal', 'node': terminals[('InputValue', 2, 4)], 'symbol': 'Enable'}]}, 'type'))
    for bad, reason in cases:
        with pytest.raises(GXWFormatError, match=reason):
            prepare_graph_edit(raw, bad)


def test_visible_graph_edit_sequences_follow_independent_topology_and_keep_opaque_records():
    from hypothesis import example, given, settings, strategies as st
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit
    raw = _visible_rewire_source()
    _, calls, terminals, _ = _rewire_handles(raw)

    @settings(max_examples=12, deadline=None, derandomize=True)
    @example(third=True, edits=[(False, 'Result'), (True, 'InputValue'), (False, 'Result')])
    @given(third=st.booleans(), edits=st.lists(st.tuples(st.booleans(), st.sampled_from(['Result', 'InputValue'])), min_size=0, max_size=4))
    def exercise(third, edits):
        command = _rewire_command(raw, third=third)
        current = 'InputValue'
        connected, branch, restoration = True, ['second:2', 'second:3'], None
        for index, (desired, symbol) in enumerate(edits):
            if symbol != current:
                command['actions'].append({'action': 'rebind_terminal', 'node': terminals[('InputValue', 2, 4)], 'symbol': symbol})
                current = symbol
            if desired != connected:
                if not desired:
                    command['actions'].extend({'action': 'delete_wire', 'wire': identity} for identity in branch)
                    name = 'restore' + str(index)
                    command['actions'].append({'action': 'add_wire', 'id': name,
                        'from': {'node': terminals[('InputValue', 33, 14)], 'port': 'OUT'},
                        'to': {'node': calls['SecondInst'], 'port': 'InValue'}, 'via': []})
                    restoration = name + ':0'
                else:
                    command['actions'].append({'action': 'delete_wire', 'wire': restoration})
                    name = 'rejoin' + str(index)
                    command['actions'].append({'action': 'add_wire', 'id': name,
                        'from': {'wire': 'second:1', 'point': [37, 18]},
                        'to': {'node': calls['SecondInst'], 'port': 'InValue'}, 'via': [[37, 15]]})
                    branch = [name + ':0', name + ':1']
                connected = desired
        prepared = prepare_graph_edit(raw, command)
        saved = generate_object_project(prepared['model'], baseline=raw).data
        assert _rewire_partition(saved) == _independent_rewire_partition(third=third, second=connected, input_symbol=current)
        assert verify_graph_edit(raw, command, saved, prepared)['status'] == 'matched'
        before = read_project_context(raw, command['caller'])
        after = read_project_context(saved, command['caller'])
        assert [r.raw for r in before.program.unknown_records] == [r.raw for r in after.program.unknown_records]
    exercise()


def test_visible_graph_checks_port_partition_when_all_nodes_and_wire_count_stay_the_same():
    import copy
    import pytest
    from src.gxw.models import GXWFormatError
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit
    raw = _visible_rewire_source()
    command = _rewire_command(raw)
    prepared = prepare_graph_edit(raw, command)
    correct = generate_object_project(prepared['model'], baseline=raw).data
    wrong = copy.deepcopy(prepared['model'])
    # Extend only the final C input segment leftwards to its old producer. All
    # nodes/ports and wire count remain; one extra driver joins the actual net.
    row = next(w for w in wrong['wires'] if w['start'] == [37, 27] and w['end'] == [40, 27])
    row['start'] = [35, 27]
    damaged = generate_object_project(wrong, baseline=raw).data
    a, b = (read_project_context(data, command['caller']) for data in (correct, damaged))
    assert [n.raw for n in a.program.nodes] == [n.raw for n in b.program.nodes]
    assert len(a.program.wires) == len(b.program.wires)
    assert _rewire_partition(correct) != _rewire_partition(damaged)
    with pytest.raises(GXWFormatError, match='topology'):
        verify_graph_edit(raw, command, damaged, prepared)


def test_visible_graph_does_not_merge_an_unmentioned_wire_only_component():
    import copy
    import pytest
    from src.gxw.models import GXWFormatError
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.visible_graph import prepare_graph_edit
    raw = _visible_rewire_source()
    model = copy.deepcopy(read_project_context(raw, 'POU_01.Program.pou').object_model())
    # No ports touch this native-format segment. The requested bend at 24,18
    # must not silently absorb it into the requested A/B conductive net.
    model['wires'].append({'start': [20, 18], 'end': [28, 18]})
    raw = generate_object_project(model, baseline=raw).data
    with pytest.raises(GXWFormatError, match='unintended'):
        prepare_graph_edit(raw, _rewire_command(raw, third=False))


def test_visible_graph_rebinds_native_subdivided_wire_handles_at_the_next_checkpoint():
    from dataclasses import replace
    from src.gxw.models import Point
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.project_writer import build_gxw_project
    from src.gxw.visible_graph import graph_edit_step, prepare_graph_edit, verify_graph_edit_step
    raw = _visible_rewire_source()
    command = _rewire_command(raw)
    whole = prepare_graph_edit(raw, command)
    prefix = {**command, 'actions': command['actions'][:3]}
    partial = prepare_graph_edit(raw, prefix)
    saved = generate_object_project(partial['model'], baseline=raw).data
    context = read_project_context(saved, command['caller'])
    trunk = next(w for w in context.program.wires if w.start == Point(24, 18) and w.end == Point(37, 18))
    next_key = max(r.offset for r in context.program.iter_records()) + 1
    program = replace(context.program, wires=tuple(replace(w, end=Point(28, 18)) if w.offset == trunk.offset else w
        for w in context.program.wires) + (replace(trunk, offset=next_key, start=Point(28, 18)),))
    split = build_gxw_project(saved, program).data
    assert verify_graph_edit_step(split, whole, 2)['status'] == 'matched'
    anchor = graph_edit_step(split, whole, 3)
    assert anchor['from']['wire'] == 'second:1' and anchor['from']['point'] == [28, 18]
    assert len(anchor['from']['segments']) == 2


def test_visible_graph_unknown_record_damage_and_indistinguishable_node_duplicates_fail_closed():
    import struct
    from dataclasses import replace
    import pytest
    from src.gxw.models import GXWFormatError
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.project_writer import build_gxw_project
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit
    raw = _visible_rewire_source()
    command = _rewire_command(raw, third=False)
    prepared = prepare_graph_edit(raw, command)
    saved = generate_object_project(prepared['model'], baseline=raw).data
    context = read_project_context(saved, command['caller'])
    unknown, = context.program.unknown_records
    changed = replace(unknown, raw=struct.pack('<III', 12, 0xface, 0x87654321))
    damaged = build_gxw_project(saved, replace(context.program, unknown_records=(changed,))).data
    with pytest.raises(GXWFormatError, match='opaque records'):
        verify_graph_edit(raw, command, damaged, prepared)
    node = context.program.nodes[0]
    next_key = max(r.offset for r in context.program.iter_records()) + 1
    duplicate = build_gxw_project(saved, replace(context.program, nodes=context.program.nodes + (replace(node, offset=next_key),))).data
    with pytest.raises(GXWFormatError, match='ambiguous'):
        prepare_graph_edit(duplicate, command)


def test_visible_graph_invalid_identity_values_and_foreign_address_profile_are_rejected():
    import copy
    import pytest
    from src.gxw.models import GXWFormatError
    from src.gxw.visible_graph import prepare_graph_edit
    raw = _visible_rewire_source()
    command = _rewire_command(raw, third=False)
    for mutation in ('action', 'wire', 'node', 'point'):
        bad = copy.deepcopy(command)
        if mutation == 'action':
            bad['actions'][0]['action'] = []
        elif mutation == 'wire':
            bad['actions'][0]['wire'] = {}
        elif mutation == 'node':
            bad['actions'][1]['to']['node'] = []
        else:
            bad['actions'][1]['from']['point'] = [False, 4]
        with pytest.raises(GXWFormatError):
            prepare_graph_edit(raw, bad)
    with pytest.raises(GXWFormatError, match='CPU'):
        prepare_graph_edit(raw, command, device_profile='L02')


def test_visible_graph_retains_old_wire_opaque_fields_even_when_a_branch_splits_its_net():
    from dataclasses import replace
    import pytest
    from src.gxw.models import GXWFormatError
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.project_writer import build_gxw_project
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit
    raw = _visible_rewire_source()
    command = _rewire_command(raw, third=False)
    prepared = prepare_graph_edit(raw, command)
    saved = generate_object_project(prepared['model'], baseline=raw).data
    context = read_project_context(saved, command['caller'])
    trunk = next(w for w in context.program.wires if (w.start.x,w.start.y,w.end.x,w.end.y) == (22,4,26,4))
    changed = replace(trunk, suffix=trunk.suffix ^ 0x20)
    damaged = build_gxw_project(saved, replace(context.program, wires=tuple(
        changed if w.offset == trunk.offset else w for w in context.program.wires))).data
    assert _rewire_partition(saved) == _rewire_partition(damaged)
    with pytest.raises(GXWFormatError, match='retained wire opaque'):
        verify_graph_edit(raw, command, damaged, prepared)


def test_visible_graph_endpoints_never_bridge_distinct_blocks_with_identical_coordinates():
    from dataclasses import replace
    import pytest
    from src.gxw.models import GXWFormatError, NodeKind, StructuredNode, StructuredWire, UnknownRecord
    from src.gxw.object_model import read_project_context
    from src.gxw.project_writer import build_gxw_project
    from src.gxw.visible_graph import prepare_graph_edit
    raw = _visible_rewire_source()
    context = read_project_context(raw, 'POU_01.Program.pou')
    original = list(context.program.iter_records())
    next_key = max(r.offset for r in original) + 1
    remap = {r.offset: next_key + i for i,r in enumerate(original)}
    copies = [replace(r, offset=remap[r.offset]) for r in original]
    block = context.program.blocks[0]
    other = replace(block, offset=next_key + len(copies), record_offsets=tuple(remap[r.offset] for r in original))
    program = replace(context.program, nodes=context.program.nodes + tuple(r for r in copies if isinstance(r, StructuredNode)),
        wires=context.program.wires + tuple(r for r in copies if isinstance(r, StructuredWire)),
        unknown_records=context.program.unknown_records + tuple(r for r in copies if isinstance(r, UnknownRecord)),
        blocks=(block, other), canvas_height=context.program.canvas_height * 2)
    raw = build_gxw_project(raw, program).data
    context = read_project_context(raw, context.program.logical_name)
    membership = {r.offset:i for i,(_,records) in enumerate(context.program.block_records()) for r in records}
    left = next(n for n in context.program.nodes if membership[n.offset] == 0 and n.kind == NodeKind.FUNCTION_BLOCK)
    right = next(n for n in context.program.nodes if membership[n.offset] == 1 and n.kind == NodeKind.FUNCTION_BLOCK)
    command = {'operation':'edit_fbd_graph','caller':context.program.logical_name,'actions':[
        {'action':'add_wire','id':'bad_cross_block','from':{'node':'n'+str(left.offset),'port':'OutValue'},
         'to':{'node':'n'+str(right.offset),'port':'InValue'},'via':[]}]}
    with pytest.raises(GXWFormatError, match='block boundaries'):
        prepare_graph_edit(raw, command)


def test_visible_graph_deletes_native_coincident_terminals_without_deleting_their_declarations():
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.visible_graph import graph_edit_step, prepare_graph_edit, verify_graph_edit
    raw = _visible_native_call_source()
    context = read_project_context(raw, 'POU_01.Program.pou')
    assert not context.program.wires  # Actual native capture, not a writer fixture.
    cases = [('Enable', 'ScaleInst.Enabled'), ('InputValue', 'ScaleInst.InValue'), ('Result', 'ScaleInst.OutValue')]
    original = _rewire_partition(raw)
    positions = {'Enable': '3,2.OUT', 'InputValue': '3,3.OUT', 'Result': '17,2.IN'}
    for symbol, formal in cases:
        node = next(n for n in context.program.nodes if n.symbol == symbol)
        command = {'operation': 'edit_fbd_graph', 'caller': context.program.logical_name,
                   'actions': [{'action': 'delete_terminal', 'node': 'n' + str(node.offset)}]}
        prepared = prepare_graph_edit(raw, command)
        anchor = graph_edit_step(raw, prepared, 0)
        assert anchor['node']['source_offset'] == node.offset and anchor['node']['symbol'] == symbol
        saved = generate_object_project(prepared['model'], baseline=raw).data
        after = read_project_context(saved, context.program.logical_name)
        terminal = symbol + '@' + positions[symbol]
        expected = {group - {terminal} for group in original}
        assert _rewire_partition(saved) == expected
        assert frozenset({formal}) in expected
        assert after.object_model()['labels'] == context.object_model()['labels']
        assert [n.raw for n in after.program.nodes] == [n.raw for n in context.program.nodes if n.offset != node.offset]
        assert verify_graph_edit(raw, command, saved, prepared)['status'] == 'matched'


def test_visible_graph_moves_native_input_terminal_then_reconnects_the_same_formal():
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.visible_graph import graph_edit_step, prepare_graph_edit, verify_graph_edit_step, verify_graph_edit
    raw = _visible_native_call_source()
    context = read_project_context(raw, 'POU_01.Program.pou')
    terminal = next(n for n in context.program.nodes if n.symbol == 'InputValue')
    call = next(n for n in context.program.nodes if n.symbol == 'ScaleInst')
    command = {'operation': 'edit_fbd_graph', 'caller': context.program.logical_name, 'actions': [
        {'action': 'move_node', 'node': 'n' + str(terminal.offset), 'x': 1, 'y': 3},
        {'action': 'add_wire', 'id': 'reattach', 'from': {'node': 'n' + str(terminal.offset), 'port': 'OUT'},
         'to': {'node': 'n' + str(call.offset), 'port': 'InValue'}, 'via': []}]}
    prepared = prepare_graph_edit(raw, command)
    move = graph_edit_step(raw, prepared, 0)
    assert move['delta'] == [-2, 0] and move['target_bbox'] == [1, 3, 3, 5]
    partial = prepare_graph_edit(raw, {**command, 'actions': command['actions'][:1]})
    detached = generate_object_project(partial['model'], baseline=raw).data
    assert verify_graph_edit_step(detached, prepared, 0)['status'] == 'matched'
    assert _rewire_partition(detached) == {
        frozenset({'Enable@3,2.OUT', 'ScaleInst.Enabled'}), frozenset({'ScaleInst.InValue'}),
        frozenset({'InputValue@1,3.OUT'}), frozenset({'ScaleInst.OutValue', 'Result@17,2.IN'})}
    next_anchor = graph_edit_step(detached, prepared, 1)
    assert next_anchor['from']['point'] == [3, 4] and next_anchor['to']['point'] == [5, 4]
    saved = generate_object_project(prepared['model'], baseline=raw).data
    assert _rewire_partition(saved) == {
        frozenset({'Enable@3,2.OUT', 'ScaleInst.Enabled'}),
        frozenset({'InputValue@1,3.OUT', 'ScaleInst.InValue'}),
        frozenset({'ScaleInst.OutValue', 'Result@17,2.IN'})}
    assert verify_graph_edit(raw, command, saved, prepared)['status'] == 'matched'


def test_visible_graph_moving_a_call_changes_only_its_bbox_and_does_not_drag_old_wires():
    from dataclasses import replace
    from src.gxw.models import Rect
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.structured_pou_writer import serialize_structured_node
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit
    raw = _visible_rewire_source()
    context, calls, _, _ = _rewire_handles(raw)
    before = next(n for n in context.program.nodes if n.symbol == 'ScaleInst')
    command = {'operation': 'edit_fbd_graph', 'caller': context.program.logical_name, 'actions': [
        {'action': 'move_node', 'node': calls['ScaleInst'], 'x': 5, 'y': 35}]}
    prepared = prepare_graph_edit(raw, command)
    saved = generate_object_project(prepared['model'], baseline=raw).data
    after = read_project_context(saved, context.program.logical_name)
    moved = next(n for n in after.program.nodes if n.symbol == 'ScaleInst')
    assert moved.bbox == Rect(5, 35, 17, 39)
    assert moved.port_point(0) == Point(5, 37) and moved.port_point(1) == Point(5, 38)
    assert moved.port_point(2) == Point(17, 37)
    assert serialize_structured_node(replace(moved, bbox=before.bbox)) == before.raw
    assert [w.raw for w in after.program.wires] == [w.raw for w in context.program.wires]
    assert [r.raw for r in after.program.unknown_records] == [r.raw for r in context.program.unknown_records]
    expected = _independent_rewire_partition(third=False, second=False)
    for pair in ({'Enable@2,3.OUT', 'ScaleInst.Enabled'}, {'InputValue@2,4.OUT', 'ScaleInst.InValue'},
                 {'ScaleInst.OutValue', 'Result@26,3.IN'}):
        expected.remove(frozenset(pair))
        expected.update(frozenset({endpoint}) for endpoint in pair)
    assert _rewire_partition(saved) == expected
    assert verify_graph_edit(raw, command, saved, prepared)['status'] == 'matched'


def test_visible_graph_deleted_terminal_identity_expires_and_current_offsets_are_rebound():
    import pytest
    from src.gxw.models import GXWFormatError
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.visible_graph import graph_edit_step, prepare_graph_edit, verify_graph_edit
    raw = _visible_native_call_source()
    context = read_project_context(raw, 'POU_01.Program.pou')
    nodes = {n.symbol: n for n in context.program.nodes}
    deleted = 'n' + str(nodes['Enable'].offset)
    retained = 'n' + str(nodes['InputValue'].offset)
    command = {'operation': 'edit_fbd_graph', 'caller': context.program.logical_name, 'actions': [
        {'action': 'delete_terminal', 'node': deleted},
        {'action': 'move_node', 'node': retained, 'x': 1, 'y': 3}]}
    prepared = prepare_graph_edit(raw, command)
    prefix = prepare_graph_edit(raw, {**command, 'actions': command['actions'][:1]})
    partial = generate_object_project(prefix['model'], baseline=raw).data
    anchor = graph_edit_step(partial, prepared, 1)
    latest = next(n for n in read_project_context(partial, context.program.logical_name).program.nodes if n.symbol == 'InputValue')
    assert anchor['node']['source_offset'] == latest.offset != nodes['InputValue'].offset
    saved = generate_object_project(prepared['model'], baseline=raw).data
    assert verify_graph_edit(raw, command, saved, prepared)['status'] == 'matched'
    for action in ({'action': 'delete_terminal', 'node': deleted},
                   {'action': 'rebind_terminal', 'node': deleted, 'symbol': 'Result'},
                   {'action': 'move_node', 'node': deleted, 'x': 0, 'y': 0}):
        with pytest.raises(GXWFormatError, match='existing terminal|existing data'):
            prepare_graph_edit(raw, {**command, 'actions': [command['actions'][0], action]})


def test_visible_graph_terminal_deletion_cannot_delete_calls_and_move_positions_are_strict():
    import pytest
    from src.gxw.models import GXWFormatError
    from src.gxw.object_model import read_project_context
    from src.gxw.visible_graph import prepare_graph_edit
    raw = _visible_native_call_source()
    context = read_project_context(raw, 'POU_01.Program.pou')
    call = next(n for n in context.program.nodes if n.symbol == 'ScaleInst')
    command = {'operation': 'edit_fbd_graph', 'caller': context.program.logical_name, 'actions': [
        {'action': 'delete_terminal', 'node': 'n' + str(call.offset)}]}
    with pytest.raises(GXWFormatError, match='terminal node'):
        prepare_graph_edit(raw, command)
    for x, y in [(True, 1), (5, False), (-1, 1), (4085, 1), (5, 4093), (5, 1), ('5', 2)]:
        command['actions'] = [{'action': 'move_node', 'node': 'n' + str(call.offset), 'x': x, 'y': y}]
        with pytest.raises(GXWFormatError, match='bounded integer position'):
            prepare_graph_edit(raw, command)


def test_visible_graph_moving_a_terminal_cannot_add_a_second_driver_or_change_a_net_type():
    import pytest
    from src.gxw.models import GXWFormatError
    from src.gxw.visible_graph import prepare_graph_edit
    raw = _visible_rewire_source()
    _, _, terminals, _ = _rewire_handles(raw)
    command = {'operation': 'edit_fbd_graph', 'caller': 'POU_01.Program.pou', 'actions': [
        {'action': 'move_node', 'node': terminals[('InputValue', 33, 14)], 'x': 8, 'y': 4}]}
    with pytest.raises(GXWFormatError, match='multiple port writers'):
        prepare_graph_edit(raw, command)
    # Move the BOOL source on top of the INT net after deleting its old source.
    command['actions'] = [
        {'action': 'delete_terminal', 'node': terminals[('InputValue', 2, 4)]},
        {'action': 'move_node', 'node': terminals[('Enable', 2, 3)], 'x': 8, 'y': 4}]
    with pytest.raises(GXWFormatError, match='port type is incompatible'):
        prepare_graph_edit(raw, command)


def test_visible_graph_native_terminal_move_and_reconnect_sequences_keep_the_independent_port_partition():
    from hypothesis import example, given, settings, strategies as st
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit
    raw = _visible_native_call_source()
    context = read_project_context(raw, 'POU_01.Program.pou')
    terminal = next(n for n in context.program.nodes if n.symbol == 'InputValue')
    call = next(n for n in context.program.nodes if n.symbol == 'ScaleInst')

    @settings(max_examples=10, deadline=None, derandomize=True)
    @example(positions=[0, 2, 1, 0])
    @given(positions=st.lists(st.integers(min_value=0, max_value=2), min_size=1, max_size=4))
    def exercise(positions):
        actions, current, wire = [], 3, None
        for index, x in enumerate(positions):
            if x == current:
                continue
            if wire is not None:
                actions.append({'action': 'delete_wire', 'wire': wire})
            actions.append({'action': 'move_node', 'node': 'n' + str(terminal.offset), 'x': x, 'y': 3})
            name = 'attach' + str(index)
            actions.append({'action': 'add_wire', 'id': name,
                'from': {'node': 'n' + str(terminal.offset), 'port': 'OUT'},
                'to': {'node': 'n' + str(call.offset), 'port': 'InValue'}, 'via': []})
            wire, current = name + ':0', x
        command = {'operation': 'edit_fbd_graph', 'caller': context.program.logical_name, 'actions': actions}
        prepared = prepare_graph_edit(raw, command)
        saved = generate_object_project(prepared['model'], baseline=raw).data
        assert _rewire_partition(saved) == {
            frozenset({'Enable@3,2.OUT', 'ScaleInst.Enabled'}),
            frozenset({f'InputValue@{positions[-1]},3.OUT', 'ScaleInst.InValue'}),
            frozenset({'ScaleInst.OutValue', 'Result@17,2.IN'})}
        after = read_project_context(saved, context.program.logical_name)
        assert len(after.program.wires) == 1
        assert after.object_model()['labels'] == context.object_model()['labels']
        assert verify_graph_edit(raw, command, saved, prepared)['status'] == 'matched'
    exercise()


def test_visible_graph_reconnects_three_existing_calls_in_series_without_changing_other_nets():
    from src.gxw.object_model import generate_object_project
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit
    raw = _visible_rewire_source()
    _, _, _, wires = _rewire_handles(raw)
    command = _rewire_command(raw)
    command['actions'][3].update(
        **{'from': {'wire': wires[(52, 14, 58, 14)], 'point': [55, 14]},
           'via': [[55, 30], [37, 30], [37, 27]]})
    prepared = prepare_graph_edit(raw, command)
    saved = generate_object_project(prepared['model'], baseline=raw).data
    expected = _independent_rewire_partition(third=False, second=True)
    expected.remove(frozenset({'InputValue@33,26.OUT', 'ThirdInst.InValue'}))
    expected.remove(frozenset({'SecondInst.OutValue', 'Result@58,13.IN'}))
    expected.update([frozenset({'InputValue@33,26.OUT'}),
                     frozenset({'SecondInst.OutValue', 'Result@58,13.IN', 'ThirdInst.InValue'})])
    assert _rewire_partition(saved) == expected
    assert verify_graph_edit(raw, command, saved, prepared)['status'] == 'matched'


def test_visible_graph_joins_existing_branches_with_one_driver_and_keeps_the_exact_partition():
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit
    raw = _visible_rewire_source()
    command = _rewire_command(raw)
    command['actions'].append({'action': 'add_wire', 'id': 'join',
        'from': {'wire': 'second:0', 'point': [24, 10]},
        'to': {'wire': 'third:0', 'point': [28, 22]}, 'via': [[20, 10], [20, 22]]})
    prepared = prepare_graph_edit(raw, command)
    saved = generate_object_project(prepared['model'], baseline=raw).data
    assert _rewire_partition(saved) == _independent_rewire_partition(third=True, second=True)
    context = read_project_context(saved, command['caller'])
    graph = build_connectivity_graph(context.program)
    start = next(w for w in context.program.wires if w.start == Point(24, 10) and w.end == Point(20, 10))
    end = next(w for w in context.program.wires if w.start == Point(20, 22) and w.end == Point(28, 22))
    assert graph.net_for_wire(start.offset).index == graph.net_for_wire(end.offset).index
    assert verify_graph_edit(raw, command, saved, prepared)['status'] == 'matched'


def test_visible_graph_complete_node_identity_does_not_normalize_a_different_native_string_record(monkeypatch):
    import struct
    from dataclasses import replace
    import pytest
    from src.gxw.models import GXWFormatError
    from src.gxw.object_model import read_project_context
    from src.gxw.structured_pou_writer import SIZE_LIKE_A_OFFSET, SIZE_LIKE_B_OFFSET
    from src.gxw.structured_pou import BODY_SIZE_OFFSET
    from src.gxw import visible_graph
    raw = _visible_native_call_source()
    context = read_project_context(raw, 'POU_01.Program.pou')
    node = next(n for n in context.program.nodes if n.symbol == 'InputValue')
    command = {'operation': 'edit_fbd_graph', 'caller': context.program.logical_name,
               'actions': [{'action': 'rebind_terminal', 'node': 'n' + str(node.offset), 'symbol': 'Result'}]}
    prepared = visible_graph.prepare_graph_edit(raw, command)
    count, = struct.unpack_from('<I', node.raw, 12)
    # The parser accepts both spellings, but a complete-record binding must
    # distinguish the observed NUL-terminated record from a shorter spelling.
    assert node.raw[16 + (count - 1) * 2:16 + count * 2] == b'\0\0'
    record = bytearray(node.raw[:16 + (count - 1) * 2] + node.raw[16 + count * 2:])
    struct.pack_into('<I', record, 0, len(record))
    struct.pack_into('<I', record, 12, count - 1)
    program_raw = bytearray(context.program.raw[:node.offset] + record + context.program.raw[node.offset + node.record_length:])
    for offset in (SIZE_LIKE_A_OFFSET, SIZE_LIKE_B_OFFSET):
        struct.pack_into('<I', program_raw, offset, len(program_raw) - 83)
    size, = struct.unpack_from('<I', program_raw, BODY_SIZE_OFFSET)
    struct.pack_into('<I', program_raw, BODY_SIZE_OFFSET, size - 2)
    changed = parse_structured_pou(bytes(program_raw), logical_name=context.program.logical_name)
    altered = next(n for n in changed.nodes if n.symbol == 'InputValue')
    assert altered.symbol == node.symbol and altered.raw != node.raw
    current = replace(context, program=changed)
    with pytest.raises(GXWFormatError, match='missing or ambiguous'):
        visible_graph._bind_nodes(current, prepared['initial'])
    monkeypatch.setattr(visible_graph, 'read_project_context', lambda *_: current)
    with pytest.raises(GXWFormatError, match='canonical complete native node records'):
        visible_graph.prepare_graph_edit(raw, command)


@pytest.mark.parametrize('case', ['rebind', 'delete'])
def test_visible_graph_real_native_terminal_rebind_and_deletion_match_whole_records(case):
    from dataclasses import replace
    from src.gxw.object_model import read_project_context
    from src.gxw.structured_pou_writer import serialize_structured_node
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit
    if case == 'rebind':
        witness = 'terminal_rebind'
        action = {'action': 'rebind_terminal', 'node': 'n369', 'symbol': 'InputValue'}
        times = ([2026, 10, 6, 17, 16, 1, 18, 0], [2026, 10, 6, 17, 16, 2, 24, 0])
    else:
        witness = 'terminal_delete'
        action = {'action': 'delete_terminal', 'node': 'n369'}
        times = ([2026, 10, 6, 17, 16, 2, 24, 0], [2026, 10, 6, 17, 16, 3, 17, 0])
    before, after = (_visible_native_graph_member(witness, field) for field in ('before', 'after'))
    command = {'operation': 'edit_fbd_graph', 'caller': 'POU_01.Program.pou', 'actions': [action]}
    prepared = prepare_graph_edit(before, command, device_profile='FX3U')
    verified = verify_graph_edit(before, command, after, prepared)
    a, b = (read_project_context(raw, command['caller']) for raw in (before, after))
    assert a.sources.cpu == b.sources.cpu == 'FX3U/FX3UC'
    assert not a.program.wires and not b.program.wires
    if case == 'rebind':
        target = next(n for n in a.program.nodes if n.offset == 369)
        assert target.symbol == '?'
        expected = [serialize_structured_node(replace(n, symbol='InputValue')) if n.offset == 369 else n.raw for n in a.program.nodes]
        partition = {frozenset({'Enable@3,2.OUT', 'ScaleInst.Enabled'}),
            frozenset({'InputValue@3,3.OUT', 'ScaleInst.InValue'}), frozenset({'ScaleInst.OutValue', 'Result@17,2.IN'})}
    else:
        expected = [n.raw for n in a.program.nodes if n.offset != 369]
        partition = {frozenset({'Enable@3,2.OUT', 'ScaleInst.Enabled'}),
            frozenset({'ScaleInst.InValue'}), frozenset({'ScaleInst.OutValue', 'Result@17,2.IN'})}
    assert [n.raw for n in b.program.nodes] == expected
    assert a.object_model()['labels'] == b.object_model()['labels']
    assert _rewire_partition(after) == partition
    assert verified['status'] == 'matched' and verified['native_compile'] == 'not_executed'
    assert verified['source_header_changes'] == [{'source': command['caller'],
        'status': 'native_header_metadata_inference', 'timestamp_before': times[0], 'timestamp_after': times[1],
        'compile_pending_before': 1, 'compile_pending_after': 1}]


@pytest.mark.parametrize('damage', ['cpu', 'length', 'before_pending', 'after_pending', 'before_date', 'after_date', 'weekday', 'millis'])
def test_visible_common_save_header_only_accepts_the_observed_fx3u_metadata_transition(damage):
    import struct
    from src.gxw.models import GXWFormatError
    from src.gxw.source_header import normalize_visible_save_header
    prefix = bytes.fromhex('01000000000001000000000001000a00000000000200000001000000010000000000')
    a = bytearray(prefix + struct.pack('<8HI', 2026, 10, 6, 17, 16, 2, 24, 0, 0))
    b = bytearray(prefix + struct.pack('<8HI', 2026, 10, 6, 17, 16, 3, 17, 0, 1))
    normalized, observation = normalize_visible_save_header(a, b, cpu='FX3U/FX3UC')
    assert normalized == a and observation['compile_pending_before'] == 0 and observation['compile_pending_after'] == 1
    cpu = 'FX3U/FX3UC'
    if damage == 'cpu': cpu = 'Q03UDV'
    elif damage == 'length': b += b'\0'
    elif damage == 'before_pending': struct.pack_into('<I', a, 50, 2)
    elif damage == 'after_pending': struct.pack_into('<I', b, 50, 0)
    elif damage == 'before_date': struct.pack_into('<H', a, 34, 0)
    elif damage == 'after_date': struct.pack_into('<H', b, 36, 13)
    elif damage == 'weekday': struct.pack_into('<H', b, 38, 0)
    elif damage == 'millis': struct.pack_into('<H', b, 48, 1000)
    with pytest.raises(GXWFormatError):
        normalize_visible_save_header(a, b, cpu=cpu)


def test_visible_common_save_header_preserves_every_unobserved_prefix_byte():
    import struct
    from hypothesis import example, given, settings, strategies as st
    from src.gxw.models import GXWFormatError
    from src.gxw.source_header import normalize_visible_save_header
    prefix = bytes.fromhex('01000000000001000000000001000a00000000000200000001000000010000000000')
    before = prefix + struct.pack('<8HI', 2026, 10, 6, 17, 16, 2, 24, 0, 1)
    after = prefix + struct.pack('<8HI', 2026, 10, 6, 17, 16, 3, 17, 0, 1)

    @settings(max_examples=16, deadline=None, derandomize=True)
    @example(offset=12, mask=1)
    @given(offset=st.integers(min_value=0, max_value=33), mask=st.integers(min_value=1, max_value=255))
    def exercise(offset, mask):
        damaged = bytearray(after)
        damaged[offset] ^= mask
        with pytest.raises(GXWFormatError, match='unverified native header'):
            normalize_visible_save_header(before, damaged, cpu='FX3U/FX3UC')
    exercise()


def test_visible_graph_rejects_the_real_native_autoconnect_side_effect_of_a_node_move():
    from dataclasses import replace
    from src.gxw.models import GXWFormatError
    from src.gxw.object_model import read_project_context
    from src.gxw.structured_pou_writer import serialize_structured_node
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit
    before, after = (_visible_native_graph_member('move_autoconnect', field) for field in ('before', 'after'))
    command = {'operation': 'edit_fbd_graph', 'caller': 'POU_01.Program.pou', 'actions': [
        {'action': 'move_node', 'node': 'n297', 'x': 23, 'y': 2}]}
    a, b = (read_project_context(raw, command['caller']) for raw in (before, after))
    assert not a.program.wires
    wire, = b.program.wires
    assert (wire.start, wire.end) == (Point(17, 3), Point(23, 3))
    expected = []
    for node in a.program.nodes:
        if node.symbol == 'Result':
            assert node.bbox.left == 17 and node.bbox.top == 2
            node = replace(node, bbox=replace(node.bbox, left=23, right=25))
        expected.append(serialize_structured_node(node))
    assert [n.raw for n in b.program.nodes] == expected
    # The requested translation really happened. The extra conductive edge is
    # still a different edit, so the strict checkpoint must reject it.
    prepared = prepare_graph_edit(before, command, device_profile='FX3U')
    with pytest.raises(GXWFormatError, match='topology, geometry or opaque'):
        verify_graph_edit(before, command, after, prepared)


@pytest.mark.parametrize('operation', ['delete_wire', 'add_wire'])
def test_visible_graph_real_native_wire_delete_and_reconnect_match_full_partition(operation):
    from src.gxw.object_model import read_project_context
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit
    witness = 'wire_delete' if operation == 'delete_wire' else 'wire_restore'
    before, after = (_visible_native_graph_member(witness, field) for field in ('before', 'after'))
    a, b = (read_project_context(raw, 'POU_01.Program.pou') for raw in (before, after))
    if operation == 'delete_wire':
        source, = a.program.wires
        assert (source.start, source.end) == (Point(17, 3), Point(23, 3))
        action = {'action': 'delete_wire', 'wire': 'w' + str(source.offset)}
        assert not b.program.wires
        output_groups = [frozenset({'ScaleInst.OutValue'}), frozenset({'Result@23,2.IN'})]
    else:
        assert not a.program.wires
        call = next(n for n in a.program.nodes if n.symbol == 'ScaleInst')
        sink = next(n for n in a.program.nodes if n.symbol == 'Result')
        action = {'action': 'add_wire', 'id': 'restore', 'from': {'node': 'n' + str(call.offset), 'port': 'OutValue'},
                  'to': {'node': 'n' + str(sink.offset), 'port': 'IN'}, 'via': []}
        created, = b.program.wires
        assert (created.start, created.end) == (Point(17, 3), Point(23, 3))
        output_groups = [frozenset({'ScaleInst.OutValue', 'Result@23,2.IN'})]
    command = {'operation': 'edit_fbd_graph', 'caller': a.program.logical_name, 'actions': [action]}
    assert [n.raw for n in a.program.nodes] == [n.raw for n in b.program.nodes]
    assert _rewire_partition(after) == {
        frozenset({'Enable@3,2.OUT', 'ScaleInst.Enabled'}), frozenset({'ScaleInst.InValue'}), *output_groups}
    verified = verify_graph_edit(before, command, after, prepare_graph_edit(before, command, device_profile='FX3U'))
    assert verified['status'] == 'matched' and verified['native_compile'] == 'not_executed'


def _native_move_wire_group():
    before, after = (_visible_native_graph_member('move_autoconnect', field) for field in ('before', 'after'))
    command = {'operation': 'edit_fbd_graph', 'caller': 'POU_01.Program.pou', 'actions': [
        {'action': 'move_node', 'node': 'n297', 'x': 23, 'y': 2},
        {'action': 'add_wire', 'id': 'requested_wire', 'from': {'node': 'n95', 'port': 'OutValue'},
         'to': {'node': 'n297', 'port': 'IN'}, 'via': []}]}
    return before, after, command


def test_visible_graph_atomic_group_accepts_the_real_move_and_explicit_wire_without_approving_an_intermediate():
    from src.gxw.models import GXWFormatError
    from src.gxw.visible_graph import (graph_edit_step, prepare_graph_edit, verify_graph_edit,
                                    verify_graph_edit_step, verify_graph_edit_group)
    before, after, command = _native_move_wire_group()
    prepared = prepare_graph_edit(before, command, device_profile='FX3U')
    assert graph_edit_step(before, prepared, 0)['action'] == 'move_node'
    result = verify_graph_edit_group(after, prepared, 0, 1)
    assert result['status'] == 'matched' and result['indices'] == [0, 1]
    assert result['action_group'] == ['move_node', 'add_wire']
    assert result['checkpoint'] == 'complete_after_requested_actions' and result['intermediate_checkpoint'] == 'not_verified'
    assert verify_graph_edit(before, command, after, prepared)['status'] == 'matched'
    with pytest.raises(GXWFormatError, match='topology, geometry or opaque'):
        verify_graph_edit_step(after, prepared, 0)
    with pytest.raises(GXWFormatError, match='topology, geometry or opaque'):
        graph_edit_step(after, prepared, 1)
    # The complete pre-move source cannot stand in for the group's final state.
    with pytest.raises(GXWFormatError, match='missing or ambiguous'):
        verify_graph_edit_group(before, prepared, 0, 1)


@pytest.mark.parametrize('start,end', [(False, 1), (0, True), (0, 0), (1, 0), (-1, 0), (0, 2), (1, 2), (0, '1')])
def test_visible_graph_atomic_group_requires_two_frozen_adjacent_indices(start, end):
    from src.gxw.models import GXWFormatError
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit_group
    before, after, command = _native_move_wire_group()
    prepared = prepare_graph_edit(before, command, device_profile='FX3U')
    with pytest.raises(GXWFormatError, match='exactly two adjacent action indices'):
        verify_graph_edit_group(after, prepared, start, end)


def test_visible_graph_atomic_group_does_not_infer_an_unrequested_route_or_approve_other_action_pairs():
    from src.gxw.models import GXWFormatError
    from src.gxw.visible_graph import prepare_graph_edit, verify_graph_edit_group
    before, after, command = _native_move_wire_group()
    # This asks for the same logical endpoints through a different, unobstructed
    # orthogonal route. The native straight AutoConnect edge is insufficient.
    command['actions'][1]['via'] = [[20, 3], [20, 0], [23, 0]]
    prepared = prepare_graph_edit(before, command, device_profile='FX3U')
    with pytest.raises(GXWFormatError, match='topology, geometry or opaque'):
        verify_graph_edit_group(after, prepared, 0, 1)
    command['actions'][1] = {'action': 'rebind_terminal', 'node': 'n297', 'symbol': 'InputValue'}
    prepared = prepare_graph_edit(before, command, device_profile='FX3U')
    with pytest.raises(GXWFormatError, match='only covers an explicit move_node then add_wire'):
        verify_graph_edit_group(after, prepared, 0, 1)


@pytest.mark.parametrize('caller', ['POU_01', 'POU_01.Program', 'POU_01.program.pou',
                                  'pou_01.Program.pou', 'POU_01.Program.pou ', 'POU_01.Program.pou\0'])
def test_visible_graph_caller_aliases_are_rejected_before_planning_any_graph_mutation(caller, monkeypatch):
    from src.gxw import visible_graph
    from src.gxw.models import GXWFormatError
    raw = _visible_native_call_source()
    command = {'operation': 'edit_fbd_graph', 'caller': caller,
               'actions': [{'action': 'rebind_terminal', 'node': 'n369', 'symbol': 'Result'}]}
    def unexpected_edit(*args, **kwargs):
        pytest.fail('caller identity must be rejected before planning an edit')
    monkeypatch.setattr(visible_graph, 'edit_draft', unexpected_edit)
    with pytest.raises(GXWFormatError):
        visible_graph.prepare_graph_edit(raw, command, device_profile='FX3U')


def test_visible_graph_current_source_roles_bind_the_owner_without_guessing_child_display_names():
    from src.gxw.container_writer import replace_project_stream, validate_cfb_streams
    from src.gxw.object_model import generate_object_project
    from src.gxw.visible_graph import graph_edit_step, prepare_graph_edit, verify_graph_edit
    raw = _visible_native_call_source()
    xml = validate_cfb_streams(raw)['projectdatalist.xml']
    # This is an explicitly synthetic metadata variation of the native witness.
    # Its current source role and payload ID remain the same; only the child
    # display name differs from the customary Program spelling.
    for encoding in ('utf-16le', 'utf-8'):
        old = 'POU_01.Program.pou'.encode(encoding)
        if old in xml:
            xml = xml.replace(old, 'POU_01.Section.pou'.encode(encoding))
            break
    else:
        pytest.fail('native source name is missing from its current directory')
    raw, _ = replace_project_stream(raw, 'projectdatalist.xml', xml)
    command = {'operation': 'edit_fbd_graph', 'caller': 'POU_01.Section.pou',
               'actions': [{'action': 'rebind_terminal', 'node': 'n369', 'symbol': 'Result'}]}
    prepared = prepare_graph_edit(raw, command, device_profile='FX3U')
    assert prepared['owner'] == 'POU_01' and prepared['plan']['caller'] == command['caller']
    assert graph_edit_step(raw, prepared, 0)['node']['symbol'] == 'InputValue'
    saved = generate_object_project(prepared['model'], baseline=raw).data
    assert verify_graph_edit(raw, command, saved, prepared)['status'] == 'matched'


@lru_cache(maxsize=1)
def _parallel_native_insertion_source():
    """Original native A/B/C records; the A/B-only midpoint is a Core derivative.

    The experiment saves initial and final sources across two insertions. Do
    not describe the constructed midpoint as a captured native B-only save.
    """
    from src.gxw.object_model import generate_object_project, read_project_context
    initial, final = (_visible_native_graph_member('parallel_calls_062', field) for field in ('before', 'after'))
    request = json.loads(_visible_native_graph_member('parallel_calls_062', 'request'))
    commands = request['plan']['commands'][:2]
    context = read_project_context(final, 'POU_01.Program.pou')
    model = context.object_model()
    assert [(n['symbol'], n['x'], n['y']) for n in model['nodes'] if n['template'].startswith('function_block:')] == [
        ('ScaleInst', 5, 1), ('ScaleInstB', 27, 1), ('ScaleInstC', 49, 1)]
    assert model['canvas_height'] == 5 and len(model['nodes']) == 12 and not model['wires']
    model['nodes'] = [node for node in model['nodes'] if node['x'] < 45]
    midpoint = generate_object_project(model, baseline=final).data
    return initial, midpoint, final, commands


@pytest.mark.parametrize('instance', ['B', 'C'])
def test_visible_call_insertion_accepts_native_parallel_positions_with_exact_old_record_and_net_preservation(instance):
    from src.gxw.visible_engineering import prepare_fbd_call, verify_fbd_call
    initial, midpoint, final, commands = _parallel_native_insertion_source()
    before, after, command = (initial, midpoint, commands[0]) if instance == 'B' else (midpoint, final, commands[1])
    result = verify_fbd_call(before, command, after, prepare_fbd_call(before, command))
    assert result['status'] == 'matched' and result['graph_preservation'] == 'complete_old_records_and_port_nets'
    expected = {frozenset({'Enable@3,2.OUT', 'ScaleInst.Enabled'}),
        frozenset({'InputValue@3,3.OUT', 'ScaleInst.InValue'}),
        frozenset({'Result@17,2.IN', 'ScaleInst.OutValue'}),
        frozenset({'Enable@25,2.OUT', 'ScaleInstB.Enabled'}),
        frozenset({'InputValue@25,3.OUT', 'ScaleInstB.InValue'}),
        frozenset({'B_Result@39,2.IN', 'ScaleInstB.OutValue'})}
    if instance == 'C':
        expected.update({frozenset({'Enable@47,2.OUT', 'ScaleInstC.Enabled'}),
            frozenset({'InputValue@47,3.OUT', 'ScaleInstC.InValue'}),
            frozenset({'C_Result@61,2.IN', 'ScaleInstC.OutValue'})})
    assert _rewire_partition(after) == expected


def test_visible_call_insertion_cannot_approve_the_second_real_native_call_as_an_unrequested_extra():
    from src.gxw.models import GXWFormatError
    from src.gxw.visible_engineering import prepare_fbd_call, verify_fbd_call
    initial, _, final, commands = _parallel_native_insertion_source()
    # These are original, unmodified native files. They span two requested
    # calls; the final file must not validate as one create-B operation.
    with pytest.raises(GXWFormatError, match='unrequested graph objects'):
        verify_fbd_call(initial, commands[0], final, prepare_fbd_call(initial, commands[0]))


@pytest.mark.parametrize('damage', ['old_node_opaque', 'new_call_negated', 'new_terminal_negated',
    'extra_unknown', 'extra_terminal', 'orphan_wire', 'attach_old_net', 'block_opaque', 'source_opaque'])
def test_visible_call_insertion_rejects_record_damage_extra_objects_and_connections_to_the_old_graph(damage):
    import struct
    from dataclasses import replace
    from src.gxw.models import GXWFormatError, Rect, UnknownRecord
    from src.gxw.object_model import default_baseline, read_project, read_project_context
    from src.gxw.project_writer import build_gxw_project
    from src.gxw.visible_engineering import prepare_fbd_call, verify_fbd_call
    initial, midpoint, _, commands = _parallel_native_insertion_source()
    context = read_project_context(midpoint, commands[0]['caller'])
    program = context.program
    key = max(r.offset for r in program.iter_records()) + 1
    if damage == 'old_node_opaque':
        program = replace(program, nodes=tuple(replace(n, reserved=17)
            if n.symbol == 'Enable' and n.bbox.left == 3 else n for n in program.nodes))
    elif damage in ('new_call_negated', 'new_terminal_negated'):
        target = next(n for n in program.nodes if n.symbol == 'ScaleInstB') if damage == 'new_call_negated' else next(
            n for n in program.nodes if n.symbol == 'Enable' and n.bbox.left == 25)
        ports = tuple(replace(p, port_kind_code=p.port_kind_code | 8) if i == 0 else p for i, p in enumerate(target.ports))
        program = replace(program, nodes=tuple(replace(n, ports=ports) if n.offset == target.offset else n for n in program.nodes))
    elif damage == 'extra_unknown':
        record = UnknownRecord(key, 12, 0xface, struct.pack('<III', 12, 0xface, 42))
        program = replace(program, unknown_records=program.unknown_records + (record,))
    elif damage == 'extra_terminal':
        source = next(n for n in program.nodes if n.symbol == 'B_Result')
        program = replace(program, nodes=program.nodes + (replace(source, offset=key, bbox=Rect(41, 2, 43, 4)),))
    elif damage in ('orphan_wire', 'attach_old_net'):
        template = read_project(default_baseline())[0].wires[0]
        points = [(21, 0), (24, 0)] if damage == 'orphan_wire' else [(5, 3), (5, 0), (27, 0), (27, 3)]
        wires = tuple(replace(template, offset=key + i, start=Point(*a), end=Point(*b))
            for i, (a, b) in enumerate(zip(points, points[1:])))
        program = replace(program, wires=program.wires + wires)
    elif damage == 'block_opaque':
        header = bytearray(program.blocks[0].raw_header)
        header[8] ^= 1
        program = replace(program, blocks=(replace(program.blocks[0], raw_header=bytes(header)),))
    else:
        source = bytearray(program.raw)
        source[16] ^= 1
    if damage == 'source_opaque':
        from src.gxw.container_writer import replace_project_stream, validate_cfb_streams
        from src.gxw.project_metadata import logical_mapping
        outer = validate_cfb_streams(midpoint)
        stream = logical_mapping(outer['projectdatalist.xml'])[commands[0]['caller']]
        hdb, _ = replace_project_stream(outer['_hdb'], stream, bytes(source))
        damaged, _ = replace_project_stream(midpoint, '_hdb', hdb)
    else:
        damaged = build_gxw_project(midpoint, program).data
    with pytest.raises(GXWFormatError):
        verify_fbd_call(initial, commands[0], damaged, prepare_fbd_call(initial, commands[0]))


def _insertion_canvas(raw, *, first=5, second=None):
    from dataclasses import replace
    from src.gxw.object_model import read_project_context
    from src.gxw.project_writer import build_gxw_project
    context = read_project_context(raw, 'POU_01.Program.pou')
    block = context.program.blocks[0]
    blocks = (replace(block, canvas_height=first),)
    if second is not None:
        blocks += (replace(block, offset=max(r.offset for r in context.program.iter_records()) + 1,
                           canvas_height=second, record_offsets=()),)
    return build_gxw_project(raw, replace(context.program, blocks=blocks, canvas_height=first + (second or 0))).data


@pytest.mark.parametrize('layout', ['grow_target_blank_rows', 'shrink_old_blank_rows', 'other_block_height'])
def test_visible_call_insertion_preserves_blank_rows_and_only_allows_the_target_canvas_to_grow(layout):
    from src.gxw.models import GXWFormatError
    from src.gxw.visible_engineering import prepare_fbd_call, verify_fbd_call
    initial, midpoint, _, commands = _parallel_native_insertion_source()
    if layout == 'grow_target_blank_rows':
        before, after = _insertion_canvas(initial, first=8), _insertion_canvas(midpoint, first=10)
        assert verify_fbd_call(before, commands[0], after, prepare_fbd_call(before, commands[0]))['status'] == 'matched'
    else:
        if layout == 'shrink_old_blank_rows':
            before, after = _insertion_canvas(initial, first=8), midpoint
        else:
            before, after = _insertion_canvas(initial, second=7), _insertion_canvas(midpoint, second=8)
        with pytest.raises(GXWFormatError, match='canvas|blank rows'):
            verify_fbd_call(before, commands[0], after, prepare_fbd_call(before, commands[0]))


def test_visible_call_insertion_keeps_existing_wire_opaque_fields_and_wire_only_components():
    from dataclasses import replace
    from src.gxw.models import GXWFormatError
    from src.gxw.object_model import generate_object_project, read_project_context
    from src.gxw.project_writer import build_gxw_project
    from src.gxw.visible_engineering import prepare_fbd_call, verify_fbd_call
    initial, midpoint, _, commands = _parallel_native_insertion_source()
    results = []
    for raw in (initial, midpoint):
        context = read_project_context(raw, commands[0]['caller'])
        model = context.object_model()
        model['wires'].append({'start': [21, 0], 'end': [24, 0]})
        results.append(generate_object_project(model, baseline=raw).data)
    before, after = results
    prepared = prepare_fbd_call(before, commands[0])
    assert verify_fbd_call(before, commands[0], after, prepared)['status'] == 'matched'
    context = read_project_context(after, commands[0]['caller'])
    wire, = context.program.wires
    damaged = build_gxw_project(after, replace(context.program, wires=(replace(wire, suffix=wire.suffix ^ 0x20),))).data
    with pytest.raises(GXWFormatError, match='existing complete wire record'):
        verify_fbd_call(before, commands[0], damaged, prepared)

"""Source-bound edits and exact checkpoints for existing visible FBD graphs.

No native calls or file writes. Initial offsets are handles for one frozen
context, not persistent GX object IDs. Every native save is rebound by complete
node records and block membership before another action can be located.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import replace
import re

from .connectivity import build_connectivity_graph
from .editor import edit_draft
from .models import GXWFormatError, NodeKind, Point
from .object_model import _ports, build_object_program, read_project_context
from .source_header import normalize_visible_save_header, source_payload_offset
from .structured_pou_writer import (PROGRAM_PREFIX_SIZE, SIZE_LIKE_A_OFFSET, SIZE_LIKE_B_OFFSET,
                                    serialize_structured_node)
from .topology import _terminal_type, validate_typed_connections


OPERATION = 'edit_fbd_graph'


def validate_graph_actions(actions):
    """Check request syntax without reading a project or resolving identities.

    Nodes, ports and wire handles may be created by earlier engineering steps.
    Their existence, direction, type and current geometry belong to planning
    against that step's actual context, after this pure ingress check.
    """
    if not isinstance(actions, list) or not 1 <= len(actions) <= 128:
        raise GXWFormatError('visible graph edit requires 1 to 128 explicit actions')
    fields = {'rebind_terminal': {'action', 'node', 'symbol'},
              'delete_terminal': {'action', 'node'}, 'move_node': {'action', 'node', 'x', 'y'},
              'delete_wire': {'action', 'wire'}, 'add_wire': {'action', 'id', 'from', 'to', 'via'}}

    def text(value):
        return isinstance(value, str) and bool(value.strip()) and '\0' not in value

    def point(value):
        return (isinstance(value, list) and len(value) == 2
                and all(type(v) is int and 0 <= v <= 4096 for v in value))

    def endpoint(value):
        return isinstance(value, dict) and (
            set(value) == {'node', 'port'} and text(value['node']) and text(value['port'])
            or set(value) == {'wire', 'point'} and text(value['wire']) and point(value['point']))

    for action in actions:
        if not isinstance(action, dict):
            raise GXWFormatError('visible graph action must be an object')
        kind = action.get('action')
        if not isinstance(kind, str) or kind not in fields or set(action) != fields[kind]:
            raise GXWFormatError('invalid visible graph action fields')
        if any(not text(action[key]) for key in ('node', 'wire', 'symbol') if key in action):
            raise GXWFormatError('visible graph identities and symbols must be nonempty text')
        if kind == 'move_node' and any(type(action[key]) is not int or not 0 <= action[key] <= 4096
                                       for key in ('x', 'y')):
            raise GXWFormatError('move_node requires a bounded integer position')
        if kind == 'add_wire':
            if not isinstance(action['id'], str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', action['id']):
                raise GXWFormatError('add_wire needs a segment handle prefix')
            if not all(endpoint(action[key]) for key in ('from', 'to')):
                raise GXWFormatError('wire endpoint must name one current port or wire tap')
            via = action['via']
            if not isinstance(via, list) or len(via) > 128 or not all(point(p) for p in via):
                raise GXWFormatError('via must contain bounded explicit grid points')
            for start, end in zip(via, via[1:]):
                _segment(start, end)


def _membership(program):
    return {r.offset: index for index, (_, records) in enumerate(program.block_records()) for r in records}


def _segment(start, end):
    a, b = tuple(start), tuple(end)
    if a == b or any(type(v) is not int or not 0 <= v <= 4096 for v in (*a, *b)):
        raise GXWFormatError('visible wire needs bounded distinct integer grid points')
    if a[1] == b[1]:
        return ('h', a[1], min(a[0], b[0]), max(a[0], b[0]))
    if a[0] == b[0]:
        return ('v', a[0], min(a[1], b[1]), max(a[1], b[1]))
    raise GXWFormatError('visible wire requires explicit orthogonal via points')


def _on(point, start, end):
    if tuple(start)==tuple(end):
        return tuple(point)==tuple(start)
    axis, fixed, low, high = _segment(start, end)
    return (point[1] == fixed and low <= point[0] <= high if axis == 'h'
            else point[0] == fixed and low <= point[1] <= high)


def _wire_segment(wire):
    if wire.record_class==3:
        return ('p',wire.start.x,wire.start.y,wire.start.y)
    return _segment((wire.start.x,wire.start.y),(wire.end.x,wire.end.y))


def _handle_segment(wire):
    if wire.get('kind')=='junction':
        if wire['start']!=wire['end']:
            raise GXWFormatError('junction handle requires one coincident point')
        x,y=wire['start']
        return ('p',x,y,y)
    return _segment(wire['start'],wire['end'])


def _geometry(wires):
    """Exact conductive intervals, allowing only collinear native subdivision."""
    grouped = defaultdict(list)
    for wire in wires:
        axis, fixed, low, high = _wire_segment(wire)
        grouped[(axis, fixed)].append((low, high))
    result = []
    for (axis, fixed), intervals in sorted(grouped.items()):
        merged = []
        for low, high in sorted(intervals):
            if merged and low <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], high)
            else:
                merged.append([low, high])
        result.extend([[axis, fixed, low, high] for low, high in merged])
    return result


def _components(program, node_ids, wire_offsets):
    """Keep existing wire-only components in the logical edit contract too."""
    return [set([*(f'{node_ids[p.node_offset]}:{p.port_index}' for p in net.ports),
                 *(f'w:{offset}' for offset in net.wire_offsets if offset in wire_offsets)])
            for net in build_connectivity_graph(program).nets]


def _snapshot(program, node_ids):
    membership = _membership(program)
    graph = build_connectivity_graph(program)
    wires = {wire.offset: wire for wire in program.wires}
    wire_keys = [(membership[w.offset], _wire_segment(w)) for w in program.wires]
    if len(wire_keys) != len(set(wire_keys)):
        raise GXWFormatError('duplicate wire geometry has ambiguous native identity')
    from .source_header import source_payload_offset
    payload = source_payload_offset(program.raw)
    header = bytearray(program.raw[:payload + 17])
    for offset in (payload + 1, payload + 5):
        header[offset:offset + 4] = b'\0' * 4
    nodes = {node_ids[node.offset]: {'block': membership[node.offset],
             'record': serialize_structured_node(node).hex()} for node in program.nodes}
    if len({(r['block'], r['record']) for r in nodes.values()}) != len(nodes):
        raise GXWFormatError('identical node records are ambiguous in the same block')
    nets = []
    for net in graph.nets:
        offsets = [p.node_offset for p in net.ports] + list(net.wire_offsets)
        block, = {membership[offset] for offset in offsets}
        nets.append({'block': block, 'ports': sorted([[node_ids[p.node_offset], p.port_index] for p in net.ports]),
                     'geometry': _geometry([wires[offset] for offset in net.wire_offsets])})
    nets.sort(key=lambda row: (row['block'], row['ports'], row['geometry']))
    return {'nodes': nodes, 'nets': nets, 'trailer': program.trailer.hex(), 'header': header.hex(),
        'blocks': [{'height': block.canvas_height, 'opaque_header': block.raw_header[4:-8].hex(),
                    'unknown': [r.raw.hex() for r in records if r in program.unknown_records]}
                   for block, records in program.block_records()]}


def _bind_nodes(context, snapshot):
    membership = _membership(context.program)
    records = defaultdict(list)
    for node in context.program.nodes:
        records[(membership[node.offset], node.raw.hex())].append(node)
    if len(context.program.nodes) != len(snapshot['nodes']):
        raise GXWFormatError('visible graph node inventory changed')
    matched = {}
    for identity, expected in snapshot['nodes'].items():
        rows = records.get((expected['block'], expected['record']), [])
        if len(rows) != 1:
            raise GXWFormatError('visible graph node missing or ambiguous after save: ' + identity)
        matched[identity] = rows[0]
    return matched


def _checkpoint(program, node_ids, protected, contracts):
    return {**_snapshot(program, node_ids), 'protected_wires': sorted(protected.values(), key=lambda r: (r['block'], r['record'])),
            'wire_fields': list(contracts.values())}


def _port_descriptors(context, node, *, device_profile=None):
    if node.kind in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
        return context.sources.callable(node)['ports']
    if node.kind in (NodeKind.INPUT, NodeKind.OUTPUT,NodeKind.INOUT):
        data_type = _terminal_type(node.symbol, context.sources, device_profile or context.sources.cpu)
        return [{**row, 'side': ({'IN':'in','OUT':'out'}[row['name']] if node.kind==NodeKind.INOUT
                 else 'out' if node.kind==NodeKind.INPUT else 'in'), 'data_type': data_type}
                for row in _ports(node, context.sources)]
    raise GXWFormatError('changed data nets require source-resolved callable or terminal ports')


def _check_nets(context, program, touched, *, device_profile=None):
    nodes = {node.offset: node for node in program.nodes}
    for net in build_connectivity_graph(program).nets:
        if not any((p.node_offset, p.port_index) in touched for p in net.ports):
            continue
        endpoints = {}
        for ref in net.ports:
            descriptors = _port_descriptors(context, nodes[ref.node_offset], device_profile=device_profile)
            endpoints[f'{ref.node_offset}:{ref.port_index}'] = descriptors[ref.port_index]
        outputs = [key for key, port in endpoints.items() if port.get('side') == 'out']
        inputs = [key for key, port in endpoints.items() if port.get('side') == 'in']
        if len(outputs) > 1:
            raise GXWFormatError('visible data net has multiple port writers')
        if outputs:
            validate_typed_connections(endpoints, [{'from': outputs[0], 'to': target} for target in inputs])
        elif len(inputs) > 1:
            # Detached sinks may form a pending branch. Its types must still
            # agree; a temporary synthetic source checks the shared type rule.
            source = '__type_check__'
            endpoints[source] = {**endpoints[inputs[0]], 'side': 'out'}
            validate_typed_connections(endpoints, [{'from': source, 'to': target} for target in inputs])


def _node_anchor(context, identity, node, *, device_profile=None):
    block = _membership(context.program)[node.offset]
    ports = _ports(node, context.sources)
    try:
        ports = _port_descriptors(context, node, device_profile=device_profile)
    except ValueError:
        pass  # An unresolved old terminal can still be explicitly corrected.
    return {'node': identity, 'source_offset': node.offset, 'block': block,
        'kind': node.kind.value, 'symbol': node.symbol, 'type_name': node.type_name,
        'bbox': [node.bbox.left, node.bbox.top, node.bbox.right, node.bbox.bottom],
        'ports': [{'index': i, 'name': port['name'], 'point': [node.port_point(i).x, node.port_point(i).y],
                   **{key: port[key] for key in ('side', 'data_type') if key in port}}
                  for i, port in enumerate(ports)]}


def _wire_anchor(context, identity, expected):
    membership = _membership(context.program)
    wanted = _handle_segment(expected)
    candidates = [w for w in context.program.wires if membership[w.offset] == expected['block']
                  and _wire_segment(w) == wanted]
    if not candidates and wanted[0]!='p':
        axis, fixed, low, high = wanted
        candidates = [w for w in context.program.wires if membership[w.offset] == expected['block']
            and (lambda s: s[:2] == (axis, fixed) and low <= s[2] < s[3] <= high)(
                _wire_segment(w))]
        if _geometry(candidates) != [list(wanted)]:
            raise GXWFormatError('wire no longer has a distinct native segment: ' + identity)
    if not candidates:
        raise GXWFormatError('junction no longer has a distinct native point: '+identity)
    keys = [_wire_segment(w) for w in candidates]
    if len(keys) != len(set(keys)):
        raise GXWFormatError('wire selection is ambiguous: ' + identity)
    return {'wire': identity, 'block': expected['block'], 'start': deepcopy(expected['start']), 'end': deepcopy(expected['end']),
        'segments': [{'source_offset': w.offset, 'start': [w.start.x, w.start.y], 'end': [w.end.x, w.end.y],
                      'point': [(w.start.x + w.end.x) / 2, (w.start.y + w.end.y) / 2]} for w in candidates]}


def _project_guard(raw, prepared):
    from .visible_engineering import _preserved, source_projection
    from .native_write import native_task_scope_context
    from .project_metadata import project_gui_configuration
    _preserved(prepared['before_project'], source_projection(raw), sources=(prepared['owner'],))
    if native_task_scope_context(raw) != prepared['task_scopes']:
        raise GXWFormatError('visible graph edit changed resource/task bindings')
    if project_gui_configuration(raw) != prepared['gui_configuration']:
        raise GXWFormatError('visible graph edit changed GUI allocation configuration')


def _verify_state(raw, prepared, snapshot):
    context = read_project_context(raw, prepared['plan']['caller'])
    matched = _bind_nodes(context, snapshot)
    expected = {key: value for key, value in snapshot.items() if key not in ('protected_wires', 'wire_fields')}
    actual = _snapshot(context.program, {node.offset: identity for identity, node in matched.items()})
    old_header, saved_header = bytes.fromhex(expected['header']), bytes.fromhex(actual['header'])
    normalized, header_change = normalize_visible_save_header(old_header[:54], saved_header[:54], cpu=context.sources.cpu)
    actual['header'] = (normalized + saved_header[54:]).hex()
    if actual != expected:
        raise GXWFormatError('saved visible graph topology, geometry or opaque records differ')
    membership = _membership(context.program)
    current = Counter((membership[wire.offset], wire.raw.hex()) for wire in context.program.wires)
    protected = Counter((row['block'], row['record']) for row in snapshot['protected_wires'])
    if protected - current:
        raise GXWFormatError('visible graph edit changed an unrelated wire record')
    for contract in snapshot['wire_fields']:
        axis, fixed, low, high = contract['geometry']
        if axis=='p':
            if not any(membership[wire.offset]==contract['block'] and wire.record_class==3
                       and list(_wire_segment(wire))==contract['geometry']
                       and list(wire.prefix_fields)==contract['prefix']
                       and wire.suffix==contract['suffix'] and wire.record_length==contract['length']
                       for wire in context.program.wires):
                raise GXWFormatError('visible graph edit changed retained junction fields')
            continue
        coverage = []
        for wire in context.program.wires:
            geometry = _wire_segment(wire)
            if (membership[wire.offset] != contract['block'] or list(wire.prefix_fields) != contract['prefix']
                    or wire.suffix != contract['suffix'] or wire.record_length != contract['length']
                    or geometry[:2] != (axis, fixed)):
                continue
            a, b = max(low, geometry[2]), min(high, geometry[3])
            if a < b:
                start, end = ((Point(a, fixed), Point(b, fixed)) if axis == 'h'
                              else (Point(fixed, a), Point(fixed, b)))
                coverage.append(replace(wire, start=start, end=end))
        if _geometry(coverage) != [contract['geometry']]:
            raise GXWFormatError('visible graph edit changed retained wire opaque fields')
    _project_guard(raw, prepared)
    return context, matched, header_change


def _endpoint(context, program, nodes, handles, endpoint, *, side, device_profile=None):
    if not isinstance(endpoint, dict):
        raise GXWFormatError('wire endpoint must name one current port or wire tap')
    membership = _membership(program)
    if set(endpoint) == {'node', 'port'}:
        node = nodes.get(endpoint['node']) if isinstance(endpoint['node'], str) else None
        if node is None or not isinstance(endpoint['port'], str):
            raise GXWFormatError('wire endpoint node is missing')
        ports = _port_descriptors(context, node, device_profile=device_profile)
        indices = [i for i, port in enumerate(ports) if port['name'] == endpoint['port']]
        if len(indices) != 1:
            raise GXWFormatError('wire endpoint port is missing or ambiguous')
        index, = indices
        if ports[index].get('side') != side:
            raise GXWFormatError('topology port direction mismatch')
        point = node.port_point(index)
        return {'point': [point.x, point.y], 'block': membership[node.offset],
                'ports': {(node.offset, index)}}
    if set(endpoint) == {'wire', 'point'}:
        wire = handles.get(endpoint['wire']) if isinstance(endpoint['wire'], str) else None
        point = endpoint['point']
        if (wire is None or not isinstance(point, list) or len(point) != 2
                or any(type(v) is not int or not 0 <= v <= 4096 for v in point)
                or not _on(point, wire['start'], wire['end'])):
            raise GXWFormatError('wire tap must lie on the selected current wire')
        matching = [w for w in program.wires if membership[w.offset] == wire['block']
                    and _wire_segment(w) == _handle_segment(wire)]
        if len(matching) != 1:
            raise GXWFormatError('wire tap source is ambiguous')
        net = build_connectivity_graph(program).net_for_wire(matching[0].offset)
        if not net.ports:
            raise GXWFormatError('wire tap has no current typed port context')
        if side == 'out' and sum(_port_descriptors(context, _nodes_by_offset(program)[p.node_offset],
                device_profile=device_profile)[p.port_index].get('side') == 'out' for p in net.ports) != 1:
            raise GXWFormatError('source wire tap needs one current driver')
        return {'point': deepcopy(point), 'block': wire['block'],
                'ports': {(p.node_offset, p.port_index) for p in net.ports}}
    raise GXWFormatError('wire endpoint must name one current port or wire tap')


def _nodes_by_offset(program):
    return {node.offset: node for node in program.nodes}


def prepare_graph_edit(raw, command, *, device_profile=None):
    """Plan existing graph edits without exporting or mutating an engineering file.

    ``add_wire.id`` creates segment handles ``id:0``, ``id:1``, ... . Initial
    node/wire handles are the current object model's n<offset>/w<offset>.
    A terminal deletion removes only an INPUT/OUTPUT graphic. Node moves retain
    every local port and native field except the bbox; existing wires keep their
    coordinates, and the existing writer grows the block canvas if necessary.
    """
    if (not isinstance(command, dict) or set(command) != {'operation', 'caller', 'actions'}
            or command['operation'] != OPERATION or not isinstance(command['caller'], str)
            or not isinstance(command['actions'], list) or not 1 <= len(command['actions']) <= 128):
        raise GXWFormatError('visible graph edit requires caller and 1 to 128 explicit actions')
    validate_graph_actions(command['actions'])
    context = read_project_context(raw, command['caller'])
    if command['caller'] != context.program.logical_name:
        raise GXWFormatError('visible graph caller must name the current logical source')
    if device_profile is not None:
        from gxworks2.project_profiles import native_project_profile
        actual, selected = native_project_profile(context.sources.cpu), native_project_profile(device_profile)
        if actual is None or selected is None or actual.cpu != selected.cpu:
            raise GXWFormatError('visible graph address profile differs from the current CPU')
    if context.program.raw[source_payload_offset(context.program.raw)] != 208:
        raise GXWFormatError('visible graph edits require a current FBD source')
    if any(serialize_structured_node(node) != node.raw for node in context.program.nodes):
        raise GXWFormatError('visible graph requires canonical complete native node records')
    model = context.object_model()
    node_ids = {node.offset: 'n' + str(node.offset) for node in context.program.nodes}
    membership = _membership(context.program)
    protected = {wire.offset: {'block': membership[wire.offset], 'record': wire.raw.hex()}
                 for wire in context.program.wires}
    contracts = {wire.offset: {'block': membership[wire.offset], 'geometry': list(_wire_segment(wire)),
                'prefix': list(wire.prefix_fields), 'suffix': wire.suffix, 'length': wire.record_length}
                 for wire in context.program.wires}
    initial = _checkpoint(context.program, node_ids, protected, contracts)
    handles = {'w' + str(wire.offset): {**row, 'block': membership[wire.offset]}
               for wire, row in zip(context.program.wires, model['wires'])}
    from .visible_engineering import source_projection
    from .native_write import native_task_scope_context
    from .project_metadata import project_gui_configuration
    prepared = {'kind': 'graph_edit', 'plan': {'caller': context.program.logical_name,
        'cpu': context.sources.cpu, 'actions': deepcopy(command['actions']), 'steps': []},
        'before_project': source_projection(raw), 'task_scopes': native_task_scope_context(raw),
        'gui_configuration': project_gui_configuration(raw), 'owner': context.declarations[context.sources.local_table].owner_name,
        'initial': initial, 'device_profile': device_profile}
    program = context.program
    for index, action in enumerate(command['actions']):
        kind = action.get('action')
        before = _checkpoint(program, node_ids, protected, contracts)
        current_nodes = {identity: node for node in program.nodes for identity in (node_ids[node.offset],)}
        step = {'index': index, 'action': deepcopy(action), 'before': before,
                'wire_handles': deepcopy(handles)}
        touched = set()
        removed_wires = set()
        if kind in ('rebind_terminal', 'delete_terminal', 'move_node'):
            node = current_nodes.get(action['node']) if isinstance(action['node'], str) else None
            allowed = ((NodeKind.INPUT, NodeKind.OUTPUT,NodeKind.INOUT, NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK)
                       if kind == 'move_node' else (NodeKind.INPUT, NodeKind.OUTPUT,NodeKind.INOUT))
            if node is None or node.kind not in allowed:
                raise GXWFormatError(kind + ' requires an existing ' +
                                     ('data node' if kind == 'move_node' else 'terminal node'))
            # A deleted or moved port can leave its former neighbours connected
            # to each other. Validate those remaining nets as well as the moved
            # node's new contacts; the old coordinates of every wire stay fixed.
            graph = build_connectivity_graph(program)
            for port in range(len(node.ports)):
                touched.update((p.node_offset, p.port_index) for p in graph.net_for_port(node.offset, port).ports)
            if kind == 'rebind_terminal':
                if not isinstance(action['symbol'], str) or not action['symbol'] or action['symbol'] == node.symbol:
                    raise GXWFormatError('rebind_terminal requires a different explicit symbol')
                model = edit_draft(model, {'action': 'update_node', 'id': action['node'],
                    'field': 'symbol', 'value': action['symbol']}, context=context)['model']
            elif kind == 'delete_terminal':
                model = edit_draft(model, {'action': 'delete_node', 'id': action['node']}, context=context)['model']
            else:
                x, y = action['x'], action['y']
                width, height = node.bbox.right - node.bbox.left, node.bbox.bottom - node.bbox.top
                if (any(type(v) is not int or v < 0 for v in (x, y)) or x + width > 4096 or y + height > 4096
                        or (x, y) == (node.bbox.left, node.bbox.top)):
                    raise GXWFormatError('move_node requires a different bounded integer position')
                for field, value in (('x', x), ('y', y)):
                    model = edit_draft(model, {'action': 'update_node', 'id': action['node'],
                        'field': field, 'value': value}, context=context)['model']
        elif kind == 'delete_wire':
            handle = handles.get(action['wire']) if isinstance(action['wire'], str) else None
            if handle is None:
                raise GXWFormatError('visible source wire is missing')
            matches = [i for i, wire in enumerate(model['wires']) if wire is handle or wire == handle
                or wire.get('source_offset') == handle.get('source_offset') and 'source_offset' in handle
                or wire.get('start') == handle['start'] and wire.get('end') == handle['end']
                    and wire.get('block', 0) == handle['block']]
            if len(matches) != 1:
                raise GXWFormatError('visible source wire is ambiguous')
            # Capture actual connected endpoints before removing the record.
            target = _wire_anchor(_context_for_program(context, program), action['wire'], handle)
            for segment in target['segments']:
                removed_wires.add(segment['source_offset'])
                touched.update((p.node_offset, p.port_index) for p in
                    build_connectivity_graph(program).net_for_wire(segment['source_offset']).ports)
            model = edit_draft(model, {'action': 'delete_wire', 'index': matches[0]}, context=context)['model']
            if 'source_offset' in handle:
                contracts.pop(handle['source_offset'], None)
            del handles[action['wire']]
        else:
            identity = action['id']
            if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', identity) or any(
                    key.startswith(identity + ':') for key in handles):
                raise GXWFormatError('add_wire needs a unique segment handle prefix')
            left = _endpoint(context, program, current_nodes, handles, action['from'], side='out', device_profile=device_profile)
            right = _endpoint(context, program, current_nodes, handles, action['to'], side='in', device_profile=device_profile)
            if left['block'] != right['block']:
                raise GXWFormatError('visible connections cannot cross block boundaries')
            via = action['via']
            if (not isinstance(via, list) or len(via) > 128 or any(not isinstance(p, list) or len(p) != 2 for p in via)):
                raise GXWFormatError('via must contain bounded explicit grid points')
            points = [left['point'], *via, right['point']]
            touched = left['ports'] | right['ports']
            block = left['block']
            for number, (a, b) in enumerate(zip(points, points[1:])):
                segment = _segment(a, b)
                if max(a[1], b[1]) > list(program.block_records())[block][0].canvas_height:
                    raise GXWFormatError('wire path exceeds the current block canvas')
                if any(_handle_segment(row) == segment and row['block'] == block for row in handles.values()):
                    raise GXWFormatError('duplicate visible wire segment')
                axis, fixed, low, high = segment
                for node in program.nodes:
                    if _membership(program)[node.offset] != block:
                        continue
                    box = node.bbox
                    crosses = (box.top < fixed < box.bottom and max(low, box.left) < min(high, box.right)
                               if axis == 'h' else box.left < fixed < box.right and max(low, box.top) < min(high, box.bottom))
                    if crosses:
                        raise GXWFormatError('wire path crosses a current node interior')
                row = {'start': deepcopy(a), 'end': deepcopy(b)}
                if len(program.blocks) > 1:
                    row['block'] = block
                model['wires'].append(row)
                handles[f'{identity}:{number}'] = {**row, 'block': block}
        if kind in ('add_wire', 'delete_wire'):
            # Wires in the touched conductive nets may be subdivided natively.
            # All other native record fields remain byte-for-byte protected.
            for net in build_connectivity_graph(program).nets:
                if any((p.node_offset, p.port_index) in touched for p in net.ports) or removed_wires.intersection(net.wire_offsets):
                    for offset in net.wire_offsets:
                        protected.pop(offset, None)
        updated = build_object_program(context.program, model, sources=context.sources)
        _check_nets(context, updated, touched, device_profile=device_profile)
        after = _checkpoint(updated, node_ids, protected, contracts)
        if kind == 'add_wire':
            wire_offsets = {wire.offset for wire in program.wires}
            expected_groups = _components(program, node_ids, wire_offsets)
            selected = {f'{node_ids[offset]}:{port}' for offset, port in touched}
            joined = set()
            rest = []
            for group in expected_groups:
                if group & selected:
                    joined.update(group)
                else:
                    rest.append(group)
            if joined:
                rest.append(joined)
            actual = _components(updated, node_ids, wire_offsets)
            if sorted(map(sorted, (group for group in rest if group))) != sorted(map(sorted, (group for group in actual if group))):
                raise GXWFormatError('wire path joins unintended current ports or nets')
        step['after'] = after
        prepared['plan']['steps'].append(step)
        program = updated
    prepared['expected'] = _checkpoint(program, node_ids, protected, contracts)
    prepared['model'] = model
    return prepared


def _context_for_program(context, program):
    return replace(context, program=program)


def graph_edit_step(raw, prepared, index):
    """Rebind native offsets and return actionable anchors from the latest save."""
    if type(index) is not int or not 0 <= index < len(prepared['plan']['steps']):
        raise GXWFormatError('visible graph action index is outside the frozen plan')
    step = prepared['plan']['steps'][index]
    context, matched, _ = _verify_state(raw, prepared, step['before'])
    action = step['action']
    result = {'index': index, 'action': action['action'], 'caller': prepared['plan']['caller']}
    if action['action'] in ('rebind_terminal', 'delete_terminal', 'move_node'):
        node = matched[action['node']]
        result['node'] = _node_anchor(context, action['node'], node, device_profile=prepared['device_profile'])
        if action['action'] == 'rebind_terminal':
            result['symbol'] = action['symbol']
        elif action['action'] == 'move_node':
            dx, dy = action['x'] - node.bbox.left, action['y'] - node.bbox.top
            result.update(x=action['x'], y=action['y'], delta=[dx, dy],
                          target_bbox=[node.bbox.left + dx, node.bbox.top + dy,
                                       node.bbox.right + dx, node.bbox.bottom + dy])
    elif action['action'] == 'delete_wire':
        result['wire'] = _wire_anchor(context, action['wire'], step['wire_handles'][action['wire']])
    else:
        result.update(id=action['id'], via=deepcopy(action['via']))
        for name in ('from', 'to'):
            endpoint = action[name]
            if 'node' in endpoint:
                node = matched[endpoint['node']]
                anchor = _node_anchor(context, endpoint['node'], node, device_profile=prepared['device_profile'])
                port, = [p for p in anchor['ports'] if p['name'] == endpoint['port']]
                result[name] = {'node': anchor, 'port': endpoint['port'], 'point': port['point'], 'block': anchor['block']}
            else:
                result[name] = {**_wire_anchor(context, endpoint['wire'], step['wire_handles'][endpoint['wire']]),
                                'point': deepcopy(endpoint['point'])}
    return result


def verify_graph_edit_step(saved, prepared, index):
    if type(index) is not int or not 0 <= index < len(prepared['plan']['steps']):
        raise GXWFormatError('visible graph action index is outside the frozen plan')
    _, _, header_change = _verify_state(saved, prepared, prepared['plan']['steps'][index]['after'])
    return {'status': 'matched', 'index': index, 'action': prepared['plan']['steps'][index]['action']['action'],
            'source_header_changes': [{'source': prepared['plan']['caller'], **header_change}] if header_change else []}


def verify_graph_edit_group(saved, prepared, start, end):
    """Verify one explicit move + wire request as a combined final state.

    Callers must check ``graph_edit_step(raw, prepared, start)`` before the
    native action. This does not verify an intermediate move-only checkpoint
    or infer unrequested wires from native AutoConnect behaviour.
    """
    steps = prepared['plan']['steps']
    if (type(start) is not int or type(end) is not int or end != start + 1
            or not 0 <= start < end < len(steps)):
        raise GXWFormatError('visible graph action group requires exactly two adjacent action indices')
    actions = [steps[index]['action']['action'] for index in (start, end)]
    if actions != ['move_node', 'add_wire']:
        raise GXWFormatError('visible graph action group only covers an explicit move_node then add_wire')
    _, _, header_change = _verify_state(saved, prepared, steps[end]['after'])
    return {'status': 'matched', 'action_group': actions, 'indices': [start, end],
            'checkpoint': 'complete_after_requested_actions', 'intermediate_checkpoint': 'not_verified',
            'source_header_changes': [{'source': prepared['plan']['caller'], **header_change}] if header_change else []}


def verify_graph_edit(raw, command, saved, prepared=None):
    prepared = prepared or prepare_graph_edit(raw, command)
    if command != {'operation': OPERATION, 'caller': prepared['plan']['caller'], 'actions': prepared['plan']['actions']}:
        raise GXWFormatError('visible graph command differs from the frozen plan')
    _verify_state(raw, prepared, prepared['initial'])
    _, _, header_change = _verify_state(saved, prepared, prepared['expected'])
    return {'status': 'matched', 'caller': prepared['plan']['caller'], 'actions': len(prepared['plan']['steps']),
            'nets': len(prepared['expected']['nets']), 'node_identity': 'unique_complete_record_and_block',
            'topology': 'complete_port_partition', 'native_compile': 'not_executed', 'execution': 'unverified',
            'source_header_changes': [{'source': prepared['plan']['caller'], **header_change}] if header_change else []}

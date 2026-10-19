"""Core projection and semantic target resolution for current native graphs.

Native memory layout stays in the C# adapter. IDs identify one observed network
membership; target anchors retain semantic fields and are resolved again after
object reconstruction. No prior process address is accepted as an edit target.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import re
from types import SimpleNamespace

from .callable_sources import ProjectCallableSources
from .connectivity import build_connectivity_graph
from .models import (GXWFormatError, NodeKind, Point, PortDescriptor, Rect,
                     StructuredBlock, StructuredNode, StructuredProgram, StructuredWire,
                     node_kind_from_code)
from .object_model import _ports, native_catalog


def _integer(value, *, minimum=0, maximum=32767):
    if type(value) is not int or not minimum <= value <= maximum:
        raise GXWFormatError('Active graph coordinate or membership is outside its supported range')
    return value


def _native_ports(raw):
    connections = raw.get('connections')
    if not isinstance(connections, dict) or connections.get('status') != 'observed':
        raise GXWFormatError('Native connection header is unavailable or not built')
    ports = connections.get('ports')
    if not isinstance(ports, list) or len(ports) > 512:
        raise GXWFormatError('Native connection array is incomplete or outside its bound')
    if _integer(connections.get('port_count', len(ports)), maximum=512) != len(ports):
        raise GXWFormatError('Native connection array is incomplete or outside its bound')
    result = []
    for index, port in enumerate(ports):
        if (not isinstance(port, dict) or not isinstance(port.get('name'), str)
                or _integer(port.get('index', index), maximum=511) != index):
            raise GXWFormatError('Native connection order or name is unavailable')
        x, y = _integer(port.get('local_x')), _integer(port.get('local_y'))
        flags = _integer(port.get('flags'), maximum=65535)
        mask = port.get('native_type_mask')
        if mask is not None:
            _integer(mask, maximum=0xffffffff)
        result.append({'name': port['name'], 'x': x, 'y': y, 'flags': flags,
                       'native_index': index, 'native_type_mask': mask})
    return result


def _geometry_node(raw, kind, bbox, ports):
    """An interface-only model with no invented GXW record or source envelope."""
    return StructuredNode(offset=raw['index'], record_length=0,
        kind_code=raw['source_kind_code'], kind=kind,
        symbol=raw.get('type_name') if kind == NodeKind.FUNCTION else raw['name'],
        type_name=raw.get('type_name') if kind == NodeKind.FUNCTION_BLOCK else None,
        bbox=bbox, ports=tuple(PortDescriptor(0, p['flags'], p['x'], p['y'], b'') for p in ports),
        object_flag=None, reserved=None, raw=b'')


def _port_name_key(value):
    # GX renders a reserved formal such as Next as @'Next'. Recognize only
    # that complete wrapper around the existing Core declaration identifier
    # shape; never trim punctuation or guess an unknown escape sequence.
    quoted = re.fullmatch(r"@'([^\W\d]\w*)'", value, re.UNICODE)
    return (quoted[1] if quoted else value).casefold()


def _project_ports(raw, kind, bbox, owner, sources):
    if kind not in (NodeKind.INPUT, NodeKind.OUTPUT, NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
        raise GXWFormatError('This native node kind has no verified active port projection')
    if kind in (NodeKind.INPUT, NodeKind.OUTPUT):
        native = native_catalog()[kind.value]
        if (bbox.right-bbox.left, bbox.bottom-bbox.top) != (
                native.bbox.right-native.bbox.left, native.bbox.bottom-native.bbox.top):
            raise GXWFormatError('Native terminal geometry differs from the stock terminal')
        # Old observations without connection transport retain the established
        # terminal-only projection. An explicit lazy/unavailable state cannot
        # borrow a template and is handled by _native_ports instead.
        if 'connections' not in raw:
            return _ports(replace(native, symbol=raw['name'], bbox=bbox))
        actual = _native_ports(raw)
        expected = _ports(native)
        if len(actual) != len(native.ports):
            raise GXWFormatError('Native terminal connection count differs from its stock geometry')
        result = []
        for connection, descriptor, named in zip(actual, native.ports, expected):
            if ((connection['x'], connection['y']) != (descriptor.local_x, descriptor.local_y)
                    or connection['flags'] & 1 != descriptor.port_kind_code & 1):
                raise GXWFormatError('Native terminal connection geometry or direction differs from stock')
            if connection['name'] and connection['name'].casefold() != named['name'].casefold():
                raise GXWFormatError('Native terminal connection name differs from its verified stock port')
            result.append({**named, 'x': connection['x'], 'y': connection['y'],
                           'native_index': connection['native_index'], 'native_name': connection['name'],
                           'flags': connection['flags'], 'side': 'in' if connection['flags'] & 1 else 'out',
                           'native_type_mask': connection['native_type_mask'], 'native_type_status': 'unavailable'})
        return result
    if not isinstance(sources, ProjectCallableSources) or sources.program_name != owner + '.Program.pou':
        raise GXWFormatError('Callable ports require current sources bound to this native owner')
    type_name = raw.get('type_name')
    if not isinstance(type_name, str) or not type_name:
        raise GXWFormatError('The native callable has no current definition identity')
    actual = _native_ports(raw)
    if not actual or any(not p['name'] for p in actual):
        raise GXWFormatError('Callable native connection names must be present')
    identities = [(_port_name_key(p['name']), bool(p['flags'] & 1)) for p in actual]
    if len(set(identities)) != len(identities):
        raise GXWFormatError('Callable native connection name and direction are ambiguous')
    node = _geometry_node(raw, kind, bbox, actual)
    # This existing Core operation checks the definition, supported port flags,
    # geometry and FB instance declaration. Do not use _ports' fallback names
    # when binding failed. CSV order alone does not establish a native port.
    binding = sources.callable(node)
    ports = binding['ports']
    if len(ports) != len(actual):
        raise GXWFormatError('Current source interface differs from native connections')
    formal_identities = [(_port_name_key(p['formal_name']), p['side']) for p in ports]
    if len(set(formal_identities)) != len(formal_identities):
        raise GXWFormatError('Current source formal name and direction are ambiguous after quote normalization')
    definitions = {id(definition): definition for entries in sources.catalog.values()
                   for stream, definition in entries
                   if stream == binding['source_stream'] and definition['offset'] == binding['definition_offset']}
    if len(definitions) != 1:
        raise GXWFormatError('The bound callable source definition is ambiguous')
    definition = next(iter(definitions.values()))
    result = []
    for connection, formal in zip(actual, ports):
        if (_port_name_key(connection['name']) != _port_name_key(formal['formal_name'])
                or bool(connection['flags'] & 1) != (formal['side'] == 'in')
                or (connection['x'], connection['y']) != (formal['x'], formal['y'])):
            raise GXWFormatError('Native connection name, direction or geometry differs from current source formal')
        expected_mask = sources._formal_type(definition, formal['data_type'])
        mask = connection['native_type_mask']
        # Zero is a native precompile/unknown observation, not proof of an exact
        # type. The current declaration still supplies its own declared type.
        if mask and expected_mask is not None and mask != expected_mask:
            raise GXWFormatError('Native connection type mask differs from the current source formal')
        result.append({**formal, 'native_index': connection['native_index'], 'native_name': connection['name'],
            'flags': connection['flags'], 'native_type_mask': mask,
            'native_type_status': 'matched' if mask and mask == expected_mask else 'unavailable'})
    return result


def active_graph_projection(observation, *, sources=None):
    if (not isinstance(observation, dict) or observation.get('status') != 'observed'
            or observation.get('protocol_version') != 1 or observation.get('native_version') != '1.635.0.1'
            or observation.get('owner_kind') not in ('program', 'fb')
            or not isinstance(observation.get('owner'), str) or not observation['owner']
            or not isinstance(observation.get('networks'), list) or len(observation['networks']) > 512):
        raise GXWFormatError('Current active graph identity or observation is incomplete')
    networks, addresses = [], set()
    for index, source in enumerate(observation['networks']):
        if source.get('index') != index:
            raise GXWFormatError('Native network membership is not a complete ordered partition')
        nodes = []
        for node_index, raw in enumerate(source['nodes']):
            if raw.get('index') != node_index or raw.get('address') in addresses:
                raise GXWFormatError('A native element belongs to more than one observed network')
            addresses.add(raw.get('address'))
            bounds = raw.get('bounds')
            if not isinstance(bounds, list) or len(bounds) != 4:
                raise GXWFormatError('Native node geometry is unavailable')
            left, top, right, bottom = [_integer(v) for v in bounds]
            if right <= left or bottom <= top:
                raise GXWFormatError('The active node does not have a supported positive rectangle')
            kind = node_kind_from_code(raw.get('source_kind_code'))
            symbol = raw.get('name')
            if not isinstance(symbol, str):
                raise GXWFormatError('The active node name was not read')
            template = kind.value
            if kind in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
                type_name = raw.get('type_name')
                if isinstance(type_name, str) and type_name:
                    template += ':' + type_name
                    if kind == NodeKind.FUNCTION:
                        symbol = type_name
            diagnostic = None
            try:
                ports = _project_ports(raw, kind, Rect(left, top, right, bottom), observation['owner'], sources)
            except GXWFormatError as error:
                ports = []
                diagnostic = {'code': 'active_ports_unavailable', 'message': str(error)}
            nodes.append({'id': f'b{index}:n{node_index}', 'network': index, 'index': node_index,
                'template': template, 'symbol': symbol, 'x': left, 'y': top,
                'width': right-left, 'height': bottom-top, 'ports': ports,
                'selected': raw.get('selected') is True,
                'ports_status': 'resolved' if ports else 'unavailable',
                'native_type_status': 'matched' if ports and all(
                    p.get('native_type_status') == 'matched' for p in ports) else 'unavailable',
                'ports_diagnostic': diagnostic})
        wires = []
        for wire_index, wire in enumerate(source['wires']):
            if wire.get('index') != wire_index:
                raise GXWFormatError('Native wire membership is incomplete')
            start, end = wire.get('start'), wire.get('end')
            if not all(isinstance(point, list) and len(point) == 2 for point in (start, end)):
                raise GXWFormatError('Native wire endpoints are unavailable')
            for value in (*start, *end):
                _integer(value)
            wires.append({'id': f'b{index}:w{wire_index}', 'network': index, 'index': wire_index,
                          'start': list(start), 'end': list(end), 'selected': wire.get('selected') is True})
        networks.append({'index': index, 'nodes': nodes, 'wires': wires,
                         'junction_count': len(source['junctions'])})
    if not networks or observation.get('active_network') not in range(len(networks)):
        raise GXWFormatError('The active network is unavailable')
    return {'schema_version': 1, 'owner': observation['owner'], 'owner_kind': observation['owner_kind'],
            'native_version': observation['native_version'], 'active_network': observation['active_network'],
            'networks': networks}


def _network_geometry(graph, network):
    """Private connectivity input, deliberately lacking a saved-file envelope."""
    nodes = []
    for node in network['nodes']:
        kind = NodeKind(node['template'].split(':', 1)[0])
        ports = tuple(PortDescriptor(0, p.get('flags', 1 if kind == NodeKind.OUTPUT else 0),
                                     p['x'], p['y'], b'') for p in node['ports'])
        code = {NodeKind.FUNCTION: 1, NodeKind.FUNCTION_BLOCK: 2, NodeKind.INPUT: 13, NodeKind.OUTPUT: 14}.get(kind, 0)
        nodes.append(StructuredNode(node['index'], 0, code, kind, node['symbol'],
            Rect(node['x'], node['y'], node['x']+node['width'], node['y']+node['height']),
            ports, None, None, b'',
            node['template'].partition(':')[2] if kind == NodeKind.FUNCTION_BLOCK else None))
    wires = tuple(StructuredWire(len(nodes)+w['index'], 0, Point(*w['start']), Point(*w['end']),
                                 (), 0, b'') for w in network['wires'])
    offsets = tuple(n.offset for n in nodes) + tuple(w.offset for w in wires)
    return StructuredProgram(graph['owner'], None, len(offsets),
        max((n.bbox.bottom for n in nodes), default=0), 0, tuple(nodes), wires, (), b'', b'',
        (StructuredBlock(0, 0, 0, offsets, b''),))


def active_graph_connections(observation, *, sources=None):
    """Electrical geometry for this observation; never a serializable GXW model."""
    graph = active_graph_projection(observation, sources=sources)
    nets, unresolved = [], []
    for network in graph['networks']:
        for node in network['nodes']:
            if node['ports_status'] != 'resolved':
                unresolved.append({'network': network['index'], 'node': node['id'],
                                   'diagnostic': node['ports_diagnostic']})
        if network['junction_count']:
            unresolved.append({'network': network['index'], 'diagnostic': {
                'code': 'active_junction_geometry_unavailable',
                'message': 'Native FIXPOINT semantics are not represented by this observation'}})
        model = _network_geometry(graph, network)
        for net in build_connectivity_graph(model).nets:
            nets.append({'id': f"b{network['index']}:net{net.index}", 'network': network['index'],
                'ports': [{'node': network['nodes'][p.node_offset]['id'],
                           'port': network['nodes'][p.node_offset]['ports'][p.port_index]['name'],
                           'port_index': p.port_index,
                           'point': [p.point.x, p.point.y]} for p in net.ports],
                'wires': [network['wires'][offset-len(model.nodes)]['id'] for offset in net.wire_offsets]})
    return {'schema_version': 1, 'owner': graph['owner'], 'owner_kind': graph['owner_kind'],
            'status': 'partial' if unresolved else 'resolved', 'nets': nets, 'unresolved': unresolved}


def graph_target_anchor(observation, selector):
    """Freeze an unambiguous semantic target, excluding addresses and indexes."""
    graph = active_graph_projection(observation)
    if (not isinstance(selector, dict) or set(selector) - {'network', 'id', 'template', 'symbol', 'x', 'y'}
            or 'network' not in selector or not any(key in selector for key in ('id', 'template', 'symbol', 'x', 'y'))):
        raise GXWFormatError('An active graph target requires explicit network and node identity')
    network = _integer(selector['network'], maximum=len(graph['networks'])-1)
    matches = [node for node in graph['networks'][network]['nodes']
               if all(node[key] == value for key, value in selector.items())]
    if len(matches) != 1:
        raise GXWFormatError('The requested active node is missing or ambiguous')
    node = matches[0]
    semantic = {key: node[key] for key in ('network', 'template', 'symbol', 'x', 'y', 'width', 'height')}
    if sum(all(candidate[key] == value for key, value in semantic.items())
           for candidate in graph['networks'][network]['nodes']) != 1:
        raise GXWFormatError('The semantic node identity is ambiguous after reconstruction')
    return {'owner': graph['owner'], 'owner_kind': graph['owner_kind'], 'semantic': semantic}


def resolve_graph_target(observation, anchor):
    graph = active_graph_projection(observation)
    if (not isinstance(anchor, dict) or set(anchor) != {'owner', 'owner_kind', 'semantic'}
            or anchor['owner'] != graph['owner'] or anchor['owner_kind'] != graph['owner_kind']):
        raise GXWFormatError('The target belongs to another native Program or FB')
    semantic = anchor['semantic']
    if not isinstance(semantic, dict) or set(semantic) != {'network', 'template', 'symbol', 'x', 'y', 'width', 'height'}:
        raise GXWFormatError('The semantic active-node anchor is incomplete')
    network = _integer(semantic['network'], maximum=len(graph['networks'])-1)
    matches = [node for node in graph['networks'][network]['nodes']
               if all(node[key] == value for key, value in semantic.items())]
    if len(matches) != 1:
        raise GXWFormatError('The active target changed or became ambiguous; read current context before editing')
    node = matches[0]
    native = observation['networks'][network]['nodes'][node['index']]
    return {'network': network, 'index': node['index'], 'expected_record': native['record']}


def graph_wire_anchor(observation, selector):
    """Bind one actual native line by undirected orthogonal geometry."""
    from .visible_graph import _segment
    graph = active_graph_projection(observation)
    if (not isinstance(selector, dict) or set(selector) - {'network', 'id', 'start', 'end'}
            or 'network' not in selector or not ('id' in selector or {'start', 'end'} <= selector.keys())
            or bool({'start', 'end'} & selector.keys()) != ({'start', 'end'} <= selector.keys())):
        raise GXWFormatError('An active wire requires explicit network and current line identity')
    network = _integer(selector['network'], maximum=len(graph['networks'])-1)
    requested = _segment(selector['start'], selector['end']) if 'start' in selector else None
    matches = [wire for wire in graph['networks'][network]['wires']
               if ('id' not in selector or wire['id'] == selector['id'])
               and (requested is None or _segment(wire['start'], wire['end']) == requested)]
    if len(matches) != 1:
        raise GXWFormatError('The active wire is missing or ambiguous')
    segment = list(_segment(matches[0]['start'], matches[0]['end']))
    if sum(list(_segment(wire['start'], wire['end'])) == segment
           for wire in graph['networks'][network]['wires']) != 1:
        raise GXWFormatError('Duplicate native wire geometry cannot identify an edit target')
    return {'owner': graph['owner'], 'owner_kind': graph['owner_kind'],
            'semantic': {'network': network, 'segment': segment}}


def resolve_graph_wire_target(observation, anchor):
    from .visible_graph import _segment
    graph = active_graph_projection(observation)
    if (not isinstance(anchor, dict) or set(anchor) != {'owner', 'owner_kind', 'semantic'}
            or anchor['owner'] != graph['owner'] or anchor['owner_kind'] != graph['owner_kind']):
        raise GXWFormatError('The wire target belongs to another native Program or FB')
    semantic = anchor['semantic']
    if not isinstance(semantic, dict) or set(semantic) != {'network', 'segment'}:
        raise GXWFormatError('The active wire anchor is incomplete')
    network = _integer(semantic['network'], maximum=len(graph['networks'])-1)
    matches = [wire for wire in graph['networks'][network]['wires']
               if list(_segment(wire['start'], wire['end'])) == semantic['segment']]
    if len(matches) != 1:
        raise GXWFormatError('The active wire changed or became ambiguous; read current context')
    wire = matches[0]
    native = observation['networks'][network]['wires'][wire['index']]
    return {'network': network, 'index': wire['index'], 'expected_record': native['record'],
            'element_kind': 'wire'}


def graph_source_state(observation, *, sources=None):
    """Compare source meaning independently of viewport, selection and pointers."""
    graph = active_graph_projection(observation, sources=sources)
    result = deepcopy(graph)
    result.pop('active_network')
    for network in result['networks']:
        for element in (*network['nodes'], *network['wires']):
            element.pop('selected')
        for node, raw in zip(network['nodes'], observation['networks'][network['index']]['nodes']):
            connections = raw.get('connections')
            if isinstance(connections, dict):
                if not isinstance(connections.get('ports'), list) or any(
                        not isinstance(port, dict) for port in connections['ports']):
                    raise GXWFormatError('Native connection evidence is incomplete during source comparison')
                # Unresolved formal identities must not hide actual native
                # interface changes during a guarded edit. Exclude addresses,
                # raw pointer bytes and selection; retain observed semantics.
                node['native_connections'] = {'status': connections.get('status'),
                    'ports': [{key: port.get(key) for key in ('name', 'local_x', 'local_y', 'flags', 'native_type_mask')}
                              for port in connections.get('ports', [])]}
    return result


def _action_state(observation, *, sources=None):
    """Order-independent node meaning and exact conductive wire intervals."""
    from .visible_graph import _geometry
    state = graph_source_state(observation, sources=sources)
    if sources is not None:
        from .topology import _terminal_type
        state['source_binding'] = {'cpu': sources.cpu, 'program': sources.program_name}
    for network in state['networks']:
        model = _network_geometry(state, network)
        wires_by_offset = {wire.offset: wire for wire in model.wires}
        nets = []
        for net in build_connectivity_graph(model).nets:
            ports = []
            for ref in net.ports:
                node = network['nodes'][ref.node_offset]
                ports.append({'node': {key: node[key] for key in
                    ('network', 'template', 'symbol', 'x', 'y', 'width', 'height')},
                    'port': node['ports'][ref.port_index]['name'], 'point': [ref.point.x, ref.point.y]})
            ports.sort(key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
            nets.append({'ports': ports, 'geometry': _geometry([wires_by_offset[offset] for offset in net.wire_offsets])})
        network['nets'] = sorted(nets, key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
        for node in network['nodes']:
            if sources is not None and node['template'] in ('input', 'output'):
                try:
                    node['value_data_type'] = _terminal_type(node['symbol'], sources, sources.cpu)
                except ValueError:
                    node['value_data_type'] = None
            node.pop('id')
            node.pop('index')
            for port in node['ports']:
                port.pop('native_index', None)
            node['ports'].sort(key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
            if 'native_connections' in node:
                node['native_connections']['ports'].sort(key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
        network['nodes'].sort(key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
        # Geometry consumes only wires; native subdivision is not a new net.
        wires = tuple(StructuredWire(i, 0, Point(*w['start']), Point(*w['end']), (), 0, b'')
                      for i, w in enumerate(network.pop('wires')))
        network['geometry'] = _geometry(wires)
    return state


def _action_node(observation, network, value):
    if isinstance(value, str):
        anchor = graph_target_anchor(observation, {'network': network, 'id': value})
    elif isinstance(value, dict) and set(value) == {'owner', 'owner_kind', 'semantic'}:
        anchor = deepcopy(value)
    elif isinstance(value, dict):
        if value.get('network', network) != network:
            raise GXWFormatError('Active graph action cannot target another network')
        anchor = graph_target_anchor(observation, {**value, 'network': network})
    else:
        raise GXWFormatError('Active node needs a current ID or semantic anchor')
    target = resolve_graph_target(observation, anchor)
    if target['network'] != network:
        raise GXWFormatError('Active graph action cannot target another network')
    return anchor, target['index']


def _action_wire(observation, network, value):
    if isinstance(value, str):
        anchor = graph_wire_anchor(observation, {'network': network, 'id': value})
    elif isinstance(value, dict) and set(value) == {'owner', 'owner_kind', 'semantic'}:
        anchor = deepcopy(value)
    elif isinstance(value, dict):
        if value.get('network', network) != network:
            raise GXWFormatError('Active graph action cannot target another network')
        anchor = graph_wire_anchor(observation, {**value, 'network': network})
    else:
        raise GXWFormatError('Active wire needs a current ID or semantic anchor')
    target = resolve_graph_wire_target(observation, anchor)
    if target['network'] != network:
        raise GXWFormatError('Active graph action cannot target another network')
    return anchor, target['index']


def validate_add_terminal_action(action):
    """Check live-only input-terminal syntax without reading current sources."""
    if (not isinstance(action, dict)
            or set(action) != {'action', 'network', 'template', 'symbol', 'x', 'y'}
            or action['action'] != 'add_terminal' or action['template'] != 'input'
            or not isinstance(action['symbol'], str) or len(action['symbol']) > 255
            or re.fullmatch(r'[^\W\d]\w*', action['symbol'], re.UNICODE) is None):
        raise GXWFormatError('Live add_terminal requires an input template and one declaration identifier')
    _integer(action['network'], maximum=511)
    for key in ('x', 'y'):
        _integer(action[key], maximum=4094)


def _wire_inventory(graph):
    from .visible_graph import _segment
    return [sorted(list(_segment(wire['start'], wire['end'])) for wire in network['wires'])
            for network in graph['networks']]


def _input_declaration_type(symbol, sources, device_profile):
    from .topology import _terminal_type
    if sources.label(symbol) is None or _terminal_type(symbol, sources, device_profile).upper() != 'BOOL':
        raise GXWFormatError('Live input creation currently requires a declared BOOL label')
    return _terminal_type(symbol, sources, device_profile)


def _prepare_add_terminal(observation, graph, action, *, sources, device_profile):
    from .visible_graph import _segment
    validate_add_terminal_action(action)
    if graph['owner_kind'] != 'program':
        raise GXWFormatError('Live input placement coordinates are verified only for Program pages')
    if action['network'] != 0:
        raise GXWFormatError('Live input placement is verified only in the current network zero')
    data_type = _input_declaration_type(action['symbol'], sources, device_profile)
    if active_graph_connections(observation, sources=sources)['status'] != 'resolved' or any(
            not isinstance(raw.get('connections'), dict) or raw['connections'].get('status') != 'observed'
            for network in observation['networks'] for raw in network['nodes']):
        raise GXWFormatError('Live input placement requires complete actual old interfaces and nets')
    stock = native_catalog()['input']
    width, height = stock.bbox.right-stock.bbox.left, stock.bbox.bottom-stock.bbox.top
    ports = _ports(stock)
    if (width, height) != (2, 2) or len(ports) != 1 or (ports[0]['x'], ports[0]['y']) != (2, 1):
        raise GXWFormatError('The input template differs from the witnessed native 2x2 placement')
    left, top = action['x'], action['y']
    right, bottom = left+width, top+height
    network = graph['networks'][action['network']]
    for node in network['nodes']:
        if (max(left, node['x']) < min(right, node['x']+node['width'])
                and max(top, node['y']) < min(bottom, node['y']+node['height'])):
            raise GXWFormatError('The live input rectangle overlaps an existing node')
        if any(left <= node['x']+port['x'] <= right and top <= node['y']+port['y'] <= bottom
               for port in node['ports']):
            raise GXWFormatError('The live input rectangle touches an existing native port')
    for wire in network['wires']:
        axis, fixed, low, high = _segment(wire['start'], wire['end'])
        if ((top <= fixed <= bottom and max(left, low) <= min(right, high)) if axis == 'h'
                else (left <= fixed <= right and max(top, low) <= min(bottom, high))):
            raise GXWFormatError('The live input rectangle touches an existing native wire')
    expected = dict(network=action['network'], template='input', symbol=action['symbol'],
                    x=left, y=top, width=width, height=height,
                    ports=[dict(name=ports[0]['name'], x=2, y=1, side='out', data_type=data_type)])
    return dict(kind='active_graph_action', schema_version=1, owner=graph['owner'], owner_kind=graph['owner_kind'],
        network=action['network'], action=deepcopy(action), target=None, expected_data_type=data_type,
        expected_node=expected, device_profile=device_profile,
        observation_state=_action_state(observation), before_state=_action_state(observation, sources=sources),
        before_wires=_wire_inventory(graph))


def active_graph_terminal_target(observation, prepared, *, placeholder=False):
    """Resolve an actual sole new input, keeping all original source objects.

    Placeholder mode is only a native placement checkpoint. It neither invents
    a native record nor claims the requested source symbol has been entered.
    """
    from .callable_sources import type_mask
    if (not isinstance(prepared, dict) or prepared.get('kind') != 'active_graph_action'
            or prepared.get('schema_version') != 1 or prepared.get('action', {}).get('action') != 'add_terminal'):
        raise GXWFormatError('A current prepared live terminal addition is required')
    graph = active_graph_projection(observation)
    if (graph['owner'], graph['owner_kind'], graph['active_network']) != (
            prepared['owner'], prepared['owner_kind'], prepared['network']):
        raise GXWFormatError('The new live terminal belongs to another owner or current network')
    expected = {key: value for key, value in prepared['expected_node'].items() if key != 'ports'}
    if placeholder:
        expected['symbol'] = '?'
    matches = [node for network in graph['networks'] for node in network['nodes']
               if all(node[key] == value for key, value in expected.items())]
    if len(matches) != 1:
        raise GXWFormatError('There is no unique actual new input at the requested rectangle')
    node, = matches
    raw = observation['networks'][node['network']]['nodes'][node['index']]
    stock = native_catalog()['input']
    if (not isinstance(raw.get('connections'), dict) or raw['connections'].get('status') != 'observed'
            or node['ports_status'] != 'resolved' or len(node['ports']) != 1
            or node['ports'][0]['flags'] != stock.ports[0].port_kind_code):
        raise GXWFormatError('The new input has no exact actual stock native port')
    mask = node['ports'][0].get('native_type_mask')
    if mask and mask != type_mask(prepared['expected_data_type']):
        raise GXWFormatError('The new input native type mask conflicts with its source declaration')
    retained = deepcopy(observation)
    retained['networks'][node['network']]['nodes'].pop(node['index'])
    for index, original in enumerate(retained['networks'][node['network']]['nodes']):
        original['index'] = index
    if (_action_state(retained) != prepared['observation_state']
            or _wire_inventory(graph) != prepared['before_wires']):
        raise GXWFormatError('Live input creation changed another node, interface, wire or complete old net')
    return graph_target_anchor(observation, {'network': node['network'], 'id': node['id']})


def prepare_active_graph_action(observation, action, *, sources, device_profile):
    """Prepare one current native graph action without files or native calls.

    Semantic anchors survive native reconstruction. The private geometry-only
    StructuredProgram is used by existing connectivity/type checks and is never
    returned as an editable or serializable saved engineering source.
    """
    from gxworks2.project_profiles import native_project_profile
    from .topology import _terminal_type
    from .visible_graph import (_check_nets, _components, _endpoint, _geometry, _segment,
                                validate_graph_actions)
    graph = active_graph_projection(observation, sources=sources)
    if (not isinstance(sources, ProjectCallableSources)
            or sources.program_name != graph['owner'] + '.Program.pou'):
        raise GXWFormatError('Active graph editing requires current sources for this native owner')
    source_profile = native_project_profile(sources.cpu)
    if source_profile is None or native_project_profile(device_profile or sources.cpu) != source_profile:
        raise GXWFormatError('Active graph device profile differs from current source CPU')
    if not isinstance(action, dict) or 'network' not in action:
        raise GXWFormatError('Active graph action requires an explicit current network')
    network_index = _integer(action['network'], maximum=len(graph['networks'])-1)
    if network_index != graph['active_network']:
        raise GXWFormatError('Only the current active network can be edited')
    network = graph['networks'][network_index]
    if network['junction_count']:
        raise GXWFormatError('Active edits with native FIXPOINT records are not yet verified')
    if any(node['ports_status'] != 'resolved' or not isinstance(raw.get('connections'), dict)
           or raw['connections'].get('status') != 'observed'
           for node, raw in zip(network['nodes'], observation['networks'][network_index]['nodes'])):
        raise GXWFormatError('Active graph editing requires actual source-resolved native ports in this network')
    if action.get('action') == 'add_terminal':
        return _prepare_add_terminal(observation, graph, action, sources=sources,
                                     device_profile=device_profile or sources.cpu)
    normalized = deepcopy(action)
    normalized.pop('network')
    kind = normalized.get('action')
    if kind in ('move_node', 'add_wire') and (graph['owner_kind'] != 'program' or network_index != 0):
        raise GXWFormatError('Coordinate graph editing is verified only for Program network zero')
    target, target_index, endpoints = None, None, {}
    if kind in ('rebind_terminal', 'delete_terminal', 'move_node'):
        target, target_index = _action_node(observation, network_index, normalized.get('node'))
        normalized['node'] = network['nodes'][target_index]['id']
    elif kind == 'delete_wire':
        target, target_index = _action_wire(observation, network_index, normalized.get('wire'))
        normalized['wire'] = network['wires'][target_index]['id']
    elif kind == 'add_wire':
        normalized.setdefault('id', 'live_wire')
        for name in ('from', 'to'):
            value = normalized.get(name)
            if not isinstance(value, dict) or set(value) != {'node', 'port'}:
                raise GXWFormatError('Active wire endpoints require actual node ports')
            anchor, index = _action_node(observation, network_index, value['node'])
            normalized[name]['node'] = network['nodes'][index]['id']
            endpoints[name] = {'node': anchor, 'port': value['port']}
    validate_graph_actions([normalized])
    program = _network_geometry(graph, network)
    context = SimpleNamespace(sources=sources)
    node_ids = {node.offset: row['id'] for node, row in zip(program.nodes, network['nodes'])}
    nodes = {identity: node for node in program.nodes for identity in (node_ids[node.offset],)}
    updated, touched = program, set()
    expected = deepcopy(observation)
    raw_network = expected['networks'][network_index]
    prepared = {'kind': 'active_graph_action', 'schema_version': 1, 'owner': graph['owner'],
        'owner_kind': graph['owner_kind'], 'network': network_index,
        'action': {**normalized, 'network': network_index}, 'target': target,
        'device_profile': device_profile or sources.cpu,
        'observation_state': _action_state(observation),
        'before_state': _action_state(observation, sources=sources)}
    if kind in ('rebind_terminal', 'delete_terminal', 'move_node'):
        node = program.nodes[target_index]
        allowed = ((NodeKind.INPUT, NodeKind.OUTPUT, NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK)
                   if kind == 'move_node' else (NodeKind.INPUT, NodeKind.OUTPUT))
        if node.kind not in allowed:
            raise GXWFormatError(kind + ' requires a supported ' + ('data node' if kind == 'move_node' else 'terminal'))
        connectivity = build_connectivity_graph(program)
        for port in range(len(node.ports)):
            touched.update((p.node_offset, p.port_index) for p in connectivity.net_for_port(node.offset, port).ports)
        if kind == 'rebind_terminal':
            if normalized['symbol'] == node.symbol:
                raise GXWFormatError('rebind_terminal requires a different explicit symbol')
            prepared['expected_data_type'] = _terminal_type(normalized['symbol'], sources, prepared['device_profile'])
            changed = replace(node, symbol=normalized['symbol'])
            raw_network['nodes'][target_index]['name'] = normalized['symbol']
            updated = replace(program, nodes=tuple(changed if n.offset == node.offset else n for n in program.nodes))
        elif kind == 'delete_terminal':
            updated = replace(program, nodes=tuple(n for n in program.nodes if n.offset != node.offset))
            raw_network['nodes'].pop(target_index)
            for index, raw in enumerate(raw_network['nodes']):
                raw['index'] = index
        else:
            x, y = normalized['x'], normalized['y']
            width, height = node.bbox.right-node.bbox.left, node.bbox.bottom-node.bbox.top
            if (x+width > 4096 or y+height > 4096 or (x, y) == (node.bbox.left, node.bbox.top)):
                raise GXWFormatError('move_node requires a different bounded integer position')
            changed = replace(node, bbox=Rect(x, y, x+width, y+height))
            updated = replace(program, nodes=tuple(changed if n.offset == node.offset else n for n in program.nodes))
            raw_network['nodes'][target_index]['bounds'] = [x, y, x+width, y+height]
    elif kind == 'delete_wire':
        wire = program.wires[target_index]
        touched.update((p.node_offset, p.port_index) for p in
                       build_connectivity_graph(program).net_for_wire(wire.offset).ports)
        updated = replace(program, wires=tuple(w for w in program.wires if w.offset != wire.offset))
        if _geometry(updated.wires) == _geometry(program.wires):
            raise GXWFormatError('Wire deletion has no unambiguous conductive geometry effect')
        raw_network['wires'].pop(target_index)
        for index, raw in enumerate(raw_network['wires']):
            raw['index'] = index
    else:
        for name, side in (('from', 'out'), ('to', 'in')):
            resolved = _endpoint(context, program, nodes, {}, normalized[name], side=side,
                                 device_profile=prepared['device_profile'])
            endpoints[name]['point'] = resolved['point']
            touched.update(resolved['ports'])
        points = [endpoints['from']['point'], *normalized['via'], endpoints['to']['point']]
        wires = list(program.wires)
        for number, (start, end) in enumerate(zip(points, points[1:])):
            segment = _segment(start, end)
            if any(_segment([w.start.x, w.start.y], [w.end.x, w.end.y]) == segment for w in wires):
                raise GXWFormatError('Duplicate active wire segment')
            axis, fixed, low, high = segment
            for node in program.nodes:
                box = node.bbox
                crosses = (box.top < fixed < box.bottom and max(low, box.left) < min(high, box.right)
                           if axis == 'h' else box.left < fixed < box.right and max(low, box.top) < min(high, box.bottom))
                if crosses:
                    raise GXWFormatError('Wire path crosses a current node interior')
            wires.append(StructuredWire(len(program.nodes)+len(program.wires)+number, 0,
                                         Point(*start), Point(*end), (), 0, b''))
            raw_network['wires'].append({'index': len(raw_network['wires']),
                                         'start': list(start), 'end': list(end), 'selected': False})
        updated = replace(program, wires=tuple(wires))
        if _geometry(updated.wires) == _geometry(program.wires):
            raise GXWFormatError('Wire addition has no new conductive geometry')
        old_wires = {w.offset for w in program.wires}
        groups = _components(program, node_ids, old_wires)
        selected = {f'{node_ids[offset]}:{port}' for offset, port in touched}
        joined = set().union(*(group for group in groups if group & selected))
        wanted = [group for group in groups if not group & selected] + ([joined] if joined else [])
        actual = _components(updated, node_ids, old_wires)
        if sorted(map(sorted, wanted)) != sorted(map(sorted, actual)):
            raise GXWFormatError('Wire path joins unintended current ports or nets')
        prepared['endpoints'] = endpoints
    _check_nets(context, updated, touched, device_profile=prepared['device_profile'])
    prepared['expected_state'] = _action_state(expected, sources=sources)
    return prepared


def verify_active_graph_action(before, after, prepared, *, sources):
    """Verify precisely the requested native effect; unexpected rewiring is unresolved."""
    if (not isinstance(prepared, dict) or prepared.get('kind') != 'active_graph_action'
            or prepared.get('schema_version') != 1):
        raise GXWFormatError('A prepared active graph action is required')
    diagnostics = []
    try:
        if _action_state(before, sources=sources) != prepared['before_state']:
            diagnostics.append({'code': 'active_graph_precondition_changed', 'message': 'Current graph differs from the prepared action'})
        if prepared['action']['action'] == 'add_terminal':
            _input_declaration_type(prepared['action']['symbol'], sources, prepared['device_profile'])
            anchor = active_graph_terminal_target(after, prepared)
            graph = active_graph_projection(after, sources=sources)
            target = resolve_graph_target(after, anchor)
            node = graph['networks'][target['network']]['nodes'][target['index']]
            retained = deepcopy(after)
            retained['networks'][target['network']]['nodes'].pop(target['index'])
            for index, row in enumerate(retained['networks'][target['network']]['nodes']):
                row['index'] = index
            if _action_state(retained, sources=sources) != prepared['before_state']:
                raise GXWFormatError('The complete original graph or source-bound port nets changed')
            connectivity = active_graph_connections(after, sources=sources)
            nets = [net for net in connectivity['nets'] if any(port['node'] == node['id'] for port in net['ports'])]
            if (connectivity['status'] != 'resolved' or len(nets) != 1
                    or len(nets[0]['ports']) != 1 or nets[0]['wires']):
                raise GXWFormatError('The new input is not its own independent one-port net')
        elif _action_state(after, sources=sources) != prepared['expected_state']:
            diagnostics.append({'code': 'active_graph_effect_unconfirmed',
                'message': 'Native nodes, interfaces or conductive wire geometry differ from the one requested action'})
        if after['active_network'] != prepared['network']:
            diagnostics.append({'code': 'active_graph_network_changed', 'message': 'The requested network is no longer active'})
        if any(key in before and before[key] != after.get(key)
               for key in ('identity', 'data_id', 'document_handle')):
            diagnostics.append({'code': 'active_graph_binding_changed', 'message': 'Native engineering session or active owner binding changed'})
    except (GXWFormatError, KeyError, TypeError) as error:
        diagnostics.append({'code': 'active_graph_readback_invalid', 'message': str(error)})
    return {'status': 'unconfirmed' if diagnostics else 'verified', 'action': prepared['action']['action'],
            'network': prepared['network'], 'owner': prepared['owner'], 'owner_kind': prepared['owner_kind'],
            'diagnostics': diagnostics, 'save_performed': False}

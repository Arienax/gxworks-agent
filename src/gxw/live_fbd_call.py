# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""One source-bound FB call inserted into the current native FBD document.

Current CSV declarations, native ports and observed conductive nets are the
authority. Geometry-only models never become a fabricated saved GXW source.
The native driver owns placement and parameter entry; this module does no I/O.
"""
from __future__ import annotations

from copy import deepcopy

from .active_graph import (_action_state, _port_name_key, active_graph_connections,
                           active_graph_projection, graph_target_anchor)
from .models import GXWFormatError
from .topology import _terminal_type, validate_typed_connections
from .visible_engineering import _fbd_call_command
from .visible_graph import _segment


def _node_key(node):
    return tuple(node[key] for key in ('network', 'template', 'symbol', 'x', 'y', 'width', 'height'))


def _old_geometry(projection):
    """Exact original objects without UI state, process addresses or indexes."""
    networks = []
    for network in projection['networks']:
        nodes = [{key: node[key] for key in ('network', 'template', 'symbol', 'x', 'y', 'width', 'height')}
                 for node in network['nodes']]
        wires = [dict(zip(('start', 'end'), sorted((wire['start'], wire['end'])))) for wire in network['wires']]
        networks.append(dict(index=network['index'], nodes=sorted(nodes, key=_node_key),
                             wires=sorted(wires, key=lambda wire: _segment(wire['start'], wire['end']))))
    return dict(owner=projection['owner'], owner_kind=projection['owner_kind'], networks=networks)


def _protected_context(context, owner, kind):
    from .live_context import live_context_state
    state = live_context_state(context)
    state['graphs'] = [row for row in state['graphs'] if (row['owner'], row['owner_kind']) != (owner, kind)]
    return state


def _current_graphs(baseline, context, *, device_profile, allow_placeholders=False):
    from .live_context import active_callable_sources
    result = {}
    for observation in context['graphs']:
        owner, kind = observation['owner'], observation['owner_kind']
        sources = active_callable_sources(baseline, context['tables'], owner=owner, kind=kind, identity=context['identity'])
        projection = active_graph_projection(observation, sources=sources)
        connections = active_graph_connections(observation, sources=sources)
        if connections['status'] != 'resolved':
            raise GXWFormatError('Live FB insertion requires complete current interfaces and no native FIXPOINT records')
        for network, raw in zip(projection['networks'], observation['networks'], strict=True):
            keys = [_node_key(node) for node in network['nodes']]
            segments = [_segment(wire['start'], wire['end']) for wire in network['wires']]
            if len(set(keys)) != len(keys) or len(set(segments)) != len(segments):
                raise GXWFormatError('Existing live node or wire identity is ambiguous')
            for node, original in zip(network['nodes'], raw['nodes'], strict=True):
                if not isinstance(original.get('connections'), dict) or original['connections'].get('status') != 'observed':
                    raise GXWFormatError('Live FB insertion requires actual current native port evidence')
                if (node['template'] in ('input', 'output')
                        and not (allow_placeholders and node['symbol'] == '?')):
                    _terminal_type(node['symbol'], sources, device_profile)
        result[(owner, kind)] = dict(observation=observation, sources=sources,
            projection=projection, connections=connections, state=_action_state(observation, sources=sources))
    return result


def _graph_states(graphs):
    return [dict(owner=owner, owner_kind=kind, state=graphs[(owner, kind)]['state'])
            for owner, kind in sorted(graphs)]


def _arguments(sources, command):
    instance = sources.label(command['instance'])
    if (instance is None or instance[1].type_code not in (0, 15)
            or instance[1].data_type != command['definition']):
        raise GXWFormatError('Live FB instance must have the exact current declared definition type')
    interface = sources.fixed_interface(command['definition'])
    formals = interface['inputs'] + interface['outputs']
    if set(command['arguments']) != {formal['name'] for formal in formals}:
        raise GXWFormatError('Live FB arguments must bind every current formal exactly')
    identities = [(_port_name_key(formal['name']), side)
                  for side, rows in (('in', interface['inputs']), ('out', interface['outputs'])) for formal in rows]
    if len(set(identities)) != len(identities):
        raise GXWFormatError('Live FB formal names are ambiguous after native quote normalization')
    for side, rows in (('in', interface['inputs']), ('out', interface['outputs'])):
        for formal in rows:
            if type(formal['type_code']) is not int or formal['type_code'] <= 0:
                raise GXWFormatError('Live FB formal has no verifiable native type mask: ' + formal['name'])
            symbol = command['arguments'][formal['name']]
            value = sources.structure_value(symbol)
            label = sources.label(symbol) if value is None else None
            data_type = value['data_type'] if value else label[1].data_type if label else None
            endpoints = {'formal': {'side': side, 'data_type': formal['declared_type']},
                         'argument': {'side': 'out' if side == 'in' else 'in', 'data_type': data_type}}
            validate_typed_connections(endpoints, [{'from': 'argument' if side == 'in' else 'formal',
                                                    'to': 'formal' if side == 'in' else 'argument'}])
    return interface


def prepare_active_fbd_call(baseline, context, command, *, device_profile):
    """Freeze one complete current-source contract before any native placement."""
    from .fb_authoring import _context
    from .live_context import _check_current_structures, _same_native_cpu, current_graph, live_context_state
    _fbd_call_command(command)
    live_context_state(context)
    cpu = _context(baseline, require_fbd=False)[0]['cpu']
    if (not _same_native_cpu(context['identity']['cpu_menu'], cpu)
            or not _same_native_cpu(device_profile or cpu, cpu)):
        raise GXWFormatError('Live FB context, baseline and device profile must have the same CPU')
    _check_current_structures(baseline, context)
    observation = current_graph(baseline, context, command['caller'])
    if observation['active_network'] != 0:
        raise GXWFormatError('Live FB placement is verified only in the current active network zero')
    graphs = _current_graphs(baseline, context, device_profile=device_profile or cpu)
    owner, kind = observation['owner'], observation['owner_kind']
    if kind != 'program':
        raise GXWFormatError('Live FB call placement is verified only in ordinary Program pages')
    graph = graphs[(owner, kind)]
    if any(node['template'].startswith('function_block:') and node['symbol'].casefold() == command['instance'].casefold()
           for network in graph['projection']['networks'] for node in network['nodes']):
        raise GXWFormatError('This current live instance already has a call in the selected caller')
    interface = _arguments(graph['sources'], command)
    return dict(kind='active_fbd_call', owner=owner, owner_kind=kind, network=0,
        plan={**deepcopy(command), 'owner': owner, 'owner_kind': kind, 'network': 0, 'cpu': cpu, 'interface': interface},
        before_graphs=_graph_states(graphs), before_projection=_old_geometry(graph['projection']),
        protected_context=_protected_context(context, owner, kind), device_profile=device_profile or cpu, _baseline=baseline)


def _prepared_command(prepared):
    if not isinstance(prepared, dict) or prepared.get('kind') != 'active_fbd_call' or not isinstance(prepared.get('plan'), dict):
        raise GXWFormatError('Live FB insertion needs its prepared current-source contract')
    command = {key: prepared['plan'].get(key) for key in ('operation', 'caller', 'instance', 'definition', 'arguments')}
    _fbd_call_command(command)
    return command


def _analyze_current_call(current_context, prepared, *, allow_pending):
    """Resolve current native nets while retaining the complete prepared old graph."""
    from .live_context import current_graph
    command = _prepared_command(prepared)
    baseline, owner, kind = prepared['_baseline'], prepared['owner'], prepared['owner_kind']
    if _protected_context(current_context, owner, kind) != prepared['protected_context']:
        raise GXWFormatError('Live FB insertion changed an unrelated declaration, source, configuration or task')
    new_graphs = _current_graphs(baseline, current_context, device_profile=prepared['device_profile'],
                                 allow_placeholders=allow_pending)
    old_states = {(row['owner'], row['owner_kind']): row['state'] for row in prepared['before_graphs']}
    if old_states.keys() != new_graphs.keys() or any(
            old_states[key] != new_graphs[key]['state'] for key in old_states if key != (owner, kind)):
        raise GXWFormatError('Live FB insertion changed another current graph or its native ports')
    new = new_graphs[(owner, kind)]
    current = current_graph(baseline, current_context, command['caller'])
    if (current['owner'], current['owner_kind'], current['active_network']) != (owner, kind, 0):
        raise GXWFormatError('Live FB insertion finished in a different native owner or network')
    if _arguments(new['sources'], command) != prepared['plan']['interface']:
        raise GXWFormatError('Live FB source interface changed after preparation')
    previous, projected = prepared['before_projection']['networks'], new['projection']['networks']
    if len(previous) != len(projected):
        raise GXWFormatError('Live FB insertion changed the network inventory')
    retained_nodes, retained_wires = set(), set()
    for before, after in zip(previous, projected, strict=True):
        for node in before['nodes']:
            matches = [candidate for candidate in after['nodes'] if _node_key(candidate) == _node_key(node)]
            if len(matches) != 1:
                raise GXWFormatError('Live FB insertion changed, removed or duplicated an existing node')
            retained_nodes.add(matches[0]['id'])
        for wire in before['wires']:
            matches = [candidate for candidate in after['wires']
                       if _segment(candidate['start'], candidate['end']) == _segment(wire['start'], wire['end'])]
            if len(matches) != 1:
                raise GXWFormatError('Live FB insertion changed, removed or duplicated an existing wire')
            retained_wires.add(matches[0]['id'])
    # Compare actual old node interfaces and old net partitions again after
    # removing only new objects. Full after nets below separately forbid a new
    # object/segment from joining an old net, including a wire-only component.
    retained = deepcopy(new['observation'])
    for network, raw in zip(projected, retained['networks'], strict=True):
        raw['nodes'] = [original for node, original in zip(network['nodes'], raw['nodes'], strict=True) if node['id'] in retained_nodes]
        raw['wires'] = [original for wire, original in zip(network['wires'], raw['wires'], strict=True) if wire['id'] in retained_wires]
        for key in ('nodes', 'wires'):
            for index, row in enumerate(raw[key]):
                row['index'] = index
    if _action_state(retained, sources=new['sources']) != old_states[(owner, kind)]:
        raise GXWFormatError('Live FB insertion changed complete old interfaces or port-net partitions')
    for net in new['connections']['nets']:
        if (any(port['node'] in retained_nodes for port in net['ports']) or retained_wires.intersection(net['wires'])) and (
                any(port['node'] not in retained_nodes for port in net['ports']) or set(net['wires']) - retained_wires):
            raise GXWFormatError('Live FB insertion connected a new object to an existing complete net')
    created = {node['id']: node for network in projected for node in network['nodes'] if node['id'] not in retained_nodes}
    calls = [node for node in created.values() if node['template'] == 'function_block:' + command['definition']
             and node['symbol'] == command['instance']]
    if len(calls) != 1 or calls[0]['network'] != 0:
        raise GXWFormatError('Exactly one new specified FB call is required in the target network')
    call, = calls
    if call['native_type_status'] != 'matched':
        raise GXWFormatError('The new FB native port masks are not matched to the current source interface')
    terminals, wires, connections, steps = set(), set(), [], []
    for index, port in enumerate(call['ports']):
        if port.get('negated'):
            raise GXWFormatError('Live FB insertion added an unrequested negated formal')
        nets = [net for net in new['connections']['nets'] if any(ref['node'] == call['id'] and ref['port_index'] == index
                                                               for ref in net['ports'])]
        if len(nets) != 1:
            raise GXWFormatError('The new FB formal has no unique actual conductive net')
        net, = nets
        symbol = command['arguments'].get(port['formal_name'])
        expected_kind = 'input' if port['side'] == 'in' else 'output'
        matches = [created[ref['node']] for ref in net['ports'] if ref['node'] in created
                   and created[ref['node']]['template'] == expected_kind]
        if len(matches) != 1 or len(net['ports']) != 2:
            raise GXWFormatError('Live FB formal lacks its unique requested symbol terminal: ' + port['name'])
        terminal, = matches
        if (terminal['id'] in terminals or terminal['network'] != 0 or len(terminal['ports']) != 1
                or terminal['ports'][0].get('flags', 0) & 8):
            raise GXWFormatError('Live FB insertion has shared, foreign or negated symbol terminals')
        status = 'observed' if terminal['symbol'] == symbol else 'pending' if allow_pending and terminal['symbol'] == '?' else None
        if status is None:
            raise GXWFormatError('Live FB formal terminal is neither its requested symbol nor an exact placeholder: ' + port['name'])
        if terminal['ports'][0]['side'] != ('out' if port['side'] == 'in' else 'in'):
            raise GXWFormatError('Live FB formal terminal has the wrong native direction')
        if status == 'observed':
            endpoints = {'formal': port, 'terminal': {'side': terminal['ports'][0]['side'],
                         'data_type': _terminal_type(terminal['symbol'], new['sources'], prepared['device_profile'])}}
            validate_typed_connections(endpoints, [{'from': 'terminal' if port['side'] == 'in' else 'formal',
                                                    'to': 'formal' if port['side'] == 'in' else 'terminal'}])
        terminals.add(terminal['id'])
        wires.update(net['wires'])
        connections.append(dict(formal=port['name'], symbol=symbol, side=port['side'],
            data_type=port['data_type'], node=call['id'], port_index=index,
            terminal=terminal['id'], native_name=port['native_name'], net=net['id']))
        # The expected datatype belongs to the current declared formal and
        # requested argument; an unbound '?' has no claimed declared type.
        steps.append(dict(formal=port['formal_name'], symbol=symbol, side=port['side'],
            data_type=port['data_type'], status=status,
            target=graph_target_anchor(new['observation'], {'network': 0, 'id': terminal['id']})))
    created_wires = {wire['id'] for network in projected for wire in network['wires']} - retained_wires
    if set(created) != terminals | {call['id']} or created_wires != wires:
        raise GXWFormatError('Live FB insertion added unrequested graph objects or wire components')
    return connections, steps


def active_fbd_call_binding_steps(current_context, prepared):
    """Return fresh terminal anchors; only an exact '?' may await binding.

    Every result comes from the actual current two-port net and the complete
    protected old graph. Callers must reread context and recompute after each
    native change. A different existing symbol is an error, never a target to
    overwrite. Placeholder rows describe expected declaration types only.
    """
    return _analyze_current_call(current_context, prepared, allow_pending=True)[1]


def verify_active_fbd_call(before_context, after_context, command, prepared):
    """Independently require all actual symbols and the complete old graph."""
    _fbd_call_command(command)
    if _prepared_command(prepared) != command:
        raise GXWFormatError('Live FB insertion command differs from its prepared current-source contract')
    owner, kind = prepared['owner'], prepared['owner_kind']
    if _protected_context(before_context, owner, kind) != prepared['protected_context']:
        raise GXWFormatError('Live FB insertion before context differs from its prepared current-source contract')
    old_graphs = _current_graphs(prepared['_baseline'], before_context, device_profile=prepared['device_profile'])
    if (_graph_states(old_graphs) != prepared['before_graphs']
            or _old_geometry(old_graphs[(owner, kind)]['projection']) != prepared['before_projection']):
        raise GXWFormatError('Live FB insertion before graph differs from the prepared context')
    connections, _ = _analyze_current_call(after_context, prepared, allow_pending=False)
    return dict(status='matched', caller=command['caller'], owner=owner, owner_kind=kind, network=0,
        instance=command['instance'], definition=command['definition'], connections=connections,
        graph_preservation='complete_old_graph_and_port_nets', unrelated_sources='matched',
        native_type_status='matched', save_performed=False, compilation='not_run')

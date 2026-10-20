"""Derive bounded executable sources from established graph semantics.

Connectivity determines values. Calls need a unique dependency order; opaque,
feedback, stateful relay and ambiguous schedules retain an explicit gap.
"""
from __future__ import annotations

from .models import GXWFormatError, NodeKind


def executable_source(pou):
    source = pou.get('execution_source', {})
    if source.get('status') != 'available' or source.get('language') != 'st':
        raise GXWFormatError(source.get('diagnostic', 'native_graph_execution_schedule_unverified'))
    return source


def project_execution_source(context):
    try:
        from .semantic import build_semantic_model
        semantic = build_semantic_model(context.program)
        if semantic.contacts or semantic.coils:
            return _relay(context, semantic)
        return _calls(context)
    except (GXWFormatError, ValueError, KeyError) as error:
        return dict(status='unverified', language='st', diagnostic=str(error))


def _relay(context, semantic):
    from .connectivity import build_connectivity_graph
    if semantic.functions or semantic.function_blocks or semantic.terminals or semantic.unmodeled_nodes or context.program.unknown_records:
        raise GXWFormatError('mixed or opaque relay execution is unverified')
    if any(c.edge.value != 'none' for c in (*semantic.contacts, *semantic.coils)):
        raise GXWFormatError('relay edge scheduling requires native execution evidence')
    if any(c.role.value != 'normal' for c in semantic.coils):
        raise GXWFormatError('stateful relay operation is outside the combinational execution projection')
    targets = [c.symbol.upper() for c in semantic.coils]
    if len(targets) != len(set(targets)) or set(targets) & {c.symbol.upper() for c in semantic.contacts}:
        raise GXWFormatError('relay feedback or repeated output requires native schedule evidence')
    graph = build_connectivity_graph(context.program)
    drivers = {}
    for contact in semantic.contacts:
        if contact.execution_in is None or contact.execution_out is None:
            raise GXWFormatError('relay execution port is unresolved')
        drivers.setdefault(contact.execution_out.net_index, []).append(contact)
    wires = {w.offset: w for w in context.program.wires}
    def condition(index, seen=()):
        if index in seen:
            raise GXWFormatError('cyclic relay connectivity')
        incoming = drivers.get(index, [])
        if incoming:
            return '(' + ' OR '.join('(' + condition(c.execution_in.net_index, (*seen, index)) + ' AND ' +
                ('NOT ' if c.polarity.value == 'normally_closed' else '') + c.symbol + ')' for c in incoming) + ')'
        net = graph.nets[index]
        # The left rail is an electrical connection, not an execution order.
        if any(wires[w].start.x == 1 or wires[w].end.x == 1 for w in net.wire_offsets):
            return 'TRUE'
        raise GXWFormatError('relay net is not driven by a recognized rail/contact')
    body = '\n'.join(c.symbol + ' := ' + condition(c.execution_in.net_index) + ';' for c in semantic.coils)
    if not body:
        raise GXWFormatError('no executable relay outputs')
    return dict(status='available', language='st', body=body, declarations=[],
                basis='combinational_relay_connectivity_without_feedback')


def _calls(context):
    from .ladder_lowering import fbd_topology_from_context
    topology = fbd_topology_from_context(context)
    if topology['preserve_blocks'] or context.program.unknown_records:
        raise GXWFormatError('opaque graph records prevent bounded execution')
    bodies = []
    all_targets = set()
    for network in topology['networks']:
        nodes = {n['id']: n for n in network['nodes']}
        inputs, outputs = {}, {}
        for wire in network['wires']:
            target, port = wire['to'].split('.', 1)
            if (target, port) in inputs:
                raise GXWFormatError('multiple graph drivers')
            inputs[target, port] = wire['from'].split('.', 1)
        ready_values, pending = {}, {}
        for identity, node in nodes.items():
            template = node['template'].split(':')[0]
            if template == 'input':
                for (target, port), (origin, origin_port) in inputs.items():
                    if origin == identity:
                        ready_values[identity, origin_port] = node['symbol']
            elif template == 'output':
                outputs[identity] = node
            elif template == 'function_block':
                pending[identity] = node
            else:
                raise GXWFormatError('graph node execution is unverified: ' + template)
        while pending:
            ready = [key for key in pending if all(tuple(origin) in ready_values for (target, _), origin in inputs.items() if target == key)]
            if len(ready) != 1:
                raise GXWFormatError('native_graph_execution_schedule_unverified')
            key = ready[0]
            node = pending.pop(key)
            # The existing callable source owns formal names and directions.
            native = next(n for n in context.program.nodes if 'n' + str(n.offset) == key)
            callable_ = context.sources.callable(native)
            arguments = []
            for port in callable_['ports']:
                endpoint = port['name']
                formal = port['formal_name']
                if port['side'] == 'in':
                    if (key, endpoint) not in inputs:
                        raise GXWFormatError('unconnected FB input')
                    expression = ready_values[tuple(inputs[key, endpoint])]
                    if node.get('port_edits', {}).get(endpoint, {}).get('negated'):
                        expression = 'NOT (' + expression + ')'
                    arguments.append(formal + ' := ' + expression)
                else:
                    ready_values[key, endpoint] = node['symbol'] + '.' + formal
            bodies.append(node['symbol'] + '(' + ', '.join(arguments) + ');')
        for key, node in outputs.items():
            origins = [origin for (target, _), origin in inputs.items() if target == key]
            if len(origins) != 1 or tuple(origins[0]) not in ready_values:
                raise GXWFormatError('unconnected or unresolved graph output')
            if node['symbol'].upper() in all_targets:
                raise GXWFormatError('repeated graph output requires native schedule evidence')
            all_targets.add(node['symbol'].upper())
            bodies.append(node['symbol'] + ' := ' + ready_values[tuple(origins[0])] + ';')
    if not bodies:
        raise GXWFormatError('no executable graph actions')
    return dict(status='available', language='st', body='\n'.join(bodies), declarations=[],
                basis='source_connectivity_unique_call_order')

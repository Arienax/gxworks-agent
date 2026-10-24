"""Project GXW source objects into the shared, storage-independent FBD rules."""
from __future__ import annotations

from collections import Counter, defaultdict
import re

from plc.fbd_semantics import (FBDSemanticError, language_rules, type_domain, available_types,
                               call_type_groups, solve_type_domains, check_timer_resolution, storage_keys)
from .connectivity import build_connectivity_graph
from .models import NodeKind, GRAPH_CONTACT_NODE_KINDS, COIL_NODE_KINDS, GXWFormatError


def inspect_graph(program, sources):
    findings, contracts, endpoints, calls, actuals = [], {}, {}, {}, {}
    memberships = {n.offset: index for index, view in enumerate(program.block_views()) for n in view.nodes}
    nodes = {node.offset: node for node in program.nodes}
    rules = {}
    for target in ('FBD.LITERALS', 'FBD.TYPES', 'FBD.OPERATORS', 'FBD.CALL.BINDING', 'FBD.TIMERS'):
        try:
            rules[target] = language_rules(target, sources.cpu)
        except FBDSemanticError as error:
            if target != 'FBD.TIMERS':
                findings.append(dict(error.finding, source_name=program.logical_name, rule=target))
            rules[target] = {}
    supported_types = available_types(sources.cpu, rules['FBD.TYPES'])

    def add(code, message, *, node=None, severity='error', **details):
        row = dict(code=code, severity=severity, message=message, source_name=program.logical_name, **details)
        if node is not None:
            row.update(node_offset=node.offset, symbol=node.symbol, block=memberships[node.offset],
                       block_number=memberships[node.offset] + 1)
        if row.get('ports'):
            enriched = []
            for port in row['ports']:
                owner = nodes.get(port.get('node_offset'))
                enriched.append({**port, **(dict(symbol=owner.symbol, kind=owner.kind.value,
                    type_name=owner.type_name) if owner is not None else {})})
            row['ports'] = enriched
        row.setdefault('rule', 'FBD.CALL.BINDING' if 'call' in code or 'inout' in code else
                       'FBD.LITERALS' if 'literal' in code or 'constant' in code else 'FBD.TYPES')
        if row not in findings:
            findings.append(row)

    def actual(node):
        try:
            value = sources.actual_value(node.symbol, literal_rules=rules['FBD.LITERALS'] or None)
        except FBDSemanticError as error:
            details = dict(error.finding)
            add(details.pop('code'), details.pop('message'), node=node, **details)
            value = dict(text=node.symbol, kind='unresolved', types=None, writable=None, constant=None)
        except ValueError as error:
            from plc.validation import ValidationProfileUnavailableError
            add('fbd_actual_binding', str(error), node=node, expression=node.symbol,
                severity='unverified' if isinstance(error, ValidationProfileUnavailableError) else 'error')
            value = dict(text=node.symbol, kind='unresolved', types=None, writable=None, constant=None)
        actuals[node.offset] = value
        return value

    for node in program.nodes:
        ports = []
        if node.kind in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
            try:
                binding = sources.callable(node)
                ports = binding['ports']
                contracts[node.offset] = [(p['name'], p['data_type'], p['side'], p['class_code']) for p in ports]
                calls[node.offset] = binding
            except GXWFormatError as error:
                add('fbd_call_binding', str(error), node=node)
                # Geometry still tells us direction even if the formal is lost.
                ports = [dict(name=str(i), side='in' if p.local_x == 0 else 'out', data_type=None)
                         for i, p in enumerate(node.ports)]
        elif node.kind in (NodeKind.INPUT, NodeKind.OUTPUT, NodeKind.INOUT):
            value = actual(node)
            ports = [dict(name=str(i), side=('out' if node.kind == NodeKind.INPUT else 'in')
                          if node.kind != NodeKind.INOUT else 'in' if p.local_x == 0 else 'out',
                          data_type=value.get('data_type'), types=value['types'], actual=value)
                     for i, p in enumerate(node.ports)]
        elif node.kind in GRAPH_CONTACT_NODE_KINDS | COIL_NODE_KINDS | {NodeKind.JUMP, NodeKind.RETURN}:
            ports = [dict(name=str(i), side='in' if p.local_x == 0 else 'out', data_type='BOOL', relay=True)
                     for i, p in enumerate(node.ports)]
            if node.kind in GRAPH_CONTACT_NODE_KINDS | COIL_NODE_KINDS:
                value = actual(node)
                if value.get('types') is not None and 'BOOL' not in value['types']:
                    add('fbd_relay_operand_type', 'Relay contact/coil requires a BOOL operand', node=node,
                        expected=['BOOL'], actual=value.get('types'))
                if node.kind in COIL_NODE_KINDS and value.get('writable') is False:
                    add('fbd_output_not_writable', 'Coil target is not writable', node=node)
        for index, port in enumerate(ports):
            key = (node.offset, index)
            if 'types' not in port and port.get('data_type') and not port['data_type'].startswith('ANY'):
                try:
                    port = dict(port, types=[sources.value_type_identity(port['data_type'])])
                except GXWFormatError as error:
                    add('fbd_port_type_coverage', str(error), node=node, severity='unverified', port=port['name'])
                    port = dict(port, types=None)
            endpoints[key] = dict(port, node_offset=node.offset,
                                  types=port.get('types', type_domain(port.get('data_type'), rules['FBD.TYPES'])))
            types = endpoints[key]['types']
            if types is not None and supported_types:
                filtered = [kind for kind in types if kind in supported_types or kind.startswith(('STRUCT:', 'FB:', 'ARRAY'))]
                if not filtered:
                    add('fbd_type_not_supported', 'The declared value type is unavailable for the selected CPU members',
                        node=node, port=port['name'], actual_types=list(types), plc_model=sources.cpu)
                endpoints[key]['types'] = filtered

    relations = []
    for offset, binding in calls.items():
        node = nodes[offset]
        selected = {key: value for key, value in endpoints.items() if key[0] == offset}
        if binding.get('binding') == 'serialized_operator':
            from plc.instruction_constraints import _cpu_members
            operator = re.sub(r'-\d+$', '', node.symbol.upper().lstrip('@'))
            conflict = rules['FBD.OPERATORS'].get('source_conflicts', {}).get(operator)
            if conflict and set(_cpu_members(sources.cpu)).intersection(conflict['models']):
                add('fbd_operator_source_conflict', conflict['reason'], node=node, severity='unverified', rule='FBD.OPERATORS')
                continue
            groups, overrides = call_type_groups(node.symbol, selected, rules['FBD.OPERATORS'])
            for key, value in overrides.items():
                endpoints[key]['types'] = list(set(value) & supported_types) if supported_types else list(value)
            for group in groups:
                relations.append(dict(kind='call', members=group, node_offset=offset,
                                      block=memberships[offset], rule='FBD.OPERATORS'))
        else:
            # An equal generic spelling alone is not a parameter relationship.
            name = node.type_name if node.kind == NodeKind.FUNCTION_BLOCK else node.symbol
            try:
                stream, definition = sources.definition(name, 'FUNCTION_BLOCK' if node.kind == NodeKind.FUNCTION_BLOCK else 'FUNCTION')
            except GXWFormatError:
                continue
            if definition.get('source_kind') != 'project_labels':
                aliases = rules['FBD.OPERATORS'].get('standard_function_aliases', {})
                canonical = str(definition.get('name') or name).upper()
                operator_name = aliases.get(canonical, canonical if canonical in rules['FBD.OPERATORS'].get('operators', {}) else None)
                if stream.casefold() == 'iec.lif' and operator_name:
                    groups, overrides = call_type_groups(operator_name, selected, rules['FBD.OPERATORS'])
                    for key, value in overrides.items():
                        endpoints[key]['types'] = list(set(value) & supported_types) if supported_types else list(value)
                    relations.extend(dict(kind='call', members=group, node_offset=offset,
                                          block=memberships[offset], rule='FBD.OPERATORS') for group in groups)
                if stream.casefold() == 'iec.lif' and canonical in rules['FBD.CALL.BINDING'].get('same_generic_value_type', ()):
                    group = [key for key, port in selected.items() if port.get('data_type') == 'ANY']
                    relations.append(dict(kind='call', members=group, node_offset=offset,
                                          block=memberships[offset], rule='FBD.CALL.BINDING'))
                for formals in rules['FBD.CALL.BINDING'].get('same_type_formals', {}).get(name.upper(), ()):
                    group = [key for key, port in selected.items() if port.get('formal_name', '').casefold() in
                             {formal.casefold() for formal in formals}]
                    if len(group) > 1:
                        relations.append(dict(kind='call', members=group, node_offset=offset,
                                              block=memberships[offset], rule='FBD.CALL.BINDING'))

    graph = build_connectivity_graph(program)
    net_ports, dependencies, bound_inputs = {}, [], set()
    for net in graph.nets:
        keys = [(p.node_offset, p.port_index) for p in net.ports]
        known = [key for key in keys if key in endpoints]
        outputs = [key for key in known if endpoints[key].get('side') == 'out']
        inputs = [key for key in known if endpoints[key].get('side') == 'in']
        if outputs:
            bound_inputs.update(inputs)
        net_ports[net.index] = (outputs, inputs)
        relay = bool(outputs) and all(nodes[key[0]].kind in GRAPH_CONTACT_NODE_KINDS for key in outputs) and all(
            endpoints[key].get('relay') or endpoints[key].get('formal_name') == 'EN' or
            nodes[key[0]].kind in (NodeKind.OUTPUT, NodeKind.INOUT) for key in inputs)
        if len(outputs) > 1 and not relay:
            add('fbd_multiple_drivers', 'Data input has multiple output drivers', net=net.index,
                block=net.block_index, ports=[dict(node_offset=k[0], port=endpoints[k].get('name')) for k in outputs])
        implicit_rail = inputs and all(nodes[key[0]].kind in GRAPH_CONTACT_NODE_KINDS and
                                      nodes[key[0]].port_point(key[1]).x == 1 for key in inputs)
        if len(keys) > 1 and (not inputs or not outputs and not implicit_rail) and not relay:
            add('fbd_net_direction', 'Connection needs both a producer and a consumer endpoint',
                net=net.index, block=net.block_index,
                ports=[dict(node_offset=k[0], port=endpoints[k].get('name')) for k in known])
        if any(key not in endpoints for key in keys):
            add('fbd_net_coverage', 'Net contains a port whose role is not yet resolved', severity='unverified',
                net=net.index, block=net.block_index)
        relations.append(dict(kind='net', members=known, net=net.index, block=net.block_index))
        # A MELSEC instruction can address a multiword device span. That is a
        # source-owned storage view, not an implicit cast of an INT label or an
        # IEC function's typed value. Reuse the current instruction width.
        for call_key in known:
            call_node, formal = nodes[call_key[0]], endpoints[call_key]
            if call_node.kind != NodeKind.FUNCTION or formal.get('formal_name') in ('EN', 'ENO'):
                continue
            try:
                width = sources.call_storage_bits(call_node, formal)
                if width is None:
                    continue
                for terminal_key in known:
                    value = endpoints[terminal_key].get('actual') or {}
                    if value.get('kind') != 'device':
                        continue
                    from plc.validation import parse_device_address, _validate_device_address, device_address_radix
                    prefix, index = parse_device_address(value['text'], sources.cpu)
                    if prefix in ('T', 'C'):
                        add('fbd_device_value_view_coverage', 'Timer/counter contact and current-value width need the selected device contract',
                            node=nodes[terminal_key[0]], severity='unverified', port=formal['name'])
                        endpoints[terminal_key]['types'] = None
                        continue
                    if (value.get('storage') or {}).get('bits') != 16:
                        continue
                    last = index + width // 16 - 1
                    radix = device_address_radix(prefix, sources.cpu)
                    suffix = format(last, 'X') if radix == 16 else format(last, 'o') if radix == 8 else str(last)
                    _validate_device_address(prefix + suffix, '$.fbd.device_span', sources.cpu)
                    endpoints[terminal_key]['types'] = ['INT','WORD'] if width == 16 else ['DINT','DWORD','REAL']
                    endpoints[terminal_key]['storage_view_bits'] = width
            except (ValueError, AttributeError) as error:
                add('fbd_device_span', str(error), node=call_node, formal=formal.get('formal_name'))
        for key in known:
            if endpoints[key].get('data_type') == 'ANY' and rules['FBD.TYPES'].get('any_includes_composites'):
                composite = {t for other in known for t in (endpoints[other].get('types') or ())
                             if t.startswith(('STRUCT:', 'FB:', 'ARRAY'))}
                endpoints[key]['types'] = list(set(endpoints[key].get('types') or ()) | composite)
        for destination in inputs:
            port, node = endpoints[destination], nodes[destination[0]]
            values = [endpoints[key].get('actual') for key in outputs if endpoints[key].get('actual')]
            size = re.fullmatch(r'STRING\s*\[\s*(\d+)\s*\]', str(port.get('data_type') or ''), re.I)
            capacity = (port.get('actual') or {}).get('string_capacity') or (int(size[1]) if size else None)
            if capacity is not None:
                for value in values:
                    if value.get('syntax') == 'string' and isinstance(value.get('value'), str) and len(value['value']) > capacity:
                        add('fbd_string_capacity', 'String value exceeds the destination capacity', node=node,
                            port=port['name'], expression=value['text'], capacity=capacity)
            if node.kind in (NodeKind.OUTPUT, NodeKind.INOUT):
                value = port.get('actual', {})
                if value.get('writable') is False:
                    add('fbd_output_not_writable', 'Output target is not writable', node=node, port=port['name'], expression=node.symbol)
            if port.get('class_code') == 11:
                if values and any(value.get('constant') is False for value in values):
                    add('fbd_constant_required', 'This source formal requires a compile-time constant', node=node,
                        port=port['name'], formal=port.get('formal_name'), role='VAR_INPUT CONSTANT')
            if port.get('class_code') == 5:
                for value in values:
                    if not value.get('constant'):
                        continue
                    try:
                        used, language = sources.inout_output_usage(node, port['formal_name'])
                    except (ValueError, AttributeError):
                        used, language = None, None
                    family = 'BOOL' if port.get('data_type') == 'BOOL' else 'other'
                    condition = rules['FBD.CALL.BINDING'].get('inout_input_constant', {}).get(language, {}).get(family, {})
                    allowed = condition.get('written' if used else 'unused') if used is not None else (
                        True if condition.get('written') is True and condition.get('unused') is True else None)
                    if allowed is False:
                        add('fbd_inout_constant', 'IN_OUT constant violates the current callee output-use contract',
                            node=node, port=port['name'], expression=value['text'], callee_language=language,
                            callee_output_usage=used, rule='FBD.CALL.BINDING')
                    elif allowed is None:
                        add('fbd_inout_constant_coverage', 'IN_OUT constant requires the callee language and output-use condition',
                            node=node, port=port['name'], severity='unverified', expression=value['text'],
                            callee_language=language, callee_output_usage=used)
            if node.kind == NodeKind.FUNCTION_BLOCK and port.get('formal_name', '').upper() == 'PT':
                try:
                    _, definition = sources.definition(node.type_name, 'FUNCTION_BLOCK')
                except GXWFormatError:
                    definition = {}
                if definition and definition.get('source_kind') != 'project_labels':
                    if not rules['FBD.TIMERS'] and node.type_name.upper() in ('TON', 'TON_E', 'TON_10', 'TON_10_E'):
                        add('fbd_timer_resolution_coverage', 'Timer resolution has no rule for the selected CPU members',
                            node=node, port=port['name'], severity='unverified', rule='FBD.TIMERS')
                    for value in values:
                        issue = check_timer_resolution(node.type_name, value, rules['FBD.TIMERS'])
                        if issue:
                            add(issue.pop('code'), issue.pop('message'), node=node, port=port['name'], **issue)
            for origin in outputs:
                if origin[0] in calls and destination[0] in calls:
                    dependencies.append(dict(source=origin[0], target=destination[0],
                        kind='enable' if port.get('formal_name') == 'EN' else 'data', net=net.index))

    for key, port in endpoints.items():
        if key[0] in calls and port.get('side') == 'in' and key not in bound_inputs:
            # Native interface records do not currently encode an optional/default
            # marker. Never invent one or silently treat omission as validated.
            add('fbd_unbound_formal_coverage', 'Unconnected input requires a current optional/default-input contract',
                node=nodes[key[0]], severity='unverified', port=port['name'], formal=port.get('formal_name'))

    composites = {kind for port in endpoints.values() for kind in (port.get('types') or ())
                  if kind.startswith(('STRUCT:', 'FB:', 'ARRAY'))}
    if rules['FBD.TYPES'].get('any_includes_composites'):
        for port in endpoints.values():
            if port.get('data_type') == 'ANY':
                port['types'] = list(set(port.get('types') or ()) | composites)
    domains, conflicts = solve_type_domains(endpoints, relations)
    for conflict in conflicts:
        ports = [dict(node_offset=key[0], port=endpoints[key].get('name'), formal=endpoints[key].get('formal_name'),
                      data_type=endpoints[key].get('data_type'), actual_types=endpoints[key].get('types'),
                      declaration_source=(endpoints[key].get('actual') or {}).get('source_table'),
                      storage=(endpoints[key].get('actual') or {}).get('storage')) for key in conflict['members']]
        add('fbd_net_type_conflict' if conflict['kind'] == 'net' else 'fbd_call_type_conflict',
            'Connected values violate the source type/shape contract', ports=ports,
            **{key: value for key, value in conflict.items() if key not in ('kind', 'members')})
    for key, domain in domains.items():
        if domain is None:
            add('fbd_terminal_type_unverified', 'Port value type remains unresolved', node=nodes[key[0]],
                severity='unverified', port=endpoints[key].get('name'))
        elif len(domain) > 1 and str(endpoints[key].get('data_type') or '').startswith('ANY'):
            add('fbd_generic_type_coverage', 'Generic remains a constrained domain; no concrete overload was selected',
                node=nodes[key[0]], severity='unverified', port=endpoints[key].get('name'), possible_types=sorted(domain))

    # Shared storage and repeated instances impose order even without a wire.
    ordered = sorted(calls, key=lambda key: (memberships[key], nodes[key].bbox.top, nodes[key].bbox.left, key))
    order_index = {key: index for index, key in enumerate(ordered)}
    access = {key: dict(read=set(), write=set()) for key in calls}
    for outputs, inputs in net_ports.values():
        for output in outputs:
            for input_ in inputs:
                if output[0] in actuals and input_[0] in calls:
                    access[input_[0]]['read'].update(storage_keys(actuals[output[0]], plc_model=sources.cpu,
                        view_bits=endpoints[output].get('storage_view_bits')))
                if input_[0] in actuals and output[0] in calls:
                    access[output[0]]['write'].update(storage_keys(actuals[input_[0]], plc_model=sources.cpu,
                        view_bits=endpoints[input_].get('storage_view_bits')))
    for index, earlier in enumerate(ordered):
        for later in ordered[index + 1:]:
            overlaps = (access[earlier]['write'] & (access[later]['read'] | access[later]['write'])
                        | access[earlier]['read'] & access[later]['write'])
            if overlaps:
                dependencies.append(dict(source=earlier, target=later, kind='shared_storage', symbols=sorted(overlaps)))
            if nodes[earlier].kind == nodes[later].kind == NodeKind.FUNCTION_BLOCK and nodes[earlier].symbol.casefold() == nodes[later].symbol.casefold():
                dependencies.append(dict(source=earlier, target=later, kind='instance_state', instance=nodes[earlier].symbol))
    for edge in dependencies:
        if edge['kind'] in ('data', 'enable') and order_index.get(edge['source'], -1) >= order_index.get(edge['target'], 0):
            add('fbd_feedback_order_coverage', 'Feedback/backward dependency needs a current state and execution-order contract',
                severity='unverified', node=nodes[edge['target']], dependency=edge)
        if edge['kind'] == 'data':
            producer = edge['source']
            en = [key for key, port in endpoints.items() if key[0] == producer and port.get('formal_name') == 'EN']
            controlled = any(other['kind'] == 'enable' and other['source'] == producer and other['target'] == edge['target']
                             for other in dependencies)
            if en and not controlled:
                add('fbd_result_enable_coverage', 'Data use is not guarded by the producing call ENO; retained/undefined result behavior needs its call contract',
                    severity='unverified', node=nodes[edge['target']], dependency=edge)
    instances = []
    for offset in ordered:
        node = nodes[offset]
        if node.kind != NodeKind.FUNCTION_BLOCK:
            continue
        try:
            context = sources.callee_context(node)
        except GXWFormatError:
            continue
        conditions = dict(instance=node.symbol, type=node.type_name, source_name=context['source_name'],
                          callee_language=context['language'], options=context['options'],
                          en_disabled_is_in_false=False, en_disabled_is_reset=False)
        conditions['missing_conditions'] = [key for key, value in context['options'].items() if value is None]
        if context['language'] is None:
            conditions['missing_conditions'].append('callee_language')
        conditions['disabled_state'] = 'unverified' if conditions['missing_conditions'] else 'source_contract_required'
        instances.append(conditions)
    return dict(contracts=contracts, findings=findings,
                execution=dict(order=ordered, dependencies=dependencies, instances=instances,
                               status='static_dependencies_only'),
                resolved_types={f'{key[0]}:{key[1]}': sorted(value) if value is not None else None for key, value in domains.items()})


def introduced_findings(before, after):
    """Compare semantic identities without treating rewritten byte offsets as new errors."""
    def key(row):
        port_identity = tuple(sorted((str(port.get('symbol')), str(port.get('port')), str(port.get('kind')),
                                      str(port.get('data_type')), str(port.get('actual_types')),
                                      str(port.get('declaration_source'))) for port in row.get('ports', ())))
        # A new preceding network shifts block numbers without changing this
        # error. Keep multiplicity, symbols and contracts, not layout positions.
        return (row.get('code'), row.get('source_name'), row.get('symbol'), row.get('port'),
                row.get('expression'), row.get('message'), port_identity)
    old = Counter(key(row) for row in before if row.get('severity') == 'error')
    result = []
    for row in after:
        if row.get('severity') != 'error':
            continue
        identity = key(row)
        if old[identity]:
            old[identity] -= 1
        else:
            result.append(dict(row, introduced=True))
    return result


def require_graph_checks(before, after, *, save_policy):
    introduced = introduced_findings(before, after)
    rejected = [row for row in introduced if save_policy == 'checked' or row['code'] in
                {'fbd_literal_syntax', 'fbd_literal_range', 'fbd_constant_type'}]
    if rejected:
        error = GXWFormatError('FBD source checks rejected the candidate: ' + '; '.join(row['message'] for row in rejected))
        error.findings = rejected
        raise error
    return introduced

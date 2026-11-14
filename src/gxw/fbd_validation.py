# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Project GXW source objects into the shared, storage-independent FBD rules."""
from __future__ import annotations

from collections import Counter, defaultdict
import re

from plc.fbd_semantics import (FBDSemanticError, language_rules, type_domain, available_types,
                               call_type_groups, solve_type_domains, check_timer_resolution, storage_keys)
from .connectivity import build_connectivity_graph
from .models import NodeKind, GRAPH_CONTACT_NODE_KINDS, COIL_NODE_KINDS, GXWFormatError


def inout_constant_allowed(rules, callee_language, data_type, used):
    """AG197: a structured caller may bind a constant only while the callee
    does not use the IN_OUT formal as an output target.  ``None`` means the
    current callee cannot decide the output-use condition."""
    family = 'BOOL' if data_type == 'BOOL' else 'other'
    condition = rules['FBD.CALL.BINDING'].get('inout_input_constant', {}).get(callee_language, {}).get(family, {})
    if used is not None:
        return condition.get('written' if used else 'unused')
    return True if condition.get('written') is True and condition.get('unused') is True else None


def _operation_value_iec_type(value_type):
    """Map only explicit source value identities to IEC domains.

    The graphics library may expose an ANY32 pin while an instruction's own
    source relation narrows the operation value. Unknown or unit-bearing
    identities stay unresolved; device names are not used to infer a type.
    """
    if not isinstance(value_type, dict) or value_type.get('unit') not in (None, ''):
        return None
    kind, bits = value_type.get('kind'), value_type.get('bits')
    if kind == 'int' and bits == 32 and type(value_type.get('signed')) is bool:
        return 'DINT' if value_type['signed'] else 'DWORD'
    if kind == 'real' and value_type.get('signed', False) is False:
        if bits == 32:
            return 'REAL'
        if bits == 64:
            return 'LREAL'
    return None


def _source_label_device(value, pin, word_count):
    """Use only a whole scalar label's explicit word-device binding."""
    if value.get('kind') != 'label' or value.get('member_path') or value.get('shape'):
        return None
    device = (value.get('storage') or {}).get('device')
    if not device:
        return None
    declared = str(value.get('data_type') or '').upper()
    if declared in {'WORD', 'INT'} and word_count == 1:
        return device
    capacity = value.get('string_capacity')
    required = pin.get('declared_capacity')
    size = re.fullmatch(r'STRING\s*\[\s*(\d+)\s*\]', declared)
    if (str(pin.get('type')).upper() == 'STRING' and size and type(capacity) is int
            and int(size[1]) == capacity
            and type(required) is int and capacity >= required
            and capacity * 8 >= word_count * 16):
        return device
    return None


def inspect_graph(program, sources):
    findings, contracts, endpoints, calls, actuals = [], {}, {}, {}, {}
    source_device_views = []
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

    def source_block_pin(binding, formal_name):
        if not binding or binding.get('binding') != 'source_instruction_block':
            return None
        wanted = str(formal_name or '').casefold()
        return next((pin for pin in binding.get('source_contract', {}).get('pins', ())
                     if str(pin.get('name', '')).casefold() == wanted), None)

    for node in program.nodes:
        ports = []
        if node.kind in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK):
            try:
                binding = sources.callable(node)
                ports = binding['ports']
                contracts[node.offset] = [(p['name'], p['data_type'], p['side'], p['class_code']) for p in ports]
                calls[node.offset] = binding
                if binding.get('binding') == 'source_instruction_block':
                    contract = binding['source_contract']
                    if contract.get('conditions_status') == 'unresolved':
                        add('fbd_instruction_block_condition_unverified',
                            'The source applicability conditions for this FBD instruction block are unresolved.',
                            node=node, severity='unverified', rule='FBD.CALL.BINDING',
                            source_fact_ids=contract.get('fact_ids', []),
                            conditions=contract.get('conditions', []))
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
            source_pin = source_block_pin(calls.get(node.offset),
                                          port.get('formal_name') or port.get('name'))
            if source_pin and 'operation_value_type' in source_pin:
                source_type = _operation_value_iec_type(source_pin.get('operation_value_type'))
                if source_type is None:
                    add('fbd_operation_value_type_unverified',
                        'The source operation value type cannot be mapped to a concrete FBD value domain.',
                        node=node, severity='unverified', formal=source_pin.get('name'),
                        operation_value_type=source_pin.get('operation_value_type'))
                elif not endpoints[key].get('types') or source_type not in endpoints[key]['types']:
                    add('fbd_operation_value_type_conflict',
                        'The source operation value type conflicts with the graphic pin domain.',
                        node=node, formal=source_pin.get('name'), operation_value_type=source_pin.get('operation_value_type'),
                        pin_types=endpoints[key].get('types'))
                    endpoints[key]['types'] = []
                else:
                    endpoints[key]['types'] = [source_type]
                    endpoints[key]['source_operation_value_type'] = source_type

    relations = []
    for offset, binding in calls.items():
        node = nodes[offset]
        selected = {key: value for key, value in endpoints.items() if key[0] == offset}
        if binding.get('binding') == 'serialized_operator':
            from plc.instruction_constraints import _cpu_members
            operator = re.sub(r'-\d+$', '', node.symbol.upper().lstrip('@'))
            conflict = rules['FBD.OPERATORS'].get('source_conflicts', {}).get(operator)
            if conflict and set(_cpu_members(sources.cpu)).intersection(conflict['models']):
                add('fbd_operator_source_conflict', 'Operator typing has an unresolved source difference',
                    node=node, severity='unverified', rule='FBD.OPERATORS',
                    source_difference=conflict['reason'])
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
        # A MELSEC instruction or source FBD block can address a multiword
        # device span. Source block layouts are checked from their own pin
        # facts; an independent-element layout must not be reinterpreted as a
        # wider scalar value.
        for call_key in known:
            call_node, formal = nodes[call_key[0]], endpoints[call_key]
            if call_node.kind != NodeKind.FUNCTION or formal.get('formal_name') in ('EN', 'ENO'):
                continue
            binding = calls.get(call_node.offset)
            source_pin = source_block_pin(binding, formal.get('formal_name'))
            source_operation_type = (_operation_value_iec_type(source_pin.get('operation_value_type'))
                                     if source_pin and 'operation_value_type' in source_pin else None)
            layout = source_pin.get('word_device_layout') if source_pin else None
            independent_layout = isinstance(layout, dict) and (
                layout.get('single_scalar') is False or layout.get('kind') == 'independent_elements')
            source_word_count = layout.get('word_count') if isinstance(layout, dict) else None
            source_unit_bits = layout.get('device_unit_bits') if isinstance(layout, dict) else None
            try:
                if isinstance(layout, dict):
                    read_layout = source_pin.get('read_layout')
                    string_layout = source_pin.get('string_layout') or {}
                    packed_string = (source_pin.get('type') == 'STRING'
                        and layout.get('element_bits') == 8
                        and type(layout.get('element_count')) is int
                        and layout['element_count'] > 0
                        and (layout['element_count'] + 1) // 2 == source_word_count
                        and string_layout.get('encoding') == 'ASCII'
                        and string_layout.get('data_character_count') == layout['element_count']
                        and string_layout.get('data_word_count') == source_word_count)
                    valid_layout = (type(source_word_count) is int and source_word_count > 0
                                    and source_unit_bits == 16
                                    and (layout.get('element_bits', 16) == 16 or packed_string))
                    if isinstance(read_layout, dict):
                        element_count = read_layout.get('element_count')
                        stride_words = read_layout.get('stride_words')
                        fields = read_layout.get('field_order')
                        referents = source_pin.get('referent_fields')
                        referent_roles = ([row.get('role') for row in referents]
                                          if isinstance(referents, list)
                                          and all(isinstance(row, dict) for row in referents) else None)
                        referent_offsets = ([row.get('offset') for row in referents]
                                            if isinstance(referents, list)
                                            and all(isinstance(row, dict) for row in referents) else None)
                        valid_layout = (valid_layout and type(element_count) is int and element_count > 0
                                        and type(stride_words) is int and stride_words > 0
                                        and element_count * stride_words == source_word_count
                                        and isinstance(fields, list) and len(fields) == element_count
                                        and referent_roles == fields
                                        and referent_offsets == [index * stride_words
                                                                 for index in range(element_count)])
                    if not valid_layout:
                        add('fbd_source_device_layout_unverified',
                            'The source device layout is incomplete or inconsistent.', node=call_node,
                            severity='unverified', formal=formal.get('formal_name'), source_pin=source_pin)
                        continue
                    tail = layout.get('conditional_tail_word_offset')
                    if tail is not None:
                        if not packed_string or type(tail) is not int or tail != source_word_count:
                            add('fbd_source_device_layout_unverified',
                                'The conditional string tail does not have a verified contiguous offset.',
                                node=call_node, severity='unverified', formal=formal.get('formal_name'))
                            continue
                        # Reserve the possible tail without asserting that this
                        # call writes it in every selector state.
                        source_word_count += 1
                    width = source_word_count * source_unit_bits
                else:
                    if source_operation_type in {'DINT', 'DWORD', 'REAL', 'LREAL'}:
                        width = {'DINT': 32, 'DWORD': 32, 'REAL': 32, 'LREAL': 64}[source_operation_type]
                    else:
                        width = sources.call_storage_bits(call_node, formal)
                if width is None:
                    continue
                for terminal_key in known:
                    if terminal_key == call_key:
                        continue
                    value = endpoints[terminal_key].get('actual') or {}
                    label_device = (_source_label_device(value, source_pin, source_word_count)
                                    if isinstance(layout, dict) else None)
                    if value.get('kind') != 'device' and not label_device:
                        if isinstance(layout, dict):
                            add('fbd_source_device_layout_unverified',
                                'The source device layout needs a directly bound word-device reference.',
                                node=call_node, severity='unverified', formal=formal.get('formal_name'),
                                expression=value.get('text'))
                        continue
                    from plc.validation import (parse_device_address, _validate_device_address, _validate_writable_device,
                                                device_address_radix, is_bit_device)
                    device = label_device or value['text']
                    prefix, index = parse_device_address(device, sources.cpu)
                    if label_device and prefix in ('T', 'C'):
                        add('fbd_device_value_view_coverage',
                            'Timer/counter label storage needs the selected device contract.',
                            node=nodes[terminal_key[0]], severity='unverified', port=formal['name'])
                        continue
                    if isinstance(layout, dict):
                        storage_bits = 16 if label_device else (value.get('storage') or {}).get('bits')
                        if storage_bits != source_unit_bits or is_bit_device(device, sources.cpu):
                            add('fbd_source_device_layout_mismatch',
                                'The actual device does not have the source-required word width.',
                                node=call_node, formal=formal.get('formal_name'), expression=value['text'],
                                expected_device_unit_bits=source_unit_bits, actual_storage_bits=storage_bits)
                            continue
                        radix = device_address_radix(prefix, sources.cpu)
                        addresses = []
                        for word_offset in range(source_word_count):
                            current = index + word_offset
                            suffix = format(current, 'X') if radix == 16 else format(current, 'o') if radix == 8 else str(current)
                            address = prefix + suffix
                            _validate_device_address(address, '$.fbd.source_device_layout', sources.cpu,
                                                     expected_prefixes={prefix})
                            if source_pin.get('direction') in {'output', 'in_out'}:
                                _validate_writable_device(address, '$.fbd.source_device_layout', sources.cpu)
                            addresses.append(address)
                        source_domain = type_domain(source_pin.get('type'), rules['FBD.TYPES'])
                        actual_domain = set(value.get('types') or ())
                        if not source_domain or not actual_domain:
                            add('fbd_source_device_type_coverage',
                                'The source generic or actual device type domain is unresolved.',
                                node=call_node, severity='unverified', formal=formal.get('formal_name'),
                                expression=value['text'], source_types=sorted(source_domain or ()),
                                actual_types=sorted(actual_domain))
                            continue
                        if not actual_domain.issubset(source_domain):
                            add('fbd_source_device_type_conflict',
                                'The actual device type domain is not covered by the source generic pin.',
                                node=call_node, formal=formal.get('formal_name'), expression=value['text'],
                                source_types=sorted(source_domain), actual_types=sorted(actual_domain))
                            continue
                        formal['source_device_layout_verified'] = True
                        formal['source_generic_domain'] = sorted(source_domain)
                        source_device_views.append({
                            'node_offset': call_node.offset,
                            'formal': formal.get('formal_name'),
                            'start_device': addresses[0],
                            'end_device': addresses[-1],
                            'word_count': source_word_count,
                            'device_unit_bits': source_unit_bits,
                            'source_types': sorted(source_domain),
                            'actual_types': sorted(actual_domain),
                        })
                        # Three independent date words are not one 48-bit scalar.
                        if independent_layout or label_device or packed_string:
                            continue
                        width = source_unit_bits
                    if prefix in ('T', 'C'):
                        add('fbd_device_value_view_coverage', 'Timer/counter contact and current-value width need the selected device contract',
                            node=nodes[terminal_key[0]], severity='unverified', port=formal['name'])
                        endpoints[terminal_key]['types'] = None
                        continue
                    if (value.get('storage') or {}).get('bits') != 16:
                        if source_operation_type in {'DINT', 'DWORD', 'REAL', 'LREAL'}:
                            add('fbd_operation_value_type_device_mismatch',
                                f'The source operation value requires a {width}-bit word-device view.',
                                node=call_node, formal=formal.get('formal_name'), expression=value['text'],
                                operation_value_type=source_pin.get('operation_value_type'),
                                actual_storage_bits=(value.get('storage') or {}).get('bits'))
                        continue
                    last = index + width // 16 - 1
                    radix = device_address_radix(prefix, sources.cpu)
                    suffix = format(last, 'X') if radix == 16 else format(last, 'o') if radix == 8 else str(last)
                    _validate_device_address(prefix + suffix, '$.fbd.device_span', sources.cpu)
                    endpoints[terminal_key]['types'] = (
                        [source_operation_type] if source_operation_type in {'DINT', 'DWORD', 'REAL', 'LREAL'}
                        else ['INT','WORD'] if width == 16 else ['DINT','DWORD','REAL'])
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
                    allowed = inout_constant_allowed(rules, language, port.get('data_type'), used)
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
                    if not values and node.type_name.upper() in rules['FBD.TIMERS'].get('unavailable_timer_forms', []):
                        add('fbd_timer_form_unavailable', 'Timer form is excluded by the source CPU table',
                            node=node, port=port['name'], rule='FBD.TIMERS')
                    for value in values:
                        issue = check_timer_resolution(node.type_name, value, rules['FBD.TIMERS'])
                        if issue:
                            add(issue.pop('code'), issue.pop('message'), node=node, port=port['name'], **issue)
            for origin in outputs:
                if origin[0] in calls and destination[0] in calls:
                    dependencies.append(dict(source=origin[0], target=destination[0],
                        kind='enable' if port.get('formal_name') == 'EN' else 'data', net=net.index))

    for key, port in endpoints.items():
        node = nodes[key[0]]
        if key[0] in calls and port.get('side') == 'in' and key not in bound_inputs:
            # Native interface records do not currently encode an optional/default
            # marker. Never invent one or silently treat omission as validated.
            add('fbd_unbound_formal_coverage', 'Unconnected input requires a current optional/default-input contract',
                node=nodes[key[0]], severity='unverified', port=port['name'], formal=port.get('formal_name'),
                rule='FBD.CALL.BINDING', omission_behavior='unknown',
                **({'declared_initial_value_raw': port['declared_initial_value_raw']}
                   if 'declared_initial_value_raw' in port else {}))
        elif (
            node.kind in (COIL_NODE_KINDS | {NodeKind.OUTPUT, NodeKind.JUMP, NodeKind.RETURN, NodeKind.INOUT})
            and port.get('side') == 'in'
            and key not in bound_inputs
        ):
            add(
                'fbd_unconnected_sink',
                f"{node.kind.value} '{node.symbol}' input pin is unconnected (native compile error 0x500c1205)",
                node=node,
                port=port['name'],
            )
        elif (
            node.kind in GRAPH_CONTACT_NODE_KINDS
            and port.get('side') == 'in'
            and key not in bound_inputs
            and node.port_point(key[1]).x != 1
        ):
            add(
                'fbd_unpowered_contact_input',
                (
                    f"{node.kind.value} '{node.symbol}' IN port has no left power rail or upstream BOOL driver "
                    "(structured FBD parameter networks do not inject a left rail; drive contacts from "
                    "input('TRUE') or use input/output terminals to avoid native 0x500c2010/0x500c2020)"
                ),
                node=node,
                severity='unverified',
                port=port['name'],
            )

    pool_ranges = getattr(sources, 'system_variable_ranges', None)
    if pool_ranges:
        from .fb_authoring import explicit_device_pool_overlap
        for offset, value in actuals.items():
            if value.get('kind') != 'device':
                continue
            view_bits = max(
                (
                    ep.get('storage_view_bits') or 0
                    for (node_offset, _), ep in endpoints.items()
                    if node_offset == offset
                ),
                default=0,
            ) or None
            overlap = explicit_device_pool_overlap(
                value['text'],
                cpu=sources.cpu,
                system_variable_ranges=pool_ranges,
                data_type=value.get('data_type') or 'BOOL',
                view_bits=view_bits,
            )
            if overlap is not None:
                add('fbd_system_pool_overlap', overlap['message'], node=nodes[offset], expression=value['text'])

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
            if (endpoints[key].get('source_device_layout_verified') is True
                    and set(domain).issubset(set(endpoints[key].get('source_generic_domain') or ()))):
                # A complete actual domain covered by the source-generic pin is
                # sufficient here; the signature has no overload-selection rule.
                continue
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
                source_device_views=source_device_views,
                resolved_types={f'{key[0]}:{key[1]}': sorted(value) if value is not None else None for key, value in domains.items()})


def introduced_findings(before, after):
    """Compare semantic identities without treating rewritten byte offsets as new errors."""
    def key(row):
        port_identity = tuple(sorted((str(port.get('symbol')), str(port.get('port')), str(port.get('kind')),
                                      str(port.get('data_type')), str(port.get('actual_types')),
                                      str(port.get('declaration_source'))) for port in row.get('ports', ())))
        # A new preceding network shifts block numbers without changing this
        # error. Keep multiplicity, symbols and contracts, not layout positions.
        return (row.get('code'), row.get('source_name'), row.get('stream'), row.get('name'),
                row.get('symbol'), row.get('port'), row.get('expression'), row.get('message'), port_identity)
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

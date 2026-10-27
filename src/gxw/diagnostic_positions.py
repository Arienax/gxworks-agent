# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Correlate compile positions with the selected current source."""
from __future__ import annotations
from gxw.diagnostic_scope import (
    PUBLIC_FBD_COMPILE_POSITION_CPUS,
    PUBLIC_LIBRARY_ST_COMPILE_POSITION_CPUS,
    _id,
)
from gxw.diagnostic_bindings import (
    _debug_st_interval,
    bind_native_instance_interval,
)


def _native_network_members(records, network, block_count):
    """Select existing source records by the original one-based network ordinal.

    Original reorder and concrete-menu public FBD controls establish that empty
    source blocks retain their ordinal. Never infer membership from coordinates
    or omit an unassigned record from a multi-block source.
    """
    if (type(block_count) is not int or block_count < 1 or type(network) is not int
            or not 1 <= network <= block_count or not isinstance(records, list)):
        return None
    selected = []
    for record in records:
        if not isinstance(record, dict):
            return None
        block = record.get('block', 0 if block_count == 1 else None)
        if type(block) is not int or not 0 <= block < block_count:
            return None
        if block == network - 1:
            selected.append(record)
    return selected


def correlate_native_compile_position(report, *, cpu, pou, program_kind, text=None, model=None, connectivity=None,
                                      source_body_id=None, logical_name=None, library=None):
    """Project observed structured compilation positions, without code.

    The caller first binds all original source getter reads around this Build.
    A compile report's POU scope does not identify an expanded FB instance.
    Formal-name and graph associations retain their distinct provenance; an
    IN_OUT formal name alone cannot choose its input or output endpoint.
    """
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    public = report.get('report_interface') == 'public-compiler'
    if public:
        if (report.get('record_size') != 100 or _id(source_body_id) is None
                or _id(report.get('source_object_id')) != _id(source_body_id)):
            return {**unresolved, 'reason': 'public compile report object identity differs from the current original source body'}
    public_fbd = (public and program_kind == 208 and isinstance(cpu, str)
                  and cpu in PUBLIC_FBD_COMPILE_POSITION_CPUS)
    library_st = (public and program_kind == 193 and isinstance(library, str) and bool(library)
                  and cpu in PUBLIC_LIBRARY_ST_COMPILE_POSITION_CPUS)
    if (not (cpu == 'Q03UDV' or public_fbd or library_st) or (library and not library_st)
            or not isinstance(pou, str) or not pou or report.get('kind') not in (2, 3)
            or report.get('instance_kind') != 6 or (not public and report.get('library', {}).get('text') != '')
            or report.get('name', {}).get('text') != pou or report.get('instance', {}).get('text') != pou
            or program_kind not in (193, 208) or report.get('program_kind') != program_kind):
        return {**unresolved, 'reason': 'outside observed current structured compile source scope'}
    source = {'pou': pou, 'program_kind': program_kind}
    if library_st:
        source['library'] = library
    result = {**unresolved, 'status': 'source-resolved', 'source': source,
              'instance_resolution': 'not_available_in_compile_report',
              'limits': ['original structured compile source position; no checked or executable code required',
                         'compile POU scope does not identify one expanded FB instance',
                         'public diagnostic conversion and overall check remain separate']}
    if public:
        result['source_identity_binding'] = 'original public report matches current native source body'
    if program_kind == 193:
        line = report.get('top')
        lines = text.splitlines() if isinstance(text, str) else []
        if (type(line) is not int or not 0 <= line < len(lines)
                or report.get('step') != (0 if library_st else line)
                or report.get('network') != -1 or any(report.get(key) != -1 for key in ('left', 'right', 'bottom'))):
            return {**unresolved, 'reason': 'native compile ST line is outside the current observed source span'}
        source.update(zero_based_line=line, text=lines[line])
        return result
    expected_program = logical_name if logical_name is not None else pou + '.Program.pou'
    if (not isinstance(expected_program, str) or not expected_program.startswith(pou + '.')
            or not expected_program.endswith('.pou') or not isinstance(model, dict)
            or model.get('schema_version') != 2 or model.get('cpu') != cpu
            or model.get('program') != expected_program):
        return {**unresolved, 'reason': 'current source-bound FBD model is unavailable'}
    blocks = model.get('blocks', [{}])
    network = report.get('network')
    if not isinstance(blocks, list) or not blocks:
        return {**unresolved, 'reason': 'current FBD source blocks are unavailable'}
    nodes = _native_network_members(model.get('nodes', []), network, len(blocks))
    wires = _native_network_members(model.get('wires', []), network, len(blocks))
    if nodes is None or wires is None:
        return {**unresolved, 'reason': 'native compile network differs from current source membership'}
    source.update(network=network, block_index=network-1)
    coordinates = [report.get(key) for key in ('left', 'top', 'right', 'bottom')]
    graph = {'status': 'unresolved', 'public_check_promoted': False}
    result['graph_projection'] = graph
    if coordinates == [-1, -1, -1, -1]:
        graph.update(status='network-only', reason='native compile report has no node span')
        return result
    if (any(type(value) is not int for value in coordinates)
            or not 0 <= coordinates[0] < coordinates[2] or not 0 <= coordinates[1] < coordinates[3]):
        graph['reason'] = 'native compile node span is incomplete or invalid'
        return result
    if model.get('unknown_record_count') != 0:
        graph['reason'] = 'opaque graph records prevent complete node correlation'
        return result
    def bbox(node):
        return [node['x'], node['y'], node['x']+node['width'], node['y']+node['height']]
    objects = [node for node in nodes if bbox(node) == coordinates]
    if len(objects) != 1:
        graph.update(reason='current compile source object is missing or ambiguous',
                     object_candidates=sorted(node['id'] for node in objects))
        return result
    node = objects[0]
    graph.update(status='node-correlated', object={'object_id': node['id'], 'source_offset': node['source_offset'],
                 'symbol': node['symbol'], 'template': node['template'], 'bbox': coordinates, 'block_index': network-1})
    def port_record(owner, port):
        return {'object_id': owner['id'], 'instance': owner['symbol'], 'formal': port['formal_name'],
                'port_name': port['name'], 'side': port['side'], 'class_code': port['class_code'],
                'data_type': port['data_type']}
    arguments = report.get('arguments', [])
    if (report.get('kind') == 2 and report.get('code') == 0x500c2025
            and node['template'].startswith('function_block:') and len(arguments) == 1):
        formal = arguments[0].get('text')
        ports = [port_record(node, port) for port in node.get('ports', []) if port.get('formal_name') == formal]
        graph['port_association'] = {'status': 'uniquely_correlated' if len(ports) == 1 else 'ambiguous' if ports else 'missing',
            'basis': 'native compile formal name; direction is absent', 'candidates': ports}
    elif (report.get('kind') == 2 and report.get('code') == 0x500c2017 and node['template'] == 'output'
            and not arguments and connectivity is not None and connectivity.logical_name == model['program']):
        association = {'status': 'unresolved', 'basis': 'current source connectivity; not a native port-direction field',
                       'candidates': []}
        graph['port_association'] = association
        try:
            net = connectivity.net_for_port(node['source_offset'], 0)
        except KeyError:
            return result
        if net.block_index != network-1 or len(net.ports) != 2:
            return result
        if net.ports[0].point != net.ports[1].point and not net.wire_offsets:
            return result
        keys = [(port.node_offset, port.port_index) for port in net.ports]
        if len(set(keys)) != 2 or (node['source_offset'], 0) not in keys:
            return result
        if any(sum(w.get('source_offset') == offset for w in wires) != 1 for offset in net.wire_offsets):
            return result
        for endpoint in net.ports:
            if endpoint.node_offset == node['source_offset']:
                owner, index = node, 0
            else:
                owners = [value for value in nodes if value['source_offset'] == endpoint.node_offset]
                if len(owners) != 1:
                    return result
                owner, index = owners[0], endpoint.port_index
            ports = owner.get('ports', [])
            if type(index) is not int or not 0 <= index < len(ports):
                return result
            port = ports[index]
            if [owner['x']+port['x'], owner['y']+port['y']] != [endpoint.point.x, endpoint.point.y]:
                return result
            if owner is not node:
                if (not owner['template'].startswith('function_block:') or port.get('side') != 'out'
                        or port.get('class_code') not in (4, 5)):
                    return result
                association['candidates'].append(port_record(owner, port))
        if len(association['candidates']) == 1:
            association['status'] = 'uniquely_correlated'
    return result


def correlate_st_diagnostic(query, link_rows, references, source_texts, caller_nodes, *, debug=None,
                            debug_encoding=None, caller_pou=None, native_ranges=None,
                            caller_block_count=1, caller_context=None):
    """Correlate a controlled native ST line with one compiled FB instance.

    This is saved evidence processing, not a replacement public check. Exact
    task/instance names and an original code interval are required. Follow
    explicit native FB call references through ST callers to a current FBD
    node; do not infer parents by splitting instance names. Optional same-
    compile debug data can resolve an inlined FB without a link-map row.
    When original instance range responses are supplied, require their complete
    agreement and do not replace a failed or conflicting API result with caches.
    Direct library ST calls additionally require the current FBD context to
    bind the graph's interface to the diagnostic's actual library namespace.
    """
    location = query.get('location') or {}
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    library = location.get('library')
    if (query.get('hresult') != 0 or query.get('code') != 0 or location.get('program_kind') != 193
            or not isinstance(library, str)):
        return {**unresolved, 'reason': 'no successful native ST location'}
    if library and (caller_context is None or native_ranges is None):
        return {**unresolved, 'reason': 'library caller requires current source context and original instance ranges'}
    pou, line = location.get('pou'), location.get('start_step')
    lines = source_texts.get((library, pou) if library else pou, '').splitlines()
    if type(line) is not int or not 0 <= line < len(lines):
        return {**unresolved, 'reason': 'native source line outside selected source'}
    native = query.get('query', {})
    step = native.get('start_step')
    line_references = [row for row in references if row.get('program_kind') == 193 and row.get('source') == pou
               and row.get('library') == library
               and row.get('top') == line and row.get('attribute') in ((1, 2) if native_ranges is not None else (2,))
               and row.get('resource') == native.get('resource')
               and row.get('task') and row.get('instance')]
    ranges = [row for row in link_rows if type(step) is int and row['step_count'] > 0
              and row['step_start'] <= step < row['step_start'] + row['step_count']]
    interval = ranges[0] if len(ranges) == 1 else None
    if native_ranges is not None:
        if type(step) is not int or step < 0:
            return {**unresolved, 'reason': 'original diagnostic code step is unavailable'}
        resolved = bind_native_instance_interval(query, line_references, native_ranges)
        if resolved['interval'] is None:
            return {**unresolved, **{key:value for key,value in resolved.items() if key != 'interval'}}
        interval = resolved['interval']
    matches = [row for row in line_references if interval is not None
               and row['task'] + '.' + row['instance'] == interval['name']]
    if native_ranges is not None and interval is not None:
        selected = interval['native_range_evidence']['original_reference']
        # GetPCodeRange is queried once per task/instance/source position, not
        # once per operand on the physical line. Keep its original representative.
        matches = [selected] if selected in matches else []
    if native_ranges is None and type(step) is int and len(matches) != 1:
        recovered = _debug_st_interval(query, debug, line_references, debug_encoding)
        if recovered is not None:
            interval = recovered
            matches = [row for row in line_references if row['task'] + '.' + row['instance'] == interval['name']]
    if interval is None:
        return {**unresolved, 'reason': 'code range missing or ambiguous', 'range_candidates': len(ranges)}
    if len(matches) != 1:
        return {**unresolved, 'reason': 'source reference missing or ambiguous', 'reference_candidates': len(matches),
                'source_instance_candidates': sorted({row['instance'] for row in line_references}),
                'compiled_interval_name': interval['name']}
    reference = matches[0]
    called_pou, called_instance = pou, reference['instance']
    call_chain, visited = [], set()
    while True:
        identity = (called_pou, called_instance)
        if identity in visited or len(call_chain) >= 64:
            return {**unresolved, 'reason': 'native caller chain cycles or exceeds the bound'}
        visited.add(identity)
        calls = [row for row in references if row.get('program_kind') in (193, 208)
                 and row.get('class_code') == 1 and row.get('type') == called_pou
                 and row.get('library') == '' and row.get('attribute') == 1
                 and (not library or row.get('program_kind') == 208 and row.get('data_type') == 15)
                 and row.get('resource') == native.get('resource') and row.get('task') == reference['task']
                 and row.get('instance') and row.get('name') and row.get('source')
                 and row['instance'] + '.' + row['name'] == called_instance]
        if len(calls) != 1:
            return {**unresolved, 'reason': 'native caller missing or ambiguous',
                    'caller_candidates': len(calls), 'called_pou': called_pou,
                    'called_instance': called_instance}
        call = calls[0]
        entry = {'called_pou': called_pou, 'called_instance': called_instance,
                 'native_reference': call}
        call_chain.append(entry)
        if call['program_kind'] == 208:
            if len(call_chain) > 1 and (not isinstance(caller_pou, str) or not caller_pou):
                return {**unresolved, 'reason': 'selected FBD source identity is missing'}
            if caller_pou is not None and call['source'] != caller_pou:
                return {**unresolved, 'reason': 'native FBD caller differs from the selected source'}
            break
        parent_lines = source_texts.get(call['source'], '').splitlines()
        parent_line = call.get('top')
        if (type(parent_line) is not int or not 0 <= parent_line < len(parent_lines)
                or call.get('network') != -1):
            return {**unresolved, 'reason': 'native ST caller line outside the selected source'}
        entry['source'] = {'pou': call['source'], 'program_kind': 193,
                           'zero_based_line': parent_line, 'text': parent_lines[parent_line]}
        called_pou, called_instance = call['source'], call['instance']
    network_nodes = _native_network_members(caller_nodes, call.get('network'), caller_block_count)
    if network_nodes is None:
        return {**unresolved, 'reason': 'native caller network or current block membership is unavailable'}
    objects = [node for node in network_nodes if node.get('symbol') == call['name']
               and node.get('template') == 'function_block:' + called_pou
               and [node['x'], node['y'], node['x'] + node['width'], node['y'] + node['height']]
               == [call[key] for key in ('left', 'top', 'right', 'bottom')]]
    if len(objects) != 1:
        return {**unresolved, 'reason': 'current caller object missing or ambiguous', 'object_candidates': len(objects)}
    interface_source = None
    if library:
        from .models import GXWFormatError, NodeKind
        current_nodes = [node for node in caller_context.program.nodes
                         if node.offset == objects[0]['source_offset']
                         and node.kind == NodeKind.FUNCTION_BLOCK
                         and node.symbol == call['name'] and node.type_name == pou
                         and [node.bbox.left, node.bbox.top, node.bbox.right, node.bbox.bottom]
                         == [call[key] for key in ('left', 'top', 'right', 'bottom')]]
        sources = caller_context.sources
        try:
            local = sources.declarations.get(sources.local_table)
        except GXWFormatError:
            return {**unresolved, 'reason': 'current FBD caller declaration owner is missing or ambiguous'}
        if (len(current_nodes) != 1 or local is None or local.owner_name != call['source']
                or caller_context.program.logical_name != sources.program_name):
            return {**unresolved, 'reason': 'original FBD call does not match the current caller source context'}
        try:
            bound = sources.callable(current_nodes[0])
        except GXWFormatError:
            return {**unresolved, 'reason': 'current caller interface is missing or ambiguous'}
        owner = sources.user_library_owners.get(bound['source_stream'], {})
        if owner.get('library') != library or owner.get('name') != pou:
            return {**unresolved, 'reason': 'current caller interface belongs to another source namespace'}
        interface_source = bound['source_stream']
    result = {'status': 'uniquely_correlated', 'public_check_promoted': False,
            'resource': native['resource'], 'diagnostic_step': step,
            'original_kind': native.get('original_kind'), 'original_code': native.get('original_code'),
            'instance': reference['instance'], 'task': reference['task'],
            'source': {'pou': pou, 'program_kind': 193, 'zero_based_line': line, 'text': lines[line]},
            'compiled_interval': {key: interval[key] for key in
                ('name', 'step_start', 'step_count', 'token_offset', 'token_length')},
            'native_reference': reference,
            'caller': {'pou': call['source'], 'program_kind': 208, 'network': call['network'],
                       'bbox': [call[key] for key in ('left', 'top', 'right', 'bottom')],
                       'object_id': objects[0]['id'], 'source_offset': objects[0]['source_offset']},
            'limits': ['controlled source line, not an expression span',
                       'public diagnostic conversion and overall check remain separate']}
    if len(call_chain) > 1:
        result['call_chain'] = call_chain
        result['caller']['instance'] = called_instance
        result['limits'][0] = 'controlled ST-to-FBD call chain, not an expression span'
    if library:
        result['source']['library'] = library
        result['caller']['interface_source'] = interface_source
        result['limits'].append('direct library ST call; graph port and nested library callers remain unresolved')
    if 'debug_evidence' in interval:
        result['debug_evidence'] = interval['debug_evidence']
    if 'native_range_evidence' in interval:
        result['native_range_evidence'] = interval['native_range_evidence']
    return result

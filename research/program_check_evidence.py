"""Classify saved native check observations without running GX Works2.

Compilation, actual resource reads, backend completion, public diagnostics and
save/reopen are independent facts. Publication alone is not read evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _id(value):
    return tuple(value) if isinstance(value, list) and len(value) == 12 else None


def project_native_source_diagnostic(query, diagnostic, *, project_id, native_version):
    """Join an original resource diagnostic to a native project-local source.

    The 1.635.0.1 workspace lookup returns the Program body (type 32), whose
    parent is the named POU (type 26). Its language belongs to the POU. Require
    the independent parent, name, type and language reads; a stored success
    flag alone is insufficient. This does not repair the public adapter or
    establish that the checker read current code.
    """
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}

    def native_id(value):
        result = _id(value)
        return result if result is not None and any(result) and all(
            type(word) is int and 0 <= word <= 0xffffffff for word in result) else None

    def accepted(value):
        return isinstance(value, dict) and value.get('hresult') == 0 and value.get('code') == 0

    def text(value):
        return value.get('text') if isinstance(value, dict) else None

    project = native_id(project_id)
    if native_version != '1.635.0.1' or project is None:
        return {**unresolved, 'reason': 'unobserved native version or missing project identity'}
    if (diagnostic.get('kind') not in (2, 3) or type(diagnostic.get('code')) is not int
            or not accepted(query) or text(diagnostic.get('library')) != ''
            or text(diagnostic.get('name')) != query.get('resource')
            or type(diagnostic.get('step')) is not int or diagnostic['step'] < 0
            or diagnostic['step'] != query.get('code_step')):
        return {**unresolved, 'reason': 'original diagnostic and native resource query do not match'}
    location, source = query.get('location') or {}, query.get('source_object') or {}
    pou, kind = location.get('pou'), location.get('program_kind')
    if (location.get('library') != '' or not isinstance(pou, str) or not pou
            or kind not in (1, 193, 208) or source.get('status') != 'verified-source-object'
            or native_id(source.get('parent')) != project
            or source.get('lookup_type') != 32 or source.get('name') != pou):
        return {**unresolved, 'reason': 'no verified project-local source identity'}
    lookup, owner_lookup = source.get('lookup') or {}, source.get('owner_lookup') or {}
    body, owner = source.get('body') or {}, source.get('owner') or {}
    parent, language = source.get('body_parent') or {}, source.get('language') or {}
    body_id, owner_id = native_id(lookup.get('id')), native_id(owner_lookup.get('id'))
    if (not accepted(lookup) or not accepted(owner_lookup) or owner_lookup.get('lookup_type') != 26
            or body_id is None or owner_id is None or body_id == owner_id
            or native_id(body.get('id')) != body_id or native_id(owner.get('id')) != owner_id
            or not accepted(parent) or native_id(parent.get('id')) != owner_id):
        return {**unresolved, 'reason': 'source body and POU parent identities disagree'}
    if (not accepted(body.get('read_type')) or body['read_type'].get('data_type') != 32
            or not accepted(body.get('read_name')) or not body['read_name'].get('name')
            or not accepted(owner.get('read_type')) or owner['read_type'].get('data_type') != 26
            or not accepted(owner.get('read_name')) or owner['read_name'].get('name') != pou
            or not accepted(language) or native_id(language.get('object_id')) != owner_id
            or language.get('value') != kind or language.get('expected') != kind):
        return {**unresolved, 'reason': 'native source type, name or POU language does not match'}
    return {'status': 'source-resolved', 'public_check_promoted': False,
            'original_diagnostic': diagnostic,
            'resource': query['resource'], 'diagnostic_step': query['code_step'],
            'source': {'project_id': list(project), 'pou': pou,
                       'body_id': list(body_id), 'pou_id': list(owner_id), 'program_kind': kind},
            'native_position': {key: location.get(key) for key in
                                ('network', 'start_step', 'step_count', 'element_id',
                                 'action_transition_present')},
            'node_port_resolution': 'not-established',
            'provenance': 'original native location and workspace identity calls'}


def compare_public_source_diagnostic(public, query, diagnostic, *, project_id, native_version):
    """Audit a converted diagnostic against an independent native source query.

    A resource can share a POU name and let the 1.635.0.1 adapter's name lookup
    succeed for the wrong body. Preserve both results. Body agreement alone
    does not turn compiled-code steps into source positions or prove a whole
    check completed.
    """
    native = project_native_source_diagnostic(query, diagnostic,
        project_id=project_id, native_version=native_version)
    result = {'status': 'unresolved', 'public_check_promoted': False,
              'original_public_diagnostic': public, 'native_source_projection': native}
    if native['status'] != 'source-resolved':
        return {**result, 'reason': 'independent native source identity is unresolved'}
    if (type(public.get('kind')) is not int or type(public.get('code')) is not int
            or type(public.get('step')) is not int
            or any(public.get(key) != diagnostic.get(key) for key in ('kind', 'code', 'step'))
            or public.get('name') != query.get('resource')):
        return {**result, 'reason': 'public and original diagnostics do not match'}
    public_id = _id(public.get('object_id'))
    if public_id is None or not any(public_id) or not all(
            type(word) is int and 0 <= word <= 0xffffffff for word in public_id):
        return {**result, 'reason': 'public source body identity is missing'}
    matches = list(public_id) == native['source']['body_id']
    return {**result, 'status': 'source-consistent' if matches else 'source-conflict',
            'public_body_matches_native_source': matches,
            'public_position': {key: public.get(key) for key in
                                ('program_kind', 'step', 'network', 'left', 'top', 'right', 'bottom')},
            'source_position_basis': 'independent native source query',
            'limits': ['body identity agreement does not establish public source coordinates',
                       'current resource reads and check completion are separate facts']}


def _debug_st_interval(query, debug, references, encoding):
    """Require one saved ST element matching the independent native location.

    Macro FBs can be inside the parent FBD interval. A native ST location
    distinguishes that child from the parent; stored order alone is not used.
    """
    if debug is None or not encoding:
        return None
    native, location = query['query'], query['location']
    step, line = native['start_step'], location['start_step']
    candidates = []
    for index, element in enumerate(debug.elements):
        try:
            resource = element.resource_bytes.decode(encoding)
            names = [name.decode(encoding) for name in element.names]
        except (UnicodeError, LookupError):
            return None
        if (element.kind_code != 193 or resource != native['resource']
                or names[0] != location['library']
                or names[2] != location['pou']
                or not element.linked_step_start <= step <= element.linked_step_end):
            continue
        table = debug.offset_tables[element.offset_table_index]
        relative = step - element.linked_step_start
        rows = [row for row in table.rows if table.kind_code == 193 and row[0] >= 0
                and row[3] == -1 and row[4] & 7 == 7
                and row[1] <= relative <= row[2] and element.fields[0] + row[0] == line]
        if len(rows) != 1:
            continue
        instance, member = names[3:5]
        if member:
            instance += '.' + member
        matches = [row for row in references if row['instance'] == instance]
        if len(matches) != 1:
            continue
        reference = matches[0]
        candidates.append({'name': reference['task'] + '.' + instance,
            'step_start': element.linked_step_start,
            'step_count': element.linked_step_end - element.linked_step_start + 1,
            'token_offset': None, 'token_length': None,
            'debug_evidence': {'element_index': index, 'element_offset': element.offset,
                'raw_element_hex': element.raw.hex(), 'offset_table_offset': table.offset,
                'raw_offset_row': list(rows[0]), 'native_location_matched': True,
                'encoding': encoding,
                'origin': 'saved same-compile debug element; not primary link map'}})
    return candidates[0] if len(candidates) == 1 else None


def correlate_st_diagnostic(query, link_rows, references, source_texts, caller_nodes, *, debug=None,
                            debug_encoding=None, caller_pou=None):
    """Correlate a controlled native ST line with one compiled FB instance.

    This is saved evidence processing, not a replacement public check. Exact
    task/instance names and an original code interval are required. Follow
    explicit native FB call references through ST callers to a current FBD
    node; do not infer parents by splitting instance names. Optional same-
    compile debug data can resolve an inlined FB without a link-map row.
    """
    location = query.get('location') or {}
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    if (query.get('hresult') != 0 or query.get('code') != 0 or location.get('program_kind') != 193
            or location.get('library') != ''):
        return {**unresolved, 'reason': 'no successful native ST location'}
    pou, line = location.get('pou'), location.get('start_step')
    lines = source_texts.get(pou, '').splitlines()
    if type(line) is not int or not 0 <= line < len(lines):
        return {**unresolved, 'reason': 'native source line outside selected source'}
    native = query.get('query', {})
    step = native.get('start_step')
    line_references = [row for row in references if row.get('program_kind') == 193 and row.get('source') == pou
               and row.get('library') == ''
               and row.get('top') == line and row.get('attribute') == 2
               and row.get('resource') == native.get('resource')
               and row.get('task') and row.get('instance')]
    ranges = [row for row in link_rows if type(step) is int and row['step_count'] > 0
              and row['step_start'] <= step < row['step_start'] + row['step_count']]
    interval = ranges[0] if len(ranges) == 1 else None
    matches = [row for row in line_references if interval is not None
               and row['task'] + '.' + row['instance'] == interval['name']]
    if type(step) is int and len(matches) != 1:
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
    objects = [node for node in caller_nodes if node.get('symbol') == call['name']
               and node.get('template') == 'function_block:' + called_pou
               and [node['x'], node['y'], node['x'] + node['width'], node['y'] + node['height']]
               == [call[key] for key in ('left', 'top', 'right', 'bottom')]]
    if len(objects) != 1:
        return {**unresolved, 'reason': 'current caller object missing or ambiguous', 'object_candidates': len(objects)}
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
    if 'debug_evidence' in interval:
        result['debug_evidence'] = interval['debug_evidence']
    return result


def correlate_fbd_bool_output(query, diagnostic, records, references, model, *, selected_pou, connectivity=None):
    """Correlate an observed FX3U direct FB BOOL-port LD/OUT diagnostic.

    records must already agree with native lexical reads and native prefix
    GetStepSize. The caller binds all inputs to one current owned compilation.
    Optional connectivity is the existing Core graph read from that same POU.
    This is research evidence processing, not a general FBD diagnostic mapper.
    """
    from native_gxw_tokens import canonical_record
    missing = {'status': 'unresolved', 'public_check_promoted': False}
    def reject(reason):
        return {**missing, 'reason': reason}
    def device(value):
        return canonical_record({'kind':'instruction','op':'LD','args':[value]})['args'][0]
    location = query.get('location') or {}
    resource, step = query.get('resource'), query.get('code_step')
    if (model.get('schema_version') != 2 or model.get('cpu') != 'FX3U/FX3UC'
            or model.get('unknown_record_count') != 0):
        return reject('outside observed FX3U source/graph scope')
    if (query.get('hresult') != 0 or query.get('code') != 0
            or location.get('program_kind') != 208 or location.get('library') != ''
            or not selected_pou or location.get('pou') != selected_pou):
        return reject('no selected native FBD source location')
    arguments = diagnostic.get('arguments', [])
    if (diagnostic.get('kind') != 2 or diagnostic.get('code') != 0x050c9300
            or diagnostic.get('name', {}).get('text') != resource
            or diagnostic.get('step') != step or len(arguments) != 1):
        return reject('outside observed duplicate-output diagnostic form')
    at = [r for r in records if r.get('native_step') == step]
    if len(at) != 1 or at[0].get('op') != 'OUT' or len(at[0].get('args', [])) != 1:
        return reject('diagnostic does not select one native OUT')
    output = at[0]
    if not arguments[0].get('text') or device(output['args'][0]) != device(arguments[0]['text']):
        return reject('native diagnostic operand differs from OUT')
    def direct_read(write):
        previous = [r for r in records if r.get('record_index') == write['record_index'] - 1]
        if (len(previous) != 1 or previous[0].get('op') != 'LD' or len(previous[0].get('args', [])) != 1
                or previous[0].get('source_end') != write.get('source_offset')
                or previous[0].get('source_end') is None):
            return None
        return previous[0]
    read = direct_read(output)
    if read is None:
        return reject('not an adjacent native LD/OUT pair')
    scoped = [r for r in references if r.get('resource') == resource and r.get('source') == selected_pou
        and r.get('program_kind') == 208 and r.get('library') == ''
        and r.get('network') == location.get('network')]
    producers = [r for r in scoped if r.get('class_code') == 4 and r.get('data_type') == 1
        and r.get('attribute') == 1 and r.get('address') == read['args'][0]]
    if len(producers) != 1:
        return reject('FB BOOL output address is missing or ambiguous')
    producer = producers[0]
    def bbox(row):
        return [row[key] for key in ('left','top','right','bottom')]
    def node_bbox(node):
        return [node['x'],node['y'],node['x']+node['width'],node['y']+node['height']]
    sources = []
    for node in model.get('nodes', []):
        if not node['template'].startswith('function_block:') or node_bbox(node) != bbox(producer):
            continue
        for port in node.get('ports', []):
            if (port.get('side') == 'out' and port.get('class_code') == 4 and port.get('data_type') == 'BOOL'
                    and not port.get('negated')
                    and producer['name'] == node['symbol'] + '.' + port.get('formal_name', port['name'])):
                sources.append((node,port))
    if len(sources) != 1:
        return reject('current graph FB output interface is missing or ambiguous')
    source, port = sources[0]
    calls = [r for r in scoped if r.get('name') == source['symbol'] and r.get('class_code') == 1
        and r.get('type') == source['template'].split(':',1)[1] and bbox(r) == node_bbox(source)
        and r.get('task') == producer.get('task') and r.get('instance') == producer.get('instance')]
    if len(calls) != 1:
        return reject('current native FB instance is missing or ambiguous')
    point = [source['x']+port['x'],source['y']+port['y']]
    def connection(node, endpoint):
        target_point = [node['x']+endpoint['x'],node['y']+endpoint['y']]
        if connectivity is None:
            return {'kind':'port_overlap','wire_offsets':[]} if target_point == point else None
        if connectivity.logical_name != model.get('program'):
            return None
        source_key = (source['source_offset'], source['ports'].index(port))
        target_key = (node['source_offset'], 0)
        try:
            net = connectivity.net_for_port(*source_key)
            target_net = connectivity.net_for_port(*target_key)
        except KeyError:
            return None
        # Observed overlap, straight, bent and T-junction forms have one scalar
        # FB driver and only output terminals; expression nodes remain excluded.
        keys = {(p.node_offset,p.port_index) for p in net.ports}
        if (net.index != target_net.index or len(keys) != len(net.ports)
                or not {source_key,target_key} <= keys):
            return None
        graph_points = {(p.node_offset,p.port_index):[p.point.x,p.point.y] for p in net.ports}
        if graph_points[source_key] != point or graph_points[target_key] != target_point:
            return None
        for offset,index in keys - {source_key,target_key}:
            terminals = [n for n in model.get('nodes', []) if n.get('source_offset') == offset]
            if len(terminals) != 1 or index != 0:
                return None
            terminal = terminals[0]
            ports = terminal.get('ports', [])
            if terminal['template'] != 'output' or len(ports) != 1 or ports[0].get('negated'):
                return None
            if [terminal['x']+ports[0]['x'],terminal['y']+ports[0]['y']] != graph_points[offset,index]:
                return None
            # Direct device terminals report data_type=0; use their original
            # native LD/OUT operands rather than inventing a BOOL type field.
            writes = [r for r in scoped if r.get('attribute') == 2 and r.get('address')
                and r.get('name') == terminal['symbol'] and bbox(r) == node_bbox(terminal)
                and r.get('task') == producer.get('task') and r.get('instance') == producer.get('instance')]
            if len(writes) != 1:
                return None
            native_writes = [r for r in records if r.get('op') == 'OUT' and len(r.get('args', [])) == 1
                and device(r['args'][0]) == device(writes[0]['address'])]
            if not any((previous := direct_read(r)) is not None and previous['args'] == read['args']
                       for r in native_writes):
                return None
        if any(sum(w.get('source_offset') == offset for w in model.get('wires', [])) != 1
                for offset in net.wire_offsets):
            return None
        if point != target_point and not net.wire_offsets:
            return None
        return {'kind':'port_overlap' if point == target_point else 'wire_network',
                'net_index':net.index,'output_terminal_count':len(keys)-1,'wire_offsets':list(net.wire_offsets),
                'wire_scope':'all conductors in the current Core net, not a unique path'}
    destinations = []
    for reference in scoped:
        if (reference.get('attribute') != 2 or not reference.get('address')
                or device(reference['address']) != device(output['args'][0])
                or reference.get('task') != producer.get('task')
                or reference.get('instance') != producer.get('instance')):
            continue
        for node in model.get('nodes', []):
            if (node['template'] != 'output' or node['symbol'] != reference['name']
                    or node_bbox(node) != bbox(reference)):
                continue
            ports = node.get('ports', [])
            if len(ports) == 1 and not ports[0].get('negated'):
                link = connection(node, ports[0])
                if link is not None:
                    destinations.append((node,reference,link))
    if len(destinations) != 1:
        result = reject('directly connected current output object is missing or ambiguous')
        result.update(original_kind=diagnostic['kind'],original_code=diagnostic['code'],resource=resource,
            source_pou=selected_pou,diagnostic_step=step,source_status='uniquely_correlated',
            source={'object_id':source['id'],'instance':source['symbol'],'formal':port['formal_name'],
                'port_name':port['name'],'side':'out','point':point},
            output_status='ambiguous' if destinations else 'missing',
            output_candidates=sorted({node['id'] for node,_,_ in destinations}))
        return result
    destination, reference, link = destinations[0]
    return {'status':'uniquely_correlated','public_check_promoted':False,
        'original_kind':diagnostic['kind'],'original_code':diagnostic['code'],
        'resource':resource,'source_pou':selected_pou,'diagnostic_step':step,
        'native_pair':{'read':read,'write':output},'native_member':producer,
        'source':{'object_id':source['id'],'instance':source['symbol'],'formal':port['formal_name'],
            'port_name':port['name'],'side':'out','point':point},
        'output':{'object_id':destination['id'],'source_offset':destination['source_offset'],
            'symbol':destination['symbol'],'bbox':node_bbox(destination),'native_reference':reference},
        'connection':link,
        'limits':['adjacent native LD/OUT and direct scalar FB BOOL/output connection only',
            'current native facts and source context required; no execution claim']}


def analyze_check(events, observations, generated, *, selection=None, reopened=None, source_audits=None):
    """generated maps native resource IDs to three exact GetPCode buffers."""
    trace = [row.get('payload', row) for row in observations if row.get('type') != 'error']
    observation_errors = [row for row in observations if row.get('type') == 'error']
    observation_errors += [row for row in trace if row.get('event') == 'observation-error']
    planned, collection = [], None
    collecting = False
    for row in events:
        if row.get('operation') == 'Workspace.GetProgramCheckCollection':
            collecting = True
        elif collecting and row.get('operation') == 'InventoryCount':
            collection = _id(row.get('id'))
        elif collecting and row.get('operation') == 'NativeObject' and _id(row.get('parent')) == collection:
            planned.append(_id(row['id']))
        elif row.get('operation') == 'ProgramCheckTarget':
            collecting = False
    started = [_id(row['id']) for row in events if row.get('operation') == 'ProgramCheckTarget']
    planned = list(dict.fromkeys(planned or started))
    reads, queries = [], []
    for row in trace:
        if row.get('event') != 'workspace-pcode-read' or row.get('phase') != 'check':
            continue
        buffers = row.get('buffers', [])
        query = (row.get('hresult') == row.get('code') == 0 and row.get('requested_sizes') == [0, 0, 0]
                 and any(b.get('size') and b.get('raw_hex') is None for b in buffers))
        if query:
            queries.append(row['serial'])
            continue
        actual = []
        for buffer in buffers:
            raw = buffer.get('raw_hex')
            # A successful zero-length channel has no bytes even with NULL.
            if raw is None and buffer.get('size') == 0 and row.get('hresult') == row.get('code') == 0:
                raw = ''
            actual.append(bytes.fromhex(raw) if raw is not None else None)
        expected = generated.get(_id(row.get('target')))
        if (row.get('hresult') != 0 or row.get('code') != 0 or len(actual) != 3 or any(b is None for b in actual)
                or any(len(data) != buffer.get('size') for data, buffer in zip(actual, buffers) if data is not None)):
            status = 'unreadable'
        elif expected is None:
            status = 'unbound_resource'
        else:
            status = 'current' if tuple(actual) == tuple(expected) else 'stale'
        reads.append({'serial': row['serial'], 'check_target': row.get('check_target'),
                      'resource': row.get('target'), 'channel_sizes': [b.get('size') for b in buffers], 'status': status})
    completed_backend = list(dict.fromkeys(_id(row.get('target')) for row in trace
        if row.get('event') == 'backend-check-progress' and row.get('percent') == 100
        and row.get('hresult') == row.get('code') == 0))
    public_completion = [row for row in events if row.get('operation') == 'ProgramCheckCompleted']
    drained_completion = [row for row in events if row.get('operation') == 'ProgramCheckPollingFinished']
    target, drained_targets = None, []
    for row in events:
        if row.get('operation') == 'ProgramCheckReportContext':
            target = _id(row.get('target'))
        elif row.get('operation') == 'ProgramCheckTargetOutcome' and 'public_check' in row:
            drained_targets.append((target, row['public_check']))
    projection_failures = [row for row in trace if row.get('event') == 'diagnostic-projection-failure']
    failed_public_poll = any(row.get('operation') in ('ProgramCheckProgress.GetProgress', 'ProgramCheckOriginalProgress')
        and (row.get('hresult', 0) < 0 or row.get('code', 0) != 0) for row in events)
    public_diagnostics = [report for row in events if (row.get('operation', '').startswith('ProgramCheckProgress')
        or row.get('operation') == 'ProgramCheckOriginalReports')
        for report in row.get('reports', []) if report.get('kind') in (2, 3)]
    backend_diagnostics = [report for row in trace if row.get('event') == 'backend-check-progress'
        for report in row.get('reports', []) if report.get('kind') in (2, 3)]
    legacy_completed = bool(public_completion) and public_completion[-1].get('targets') == len(planned)
    drained_completed = (bool(drained_completion) and drained_completion[-1].get('targets') == len(planned)
        and len(drained_targets) == len(planned) and {t for t, _ in drained_targets} == set(planned)
        and all(status in ('completed-accepted', 'completed-rejected') for _, status in drained_targets))
    whole_completed = (bool(planned) and set(started) == set(planned)
        and (legacy_completed or drained_completed) and not projection_failures and not failed_public_poll)
    public_status = ('rejected' if (public_completion and public_completion[-1].get('rejected'))
                     or any(status == 'completed-rejected' for _, status in drained_targets)
                     or any(d['kind'] == 2 for d in public_diagnostics)
                     else 'passed') if whole_completed else 'incomplete'
    source_binding = ('conflicted' if source_audits and any(a['status'] == 'source-conflict' for a in source_audits)
        else 'consistent' if source_audits and len(source_audits) == len(public_diagnostics)
            and all(a['status'] == 'source-consistent' for a in source_audits)
        else 'partial_or_unresolved' if source_audits else 'not_observed')
    requested = any(row.get('operation') == 'Workspace.GetProgramCheckCollection' for row in events) or bool(started)
    if not requested:
        public_status = 'not_requested'
    if not reads:
        correspondence = 'not_observed'
    elif observation_errors or any(row['status'] not in ('current', 'stale') for row in reads):
        correspondence = 'not_established'
    elif any(row['status'] == 'stale' for row in reads):
        correspondence = 'stale'
    elif not planned or any(not any(_id(row['check_target']) == target for row in reads) for target in planned):
        correspondence = 'partial_targets'
    else:
        correspondence = 'current'
    compile_completed = any(row.get('operation') == 'Progress' and row.get('percent') == 100 for row in events)
    compile_diagnostics = [report for row in events if row.get('operation') == 'Progress'
        for report in row.get('reports', []) if report.get('kind') in (2, 3)]
    compile_failures = [row for row in events if row.get('operation') in ('Compiler.Build', 'Progress.GetProgress')
        and (row.get('hresult', 0) < 0 or row.get('code', 0) != 0)]
    compile_started = any(row.get('operation') == 'Compiler.Build'
        and row.get('hresult') == row.get('code') == 0 for row in events)
    compile_acceptance = ('rejected' if compile_failures or any(row['kind'] == 2 for row in compile_diagnostics)
        else 'accepted' if compile_started and compile_completed else 'not_established')
    selected = selection or {'status': 'not_observed'}
    selection_current = (selected.get('status') == 'verified' and selected.get('source_bytes_current') is True
        and selected.get('configuration_bytes_current') is True and bool(selected.get('selected_sources'))
        and not selected.get('empty_task'))
    current_source_check = ('established' if selection_current and compile_acceptance == 'accepted'
        and correspondence == 'current' and whole_completed else 'not_established')
    return {
        'selection': selected,
        'compilation': {'completed': compile_completed, 'acceptance': compile_acceptance,
            'diagnostics': compile_diagnostics, 'failures': compile_failures,
            'returned_resources': sum(row.get('operation') == 'Resource' for row in events), 'bound_resources': len(generated)},
        'publication': {'completed': any(row.get('operation') == 'Workspace.UpdatePCodeBeforeProgramCheck'
            and row.get('hresult') == row.get('code') == 0 for row in events)},
        'checker_resources': {'correspondence': correspondence, 'reads': reads, 'size_queries': queries,
                              'observation_errors': observation_errors},
        'targets': {'planned': [list(t) for t in planned], 'started': [list(t) for t in started],
                    'backend_completed': [list(t) for t in completed_backend]},
        'public_check': {'status': public_status, 'completed': whole_completed},
        'diagnostic_projection': {'status': 'failed' if projection_failures or failed_public_poll else
            'completed' if whole_completed else 'not_completed', 'failures': projection_failures,
            'source_body_binding': source_binding, 'source_audits': source_audits or [],
            'public_diagnostics': public_diagnostics, 'backend_diagnostics': backend_diagnostics,
            'empty_public_list_means_no_errors': whole_completed and not public_diagnostics and not backend_diagnostics},
        'save': {'completed': any(row.get('operation') == 'SaveProject' and row.get('hresult') == row.get('code') == 0
            for row in events), 'exported': any(row.get('operation') == 'NativeExport' for row in events)},
        'reopen': reopened or {'status': 'not_observed'},
        'current_source_check': current_source_check,
    }


def analyze_directory(directory):
    directory = Path(directory)
    events = [json.loads(line) for line in (directory/'native-events.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    observed = next((directory/name for name in ('observation-events.jsonl', 'events.jsonl') if (directory/name).exists()), None)
    observations = [json.loads(line) for line in observed.read_text(encoding='utf-8').splitlines()] if observed else []
    names = {_id(row['id']): row['name'] for row in events if row.get('operation') == 'NativeObject' and row.get('name')}
    resources = {row['name']: tuple((directory/f"pcode-{row['index']}-{i}.bin").read_bytes() for i in range(3))
                 for row in events if row.get('operation') == 'Resource'}
    generated = {identity: resources[name] for identity, name in names.items() if name in resources}
    source_audits = []
    audit_path = directory/'public-source-diagnostic-audit.json'
    if audit_path.exists():
        projects = [row['words'] for row in events if row.get('operation') == 'ProjectID']
        modules = [row for row in events if row.get('operation') == 'OwnedBackendModule']
        version = ('1.635.0.1' if modules and all(row.get('version') == '1.635.0.1' for row in modules)
            and {row['name'] for row in modules} == {'DZDataABS_CompilerAdapter.dll', 'DZDataABS_Compiler_IEC.dll'} else None)
        for row in json.loads(audit_path.read_text(encoding='utf-8')):
            public = row.get('audit', {}).get('original_public_diagnostic')
            raw = row.get('raw_observation')
            if public is None or raw is None:
                source_audits.append({'status': 'unresolved', 'reason': 'original correspondence missing'})
                continue
            original = dict(raw, library={'text': bytes.fromhex(raw['library_hex']).decode('cp936')},
                name={'text': bytes.fromhex(raw['name_hex']).decode('cp936')})
            source_audits.append(compare_public_source_diagnostic(public, row['source_query'], original,
                project_id=projects[0] if len(projects) == 1 else None, native_version=version))
    return analyze_check(events, observations, generated, source_audits=source_audits)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze_directory(args.directory), ensure_ascii=False, indent=2))

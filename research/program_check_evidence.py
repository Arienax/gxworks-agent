"""Classify saved native check observations without running GX Works2.

Compilation, actual resource reads, backend completion, public diagnostics and
save/reopen are independent facts. Publication alone is not read evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gxw.native_diagnostics import (analyze_native_check as analyze_check, bind_native_check_input, bind_native_source_chain, bind_native_source_snapshot,
                                    project_native_source_identity,
                                    project_native_source_diagnostic as _project_source_diagnostic)


def _id(value):
    return tuple(value) if isinstance(value, list) and len(value) == 12 else None


def has_current_native_check_code(evidence):
    """Accept owned task bytes without hiding conflicting observed reads."""
    traced = evidence.get('checker_resources', {})
    owned = evidence.get('owned_check_inputs', {}).get('correspondence', 'not_observed')
    if owned == 'not_observed':
        return traced.get('correspondence') == 'current'
    return (owned == 'current' and not traced.get('observation_errors')
            and traced.get('correspondence') in ('current', 'not_observed')
            and evidence.get('raw_backend_check', {}).get('completed') is True)


def project_native_source_diagnostic(query, diagnostic, *, project_id, native_version, references=None):
    """Join an original resource diagnostic to a verified project-local source.

    Source identity, current checker reads and graph positions are separate
    facts. Optional instance references require complete original ranges.
    """
    result = _project_source_diagnostic(query, diagnostic, project_id=project_id, native_version=native_version)
    if result['status'] != 'source-resolved':
        return result
    location = query['location']
    pou, kind = location['pou'], location['program_kind']
    if references is not None and kind in (193, 208):
        scoped = [row for row in references if row.get('program_kind') == kind
                  and row.get('source') == pou and row.get('library') == ''
                  and row.get('resource') == query['resource'] and row.get('task') and row.get('instance')
                  and (row.get('top') == location.get('start_step') and row.get('attribute') == 2
                       if kind == 193 else row.get('network') == location.get('network'))]
        adapted = {**query, 'query': {'resource': query['resource'], 'start_step': query['code_step']}}
        binding = _native_instance_interval(adapted, scoped, query.get('instance_ranges'))
        interval = binding.pop('interval')
        if interval is None:
            result['instance_projection'] = {'status': 'unresolved', **binding}
        else:
            reference = interval['native_range_evidence']['original_reference']
            result['instance_projection'] = {'status': 'instance-resolved',
                'instance': reference['instance'], 'task': reference['task'],
                'compiled_interval': interval, 'node_port_resolution': 'not-established'}
    return result


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


def _native_instance_interval(query, references, evidence):
    """Require complete original GetPCodeRange responses for native instances.

    The observed 1.635.0.1 ordinary interface accepts a full instance path
    without a task prefix.
    ST uses the observed single-line query. FBD retains every source position
    field returned by GetPOULocation, including its -1 span; it is not a bbox.
    An accepted call can still return a null-resource/-1 sentinel. Inactive
    declarations and cached variable references are not executable intervals.
    """
    native, location = query['query'], query['location']
    missing = {'interval': None}
    kind = location.get('program_kind')
    if kind not in (193, 208):
        return {**missing, 'reason': 'source language outside observed native instance queries'}
    if (not isinstance(evidence, dict) or evidence.get('status') != 'completed'
            or evidence.get('resource') != native.get('resource')
            or evidence.get('diagnostic_step') != native.get('start_step')):
        return {**missing, 'reason': 'native instance range queries incomplete or mismatched'}
    candidates = evidence.get('candidates')
    if not isinstance(candidates, list):
        return {**missing, 'reason': 'native instance range responses unavailable'}
    expected = {(row['task'], row['instance']) for row in references}
    seen, matches = set(), []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return {**missing, 'reason': 'malformed native instance range response'}
        row, source = candidate.get('original_reference'), candidate.get('query') or {}
        if not isinstance(row, dict) or row not in references:
            return {**missing, 'reason': 'native interval reference differs from current source references'}
        identity = (row['task'], row['instance'])
        if identity in seen:
            return {**missing, 'reason': 'duplicate native instance range response'}
        seen.add(identity)
        expected_source = {'library': '', 'pou': row['instance'], 'program_kind': kind,
            'network': location.get('network'), 'start_step': location.get('start_step'),
            'step_count': 1 if kind == 193 else location.get('step_count'), 'element_id': location.get('element_id')}
        if (source != expected_source
                or any(type(source.get(key)) is not int for key in
                       ('program_kind','network','start_step','step_count','element_id'))
                or any(type(candidate.get(key)) is not int or candidate[key] != 0 for key in ('hresult','code'))):
            return {**missing, 'reason': 'original native source range query failed or differs'}
        value = candidate.get('range')
        if (isinstance(value, dict) and value.get('resource') is None
                and all(value.get(key) == -1 for key in ('start_step', 'step_count', 'timestamp'))):
            continue
        if (not isinstance(value, dict) or value.get('resource') != native.get('resource')
                or any(type(value.get(key)) is not int for key in ('start_step','step_count','timestamp'))
                or value['start_step'] < 0 or value['step_count'] <= 0 or value['timestamp'] < 0):
            return {**missing, 'reason': 'native source interval is absent or belongs to another resource'}
        if value['start_step'] <= native['start_step'] < value['start_step'] + value['step_count']:
            matches.append(dict(name=row['task']+'.'+row['instance'], step_start=value['start_step'],
                step_count=value['step_count'], token_offset=None, token_length=None,
                native_range_evidence=candidate))
    if seen != expected or not expected:
        return {**missing, 'reason': 'native instance range selection is incomplete'}
    if len(matches) != 1:
        return {**missing, 'reason': 'native instance source interval missing or ambiguous',
                'range_candidates': len(matches)}
    return {'interval': matches[0]}


def correlate_st_diagnostic(query, link_rows, references, source_texts, caller_nodes, *, debug=None,
                            debug_encoding=None, caller_pou=None, native_ranges=None):
    """Correlate a controlled native ST line with one compiled FB instance.

    This is saved evidence processing, not a replacement public check. Exact
    task/instance names and an original code interval are required. Follow
    explicit native FB call references through ST callers to a current FBD
    node; do not infer parents by splitting instance names. Optional same-
    compile debug data can resolve an inlined FB without a link-map row.
    When original instance range responses are supplied, require their complete
    agreement and do not replace a failed or conflicting API result with caches.
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
    if native_ranges is not None:
        if type(step) is not int or step < 0:
            return {**unresolved, 'reason': 'original diagnostic code step is unavailable'}
        resolved = _native_instance_interval(query, line_references, native_ranges)
        if resolved['interval'] is None:
            return {**unresolved, **{key:value for key,value in resolved.items() if key != 'interval'}}
        interval = resolved['interval']
    matches = [row for row in line_references if interval is not None
               and row['task'] + '.' + row['instance'] == interval['name']]
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
    if 'native_range_evidence' in interval:
        result['native_range_evidence'] = interval['native_range_evidence']
    return result


def correlate_fbd_bool_output(query, diagnostic, records, references, model, *, selected_pou, connectivity=None):
    """Correlate observed FX3U/FX3UC and Q03UDV scalar FB output diagnostics.

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
    if (model.get('schema_version') != 2 or model.get('cpu') not in ('FX3U/FX3UC','Q03UDV')
            or model.get('unknown_record_count') != 0):
        return reject('outside observed FX3U/FX3UC or Q03UDV source/graph scope')
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
    def output_chain(write):
        # Q03UDV's controlled one-driver/two-terminal case emits LD/OUT/OUT.
        # Follow only contiguous ordinary OUT records; every other opcode,
        # lexical gap or duplicate record leaves the producer unresolved.
        chain,seen = [write],set()
        for _ in range(64):
            current=chain[-1]
            if (any(type(current.get(key)) is not int for key in
                    ('record_index','native_step','source_offset','source_end'))
                    or current['record_index'] in seen or current['record_index']<0
                    or sum(type(r.get('record_index')) is int and r['record_index']==current['record_index']
                           for r in records)!=1
                    or not 0<=current['source_offset']<current['source_end']):
                return None
            seen.add(current['record_index'])
            previous=[r for r in records if type(r.get('record_index')) is int
                and r['record_index']==current['record_index']-1]
            if len(previous)!=1:
                return None
            prior=previous[0]
            if (len(prior.get('args',[]))!=1
                    or any(type(prior.get(key)) is not int for key in ('native_step','source_offset','source_end'))
                    or prior['source_end']!=current['source_offset']
                    or not 0<=prior['source_offset']<prior['source_end']
                    or not 0<=prior['native_step']<current['native_step']):
                return None
            if prior.get('op')=='LD':
                return [prior,*reversed(chain)]
            if prior.get('op')!='OUT':
                return None
            chain.append(prior)
        return None
    def direct_read(write):
        chain=output_chain(write)
        return chain[0] if chain is not None else None
    chain=output_chain(output)
    if chain is None:
        return reject('not a contiguous native LD/OUT output chain')
    read=chain[0]
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
    def scalar_terminal(row):
        # Original direct-device references have no declaration type. The
        # independently observed scalar local/global BOOL references do.
        # Other declaration roles, arrays and conflicting types stay unresolved.
        return (type(row.get('array_data_type')) is int and row['array_data_type']==0
            and ((type(row.get('class_code')) is int and row['class_code']==0
                  and type(row.get('data_type')) is int and row['data_type']==0)
                or (type(row.get('class_code')) is int and row['class_code'] in (1,8)
                    and type(row.get('data_type')) is int and row['data_type']==1)))
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
            writes = [r for r in scoped if r.get('attribute') == 2 and r.get('address') and scalar_terminal(r)
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
        if (reference.get('attribute') != 2 or not reference.get('address') or not scalar_terminal(reference)
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
        if len(chain)>2:
            result['native_output_chain']=chain
        return result
    destination, reference, link = destinations[0]
    result = {'status':'uniquely_correlated','public_check_promoted':False,
        'original_kind':diagnostic['kind'],'original_code':diagnostic['code'],
        'resource':resource,'source_pou':selected_pou,'diagnostic_step':step,
        'native_pair':{'read':read,'write':output},'native_member':producer,
        'source':{'object_id':source['id'],'instance':source['symbol'],'formal':port['formal_name'],
            'port_name':port['name'],'side':'out','point':point},
        'output':{'object_id':destination['id'],'source_offset':destination['source_offset'],
            'symbol':destination['symbol'],'bbox':node_bbox(destination),'native_reference':reference},
        'connection':link,
        'limits':['contiguous native LD/OUT chain and direct scalar FB BOOL/output connection only',
            'current native facts and source context required; no execution claim']}
    if len(chain)>2:
        result['native_output_chain']=chain
    return result


def analyze_directory(directory):
    directory = Path(directory)
    events = [json.loads(line) for line in (directory/'native-events.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    observed = next((directory/name for name in ('observation-events.jsonl', 'events.jsonl') if (directory/name).exists()), None)
    observations = [json.loads(line) for line in observed.read_text(encoding='utf-8').splitlines()] if observed else []
    names = {_id(row['id']): row['name'] for row in events if row.get('operation') == 'NativeObject' and row.get('name')}
    resources = {row['name']: tuple((directory/f"pcode-{row['index']}-{i}.bin").read_bytes() for i in range(3))
                 for row in events if row.get('operation') == 'Resource'}
    generated = {identity: resources[name] for identity, name in names.items() if name in resources}
    published = {}
    owned_snapshots = {}
    for row in events:
        if row.get('operation') == 'OwnedCheckInput':
            for item in row.get('rows', []):
                path = directory / item['file']
                if path.resolve().is_relative_to(directory.resolve()) and path.is_file():
                    owned_snapshots[item['file']] = path.read_bytes()
        if row.get('operation') != 'PublishedResourceCodeCorrespondence':
            continue
        channels = row.get('channels', [])
        if len(channels) != 3 or [channel.get('channel') for channel in channels] != [0, 1, 2]:
            continue
        paths = [directory/channel['published_snapshot'] for channel in channels]
        if all(path.resolve().is_relative_to(directory.resolve()) and path.is_file() for path in paths):
            published[_id(row.get('target'))] = tuple(path.read_bytes() for path in paths)
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
    return analyze_check(events, observations, generated, source_audits=source_audits,
                         published=published, owned_snapshots=owned_snapshots)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze_directory(args.directory), ensure_ascii=False, indent=2))

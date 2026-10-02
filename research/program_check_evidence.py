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
                            debug_encoding=None):
    """Correlate a controlled native ST line with one compiled FB instance.

    This is saved evidence processing, not a replacement public check. Exact
    task/instance names and an original code interval are required. Optional
    same-compile debug data can resolve an inlined FB without a link-map row.
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
    calls = [row for row in references if row.get('program_kind') == 208 and row.get('class_code') == 1
             and row.get('type') == pou and row.get('resource') == native.get('resource')
             and row.get('task') == reference['task']
             and row.get('instance', '') + '.' + row.get('name', '') == reference['instance']]
    if len(calls) != 1:
        return {**unresolved, 'reason': 'native caller missing or ambiguous', 'caller_candidates': len(calls)}
    call = calls[0]
    objects = [node for node in caller_nodes if node.get('symbol') == call['name']
               and node.get('template') == 'function_block:' + pou
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
    if 'debug_evidence' in interval:
        result['debug_evidence'] = interval['debug_evidence']
    return result


def analyze_check(events, observations, generated, *, selection=None, reopened=None):
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
    projection_failures = [row for row in trace if row.get('event') == 'diagnostic-projection-failure']
    failed_public_poll = any(row.get('operation') == 'ProgramCheckProgress.GetProgress'
        and (row.get('hresult', 0) < 0 or row.get('code', 0) != 0) for row in events)
    public_diagnostics = [report for row in events if row.get('operation', '').startswith('ProgramCheckProgress')
        for report in row.get('reports', []) if report.get('kind') in (2, 3)]
    backend_diagnostics = [report for row in trace if row.get('event') == 'backend-check-progress'
        for report in row.get('reports', []) if report.get('kind') in (2, 3)]
    whole_completed = (bool(planned) and set(started) == set(planned) and bool(public_completion)
        and public_completion[-1].get('targets') == len(planned) and not projection_failures and not failed_public_poll)
    public_status = ('rejected' if public_completion[-1].get('rejected') or any(d['kind'] == 2 for d in public_diagnostics)
                     else 'passed') if whole_completed else 'incomplete'
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
    return analyze_check(events, observations, generated)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze_directory(args.directory), ensure_ascii=False, indent=2))

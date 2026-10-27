# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Saved source and requested native inspection correspondence."""
from __future__ import annotations
from collections import defaultdict
from .diagnostic_events import EventIndex
from gxw.diagnostic_sources import (
    _native_assignment_sources,
    native_inspection_queries,
)
from gxw.diagnostic_bindings import (
    project_native_source_identity,
    bind_native_instance_interval,
)


def project_native_inspection(raw, observation, snapshots, validation, requested):
    """Project original getters using the established compile/source binding.

    Saved code mismatch is an observation, never a save gate. Code locations
    describe this offline compilation; execution correspondence needs separate
    simulator/PLC evidence.
    """
    requested = native_inspection_queries(raw, requested)
    declarations = {}
    if requested['assignments']:
        local, global_ = _native_assignment_sources(raw,
            include_globals=any(row.get('scope') == 'global' for row in requested['assignments']))
        declarations = {(scope, name, row.name): row
                        for scope, documents in (('local', local), ('global', global_))
                        for name, doc in documents.items() for row in doc.rows}

    def assignment_name_bound(name, query):
        if name == query['name'] or name.startswith(query['name'] + '.'):
            return True
        # Original getters expand local arrays into indexed names. Bind those
        # names to the saved declaration rather than accepting any prefix.
        import re
        from .declarations import _type_parts
        from .models import GXWFormatError
        try:
            _, bounds = _type_parts(declarations[(query.get('scope', 'local'), query['source'], query['name'])].data_type)
        except GXWFormatError:
            return False
        match = re.fullmatch(re.escape(query['name']) + r'\[(-?[0-9]+(?:,-?[0-9]+)*)\]', name)
        if not bounds or match is None:
            return False
        try:
            indices = [int(part) for part in match[1].split(',')]
        except ValueError:
            return False
        return len(indices) == len(bounds) and all(low <= index <= high for index, (low, high) in zip(indices, bounds))

    events = EventIndex.of((observation.get('validation') or {}).get('events', []))
    assignments = events.grouped('NativeDeviceAssignment', key=lambda row: (
        tuple(row['query'].get(field) for field in ('scope', 'source', 'name'))
        if isinstance(row.get('query'), dict) else None))
    def location_key(row):
        location = row.get('source_location')
        return (location.get('resource'), location.get('code_step')) if isinstance(location, dict) else None

    locations = events.grouped('NativeRequestedCodeLocation', key=location_key)
    current = validation.get('compilation', {}).get('source_context', {}).get('status') == 'current'
    references = validation.get('source_references') or {}
    rows = references.get('rows') if references.get('status') == 'current' else None
    references_by_source = defaultdict(list)
    for reference in rows or []:
        if reference.get('task') and reference.get('instance'):
            try:
                references_by_source[tuple(reference.get(field) for field in
                    ('program_kind', 'source', 'library', 'resource'))].append(reference)
            except TypeError:
                continue
    result = {'source_context': 'current' if current else 'unresolved',
              'execution_correspondence': 'unverified', 'assignments': [], 'locations': [],
              'saved_code': {'status': 'not_requested', 'resources': []}}
    for query in requested['assignments']:
        key = tuple(query.get(field) for field in ('scope', 'source', 'name'))
        matches = [row for _, row in assignments.get(key, ()) if row.get('query') == query]
        item = {'query': query, 'status': 'unresolved', 'assignments': []}
        if len(matches) == 1:
            row = matches[0]
            values = row.get('assignments')
            valid = (type(row.get('hresult')) is int and row['hresult'] >= 0 and type(row.get('code')) is int and row['code'] == 0
                     and type(row.get('count')) is int and isinstance(values, list) and row['count'] == len(values)
                     and len(values) <= 4096 and all(isinstance(v, dict) and set(v) == {'name', 'address', 'source', 'type', 'count'}
                         and all(isinstance(v[k], str) for k in ('name', 'address', 'source', 'type'))
                         and type(v['count']) is int and v['count'] > 0
                         and assignment_name_bound(v['name'], query) for v in values))
            if valid:
                if query.get('scope', 'local') == 'global':
                    # The measured name-only getter also returns same-named
                    # locals. Its empty source identifies project globals;
                    # retain that original field rather than inventing a POU.
                    values = [v for v in values if v['source'] == '']
                else:
                    valid = all(v['source'] == query['source'] for v in values)
            item.update(hresult=row.get('hresult'), code=row.get('code'))
            if current and valid:
                item.update(status='resolved' if values else 'not_assigned', assignments=values)
        result['assignments'].append(item)
    for query in requested['locations']:
        matches = [row.get('source_location') for _, row in locations.get((query['resource'], query['step']), ())]
        projection = {'status': 'unresolved', 'reason': 'current compile source and one original getter result are required'}
        native_result = None
        if len(matches) == 1:
            native_result = {key: matches[0].get(key) for key in ('hresult', 'code')}
            if matches[0].get('reason'):
                projection['reason'] = matches[0]['reason']
        if current and len(matches) == 1:
            getter = matches[0]
            location = getter.get('location') or {}
            if all(type(getter.get(key)) is int and getter[key] == 0 for key in ('hresult', 'code')):
                projection = project_native_source_identity(getter.get('source_object'), pou=location.get('pou'),
                    program_kind=location.get('program_kind'), project_id=(observation.get('validation') or {}).get('project'),
                    native_version=observation.get('native_version'), library=location.get('library'))
                if projection['status'] == 'source-resolved':
                    projection['native_position'] = {key: location.get(key) for key in
                        ('network', 'start_step', 'step_count', 'element_id', 'action_transition_present')}
                    if location.get('program_kind') == 193 and type(location.get('start_step')) is int and location['start_step'] >= 0:
                        projection['source_line'] = location['start_step'] + 1
                    key = (location.get('program_kind'), location.get('pou'), location.get('library'), query['resource'])
                    scoped = [r for r in references_by_source.get(key, ())
                        if (r.get('top') == location.get('start_step') and r.get('attribute') in (1, 2)
                             if location.get('program_kind') == 193 else r.get('network') == location.get('network'))]
                    binding = bind_native_instance_interval({**getter, 'query': {'resource': query['resource'], 'start_step': query['step']}},
                        scoped, getter.get('instance_ranges'))
                    interval = binding.get('interval')
                    if interval is not None:
                        ref = interval['native_range_evidence']['original_reference']
                        projection['instance'] = {'status': 'instance-resolved', 'task': ref['task'], 'name': ref['instance']}
                    else:
                        projection['instance'] = {'status': 'unresolved', 'reason': binding.get('reason'),
                            'candidates': [{'task': r['task'], 'name': r['instance']} for r in scoped]}
        result['locations'].append({'query': query, 'native_result': native_result, **projection})
    if requested['saved_code']:
        def buffers(event):
            channels = event.get('channels')
            if not isinstance(channels, list) or [c.get('channel') for c in channels] != [0, 1, 2]:
                return None
            data = [snapshots.get(c.get('file')) for c in channels]
            return data if all(isinstance(b, bytes) and type(c.get('bytes')) is int and len(b) == c['bytes']
                               for b, c in zip(data, channels)) else None
        saved = list(events.rows('SavedResourceCode'))
        generated = list(events.rows('Resource'))
        saved_by_name = events.grouped('SavedResourceCode', key=lambda row: row.get('resource'))
        generated_by_name = events.grouped('Resource', key=lambda row: row.get('name'))
        for entry in saved:
            name = entry.get('resource')
            matches = generated_by_name.get(name, ())
            before = buffers(entry)
            after = buffers(matches[0][1]) if len(matches) == 1 else None
            item = {'resource': name, 'status': 'unresolved', 'channels': []}
            if current and before is not None and after is not None and len(saved_by_name.get(name, ())) == 1:
                item['channels'] = [{'channel': i, 'saved_bytes': len(a), 'generated_bytes': len(b), 'matches': a == b}
                                    for i, (a, b) in enumerate(zip(before, after))]
                item['status'] = 'matches' if before == after else 'differs'
            result['saved_code']['resources'].append(item)
        complete = (current and saved and len(saved) == len(generated) and
                    all(r['status'] != 'unresolved' for r in result['saved_code']['resources']))
        result['saved_code']['status'] = ('matches' if all(r['status'] == 'matches' for r in result['saved_code']['resources'])
                                         else 'differs') if complete else 'unresolved'
    return result

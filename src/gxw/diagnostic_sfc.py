"""SFC child source, graph and diagnostic correspondence."""
from __future__ import annotations
from .diagnostic_events import EventIndex
from gxw.diagnostic_scope import (
    NATIVE_SOURCE_CAPACITY,
    SFC_CHILD_INSTRUCTION_POSITION_CPUS,
    _id,
)
from gxw.diagnostic_sources import (
    _native_validation_sfc_children,
    sfc_child_source_scope,
)
from gxw.diagnostic_bindings import (
    bind_native_source_snapshot,
    project_native_source_identity,
)


def bind_native_sfc_child_snapshots(program_raw, *, owner_id, stage, reads, snapshots):
    """Bind all stored SFC child identities to this phase's original getters."""
    from .models import GXWFormatError
    try:
        expected = _native_validation_sfc_children(program_raw)
        scope = sfc_child_source_scope(program_raw)
    except GXWFormatError:
        return {'status': 'unresolved', 'stage': stage, 'children': [], 'reason': 'SFC child framing is not established'}
    owner = _id(owner_id)
    reads = EventIndex.of(reads)
    selected = [row for row in reads.rows('NativeSfcChildRead')
                if row.get('stage') == stage and _id(row.get('parent')) == owner]
    missing = {'status': 'unresolved', 'stage': stage, 'children': [], 'source_scope': scope}
    keys = [(row['type'], row['name']) for row in expected]
    if owner is None or not any(owner) or len(set(keys)) != len(keys) or len(selected) > len(expected):
        return {**missing, 'reason': 'current SFC child membership is missing or ambiguous'}
    if any(not isinstance(row.get('identity'), dict) or any(not isinstance(row['identity'].get(key), dict)
            for key in ('read_type', 'read_name')) for row in selected):
        return {**missing, 'reason': 'original SFC child facts are missing or malformed'}
    candidates_by_name = EventIndex.of(selected).grouped('NativeSfcChildRead', key=lambda row: (
        row['identity']['read_type'].get('data_type'), row['identity']['read_name'].get('name')))
    children, identities, absent = [], set(), []
    roles = {(row['type'], row['name']): row['role'] for row in scope['children']}
    for wanted in expected:
        candidates = candidates_by_name.get((wanted['type'], wanted['name']), ())
        role = roles.get((wanted['type'], wanted['name']), 'unresolved')
        if not candidates:
            absent.append({'type': wanted['type'], 'name': wanted['name'], 'role': role})
            continue
        if len(candidates) != 1:
            return {**missing, 'reason': 'stored SFC child type/name is missing or duplicated'}
        (_, row), = candidates
        facts = row['identity']
        identity = _id(facts.get('id'))
        if (identity is None or not any(identity) or identity == owner or identity in identities
                or any(type(word) is not int or not 0 <= word <= 0xffffffff for word in identity)
                or any((facts.get(key) or {}).get('hresult') != 0 or (facts.get(key) or {}).get('code') != 0
                       for key in ('read_type', 'read_name'))
                or row.get('provenance') != 'original Workspace.GetPOUBodyData(1692); source bytes, not compiled PCode'):
            return {**missing, 'reason': 'original SFC child identity/getter is not bound'}
        identities.add(identity)
        current = snapshots.get(row.get('file'))
        if not isinstance(current, bytes) or type(row.get('bytes')) is not int or len(current) != row['bytes']:
            return {**missing, 'reason': 'original SFC child snapshot is missing or truncated'}
        children.append({'type': wanted['type'], 'name': wanted['name'], 'role': role, 'id': list(identity),
                         'status': 'current' if current == wanted['body'] else 'stale',
                         'native_snapshot': row['file'], 'native_body_bytes': len(current)})
    if len(children) != len(selected):
        return {**missing, 'reason': 'original SFC child selection includes an unexpected source'}
    return {'status': 'unresolved' if absent else 'current' if all(row['status'] == 'current' for row in children) else 'stale',
            'stage': stage, 'children': children, 'missing': absent, 'source_scope': scope,
            **({'reason': 'stored SFC children are absent from the native workspace'} if absent else {})}


def bind_native_source_chain(programs, required_sources, *, project_id, native_version,
                             selections, reads, snapshots):
    """Require current original bodies for every callee and caller in a chain.

    Equal compiled code, names or coordinates cannot bind source offsets. Use
    the last selection and body read for each POU; never fill a missing caller
    with another source that happens to have the same text or graph.
    """
    def source_key(value):
        if isinstance(value, str) and value:
            return '', value
        if (isinstance(value, tuple) and len(value) == 2
                and all(isinstance(part, str) and part for part in value)):
            return value
        return None
    if (not required_sources or any(source_key(name) is None for name in required_sources)
            or len(required_sources) > NATIVE_SOURCE_CAPACITY):
        return {'status': 'unresolved', 'reason': 'native source chain is missing or outside the bound',
                'sources': []}
    selection_rows = selections
    selections = EventIndex.of(selections)
    reads = selections if reads is selection_rows else EventIndex.of(reads)
    selections_by_source = selections.grouped('NativeBodySelection', key=lambda row: (row.get('library', ''), row.get('name')))
    bodies_by_id = reads.grouped('NativeBodyRead', key=lambda row: _id(row.get('body')))
    children_by_owner = reads.grouped('NativeSfcChildRead', key=lambda row: (_id(row.get('parent')), row.get('stage')))
    sources = []
    for key in dict.fromkeys(required_sources):
        library, pou = source_key(key)
        namespace = {'library': library} if library else {}
        matching = selections_by_source.get((library, pou), ())
        selected = matching[-1][1] if matching else {}
        identity = project_native_source_identity(selected.get('source_object'), pou=pou,
            program_kind=selected.get('program_kind'), project_id=project_id, native_version=native_version, library=library)
        if identity['status'] != 'source-resolved':
            sources.append({'pou': pou, **namespace, 'status': 'unresolved', 'reason': identity['reason']})
            continue
        raw = programs.get(key)
        if not isinstance(raw, bytes):
            sources.append({'pou': pou, **namespace, 'status': 'unresolved', 'reason': 'selected source buffer is missing'})
            continue
        from .models import GXWFormatError
        from .source_header import source_payload_offset
        try:
            source_kind = raw[source_payload_offset(raw)]
        except (GXWFormatError, IndexError):
            source_kind = None
        if source_kind != selected.get('program_kind'):
            sources.append({'pou': pou, **namespace, 'status': 'unresolved',
                            'reason': 'native source language differs from the current source envelope'})
            continue
        binding = bind_native_source_snapshot(raw, body_id=identity['source']['body_id'],
            reads=[row for _, row in bodies_by_id.get(_id(identity['source']['body_id']), ())], snapshots=snapshots)
        if source_kind == 240:
            child_binding = bind_native_sfc_child_snapshots(raw, owner_id=identity['source']['pou_id'],
                stage=selected.get('stage'), reads=[row for _, row in children_by_owner.get(
                    (_id(identity['source']['pou_id']), selected.get('stage')), ())], snapshots=snapshots)
            binding = {**binding, 'pou_id': identity['source']['pou_id'], 'sfc_children': child_binding,
                       **({'graph_status': binding['status']} if child_binding.get('missing') else {}),
                       'status': binding['status'] if child_binding['status'] == 'current' else child_binding['status']}
        sources.append({'pou': pou, **namespace, **binding})
    statuses = {row['status'] for row in sources}
    return {'status': 'current' if statuses == {'current'} else
            'unresolved' if 'unresolved' in statuses else 'stale', 'sources': sources}


def bind_native_sfc_referenced_check(programs, *, project_id, native_version, events, snapshots, imported, result,
                                     configuration_current, task_binding, selected, cpu):
    """Bind the current referenced SFC sources without promoting stored sources.

    Missing retained children stay missing. Every referenced child and graph
    needs independent, ordered original getter bytes in all three phases.
    """
    unresolved = {'status': 'unresolved', 'scope': 'referenced_sfc_source',
                  'public_check_promoted': False, 'sources': []}
    from .source_header import source_payload_offset
    if (cpu != 'FX3U/FX3UC' or not programs or any(not isinstance(key, str) or raw[source_payload_offset(raw)] != 240
                            for key, raw in programs.items())
            or native_version != '1.635.0.1' or imported.get('status') != 'current'
            or not configuration_current or not task_binding or not selected or any(name not in programs for name in selected)
            or result['compilation']['acceptance'] != 'accepted' or not result['compilation']['diagnostics_complete']
            or not result['public_check']['completed']
            or result['published_resources']['correspondence'] != 'current'
            or result['owned_check_inputs']['correspondence'] != 'current'
            or result['checker_resources']['correspondence'] not in ('current', 'not_observed')
            or result['checker_resources']['observation_errors']):
        return {**unresolved, 'reason': 'current SFC configuration, compilation and completed check are required'}
    events = EventIndex.of(events)
    builds = [i for i, row in events.numbered('Compiler.ChangeSFCProgram') if row.get('hresult') == row.get('code') == 0]
    ends = [i for i, row in events.numbered('CompilePublicReports') if row.get('percent') == 100]
    checks = [i for i, row in events.numbered('Compiler.ProgramCheck') if row.get('hresult') == row.get('code') == 0]
    completions = [i for i, row in events.numbered('ProgramCheckOriginalProgress')
                   if row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0]
    if len(builds) != 1 or len(ends) != 1 or not checks or not completions or not builds[0] < ends[0] < min(checks) <= max(completions):
        return {**unresolved, 'reason': 'original SFC build/check boundaries are missing or ambiguous'}
    check_start, check_end = min(checks), max(completions)
    body_reads = events.grouped('NativeBodyRead', key=lambda row: (row.get('stage'), _id(row.get('body'))))
    def child_key(row):
        identity = row.get('identity')
        return row.get('stage'), _id(identity.get('id')) if isinstance(identity, dict) else None

    child_reads = events.grouped('NativeSfcChildRead', key=child_key)
    phases, identities = [], []
    for phase in ('before-build', 'after-build', 'after-check'):
        scoped = EventIndex.of(events.at_stage(phase))
        selections = list(scoped.rows('NativeBodySelection'))
        selections_by_name = scoped.grouped('NativeBodySelection', key=lambda row: row.get('name'))
        if len(selections) != len(programs) or any(sum(not row.get('library')
                for _, row in selections_by_name.get(name, ())) != 1 for name in programs):
            return {**unresolved, 'reason': 'original SFC source selection is missing or duplicated'}
        binding = bind_native_source_chain(programs, list(programs), project_id=project_id, native_version=native_version,
            selections=scoped, reads=scoped, snapshots=snapshots)
        phase_sources, phase_ids = [], []
        for source in binding['sources']:
            children = source.get('sfc_children') or {}
            scope = children.get('source_scope') or {}
            expected = {(row['type'], row['name']) for row in scope.get('children', []) if row['role'] == 'referenced'}
            actual = [row for row in children.get('children', []) if row.get('role') == 'referenced']
            if (source.get('graph_status', source.get('status')) != 'current' or scope.get('status') != 'source-projected' or not expected
                    or len(actual) != len(expected) or {(row['type'], row['name']) for row in actual} != expected
                    or any(row['status'] != 'current' for row in actual)
                    or any(row['role'] != 'retained' for row in children.get('missing', []))):
                return {**unresolved, 'reason': 'a referenced SFC graph or child is missing, stale or ambiguous'}
            required_ids = {_id(source['body_id']), *[_id(row['id']) for row in actual]}
            readings = [item for identity in required_ids
                        for groups in (body_reads, child_reads) for item in groups.get((phase, identity), ())]
            if len(readings) != len(required_ids) or any(not (
                    i < builds[0] if phase == 'before-build' else ends[0] < i < check_start
                    if phase == 'after-build' else i > check_end) for i, _ in readings):
                return {**unresolved, 'reason': 'referenced SFC getters are duplicated or out of phase'}
            phase_ids.append((source['pou'], tuple(source['pou_id']), tuple(source['body_id']),
                              tuple(sorted((row['type'], row['name'], tuple(row['id'])) for row in actual))))
            phase_sources.append({'pou': source['pou'], 'pou_id': source['pou_id'], 'body_id': source['body_id'],
                                  'children': actual, 'unloaded_retained_children': children.get('missing', [])})
        if len(phase_sources) != len(programs):
            return {**unresolved, 'reason': 'current SFC source selection is incomplete'}
        phases.append({'stage': phase, 'sources': phase_sources}); identities.append(sorted(phase_ids))
    if not identities[0] == identities[1] == identities[2]:
        return {**unresolved, 'reason': 'referenced SFC native identities changed between phases'}
    return {'status': 'current', 'scope': 'referenced_sfc_source', 'public_check_promoted': False,
            'sources': phases[-1]['sources'], 'phases': phases,
            'provenance': 'current imported SFC source/configuration, three-phase original getters and current owned checker input'}


def project_native_sfc_child_diagnostic(report, sources, source_bodies, *, project_id, native_version, referenced_check=None):
    """Bind a typed SFC child report to its independently read current body.

    The public name is qualified by the SFC POU, while its object ID names a
    zoom/transition body. GetPCodeLocation may fail for this namespace. Keep
    that failure and the uninterpreted report coordinates separate from this
    source-object association.
    """
    from .models import GXWFormatError
    from .sfc_pou import parse_sfc_pou
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    identity = _id(report.get('source_object_id'))
    name = report.get('name', {}).get('text')
    if (native_version != '1.635.0.1' or _id(project_id) is None
            or report.get('report_interface') != 'public-compiler'
            or type(report.get('record_size')) is not int or report['record_size'] != 100
            or report.get('kind') not in (2, 3) or type(report.get('code')) is not int
            or report.get('instance_kind') != 6 or report.get('program_kind') != 1
            or not isinstance(name, str) or report.get('instance', {}).get('text') != name
            or identity is None or not any(identity)):
        return {**unresolved, 'reason': 'outside the observed typed SFC child report form'}
    candidates = []
    referenced_projection = False
    for row in sources:
        if row['program_kind'] != 240:
            continue
        bodies = [body for body in source_bodies.get('sources', []) if body.get('pou') == row['name']]
        if len(bodies) != 1:
            continue
        body = bodies[0]
        children = body.get('sfc_children') or {}
        scoped = [item for item in (referenced_check or {}).get('sources', []) if item.get('pou') == row['name']]
        scoped_current = ((referenced_check or {}).get('status') == 'current' and len(scoped) == 1
                          and _id(scoped[0].get('body_id')) == _id(body.get('body_id'))
                          and _id(scoped[0].get('pou_id')) == _id(body.get('pou_id')))
        if ((body.get('status') != 'current' or children.get('status') != 'current') and not scoped_current
                or children.get('stage') != 'after-check'):
            continue
        selected_children = scoped[0]['children'] if scoped_current else children['children']
        matching = [child for child in selected_children if _id(child.get('id')) == identity
                    and child.get('status') == 'current' and name == row['name'] + '.' + child['name']]
        if len(matching) != 1:
            continue
        child = matching[0]
        try:
            source = parse_sfc_pou(row['raw'])
        except GXWFormatError:
            continue
        kind = 'zoom' if child['type'] == 68 else 'transition'
        stored = [item for item in source.layout['children'] if (item['kind'], item['name']) == (kind, child['name'])]
        if len(stored) != 1:
            continue
        item = stored[0]
        registrations = [dict(action_name=action['name'], action_number=action['number'],
                              registration_index=index, qualifier=entry['qualifier'])
                         for action in source.layout['actions']
                         for index, entry in enumerate(action['registrations']) if entry['zoom'] == child['name']]
        referenced_projection = scoped_current
        candidates.append({'project_id': list(_id(project_id)), 'pou': row['name'], 'program_kind': 240,
            'logical_name': row['logical_name'], 'pou_id': body['pou_id'], 'body_id': list(identity),
            'parent_body_id': body['body_id'], 'child': {'kind': kind, 'name': child['name'],
                'number': item['number'], 'native_type': child['type'], 'native_snapshot': child['native_snapshot'],
                'token_region': {'offset': item['token_offset'], 'bytes': item['token_size']},
                'registrations': registrations}})
    if len(candidates) != 1:
        return {**unresolved, 'reason': 'typed report does not select one current original SFC child'}
    return {'status': 'source-resolved', 'public_check_promoted': False, 'source': candidates[0],
        'current_source_body': 'current', 'node_port_resolution': 'not-established',
        **({'check_scope': 'referenced_sfc_source', 'stored_source_check_promoted': False} if referenced_projection else {}),
        'native_position': {key: report.get(key) for key in ('step', 'network', 'left', 'top', 'right', 'bottom')},
        'position_interpretation': 'not-established',
        'provenance': 'typed public SFC child identity and qualified name matched to current original child getters'}


def project_native_sfc_instruction_position(report, projection, sources, *, cpu, native_version, current_check):
    """Match a checked SFC child's native code step to a known source record.

    This is a child-local instruction position, not a graph cell or resource
    code address. Unknown widths do not permit resynchronizing later steps.
    """
    from .models import GXWFormatError
    from .sfc_pou import parse_sfc_pou
    from .token_pou import parse_token_fragment
    from .token_listing import decode_token_program, TokenInstruction
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    if (not current_check or cpu not in SFC_CHILD_INSTRUCTION_POSITION_CPUS or native_version != '1.635.0.1'
            or projection.get('status') != 'source-resolved' or projection.get('current_source_body') != 'current'
            or report.get('report_interface') != 'public-compiler' or report.get('record_size') != 100
            or report.get('instance_kind') != 6 or report.get('program_kind') != 1 or report.get('kind') != 2
            or report.get('code') not in (0x050c9319, 0x050c9512)
            or any(report.get(key) != -1 for key in ('left', 'top'))
            or type(report.get('step')) is not int or report['step'] < 0):
        return {**unresolved, 'reason': 'outside the observed current SFC child instruction report scope'}
    source = projection.get('source', {}); child = source.get('child', {})
    selected = [row for row in sources if row.get('name') == source.get('pou') and row.get('program_kind') == 240
                and row.get('logical_name') == source.get('logical_name')]
    if (len(selected) != 1 or _id(source.get('body_id')) != _id(report.get('source_object_id'))
            or report.get('name', {}).get('text') != source.get('pou', '') + '.' + child.get('name', '')
            or report.get('instance', {}).get('text') != report.get('name', {}).get('text')):
        return {**unresolved, 'reason': 'checked child report identity differs from the current source'}
    block_number = getattr(selected[0].get('declarations'), 'owner_sfc_block_number', None)
    if (type(block_number) is not int or type(report.get('network')) is not int
            or report['network'] != block_number):
        return {**unresolved, 'reason': 'checked child report differs from the current SFC block number'}
    if child.get('kind') == 'zoom':
        registrations = child.get('registrations', [])
        matching = [row for row in registrations if row.get('action_number') == report.get('right') and row.get('qualifier') == 0]
        if len(matching) != 1 or report.get('bottom') != -1:
            return {**unresolved, 'reason': 'action registration does not match the observed report form'}
    elif child.get('kind') == 'transition':
        if report.get('bottom') != child.get('number') or report.get('right') != -1:
            return {**unresolved, 'reason': 'transition number does not match the observed report form'}
    else:
        return {**unresolved, 'reason': 'unsupported SFC child role'}
    try:
        document = parse_sfc_pou(selected[0]['raw'])
        stored, = [row for row in document.layout['children'] if (row['kind'], row['name']) == (child['kind'], child['name'])]
        region = child.get('token_region', {})
        if region != dict(offset=stored['token_offset'], bytes=stored['token_size']):
            return {**unresolved, 'reason': 'current child source region differs'}
        listing = decode_token_program(parse_token_fragment(document.raw, stored['token_offset'], stored['token_size']), profile='fx3u')
    except (GXWFormatError, ValueError):
        return {**unresolved, 'reason': 'current child token position is unavailable'}
    records = [row for row in listing.records if isinstance(row, TokenInstruction) and row.step == report['step']]
    if len(records) != 1:
        return {**unresolved, 'reason': 'native step does not uniquely select a known current instruction boundary'}
    record = records[0]; raw = b''.join(token.raw for token in record.tokens)
    return {'status': 'source-resolved', 'public_check_promoted': False,
        'position': {'native_step': record.step, 'step_width': record.step_width,
                     'source_offset': record.tokens[0].offset, 'child_byte_offset': record.tokens[0].offset - stored['token_offset'],
                     'source_bytes': len(raw), 'raw_hex': raw.hex(), 'op': record.mnemonic,
                     'args': [operand.text for operand in record.operands]},
        'provenance': f'current original child getters and controlled instruction-prefix/width moves; {cpu}, native 1.635.0.1',
        'limits': ['child-local code step; report network matched to the paired SFC block metadata; graph coordinates are separate',
                   'known instruction boundary only; no position after an unknown width or within an instruction']}


def project_native_sfc_graph_diagnostics(raw, result, native_graph):
    """Correlate current checked child source with original native cell facts.

    Geometry never repairs/promotes the public location query or check status.
    Multiple occurrences of one action remain explicit candidates.
    """
    from copy import deepcopy
    from .models import GXWFormatError
    from .sfc_pou import read_sfc_project_context

    projected = deepcopy(result)
    if (result.get('current_source_check') != 'established' and result.get('referenced_sfc_check', {}).get('status') != 'current'
            or not isinstance(native_graph, dict)
            or result.get('native_version') != native_graph.get('native_version')):
        return projected
    try:
        context = read_sfc_project_context(raw, native_graph.get('logical_name'))
    except GXWFormatError:
        return projected
    grid = context.native_graph(native_graph)
    if grid is None or context.project['cpu'] != result.get('cpu'):
        return projected
    for diagnostic in projected.get('diagnostic_projection', {}).get('source_diagnostics', []):
        source = diagnostic.get('source_projection') or {}
        owner = source.get('source') or {}; child = owner.get('child') or {}
        if (source.get('status') != 'source-resolved' or source.get('current_source_body') != 'current'
                or owner.get('program_kind') != 240 or owner.get('logical_name') != context.program_name):
            continue
        matches = [row for row in context.source.layout['children']
                   if (row['kind'], row['name']) == (child.get('kind'), child.get('name'))]
        if len(matches) != 1:
            continue
        current = matches[0]
        if child.get('token_region') != {'offset': current['token_offset'], 'bytes': current['token_size']}:
            continue
        cells = []
        for cell in grid['cells']:
            binding = cell['binding']
            if binding.get('status') != 'source-resolved':
                continue
            if child.get('kind') == 'zoom' and cell['role'] == 'step':
                registrations = [row for row in binding['registrations'] if row['zoom'] == child['name']]
                if not registrations:
                    continue
                if not any(row.get('action_number') == binding['action_number'] and row.get('qualifier') == 0
                           for row in child.get('registrations', [])):
                    continue
            elif (child.get('kind') == 'transition' and cell['role'] == 'transition'
                    and binding.get('child') == child['name'] and binding.get('transition_number') == child.get('number')):
                pass
            else:
                continue
            cells.append({'id': cell['id'], 'x': cell['x'], 'y': cell['y'], 'role': cell['role'],
                          'binding': deepcopy(binding)})
        if len(cells) == 1:
            diagnostic['graph_projection'] = {'status': 'uniquely_correlated', 'public_check_promoted': False,
                'source': {'pou': owner.get('pou'), 'logical_name': context.program_name, 'child': deepcopy(child)},
                'cell': cells[0], 'provenance': 'current checked child source and source-bound original native cells',
                'limits': ['child-to-cell correspondence only; no repaired native location query, graph ports or PLC execution']}
        elif len(cells) > 1:
            diagnostic['graph_projection'] = {'status': 'unresolved', 'public_check_promoted': False,
                'reason': 'multiple_current_native_cells', 'candidates': cells,
                'provenance': 'current checked child source and source-bound original native cells'}
    return projected

"""Interpret original source, code, completion and diagnostic observations.

Compilation, publication, actual checker inputs, source locations and public
projection remain independent facts. None establishes PLC execution.
"""
from __future__ import annotations


def _native_validation_sources(raw):
    from .container_writer import validate_cfb_streams
    from .project_metadata import logical_mapping, read_project_text_context
    from .declarations import parse_declarations
    from .source_header import source_payload_offset
    from .models import GXWFormatError
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested = validate_cfb_streams(outer['_hdb'])
    metadata = [nested[key] for name, key in mapping.items() if name.endswith('.prj')]
    if len(metadata) != 1:
        raise GXWFormatError('native validation requires one project configuration')
    context = read_project_text_context(metadata[0])
    programs, names = [], set()
    for logical, key in mapping.items():
        if not logical.endswith('.Program.pou'):
            continue
        labels = logical.removesuffix('.Program.pou') + '.Labels.lh'
        if labels not in mapping:
            raise GXWFormatError('native validation source has no paired declarations: ' + logical)
        declarations = parse_declarations(nested[mapping[labels]], logical_name=labels)
        name = declarations.owner_name
        kind = nested[key][source_payload_offset(nested[key])]
        if declarations.scope != 'local' or not name or name in names or kind not in (193, 208):
            raise GXWFormatError('native validation source is outside the current structured body binding: ' + logical)
        names.add(name)
        programs.append({'name': name, 'program_kind': kind, 'logical_name': logical, 'raw': nested[key]})
    if not 1 <= len(programs) <= 64:
        raise GXWFormatError('native validation source selection is empty or exceeds the bound')
    return context, programs


def native_validation_source_plan(raw):
    """Bind an isolated structured-project validation to all local POU bodies."""
    import base64
    from .native_write import workspace_body
    context, programs = _native_validation_sources(raw)
    return {'cpu': context['cpu'], 'codepage': context['codepage'], 'programs': [
        {'name': row['name'], 'program_kind': row['program_kind'],
         'body': base64.b64encode(workspace_body(row['raw'])).decode('ascii')} for row in programs]}


def native_import_selection(raw, imported_hdb):
    """Compare mapped HDB streams, including global declarations/libraries.

    Imported caches are only input-preservation facts; their bytes never prove
    that compilation or checking used current code.
    """
    from .container_writer import validate_cfb_streams
    from .project_metadata import logical_mapping
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    before, actual = validate_cfb_streams(outer['_hdb']), validate_cfb_streams(imported_hdb)
    sources, outer_only = [], []
    for name, key in mapping.items():
        if key not in before and name in outer:
            # Legacy templates can register the outer Project.gd2 alongside
            # HDB IDs. It has no original HDB payload to compare.
            outer_only.append({'logical_name': name, 'stream': key, 'location': 'outer GXW, outside imported HDB'})
            continue
        sources.append({'logical_name': name, 'stream': key,
                        'status': 'current' if key in before and key in actual and actual[key] == before[key] else 'stale_or_missing'})
    return {'status': 'current' if sources and all(row['status'] == 'current' for row in sources) else 'unresolved',
            'streams': sources, 'outer_only_streams': outer_only,
            'provenance': 'input GXW versus original imported workspace HDB; exact bytes'}


def _id(value):
    return tuple(value) if isinstance(value, list) and len(value) == 12 else None


def bind_native_check_input(submitted, completed, *, generated, snapshots,
                            native_version, checker_version):
    """Compare the original task's retained primary code to this compilation.

    ECCompiler 15.50 uses its original 0x39f30 helper to find the code body.
    Compare both owned snapshots and their native framing; publication, task
    completion, diagnostics and source positions remain separate evidence.
    """
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    if native_version != '1.635.0.1' or checker_version != '15.50':
        return {**unresolved, 'reason': 'unobserved native task owner or checker version'}
    if not isinstance(submitted, dict) or not isinstance(completed, dict):
        return {**unresolved, 'reason': 'original submission and completion copies are required'}
    target = _id(submitted.get('target'))
    if (target is None or not any(target)
            or any(type(word) is not int or not 0 <= word <= 0xffffffff for word in target)
            or target != _id(completed.get('target'))
            or submitted.get('stage') != 'submitted' or completed.get('stage') != 'completed'
            or submitted.get('manager') != completed.get('manager') or not submitted.get('manager')
            or type(submitted.get('mask')) is not int or not 0 < submitted['mask'] <= 0x7fffffff
            or submitted.get('mask') != completed.get('mask')):
        return {**unresolved, 'reason': 'native task identities or stages differ'}
    stages = []
    for event in (submitted, completed):
        rows = event.get('rows')
        if (event.get('operation') != 'OwnedCheckInput'
                or event.get('provenance') != 'original ProcessManager.ProgramCheck owned code copy; read-only; no interception'
                or event.get('compiler_member_offset') != 12 or event.get('manager_records_offset') != 32
                or not isinstance(rows, list) or not 1 <= len(rows) <= 1024):
            return {**unresolved, 'reason': 'original owned input table is not bound'}
        stage = {}
        for row in rows:
            name, framing = row.get('resource'), row.get('framing') or {}
            full = snapshots.get(row.get('file'))
            if (not isinstance(name, str) or not name or name in stage or row.get('tag') != -1
                    or not isinstance(full, bytes) or len(full) != row.get('bytes') or len(full) < 4
                    or framing.get('reader_module') != 'ECCompiler_IEC.dll'
                    or framing.get('reader_version') != '15.50' or framing.get('reader_rva') != 0x39f30
                    or framing.get('code') != 0
                    or framing.get('provenance') != 'original checker envelope helper; optional tables omitted; read-only'):
                return {**unresolved, 'reason': 'saved original input or envelope reader is not bound'}
            first = int.from_bytes(full[:2], 'little')
            mode_length_offset = full[2] + 2
            if first > len(full) - 2 or mode_length_offset >= len(full):
                return {**unresolved, 'reason': 'native code envelope is truncated'}
            second = int.from_bytes(full[first:first + 2], 'little')
            offset, mode_offset = first + second, full[2] + full[mode_length_offset] + 4
            if (offset > len(full) or mode_offset >= len(full)
                    or framing.get('first_length') != first or framing.get('second_length') != second
                    or framing.get('body_offset') != offset or framing.get('body_bytes') != len(full) - offset
                    or framing.get('mode') != full[mode_offset]):
                return {**unresolved, 'reason': 'original envelope result differs from its buffer'}
            stage[name] = (full, full[offset:])
        stages.append(stage)
    if set(stages[0]) != set(stages[1]) or set(stages[0]) != set(generated):
        return {**unresolved, 'reason': 'task resources do not match the selected generated resources'}
    if any(not isinstance(value, bytes) or not value for value in generated.values()):
        return {**unresolved, 'reason': 'current generated primary code is missing or empty'}
    stable = all(stages[0][name][0] == stages[1][name][0] for name in generated)
    rows = [{'resource': name, 'body_bytes': len(stages[1][name][1]),
             'status': 'current' if all(stage[name][1] == generated[name] for stage in stages) else 'stale'}
            for name in generated]
    return {**unresolved, 'status': 'unresolved' if not stable else
            'current' if all(row['status'] == 'current' for row in rows) else 'stale',
            'target': list(target), 'mask': submitted.get('mask'),
            'task_copy_stable': stable, 'resources': rows,
            'coverage': 'primary code consumed by original ECCompiler; other channels are separate',
            **({'reason': 'task code copy changed during the check'} if not stable else {})}


def bind_native_source_snapshot(program_raw, *, body_id, reads, snapshots):
    """Bind source offsets to the last original getter read of the same body.

    Equal compiled code or matching graph coordinates cannot establish source
    offsets. A semantically equivalent text edit can move later records. The
    caller supplies independently saved getter buffers, not stored match flags.
    Source framing remains owned by the existing Core workspace-body reader.
    """
    missing = {'status': 'unresolved'}
    identity = _id(body_id)
    if (identity is None or not any(identity)
            or any(type(word) is not int or not 0 <= word <= 0xffffffff for word in identity)):
        return {**missing, 'reason': 'verified native source body identity is missing'}
    matching = [row for row in reads if row.get('operation') == 'NativeBodyRead'
                and _id(row.get('body')) == identity]
    if not matching:
        return {**missing, 'reason': 'original native source body read is missing'}
    latest = matching[-1]
    filename, size = latest.get('file'), latest.get('bytes')
    if (latest.get('provenance') != 'original Workspace.GetPOUBodyData(1692); source bytes, not compiled PCode'
            or not isinstance(filename, str) or type(size) is not int or size < 0):
        return {**missing, 'reason': 'original native source body read is not bound'}
    current = snapshots.get(filename)
    if not isinstance(current, bytes) or len(current) != size:
        return {**missing, 'reason': 'saved original native body buffer is missing or differs in size'}
    from .models import GXWFormatError
    from .native_write import workspace_body
    try:
        expected = workspace_body(program_raw)
    except GXWFormatError:
        return {**missing, 'reason': 'source snapshot framing is outside the observed workspace body'}
    return {'status': 'current' if expected == current else 'stale',
            'body_id': list(identity), 'native_snapshot': filename,
            'source_body_bytes': len(expected), 'native_body_bytes': size,
            'provenance': 'source bytes versus last original getter read of this body'}


def project_native_source_identity(source, *, pou, program_kind, project_id, native_version):
    """Verify original project-local body, POU parent and language reads."""
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}

    def native_id(value):
        result = _id(value)
        return result if result is not None and any(result) and all(
            type(word) is int and 0 <= word <= 0xffffffff for word in result) else None

    def accepted(value):
        return isinstance(value, dict) and value.get('hresult') == 0 and value.get('code') == 0

    project = native_id(project_id)
    if native_version != '1.635.0.1' or project is None:
        return {**unresolved, 'reason': 'unobserved native version or missing project identity'}
    source = source if isinstance(source, dict) else {}
    kind = program_kind
    if (not isinstance(pou, str) or not pou
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
            'source': {'project_id': list(project), 'pou': pou,
                       'body_id': list(body_id), 'pou_id': list(owner_id), 'program_kind': kind}}


def project_native_source_diagnostic(query, diagnostic, *, project_id, native_version):
    """Bind an original code diagnostic to its independently queried POU."""
    unresolved = {'status': 'unresolved', 'public_check_promoted': False}
    def text(value):
        return value.get('text') if isinstance(value, dict) else None
    if (diagnostic.get('kind') not in (2, 3) or type(diagnostic.get('code')) is not int
            or query.get('hresult') != 0 or query.get('code') != 0
            or text(diagnostic.get('library')) != ''
            or text(diagnostic.get('name')) != query.get('resource')
            or type(diagnostic.get('step')) is not int or diagnostic['step'] < 0
            or diagnostic['step'] != query.get('code_step')):
        return {**unresolved, 'reason': 'original diagnostic and native resource query do not match'}
    location = query.get('location') or {}
    if location.get('library') != '':
        return {**unresolved, 'reason': 'no verified project-local source identity'}
    identity = project_native_source_identity(query.get('source_object'), pou=location.get('pou'),
        program_kind=location.get('program_kind'), project_id=project_id, native_version=native_version)
    if identity['status'] != 'source-resolved':
        return identity
    return {**identity, 'original_diagnostic': diagnostic,
            'resource': query['resource'], 'diagnostic_step': query['code_step'],
            'native_position': {key: location.get(key) for key in
                                ('network', 'start_step', 'step_count', 'element_id',
                                 'action_transition_present')},
            'node_port_resolution': 'not-established',
            'provenance': 'original native location and workspace identity calls'}


def bind_native_source_chain(programs, required_sources, *, project_id, native_version,
                             selections, reads, snapshots):
    """Require current original bodies for every callee and caller in a chain.

    Equal compiled code, names or coordinates cannot bind source offsets. Use
    the last selection and body read for each POU; never fill a missing caller
    with another source that happens to have the same text or graph.
    """
    if (not required_sources or any(not isinstance(name, str) or not name for name in required_sources)
            or len(required_sources) > 64):
        return {'status': 'unresolved', 'reason': 'native source chain is missing or outside the bound',
                'sources': []}
    sources = []
    for pou in dict.fromkeys(required_sources):
        matching = [row for row in selections if row.get('operation') == 'NativeBodySelection'
                    and row.get('name') == pou]
        selected = matching[-1] if matching else {}
        identity = project_native_source_identity(selected.get('source_object'), pou=pou,
            program_kind=selected.get('program_kind'), project_id=project_id, native_version=native_version)
        if identity['status'] != 'source-resolved':
            sources.append({'pou': pou, 'status': 'unresolved', 'reason': identity['reason']})
            continue
        raw = programs.get(pou)
        if not isinstance(raw, bytes):
            sources.append({'pou': pou, 'status': 'unresolved', 'reason': 'selected source buffer is missing'})
            continue
        binding = bind_native_source_snapshot(raw, body_id=identity['source']['body_id'],
            reads=reads, snapshots=snapshots)
        sources.append({'pou': pou, **binding})
    statuses = {row['status'] for row in sources}
    return {'status': 'current' if statuses == {'current'} else
            'unresolved' if 'unresolved' in statuses else 'stale', 'sources': sources}


def project_native_validation(raw, observation, snapshots):
    """Interpret a standalone collector without trusting its match/status flags.

    Snapshots are independent bytes read by the transport before its isolated
    directory is removed. Missing copies retain incomplete or unresolved facts.
    """
    from .models import GXWFormatError
    context, sources = _native_validation_sources(raw)
    if (not isinstance(observation, dict) or observation.get('protocol_version') != 1
            or type(observation.get('protocol_version')) is not int
            or observation.get('native_version') != '1.635.0.1'
            or observation.get('cpu') != context['cpu']
            or observation.get('status') not in ('observed', 'failed')):
        raise GXWFormatError('native validation observation has an unbound protocol, version or CPU')
    validation = observation.get('validation') or {}
    events = validation.get('events', [])
    if not isinstance(events, list) or any(not isinstance(row, dict) for row in events):
        raise GXWFormatError('native validation event list is invalid')
    project = validation.get('project')
    programs = {row['name']: row['raw'] for row in sources}
    source_bodies = bind_native_source_chain(programs, list(programs), project_id=project,
        native_version=observation['native_version'], selections=events, reads=events, snapshots=snapshots)
    imported = {'status': 'unresolved', 'streams': [], 'reason': 'original imported HDB is missing'}
    if isinstance(snapshots.get('imported-hdb.bin'), bytes):
        try:
            imported = native_import_selection(raw, snapshots['imported-hdb.bin'])
        except GXWFormatError as error:
            imported['reason'] = str(error)

    def facts(row, kind):
        identity = _id(row.get('id'))
        return (identity is not None and any(identity)
            and all(type(word) is int and 0 <= word <= 0xffffffff for word in identity)
            and row.get('read_type') == {'hresult': 0, 'code': 0, 'data_type': kind}
            and row.get('read_name', {}).get('hresult') == row.get('read_name', {}).get('code') == 0
            and isinstance(row.get('read_name', {}).get('name'), str) and bool(row['read_name']['name']))

    tasks, selected, resources = [], [], []
    task_binding = True
    for row in events:
        if row.get('operation') != 'NativeTaskSelection':
            continue
        resource = row.get('resource') or {}
        task_binding &= facts(resource, 8) and row.get('provenance') == (
            'original Workspace collection, child, type, name and parent reads before compilation')
        resources.append(_id(resource.get('id')))
        for task in row.get('tasks', []):
            task_facts = task.get('task') or {}
            task_binding &= facts(task_facts, 10)
            entries = task.get('programs', [])
            names = [entry.get('read_name', {}).get('name') for entry in entries]
            task_binding &= all(facts(entry, 12) and name in programs for entry, name in zip(entries, names))
            tasks.append({'resource': resource.get('read_name', {}).get('name'),
                          'task': task_facts.get('read_name', {}).get('name'), 'programs': names})
            selected.extend(name for name in names if isinstance(name, str))
    task_binding &= bool(resources) and len(resources) == len(set(resources))
    selected = list(dict.fromkeys(selected))
    configuration_current = bool(imported['streams']) and all(row['status'] == 'current'
        for row in imported['streams'] if row['logical_name'].endswith(('.prj', '.tsk', '.lnl')))
    source_current = imported['status'] == 'current' and source_bodies['status'] == 'current'
    selection = {'status': 'verified' if task_binding and selected and source_current else 'unresolved',
        'source_bytes_current': source_current, 'configuration_bytes_current': configuration_current,
        'selected_sources': selected, 'empty_task': bool(resources) and not selected, 'tasks': tasks,
        'imported_workspace': imported}

    def channels(rows, size_key, file_key):
        if (not isinstance(rows, list) or len(rows) != 3
                or [row.get('channel') for row in rows] != [0, 1, 2]):
            return None
        data = tuple(snapshots.get(row.get(file_key)) for row in rows)
        return data if all(isinstance(value, bytes) and len(value) == row.get(size_key)
                           for row, value in zip(rows, data)) else None

    generated_names, generated, published = {}, {}, {}
    resource_rows = [row for row in events if row.get('operation') == 'Resource']
    for row in resource_rows:
        name = row.get('name')
        if (isinstance(name, str) and name and sum(other.get('name') == name for other in resource_rows) == 1):
            buffers = channels(row.get('channels'), 'bytes', 'file')
            if buffers is not None:
                generated_names[name] = buffers
    publication_rows = [row for row in events if row.get('operation') == 'PublishedResourceCodeCorrespondence']
    for row in publication_rows:
        target, resource = _id(row.get('target')), row.get('resource') or {}
        if (target is None or not facts(resource, 8) or _id(resource.get('id')) != target
                or sum(_id(other.get('target')) == target for other in publication_rows) != 1):
            continue
        name = resource['read_name']['name']
        buffers = channels(row.get('channels'), 'published_size', 'published_snapshot')
        if buffers is not None:
            published[target] = buffers
        if name in generated_names:
            generated[target] = generated_names[name]
    result = analyze_native_check(events, [], generated, selection=selection,
        published=published, owned_snapshots=snapshots)
    raw_reports, target = [], None
    for row in events:
        if row.get('operation') == 'ProgramCheckReportContext':
            target = row.get('target')
        elif row.get('operation') == 'ProgramCheckRawReports':
            raw_reports.extend((target, row.get('poll'), index, report)
                for index, report in enumerate(row.get('reports', [])) if report.get('kind') in (2, 3))
    diagnostics = []
    locations = [row for row in events if row.get('operation') == 'NativeDiagnosticLocation']
    for target, poll, index, report in raw_reports:
        matches = [row for row in locations if row.get('original') == {
            'poll': poll, 'report_index': index, 'kind': report.get('kind'), 'code': report.get('code'),
            'resource': report.get('name', {}).get('text'), 'step': report.get('step')}
            and row.get('target_resource') == report.get('name', {}).get('text')]
        projection = {'status': 'unresolved', 'reason': 'original diagnostic location is missing or ambiguous',
                      'public_check_promoted': False}
        if len(matches) == 1:
            projection = project_native_source_diagnostic(matches[0].get('source_location') or {}, report,
                project_id=project, native_version=observation['native_version'])
            if projection['status'] == 'source-resolved':
                current = next((body for body in source_bodies['sources']
                    if body['pou'] == projection['source']['pou']), {})
                projection['current_source_body'] = current.get('status', 'unresolved')
                if current.get('status') != 'current':
                    projection.update(status='unresolved', reason='original source body is not current')
        diagnostics.append({'target': target, 'report_origin': {'target': target, 'poll': poll, 'report_index': index},
                            'original': report, 'source_projection': projection})
    builds = [index for index, row in enumerate(events) if row.get('operation') == 'Compiler.Build'
              and row.get('hresult') == row.get('code') == 0]
    compile_ends = [index for index, row in enumerate(events) if row.get('operation') == 'Progress'
                    and row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0]
    check_starts = [index for index, row in enumerate(events) if row.get('operation') == 'Compiler.ProgramCheck']
    check_ends = [index for index, row in enumerate(events) if row.get('operation') == 'ProgramCheckRawProgress'
                  and row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0]
    boundaries = bool(len(builds) == 1 and compile_ends and check_starts and check_ends
                      and builds[0] < max(compile_ends) < min(check_starts) <= max(check_ends))
    ordered_reads = boundaries and all(
        any(_id(read.get('body')) == _id(body.get('body_id')) and read.get('operation') == 'NativeBodyRead'
            and read.get('stage') == stage and (
                index < builds[0] if stage == 'before-build' else
                max(compile_ends) < index < min(check_starts) if stage == 'after-build' else
                index > max(check_ends)) for index, read in enumerate(events))
        for body in source_bodies['sources'] for stage in ('before-build', 'after-build', 'after-check'))
    phase_reads_complete = source_bodies['status'] == 'current' and ordered_reads and all(
        bind_native_source_snapshot(programs[body['pou']], body_id=body['body_id'],
            reads=[read for read in events if read.get('stage') == stage], snapshots=snapshots)['status'] == 'current'
        for body in source_bodies['sources'] for stage in ('before-build', 'after-build', 'after-check'))
    current_check = (selection['status'] == 'verified' and configuration_current and phase_reads_complete
        and result['compilation']['acceptance'] == 'accepted'
        and result['published_resources']['correspondence'] == 'current'
        and result['owned_check_inputs']['correspondence'] == 'current'
        and result['raw_backend_check']['completed'])
    result.update(native_version=observation['native_version'], cpu=context['cpu'], codepage=context['codepage'],
        adapter={'status': observation['status'], 'message': observation.get('message') or ''},
        source_bodies={**source_bodies, 'phase_reads_complete': phase_reads_complete},
        raw_diagnostics=diagnostics,
        native_source_locations={'status': 'completed' if result['raw_backend_check']['completed']
            and all(row['source_projection']['status'] == 'source-resolved' for row in diagnostics)
            else 'not_requested' if result['raw_backend_check']['status'] == 'not_observed' else 'partial_or_unresolved',
            'node_port_resolution': 'not-established', 'public_check_promoted': False},
        current_raw_source_check='established' if current_check else 'not_established',
        generated_resources=[{'name': name, 'channel_sizes': [len(value) for value in data]}
                             for name, data in generated_names.items()])
    return result


def analyze_native_check(events, observations, generated, *, selection=None, reopened=None, source_audits=None,
                  published=None, owned_snapshots=None):
    """generated maps native resource IDs to three exact GetPCode buffers."""
    trace = [row.get('payload', row) for row in observations if row.get('type') != 'error']
    observation_errors = [row for row in observations if row.get('type') == 'error']
    observation_errors += [row for row in trace if row.get('event') == 'observation-error']
    planned, collection = [], None
    collecting, explicit_targets = False, None
    for row in events:
        if row.get('operation') == 'Workspace.GetProgramCheckCollection':
            collecting = True
        elif collecting and row.get('operation') == 'InventoryCount':
            collection = _id(row.get('id'))
        elif collecting and row.get('operation') == 'NativeObject' and _id(row.get('parent')) == collection:
            planned.append(_id(row['id']))
        elif row.get('operation') == 'ProgramCheckTarget':
            collecting = False
        elif row.get('operation') == 'ProgramCheckTargetOrder':
            explicit_targets = [_id(target) for target in row.get('targets', [])]
    started = [_id(row['id']) for row in events if row.get('operation') == 'ProgramCheckTarget']
    planned = list(dict.fromkeys(explicit_targets if explicit_targets is not None else planned or started))
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
    compile_completed = any(row.get('operation') == 'Progress' and row.get('percent') == 100
        and row.get('hresult', 0) == row.get('code', 0) == 0 for row in events)
    compile_diagnostics = [report for row in events if row.get('operation') in ('Progress', 'CompileRawReports')
        for report in row.get('reports', []) if report.get('kind') in (2, 3)]
    compile_failures = [row for row in events if row.get('operation') in ('Compiler.Build', 'Progress.GetProgress', 'Compiler.BuildProgress')
        and (row.get('hresult', 0) < 0 or row.get('code', 0) != 0)]
    compile_started = any(row.get('operation') == 'Compiler.Build'
        and row.get('hresult') == row.get('code') == 0 for row in events)
    conversion_failure = any(row.get('operation') == 'CompileRawReports'
        and any(report.get('kind') == 1 and report.get('code') == 0x20 for report in row.get('reports', [])) for row in events)
    compile_polls = [row for row in events if row.get('operation') == 'Progress' and 'poll' in row]
    compile_reports_complete = all(len(matching := [report for report in events
        if report.get('operation') == 'CompileRawReports' and report.get('poll') == poll.get('poll')]) == 1
        and matching[0].get('percent') == poll.get('percent')
        and len(matching[0].get('reports', [])) == poll.get('count') for poll in compile_polls)
    compile_acceptance = ('rejected' if compile_failures or conversion_failure or any(row['kind'] == 2 for row in compile_diagnostics)
        else 'accepted' if compile_started and compile_completed and compile_reports_complete else 'not_established')
    published_reads = []
    for index, row in enumerate(events):
        if row.get('operation') != 'PublishedResourceCodeCorrespondence':
            continue
        identity = _id(row.get('target'))
        expected, actual = generated.get(identity), (published or {}).get(identity)
        facts = row.get('resource', {})
        successful_read = any(event.get('operation') == 'PublishedResourceCodeRead'
            and _id(event.get('target')) == identity and event.get('hresult') == event.get('code') == 0
            and event.get('name') == facts.get('read_name', {}).get('name')
            and actual is not None and event.get('sizes') == [len(value) for value in actual]
            for event in events[:index])
        bound = (identity is not None and _id(facts.get('id')) == identity
            and facts.get('read_type') == {'hresult': 0, 'code': 0, 'data_type': 8}
            and facts.get('read_name', {}).get('hresult') == facts.get('read_name', {}).get('code') == 0
            and bool(facts.get('read_name', {}).get('name')) and successful_read)
        if not bound or actual is None or expected is None or len(actual) != 3 or len(expected) != 3:
            status = 'not_established'
        elif tuple(actual) != tuple(expected):
            status = 'stale'
        else:
            status = 'current' if any(actual) else 'empty'
        published_reads.append({'resource': list(identity) if identity else None,
            'name': facts.get('read_name', {}).get('name'), 'status': status,
            'channel_sizes': [len(value) for value in actual] if actual is not None else []})
    published_correspondence = ('not_observed' if not published_reads else
        'stale' if any(row['status'] == 'stale' for row in published_reads) else
        'not_established' if any(row['status'] != 'current' for row in published_reads) else
        'partial_targets' if not planned or {tuple(row['resource']) for row in published_reads} != set(planned) else 'current')
    skipped_targets = [row for row in events if row.get('operation') == 'ProgramCheckTargetSkipped']
    raw_sessions, raw_session, pending_target, check_started = [], None, None, False
    native_names = {}
    for row in events:
        operation = row.get('operation')
        if operation == 'NativeObject' and row.get('name_hresult') == row.get('name_code') == 0:
            native_names[_id(row.get('id'))] = row.get('name')
        elif operation == 'ProgramCheckTarget':
            pending_target, check_started = _id(row.get('id')), False
        elif operation == 'Compiler.ProgramCheck':
            check_started = row.get('hresult') == row.get('code') == 0
        elif operation == 'ProgramCheckReportContext':
            raw_session = None
            if row.get('path') == 'owned-native-backend':
                identity = _id(row.get('target'))
                raw_session = {'target': identity, 'name': native_names.get(identity),
                    'started': identity is not None and identity == pending_target and check_started,
                    'polls': {}, 'report_polls': set(), 'reports': [], 'valid': True,
                    'poll_failed': False, 'reported_status': None, 'owned_inputs': []}
                raw_sessions.append(raw_session)
        elif raw_session is not None:
            if operation == 'OwnedCheckInput':
                raw_session['owned_inputs'].append(row)
            elif operation == 'ProgramCheckRawProgress':
                raw_session['polls'][row.get('poll')] = row
                raw_session['valid'] &= row.get('hresult') == row.get('code') == 0
                raw_session['poll_failed'] |= row.get('hresult') != 0 or row.get('code') != 0
            elif operation == 'ProgramCheckRawReports':
                poll = raw_session['polls'].get(row.get('poll'), {})
                reports = row.get('reports', [])
                raw_session['valid'] &= (bool(poll) and poll.get('count') == len(reports)
                    and poll.get('percent') == row.get('percent'))
                raw_session['report_polls'].add(row.get('poll'))
                raw_session['reports'].extend(reports)
            elif operation == 'ProgramCheckTargetOutcome' and 'backend_check' in row:
                raw_session['reported_status'] = row['backend_check']
                raw_session = None
    raw_outcomes = []
    for session in raw_sessions:
        terminal = any(row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0
            for row in session['polls'].values())
        markers = [row for row in session['reports'] if row.get('kind') == 1 and row.get('code') == 0x23
            and session['name'] and row.get('name', {}).get('text') == session['name']]
        end_marker = len(markers) == 1
        expected_counts = None
        if end_marker:
            arguments = [argument.get('text') for argument in markers[0].get('arguments', [])]
            if (len(arguments) == 2 and all(isinstance(value, str) and value.isascii() and value.isdigit()
                    and len(value) <= 10 for value in arguments)):
                expected_counts = [int(value) for value in arguments]
        observed_counts = [sum(row.get('kind') == kind and row.get('name', {}).get('text') == session['name']
            for row in session['reports']) for kind in (2, 3)]
        diagnostics_complete = expected_counts is not None and expected_counts == observed_counts
        completed = (session['started'] and session['valid'] and terminal and end_marker and diagnostics_complete
            and session['reported_status'] in ('completed-accepted', 'completed-rejected')
            and all(poll in session['report_polls'] or row.get('count') == 0
                for poll, row in session['polls'].items()))
        status = ('completed-rejected' if any(row.get('kind') == 2 for row in session['reports'])
            else 'completed-accepted') if completed else 'failed' if session['poll_failed'] else 'incomplete'
        raw_outcomes.append({'target': list(session['target']) if session['target'] else None,
            'status': status, 'reported_status': session['reported_status'],
            'terminal_progress': terminal, 'resource_end_marker': end_marker,
            'diagnostics_complete': diagnostics_complete, 'reported_diagnostic_counts': expected_counts,
            'observed_diagnostic_counts': observed_counts,
            'completed_resource': session['name'] if end_marker else None})
    raw_completed = (bool(planned) and len(raw_outcomes) == len(planned) and not skipped_targets
        and {tuple(row['target']) if row['target'] else None for row in raw_outcomes} == set(planned)
        and all(row['status'] in ('completed-accepted', 'completed-rejected') for row in raw_outcomes)
        and bool(drained_completion) and drained_completion[-1].get('targets') == len(planned))
    raw_status = ('completed-rejected' if any(row['status'] == 'completed-rejected' for row in raw_outcomes)
        else 'completed-accepted') if raw_completed else 'incomplete' if raw_sessions or skipped_targets else 'not_observed'
    owned_inputs = []
    versions = {(row.get('name'), row.get('version')) for row in events if row.get('operation') == 'OwnedBackendModule'}
    required = {'DZDataABS_CompilerAdapter.dll', 'DZDataABS_Compiler_IEC.dll', 'DZDataABS_SICConverter_IEC.dll'}
    owner_version = '1.635.0.1' if all((name, '1.635.0.1') in versions
        and all(version == '1.635.0.1' for module, version in versions if module == name) for name in required) else None
    ec_version = '15.50' if {version for name, version in versions if name == 'ECCompiler_IEC.dll'} == {'15.50'} else None
    for session in raw_sessions:
        copies = session['owned_inputs']
        if not copies:
            continue
        submitted = [row for row in copies if row.get('stage') == 'submitted']
        completed = [row for row in copies if row.get('stage') == 'completed']
        code = generated.get(session['target'])
        proof = bind_native_check_input(submitted[0] if len(submitted) == 1 else None,
            completed[0] if len(completed) == 1 else None,
            generated={session['name']: code[0]} if session['name'] and code is not None else {},
            snapshots=owned_snapshots or {}, native_version=owner_version, checker_version=ec_version)
        if not session['started'] or any(_id(row.get('target')) != session['target'] for row in copies):
            proof = {'status': 'unresolved', 'reason': 'owned input is not bound to this original check start',
                     'public_check_promoted': False}
        owned_inputs.append({'target': list(session['target']) if session['target'] else None, **proof})
    owned_correspondence = ('not_observed' if not owned_inputs else
        'unresolved' if any(row['status'] == 'unresolved' for row in owned_inputs) else
        'stale' if any(row['status'] == 'stale' for row in owned_inputs) else
        'partial_targets' if {tuple(row['target']) for row in owned_inputs} != set(planned) else 'current')
    selected = selection or {'status': 'not_observed'}
    selection_current = (selected.get('status') == 'verified' and selected.get('source_bytes_current') is True
        and selected.get('configuration_bytes_current') is True and bool(selected.get('selected_sources'))
        and not selected.get('empty_task'))
    current_source_check = ('established' if selection_current and compile_acceptance == 'accepted'
        and correspondence == 'current' and whole_completed else 'not_established')
    return {
        'selection': selected,
        'compilation': {'completed': compile_completed, 'acceptance': compile_acceptance,
            'diagnostics_complete': compile_reports_complete if compile_polls else None,
            'diagnostics': compile_diagnostics, 'failures': compile_failures,
            'returned_resources': sum(row.get('operation') == 'Resource' for row in events), 'bound_resources': len(generated)},
        'publication': {'completed': any(row.get('operation') == 'Workspace.UpdatePCodeBeforeProgramCheck'
            and row.get('hresult') == row.get('code') == 0 for row in events)},
        'checker_resources': {'correspondence': correspondence, 'reads': reads, 'size_queries': queries,
                              'observation_errors': observation_errors},
        'owned_check_inputs': {'correspondence': owned_correspondence, 'targets': owned_inputs,
            'provenance': 'original retained task code and original envelope reader; no interception'},
        'published_resources': {'correspondence': published_correspondence, 'reads': published_reads,
            'provenance': 'original workspace read-back before checking; not an observed checker input'},
        'raw_backend_check': {'status': raw_status, 'completed': raw_completed,
            'targets': raw_outcomes, 'skipped_targets': skipped_targets,
            'public_adapter_projection': 'not_called' if raw_outcomes else 'not_observed'},
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

"""Bind native validation evidence in independently reviewable phases."""
from __future__ import annotations

from collections import Counter, defaultdict

from gxw.diagnostic_scope import (
    NATIVE_SOURCE_CAPACITY,
    NATIVE_COMPILATION_OPERATIONS,
    PUBLIC_FBD_COMPILE_POSITION_CPUS,
    PUBLIC_DECLARATION_COMPILE_POSITION_CPUS,
    PUBLIC_LIBRARY_ST_COMPILE_POSITION_CPUS,
    PUBLIC_GLOBAL_DECLARATION_COMPILE_POSITION_CPUS,
    PUBLIC_LIBRARY_STRUCTURE_COMPILE_POSITION_CPUS,
    SFC_CHILD_INSTRUCTION_POSITION_CPUS,
    _id,
    PUBLIC_CHECK_REPORT_CONVERSION_CPUS,
)
from gxw.diagnostic_sources import (
    _native_validation_sources,
    _native_validation_source_body,
    _native_compilation_method,
    _native_validation_sfc_children,
    sfc_child_source_scope,
    _native_validation_library_sources,
    native_validation_source_plan,
    _native_assignment_sources,
    native_inspection_queries,
    bind_native_ladder_candidate,
    native_import_selection,
)
from gxw.diagnostic_inspection import (
    project_native_inspection,
)
from gxw.diagnostic_bindings import (
    bind_native_code_lexical_read,
    bind_native_check_input,
    bind_native_source_snapshot,
    project_native_source_identity,
    project_native_source_diagnostic,
    _debug_st_interval,
    bind_native_instance_interval,
    _public_source_audit_binding,
    compare_public_source_diagnostic,
)
from gxw.diagnostic_positions import (
    _native_network_members,
    correlate_native_compile_position,
    correlate_st_diagnostic,
)
from gxw.diagnostic_sfc import (
    bind_native_sfc_child_snapshots,
    bind_native_source_chain,
    bind_native_sfc_referenced_check,
    project_native_sfc_child_diagnostic,
    project_native_sfc_instruction_position,
    project_native_sfc_graph_diagnostics,
)
from gxw.diagnostic_declarations import (
    _native_declaration_facts,
    _native_declaration_parent,
    _native_validation_declaration_owners,
    bind_native_compile_declaration_owners,
    correlate_native_compile_declaration_owner,
    bind_native_compile_declarations,
    correlate_native_compile_declaration,
    bind_native_compile_source_context,
    bind_native_source_references,
    _native_task_source_bindings,
)
from gxw.diagnostic_reports import (
    _public_report_text,
    _validate_public_report_layout,
    _public_diagnostic_identity,
    _project_public_diagnostic_conversion,
)
from gxw.diagnostic_checks import (
    analyze_native_check,
)
from gxw.diagnostic_outputs import (
    correlate_fbd_instance_output,
    correlate_fbd_bool_output,
)


from .models import GXWFormatError
from .diagnostic_events import EventIndex
from .diagnostic_checks import bind_native_check_selection

def _resource_facts(row, kind):
    identity = _id(row.get('id'))
    return (identity is not None and any(identity)
        and all(type(word) is int and 0 <= word <= 0xffffffff for word in identity)
        and row.get('read_type') == {'hresult': 0, 'code': 0, 'data_type': kind}
        and row.get('read_name', {}).get('hresult') == row.get('read_name', {}).get('code') == 0
        and isinstance(row.get('read_name', {}).get('name'), str) and bool(row['read_name']['name']))


def _diagnostic_graphs(
    diagnostics,
    diagnostic_locations,
    current_check,
    references,
    sources,
    context,
    observation,
    referenced_check,
    texts,
    graphs,
    graph_contexts,
    graph_models,
    all_programs,
    project,
    events,
    snapshots,
    lexical_by_resource,
    graph_connectivity,
    programs,
):
    for diagnostic, matching in zip(diagnostics, diagnostic_locations):
        projection = diagnostic['source_projection']
        if 'child' in projection.get('source', {}) and projection.get('current_source_body') == 'current':
            projection['instruction_position'] = project_native_sfc_instruction_position(diagnostic['original'], projection,
                sources, cpu=context['cpu'], native_version=observation['native_version'],
                current_check=current_check or referenced_check.get('status') == 'current')
        graph = {'status': 'unresolved', 'public_check_promoted': False,
                 'reason': 'current original code, references and source chain are required'}
        if not current_check and 'instance_projection' in projection:
            projection['instance_projection'].update(status='unresolved', reason='actual check code is not bound to this compilation')
        if current_check and references is not None and projection.get('status') == 'source-resolved':
            location = projection['source']
            if location['program_kind'] == 193:
                if len(matching) == 1:
                    query = matching[0]['source_location']
                    query = {**query, 'query': {'resource': projection['resource'], 'start_step': projection['diagnostic_step'],
                             'original_kind': diagnostic['original']['kind'], 'original_code': diagnostic['original']['code']}}
                    candidates = [correlate_st_diagnostic(query, [], references, texts, nodes, caller_pou=pou,
                        native_ranges=query.get('instance_ranges'),
                        caller_context=graph_contexts[pou],
                        caller_block_count=len(graph_models[pou].get('blocks', [None])))
                        for pou, nodes in graphs.items()]
                    resolved = [row for row in candidates if row['status'] == 'uniquely_correlated']
                    if len(resolved) == 1:
                        graph = resolved[0]
                        callee = (location['library'], location['pou']) if location.get('library') else location['pou']
                        needed = [callee, graph['caller']['pou']]
                        needed.extend(row['native_reference']['source'] for row in graph.get('call_chain', []))
                        chain = bind_native_source_chain(all_programs, needed, project_id=project,
                            native_version=observation['native_version'], selections=events, reads=events, snapshots=snapshots)
                        graph['source_chain'] = chain
                        if chain['status'] != 'current':
                            graph.update(status='unresolved', reason='an original caller or callee body is not current')
                    else:
                        graph['reason'] = 'original ST instance does not select one current FBD caller'
            elif location.get('library'):
                graph['reason'] = 'library source and instance are bound; graph and port correspondence is not established'
            elif location['program_kind'] == 208:
                instance = projection.get('instance_projection') or {}
                lexical = lexical_by_resource.get(projection['resource'])
                pou = location['pou']
                if (lexical is not None and len(matching) == 1 and pou in graph_models
                        and instance.get('status') == 'instance-resolved'):
                    interval = instance['compiled_interval']
                    records = [row for row in lexical['records']
                               if interval['step_start'] <= row['native_step'] < interval['step_start'] + interval['step_count']]
                    if context['cpu'] in ('FXU/FX2C', 'FX3G'):
                        graph = correlate_fbd_instance_output(matching[0]['source_location'], diagnostic['original'],
                            records, references, graph_contexts[pou], graph_contexts,
                            instance=instance, connectivity=graph_connectivity[pou])
                    else:
                        graph = correlate_fbd_bool_output(matching[0]['source_location'], diagnostic['original'],
                            records, references, graph_models[pou], selected_pou=pou,
                            connectivity=graph_connectivity[pou], instance=(instance['task'], instance['instance']))
                    needed = list(dict.fromkeys([pou, *([graph['caller']['pou']] if 'caller' in graph else [])]))
                    graph['source_chain'] = bind_native_source_chain(programs, needed, project_id=project,
                        native_version=observation['native_version'], selections=events, reads=events, snapshots=snapshots)
                    if graph['source_chain']['status'] != 'current':
                        graph.update(status='unresolved', reason='original FBD source body is not current')
                else:
                    graph['reason'] = 'current native lexical code, instance interval and FBD context are required'
        diagnostic['graph_projection'] = graph
    return diagnostics


def _compile_diagnostics(
    events,
    compile_context,
    declaration_bindings,
    declaration_owners,
    context,
    source_map,
    texts,
    graph_models,
    graph_connectivity,
):
    compile_diagnostics = []
    for row in events.rows('CompileRawReports', 'CompilePublicReports'):
        if row.get('operation') not in ('CompileRawReports', 'CompilePublicReports'):
            continue
        for index, report in enumerate(row.get('reports', [])):
            if report.get('kind') not in (2, 3):
                continue
            pou = report.get('name', {}).get('text')
            projection = {'status': 'unresolved', 'public_check_promoted': False,
                          'reason': 'current compile-phase source context is unavailable'}
            if compile_context['status'] == 'current':
                tables = [value for value in declaration_bindings.values() if value['status'] == 'current'
                          and value['pou'] == pou and _id(value.get('table_id')) == _id(report.get('source_object_id'))]
                if report.get('instance_kind') in (1, 2):
                    owners = [value for value in declaration_owners.values() if value['status'] == 'current'
                              and value['name'] == pou and _id(value.get('owner_id')) == _id(report.get('source_object_id'))]
                    projection = correlate_native_compile_declaration_owner(report, cpu=context['cpu'],
                        binding=owners[0] if len(owners) == 1 else {})
                elif report.get('instance_kind') == 5:
                    projection = correlate_native_compile_declaration(report, cpu=context['cpu'],
                        binding=tables[0] if len(tables) == 1 else {})
                else:
                    public = report.get('report_interface') == 'public-compiler'
                    bodies = [value for value in compile_context['stages'][-1]['sources'] if value['pou'] == pou
                              and (_id(value['body_id']) == _id(report.get('source_object_id')) if public else not value.get('library'))]
                    body = bodies[0] if len(bodies) == 1 else {}
                    library = body.get('library', '')
                    source = source_map.get((library, pou), {}) if body else {}
                    if library and declaration_bindings.get((library, pou), {}).get('status') != 'current':
                        projection['reason'] = 'current library declaration context is unavailable'
                    else:
                        projection = correlate_native_compile_position(report, cpu=context['cpu'], pou=pou,
                            program_kind=source.get('program_kind'), text=texts.get((library, pou) if library else pou),
                            model=graph_models.get(pou), connectivity=graph_connectivity.get(pou),
                            source_body_id=body.get('body_id'), logical_name=source.get('logical_name'), library=library)
                        if projection['status'] == 'source-resolved':
                            projection['source']['body_id'] = body['body_id']
            graph = projection.pop('graph_projection', {'status': 'not_applicable', 'public_check_promoted': False})
            compile_diagnostics.append({'report_origin': {'phase': 'compile', 'poll': row.get('poll'), 'report_index': index},
                'original': report, 'source_projection': projection, 'graph_projection': graph})
    return compile_diagnostics


def _source_views(raw, sources, library_sources, current_check, references, compile_context):
    texts, graphs, graph_models, graph_connectivity, graph_contexts = {}, {}, {}, {}, {}
    if (current_check and references is not None) or compile_context['status'] == 'current':
        from .text_pou import parse_st_pou
        from .object_model import read_project_context
        from .connectivity import build_connectivity_graph
        for source in [*sources, *library_sources]:
            try:
                if source['program_kind'] == 193:
                    key = (source['library'], source['name']) if source.get('library') else source['name']
                    texts[key] = parse_st_pou(source['raw']).text
                elif source['program_kind'] == 208 and not source.get('library'):
                    graph_context = read_project_context(raw, source['logical_name'])
                    graph_contexts[source['name']] = graph_context
                    graph_models[source['name']] = graph_context.object_model()
                    graphs[source['name']] = graph_models[source['name']]['nodes']
                    graph_connectivity[source['name']] = build_connectivity_graph(graph_context.program)
            except GXWFormatError:
                continue
    return texts, graphs, graph_models, graph_connectivity, graph_contexts


def _source_phase_reads(events, all_programs, source_bodies, snapshots):
    events = EventIndex.of(events)
    builds = [index for index, row in events.numbered(*NATIVE_COMPILATION_OPERATIONS)
              if row.get('hresult') == row.get('code') == 0]
    compile_ends = [index for index, row in events.numbered('Progress')
                    if row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0]
    check_starts = [index for index, _ in events.numbered('Compiler.ProgramCheck')]
    check_ends = [index for index, row in events.numbered('ProgramCheckRawProgress', 'ProgramCheckOriginalProgress')
                  if row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0]
    boundaries = bool(len(builds) == 1 and compile_ends and check_starts and check_ends
        and events[builds[0]].get('operation') == _native_compilation_method(all_programs.values())
        and builds[0] < max(compile_ends) < min(check_starts) <= max(check_ends))
    if source_bodies['status'] != 'current' or not boundaries:
        return False
    build, compiled, checking, checked = builds[0], max(compile_ends), min(check_starts), max(check_ends)
    reads = {}
    for index, row in events.numbered('NativeBodyRead'):
        stage = row.get('stage')
        if stage in ('before-build', 'after-build', 'after-check'):
            reads.setdefault((stage, _id(row.get('body'))), []).append((index, row))
    child_reads = events.grouped('NativeSfcChildRead', key=lambda row: (row.get('stage'), _id(row.get('parent'))))
    for body in source_bodies['sources']:
        raw = all_programs[(body['library'], body['pou']) if body.get('library') else body['pou']]
        for stage in ('before-build', 'after-build', 'after-check'):
            matching = reads.get((stage, _id(body.get('body_id'))), [])
            ordered = any(index < build if stage == 'before-build'
                          else compiled < index < checking if stage == 'after-build'
                          else index > checked for index, _ in matching)
            if not ordered or bind_native_source_snapshot(raw, body_id=body['body_id'],
                    reads=[row for _, row in matching], snapshots=snapshots)['status'] != 'current':
                return False
            if 'sfc_children' in body and bind_native_sfc_child_snapshots(all_programs[body['pou']],
                    owner_id=body['pou_id'], stage=stage,
                    reads=[row for _, row in child_reads.get((stage, _id(body['pou_id'])), ())],
                    snapshots=snapshots)['status'] != 'current':
                return False
    return True


def _source_diagnostics(
    events,
    check_reports,
    project,
    observation,
    references,
    source_bodies,
    sources,
    referenced_check,
):
    diagnostics, source_audits, diagnostic_locations = [], [], []
    location_fields = ('poll', 'report_index', 'kind', 'code', 'resource', 'step')
    indexed_locations = events.grouped('NativeDiagnosticLocation', key=lambda row: (
        tuple(row['original'].get(field) for field in location_fields)
        if isinstance(row.get('original'), dict) else None))
    bodies_by_source = {}
    for body in source_bodies['sources']:
        bodies_by_source.setdefault((body.get('library', ''), body['pou']), body)
    for target, poll, index, report, public, report_index, check_context in check_reports:
        original = {
            'poll': poll, 'report_index': index, 'kind': report.get('kind'), 'code': report.get('code'),
            'resource': report.get('name', {}).get('text'), 'step': report.get('step')}
        key = tuple(original[field] for field in location_fields)
        matches = [row for i, row in indexed_locations.get(key, ()) if check_context is not None
            and report_index < i < check_context['end'] and row.get('original') == original
            and row.get('target_resource') == report.get('name', {}).get('text')]
        projection = {'status': 'unresolved', 'reason': 'original diagnostic location is missing or ambiguous',
                      'public_check_promoted': False}
        if len(matches) == 1:
            projection = project_native_source_diagnostic(matches[0].get('source_location') or {}, report,
                project_id=project, native_version=observation['native_version'], references=references)
            if projection['status'] == 'source-resolved':
                current = bodies_by_source.get((projection['source'].get('library', ''), projection['source']['pou']), {})
                projection['current_source_body'] = current.get('status', 'unresolved')
                if current.get('status') != 'current':
                    projection.update(status='unresolved', reason='original source body is not current')
        origin = {'target': target, 'poll': poll, 'report_index': index}
        if public:
            origin.update(report_interface='public-compiler', record_size=100)
            public_row = {**report, 'name': report.get('name', {}).get('text'),
                'instance': report.get('instance', {}).get('text'), 'object_id': report.get('source_object_id')}
            audit = compare_public_source_diagnostic(public_row,
                matches[0].get('source_location') or {} if len(matches) == 1 else {}, report,
                project_id=project, native_version=observation['native_version'])
            if projection['status'] != 'source-resolved':
                child = project_native_sfc_child_diagnostic(report, sources, source_bodies,
                    project_id=project, native_version=observation['native_version'], referenced_check=referenced_check)
                if child['status'] == 'source-resolved':
                    projection = {**child, 'native_location_query':
                        matches[0].get('source_location') if len(matches) == 1 else None}
                    audit = {'status': 'source-consistent', 'public_check_promoted': False,
                        'original_public_diagnostic': public_row, 'native_source_projection': projection,
                        'position_interpretation': 'not-established'}
            if projection['status'] != 'source-resolved':
                audit.update(status='unresolved', reason=projection.get('reason', 'current source identity is unresolved'))
            source_audits.append(audit)
        diagnostics.append({'target': target, 'report_origin': origin,
                            'original': report, 'source_projection': projection})
        diagnostic_locations.append(matches)
    return diagnostics, source_audits, diagnostic_locations


def _collect_check_reports(events):
    check_reports, target, report_path, check_context = [], None, None, None
    for event_index, row in events.numbered('ProgramCheckReportContext', 'ProgramCheckRawReports',
            'ProgramCheckOriginalReports', 'ProgramCheckTarget', 'ProgramCheckTargetOutcome'):
        if row.get('operation') == 'ProgramCheckReportContext':
            if check_context is not None:
                check_context['end'] = event_index
            target = row.get('target')
            report_path = row.get('path')
            check_context = {'end': len(events)}
        elif row.get('operation') == 'ProgramCheckRawReports':
            check_reports.extend((target, row.get('poll'), index, report, False, event_index, check_context)
                for index, report in enumerate(row.get('reports', [])) if report.get('kind') in (2, 3))
        elif row.get('operation') == 'ProgramCheckOriginalReports' and report_path == 'original-public-get-progress':
            check_reports.extend((target, row.get('poll'), index, report, True, event_index, check_context)
                for index, report in enumerate(row.get('reports', [])) if report.get('kind') in (2, 3))
        elif row.get('operation') in ('ProgramCheckTarget', 'ProgramCheckTargetOutcome') and check_context is not None:
            check_context['end'] = event_index
            check_context = None
            target, report_path = None, None
    return check_reports


def _code_buffers(events, snapshots):
    def channels(rows, size_key, file_key):
        if (not isinstance(rows, list) or len(rows) != 3
                or [row.get('channel') for row in rows] != [0, 1, 2]):
            return None
        data = tuple(snapshots.get(row.get(file_key)) for row in rows)
        return data if all(isinstance(value, bytes) and len(value) == row.get(size_key)
                           for row, value in zip(rows, data)) else None

    generated_names, generated, published = {}, {}, {}
    resource_rows = list(events.rows('Resource'))
    names = Counter(row.get('name') for row in resource_rows if isinstance(row.get('name'), str))
    for row in resource_rows:
        name = row.get('name')
        if (isinstance(name, str) and name and names[name] == 1):
            buffers = channels(row.get('channels'), 'bytes', 'file')
            if buffers is not None:
                generated_names[name] = buffers
    publication_rows = defaultdict(list)
    for row in events.rows('PublishedResourceCodeCorrespondence'):
        publication_rows[_id(row.get('target'))].append(row)
    for target, rows in publication_rows.items():
        resources = [row.get('resource') or {} for row in rows]
        if (target is None or any(not _resource_facts(resource, 8) or _id(resource.get('id')) != target for resource in resources)
                or len({resource['read_name']['name'] for resource in resources}) != 1):
            continue
        name = resources[0]['read_name']['name']
        buffers = [channels(row.get('channels'), 'published_size', 'published_snapshot') for row in rows]
        if any(value is None for value in buffers) or any(value != buffers[0] for value in buffers):
            continue
        published[target] = buffers[0]
        if name in generated_names:
            generated[target] = generated_names[name]
    return generated_names, generated, published


def _task_selection(events, programs, source_bodies, imported):
    tasks, selected, resources = [], [], []
    task_binding = True
    for row in events.rows('NativeTaskSelection'):
        resource = row.get('resource') or {}
        task_binding &= _resource_facts(resource, 8) and row.get('provenance') == (
            'original Workspace collection, child, type, name and parent reads before compilation')
        resources.append(_id(resource.get('id')))
        for task in row.get('tasks', []):
            task_facts = task.get('task') or {}
            task_binding &= _resource_facts(task_facts, 10)
            entries = task.get('programs', [])
            names = [entry.get('read_name', {}).get('name') for entry in entries]
            task_binding &= all(_resource_facts(entry, 12) for entry in entries)
            tasks.append({'resource': resource.get('read_name', {}).get('name'),
                          'task': task_facts.get('read_name', {}).get('name'), 'programs': names})
            selected.extend(name for name in names if isinstance(name, str))
    task_binding &= bool(resources) and len(resources) == len(set(resources))
    selected = list(dict.fromkeys(selected))
    configuration_current = bool(imported['streams']) and all(row['status'] == 'current'
        for row in imported['streams'] if row['logical_name'].endswith(('.prj', '.tsk', '.lnl')))
    source_current = imported['status'] == 'current' and source_bodies['status'] == 'current'
    local_tasks = all(name in programs for name in selected)
    selection = {'status': 'verified' if task_binding and selected and source_current and local_tasks else 'unresolved',
        'source_bytes_current': source_current, 'configuration_bytes_current': configuration_current,
        'selected_sources': selected, 'empty_task': bool(resources) and not selected, 'tasks': tasks,
        'imported_workspace': imported}
    return selection, tasks, task_binding, selected, configuration_current, source_current


def _project_native_validation(raw, observation, snapshots, *, event_index=None):
    """Interpret a standalone collector without trusting its match/status flags.

    Snapshots are independent bytes read by the transport before its isolated
    directory is removed. Missing copies retain incomplete or unresolved facts.
    """
    from .models import GXWFormatError
    context, sources = _native_validation_sources(raw)
    library_sources = _native_validation_library_sources(raw)
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
    events = EventIndex.of(events if event_index is None else event_index)
    project = validation.get('project')
    programs = {row['name']: row['raw'] for row in sources}
    all_programs = {**programs, **{(row['library'], row['name']): row['raw'] for row in library_sources}}
    source_bodies = bind_native_source_chain(all_programs, list(all_programs), project_id=project,
        native_version=observation['native_version'], selections=events, reads=events, snapshots=snapshots)
    imported = {'status': 'unresolved', 'streams': [], 'reason': 'original imported HDB is missing'}
    if isinstance(snapshots.get('imported-hdb.bin'), bytes):
        try:
            imported = native_import_selection(raw, snapshots['imported-hdb.bin'])
        except GXWFormatError as error:
            imported['reason'] = str(error)

    selection, tasks, task_binding, selected, configuration_current, source_current = _task_selection(
        events,
        programs,
        source_bodies,
        imported,
    )
    generated_names, generated, published = _code_buffers(events, snapshots)
    result = analyze_native_check(events, [], generated, selection=selection,
        published=published, owned_snapshots=snapshots)
    reference_binding = bind_native_source_references(events, compilation=result['compilation'])
    references = reference_binding['rows'] if reference_binding['status'] == 'current' else None
    task_sources = _native_task_source_bindings(tasks, programs, source_bodies, events, references,
        project_id=project, native_version=observation['native_version'])
    selected_status = ('verified' if task_binding and selected and source_current and task_sources
                       and all(row['status'] == 'source-resolved' for row in task_sources) else 'unresolved')
    selection['task_program_sources'] = task_sources
    if selected_status != selection['status']:
        selection['status'] = selected_status
        result = bind_native_check_selection(result, selection)
    compile_context = bind_native_compile_source_context(all_programs, project_id=project,
        native_version=observation['native_version'], events=events, snapshots=snapshots,
        selection=selection, compilation=result['compilation'])
    result['compilation']['source_context'] = compile_context
    declaration_bindings = bind_native_compile_declarations([*sources, *library_sources],
        events=events, source_context=compile_context)
    declaration_statuses = {row['status'] for row in declaration_bindings.values()}
    result['compilation']['declaration_context'] = {
        'status': ('current' if declaration_statuses == {'current'} else 'not_observed'
                   if declaration_statuses == {'not_observed'} else 'partial_or_unresolved'),
        'tables': [{key: value for key, value in row.items() if key != 'rows'}
                   for row in declaration_bindings.values()],
        'public_check_promoted': False}
    declaration_owners = bind_native_compile_declaration_owners(_native_validation_declaration_owners(raw),
        project_id=project, events=events, source_context=compile_context)
    owner_statuses = {row['status'] for row in declaration_owners.values()}
    result['compilation']['declaration_context']['global_and_structure'] = {
        'status': ('current' if owner_statuses == {'current'} else 'not_observed'
                   if owner_statuses <= {'not_observed'} else 'partial_or_unresolved'),
        'tables': [{key: value for key, value in row.items() if key != 'rows'} for row in declaration_owners.values()],
        'public_check_promoted': False}
    referenced_check = {}
    if any((body.get('sfc_children') or {}).get('missing') for body in source_bodies['sources']):
        referenced_check = bind_native_sfc_referenced_check(all_programs, project_id=project,
            native_version=observation['native_version'], events=events, snapshots=snapshots,
            imported=imported, result=result, configuration_current=configuration_current,
            task_binding=task_binding, selected=selected, cpu=context['cpu'])
        result['referenced_sfc_check'] = referenced_check
    check_reports = _collect_check_reports(events)
    diagnostics, source_audits, diagnostic_locations = _source_diagnostics(
        events,
        check_reports,
        project,
        observation,
        references,
        source_bodies,
        sources,
        referenced_check,
    )
    result['diagnostic_projection'].update(source_audits=source_audits,
        source_body_binding=_public_source_audit_binding(source_audits, result['diagnostic_projection']['public_diagnostics']))
    phase_reads_complete = _source_phase_reads(events, all_programs, source_bodies, snapshots)
    code_current = (selection['status'] == 'verified' and configuration_current and phase_reads_complete
        and result['compilation']['acceptance'] == 'accepted'
        and result['published_resources']['correspondence'] == 'current'
        and result['owned_check_inputs']['correspondence'] == 'current'
        and result['checker_resources']['correspondence'] in ('current', 'not_observed')
        and not result['checker_resources']['observation_errors'])
    current_check = code_current and (result['raw_backend_check']['completed'] or result['public_check']['completed'])
    result['current_source_check'] = 'established' if code_current and result['public_check']['completed'] else 'not_established'
    lexical_reads = bind_native_code_lexical_read(events, generated_names, snapshots,
                                                cpu=context['cpu'], codepage=context['codepage'])
    lexical_by_resource = {row['resource']: row for row in lexical_reads if row['status'] == 'current'}
    texts, graphs, graph_models, graph_connectivity, graph_contexts = _source_views(
        raw,
        sources,
        library_sources,
        current_check,
        references,
        compile_context,
    )
    source_map = {(source.get('library', ''), source['name']): source for source in [*sources, *library_sources]}
    compile_diagnostics = _compile_diagnostics(
        events,
        compile_context,
        declaration_bindings,
        declaration_owners,
        context,
        source_map,
        texts,
        graph_models,
        graph_connectivity,
    )
    result['compilation']['source_diagnostics'] = compile_diagnostics
    diagnostics = _diagnostic_graphs(
        diagnostics,
        diagnostic_locations,
        current_check,
        references,
        sources,
        context,
        observation,
        referenced_check,
        texts,
        graphs,
        graph_contexts,
        graph_models,
        all_programs,
        project,
        events,
        snapshots,
        lexical_by_resource,
        graph_connectivity,
        programs,
    )
    result['diagnostic_projection']['source_diagnostics'] = diagnostics
    result.update(native_version=observation['native_version'], cpu=context['cpu'], codepage=context['codepage'],
        adapter={'status': observation['status'], 'message': observation.get('message') or ''},
        source_bodies={**source_bodies, 'phase_reads_complete': phase_reads_complete},
        raw_diagnostics=[row for row in diagnostics if row['report_origin'].get('report_interface') != 'public-compiler'],
        source_references=reference_binding,
        native_code_lexical_reads=lexical_reads,
        native_instance_locations={'status': 'completed' if current_check and references is not None and all(
            row['source_projection'].get('instance_projection', {}).get('status') == 'instance-resolved' for row in diagnostics)
            else 'not_observed' if reference_binding['status'] == 'not_observed' else 'partial_or_unresolved',
            'public_check_promoted': False},
        native_source_locations={'status': 'completed' if (result['raw_backend_check']['completed'] or result['public_check']['completed'])
            and all(row['source_projection']['status'] == 'source-resolved' for row in diagnostics)
            else 'not_requested' if result['raw_backend_check']['status'] == 'not_observed' and not check_reports else 'partial_or_unresolved',
            'node_port_resolution': 'correlated' if any(row['source_projection'].get('source', {}).get('program_kind') == 208
                for row in diagnostics) and all(row['graph_projection']['status'] == 'uniquely_correlated'
                    for row in diagnostics if row['source_projection'].get('source', {}).get('program_kind') == 208)
                else 'partial_or_unresolved', 'public_check_promoted': False},
        current_raw_source_check='established' if code_current and result['raw_backend_check']['completed'] else 'not_established',
        generated_resources=[{'name': name, 'channel_sizes': [len(value) for value in data]}
                             for name, data in generated_names.items()])
    return result

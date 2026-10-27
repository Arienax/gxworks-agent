# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Declaration owners, source contexts and reference binding."""
from __future__ import annotations
from .project_metadata import project_stream_mapping
from collections import Counter, defaultdict
from .diagnostic_events import EventIndex
from gxworks2.workspace_observations import EvidenceOrigin, evidence_origin_bound
from gxworks2.workspace_observations import (native_code_reader_scope, check_input_kind,
    check_input_layout_bound, check_input_framing_bound, workspace_code_read_origin,
    source_reference_modules_bound, structured_input_reader_bound, public_report_converter_bound,
    source_named_check_diagnostics, check_input_versions, workspace_read_scope_verified)
from gxw.diagnostic_scope import (
    NATIVE_COMPILATION_OPERATIONS,
    PUBLIC_DECLARATION_COMPILE_POSITION_CPUS,
    PUBLIC_GLOBAL_DECLARATION_COMPILE_POSITION_CPUS,
    PUBLIC_LIBRARY_STRUCTURE_COMPILE_POSITION_CPUS,
    _id,
)
from gxw.diagnostic_sources import (
    _native_compilation_method,
)
from gxw.diagnostic_bindings import (
    project_native_source_identity,
)
from gxw.diagnostic_sfc import (
    bind_native_source_chain,
)


def _native_declaration_facts(value, kind, name=None):
    if not isinstance(value, dict):
        return False
    identity = _id(value.get('id'))
    return (identity is not None and any(identity)
        and all(type(word) is int and 0 <= word <= 0xffffffff for word in identity)
        and value.get('read_type') == {'hresult': 0, 'code': 0, 'data_type': kind}
        and value.get('read_name', {}).get('hresult') == value.get('read_name', {}).get('code') == 0
        and isinstance(value.get('read_name', {}).get('name'), str)
        and (name is None or value['read_name']['name'] == name))


def _native_declaration_parent(value, identity):
    return (isinstance(value, dict) and value.get('hresult') == value.get('code') == 0
            and _id(value.get('id')) == _id(identity))


def _native_validation_declaration_owners(raw):
    """Reuse current label/type documents and their native directory owners."""
    from .callable_sources import _user_library_owners, _user_library_structures
    from .container_writer import validate_cfb_streams
    from .integrity import read_project_hdb
    from .declarations import parse_declarations
    from .models import GXWFormatError
    from .project_metadata import current_rows, logical_mapping
    outer = validate_cfb_streams(raw)
    mapping = project_stream_mapping(outer)
    nested = read_project_hdb(outer).payloads
    streams = {name: nested[key] for name, key in mapping.items() if key in nested}
    sources = []
    for row in current_rows(outer['projectdatalist.xml'], 'DSPROJECTDATA', 'D_Projectdata')[0]:
        fields = {key: value.text.strip() for key, value in row.fields().items()}
        logical = fields.get('szName', '')
        if not logical.endswith('.gh') or fields.get('bScrapFlag', 'false').lower() in ('true', '1'):
            continue
        document = None
        try:
            if (any(fields.get(key) != value for key, value in
                    {'ucProductType': '1', 'ucFolderType': '5', 'ucFileType': '1', 'ucReserve': '0',
                     'uiFolderNo': '0', 'bStructuredStorage': 'true'}.items()) or logical not in streams):
                raise GXWFormatError('global table has no current native declaration owner')
            document = parse_declarations(streams[logical], logical_name=logical)
        except GXWFormatError:
            pass
        sources.append(dict(role='global', library='', name=logical.removesuffix('.gh'),
                            logical_name=logical, declarations=document))
    _, _, descriptors = _user_library_owners(outer['projectdatalist.xml'], streams)
    structures, _, _ = _user_library_structures(outer['projectdatalist.xml'], streams, descriptors)
    for (namespace, name), documents in structures.items():
        for document in documents:
            logical = document.logical_name if document is not None else name + '\\' + namespace + '.lns'
            owner, _, library = logical.rpartition('\\')
            sources.append(dict(role='library-structure', library=library.removesuffix('.lns'), name=owner,
                                logical_name=logical, declarations=document))
    return sources


def bind_native_compile_declaration_owners(sources, *, project_id, events, source_context):
    """Enrich the same compile context with optional global/type getter reads."""
    import struct
    from .declarations import _string
    facts, parent = _native_declaration_facts, _native_declaration_parent
    key = lambda row: (row['role'], row['library'], row['name'])
    events = EventIndex.of(events)
    builds = [i for i, _ in events.numbered(*NATIVE_COMPILATION_OPERATIONS)]
    ends = [i for i, row in events.numbered('CompileRawReports', 'CompilePublicReports') if row.get('percent') == 100]
    check_start = min((i for i, _ in events.numbered('Compiler.ProgramCheck')), default=len(events))
    def owner_key(row):
        owner = row.get('owner')
        name = owner.get('read_name') if isinstance(owner, dict) else None
        return row.get('role'), row.get('library'), name.get('name') if isinstance(name, dict) else None

    reads = events.grouped('NativeDeclarationOwnerRead', key=owner_key)
    source_counts = Counter(key(item) for item in sources)
    current = source_context.get('status') == 'current' and len(builds) == len(ends) == 1
    result = {}
    for source in sources:
        identity = key(source); global_ = source['role'] == 'global'
        binding = dict(role=source['role'], library=source['library'], name=source['name'],
                       logical_name=source['logical_name'], status='not_observed')
        result[identity] = binding
        selected = [(i, row) for i, row in reads.get(identity, ())
                    if row.get('stage') in ('before-build', 'after-build')]
        if not selected:
            continue
        binding.update(status='unresolved', reason='current declaration owner, fields or ordered reads differ')
        document = source['declarations']
        if (not current or len(selected) != 2 or document is None
                or source_counts[identity] != 1):
            continue
        declared = document.rows if global_ else document.members
        expected = {row.name: row for row in declared}
        if len(expected) != len(declared):
            continue
        stages = []
        for stage in ('before-build', 'after-build'):
            observed = [(i, row) for i, row in selected if row.get('stage') == stage]
            if len(observed) != 1:
                break
            index, row = observed[0]
            owner, collection = row.get('owner', {}), row.get('collection', {})
            if (not (index < builds[0] if stage == 'before-build' else ends[0] < index < check_start)
                    or row.get('status') != 'observed' or _id(row.get('project_id')) != _id(project_id)
                    or not facts(owner, 14 if global_ else 0x303, source['name'])
                    or not facts(collection, 13 if global_ else 0x302)
                    or not parent(row.get('owner_parent'), collection.get('id'))):
                break
            if global_:
                if not parent(row.get('collection_parent'), project_id):
                    break
            else:
                namespace, namespaces = row.get('namespace_object', {}), row.get('namespace_collection', {})
                if (not facts(namespace, 0x301, source['library']) or not facts(namespaces, 0x300)
                        or not parent(row.get('collection_parent'), namespace.get('id'))
                        or not parent(row.get('namespace_parent'), namespaces.get('id'))
                        or not parent(row.get('namespace_collection_parent'), project_id)):
                    break
            variables = row.get('rows')
            if not isinstance(variables, list) or len(variables) != len(expected):
                break
            rows = {}
            for item in variables:
                if not isinstance(item, dict) or item.get('name') not in expected or item['name'] in rows:
                    break
                wanted = expected[item['name']]
                if (not facts(item.get('identity'), 16 if global_ else 0x305, wanted.name)
                        or not parent(item.get('parent'), owner['id'])
                        or type(item.get('line')) is not int or item['line'] != wanted.record_id
                        or any(item.get(field) != getattr(wanted, field) for field in ('data_type', 'initial_value', 'comment'))):
                    break
                value = dict(name=wanted.name, record_id=wanted.record_id, data_type=wanted.data_type,
                             variable_id=item['identity']['id'])
                if global_:
                    pairs = item.get('devices')
                    if (type(item.get('class_code')) is not int or item['class_code'] != wanted.class_code
                            or type(item.get('struct_mode')) is not int or item['struct_mode'] not in (0, 1)
                            or item['struct_mode'] != wanted.unknown_u32
                            or any(item.get(field) != getattr(wanted, field) for field in ('device', 'iec_address'))
                            or not isinstance(pairs, list) or any(not isinstance(pair, list) or len(pair) != 2
                                or any(not isinstance(text, str) or '\0' in text for text in pair) for pair in pairs)):
                        break
                    extension = struct.pack('<I', len(pairs)) + b''.join(_string(text) for pair in pairs for text in pair) if item['struct_mode'] == 1 else b''
                    if wanted.value_extension != extension or (item['struct_mode'] == 0 and pairs):
                        break
                    value.update(class_code=wanted.class_code, device=wanted.device, iec_address=wanted.iec_address,
                                 struct_mode=item['struct_mode'], devices=pairs)
                rows[wanted.name] = value
            if len(rows) != len(expected) or len({_id(item['variable_id']) for item in rows.values()}) != len(rows):
                break
            stages.append(dict(owner_id=owner['id'], collection_id=collection['id'], rows=rows,
                               namespace_id=None if global_ else namespace['id']))
        if len(stages) == 2 and stages[0] == stages[1]:
            binding.pop('reason', None); binding.update(status='current', **stages[0])
    return result


def correlate_native_compile_declaration_owner(report, *, cpu, binding):
    """Resolve global rows and library members only in controlled CPU scopes."""
    unresolved = {'status': 'unresolved', 'public_check_promoted': False,
                  'reason': 'current native declaration owner binding is unavailable'}
    global_ = binding.get('role') == 'global'
    scope = PUBLIC_GLOBAL_DECLARATION_COMPILE_POSITION_CPUS if global_ else PUBLIC_LIBRARY_STRUCTURE_COMPILE_POSITION_CPUS
    if (cpu not in scope or binding.get('status') != 'current' or binding.get('role') not in ('global', 'library-structure')
            or report.get('report_interface') != 'public-compiler' or report.get('record_size') != 100
            or report.get('kind') not in (2, 3) or report.get('instance_kind') != (1 if global_ else 2)
            or report.get('program_kind') != 0 or _id(report.get('source_object_id')) != _id(binding.get('owner_id'))
            or report.get('name', {}).get('text') != binding.get('name') or report.get('instance', {}).get('text') != binding.get('name')
            or any(report.get(field) != -1 for field in ('step', 'network', 'right', 'bottom'))
            or report.get('left') not in ((4, 5) if global_ else (2,)) or type(report.get('top')) is not int):
        return unresolved
    matches = [row for row in binding['rows'].values() if row['record_id'] == report['top']]
    if len(matches) != 1:
        return unresolved
    return {'status': 'source-resolved', 'public_check_promoted': False,
        'source': dict(role=binding['role'], declaration_owner=binding['name'], library=binding['library'],
                       logical_name=binding['logical_name'], owner_id=binding['owner_id'], declaration=matches[0], native_column=report['left']),
        'source_identity_binding': 'original public report matches current native declaration owner',
        'instance_resolution': 'not_available_in_compile_report',
        'limits': ['declaration record number, not physical row index; original getters before and after Build',
                   'native column semantics and expanded caller instance are not established']}


def bind_native_compile_declarations(sources, *, events, source_context):
    """Bind original table/variable reads to current declarations around Build.

    Only the observed declaration fields are compared. This enriches source
    positions; missing observations never prevent source saving or compilation.
    """
    def key(row):
        return row.get('library', ''), row.get('pou', row.get('name'))

    facts, parent = _native_declaration_facts, _native_declaration_parent

    events = EventIndex.of(events)
    builds = [i for i, _ in events.numbered(*NATIVE_COMPILATION_OPERATIONS)]
    ends = [i for i, row in events.numbered('CompileRawReports', 'CompilePublicReports')
            if row.get('percent') == 100]
    check_start = min((i for i, _ in events.numbered('Compiler.ProgramCheck')), default=len(events))
    reads = events.grouped('NativeDeclarationRead', key=key)
    selections_by_source = events.grouped('NativeBodySelection', key=lambda row: (row.get('stage'), *key(row)))
    bodies_by_source = defaultdict(list)
    for group in source_context.get('stages', []):
        for body in group['sources']:
            bodies_by_source[group['stage'], *key(body)].append(body)
    current = source_context.get('status') == 'current' and len(builds) == len(ends) == 1
    result = {}
    for source in sources:
        identity = key(source)
        binding = dict(pou=source['name'], library=source.get('library', ''), status='not_observed',
                       logical_name=source['declaration_name'])
        selected = [(i, row) for i, row in reads.get(identity, ())
                    if row.get('stage') in ('before-build', 'after-build')]
        result[identity] = binding
        if not selected:
            continue
        binding.update(status='unresolved', reason='current declaration identities, fields or ordered reads differ')
        if not current or len(selected) != 2:
            continue
        stages = []
        library = bool(identity[0])
        expected = {row.name: row for row in source['declarations'].rows}
        for stage in ('before-build', 'after-build'):
            observed = [(i, row) for i, row in selected if row.get('stage') == stage]
            bodies = bodies_by_source.get((stage, *identity), ())
            selections = [row.get('source_object', {})
                          for _, row in selections_by_source.get((stage, *identity), ())]
            if len(observed) != 1 or len(bodies) != 1 or len(selections) != 1:
                break
            index, row = observed[0]
            if not (index < builds[0] if stage == 'before-build' else
                    ends[0] < index < check_start):
                break
            owner, table = row.get('owner', {}), row.get('table', {})
            children, variables = row.get('owner_children'), row.get('rows')
            if (row.get('status') != 'observed' or _id(row.get('body_id')) != _id(bodies[0]['body_id'])
                    or not facts(owner, 0x30b if library else 26, identity[1])
                    or _id(owner.get('id')) != _id(selections[0].get('owner', {}).get('id'))
                    or not parent(row.get('body_parent'), owner.get('id'))
                    or not facts(table, 0x30f if library else 28)
                    or not parent(row.get('table_parent'), owner.get('id'))
                    or not isinstance(children, list) or not all(isinstance(child, dict) for child in children)
                    or sum(child == table for child in children) != 1
                    or sum(facts(child, 0x30d if library else 32)
                           and _id(child.get('id')) == _id(bodies[0]['body_id']) for child in children) != 1
                    or not isinstance(variables, list) or len(variables) != len(expected)
                    or not all(isinstance(item, dict) for item in variables)):
                break
            rows = {}
            for item in variables:
                name = item.get('name')
                if not isinstance(name, str) or name not in expected or name in rows:
                    break
                wanted = expected[name]
                if (not facts(item.get('identity'), 0x311 if library else 30, name)
                        or not parent(item.get('parent'), table['id'])
                        or type(item.get('line')) is not int or item['line'] != wanted.record_id
                        or type(item.get('class_code')) is not int
                        or any(item.get(field) != getattr(wanted, field)
                               for field in ('data_type', 'class_code', 'initial_value', 'comment'))):
                    break
                rows[name] = dict(name=name, record_id=wanted.record_id, data_type=wanted.data_type,
                                  class_code=wanted.class_code, variable_id=item['identity']['id'])
            if len(rows) != len(expected) or len({_id(item['variable_id']) for item in rows.values()}) != len(rows):
                break
            stages.append(dict(table_id=table['id'], body_id=bodies[0]['body_id'], rows=rows))
        if len(stages) == 2 and stages[0] == stages[1]:
            binding.pop('reason', None)
            binding.update(status='current', **stages[0])
    return result


def correlate_native_compile_declaration(report, *, cpu, binding):
    """Resolve an observed declaration row; keep its column and instance opaque."""
    unresolved = {'status': 'unresolved', 'public_check_promoted': False,
                  'reason': 'current native declaration row binding is unavailable'}
    if (cpu not in PUBLIC_DECLARATION_COMPILE_POSITION_CPUS or binding.get('status') != 'current'
            or report.get('report_interface') != 'public-compiler' or report.get('record_size') != 100
            or report.get('kind') not in (2, 3) or report.get('instance_kind') != 5 or report.get('program_kind') != 0
            or _id(report.get('source_object_id')) != _id(binding.get('table_id'))
            or report.get('name', {}).get('text') != binding.get('pou')
            or report.get('instance', {}).get('text') != binding.get('pou')
            or any(report.get(field) != -1 for field in ('step', 'network', 'right', 'bottom'))
            or report.get('left') != 2 or type(report.get('top')) is not int):
        return unresolved
    matches = [row for row in binding['rows'].values() if row['record_id'] == report['top']]
    if len(matches) != 1:
        return unresolved
    return {'status': 'source-resolved', 'public_check_promoted': False,
        'source': {'pou': binding['pou'], 'library': binding['library'],
                   'logical_name': binding['logical_name'], 'table_id': binding['table_id'],
                   'declaration': matches[0], 'native_column': report['left']},
        'source_identity_binding': 'original public report matches current native declaration table',
        'instance_resolution': 'not_available_in_compile_report',
        'limits': ['declaration record number, not physical row index; original getters before and after Build',
                   'native column semantics and expanded caller instance are not established']}


def bind_native_compile_source_context(programs, *, project_id, native_version, events, snapshots,
                                       selection, compilation, cpu=None):
    """Bind completed Build reports to original source reads around that Build.

    Rejected compilation can have current source positions even though it has
    no executable code or check task. All selected declarations/configuration
    must also match the independently imported workspace HDB.
    """
    unresolved = {'status': 'unresolved', 'public_check_promoted': False, 'stages': []}
    events = EventIndex.of(events)
    builds = [i for i, row in events.numbered(*NATIVE_COMPILATION_OPERATIONS)
              if row.get('hresult') == row.get('code') == 0]
    ends = [i for i, row in events.numbered('CompileRawReports', 'CompilePublicReports') if row.get('percent') == 100]
    check_start = min((i for i, _ in events.numbered('Compiler.ProgramCheck')), default=len(events))
    if (selection.get('status') != 'verified' or selection.get('configuration_bytes_current') is not True
            or compilation.get('completed') is not True or compilation.get('diagnostics_complete') is not True
            or compilation.get('acceptance') not in ('accepted', 'rejected')
            or len(builds) != 1 or len(ends) != 1 or not builds[0] < ends[0]
            or events[builds[0]].get('operation') != _native_compilation_method(programs.values(), cpu=cpu)
            or ends[0] >= check_start):
        return {**unresolved, 'reason': 'current source selection or complete original compilation is unavailable'}
    stages = []
    for stage in ('before-build', 'after-build'):
        selections = list(events.rows('NativeBodySelection', stage=stage))
        reads = list(events.numbered('NativeBodyRead', stage=stage))
        phase_reads = EventIndex.of(events.rows('NativeBodyRead', 'NativeSfcChildRead', stage=stage))
        binding = bind_native_source_chain(programs, list(programs), project_id=project_id, native_version=native_version,
            selections=selections, reads=phase_reads, snapshots=snapshots)
        stages.append({'stage': stage, **binding})
        selection_counts, read_counts = {}, {}
        if binding['status'] == 'current' and len(reads) == len(selections) == len(programs):
            selection_counts = Counter((row.get('library', ''), row.get('name')) for row in selections)
            read_counts = Counter(_id(row.get('body')) for _, row in reads)
        if (binding['status'] != 'current' or len(reads) != len(programs) or len(selections) != len(programs)
                or any(selection_counts[key if isinstance(key, tuple) else ('', key)] != 1 for key in programs)
                or any(read_counts[_id(source.get('body_id'))] != 1 for source in binding['sources'])
                or any(not (index < builds[0] if stage == 'before-build' else
                            ends[0] < index < check_start) for index, _ in reads)):
            return {**unresolved, 'stages': stages, 'reason': 'original compile-phase source reads are missing, stale or out of order'}
    before = {(row.get('library', ''), row['pou']): row['body_id'] for row in stages[0]['sources']}
    after = {(row.get('library', ''), row['pou']): row['body_id'] for row in stages[1]['sources']}
    if before != after:
        return {**unresolved, 'stages': stages, 'reason': 'native source body identities changed during compilation'}
    return {'status': 'current', 'public_check_promoted': False, 'stages': stages,
            'provenance': 'exact imported HDB and original source getter reads before and after completed Build'}


def bind_native_source_references(events, *, compilation):
    """Require the original complete analysis performed after this build."""
    unresolved = {'status': 'unresolved', 'rows': []}
    events = EventIndex.of(events)
    creates = list(events.numbered('Compiler.CreateProgramAnalysis3'))
    if not creates:
        return {**unresolved, 'status': 'not_observed', 'reason': 'original reference analysis was not observed'}
    queries = list(events.numbered('Compiler.GetProgramAnalysis3'))
    records = list(events.numbered('NativeSourceReferences'))
    polls = list(events.numbered('NativeReferenceProgress'))
    build_ends = [i for i, row in events.numbered('Progress') if row.get('percent') == 100]
    check_starts = [i for i, _ in events.numbered('Compiler.ProgramCheck')]
    if (compilation.get('acceptance') != 'accepted' or len(creates) != 1 or len(queries) != 1 or len(records) != 1
            or not polls or not build_ends or not check_starts
            or not source_reference_modules_bound(tuple(events.rows('OwnedBackendModule')))):
        return {**unresolved, 'reason': 'original reference analysis or current compilation is incomplete'}
    create_index, create = creates[0]; query_index, query = queries[0]; record_index, record = records[0]
    if (any(type(row.get(key)) is not int or row[key] != 0
            for row in (create, query, record) for key in ('hresult', 'code'))
            or record.get('query') != 'all-references' or record.get('declared') != 0 or record.get('plural') != 0
            or record.get('symbol') != '' or record.get('scope') != ''
            or not evidence_origin_bound(record, EvidenceOrigin.SOURCE_REFERENCES)
            or not isinstance(record.get('rows'), list) or type(record.get('count')) is not int
            or not 0 <= record['count'] <= 100000 or query.get('count') != record['count']
            or record['count'] != len(record['rows']) or any(not isinstance(row, dict) for row in record['rows'])
            or not max(build_ends) < create_index < min(i for i, _ in polls)
            or not max(i for i, _ in polls) < query_index <= record_index < min(check_starts)):
        return {**unresolved, 'reason': 'original reference query is unbound, partial or out of order'}
    reports_by_poll = events.grouped('NativeReferenceRawReports', key=lambda row: row.get('poll'))
    seen = set()
    for poll_index, poll in polls:
        if (any(type(poll.get(key)) is not int or poll[key] != 0 for key in ('hresult', 'code'))
                or type(poll.get('poll')) is not int or poll['poll'] <= 0 or poll['poll'] in seen
                or type(poll.get('percent')) is not int or not 0 <= poll['percent'] <= 100
                or type(poll.get('count')) is not int or not 0 <= poll['count'] <= 10000
                or len(matching := reports_by_poll.get(poll['poll'], ())) != 1
                or not poll_index < matching[0][0] < query_index
                or matching[0][1].get('percent') != poll['percent']
                or not isinstance(matching[0][1].get('reports'), list)
                or len(matching[0][1]['reports']) != poll['count']
                or any(not isinstance(report, dict) or report.get('kind') == 2
                       for report in matching[0][1]['reports'])):
            return {**unresolved, 'reason': 'original reference progress or reports are incomplete or rejected'}
        seen.add(poll['poll'])
    terminal = polls[-1][1]['percent'] == 100
    return {'status': 'current' if terminal else 'unresolved', 'rows': record['rows'] if terminal else [],
            'count': record['count'],
            **({'provenance': record['provenance']} if 'provenance' in record else {})}


def _native_task_source_bindings(tasks, local_programs, source_bodies, events, references, *,
                                 project_id, native_version):
    """Bind library task entries to original compiled root references and objects.

    A task entry carries a bare program name. The FX3G community-library
    control establishes the namespace through current root references, not
    through a same-named library candidate or an expanded FB instance.
    """
    events = EventIndex.of(events)
    selections_by_source = events.grouped('NativeBodySelection', key=lambda row: (
        row.get('stage'), row.get('name'), row.get('library'), row.get('program_kind')))
    roots_by_task = defaultdict(list)
    for reference in references or []:
        if reference.get('source') == reference.get('instance'):
            try:
                roots_by_task[reference.get('resource'), reference.get('task'), reference.get('source')].append(reference)
            except TypeError:
                # Invalid names cannot equal the string identities of a task.
                continue
    bodies_by_source = defaultdict(list)
    library_shadows = set()
    for body in source_bodies.get('sources', []):
        bodies_by_source[body.get('library', ''), body.get('pou')].append(body)
        if body.get('library'):
            library_shadows.add(body.get('pou'))
    bindings = []
    for task in tasks:
        for name in task['programs']:
            row = {'resource': task['resource'], 'task': task['task'], 'pou': name,
                   'status': 'unresolved', 'public_check_promoted': False}
            roots = roots_by_task.get((task['resource'], task['task'], name), ())
            def valid_root(reference):
                library, language = reference.get('library'), reference.get('program_kind')
                if not isinstance(library, str) or type(language) is not int:
                    return False
                if not library and name in local_programs:
                    from .source_header import source_payload_offset
                    source = local_programs[name]
                    return language == source[source_payload_offset(source)]
                return language in (193, 208)
            scopes = {(reference.get('library'), reference.get('program_kind')) for reference in roots
                      if valid_root(reference)}
            if len(scopes) > 1 or any(not valid_root(reference) for reference in roots):
                row['reason'] = 'compiled task root has conflicting source namespaces or languages'
                bindings.append(row)
                continue
            library, kind = next(iter(scopes)) if scopes else ('', None)
            bodies = bodies_by_source.get((library, name), ())
            if len(bodies) != 1 or bodies[0].get('status') != 'current':
                row['reason'] = 'task root source body is missing, ambiguous or not current'
                bindings.append(row)
                continue
            body = bodies[0]
            if not library:
                if not roots and name in library_shadows:
                    row['reason'] = 'same-named task sources require an original compiled root namespace'
                    bindings.append(row)
                    continue
                if name not in local_programs:
                    row['reason'] = 'task entry has no original compiled library root'
                    bindings.append(row)
                    continue
                row.update(status='source-resolved', library='', body_id=body['body_id'],
                           provenance='original local task entry and current local source body')
            else:
                selections = [event for _, event in selections_by_source.get(('before-build', name, library, kind), ())]
                identity = project_native_source_identity(
                    selections[0].get('source_object') if len(selections) == 1 else None,
                    pou=name, library=library, program_kind=kind, project_id=project_id,
                    native_version=native_version)
                source = identity.get('source', {})
                owner, selected_body = _id(source.get('pou_id')), _id(source.get('body_id'))
                if (identity['status'] != 'source-resolved' or owner is None or selected_body is None
                        or owner[6] != 0x1000000 or selected_body[6] != 0x1000000
                        or selected_body != _id(body.get('body_id'))):
                    row['reason'] = 'compiled library task root is not the current original Program object'
                else:
                    row.update(status='source-resolved', library=library, program_kind=kind,
                               body_id=list(selected_body), pou_id=list(owner),
                               original_root_reference=roots[0],
                               provenance='original compiled root reference and current namespaced Program object')
            bindings.append(row)
    return bindings

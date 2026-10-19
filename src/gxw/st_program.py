"""Current-project context and ordinary ST authoring over existing Core rules.

These functions read the supplied saved project. They never choose a template,
write files, open an IDE, compile, or turn stored addresses into current results.
The isolated workspace adapter owns the native save operation.
"""
from __future__ import annotations

import base64
from copy import deepcopy

from .fb_authoring import (_context, _global_snapshot,
                           st_program_authoring_plan, verify_st_program_readback)
from .declarations import CLASS_CODES
from .library_transfer import _row
from .models import GXWFormatError
from .native_write import _native_project_source_inventory, _native_scope_inventory, workspace_body
from .project_writer import ProjectWriteResult, prepare_st_project_write
from .source_header import source_payload_offset
from .text_pou import parse_st_pou


ST_PROGRAM_OPERATIONS = frozenset({'create_st_program', 'edit_st_program'})


def _context_declaration_rows(document, sources):
    from .native_write import _values
    classes, rows = {value: key for key, value in CLASS_CODES.items()}, []
    for row in sorted(document.rows, key=lambda row: (row.record_id, row.class_code)):
        item = _row(row)
        storage = {key: value for key, value in _values(row).items() if key not in item}
        storage['value_extension_base64'] = base64.b64encode(storage.pop('value_extension')).decode('ascii')
        rows.append(item | dict(class_name=classes.get(row.class_code, str(row.class_code)),
            kind=sources.declaration_kind(row), storage=storage,
            address_storage_status='unresolved' if row.unknown_u32 or row.value_extension else 'scalar'))
    return rows


def project_context_snapshot(raw, source_name=None):
    """Read globals, local declarations, Program/FB sources and current tasks.

    Selection accepts an exact logical source or one unambiguous current owner.
    Unsupported source languages stay explicit; an unavailable source is never
    replaced by a blank template or another saved version.
    """
    from .project_metadata import read_project_compile_options, read_project_system_variables
    config, streams, groups, sources, _ = _context(raw, require_fbd=False)
    metadata_name, = [name for name in streams if name.endswith('.prj')]
    options = read_project_compile_options(streams[metadata_name])
    configuration = dict(name=metadata_name,
        ranges={name: list(pair) for name, pair in read_project_system_variables(streams[metadata_name])['ranges'].items()},
        compile_options={'global_variable_hiding': options['global_variable_hiding']})
    parameter = streams.get('Param.wpa', b'')
    if (config['cpu'] == 'FX3U/FX3UC' and len(parameter) == 1100
            and parameter[:4] == bytes.fromhex('40002201')):
        configuration['program_capacity'] = parameter[704] * 1000
    resources, tasks = _native_scope_inventory(raw)
    pous, owners, issues = [], set(), deepcopy(sources.issues)
    for logical, group in groups.items():
        document = sources.declarations.get(group['labels'])
        if (logical not in streams or document is None or sources.source_groups.get(logical) != group
                or not document.owner_name or document.owner_name.casefold() in owners):
            raise GXWFormatError('current project context requires unique paired POU/declaration ownership')
        owners.add(document.owner_name.casefold())
        kind = {0x1000000: 'program', 0x1000002: 'function_block', 0x1000003: 'function'}.get(
            document.owner_pou_type, 'unknown')
        language_code = streams[logical][source_payload_offset(streams[logical])]
        language = {193: 'st', 208: 'fbd', 1: 'ladder', 240: 'sfc'}.get(language_code, 'unknown')
        entry = dict(name=document.owner_name, program=logical, labels=group['labels'],
            kind=kind, language=language, declarations=_context_declaration_rows(document, sources),
            task_bindings=[dict(resource=task['resource'], task=task['task'], line=row['line'])
                           for task in tasks for row in task['programs']
                           if row['name'].casefold() == document.owner_name.casefold()])
        if kind == 'program' and len(entry['task_bindings']) > 1:
            raise GXWFormatError('current ordinary Program has ambiguous task membership: ' + document.owner_name)
        if kind in ('function_block', 'function'):
            try:
                entry.update(interface_status='available', interface=sources.fixed_interface(
                    document.owner_name, 'FUNCTION' if kind == 'function' else 'FUNCTION_BLOCK'))
            except GXWFormatError as error:
                entry.update(interface_status='unavailable', interface_diagnostic=str(error))
                issues.append(dict(code='pou_interface_gap', source=logical, message=str(error)))
        if language == 'st':
            source = parse_st_pou(streams[logical])
            entry.update(source_status='available' if source.text is not None else 'unavailable',
                         st_text=source.text)
            if source.diagnostic:
                entry['source_diagnostic'] = source.diagnostic
        elif language == 'fbd':
            from .object_model import read_project_context
            try:
                entry.update(source_status='available', model=read_project_context(raw, logical).object_model())
            except GXWFormatError as error:
                entry.update(source_status='unavailable', source_diagnostic=str(error))
        else:
            entry.update(source_status='unavailable', source_diagnostic='source language is outside this context reader')
        pous.append(entry)
    if source_name is not None:
        if not isinstance(source_name, str):
            raise GXWFormatError('current source selection requires a logical POU or owner name')
        selected = [row for row in pous if row['program'] == source_name]
        if not selected:
            selected = [row for row in pous if row['name'].casefold() == source_name.casefold()]
        if len(selected) != 1:
            raise GXWFormatError('missing or ambiguous current project source: ' + source_name)
        selected_source = selected[0]['program']
    else:
        selected_source = None
    globals_ = []
    for logical, document in sources.declarations.items():
        if document.scope != 'global':
            continue
        declarations = _context_declaration_rows(document, sources)
        try:
            table = _global_snapshot(sources, logical)
        except GXWFormatError as error:
            # A readable declaration is not made unavailable merely because
            # its member-address storage is outside the editing contract.
            table = dict(name=logical.removesuffix('.gh'), kind='global_table', declarations=declarations,
                         member_addresses='unresolved', diagnostic=str(error))
            issues.append(dict(code='global_member_address_gap', source=logical, message=str(error)))
        else:
            table['declarations'] = [original | resolved for original, resolved in
                                     zip(declarations, table['declarations'], strict=True)]
        globals_.append(table | {'source': logical})
    structures = [dict(name=document.logical_name.removesuffix('.str'), source=document.logical_name,
        kind='structure', status='available' if len(documents) == 1 else 'ambiguous',
        members=[dict(name=row.name, data_type=row.data_type, initial_value=row.initial_value, comment=row.comment,
            storage={key: getattr(row, key) for key in ('unknown_text', 'record_id', 'array_marker',
                                                       'type_code', 'type_reference')})
                 for row in document.members])
        for documents in sources.project_structures.values() for document in documents]
    return dict(status='partial' if issues or any(row['source_status'] != 'available' for row in pous) else 'available',
        origin='supplied_saved_project', cpu=config['cpu'], codepage=config['codepage'],
        selected_source=selected_source, globals=globals_, pous=pous,
        structures=structures, configuration=configuration,
        # Preserve source binding for unknown option words and parameters.
        # Shared public projections omit this underscore-prefixed field.
        _configuration_storage=dict(compile_options=options, parameter_present='Param.wpa' in streams,
            parameter_source_base64=base64.b64encode(parameter).decode('ascii')),
        resources=[name[:-4] for name in resources], tasks=tasks, issues=issues,
        compilation='not_run', execution='not_run')


def background_st_program_plan(raw, command):
    """Compose ordinary ST semantics with the isolated native save inventory.

    Creation requires the explicit existing resource/task supported by the
    shared planner. Other scope modes are rejected, never changed implicitly.
    """
    plan, before, expected, caller = st_program_authoring_plan(raw, command)
    keys = {'cpu', 'codepage', 'operation', 'source_name', 'write_declarations', 'rows', 'st_text',
            'before_body', 'before_rows', 'tasks'}
    if before is None:
        keys |= {'resource', 'task', 'line'}
    source = {key: deepcopy(value) for key, value in plan.items() if key in keys}
    _, source['programs'] = _native_project_source_inventory(raw)
    if before is not None:
        streams = _context(raw, require_fbd=False)[1]
        prepared = prepare_st_project_write(raw, before['program'], parse_st_pou(streams[before['program']]),
                                           plan['st_text'])
        source['after_body'] = base64.b64encode(workspace_body(
            prepared.replacements[before['program']][2])).decode('ascii')
    return source, before, expected, caller


def verify_background_st_program_save(baseline, command, saved, observation):
    """Require native save evidence and the same source/task preservation rules."""
    plan, before, _, caller = background_st_program_plan(baseline, command)
    calls = observation.get('calls') if isinstance(observation, dict) else None
    required = {'OpenProjectEX2', 'SaveProject', 'ExportOneFileProject', 'Definition.SetSTBody',
                'Definition.GetPOUType', 'Definition.GetProgramLanguage',
                'Definition.GetOwner' if before is not None else 'Definition.Create'}
    if before is None:
        required |= {'Workspace.GetTaskEventName', 'Workspace.GetTaskInterval', 'Workspace.GetTaskPriority',
                     'Workspace.CreateTaskElement', 'Workspace.SetTaskElementName', 'Workspace.SetTaskElementLine'}
    if (not isinstance(observation, dict) or observation.get('status') != 'saved'
            or type(observation.get('protocol_version')) is not int or observation['protocol_version'] != 1
            or observation.get('native_version') != '1.635.0.1' or observation.get('cpu') != plan['cpu']
            or observation.get('compile') != 'not_requested' or observation.get('check') != 'not_requested'
            or not isinstance(calls, list) or any(not isinstance(row, dict)
                or not isinstance(row.get('operation'), str)
                or type(row.get('hresult')) is not int or row['hresult'] < 0
                or type(row.get('code')) is not int or row['code'] != 0 for row in calls)
            or not required <= {row.get('operation') for row in calls}):
        raise GXWFormatError('ordinary ST Program native save evidence is incomplete or belongs to another CPU/version')
    verified = verify_st_program_readback(baseline, command, saved)
    # The shared readback binds every POU and declaration plus the task append.
    # Keep type/library/global sources byte-exact, including storage that can
    # be read but is intentionally not accepted by the declaration editor.
    from .container_writer import validate_cfb_streams
    from .native_write import _saved_object_names
    from .project_metadata import logical_mapping

    def protected(data):
        metadata = validate_cfb_streams(data)['projectdatalist.xml']
        mapping = {name: key for name, key in logical_mapping(metadata).items()
                   if name.endswith(('.gh', '.lnl', '.lnb', '.lns', '.lng', '.lif', '.str'))}
        streams = _context(data, require_fbd=False)[1]
        if not mapping.keys() <= streams.keys():
            raise GXWFormatError('ordinary ST Program save is missing protected source bytes')
        return metadata, mapping, {name: streams[name] for name in mapping}

    old_metadata, old_mapping, old_sources = protected(baseline)
    new_metadata, new_mapping, new_sources = protected(saved)
    # Reuse the existing SDK library relocation roles, independently of POU
    # creation. The identity/role mapping still rejects missing or swapped
    # libraries, and every relocated payload must remain byte-for-byte exact.
    names = _saved_object_names(old_metadata, old_mapping, new_metadata, new_mapping, {}, {})
    if any(value != new_sources[names[name]] for name, value in old_sources.items()):
        raise GXWFormatError('ordinary ST Program save changed global, library or structure source bytes')
    from .project_metadata import (project_gui_configuration, read_project_compile_options,
                                   read_project_system_variables)
    def configuration(data):
        streams = _context(data, require_fbd=False)[1]
        project, = [value for name, value in streams.items() if name.endswith('.prj')]
        return dict(ranges=read_project_system_variables(project)['ranges'],
                    options=read_project_compile_options(project), gui=project_gui_configuration(data))
    if configuration(baseline) != configuration(saved):
        raise GXWFormatError('ordinary ST Program save changed unrelated current project configuration')
    return ProjectWriteResult(bytes(saved), dict(operation='native_st_program_authoring',
        definition_before=before, definition_after=verified['program'], caller=caller,
        library_relocations=[dict(before=name, after=current) for name, current in names.items() if name != current],
        native_workspace=deepcopy(observation), task_header_changes=verified['task_header_changes'],
        validation={**verified['validation'], 'native_save': 'passed', 'definition_readback': 'matched',
                    'gx_compile': 'not_run'}))

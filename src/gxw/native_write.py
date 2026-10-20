"""Source-bound plans and saved-source verification for the offline workspace.

PLC/declaration interpretation stays here. The native adapter selects vendor
profiles and call sequences; successful save/readback does not establish compilation.
"""
from __future__ import annotations

import base64
from dataclasses import fields
import re
import struct

from .declarations import BASIC_TYPES, LabelRecord, _type_parts, local_table_for, parse_declarations
from .models import GXWFormatError
from gxworks2.workspace_observations import task_configuration_values
from .project_metadata import (current_rows, logical_mapping, project_resource_groups, project_source_groups,
                               read_project_text_context, read_resource_source, read_task_source, verify_task_source_append)
from .project_writer import PreparedProjectWrite, ProjectWriteResult, binary_diff
from .container_writer import validate_cfb_streams
from .source_header import source_payload_offset


def verify_native_compilation_save(before, saved, validation, cold, observation):
    """Keep current source and compare saved code after an independent cold build."""
    from .native_diagnostics import native_validation_source_plan
    previous, current = native_validation_source_plan(before), native_validation_source_plan(saved)
    for plan in (previous, current):
        plan['programs'] = sorted(plan['programs'], key=lambda row: row['name'])
        plan['libraries'] = sorted(plan['libraries'], key=lambda row: (row['library'],row['name']))
    if previous != current:
        raise GXWFormatError('native compile save changed current source bodies or CPU')
    from .project_metadata import project_gui_configuration
    gui = project_gui_configuration(saved)
    # Earlier saved Workspace exports retained the generic T record without
    # the GUI's 100T field. They still bind current source and cold code below,
    # but do not establish the complete GUI allocation layer. Visible sessions
    # independently require that layer to be matched before native execution.
    legacy_timer_gap = (gui.get('status') == 'missing' and gui.get('missing') == ['T']
        and not gui.get('differences') and not gui.get('unverified_selectors')
        and gui.get('word_device') == 0 and gui.get('bit_device') == 3)
    if current['cpu'] == 'FX3U/FX3UC' and gui['status'] != 'matched' and not legacy_timer_gap:
        raise GXWFormatError('saved GUI allocation configuration differs from the current workspace')
    for result in (validation, cold):
        if (result.get('compilation',{}).get('acceptance') != 'accepted'
                or result.get('compilation',{}).get('completed') is not True
                or result.get('compilation',{}).get('diagnostics_complete') is not True
                or result.get('public_check') != {'status':'passed','completed':True}
                or result.get('current_source_check') != 'established'):
            raise GXWFormatError('native compile save requires current completed compilation and public check')
    calls = observation.get('calls',[])
    if (observation.get('native_version')!='1.635.0.1' or observation.get('cpu')!=previous['cpu']
            or observation.get('status')!='observed' or observation.get('protocol_version')!=1
            or not {'SaveProject','ExportOneFileProject'}.issubset(row.get('operation') for row in calls)):
        raise GXWFormatError('native compile save observation lacks the original save/export calls')
    code = cold.get('inspection',{}).get('saved_code',{})
    if code.get('status')!='matches' or not code.get('resources'):
        raise GXWFormatError('cold saved code differs from the current compiler output')
    for resource in code['resources']:
        channels = resource.get('channels',[])
        if (resource.get('status')!='matches' or [row.get('channel') for row in channels]!=[0,1,2]
                or any(row.get('matches') is not True or type(row.get('saved_bytes')) is not int
                    or row['saved_bytes']<0 or row['saved_bytes']!=row.get('generated_bytes') for row in channels)):
            raise GXWFormatError('cold saved code channels are incomplete or differ')
    return ProjectWriteResult(bytes(saved),dict(operation='native_compilation_save',native_workspace=observation,
        native_validation=validation,cold_validation=cold,validation=dict(native_save='passed',gx_compile='passed',
            source_bodies='matched',public_check='completed-passed',saved_code='matched',cold_reopen='passed',
            gui_configuration=gui['status'],execution='not_run')))


def workspace_body(raw: bytes) -> bytes:
    """The observed self-sized ST/FBD body, excluding the source envelope."""
    start = source_payload_offset(raw) + 5
    if start + 4 > len(raw):
        raise GXWFormatError('truncated native structured source body')
    size = struct.unpack_from('<I', raw, start)[0]
    if not 4 <= size <= 8 * 1024 * 1024 or start + size + 24 != len(raw):
        raise GXWFormatError('structured source is outside the observed native body envelope')
    return raw[start:start + size]


def _identity(row):
    return {'name': row.name, 'data_type': row.data_type, 'class_code': row.class_code}


def _values(row, *, omit=()):
    return {f.name: getattr(row, f.name) for f in fields(LabelRecord)
            if f.name not in {'offset', 'raw', *omit}}


def _project_fbd_fb(sources, prepared, name):
    """Require a current registered project FB source, beyond a cached name."""
    try:
        labels, _ = sources.definition(name, 'FUNCTION_BLOCK')
        programs = [program for program, group in sources.source_groups.items() if group['labels'] == labels]
        if len(programs) != 1:
            return False
        raw = prepared.nested[prepared.mapping[programs[0]]]
        return raw[source_payload_offset(raw)] == 208
    except (GXWFormatError, KeyError, IndexError):
        return False


def _created_inout_type(sources, data_type):
    """The source-bound scalar/rank-one shapes observed in original creation."""
    base, bounds = _type_parts(data_type)
    if len(bounds) > 1 or any(not -2**31 <= low <= high < 2**31 for low, high in bounds):
        return False
    if base in ('BOOL', 'WORD'):
        return not bounds or base == 'WORD'
    if base.upper() in BASIC_TYPES:
        return False
    try:
        document = sources.structure(base)
    except GXWFormatError:
        return False
    if bounds and base.casefold() not in sources.project_structures:
        return False
    for member in document.members:
        member_base, member_bounds = _type_parts(member.data_type)
        if (member_base not in BASIC_TYPES or len(member_bounds) > 1
                or member_bounds and (member_base not in ('BOOL', 'WORD')
                    or any(not -2**31 <= low <= high < 2**31 for low, high in member_bounds))):
            return False
    return bool(document.members)


def _created_root_type(sources, data_type):
    """Original FBD Program/FB local array and flat structure creation shapes."""
    base, bounds = _type_parts(data_type)
    if base in ('BOOL', 'WORD'):
        return len(bounds) == 1 and all(-2**31 <= low <= high < 2**31 for low, high in bounds)
    return base.upper() not in BASIC_TYPES and _created_inout_type(sources, data_type)


def _native_project_source_inventory(raw: bytes) -> tuple[dict, list[dict]]:
    """The current paired local source inventory shared by native edit plans."""
    from .native_diagnostics import _native_validation_sources
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested = validate_cfb_streams(outer['_hdb'])
    groups, issues = project_source_groups(outer['projectdatalist.xml'])
    if issues:
        raise GXWFormatError('native operation requires current unambiguous source ownership')
    project, programs = _native_validation_sources(raw)
    if any(row['logical_name'] not in groups for row in programs):
        raise GXWFormatError('native operation requires current paired source and declaration roles')
    return project, [
        {'name': row['name'], 'program_kind': row['program_kind'],
         'pou_type': parse_declarations(nested[mapping[groups[row['logical_name']]['labels']]],
                                       logical_name=groups[row['logical_name']]['labels']).owner_pou_type,
         'body': base64.b64encode(workspace_body(row['raw'])).decode('ascii')}
        for row in programs]


def native_source_creation_plan(raw: bytes, name: str, *, pou_kind='function_block') -> dict:
    """Create an empty FBD Program or FB through its original native constructor."""
    if not isinstance(pou_kind, str) or pou_kind not in ('program', 'function_block'):
        raise GXWFormatError('native FBD creation requires program or function_block POU kind')
    if not isinstance(name, str) or not re.fullmatch(r'[^\W\d]\w*', name, re.UNICODE):
        raise GXWFormatError('invalid native POU name')
    project, programs = _native_project_source_inventory(raw)
    if any(row['name'].casefold() == name.casefold() for row in programs):
        raise GXWFormatError('native POU name already exists')
    try:
        name.encode('cp' + str(project['codepage']))
    except (LookupError, UnicodeError) as error:
        raise GXWFormatError('native POU name cannot be represented by the project codepage') from error
    return {'program_name': name, 'cpu': project['cpu'], 'codepage': project['codepage'],
            'pou_type': 0x01000000 if pou_kind == 'program' else 0x01000002,
            'program_kind': 208, 'programs': programs}


def _native_scope_inventory(raw: bytes) -> tuple[dict, list[dict]]:
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested = validate_cfb_streams(outer['_hdb'])
    resources, issues = project_resource_groups(outer['projectdatalist.xml'])
    if issues:
        raise GXWFormatError('native scope operation requires current unambiguous resource/task ownership')
    if any(logical not in mapping or mapping[logical] not in nested
           for name, group in resources.items() for logical in (name, *group['tasks'])):
        raise GXWFormatError('native scope operation requires materialized current resource/task records')
    tasks = []
    for logical, group in resources.items():
        for task in group['tasks']:
            parsed = read_task_source(nested[mapping[task]])
            rows = parsed['rows']
            if (any(row['line'] < 1 or not row['name'] for row in rows)
                    or len({row['line'] for row in rows}) != len(rows)):
                raise GXWFormatError('native task has ambiguous Program ordering')
            tasks.append({'resource': logical[:-4], 'task': task[:-4],
                **{field: parsed[field] for field in ('event_name', 'interval', 'priority')},
                'programs': [{'name': row['name'], 'line': row['line']} for row in rows]})
    return resources, tasks


def native_scope_creation_plan(raw: bytes, name: str, *, scope_kind: str, resource: str | None = None) -> dict:
    """Create a resource or an empty task using the original typed constructor."""
    if scope_kind not in ('resource', 'task'):
        raise GXWFormatError('native scope creation requires resource or task kind')
    if not isinstance(name, str) or not re.fullmatch(r'[^\W\d]\w*', name, re.UNICODE):
        raise GXWFormatError('invalid native scope name')
    project, programs = _native_project_source_inventory(raw)
    resources, tasks = _native_scope_inventory(raw)
    if scope_kind == 'resource':
        if resource is not None or any(logical[:-4].casefold() == name.casefold() for logical in resources):
            raise GXWFormatError('native resource name already exists or has a conflicting parent')
    elif (not isinstance(resource, str) or resource + '.res' not in resources
          or any(row['task'].casefold() == name.casefold() for row in tasks)):
        raise GXWFormatError('native task requires an existing resource and a unique task name')
    try:
        name.encode('cp' + str(project['codepage']))
    except (LookupError, UnicodeError) as error:
        raise GXWFormatError('native scope name cannot be represented by the project codepage') from error
    return {'cpu': project['cpu'], 'codepage': project['codepage'], 'resource': name if scope_kind == 'resource' else resource,
            **({'task': name} if scope_kind == 'task' else {}), 'programs': programs,
            'resources': [logical[:-4] for logical in resources], 'tasks': tasks}


def native_task_scope_context(raw: bytes) -> dict:
    """Read current resource/task choices; no native operation or scheduling claim."""
    try:
        resources, tasks = _native_scope_inventory(raw)
        return {'status': 'available', 'resources': [name[:-4] for name in resources], 'tasks': tasks}
    except GXWFormatError as error:
        return {'status': 'unavailable', 'resources': [], 'tasks': [], 'message': str(error)}


def native_program_creation_plan(raw: bytes, name: str, *, resource: str, task: str, scope_mode: str) -> dict:
    """Preflight ordinary FBD creation and its explicit task destination."""
    source = native_source_creation_plan(raw, name, pou_kind='program')
    resources, tasks = _native_scope_inventory(raw)
    if scope_mode == 'existing_task':
        selected = [row for row in tasks if row['resource'] == resource and row['task'] == task]
        if len(selected) != 1:
            raise GXWFormatError('new Program requires the selected current resource/task ownership')
        if max((row['line'] for row in selected[0]['programs']), default=0) >= 0x7fffffff:
            raise GXWFormatError('native task Program line exceeds the original signed integer field')
    elif scope_mode == 'new_task':
        native_scope_creation_plan(raw, task, scope_kind='task', resource=resource)
    elif scope_mode == 'new_resource':
        native_scope_creation_plan(raw, resource, scope_kind='resource')
        if (not isinstance(task, str) or not re.fullmatch(r'[^\W\d]\w*', task, re.UNICODE)
                or any(row['task'].casefold() == task.casefold() for row in tasks)):
            raise GXWFormatError('new resource requires a valid unique task name')
        try:
            task.encode('cp' + str(source['codepage']))
        except (LookupError, UnicodeError) as error:
            raise GXWFormatError('native task name cannot be represented by the project codepage') from error
    else:
        raise GXWFormatError('Program creation requires existing_task, new_task or new_resource scope mode')
    return {'cpu': source['cpu'], 'codepage': source['codepage'], 'source_name': name,
        'resource': resource, 'task': task, 'scope_mode': scope_mode,
        'create_resource': scope_mode == 'new_resource', 'create_task': scope_mode != 'existing_task'}


def native_program_task_plan(raw: bytes, program: str, *, resource: str, task: str) -> dict:
    """Append one current ordinary Program to its selected existing task.

    `program` is the current logical source filename. Resource ownership comes
    from directory roles. Task configuration is bound to original getters on
    load; cached names and compiled resource bytes are not ownership evidence.
    """
    project, programs = _native_project_source_inventory(raw)
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested = validate_cfb_streams(outer['_hdb'])
    sources, _ = project_source_groups(outer['projectdatalist.xml'])
    resources, tasks = _native_scope_inventory(raw)
    if (not isinstance(program, str) or program not in sources
            or not isinstance(resource, str) or not isinstance(task, str)
            or resource + '.res' not in resources
            or task + '.tsk' not in resources[resource + '.res']['tasks']):
        raise GXWFormatError('native task binding requires current unambiguous source/resource/task ownership')
    labels = sources[program]['labels']
    owner = parse_declarations(nested[mapping[labels]], logical_name=labels).owner_name
    selected, = [row for row in programs if row['name'] == owner]
    if selected['pou_type'] != 0x01000000:
        raise GXWFormatError('native task binding requires an ordinary Program, not a callable FB/function')
    for row in tasks:
        if any(entry['name'].casefold() == owner.casefold() for entry in row['programs']):
            raise GXWFormatError('ordinary Program is already registered in a task')
    selected_rows = next(row['programs'] for row in tasks if row['resource'] == resource and row['task'] == task)
    if selected_rows is None or max([0] + [row['line'] for row in selected_rows]) >= 2**31 - 1:
        raise GXWFormatError('native task append line is not representable as a signed native integer')
    return {'cpu': project['cpu'], 'codepage': project['codepage'], 'program_name': owner,
        'program_kind': selected['program_kind'], 'before_body': selected['body'],
        'resource': resource, 'task': task,
        'existing_programs': [row['name'] for row in sorted(selected_rows, key=lambda row: row['line'])],
        'programs': programs, 'tasks': tasks}


def _native_fact(value, kind, name=None):
    """A returned native object, with successful original type/name getters."""
    try:
        identity, read_type, read_name = value['id'], value['read_type'], value['read_name']
        if (not isinstance(identity, list) or len(identity) != 12 or not any(identity)
                or any(type(word) is not int or not 0 <= word <= 0xffffffff for word in identity)
                or type(read_type['data_type']) is not int or read_type['data_type'] != kind
                or not isinstance(read_name['name'], str) or name is not None and read_name['name'] != name
                or any(type(row['hresult']) is not int or row['hresult'] < 0
                       or type(row['code']) is not int or row['code'] != 0
                       for row in (read_type, read_name))):
            raise ValueError
        return identity
    except (KeyError, TypeError, ValueError) as error:
        raise GXWFormatError('native created source object identity is incomplete or conflicting') from error


def _native_source_identity(source, name, language):
    try:
        owner, body = _native_fact(source['owner'], 26, name), _native_fact(source['body'], 32)
        read_language = source['language']
        if (source['status'] != 'verified-source-object' or source['name'] != name
                or source['lookup_type'] != 32 or source['lookup']['id'] != body
                or source['owner_lookup']['lookup_type'] != 26):
            raise ValueError
        if (source['owner_lookup']['id'] != owner or source['body_parent']['id'] != owner
                or read_language['object_id'] != owner or type(read_language['value']) is not int or read_language['value'] != language
                or read_language['expected'] != language
                or any(type(row['hresult']) is not int or row['hresult'] < 0
                       or type(row['code']) is not int or row['code'] != 0
                       for row in (source['lookup'], source['owner_lookup'], source['body_parent'], read_language))):
            raise ValueError
        return owner, body
    except (KeyError, TypeError, ValueError) as error:
        raise GXWFormatError('native source getter/parent/language identity is conflicting') from error


def _verify_scope_sources(programs, inventory, groups, payloads):
    if not isinstance(inventory, list) or len(inventory) != len(programs):
        raise GXWFormatError('native scope source inventory is incomplete')
    saved = {}
    for logical, group in groups.items():
        labels = parse_declarations(payloads[group['labels']], logical_name=group['labels'])
        if labels.owner_name in saved:
            raise GXWFormatError('saved scope has ambiguous POU ownership')
        saved[labels.owner_name] = (logical, group, labels)
    expected = {row['name']: row for row in programs}
    if saved.keys() != expected.keys():
        raise GXWFormatError('native scope creation changed POU membership')
    verified = set()
    for item in inventory:
        name = item['source']['name']
        row = expected[name]
        owner, _ = _native_source_identity(item['source'], name, row['program_kind'])
        logical, group, labels = saved[name]
        if (name in verified or type(item['pou_type']) is not int or item['pou_type'] != row['pou_type']
                or labels.owner_pou_type != row['pou_type'] or group['folder'] != owner[4]
                or workspace_body(payloads[logical]) != base64.b64decode(row['body'])):
            raise GXWFormatError('saved scope source differs from the current native identity/body/type')
        verified.add(name)


def _verify_scope_resources(rows, groups):
    if not isinstance(rows, list) or len(rows) != len(groups):
        raise GXWFormatError('native resource inventory is incomplete')
    indexed = {}
    for row in rows:
        name = row['read_name']['name']
        identity = tuple(_native_fact(row, 8, name))
        if (name + '.res' not in groups or identity in indexed
                or identity[3] != groups[name + '.res']['folder']):
            raise GXWFormatError('saved resource folder differs from the original native identity')
        indexed[identity] = row
    if {row['read_name']['name'] for row in indexed.values()} != {name[:-4] for name in groups}:
        raise GXWFormatError('native resource name membership differs')
    return indexed


def _verify_scope_tasks(rows, expected, groups):
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise GXWFormatError('native scope task inventory is incomplete')
    wanted = {(row['resource'], row['task']): row for row in expected}
    indexed, configurations = {}, []
    for row in rows:
        resource, task = row['resource']['read_name']['name'], row['task']['read_name']['name']
        resource_id = _native_fact(row['resource'], 8, resource)
        task_id = _native_fact(row['task'], 10, task)
        key = resource, task
        if (key not in wanted or key in indexed or resource + '.res' not in groups
                or task + '.tsk' not in groups[resource + '.res']['tasks']
                or resource_id[3] != groups[resource + '.res']['folder'] or task_id[3] != resource_id[3]):
            raise GXWFormatError('native scope task ownership differs from its saved directory')
        entries, identities = [], set()
        for entry in row['programs']:
            name, line = entry['element']['read_name']['name'], entry['line']
            identity = tuple(_native_fact(entry['element'], 12, name))
            if type(line) is not int or line < 1 or identity in identities:
                raise GXWFormatError('native scope task Program identity/order is conflicting')
            identities.add(identity)
            entries.append({'name': name, 'line': line})
        if sorted(entries, key=lambda value: value['line']) != sorted(wanted[key]['programs'], key=lambda value: value['line']):
            raise GXWFormatError('native scope task Program rows differ from the current source')
        cfg = row.get('configuration')
        configurations.append(cfg is not None)
        if cfg is not None:
            values = task_configuration_values(cfg)
            if any(type(values[field]) is not type(wanted[key][field]) or values[field] != wanted[key][field]
                   for field in ('event_name', 'interval', 'priority')):
                raise GXWFormatError('native scope task configuration differs from the current source')
        indexed[key] = row
    return indexed, configurations


def verify_native_scope_creation(baseline: bytes, name: str, raw: bytes, observation: dict,
                                 *, scope_kind: str, resource: str | None = None) -> ProjectWriteResult:
    """Bind an original constructor to its saved scope and preserve old sources."""
    from .compiler_debug import compiler_debug_search_map_permutation
    plan = native_scope_creation_plan(baseline, name, scope_kind=scope_kind, resource=resource)
    containers = [validate_cfb_streams(value) for value in (baseline, raw)]
    before, after = [{logical: nested[stream] for logical, stream in logical_mapping(container['projectdatalist.xml']).items()
                      if stream in nested} for container in containers for nested in [validate_cfb_streams(container['_hdb'])]]
    groups, issues = project_source_groups(containers[1]['projectdatalist.xml'])
    saved_resources, saved_tasks = _native_scope_inventory(raw)
    old_resources, _ = _native_scope_inventory(baseline)
    added = name + ('.res' if scope_kind == 'resource' else '.tsk')
    if issues or after.keys() - before.keys() != {added} or before.keys() - after.keys():
        raise GXWFormatError('native scope creation changed unexpected materialized source membership')
    if (saved_resources.keys() != old_resources.keys() | ({added} if scope_kind == 'resource' else set())
            or any(set(saved_resources[logical]['tasks']) != set(group['tasks']) |
                   ({added} if scope_kind == 'task' and logical == resource + '.res' else set())
                   for logical, group in old_resources.items())):
        raise GXWFormatError('native scope creation changed unexpected directory ownership')
    config_observations = []
    try:
        creation = observation['creation']
        operation = 'create_' + scope_kind + '_scope'
        if creation['operation'] != operation or creation['stage'] != 'saved_' + scope_kind:
            raise ValueError
        _verify_scope_sources(plan['programs'], creation['existing_sources'], groups, after)
        _verify_scope_sources(plan['programs'], creation['final_sources'], groups, after)
        if creation['existing_sources'] != creation['final_sources']:
            raise ValueError
        previous_tasks, cfg = _verify_scope_tasks(creation['before_tasks'], plan['tasks'], saved_resources)
        config_observations.extend(cfg)
        current_tasks, cfg = _verify_scope_tasks(creation['after_tasks'], saved_tasks, saved_resources)
        config_observations.extend(cfg)
        expected_tasks = dict(previous_tasks)
        if scope_kind == 'resource':
            _native_fact(creation['collection'], 7)
            created_id = tuple(_native_fact(creation['created'], 8, name))
            previous = _verify_scope_resources(creation['before_resources'],
                {logical: saved_resources[logical] for logical in old_resources})
            current = _verify_scope_resources(creation['after_resources'], saved_resources)
            if created_id in previous:
                raise ValueError
            previous[created_id] = creation['created']
            if current != previous or saved_resources[added]['tasks']:
                raise ValueError
            parsed_resource = read_resource_source(after[added])
            if any(size for _, size in parsed_resource['code_spans']) or parsed_resource['cached_names']:
                raise GXWFormatError('new native resource is not empty')
        else:
            resource_id = _native_fact(creation['resource'], 8, resource)
            _native_fact(creation['collection'], 9)
            task_id = _native_fact(creation['created'], 10, name)
            new_task = current_tasks[resource, name]
            if (task_id[3] != resource_id[3] or new_task['resource'] != creation['resource']
                    or new_task['task'] != creation['created'] or new_task['programs']):
                raise ValueError
            expected_tasks[resource, name] = new_task
            if 'before_resources' in creation or 'after_resources' in creation:
                if (_verify_scope_resources(creation['before_resources'], saved_resources)
                        != _verify_scope_resources(creation['after_resources'], saved_resources)):
                    raise ValueError
        if current_tasks != expected_tasks:
            raise GXWFormatError('native scope creation changed existing task objects or configurations')
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise GXWFormatError('native scope constructor identity/source/configuration is conflicting') from error
    vendor_resources = {'Param.lup', 'BlockPassword.bpw', 'DeviceAssignment.dat', 'CallTree.dat', 'ESCompiler.stg'}
    vendor_changes, debug_changes = [], []
    resource_cache = None
    for logical in before:
        if before[logical] == after[logical]:
            continue
        if logical in vendor_resources:
            vendor_changes.append(logical)
        elif logical == 'DebugInformation2.dat' and compiler_debug_search_map_permutation(before[logical], after[logical]):
            debug_changes.append(logical)
        elif scope_kind == 'task' and logical == resource + '.res':
            old, saved = read_resource_source(before[logical]), read_resource_source(after[logical])
            if (old['prefix'] != saved['prefix'] or old['suffix'] != saved['suffix']
                    or sorted(saved['cached_names']) != sorted(task[:-4] for task in saved_resources[logical]['tasks'])):
                raise GXWFormatError('native task creation changed resource code or unknown bytes outside its name cache')
            resource_cache = {'resource': resource, 'before': old['cached_names'], 'after': saved['cached_names'],
                              'code_header_and_suffix': 'exact', 'ownership_evidence': 'directory and original native parents',
                              'execution_order': 'unresolved'}
        else:
            raise GXWFormatError('native scope creation changed an unedited original object: ' + logical)
    if scope_kind == 'task' and resource_cache is None:
        raise GXWFormatError('native task creation did not persist its observed resource name-cache update')
    configuration_status = ('current' if all(config_observations) else 'not_observed') if config_observations else 'not_applicable'
    report = {'schema_version': 1, 'operation': 'native_scope_creation', 'scope_kind': scope_kind, 'created_name': name,
        'resource': plan['resource'], 'native_workspace': observation, 'native_task_configuration': configuration_status,
        'created_task_configuration': next(({field: row[field] for field in ('event_name', 'interval', 'priority')}
            for row in saved_tasks if row['task'] == name and scope_kind == 'task'), None),
        'vendor_changed_streams': vendor_changes, 'debug_map_changes': debug_changes, 'resource_cache_change': resource_cache,
        'validation': {'parser': 'passed', 'source_body': 'exact', 'unedited_sources': 'preserved',
            'old_task_configuration_and_records': 'exact', 'native_save': 'passed', 'saved_file_readback': 'passed',
            'gxworks_compile': 'not_run', 'gxworks_check': 'not_run'},
        'limitations': ['Original typed constructor; default task values are observed, not generalized across CPUs or interpreted as execution semantics']}
    return ProjectWriteResult(bytes(raw), report)


def _task_name_pair_permutation(before: bytes, after: bytes, names: list[str]) -> int | None:
    """Only the frozen two-task-name swap; no scheduling interpretation."""
    if len(names) != 2 or len(set(names)) != 2:
        return None
    records = [struct.pack('<I', len(encoded) // 2) + encoded
               for name in names for encoded in [(name + '\0').encode('utf-16le')]]
    for order in (records, records[::-1]):
        block = b''.join(order)
        offset = before.find(block)
        if (offset >= 4 and before.count(block) == 1
                and struct.unpack_from('<I', before, offset - 4)[0] == 2
                and before[:offset] + b''.join(order[::-1]) + before[offset + len(block):] == after):
            return offset
    return None


def verify_native_program_task_binding(baseline: bytes, program: str, raw: bytes, observation: dict,
                                       *, resource: str, task: str) -> ProjectWriteResult:
    """Verify a native append against current sources, task ownership and bytes."""
    from copy import deepcopy
    from .compiler_debug import compiler_debug_search_map_permutation

    plan = native_program_task_plan(baseline, program, resource=resource, task=task)
    containers = [validate_cfb_streams(value) for value in (baseline, raw)]
    mappings = [logical_mapping(value['projectdatalist.xml']) for value in containers]
    payloads = [validate_cfb_streams(value['_hdb']) for value in containers]
    before, after = [{name: nested[stream] for name, stream in mapping.items() if stream in nested}
                     for mapping, nested in zip(mappings, payloads)]
    resources, resource_issues = project_resource_groups(containers[1]['projectdatalist.xml'])
    sources, source_issues = project_source_groups(containers[1]['projectdatalist.xml'])
    if source_issues or resource_issues:
        raise GXWFormatError('native task binding changed materialized source/resource membership or ownership')
    previous_resources, _ = project_resource_groups(containers[0]['projectdatalist.xml'])
    previous_sources, _ = project_source_groups(containers[0]['projectdatalist.xml'])
    documents = {name: parse_declarations(data, logical_name=name)
                 for name, data in before.items() if name.endswith('.lh')}
    names = _saved_object_names(containers[0]['projectdatalist.xml'], mappings[0],
        containers[1]['projectdatalist.xml'], mappings[1], previous_sources, documents)
    if ({names[name] for name in before} != after.keys()
            or sources.keys() != {names[name] for name in previous_sources}
            or resources.keys() != previous_resources.keys()
            or any(sources[names[name]]['labels'] != names[group['labels']] for name, group in previous_sources.items())
            or any(set(resources[name]['tasks']) != set(group['tasks']) for name, group in previous_resources.items())):
        raise GXWFormatError('native task binding changed unedited directory ownership')
    tasks_by_name = {name[:-4]: (logical[:-4], group['folder'], read_task_source(after[name]))
                     for logical, group in resources.items() for name in group['tasks']}
    expected_tasks = {(row['resource'], row['task']): row for row in plan['tasks']}
    observed_configurations = []

    def task_index(rows, *, original):
        if not isinstance(rows, list) or len(rows) != len(expected_tasks):
            raise GXWFormatError('native task inventory is incomplete')
        indexed = {}
        for row in rows:
            resource_name = row['resource']['read_name']['name']
            task_name = row['task']['read_name']['name']
            resource_id = _native_fact(row['resource'], 8, resource_name)
            task_id = _native_fact(row['task'], 10, task_name)
            key = resource_name, task_name
            if key not in expected_tasks or key in indexed:
                raise GXWFormatError('native task inventory has ambiguous ownership')
            saved_resource, folder, saved_task = tasks_by_name[task_name]
            if saved_resource != resource_name or resource_id[3] != folder or task_id[3] != folder:
                raise GXWFormatError('saved task ownership differs from original native resource/parent identities')
            entries = row['programs']
            if not isinstance(entries, list):
                raise GXWFormatError('native task Program inventory is incomplete')
            projected, identities = [], set()
            for entry in entries:
                name, line = entry['element']['read_name']['name'], entry['line']
                identity = tuple(_native_fact(entry['element'], 12, name))
                if (type(line) is not int or line < 1 or identity in identities):
                    raise GXWFormatError('native task Program identity/line is conflicting')
                identities.add(identity)
                projected.append({'name': name, 'line': line})
            expected = expected_tasks[key]['programs'] if original else [
                {'name': entry['name'], 'line': entry['line']} for entry in saved_task['rows']]
            if sorted(projected, key=lambda entry: entry['line']) != sorted(expected, key=lambda entry: entry['line']):
                raise GXWFormatError('native task Program inventory differs from the selected source records')
            configuration = row.get('configuration')
            observed_configurations.append(configuration is not None)
            if configuration is not None:
                values = task_configuration_values(configuration)
                if any(type(values[field]) is not type(expected_tasks[key][field])
                       or values[field] != expected_tasks[key][field] for field in ('event_name', 'interval', 'priority')):
                    raise GXWFormatError('native task configuration differs from the current source')
            indexed[key] = row
        return indexed

    try:
        created = observation['creation']
        if created['operation'] != 'bind_program_task' or created['pou_type'] != 0x01000000:
            raise ValueError
        source_owner, source_body = _native_source_identity(created['source'], plan['program_name'], plan['program_kind'])
        _native_fact(created['resource'], 8, resource)
        _native_fact(created['task'], 10, task)
        element = _native_fact(created['element'], 12, plan['program_name'])
        before_tasks = task_index(created['before_tasks'], original=True)
        after_tasks = task_index(created['after_tasks'], original=False)
        selected = before_tasks[resource, task]
        if selected['resource'] != created['resource'] or selected['task'] != created['task']:
            raise ValueError
        if any(element == entry['element']['id'] for row in before_tasks.values() for entry in row['programs']):
            raise ValueError
        expected = deepcopy(before_tasks)
        expected[resource, task]['programs'].append({'element': created['element'], 'line': created['line']})
        if expected != after_tasks:
            raise ValueError
        inventory = created['existing_sources']
        expected_programs = {row['name']: row for row in plan['programs']}
        saved_programs = {}
        for logical, group in sources.items():
            labels = parse_declarations(after[group['labels']], logical_name=group['labels'])
            saved_programs[labels.owner_name] = (logical, group, labels)
        if set(saved_programs) != set(expected_programs) or len(inventory) != len(expected_programs):
            raise ValueError
        native_owners = set()
        for item in inventory:
            name = item['source']['name']
            row = expected_programs[name]
            owner, body = _native_source_identity(item['source'], name, row['program_kind'])
            logical, group, labels = saved_programs[name]
            if (name in native_owners or item['pou_type'] != row['pou_type'] or labels.owner_pou_type != row['pou_type']
                    or group['folder'] != owner[4] or workspace_body(after[logical]) != base64.b64decode(row['body'])
                    or name == plan['program_name'] and (owner != source_owner or body != source_body)):
                raise ValueError
            native_owners.add(name)
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise GXWFormatError('native task append identity, configuration or source inventory is conflicting') from error
    append = verify_task_source_append(before[task + '.tsk'], after[task + '.tsk'],
                                      program_name=plan['program_name'], line=created['line'])
    vendor_resources = {'Param.lup', 'BlockPassword.bpw', 'DeviceAssignment.dat', 'CallTree.dat', 'ESCompiler.stg'}
    vendor_changes, debug_changes, resource_changes = [], [], []
    for logical in before:
        saved = after[names[logical]]
        if before[logical] == saved or logical == task + '.tsk':
            continue
        if logical in vendor_resources:
            vendor_changes.append(logical)
        elif logical == 'DebugInformation2.dat' and compiler_debug_search_map_permutation(before[logical], saved):
            debug_changes.append(logical)
        elif logical == resource + '.res':
            task_names = [task_name for resource_name, task_name in before_tasks if resource_name == resource]
            offset = _task_name_pair_permutation(before[logical], saved, task_names)
            if offset is None:
                raise GXWFormatError('native task append changed unknown resource bytes')
            resource_changes.append({'resource': resource, 'offset': offset, 'change': 'two-task-name-order-only',
                                     'execution_order': 'unresolved'})
        else:
            raise GXWFormatError('native task append changed an unedited original object: ' + logical)
    report = {'schema_version': 1, 'operation': 'native_program_task_binding', 'program': names[program],
        'objects': [{'object': name, 'saved_object': saved, 'stream': mappings[1][saved], 'byte_preservation': 'exact'}
                    for name, saved in names.items() if name != saved],
        'registered_object_renames': [{'before': name, 'after': saved} for name, saved in names.items() if name != saved],
        'resource': resource, 'task': task, 'append': append, 'native_workspace': observation,
        'task_configuration': {field: read_task_source(after[task + '.tsk'])[field]
                               for field in ('event_name', 'interval', 'priority')},
        'native_task_configuration': 'current' if all(observed_configurations) else 'not_observed',
        'vendor_changed_streams': vendor_changes, 'debug_map_changes': debug_changes,
        'resource_name_order_changes': resource_changes,
        'validation': {'parser': 'passed', 'source_body': 'exact', 'unedited_sources': 'preserved',
            'task_configuration': 'exact', 'old_task_rows': 'exact', 'native_save': 'passed',
            'saved_file_readback': 'passed', 'gxworks_compile': 'not_run', 'gxworks_check': 'not_run'},
        'limitations': ['Append an existing ordinary Program; compilation/checking and PLC execution are separate operations']}
    return ProjectWriteResult(bytes(raw), report)


def verify_native_source_creation(baseline: bytes, name: str, raw: bytes, observation: dict,
                                  *, pou_kind='function_block') -> ProjectWriteResult:
    """Bind returned native identity to the exported POU and preserve old bytes."""
    from .object_model import read_project_context
    plan = native_source_creation_plan(baseline, name, pou_kind=pou_kind)
    try:
        creation = observation['creation']
        source = creation['source']
        owner, body_id = _native_source_identity(source, name, plan['program_kind'])
        labels_id = _native_fact(creation['labels'], 28)
        collection = _native_fact(creation['collection'], 25)
        language = source['language']
        if (type(creation['pou_type']) is not int or creation['pou_type'] != plan['pou_type']
                or type(creation['program_kind']) is not int or creation['program_kind'] != plan['program_kind']
                or source['lookup']['id'] != body_id or source['owner_lookup']['id'] != owner
                or source['body_parent']['id'] != owner or language['object_id'] != owner
                or language['value'] != language['expected'] or language['value'] != plan['program_kind']
                or len({tuple(owner), tuple(body_id), tuple(labels_id), tuple(collection)}) != 4):
            raise ValueError
        native_body = base64.b64decode(creation['body'], validate=True)
        existing = creation['existing_sources']
        if not isinstance(existing, list) or len(existing) != len(plan['programs']):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise GXWFormatError('native creation result is not bound to the selected new POU') from error
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested = validate_cfb_streams(outer['_hdb'])
    groups, issues = project_source_groups(outer['projectdatalist.xml'])
    if issues:
        raise GXWFormatError('saved native creation has missing or ambiguous source roles')
    saved = {}
    for program, group in groups.items():
        labels = parse_declarations(nested[mapping[group['labels']]], logical_name=group['labels'])
        if labels.owner_name in saved:
            raise GXWFormatError('saved native creation has ambiguous POU owners')
        saved[labels.owner_name] = (program, group, labels)
    if set(saved) != {name, *(row['name'] for row in plan['programs'])}:
        raise GXWFormatError('saved native creation changed the selected POU membership')
    program, group, labels = saved[name]
    if (group['folder'] != owner[4] or labels_id[4] != owner[4]
            or labels.owner_pou_type != plan['pou_type'] or labels.rows
            or workspace_body(nested[mapping[program]]) != native_body):
        raise GXWFormatError('saved native POU body/declarations differ from the returned object')
    context = read_project_context(raw, program)
    if (context.sources.cpu != plan['cpu'] or context.program.raw[source_payload_offset(context.program.raw)] != 208
            or context.program.nodes or context.object_model()['issues']):
        raise GXWFormatError('saved native constructor is not an empty current FBD source')
    previous = validate_cfb_streams(baseline)
    previous_mapping = logical_mapping(previous['projectdatalist.xml'])
    previous_nested = validate_cfb_streams(previous['_hdb'])
    previous_groups, _ = project_source_groups(previous['projectdatalist.xml'])
    native_existing = {}
    expected_programs = {row['name']: row for row in plan['programs']}
    for item in existing:
        try:
            selected = item['source']; selected_name = selected['name']
            native_id, _ = _native_source_identity(selected, selected_name, expected_programs[selected_name]['program_kind'])
            if selected_name in native_existing:
                raise ValueError
            native_existing[selected_name] = (native_id, item['pou_type'])
        except (KeyError, TypeError, ValueError) as error:
            raise GXWFormatError('native existing source inventory is incomplete or conflicting') from error
    renumbered = []
    for row in plan['programs']:
        program_name = row['name']
        native_id, pou_type = native_existing.get(program_name, (None, None))
        saved_program, saved_group, saved_labels = saved[program_name]
        if native_id is None or saved_group['folder'] != native_id[4] or pou_type != row['pou_type']:
            raise GXWFormatError('saved existing POU does not match the opened native inventory')
        old_programs = [p for p, g in previous_groups.items()
                        if parse_declarations(previous_nested[previous_mapping[g['labels']]], logical_name=g['labels']).owner_name == program_name]
        old_program, = old_programs
        old_group = previous_groups[old_program]
        if old_group['folder'] != saved_group['folder']:
            renumbered.append({'program': program_name, 'before': old_group['folder'], 'after': saved_group['folder']})
    # Native caches may be updated by the vendor save. Source and unknown
    # materialized streams must not change during an empty POU creation.
    vendor_resources = {'Param.lup', 'BlockPassword.bpw', 'DeviceAssignment.dat', 'CallTree.dat', 'ESCompiler.stg'}
    after_by_stream = {stream: logical for logical, stream in mapping.items()}
    changes = []
    debug_map_changes = []
    for logical, stream in previous_mapping.items():
        before = previous_nested.get(stream)
        if before is None:
            continue
        saved_name = after_by_stream.get(stream)
        after = nested.get(stream)
        if saved_name is None or after is None:
            raise GXWFormatError('native creation removed a materialized original object: ' + logical)
        if before != after:
            debug_permutation = False
            if logical == saved_name == 'DebugInformation2.dat':
                from .compiler_debug import compiler_debug_search_map_permutation
                debug_permutation = compiler_debug_search_map_permutation(before, after)
            if saved_name != logical or not (logical in vendor_resources or debug_permutation):
                raise GXWFormatError('native creation changed an unedited original object: ' + logical)
            changes.append(logical)
            if debug_permutation:
                debug_map_changes.append({'object': logical, 'change': 'search-map-entry-order-only',
                    'offset_tables_and_elements': 'exact', 'group_order_and_fields': 'exact', 'unknown_suffix': 'exact'})
    report = {'schema_version': 1, 'operation': 'native_source_creation', 'created_program': program,
              'created_declarations': group['labels'], 'native_workspace': observation,
              'native_folder_renumbering': renumbered, 'vendor_changed_streams': changes,
              'debug_map_changes': debug_map_changes,
              'validation': {'parser': 'passed', 'source_body': 'exact', 'declarations': 'passed',
                             'unedited_sources': 'preserved', 'workspace_load': 'passed', 'native_save': 'passed',
                             'saved_file_readback': 'passed', 'gxworks_compile': 'not_run', 'gxworks_check': 'not_run'},
              'pou_kind': pou_kind,
              'limitations': ['Empty project FBD ' + ('Program' if pou_kind == 'program' else 'FB')
                              + '; task registration, source edits and compilation/checking are separate operations']}
    return ProjectWriteResult(bytes(raw), report)


def _sfc_graph_rebuild(source, symbols, *, cpu):
    """Bind original symbol writers to existing Q/L action/condition identities."""
    from .sfc_pou import NATIVE_SFC_GRAPH_QL_CPUS
    if cpu not in NATIVE_SFC_GRAPH_QL_CPUS:
        raise GXWFormatError('SFC symbol rebuilding requires a measured Q/L CPU profile')
    if not isinstance(symbols, list) or not symbols or len(symbols) > 8192:
        raise GXWFormatError('SFC symbol rebuilding requires a bounded nonempty symbol list')
    fields = {'initial_step': {'x', 'y', 'number'}, 'step': {'x', 'y', 'number'},
              'transition': {'x', 'y', 'number'}, 'end': {'x', 'y'},
              'line': {'x', 'start', 'end'},
              **{kind: {'start', 'end', 'y'} for kind in ('parallel_branch', 'parallel_coupling',
                                                         'selective_branch', 'selective_coupling')}}
    result, seen, steps, transitions = [], set(), [], []
    for symbol in symbols:
        kind = symbol.get('kind') if isinstance(symbol, dict) else None
        required = fields.get(kind)
        if (required is None or set(symbol) != required | {'kind'}
                or any(type(symbol[key]) is not int or not 0 <= symbol[key] <= 32767 for key in required)):
            raise GXWFormatError('SFC symbols require known kinds and nonnegative integer coordinates/references')
        if ('start' in required and (symbol['start'] > symbol['end'] or
                kind != 'line' and symbol['start'] == symbol['end'])) or (
                kind in ('step', 'initial_step', 'end') and symbol['y'] % 3):
            raise GXWFormatError('SFC symbol spans or step-row alignment differ from the measured writer form')
        key = ('cell', symbol['x'], symbol['y']) if 'y' in required and 'x' in required else tuple(sorted(symbol.items()))
        if key in seen:
            raise GXWFormatError('SFC symbol rebuilding repeats a cell or writer operation')
        seen.add(key)
        if kind in ('step', 'initial_step'):
            steps.append(symbol['number'])
        elif kind == 'transition':
            transitions.append(symbol['number'])
        result.append(dict(symbol))
    expected_steps = [row['references'][0] for row in source.graph_records if row['kind'] == 'step-action-reference']
    expected_transitions = [row['references'][0] for row in source.graph_records if row['kind'] == 'transition-reference']
    if (len(set(steps)) != len(steps) or sorted(steps) != sorted(expected_steps)
            or len(set(transitions)) != len(transitions) or sorted(transitions) != sorted(expected_transitions)
            or sum(row['kind'] == 'initial_step' for row in result) != 1
            or sum(row['kind'] == 'end' for row in result) != 1):
        raise GXWFormatError('SFC rebuilding must retain each current step/action and transition identity once')
    for number in steps:
        if len([row for row in source.layout['actions'] if row['number'] == number]) != 1:
            raise GXWFormatError('SFC rebuilding requires an unambiguous same-number action registration')
    return {'kind': 'rebuild', 'symbols': result}


def _sfc_graph_edits(source, supplied, *, cpu=None) -> list[dict]:
    """Bind action and jump-reference edits to the current stored graph."""
    if not isinstance(supplied, list) or not supplied or len(supplied) > 8192:
        raise GXWFormatError('SFC graph edits require a bounded nonempty list')
    if source.graph_tokens is None or source.layout['graph']['storage_words'] != [0, 0]:
        raise GXWFormatError('native SFC graph edits require current converted graph references')
    if len(supplied) == 1 and isinstance(supplied[0], dict) and supplied[0].get('kind') == 'rebuild':
        if set(supplied[0]) != {'kind', 'symbols'}:
            raise GXWFormatError('SFC rebuilding requires the complete requested symbol list')
        return [_sfc_graph_rebuild(source, supplied[0]['symbols'], cpu=cpu)]
    result, seen = [], set()
    for edit in supplied:
        if isinstance(edit, dict) and edit.get('kind') == 'jump':
            fields = {'x', 'y', 'before_step_reference', 'target_step_reference'}
            if (set(edit) != fields | {'kind'}
                    or any(type(edit[key]) is not int or not 0 <= edit[key] <= 65535 for key in fields)):
                raise GXWFormatError('SFC jump edits require integer coordinates and current/target references')
            key = ('jump', edit['x'], edit['y'])
            targets = [row for row in source.graph_records if row['kind'] == 'step-action-reference'
                       and row['references'][0] == edit['target_step_reference']]
            if (key in seen or len(targets) != 1 or not any(row['kind'] == 'step-reference'
                    and row['references'] == [edit['before_step_reference']] for row in source.graph_records)):
                raise GXWFormatError('SFC jump requires current references and one existing target step')
            seen.add(key); result.append(dict(edit))
            continue
        if (not isinstance(edit, dict) or set(edit) != {'step_reference', 'action_number'}
                or any(type(edit[key]) is not int or not 0 <= edit[key] <= 65535 for key in edit)):
            raise GXWFormatError('SFC graph edits require integer step_reference and action_number')
        reference, action = edit['step_reference'], edit['action_number']
        selected = [row for row in source.graph_records if row['kind'] == 'step-action-reference'
                    and row['references'][0] == reference]
        if len(selected) != 1 or len(selected[0]['references']) != 2 or reference in seen:
            raise GXWFormatError('missing, ambiguous or repeated current SFC step reference')
        if len([row for row in source.layout['actions'] if row['number'] == action]) != 1:
            raise GXWFormatError('SFC graph edit requires an existing unambiguous action registration')
        seen.add(reference)
        result.append({'step_reference': reference, 'before_action_number': selected[0]['references'][1],
                       'action_number': action})
    return result


def native_source_plan(prepared: PreparedProjectWrite, *, graph_edits=None) -> dict:
    """Prepare one existing POU and supported root-local declaration changes."""
    if len(prepared.programs) != 1:
        raise GXWFormatError('native source save requires one selected existing Program.pou')
    logical = next(iter(prepared.programs))
    from .sfc_pou import SfcSource, frame_sfc_pou
    sfc = isinstance(prepared.programs[logical], SfcSource)
    if graph_edits is not None and not sfc:
        raise GXWFormatError('SFC graph references require a selected SFC source')
    groups, _ = project_source_groups(prepared.outer['projectdatalist.xml'], folder_type=8 if sfc else 7)
    sfc_labels = {group['labels'] for group in groups.values()} if sfc else set()
    documents = {name: parse_declarations(prepared.nested[stream], logical_name=name,
                    source_kind=240 if name in sfc_labels else None)
                 for name, stream in prepared.mapping.items() if name.endswith('.lh') and stream in prepared.nested}
    local = local_table_for(documents, logical, groups)
    if local not in prepared.mapping:
        raise GXWFormatError('native source save requires the selected POU local declaration table')
    original = documents[local]
    if original.scope != 'local' or not original.owner_name:
        raise GXWFormatError('native source save cannot resolve the declaration owner')
    changed = prepared.declarations.get(local, original)
    for name, (_, old, new) in prepared.replacements.items():
        if old != new and name not in (logical, local):
            raise GXWFormatError('native source save does not yet edit other POU or global tables: ' + name)
    metadata = [prepared.nested[stream] for name, stream in prepared.mapping.items() if name.endswith('.prj')]
    if len(metadata) != 1:
        raise GXWFormatError('native source save requires unambiguous project CPU/configuration')
    project = read_project_text_context(metadata[0])
    if sfc:
        if local in prepared.declarations:
            raise GXWFormatError('native SFC save currently edits existing child bodies, not declarations')
        _, before, after = prepared.replacements[logical]
        old_layout, new_layout = frame_sfc_pou(before), frame_sfc_pou(after)
        old_graph, new_graph = old_layout['graph'], new_layout['graph']
        graph = before[old_graph['offset']:old_graph['offset'] + old_graph['size']]
        if (graph != after[new_graph['offset']:new_graph['offset'] + new_graph['size']]
                or [(r['kind'], r['name'], r['number']) for r in old_layout['children']]
                != [(r['kind'], r['name'], r['number']) for r in new_layout['children']]
                or [before[r['record_offset']:r['record_offset'] + r['record_size']] for r in old_layout['actions']]
                != [after[r['record_offset']:r['record_offset'] + r['record_size']] for r in new_layout['actions']]):
            raise GXWFormatError('native SFC child save does not change graph or registration ownership')

        def children(raw, layout):
            return [{'type': 68 if r['kind'] == 'zoom' else 66, 'name': r['name'],
                     'body': base64.b64encode(raw[r['payload_offset']:r['tail_offset']]).decode('ascii')}
                    for r in layout['children']]

        plan = {'program_name': original.owner_name, 'program_kind': 240,
                'cpu': project['cpu'], 'codepage': project['codepage'],
                'graph_body': base64.b64encode(graph).decode('ascii'),
                'before_children': children(before, old_layout), 'after_children': children(after, new_layout)}
        if project['cpu'] == 'FX3U/FX3UC':
            from .native_diagnostics import sfc_child_source_scope
            scope = sfc_child_source_scope(before)
            retained = {row['name'] for row in scope['children']
                        if row['type'] == 68 and row['role'] == 'retained'}
            actions = [{'number': row['number'], 'action_name': row['name'],
                        'zoom_name': row['registrations'][0]['zoom']}
                       for row in old_layout['actions'] if len(row['registrations']) == 1
                       and row['registrations'][0]['qualifier'] == 0
                       and row['registrations'][0]['zoom'] in retained]
            if actions:
                plan['retained_actions'] = actions
        if graph_edits is not None:
            from .sfc_pou import NATIVE_SFC_GRAPH_QL_CPUS, NATIVE_SFC_GRAPH_FX_CPUS
            if project['cpu'] not in NATIVE_SFC_GRAPH_FX_CPUS | NATIVE_SFC_GRAPH_QL_CPUS:
                raise GXWFormatError('SFC graph writing requires a measured FX reference or Q/L rebuild profile')
            plan['graph_edits'] = _sfc_graph_edits(prepared.programs[logical], graph_edits, cpu=project['cpu'])
            if project['cpu'] in NATIVE_SFC_GRAPH_QL_CPUS:
                if len(plan['graph_edits']) != 1 or plan['graph_edits'][0].get('kind') != 'rebuild':
                    raise GXWFormatError('Q/L SFC graph writes require an explicit complete symbol rebuild')
        return plan
    # Editor rows retain their original byte span until serialization. Saved
    # record_id is an opaque value and can be shared; never use it as identity.
    old_rows = {r.offset: r for r in original.rows}
    retained, added = {}, []
    for row in changed.rows:
        if not row.raw and row.offset == 0:
            added.append(row)
        elif (row.offset not in old_rows or row.raw != old_rows[row.offset].raw or row.offset in retained):
            raise GXWFormatError('native declaration edit has a stale or repeated source row')
        else:
            retained[row.offset] = row
    remove, create, updates = [], [], []
    sources = None
    for old in original.rows:
        new = retained.get(old.offset)
        if new is None:
            if old.class_code != 1 or old.value_extension:
                raise GXWFormatError('native removal currently supports root VAR declarations only')
            remove.append(_identity(old))
        elif _values(old) != _values(new):
            if old.class_code != new.class_code or old.value_extension:
                raise GXWFormatError('native save does not yet change declaration classes or structure storage')
            omitted = {'name'}
            if old.data_type != new.data_type:
                scalar_bool = old.data_type == 'BOOL' and old.type_code == 0
                project_fb = False
                if (old.class_code == 1 and not old.array_marker and not old.unknown_u32
                        and old.initial_value == new.initial_value == '' and old.type_code in (0, 15)
                        and new.type_reference == new.data_type):
                    from .callable_sources import ProjectCallableSources
                    if sources is None:
                        sources = ProjectCallableSources.from_project(prepared.baseline, logical)
                    project_fb = (all(_project_fbd_fb(sources, prepared, name) for name in (old.data_type, new.data_type))
                        and (old.type_code == 0 and not old.type_reference
                             or old.type_code == 15 and _project_fbd_fb(sources, prepared, old.type_reference)))
                if new.type_code != 15 or new.array_marker or not (scalar_bool or project_fb):
                    raise GXWFormatError('native declaration type change is not verified for this transition')
                omitted |= {'data_type', 'initial_value', 'type_code', 'type_reference'}
            if _values(old, omit=omitted) != _values(new, omit=omitted):
                raise GXWFormatError('native save does not yet edit devices, initial values or comments')
            updates.append({'before': _identity(old), 'after': _identity(new),
                            'initial_before': old.initial_value, 'initial_after': new.initial_value})
    for new in added:
        formal = (original.owner_pou_type == 0x01000002 and new.class_code in (3, 4)
                  and new.data_type == 'BOOL' and new.type_code == 1 and new.initial_value == 'FALSE'
                  and not new.type_reference and 0 < new.record_id < 2**31
                  and prepared.nested[prepared.mapping[logical]][source_payload_offset(
                      prepared.nested[prepared.mapping[logical]])] == 208)
        inout = (original.owner_pou_type == 0x01000002 and new.class_code == 5
                 and (new.type_code, new.type_reference, new.array_marker, new.initial_value) == (0, '', 0, '')
                 and 0 < new.record_id < 2**31
                 and prepared.nested[prepared.mapping[logical]][source_payload_offset(
                     prepared.nested[prepared.mapping[logical]])] == 208)
        if inout:
            from .callable_sources import ProjectCallableSources
            if sources is None:
                sources = ProjectCallableSources.from_project(prepared.baseline, logical)
            inout = _created_inout_type(sources, new.data_type)
        root_value = (original.owner_pou_type in (0x01000000, 0x01000002) and new.class_code == 1
                      and (new.type_code, new.type_reference, new.array_marker, new.initial_value) == (0, '', 0, '')
                      and prepared.nested[prepared.mapping[logical]][source_payload_offset(
                          prepared.nested[prepared.mapping[logical]])] == 208)
        if root_value:
            from .callable_sources import ProjectCallableSources
            if sources is None:
                sources = ProjectCallableSources.from_project(prepared.baseline, logical)
            root_value = _created_root_type(sources, new.data_type)
        if ((new.class_code != 1 and not formal and not inout) or new.array_marker or new.value_extension or new.device or new.iec_address
                or new.comment or new.unknown_text or new.unknown_u32
                or not (inout or root_value or new.data_type == 'BOOL' and new.initial_value == 'FALSE'
                        or new.type_code == 15 and new.initial_value == '')):
            raise GXWFormatError('native creation supports default root BOOL/FB, source-bound FBD local values, BOOL input/output, or IN_OUT declarations')
        create.append(_identity(new) | {'initial_value': new.initial_value} | ({'line': new.record_id} if formal or inout else {}))
    _, before, after = prepared.replacements[logical]
    plan = {'program_name': original.owner_name, 'cpu': project['cpu'], 'codepage': project['codepage'],
            'before_body': base64.b64encode(workspace_body(before)).decode('ascii'),
            'after_body': base64.b64encode(workspace_body(after)).decode('ascii'),
            'local_before': [_identity(r) for r in original.rows],
            'local_after': [_identity(r) for r in changed.rows],
            'remove': remove, 'create': create, 'updates': updates}
    from .project_metadata import native_fbd_pointer_initialization
    initialization = native_fbd_pointer_initialization(metadata[0], after[source_payload_offset(after)])
    if initialization is not None:
        plan['system_variable_initialization'] = initialization
    return plan


def _saved_object_names(before_metadata, before_mapping, saved_metadata, saved_mapping, groups, documents):
    """Follow native child renaming and paired source-folder renumbering."""
    def identities(metadata, mapping):
        rows, _ = current_rows(metadata, 'DSPROJECTDATA', 'D_Projectdata')
        result = {}
        for row in rows:
            values = {key: value.text.strip() for key, value in row.fields().items()}
            name = values.get('szName')
            if name in mapping and mapping[name] == values.get('iID'):
                result[name] = tuple(values.get(key) for key in
                    ('iID', 'ucProductType', 'ucFolderType', 'uiFolderNo', 'ucReserve', 'ucFileType'))
        return result

    before = identities(before_metadata, before_mapping)
    after = identities(saved_metadata, saved_mapping)
    # A first SDK save can register the GUI entry and application defaults.
    # Existing entries still need identity mappings during subsequent saves.
    for name in ('Project.gd2', 'Gppw2.gpj'):
        if name not in before and name in after and all(value == '0' for value in after[name][1:]):
            del after[name]
    # A cold save compacts folder numbers after native POU deletion. The
    # stream IDs and roles persist; a source and its declarations must move
    # together into one current group. A folder number alone is not identity.
    saved_groups = {}
    for folder_type in {int(before[name][2]) for name in groups if name in before}:
        current, _ = project_source_groups(saved_metadata, folder_type=folder_type)
        saved_groups.update(current)
    stable_role = lambda key: key[:3] + key[4:]
    candidates = {}
    for name, key in after.items():
        candidates.setdefault(stable_role(key), []).append(name)
    for program in groups:
        local = local_table_for(documents, program, groups)
        if program not in before or local not in before:
            continue
        body_names = candidates.get(stable_role(before[program]), [])
        label_names = candidates.get(stable_role(before[local]), [])
        if len(body_names) != 1 or len(label_names) != 1:
            continue
        body, labels = body_names[0], label_names[0]
        if saved_groups.get(body, {}).get('labels') != labels:
            continue
        for original, saved in ((program, body), (local, labels)):
            key = after[saved]
            after[saved] = key[:3] + (before[original][3],) + key[4:]
    by_identity = {key: name for name, key in after.items()}
    if len(by_identity) != len(after) or set(before.values()) != set(after.values()):
        raise GXWFormatError('native save changed the registered project object identities')
    names = {name: by_identity[key] for name, key in before.items()}
    allowed = {}
    for program, group in groups.items():
        local = local_table_for(documents, program, groups)
        if local is not None:
            owner = documents[local].owner_name
            allowed[program], allowed[local] = owner + '.Program.pou', owner + '.Labels.lh'
    # Workspace normalizes built-in child names; the visible IDE can restore
    # the two witnessed Function aliases. Roles remain exact and callers still
    # compare complete library payloads rather than normalizing their contents.
    library_names = {
        'UserLibrary.col': ('UserLibraryCollection.col', ('1', '87', '0', '0', '1')),
        'MELSECFunction.lif': ('MELSEC.lif', ('1', '48', '0', '0', '0')),
        'IECFunction.lif': ('IEC.lif', ('1', '64', '0', '0', '0')),
        'MELSEC.lif': ('MELSECFunction.lif', ('1', '48', '0', '0', '0')),
        'IEC.lif': ('IECFunction.lif', ('1', '64', '0', '0', '0')),
    }
    for original, (saved, role) in library_names.items():
        if original in before and before[original][1:] == role:
            allowed[original] = saved
    for original, saved in names.items():
        if original != saved and allowed.get(original) != saved:
            raise GXWFormatError('native save changed an unverified project object name: ' + original)
    return names


def _verify_sfc_rebuild(source, edit, cells, traces):
    """Check requested symbol identities and geometry against original getters."""
    symbols = edit['symbols']
    if (len(traces) != len(symbols) + 1 or traces[0].get('edit') != {'kind': 'clear'}
            or any(row.get('result') != 0 for row in traces)
            or [row.get('edit') for row in traces[1:]] != symbols):
        raise GXWFormatError('native SFC symbol writer calls are incomplete or differ from the request')
    expected_nodes = set()
    branches = {'parallel_branch': 0x90, 'parallel_coupling': 0xa0,
                'selective_branch': 0x50, 'selective_coupling': 0x60}
    connectors = {}
    for symbol in symbols:
        kind = symbol['kind']
        if kind in ('step', 'initial_step', 'end', 'transition'):
            point = symbol['x'], symbol['y']
            expected_nodes.add(point)
            matches = [row for row in cells if (row['x'], row['y']) == point]
            if (len(matches) != 1 or matches[0]['result'] != (2 if kind == 'transition' else 1)
                    or matches[0]['number'] != (999 if kind == 'end' else symbol['number'])
                    or (kind != 'transition' and matches[0]['type'] != {'step': 0, 'initial_step': 6, 'end': 14}[kind])):
                raise GXWFormatError('native SFC rebuilt symbol does not match the requested cell/reference')
        elif kind == 'line':
            for y in range(symbol['start'], symbol['end'] + 1):
                connectors[symbol['x'], y] = 0
        else:
            for x in range(symbol['start'], symbol['end'] + 1):
                connectors[x, symbol['y']] = branches[kind]
    actual_nodes = {(row['x'], row['y']) for row in cells if row['result'] in (1, 2)}
    if actual_nodes != expected_nodes:
        raise GXWFormatError('native SFC rebuilding added or lost a requested node')
    actual_connectors = {(row['x'], row['y']): row['branch'] & 0xf0 for row in cells if row['result'] == 3}
    if actual_connectors != connectors:
        raise GXWFormatError('native SFC rebuilt connector spans/classes differ from the request')
    steps = sorted(row['references'][0] for row in source.graph_records if row['kind'] == 'step-action-reference')
    transitions = sorted(row['references'][0] for row in source.graph_records if row['kind'] == 'transition-reference')
    if (steps != sorted(row['number'] for row in symbols if row['kind'] in ('step', 'initial_step'))
            or transitions != sorted(row['number'] for row in symbols if row['kind'] == 'transition')):
        raise GXWFormatError('native SFC rebuilt source references differ from the requested symbols')


def _native_sfc_graph_output(prepared, plan, graph_body, observation):
    """Frame vendor-generated tokens and verify the requested reference change."""
    from .sfc_pou import parse_sfc_pou
    import struct

    logical = next(iter(prepared.programs))
    wanted = prepared.replacements[logical][2]
    before = parse_sfc_pou(wanted)
    graph = before.layout['graph']
    if (not isinstance(graph_body, bytes) or len(graph_body) < 20 or len(graph_body) > 8 * 1024 * 1024
            or struct.unpack_from('<I', graph_body)[0] != len(graph_body)):
        raise GXWFormatError('native SFC graph output is missing or has an invalid extent')
    wanted = (wanted[:graph['offset'] - 4] + struct.pack('<I', len(graph_body)) + graph_body
              + wanted[graph['offset'] + graph['size']:])
    after = parse_sfc_pou(wanted)
    if after.graph_tokens is None or after.layout['graph']['storage_words'] != [0, 0]:
        raise GXWFormatError('native SFC graph output has unobserved framing')
    from collections import Counter
    rebuilding = [row for row in plan['graph_edits'] if row.get('kind') == 'rebuild']
    actions = [row for row in plan['graph_edits'] if row.get('kind') not in ('jump', 'rebuild')]
    jumps = [row for row in plan['graph_edits'] if row.get('kind') == 'jump']
    edits = {row['step_reference']: row for row in actions}
    # Unknown graph records have no interpreted references. Keep their exact
    # bytes in this comparison so a read/local edit can retain them without
    # inventing a meaning or raising an incidental KeyError.
    semantic = lambda row: (row['kind'], tuple(row['references']) if 'references' in row else (row['raw_hex'],))
    expected = []
    for row in before.graph_records:
        kind, refs = semantic(row)
        if kind == 'step-action-reference' and refs[0] in edits:
            refs = refs[0], edits[refs[0]]['action_number']
        expected.append((kind, refs))
    observed_references = [semantic(row) for row in after.graph_records]
    changes = Counter()
    if not rebuilding and len(observed_references) != len(expected):
        raise GXWFormatError('native SFC graph references differ from the requested edit')
    for old, new in (() if rebuilding else zip(expected, observed_references)):
        if old == new:
            continue
        if old[0] != 'step-reference' or new[0] != old[0] or len(old[1]) != 1 or len(new[1]) != 1:
            raise GXWFormatError('native SFC graph references differ from the requested edit')
        changes[old[1][0], new[1][0]] += 1
    if changes != Counter((row['before_step_reference'], row['target_step_reference']) for row in jumps
                          if row['before_step_reference'] != row['target_step_reference']):
        raise GXWFormatError('native SFC jump references differ from the requested edit')
    events = (observation.get('validation') or {}).get('events', [])
    find = lambda operation: [item for item in events if isinstance(item, dict) and item.get('operation') == operation]
    decoded, reads, checked, encoded, buffers = (find(name) for name in (
        'SfcGraphDecode', 'SfcGraphReadback', 'SfcGraphCheck', 'SfcGraphEncode', 'SfcGraphBuffer'))
    if (len(decoded) != 1 or decoded[0].get('result') != 0 or decoded[0].get('source_exact') is not True
            or len(reads) != 1 or reads[0].get('source_exact') is not True or reads[0].get('bytes') != len(graph_body)
            or len(checked) != 1 or checked[0].get('result') != 0
            or len(encoded) != 1 or encoded[0].get('bytes') != after.layout['graph']['code_size']
            or len(buffers) != 9 or {item.get('index') for item in buffers} != set(range(9))
            or any(item.get('canary_intact') is not True for item in buffers)):
        raise GXWFormatError('native SFC graph conversion or source readback is incomplete')
    identity = decoded[0].get('source_object') or {}
    selected = [item for item in find('NativeBodySelection') if item.get('stage') == 'before-graph-edit'
                and item.get('name') == plan['program_name'] and item.get('program_kind') == 240]
    owner, body, language = (identity.get(key) or {} for key in ('owner', 'body', 'language'))
    if (len(selected) != 1 or selected[0].get('source_object') != identity
            or decoded[0].get('bytes') != graph['size']
            or identity.get('name') != plan['program_name'] or identity.get('status') != 'verified-source-object'
            or (owner.get('read_type') or {}).get('data_type') != 60
            or (body.get('read_type') or {}).get('data_type') != 32
            or (identity.get('body_parent') or {}).get('id') != owner.get('id')
            or language.get('value') != 240 or language.get('expected') != 240):
        raise GXWFormatError('native SFC graph converter has no current typed source identity')
    grids = find('SfcGraphCells')
    initial = [item for item in grids if item.get('stage') == 'before-edit']
    final = [item for item in grids if item.get('stage') == 'after-edit']
    if len(initial) != 1 or len(final) != 1:
        raise GXWFormatError('native SFC graph cells are missing')
    initial, final = initial[0], final[0]
    if not rebuilding and (initial.get('width'), initial.get('height')) != (final.get('width'), final.get('height')):
        raise GXWFormatError('native SFC reference edit changed the grid dimensions')
    originals, modified = initial.get('cells'), final.get('cells')
    if not isinstance(originals, list) or not isinstance(modified, list):
        raise GXWFormatError('native SFC graph cell output is invalid')
    if rebuilding:
        if len(rebuilding) != 1 or len(plan['graph_edits']) != 1:
            raise GXWFormatError('native SFC rebuilding cannot mix reference-only edits')
        _verify_sfc_rebuild(after, rebuilding[0], modified, find('SfcGraphSymbolEdit'))
    native_edits = find('SfcGraphStepEdit')
    if len(native_edits) != len(actions):
        raise GXWFormatError('native SFC step edits are incomplete')
    for edit, trace in zip(actions, native_edits):
        matches = [row for row in originals if row.get('result') == 1 and row.get('type') in (4, 10, 20, 26)
                   and row.get('number') == edit['step_reference']]
        if len(matches) != 1 or matches[0].get('substep') != edit['before_action_number']:
            raise GXWFormatError('native SFC step does not match the current graph reference')
        cell = matches[0]
        changed = [row for row in modified if (row.get('x'), row.get('y')) == (cell.get('x'), cell.get('y'))]
        expected_type = cell['type']
        if (jumps and len(changed) == 1 and changed[0].get('type') in (4, 10, 20, 26)
                and changed[0]['type'] & ~16 == cell['type'] & ~16):
            # The measured original setter may update bit 16 while keeping the
            # same step class. Its lifecycle is not promoted to execution state.
            expected_type = changed[0]['type']
        if (len(changed) != 1 or changed[0] != {**cell, 'type': expected_type, 'substep': edit['action_number']}
                or trace.get('edit') != edit or trace.get('mode') != 1 or trace.get('result') != 0
                or (trace.get('x'), trace.get('y')) != (cell.get('x'), cell.get('y'))):
            raise GXWFormatError('native SFC cell edit was rejected or differs from the request')
    jump_traces = find('SfcGraphJumpEdit')
    if len(jump_traces) != len(jumps):
        raise GXWFormatError('native SFC jump edits are incomplete')
    for edit, trace in zip(jumps, jump_traces):
        matches = [row for row in originals if (row.get('x'), row.get('y')) == (edit['x'], edit['y'])
                   and row.get('result') == 1 and row.get('type') == 15]
        targets = [row for row in originals if row.get('result') == 1 and row.get('type') in (4, 10, 20, 26)
                   and row.get('number') == edit['target_step_reference']]
        if len(matches) != 1 or len(targets) != 1 or matches[0].get('number') != edit['before_step_reference']:
            raise GXWFormatError('native SFC jump does not match the current cell or target')
        changed = [row for row in modified if (row.get('x'), row.get('y')) == (edit['x'], edit['y'])]
        if (len(changed) != 1 or changed[0] != {**matches[0], 'number': edit['target_step_reference']}
                or trace.get('edit') != edit or trace.get('mode') != 1 or trace.get('result') != 0):
            raise GXWFormatError('native SFC jump edit was rejected or differs from the request')
    return wanted, {'status': 'source-bound', 'cpu': plan['cpu'], 'native_version': '1.635.0.1',
                    'edits': plan['graph_edits'], 'before': initial, 'after': final,
                    'encoding': encoded[0], 'graph_check': checked[0],
                    'limits': ['original graph cells; branch values remain raw',
                               'converted-source cache retained; native editor display and PLC execution not observed']}


def verify_native_save(prepared: PreparedProjectWrite, raw: bytes, observation: dict, *,
                       graph_edits=None, graph_body=None) -> ProjectWriteResult:
    """Verify the requested edit and report other changes made by native save."""
    plan = native_source_plan(prepared, graph_edits=graph_edits)
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested = validate_cfb_streams(outer['_hdb'])
    logical = next(iter(prepared.programs))
    sfc = plan.get('program_kind') == 240
    groups, _ = project_source_groups(prepared.outer['projectdatalist.xml'], folder_type=8 if sfc else 7)
    sfc_labels = {group['labels'] for group in groups.values()} if sfc else set()
    documents = {name: parse_declarations(prepared.nested[stream], logical_name=name,
                    source_kind=240 if name in sfc_labels else None)
                 for name, stream in prepared.mapping.items() if name.endswith('.lh') and stream in prepared.nested}
    local = local_table_for(documents, logical, groups)
    names = _saved_object_names(prepared.outer['projectdatalist.xml'], prepared.mapping,
                                outer['projectdatalist.xml'], mapping, groups, documents)
    saved_groups, _ = project_source_groups(outer['projectdatalist.xml'], folder_type=8 if sfc else 7)
    relocated_groups = [{'program': program, 'saved_program': names[program],
                         'declarations': group['labels'], 'saved_declarations': names[group['labels']],
                         'before_folder': group['folder'], 'after_folder': saved_groups[names[program]]['folder']}
                        for program, group in groups.items() if names[program] in saved_groups
                        and group['folder'] != saved_groups[names[program]]['folder']]
    actual_source, wanted_source = nested[mapping[names[logical]]], prepared.replacements[logical][2]
    native_graph = None
    if graph_edits is not None:
        wanted_source, native_graph = _native_sfc_graph_output(prepared, plan, graph_body, observation)
    elif graph_body is not None:
        raise GXWFormatError('native graph output requires explicit source-bound graph edits')
    if sfc:
        from .sfc_pou import frame_sfc_pou

        def source_parts(source):
            layout = frame_sfc_pou(source)
            graph = layout['graph']
            return (source[graph['offset']:graph['offset'] + graph['size']],
                    [(r['kind'], r['name'], r['number'], source[r['payload_offset']:r['tail_offset']])
                     for r in layout['children']],
                    [source[r['record_offset']:r['record_offset'] + r['record_size']] for r in layout['actions']])

        if source_parts(actual_source) != source_parts(wanted_source):
            raise GXWFormatError('saved native SFC graph, child bodies or registration ownership differ')
    elif workspace_body(actual_source) != workspace_body(wanted_source):
        raise GXWFormatError('saved native source differs from the current prepared graph')
    unedited_changes = []
    for name, stream in prepared.mapping.items():
        if name not in (logical, local) and name.endswith(('.pou', '.lh', '.gh', '.str', '.lns', '.lnl', '.lnb', '.lif', '.col', '.prj', '.tsk')):
            before, after = prepared.nested.get(stream), nested.get(mapping[names[name]])
            if before != after:
                unedited_changes.append({'object': name, 'saved_object': names[name],
                    'stream': mapping[names[name]],
                    'kind': 'configuration' if name.endswith(('.prj', '.tsk')) else 'source',
                    'old_length': len(before) if before is not None else None,
                    'new_length': len(after) if after is not None else None,
                    'offset_space': 'logical source stream bytes',
                    'binary_changes': binary_diff(before or b'', after or b'')})
    original = documents[local]
    expected = prepared.declarations.get(local, original)
    actual = parse_declarations(nested[mapping[names[local]]], logical_name=names[local],
                                source_kind=240 if sfc else None)
    if (actual.owner_name, actual.owner_pou_type, actual.owner_return_type) != (
            expected.owner_name, expected.owner_pou_type, expected.owner_return_type):
        raise GXWFormatError('saved native declaration owner differs from the selected POU')
    if len(actual.rows) != len(expected.rows):
        raise GXWFormatError('saved native declaration membership differs from the prepared edit')
    created = {r['name'] for r in plan.get('create', [])}
    typed = {r['after']['name']: r for r in plan.get('updates', [])
             if r['before']['data_type'] != r['after']['data_type']}
    for wanted, saved in zip(expected.rows, actual.rows):
        omit = {'record_id', 'type_code', 'type_reference'} if wanted.name in created else set()
        if wanted.name in typed:
            omit |= {'type_code', 'type_reference'}
        if _values(wanted, omit=omit) != _values(saved, omit=omit):
            raise GXWFormatError('saved native declaration fields differ: ' + wanted.name)
        if wanted.name in created and wanted.type_code == 15 and (saved.type_code, saved.type_reference) != (0, ''):
            raise GXWFormatError('created native FB cache fields differ from the observed layout')
        if (wanted.name in created and wanted.class_code == 1 and wanted.type_code == 0 and wanted.initial_value == ''
                and (saved.type_code, saved.type_reference) != (0, '')):
            raise GXWFormatError('created native root value cache fields differ from the observed layout')
        if wanted.name in created and wanted.class_code in (3, 4, 5):
            if saved.record_id != wanted.record_id or (saved.type_code, saved.type_reference) != (0, ''):
                raise GXWFormatError('created native formal line/cache fields differ from the observed layout')
        if wanted.name in typed:
            old, = [row for row in original.rows if row.name == typed[wanted.name]['before']['name']]
            # SetDataType preserves a project FB's original cache. The earlier
            # BOOL-to-FB lifecycle control starts from the zero-cache layout.
            cache = (0, '') if old.data_type == 'BOOL' else (old.type_code, old.type_reference)
            if (saved.type_code, saved.type_reference) != cache:
                raise GXWFormatError('changed native FB cache fields differ from the observed layout')
    objects = [{'object': name, 'saved_object': names[name], 'stream': mapping[names[name]], 'old_length': len(old),
                'new_length': len(nested[mapping[names[name]]]), 'offset_space': 'logical source stream bytes',
                'binary_changes': binary_diff(old, nested[mapping[names[name]]])}
               for name, (_, old, _) in prepared.replacements.items()]
    report = {'schema_version': 1, 'operation': 'native_source_save', 'objects': objects,
              'native_workspace': observation,
              'registered_object_renames': [{'before': name, 'after': saved}
                                           for name, saved in names.items() if name != saved],
              'registered_object_relocations': relocated_groups,
              'unedited_object_changes': unedited_changes,
              'vendor_changed_streams': [name for name, stream in prepared.mapping.items()
                                         if prepared.nested.get(stream) != nested.get(mapping[names[name]])],
              'validation': {'parser': 'passed', 'source_body': 'exact', 'declarations': 'passed',
                             'unedited_sources': 'changed' if any(row['kind'] == 'source' for row in unedited_changes) else 'preserved',
                             'unedited_configuration': 'changed' if any(row['kind'] == 'configuration' for row in unedited_changes) else 'preserved',
                             'workspace_load': 'passed',
                             'native_save': 'passed', 'saved_file_readback': 'passed', 'gxworks_open': 'not_run',
                             'gxworks_compile': 'not_run', 'gxworks_check': 'not_run',
                             'gxworks_save_reload': 'not_run'},
              'limitations': ['Offline source save; compilation/checking and cold reopen are separate operations',
                              'Existing SFC child bodies; graph and registration ownership retained' if sfc and native_graph is None
                              else 'Original SFC action-reference edits; registration and child ownership retained' if sfc
                              else 'Existing selected POU; observed local declaration lifecycle only']}
    if native_graph is not None:
        report['sfc_graph'] = native_graph
    restored = [row for row in (observation.get('validation') or {}).get('events', [])
                if row.get('operation') == 'NativeSfcRetainedAction']
    if restored:
        report['sfc_retained_sources'] = [{'action_number': row['number'], 'action_name': row['action_name'],
            'name': row['zoom_name'], 'native_source_readback': 'current' if row.get('source_exact') is True else 'unresolved',
            'provenance': row['provenance']} for row in restored]
    return ProjectWriteResult(bytes(raw), report)

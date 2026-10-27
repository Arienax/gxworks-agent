# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Current-project definition plans, independent of imported user libraries."""
from __future__ import annotations

import base64
from dataclasses import replace
import re

from .callable_sources import ProjectCallableSources
from .container_writer import validate_cfb_streams
from .declarations import (BASIC_TYPES, CLASS_CODES, _type_parts, edit_declarations,
                           parse_declarations)
from .models import GXWFormatError
from .native_write import workspace_body
from .project_metadata import project_stream_mapping, read_project_text_context, existing_source_groups
from .project_writer import ProjectWriteResult
from .source_header import source_payload_offset
from .text_pou import parse_st_pou


def normalize_declaration_address_pair(pair, *, cpu):
    """The existing global-label MELSEC/IEC rule, shared by offline edits."""
    from plc.device_policy import device_address
    from plc.validation import parse_device_address
    result = dict(pair)
    if any(not isinstance(value, str) or '\0' in value for value in result.values()):
        raise GXWFormatError('declaration address fields require text without NUL')
    if result.get('device'):
        address = device_address(result['device'], cpu)
        family, number = parse_device_address(address, cpu)
        if family in ('M', 'D') and re.fullmatch(r'[MD][0-9]+', address):
            from .compiler_assignment import iec_device_address
            expected = iec_device_address(family, number)
            if result.get('iec_address') and result['iec_address'] != expected:
                raise GXWFormatError('MELSEC and IEC declaration addresses disagree')
            result['iec_address'] = expected
        elif not result.get('iec_address'):
            raise GXWFormatError('this device spelling requires an explicit IEC declaration address')
    if result.get('iec_address') and not re.fullmatch(r'%[IQM][XBWDL][0-9]+(?:\.[0-9]+)?', result['iec_address'], re.I):
        raise GXWFormatError('unsupported IEC declaration address')
    return result


def _global_snapshot(sources, logical):
    from .declarations import _Reader
    document = sources.declarations.get(logical)
    if document is None or document.scope != 'global':
        raise GXWFormatError('missing current global declaration table: ' + logical)
    rows = []
    for row in sorted(document.rows, key=lambda r:r.record_id):
        item = dict(name=row.name, data_type=row.data_type, class_name={8:'VAR_GLOBAL',9:'VAR_GLOBAL_CONSTANT'}.get(row.class_code,str(row.class_code)),
            kind=sources.declaration_kind(row), initial_value=row.initial_value, comment=row.comment,
            device=row.device, iec_address=row.iec_address)
        if row.unknown_u32 == 1:
            reader = _Reader(row.value_extension)
            count = reader.uint()
            members = sources.structure(_type_parts(row.data_type)[0]).members
            if count != len(members):
                raise GXWFormatError('global structure member-address extent differs from its source type')
            item['member_devices'] = {m.name:dict(device=reader.string(),iec_address=reader.string()) for m in members}
        elif row.unknown_u32:
            raise GXWFormatError('global editing does not yet cover structure-array storage')
        rows.append(item)
    return dict(name=logical.removesuffix('.gh'),kind='global_table',declarations=rows)


def _global_plan(config, sources, command, caller):
    if (set(command)-{'operation','source_name','declarations','device_profile'}
            or not {'operation','source_name','declarations'}<=command.keys() or not isinstance(command['declarations'],list)):
        raise GXWFormatError('edit_global_labels requires source_name and explicit declaration upserts')
    from gxworks2.project_profiles import native_project_profile
    device_profile=command.get('device_profile',config['cpu'])
    profile=native_project_profile(device_profile)
    current_profile=native_project_profile(config['cpu'])
    if profile is None or current_profile is None or profile.cpu!=current_profile.cpu:
        raise GXWFormatError('global device profile differs from current native CPU menu')
    logical = command['source_name'] if command['source_name'].endswith('.gh') else command['source_name']+'.gh'
    create=command['operation']=='create_global_labels'
    if create:
        if logical in sources.declarations:raise GXWFormatError('global declaration table already exists')
        _name(logical.removesuffix('.gh'),config['codepage'])
        before=dict(name=logical.removesuffix('.gh'),kind='global_table',declarations=[])
    else: before = _global_snapshot(sources,logical)
    rows = [dict(row) for row in before['declarations']]
    seen = set()
    from plc.device_policy import device_address
    for supplied in command['declarations']:
        allowed={'name','data_type','class_name','kind','initial_value','comment','device','iec_address','member_devices'}
        if not isinstance(supplied,dict) or set(supplied)-allowed or not {'name','data_type'} <= supplied.keys():
            raise GXWFormatError('invalid global declaration fields')
        name = _name(supplied['name'],config['codepage'])
        if name.casefold() in seen:
            raise GXWFormatError('duplicate global declaration upsert: '+name)
        seen.add(name.casefold())
        old = next((row for row in rows if row['name'].casefold()==name.casefold()),None)
        base,bounds = _type_parts(supplied['data_type'])
        if len(bounds)>1 or any(not -2**31 <= a <= b < 2**31 for a,b in bounds):
            raise GXWFormatError('global declarations require scalar or rank-one types')
        kind = supplied.get('kind',old['kind'] if old else 'variable')
        members = None
        if base in BASIC_TYPES:
            if kind!='variable': raise GXWFormatError('basic global requires variable kind')
            initial = BASIC_TYPES[base][1] if not bounds else ''
        elif kind=='function_block':
            sources.definition(base,'FUNCTION_BLOCK')
            if bounds: raise GXWFormatError('global FB instances must be scalar')
            initial=''
        elif kind=='structure':
            if bounds: raise GXWFormatError('global structure arrays are not covered by this entry')
            members=sources.structure(base).members
            initial=''
        else:
            raise GXWFormatError('global nonbasic type requires source-bound structure or function_block kind')
        item = {**(old or dict(name=name,device='',iec_address='',comment='',initial_value=initial)), **supplied, 'kind':kind}
        item.setdefault('class_name','VAR_GLOBAL')
        if item['class_name'] not in ('VAR_GLOBAL','VAR_GLOBAL_CONSTANT'):
            raise GXWFormatError('global declaration requires a global class')
        if members is not None:
            requested = item.get('member_devices',{})
            if not isinstance(requested,dict) or set(requested)-{m.name for m in members}:
                raise GXWFormatError('global device mappings must use current structure member names')
            item['member_devices']={m.name:dict(device='',iec_address='') for m in members}
            for member,pair in requested.items():
                if isinstance(pair,str):pair=dict(device=pair)
                if not isinstance(pair,dict) or set(pair)-{'device','iec_address'} or 'device' not in pair:
                    raise GXWFormatError('member mapping requires a device or a device/IEC pair')
                item['member_devices'][member]=dict(device=pair['device'],iec_address=pair.get('iec_address',''))
        elif 'member_devices' in item:
            raise GXWFormatError('member mappings require a scalar structure')
        for pair in [item,*item.get('member_devices',{}).values()]:
            pair.update(normalize_declaration_address_pair({key: pair.get(key, '') for key in ('device','iec_address')},
                                                           cpu=device_profile))
        if any(not isinstance(v,str) or '\0' in v for k,v in item.items() if k!='member_devices'):
            raise GXWFormatError('global declaration fields require text without NUL')
        if old is not None: rows[rows.index(old)]=item
        else: rows.append(item)
    native=[]
    for index,row in enumerate(rows):
        native.append(dict(name=row['name'],data_type=row['data_type'],class_code=CLASS_CODES[row['class_name']],
            initial_value=row['initial_value'],comment=row['comment'],line=index+1,
            device=row['device'],iec_address=row['iec_address'],
            devices=[[p['device'],p['iec_address']] for p in row.get('member_devices',{}).values()]))
    plan=dict(cpu=config['cpu'],codepage=config['codepage'],operation=command['operation'],source_name=before['name'],create_table=create,
        before_rows=[dict(name=row['name'],data_type=row['data_type'],class_code=CLASS_CODES[row['class_name']]) for row in before['declarations']],rows=native)
    return plan,before,rows,caller


def _pou_labels_plan(config, streams, groups, sources, command, caller):
    if set(command)!={'operation','source_name','declarations'} or not isinstance(command['declarations'],list):
        raise GXWFormatError('edit_pou_labels requires source_name and declaration upserts')
    matched=[(logical,group,sources.declarations[group['labels']]) for logical,group in groups.items()
             if group['labels'] in sources.declarations and sources.declarations[group['labels']].owner_name==command['source_name']]
    if len(matched)!=1:
        raise GXWFormatError('missing or ambiguous project POU declaration owner')
    logical,group,document=matched[0]
    if document.owner_pou_type not in (0x1000000,0x1000002):
        raise GXWFormatError('POU label editing requires a Program or FB')
    seen=set()
    for row in command['declarations']:
        if (not isinstance(row,dict) or not {'name','data_type'}<=row.keys()
                or set(row)-{'name','data_type','class_name','kind','initial_value','comment'}):
            raise GXWFormatError('POU declarations require name and data_type')
        if row['name'].casefold() in seen: raise GXWFormatError('duplicate POU declaration upsert')
        seen.add(row['name'].casefold())
        base,bounds=_type_parts(row['data_type'])
        if len(bounds)>1: raise GXWFormatError('POU label editing covers rank-one arrays')
        if base not in BASIC_TYPES:
            if row.get('kind')=='function_block':
                sources.definition(base,'FUNCTION_BLOCK')
                if bounds or row.get('class_name','VAR')!='VAR':
                    raise GXWFormatError('FB instances require scalar local VAR')
            elif row.get('kind')=='structure': sources.structure(base)
            else: raise GXWFormatError('nonbasic POU labels require an explicit source-bound kind')
    changed=edit_declarations(document,upserts=command['declarations'],native_fbd_locals=True)
    expected=_rows(changed,sources)
    kind=streams[logical][source_payload_offset(streams[logical])]
    if kind not in (193,208): raise GXWFormatError('POU label authoring covers ST and FBD sources')
    plan=dict(cpu=config['cpu'],codepage=config['codepage'],operation='edit_pou_labels',source_name=document.owner_name,
        source_language={193:'st',208:'fbd'}[kind],before_body=base64.b64encode(workspace_body(streams[logical])).decode('ascii'),
        before_rows=[dict(name=r.name,data_type=r.data_type,class_code=r.class_code) for r in document.rows],
        rows=[dict(name=r['name'],data_type=r['data_type'],class_code=CLASS_CODES[r['class_name']],
            initial_value=r['initial_value'],comment=r['comment'],line=index+1) for index,r in enumerate(expected)])
    return plan,dict(name=document.owner_name,kind='pou_labels',program=logical,declarations=_rows(document,sources)),expected,caller


def validate_configuration_ranges(edits, *, cpu, known_devices):
    """Validate allocation bounds without requiring an editable workspace."""
    if not isinstance(edits, dict) or set(edits) - set(known_devices):
        raise GXWFormatError('range changes require known device classes')
    normalized = {}
    for key, pair in edits.items():
        if pair is None:
            pair = [0xffffffff, 0xffffffff]
        if (not isinstance(pair, list) or len(pair) != 2 or any(type(v) is not int for v in pair)
                or not (pair == [0xffffffff, 0xffffffff] or 0 <= pair[0] <= pair[1] < 2**31)):
            raise GXWFormatError('system variable range requires ordered integer bounds or null')
        if cpu == 'FX3U/FX3UC' and key == 'D' and pair != [0xffffffff, 0xffffffff] and pair[1] > 7999:
            raise GXWFormatError('FX3U allocation range exceeds D7999')
        normalized[key] = tuple(pair)
    return normalized


def _configuration_plan(config,streams,command,caller):
    from .project_metadata import read_project_system_variables
    if set(command)!={'operation','source_name','options'} or not isinstance(command['options'],dict):
        raise GXWFormatError('configure_project requires source_name and options')
    if not command['options'] or set(command['options'])-{'system_variable_ranges','program_capacity'}:
        raise GXWFormatError('configure_project accepts system_variable_ranges and observed FX3U program_capacity')
    name=command['source_name']
    if name not in streams or not name.endswith('.prj'):
        raise GXWFormatError('configuration requires the current project metadata identity')
    before=read_project_system_variables(streams[name])['ranges']
    edits=validate_configuration_ranges(command['options'].get('system_variable_ranges',{}),
        cpu=config['cpu'],known_devices=before)
    after=dict(before);ranges=[]
    for key,pair in edits.items():
        after[key]=tuple(pair)
        signed=lambda values:[-1 if v==0xffffffff else v for v in values]
        ranges.append(dict(device=key,before=signed(before[key]),after=signed(pair)))
    snapshot=lambda values:dict(name=name,kind='project_configuration',system_variable_ranges={k:list(v) for k,v in values.items()})
    original,expected=snapshot(before),snapshot(after)
    plan=dict(cpu=config['cpu'],codepage=config['codepage'],operation='configure_project',source_name=name,ranges=ranges)
    if 'program_capacity' in command['options']:
        capacity=command['options']['program_capacity']
        parameter=streams.get('Param.wpa',b'')
        # The original FX3U GUI 16K/64K control changes this one field;
        # all other 1099 parameter bytes are retained from the current file.
        if (config['cpu']!='FX3U/FX3UC' or len(parameter)!=1100 or parameter[:4]!=bytes.fromhex('40002201')
                or parameter[704] not in (16,64) or type(capacity)is not int or capacity not in (16000,64000)):
            raise GXWFormatError('program capacity change requires the observed FX3U 16K/64K parameter layout')
        replacement=bytearray(parameter);replacement[704]=capacity//1000
        plan.update(parameter_before=base64.b64encode(parameter).decode('ascii'),parameter_after=base64.b64encode(replacement).decode('ascii'))
        original['program_capacity']=parameter[704]*1000;expected['program_capacity']=capacity
    return plan,original,expected,caller


def _context(raw, *, require_fbd=False, allow_source_gaps=False):
    from .integrity import read_project_hdb
    outer = validate_cfb_streams(raw)
    nested = read_project_hdb(outer).payloads
    mapping = project_stream_mapping(outer)
    streams = {name: nested[key] for name, key in mapping.items() if key in nested}
    configs = [read_project_text_context(value) for name, value in streams.items() if name.endswith('.prj')]
    if len(configs) != 1:
        raise GXWFormatError('definition authoring requires one project CPU configuration')
    groups, issues = existing_source_groups(outer['projectdatalist.xml'], streams)
    callers = [name for name in groups if name in streams
               and streams[name][source_payload_offset(streams[name])] == 208]
    if not callers and not require_fbd:
        callers = [name for name in groups if name in streams
                   and streams[name][source_payload_offset(streams[name])] == 193]
    if not callers and not require_fbd:
        callers = [name for name in groups if name in streams
                   and streams[name][source_payload_offset(streams[name])] == 1]
    if not callers and not require_fbd:
        callers = [name for name in groups if name in streams
                   and streams[name][source_payload_offset(streams[name])] == 240]
    if (issues and not allow_source_gaps) or not callers:
        raise GXWFormatError('definition authoring requires a current FBD caller' if require_fbd
                             else 'definition authoring requires a current Ladder/ST/FBD/SFC source with declarations')
    sources = ProjectCallableSources.from_project(raw, callers[0])
    return configs[0], streams, groups, sources, callers[0]


def _name(name, codepage):
    if not isinstance(name, str) or len(name) > 255 or not re.fullmatch(r'[^\W\d]\w*', name, re.UNICODE):
        raise GXWFormatError('invalid project definition name')
    try:
        name.encode('cp' + str(codepage))
    except (LookupError, UnicodeError) as error:
        raise GXWFormatError('definition name cannot be represented by the project codepage') from error
    return name


def _rows(document, sources=None):
    names = {value: key for key, value in CLASS_CODES.items()}
    return [dict(name=row.name, data_type=row.data_type, class_name=names.get(row.class_code,str(row.class_code)),
                 initial_value=row.initial_value, comment=row.comment,
                 **({'kind': sources.declaration_kind(row)} if sources else {}))
            for row in sorted(document.rows, key=lambda row: (row.record_id, row.class_code))]


def _definition_rows(config, sources, document, declarations, *, structure=False):
    """Use the existing declaration editor for a complete local row set."""
    if not isinstance(declarations, list) or len(declarations) > 8192 or structure and not declarations:
        raise GXWFormatError('definition requires explicit declarations or nonempty members')
    supplied, seen = [], set()
    for row in declarations:
        allowed = {'name', 'data_type', 'initial_value', 'comment'}
        if not structure:
            allowed |= {'class_name', 'kind'}
        if not isinstance(row, dict) or set(row) - allowed or not {'name', 'data_type'} <= row.keys():
            raise GXWFormatError('invalid definition declaration fields')
        _name(row['name'], config['codepage'])
        if row['name'].casefold() in seen:
            raise GXWFormatError('duplicate definition member: ' + row['name'])
        seen.add(row['name'].casefold())
        base_type, bounds = _type_parts(row['data_type'])
        if len(bounds) > 1 or any(not -2**31 <= low <= high < 2**31 for low, high in bounds):
            raise GXWFormatError('definition creation currently supports rank-one arrays')
        item = dict(row)
        if base_type not in BASIC_TYPES:
            if item.get('kind') == 'function_block':
                sources.definition(base_type, 'FUNCTION_BLOCK')
                if bounds or item.get('class_name', 'VAR') != 'VAR' or structure:
                    raise GXWFormatError('FB instance declarations require a scalar local VAR')
            else:
                sources.structure(base_type)
                item['kind'] = 'structure'
        if structure:
            item['class_name'] = 'VAR'
        else:
            item.setdefault('class_name', 'VAR')
            if item['class_name'] not in ('VAR', 'VAR_CONSTANT', 'VAR_INPUT', 'VAR_OUTPUT', 'VAR_IN_OUT'):
                raise GXWFormatError('project POU declaration requires a local variable class')
        supplied.append(item)
    return _rows(edit_declarations(replace(document, rows=()), upserts=supplied,
                                   native_fbd_locals=True), sources)


def _st_implementation(text):
    if not isinstance(text, str) or '\0' in text:
        raise GXWFormatError('ST implementation text is required without embedded NUL')
    try:
        if len(text.encode('utf-16le')) > 8 * 1024 * 1024 - 18:
            raise GXWFormatError('ST implementation exceeds the native body bound')
    except UnicodeError as error:
        raise GXWFormatError('ST implementation must be valid UTF-16 text') from error
    return text


def _native_rows(rows):
    return [dict(name=row['name'], data_type=row['data_type'],
        class_code=CLASS_CODES[row['class_name']], initial_value=row['initial_value'],
        comment=row['comment'], line=index + 1) for index, row in enumerate(rows)]


def _visible_st_declaration_rows(rows):
    """The observed GUI adds one space after ARRAY; retain the rest exactly."""
    result = []
    for row in rows:
        item = dict(row)
        data_type = item['data_type']
        if re.match(r'^ARRAY ?\[', data_type):
            _type_parts(data_type)
            item['data_type'] = re.sub(r'^ARRAY ?(?=\[)', 'ARRAY ', data_type)
        result.append(item)
    return result


def definition_snapshot(raw, name=None, *, require_fbd=False):
    config, streams, groups, sources, caller = _context(raw, require_fbd=require_fbd)
    definitions = []
    for logical, group in groups.items():
        document = sources.declarations.get(group['labels'])
        if document is None or document.owner_pou_type != 0x1000002:
            continue
        language = {193: 'st', 208: 'fbd'}.get(streams[logical][source_payload_offset(streams[logical])])
        interface = (sources.fixed_interface(document.owner_name) if any(row.class_code in (3, 4, 5) for row in document.rows)
                     else {'source_stream': group['labels'], 'inputs': [], 'outputs': []})
        entry = dict(name=document.owner_name, kind='function_block', language=language,
                     program=logical, declarations=_rows(document, sources), interface=interface)
        if language == 'st':
            source = parse_st_pou(streams[logical])
            if source.text is None:
                raise GXWFormatError('project ST FB text is opaque: ' + document.owner_name)
            entry['st_text'] = source.text
        elif language == 'fbd':
            from .object_model import read_project_context
            entry['model'] = read_project_context(raw, logical).object_model()
        definitions.append(entry)
    for type_name, documents in sources.project_structures.items():
        if len(documents) != 1:
            raise GXWFormatError('ambiguous current project structure: ' + type_name)
        document, = documents
        definitions.append(dict(name=document.logical_name.removesuffix('.str'), kind='structure', members=[
            dict(name=m.name, data_type=m.data_type, initial_value=m.initial_value, comment=m.comment)
            for m in document.members]))
    if name is not None:
        definitions = [row for row in definitions if row['name'].casefold() == name.casefold()]
        if len(definitions) != 1:
            raise GXWFormatError('missing or ambiguous current project definition: ' + name)
    return dict(cpu=config['cpu'], definitions=definitions, caller=caller,
                compilation='not_run', execution='not_run')


def st_program_snapshot(raw, source_name=None):
    """Read ordinary ST Programs and their current source task membership.

    This also works in ST-only projects. A Program never acquires an FB
    interface merely because its declarations use the same table layout.
    """
    from .native_write import _native_scope_inventory
    config, streams, groups, sources, _ = _context(raw, require_fbd=False)
    resources, tasks = _native_scope_inventory(raw)
    programs, owners = [], set()
    for logical, group in groups.items():
        document = sources.declarations.get(group['labels'])
        if logical not in streams or document is None or sources.source_groups.get(logical) != group:
            raise GXWFormatError('ST Program requires current paired source and declaration ownership')
        owner = document.owner_name
        if not owner or owner.casefold() in owners:
            raise GXWFormatError('ambiguous current project POU owner')
        owners.add(owner.casefold())
        if (document.owner_pou_type != 0x1000000
                or streams[logical][source_payload_offset(streams[logical])] != 193):
            continue
        source = parse_st_pou(streams[logical])
        if source.text is None:
            raise GXWFormatError('project ST Program text is opaque: ' + owner)
        bindings = [dict(resource=task['resource'], task=task['task'], line=row['line'])
                    for task in tasks for row in task['programs'] if row['name'].casefold() == owner.casefold()]
        if len(bindings) > 1:
            raise GXWFormatError('ordinary ST Program has ambiguous task membership: ' + owner)
        programs.append(dict(name=owner, kind='program', language='st', program=logical,
            labels=group['labels'], declarations=_rows(document, sources), st_text=source.text,
            task_bindings=bindings))
    if source_name is not None:
        if not isinstance(source_name, str):
            raise GXWFormatError('ST Program selection requires its current owner name')
        programs = [row for row in programs if row['name'].casefold() == source_name.casefold()]
        if len(programs) != 1:
            raise GXWFormatError('missing or ambiguous current ordinary ST Program: ' + source_name)
    return dict(cpu=config['cpu'], programs=programs, resources=[name[:-4] for name in resources],
                tasks=tasks, compilation='not_run', execution='not_run')


def st_program_authoring_plan(raw, command):
    """Prepare ordinary ST Program edits for native pages or isolated saves.

    New Programs require an explicit existing task. Creating task/resource
    objects is a separate native operation and has no implicit default here.
    """
    from .native_write import native_program_creation_plan
    if not isinstance(command, dict) or command.get('operation') not in ('create_st_program', 'edit_st_program'):
        raise GXWFormatError('unsupported ordinary ST Program operation')
    create = command['operation'] == 'create_st_program'
    allowed = {'operation', 'source_name', 'declarations', 'st_text'}
    if create:
        allowed |= {'resource', 'task', 'scope_mode'}
    if set(command) - allowed:
        raise GXWFormatError('unknown ST Program authoring fields')
    config, streams, groups, sources, caller = _context(raw, require_fbd=False)
    inventory = st_program_snapshot(raw)
    before = None
    if create:
        name = _name(command.get('source_name'), config['codepage'])
        owners = [document.owner_name for logical, group in groups.items()
                  if (document := sources.declarations.get(group['labels'])) is not None]
        if (any(owner and owner.casefold() == name.casefold() for owner in owners)
                or name.casefold() in sources.catalog or name.casefold() in sources.project_structures
                or any(key[1] == name.casefold() for key in sources.library_structures)):
            raise GXWFormatError('project callable, Program or type already exists: ' + name)
        if command.get('scope_mode') != 'existing_task':
            raise GXWFormatError('ST Program creation requires explicit existing_task scope')
        scope = native_program_creation_plan(raw, name, resource=command.get('resource'),
                                            task=command.get('task'), scope_mode='existing_task')
        template = sources.declarations[groups[caller]['labels']]
        document = replace(template, owner_name=name, owner_pou_type=0x1000000,
                           owner_return_type=None, rows=())
    else:
        if not isinstance(command.get('source_name'), str):
            raise GXWFormatError('ST Program selection requires its current owner name')
        before, = st_program_snapshot(raw, command['source_name'])['programs']
        name = before['name']
        document = sources.declarations[before['labels']]
        scope = {}
    keep_declarations = not create and command.get('declarations') is None
    expected = before['declarations'] if keep_declarations else _definition_rows(
        config, sources, document, command.get('declarations'))
    plan = dict(scope)
    plan.update(cpu=config['cpu'], codepage=config['codepage'], operation=command['operation'],
        source_name=name, source_language='st', pou_type=0x1000000, program_kind=193,
        write_declarations=not keep_declarations, rows=_native_rows(expected),
        st_text=_st_implementation(command.get('st_text')), tasks=inventory['tasks'])
    if before is not None:
        plan.update(program=before['program'], before_body=base64.b64encode(
            workspace_body(streams[before['program']])).decode('ascii'),
            before_rows=[dict(name=row['name'], data_type=row['data_type'],
                             class_code=CLASS_CODES[row['class_name']]) for row in before['declarations']])
    else:
        selected, = [task for task in inventory['tasks']
                     if task['resource'] == scope['resource'] and task['task'] == scope['task']]
        plan['line'] = 1 + max([0] + [row['line'] for row in selected['programs']])
    return plan, before, expected, caller


def _visible_task_append_header(before, after, *, cpu, native_identity=None):
    """Bound measured task-append save metadata to the actual native context.

    Only the selected appended task may update its common SYSTEMTIME and
    compile-pending word. The source description, all other prefix bytes and
    all task payload bytes are still checked by verify_task_source_append.
    """
    from .project_metadata import read_task_source
    from .source_header import normalize_visible_save_header
    previous, saved = read_task_source(before), read_task_source(after)
    normalized, observed = normalize_visible_save_header(previous['header'], saved['header'], cpu=cpu,
        native_identity=native_identity, operation='task_append')
    return normalized + bytes(after[len(saved['header']):]), observed


def verify_st_program_readback(baseline, command, saved):
    """Check saved source/task bytes independently of UI or offline call logs.

    A passing result establishes the selected source edit and preservation;
    native compilation, GUI completion and execution need their own evidence.
    """
    from .native_write import _native_scope_inventory, _values
    from .project_metadata import verify_task_source_append
    plan, before, expected, _ = st_program_authoring_plan(baseline, command)
    previous = _context(baseline, require_fbd=False)
    current = _context(saved, require_fbd=False)
    if (previous[0]['cpu'], previous[0]['codepage']) != (current[0]['cpu'], current[0]['codepage']):
        raise GXWFormatError('ST Program save changed the project CPU or codepage')
    actual, = st_program_snapshot(saved, plan['source_name'])['programs']
    normalize = lambda text: text.replace('\r\n', '\n').replace('\r', '\n')
    declarations = actual['declarations']
    expected_declarations = expected
    if plan['write_declarations'] and plan['cpu'] == 'FX3U/FX3UC':
        declarations, expected_declarations = map(_visible_st_declaration_rows, (declarations, expected))
    if declarations != expected_declarations or normalize(actual['st_text']) != normalize(plan['st_text']):
        raise GXWFormatError('ST Program readback differs from the requested declarations or implementation')

    def source_inventory(context):
        _, streams, groups, sources, _ = context
        rows = {}
        for logical, group in groups.items():
            document = sources.declarations[group['labels']]
            source = streams[logical]
            kind = source[source_payload_offset(source)]
            if kind == 193:
                text = parse_st_pou(source).text
                if text is None:
                    raise GXWFormatError('ST Program preservation requires readable current source text')
                body = normalize(text)
            elif kind == 240:
                from .diagnostic_sources import _native_validation_source_body, _native_validation_sfc_children
                body = (_native_validation_source_body(source), _native_validation_sfc_children(source), source[-24:])
            elif kind in (1, 208):
                body = (workspace_body(source), source[-24:])
            else:
                raise GXWFormatError('ST Program preservation covers current Ladder/ST/FBD/SFC sources; kind=' + str(kind))
            values = sorted((_values(row, omit=('record_id',)) for row in document.rows),
                            key=lambda row: (row['name'], row['class_code']))
            rows[document.owner_name] = dict(kind=kind, body=body, pou_type=document.owner_pou_type,
                return_type=document.owner_return_type, declarations=values)
        return rows

    left, right = source_inventory(previous), source_inventory(current)
    target = plan['source_name']
    if {name: row for name, row in left.items() if name != target} != {
            name: row for name, row in right.items() if name != target}:
        raise GXWFormatError('ST Program save changed an unrelated POU or its declarations')
    if before is not None and not plan['write_declarations'] and left[target]['declarations'] != right[target]['declarations']:
        raise GXWFormatError('ST Program body edit changed retained local declarations')
    if before is not None and plan['write_declarations']:
        old_rows = {row['name'].casefold(): row for row in left[target]['declarations']}
        for row in right[target]['declarations']:
            old = old_rows.get(row['name'].casefold())
            if old is not None and any(old[key] != row[key] for key in (
                    'device', 'iec_address', 'unknown_u32', 'unknown_text', 'value_extension')):
                raise GXWFormatError('ST Program declaration edit changed retained addresses or unknown fields')
    for logical, document in previous[3].declarations.items():
        if document.scope == 'global' or logical.endswith('.lnl'):
            if logical not in current[1] or previous[1][logical] != current[1][logical]:
                raise GXWFormatError('ST Program save changed an unrelated global/library declaration')
    old_resources, old_tasks = _native_scope_inventory(baseline)
    new_resources, new_tasks = _native_scope_inventory(saved)
    if {name: row['tasks'] for name, row in old_resources.items()} != {
            name: row['tasks'] for name, row in new_resources.items()}:
        raise GXWFormatError('ST Program save changed resource/task ownership')
    if before is None:
        expected_tasks = [{**task, 'programs': task['programs'] + ([{'name': target, 'line': plan['line']}]
            if (task['resource'], task['task']) == (plan['resource'], plan['task']) else [])} for task in old_tasks]
    else:
        expected_tasks = old_tasks
    if new_tasks != expected_tasks:
        raise GXWFormatError('ST Program save changed the explicit task plan or existing task configuration')
    task_header_changes = []
    for resource in old_resources.values():
        for logical in resource['tasks']:
            if before is None and logical == plan['task'] + '.tsk':
                task, header = _visible_task_append_header(previous[1][logical], current[1][logical], cpu=plan['cpu'])
                if header is not None:
                    task_header_changes.append(dict(task=logical, **header))
                verify_task_source_append(previous[1][logical], task,
                                          program_name=target, line=plan['line'])
            elif previous[1][logical] != current[1][logical]:
                raise GXWFormatError('ST Program save changed unedited task bytes')
    return dict(operation=command['operation'], program=actual, task_header_changes=task_header_changes,
        validation=dict(source_readback='matched', unedited_sources='preserved',
                        task_configuration='exact', task_membership='matched',
                        compilation='not_run', execution='not_run'))


def authoring_plan(raw, command, *, require_fbd=False):
    """Validate engineering rows with the existing declaration/type contracts."""
    config, streams, groups, sources, caller = _context(raw, require_fbd=require_fbd)
    operation = command.get('operation')
    if operation == 'remove_empty_program':
        from .native_write import _native_project_source_inventory, _native_scope_inventory
        from .object_model import read_project_context
        if set(command) != {'operation','source_name'} or command['source_name'] not in groups:
            raise GXWFormatError('empty Program removal requires its current logical source identity')
        logical = command['source_name']; document = sources.declarations[groups[logical]['labels']]
        graph = read_project_context(raw,logical).program
        if (document.owner_pou_type != 0x01000000 or document.rows or graph.nodes or graph.wires
                or graph.unknown_records):
            raise GXWFormatError('only an empty ordinary FBD Program without declarations may be removed')
        remaining = [name for name in groups if name!=logical and streams[name][source_payload_offset(streams[name])]==208]
        if not remaining:
            raise GXWFormatError('empty Program removal requires a remaining FBD caller')
        _, programs = _native_project_source_inventory(raw)
        _, tasks = _native_scope_inventory(raw)
        name = document.owner_name
        plan = dict(cpu=config['cpu'],codepage=config['codepage'],operation=operation,source_name=name,
            before_body=base64.b64encode(workspace_body(streams[logical])).decode('ascii'),programs=programs,tasks=tasks)
        before = dict(name=name,kind='empty_program',program=logical)
        expected = dict(name=name,kind='removed_program',program=logical)
        return plan,before,expected,remaining[0]
    if operation in ('edit_global_labels','create_global_labels'):
        return _global_plan(config,sources,command,caller)
    if operation == 'edit_pou_labels':
        return _pou_labels_plan(config,streams,groups,sources,command,caller)
    if operation == 'configure_project':
        return _configuration_plan(config,streams,command,caller)
    if operation not in ('create_structure', 'create_st_fb', 'edit_st_fb'):
        raise GXWFormatError('unsupported project definition operation')
    allowed = {'operation', 'source_name', 'members'} if operation == 'create_structure' else {'operation', 'source_name', 'declarations', 'st_text'}
    if set(command) - allowed:
        raise GXWFormatError('unknown project definition fields')
    name = _name(command.get('source_name'), config['codepage'])
    existing = definition_snapshot(raw, require_fbd=require_fbd)['definitions']
    matched = [row for row in existing if row['name'].casefold() == name.casefold()]
    before = None
    if operation == 'edit_st_fb':
        if len(matched) != 1 or matched[0]['kind'] != 'function_block' or matched[0]['language'] != 'st':
            raise GXWFormatError('ST editing requires an existing project ST FB')
        before, = matched
        name = before['name']
    elif matched or name.casefold() in sources.catalog or any(
            key[1] == name.casefold() for key in sources.library_structures):
        raise GXWFormatError('project callable or type already exists: ' + name)
    declarations = command.get('members') if operation == 'create_structure' else command.get('declarations')
    if declarations is None and before is not None:
        declarations = before['declarations']
    if not isinstance(declarations, list) or len(declarations) > 8192 or operation == 'create_structure' and not declarations:
        raise GXWFormatError('definition requires explicit declarations or nonempty members')
    keep_declarations = before is not None and command.get('declarations') is None
    if keep_declarations:
        normalized = before['declarations']
    else:
        base = sources.declarations[groups[caller]['labels']]
        document = replace(base, owner_name=name, owner_pou_type=0x1000002, rows=())
        normalized = _definition_rows(config, sources, document, declarations,
                                      structure=operation == 'create_structure')
    st_text = command.get('st_text')
    if operation != 'create_structure':
        _st_implementation(st_text)
    elif st_text is not None or command.get('declarations') is not None:
        raise GXWFormatError('structure creation accepts members, not an FB implementation')
    plan = dict(cpu=config['cpu'], codepage=config['codepage'], operation=operation,
                source_name=name, write_declarations=not keep_declarations, rows=_native_rows(normalized))
    if st_text is not None:
        plan['st_text'] = st_text
    if before is not None:
        plan['before_body'] = base64.b64encode(workspace_body(streams[before['program']])).decode('ascii')
        plan['before_rows'] = [dict(name=row['name'], data_type=row['data_type'], class_code=CLASS_CODES[row['class_name']])
                               for row in before['declarations']]
    return plan, before, normalized, caller


def verify_authoring(raw, command, saved, observation):
    plan, before, expected, caller = authoring_plan(raw, command)
    calls = observation.get('calls')
    global_edit=command['operation'] in ('edit_global_labels','create_global_labels')
    pou_edit=command['operation']=='edit_pou_labels'
    configure=command['operation']=='configure_project'
    remove=command['operation']=='remove_empty_program'
    required = {'OpenProjectEX2', 'SaveProject', 'ExportOneFileProject',
                'Definition.RemoveEmptyProgram' if remove else 'Definition.ConfigureProject' if configure else 'Definition.GetGlobalTable' if global_edit else 'Definition.EditPOULabels' if pou_edit
                else 'Definition.GetOwner' if command['operation'] == 'edit_st_fb' else 'Definition.Create'}
    if command['operation'] != 'create_structure' and not global_edit and not pou_edit and not configure and not remove:
        required.add('Definition.SetSTBody')
    if (observation.get('status') != 'saved' or observation.get('native_version') != '1.635.0.1'
            or type(observation.get('protocol_version')) is not int or observation['protocol_version'] != 1
            or observation.get('cpu') != plan['cpu']
            or observation.get('compile') != 'not_requested' or observation.get('check') != 'not_requested'
            or not isinstance(calls, list) or any(not isinstance(row, dict) or type(row.get('hresult')) is not int
                or row['hresult'] < 0 or type(row.get('code')) is not int or row['code'] != 0 for row in calls)
            or not required.issubset(row.get('operation') for row in calls)):
        raise GXWFormatError('definition save observation is incomplete or belongs to another CPU/version')
    if remove:
        from .native_write import _native_project_source_inventory, _native_scope_inventory
        after_config,programs=_native_project_source_inventory(saved)
        remaining=[row for row in plan['programs'] if row['name']!=plan['source_name']]
        if after_config['cpu']!=plan['cpu'] or sorted(programs,key=lambda r:r['name'])!=sorted(remaining,key=lambda r:r['name']):
            raise GXWFormatError('empty Program removal changed another current source')
        _,tasks=_native_scope_inventory(saved)
        expected_tasks=[{**task,'programs':[row for row in task['programs'] if row['name']!=plan['source_name']]} for task in plan['tasks']]
        if tasks!=expected_tasks:
            raise GXWFormatError('empty Program removal changed another task selection or configuration')
        return ProjectWriteResult(saved,dict(operation='native_definition_authoring',definition_before=before,
            definition_after=expected,caller=caller,native_workspace=observation,
            validation={'native_save':'passed','definition_readback':'matched','gx_compile':'not_run'}))
    if configure:
        after_config,streams,_,_,_=_context(saved)
        from .project_metadata import read_project_system_variables
        candidates=[name for name in streams if name.endswith('.prj')]
        if len(candidates)!=1:raise GXWFormatError('saved project metadata is ambiguous')
        definition=dict(name=candidates[0],kind='project_configuration',system_variable_ranges={k:list(v)
            for k,v in read_project_system_variables(streams[candidates[0]])['ranges'].items()})
        if 'parameter_after' in plan:
            if streams.get('Param.wpa')!=base64.b64decode(plan['parameter_after']):
                raise GXWFormatError('saved FX3U parameters differ from the requested capacity field')
            definition['program_capacity']=streams['Param.wpa'][704]*1000
        if definition['system_variable_ranges']!=expected['system_variable_ranges'] or after_config['cpu']!=plan['cpu']:
            raise GXWFormatError('configuration readback differs from requested source-bound changes')
        return ProjectWriteResult(saved,dict(operation='native_definition_authoring',definition_before=before,
            definition_after=definition,caller=caller,native_workspace=observation,
            validation={'native_save':'passed','definition_readback':'matched','gx_compile':'not_run'}))
    if global_edit or pou_edit:
        after_config,after_streams,after_groups,sources,_=_context(saved)
        if global_edit: definition=_global_snapshot(sources,plan['source_name']+'.gh')
        else:
            matched=[(logical,group,sources.declarations[group['labels']]) for logical,group in after_groups.items()
                if sources.declarations[group['labels']].owner_name==plan['source_name']]
            if len(matched)!=1:raise GXWFormatError('saved POU declaration owner differs')
            logical,group,document=matched[0]
            if workspace_body(after_streams[logical])!=base64.b64decode(plan['before_body']):
                raise GXWFormatError('POU label edit changed the source body')
            definition=dict(name=plan['source_name'],kind='pou_labels',program=logical,declarations=_rows(document,sources))
        if after_config['cpu']!=plan['cpu'] or definition['declarations']!=expected:
            raise GXWFormatError('global declaration readback differs from source-bound requested rows')
        return ProjectWriteResult(saved,dict(operation='native_definition_authoring',definition_before=before,
            definition_after=definition,caller=caller,native_workspace=observation,
            validation={'native_save':'passed','definition_readback':'matched','gx_compile':'not_run'}))
    snapshot = definition_snapshot(saved, plan['source_name'])
    definition, = snapshot['definitions']
    if snapshot['cpu'] != plan['cpu']:
        raise GXWFormatError('authored definition changed target CPU')
    actual = definition.get('members') if definition['kind'] == 'structure' else definition['declarations']
    if definition['kind'] == 'structure':
        expected = [{key: row[key] for key in ('name', 'data_type', 'initial_value', 'comment')} for row in expected]
    if actual != expected or command['operation'] != 'create_structure' and definition.get('st_text') != command['st_text']:
        raise GXWFormatError('authored definition readback differs from requested members, declarations or implementation')
    return ProjectWriteResult(saved, dict(operation='native_definition_authoring',
        definition_before=before, definition_after=definition, caller=caller,
        native_workspace=observation, validation={'native_save': 'passed', 'definition_readback': 'matched', 'gx_compile': 'not_run'}))

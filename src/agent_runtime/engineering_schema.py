"""Declarative operation schemas and engineering request bounds."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from agent_runtime.tool_policy import (
    NATIVE_INSPECTION_TOOL_NAMES,
)


def _engineering_object(properties, required=(), **bounds):
    return {'type': 'object', 'properties': properties, 'required': list(required),
            'additionalProperties': False, **bounds}


def _engineering_text(*, empty=False, maximum=None):
    return {'type': 'string', 'pattern': r'^[^\x00]*$' if empty else r'^(?=[\s\S]*\S)[^\x00]+$',
            **({} if empty else {'minLength': 1}), **({'maxLength': maximum} if maximum is not None else {})}


def _engineering_declarations(scope, *, maximum=4096, nonempty=False, visible=False):
    text = _engineering_text(empty=True)
    fields = {'name': _engineering_text(maximum=255),
              'data_type': _engineering_text(), 'initial_value': text, 'comment': text}
    if scope != 'structure':
        classes = (['VAR_GLOBAL', 'VAR_GLOBAL_CONSTANT', 'VAR', 'VAR_CONSTANT', 'VAR_INPUT',
                    'VAR_OUTPUT', 'VAR_IN_OUT', 'VAR_INPUT_CONSTANT', 'VAR_IN_EXT', 'VAR_RETAIN'] if scope == 'any' else
                   ['VAR_GLOBAL', 'VAR_GLOBAL_CONSTANT'] if scope == 'global' else
                   ['VAR', 'VAR_CONSTANT', 'VAR_RETAIN'] if scope == 'program' else
                   ['VAR', 'VAR_CONSTANT', 'VAR_RETAIN', 'VAR_INPUT', 'VAR_OUTPUT', 'VAR_IN_OUT'] +
                   (['VAR_INPUT_CONSTANT', 'VAR_IN_EXT'] if scope == 'pou' else []))
        fields.update(class_name={'type': 'string', 'enum': classes},
                      kind={'type': 'string', 'enum': (['variable', 'function_block'] if visible and scope == 'global'
                                                       else ['variable', 'structure', 'function_block'])})
    if scope in ('global', 'any'):
        fields.update(device=text, iec_address=text)
        if not visible:
            pair = _engineering_object({'device': text, 'iec_address': text}, ['device'])
            fields['member_devices'] = {'type': 'object', 'propertyNames': _engineering_text(),
                                        'additionalProperties': {'oneOf': [text, pair]}}
            fields['structure_array_addresses'] = _engineering_object({
                'word_address': text, 'word_offset': text, 'word_use_bit': {'enum': [0, 1]},
                'bit_address': text, 'bit_offset': text},
                ['word_address', 'word_offset', 'word_use_bit', 'bit_address', 'bit_offset'])
    return {'type': 'array', 'maxItems': maximum, 'minItems': 1 if nonempty else 0,
            'items': _engineering_object(fields, ['name', 'data_type'])}


def _engineering_declaration_edits():
    identity = _engineering_text(maximum=512)
    patch = _engineering_object({
        'upserts': _engineering_declarations('any', maximum=8192),
        'renames': {'type': 'object', 'maxProperties': 8192, 'propertyNames': identity,
                    'additionalProperties': _engineering_text(maximum=255)},
        'remove': {'type': 'array', 'maxItems': 8192, 'uniqueItems': True, 'items': identity},
        'order': {'type': 'array', 'maxItems': 8192, 'uniqueItems': True, 'items': identity},
        'return_type': _engineering_text(maximum=255),
    }, minProperties=1)
    return {'type': 'object', 'maxProperties': 8192, 'propertyNames': identity, 'additionalProperties': patch}


def _engineering_configuration(*, visible, live=False):
    pair = {'type': 'array', 'minItems': 2, 'maxItems': 2,
            'items': {'type': 'integer', 'minimum': 0, 'maximum': 2147483647}}
    if live:
        ranges = _engineering_object({key: pair for key in ('D', 'R', 'W', 'T', 'ST', 'C', 'M', 'B', 'P', 'S', 'T10')},
                                     minProperties=1)
    elif visible:
        ranges = _engineering_object({'D': {**pair, 'items': {'type': 'integer', 'minimum': 0, 'maximum': 7999}},
                                      'M': pair}, minProperties=1)
    else:
        pair = {**pair, 'items': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295}}
        ranges = {'type': 'object', 'propertyNames': _engineering_text(),
                  'additionalProperties': {'oneOf': [pair, {'type': 'null'}]}}
    fields = {'system_variable_ranges': ranges}
    if not visible:
        fields['program_capacity'] = {'type': 'integer', 'enum': [16000, 64000]}
    return _engineering_object(fields, ['system_variable_ranges'] if visible else (), minProperties=1)


def _engineering_graph_actions(*, live=False):
    text = _engineering_text()
    coordinate = {'type': 'integer', 'minimum': 0, 'maximum': 4096}
    point = {'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': coordinate}
    node_endpoint = _engineering_object({'node': text, 'port': text}, ['node', 'port'])
    endpoint = node_endpoint if live else {'oneOf': [node_endpoint,
                          _engineering_object({'wire': text, 'point': point}, ['wire', 'point'])]}
    variants = {
        'rebind_terminal': {'node': text, 'symbol': text}, 'delete_terminal': {'node': text},
        'move_node': {'node': text, 'x': coordinate, 'y': coordinate}, 'delete_wire': {'wire': text},
        'add_wire': {'id': {'type': 'string', 'pattern': r'^[A-Za-z][A-Za-z0-9_-]{0,63}$'},
                     'from': endpoint, 'to': endpoint,
                     'via': {'type': 'array', 'maxItems': 128, 'items': point}}}
    if live:
        variants['add_terminal'] = {'template': {'type': 'string', 'enum': ['input']},
            'symbol': text, 'x': coordinate, 'y': coordinate}
        variants = {name: {'network': ({'type': 'integer', 'enum': [0]}
                                      if name in ('move_node', 'add_wire', 'add_terminal') else
                                      {'type': 'integer', 'minimum': 0, 'maximum': 511}), **fields}
                    for name, fields in variants.items()}
    return {'type': 'array', 'minItems': 1, 'maxItems': 128, 'items': {'oneOf': [
        _engineering_object({'action': {'type': 'string', 'enum': [name]}, **fields}, ['action', *fields])
        for name, fields in variants.items()]}}


def _engineering_authoring_commands(*, visible, live=False):
    identity = _engineering_text(maximum=255)
    body = _engineering_text(empty=not visible, maximum=4 * 1024 * 1024)
    def declarations(scope):
        return _engineering_declarations(scope, maximum=4096 if visible else 8192, visible=visible)
    commands = {}
    for name in ('create_global_labels', 'edit_global_labels', 'edit_pou_labels'):
        # Global tables also accept their exact logical source with .gh.
        source = identity if name == 'edit_pou_labels' else _engineering_text(maximum=512)
        commands[name] = _engineering_object({'source_name': source,
            'declarations': declarations('pou' if name == 'edit_pou_labels' else 'global')},
            ['source_name', 'declarations'])
        if not visible and name in ('edit_global_labels', 'edit_pou_labels'):
            commands[name]['properties']['remove_declarations'] = {
                'type': 'array', 'maxItems': 8192, 'items': identity, 'uniqueItems': True}
            commands[name]['properties'].update(device_comments={'type': 'object', 'additionalProperties': {'oneOf': [{'type': 'string'}, {'type': 'null'}]}},
                                                comment_table=identity, save_policy={'enum': ['draft', 'checked']})
    if not live:
        commands['create_structure'] = _engineering_object({'source_name': identity,
            'members': _engineering_declarations('structure', maximum=4096 if visible else 8192, nonempty=True)},
            ['source_name', 'members'])
    st_operations = ('create_st_fb', 'edit_st_fb', 'create_st_program', 'edit_st_program')
    if not visible:
        st_operations += ('edit_st_function', 'edit_st_source')
    for name in st_operations:
        fields = {'source_name': identity, 'st_text': body,
                  'declarations': declarations('program' if name.endswith('program') else 'fb')}
        required = ['source_name', 'st_text'] + (['declarations'] if name.startswith('create_') else [])
        if name == 'create_st_program':
            fields.update(resource=identity, task=identity, scope_mode={'type': 'string', 'enum': ['existing_task']})
            required += ['resource', 'task', 'scope_mode']
        if not visible and name.startswith('edit_st_'):
            fields.update(device_comments={'type': 'object', 'additionalProperties': {'oneOf': [{'type': 'string'}, {'type': 'null'}]}},
                          comment_table=identity, save_policy={'enum': ['draft', 'checked']})
            if name in ('edit_st_function', 'edit_st_source'):
                fields['return_type'] = _engineering_text(maximum=255)
        commands[name] = _engineering_object(fields, required)
    commands['configure_project'] = _engineering_object({'source_name': _engineering_text(maximum=512),
        'options': _engineering_configuration(visible=visible, live=live)}, ['source_name', 'options'])
    return commands


def _operation_schema(commands, *, declarations=None):
    variants = [_engineering_object({'operation': {'type': 'string', 'enum': [name]}, **rule['properties']},
                 ['operation', *rule['required']]) for name, rule in commands.items()]
    common = {key: value for rule in variants for key, value in rule['properties'].items()}
    common['operation'] = {'type': 'string', 'enum': list(commands)}
    if declarations is not None:
        common['declarations'] = declarations
    return _engineering_object(common, ['operation'], oneOf=variants)


def _native_commands(*, live=False):
    commands = _engineering_authoring_commands(visible=True, live=live)
    text = _engineering_text(maximum=512)
    commands.update(create_fbd_call=_engineering_object({'caller': text, 'instance': text, 'definition': text,
        'arguments': {'type': 'object', 'minProperties': 1, 'maxProperties': 256,
                      'propertyNames': _engineering_text(), 'additionalProperties': _engineering_text()}},
        ['caller', 'instance', 'definition', 'arguments']),
        edit_fbd_graph=_engineering_object({'caller': text, 'actions': _engineering_graph_actions(live=live)}, ['caller', 'actions']),
        show_assignments=_engineering_object({'queries': {'type': 'array', 'minItems': 1, 'maxItems': 32,
            'items': _engineering_object({'scope': {'type': 'string', 'enum': ['global', 'local', 'instance'] if live else ['global']},
                                         'source': text, 'name': text}, ['scope', 'source', 'name'])}}, ['queries']))
    commands.update({name: _engineering_object({}) for name in ('compile', 'save', 'reopen')})
    if live:
        fields = {'scope': {'type': 'string', 'enum': ['program']},
                  'source_name': _engineering_text(maximum=255), 'name': _engineering_text(maximum=255)}
        commands['delete_label'] = _engineering_object(fields, fields)
    return commands


def _engineering_native_schema():
    command = _operation_schema(_native_commands(), declarations=_engineering_declarations('any'))
    live_command = _operation_schema(_native_commands(live=True), declarations=_engineering_declarations('any'))
    batch = _engineering_object({
        'schema_version': {'type': 'integer', 'enum': [1]},
        'commands': {'type': 'array', 'minItems': 1, 'maxItems': 256, 'items': command}},
        ['schema_version', 'commands'])
    controls = _engineering_object({
        'dwell_ms': {'type': 'integer', 'minimum': 0, 'maximum': 60000},
        'max_steps': {'type': 'integer', 'minimum': 1, 'maximum': 256},
        'expected_revision': {'type': 'integer', 'minimum': 0, 'maximum': 2147483647},
        'step_mode': {'type': 'boolean'}, 'resume_pending': {'type': 'boolean'}})
    live = _engineering_object({'schema_version': {'type': 'integer', 'enum': [1]},
        'mode': {'type': 'string', 'enum': ['live']},
        'session_action': {'type': 'string', 'enum': ['start', 'continue', 'end', 'rebind']},
        'session_id': {'type': 'string', 'pattern': r'^[a-f0-9]{32}$'},
        'controls': controls,
        'commands': {'type': 'array', 'minItems': 0, 'maxItems': 256, 'items': live_command}},
        ['schema_version', 'mode', 'commands'])
    discoverable = {
        **command['properties'], **live_command['properties'],
        'operation': {'type': 'string', 'enum': list(dict.fromkeys([*_native_commands(), *_native_commands(live=True)]))},
        'actions': {'oneOf': [_engineering_graph_actions(), _engineering_graph_actions(live=True)]},
    }
    properties = {**batch['properties'], **live['properties'],
        'commands': {'type': 'array', 'maxItems': 256, 'items': _engineering_object(discoverable, ['operation'])}}
    return _engineering_object({'plan': _engineering_object(properties, ['schema_version', 'commands'],
        oneOf=[batch, live])}, ['plan'])


def _engineering_st_schema():
    commands = _engineering_authoring_commands(visible=False)
    commands = {key: commands[key] for key in ('create_st_program', 'edit_st_program', 'edit_st_fb',
                                              'edit_st_function', 'edit_st_source')}
    commands = {'from_candidate': _engineering_object({'candidate': {'type': 'object'}}, ['candidate']),
        'import': _engineering_object({'data_base64': _engineering_text(),
        'program': _engineering_text(maximum=512)}, ['data_base64', 'program']), **commands}
    variants = [_engineering_object({'operation': {'type': 'string', 'enum': [name]},
        **rule['properties'], 'summary': _engineering_text(empty=True)}, ['operation', *rule['required']])
        for name, rule in commands.items()]
    common = {key: value for rule in variants for key, value in rule['properties'].items()}
    common['operation'] = {'type': 'string', 'enum': list(commands)}
    return _engineering_object(common, ['operation'], oneOf=variants)


def _engineering_fbd_schema():
    commands = {name: rule for name, rule in _engineering_authoring_commands(visible=False).items()
                if name not in {'create_st_program', 'edit_st_program'}}
    text = _engineering_text(maximum=255)
    commands.update({name: _engineering_object({'model': {'type': 'object'},
        'native_save': {'type': 'boolean'},
        **({'device_comments': {'type': 'object', 'additionalProperties': {'oneOf': [{'type': 'string'}, {'type': 'null'}]}},
            'comment_table': text} if name == 'edit' else {})}, ['model'])
                     for name in ('generate', 'edit')})
    commands.update(import_library=_engineering_object({'data_base64': _engineering_text(), 'library_name': text},
                                                       ['data_base64', 'library_name']),
        create_fb=_engineering_object({'source_name': text}, ['source_name']),
        create_program=_engineering_object({'source_name': text, 'resource': text, 'task': text,
            'scope_mode': {'type': 'string', 'enum': ['existing_task', 'new_task', 'new_resource']}},
            ['source_name', 'resource', 'task', 'scope_mode']),
        remove_empty_program=_engineering_object({'source_name': _engineering_text(maximum=512)}, ['source_name']),
        compile=_engineering_object({}))
    commands['import'] = _engineering_object({'data_base64': _engineering_text(), 'program': _engineering_text(),
                                             'native_save': {'type': 'boolean'}}, ['data_base64'])
    extras = {'summary': _engineering_text(empty=True),
              'generation_context_id': _engineering_text(maximum=128)}
    variants = [_engineering_object({'operation': {'type': 'string', 'enum': [name]},
        **rule['properties'], **extras}, ['operation', *rule['required']]) for name, rule in commands.items()]
    common = {key: value for rule in variants for key, value in rule['properties'].items()}
    common['operation'] = {'type': 'string', 'enum': list(commands)}
    common['declarations'] = _engineering_declarations('any', maximum=8192)
    return _engineering_object(common, ['operation'], oneOf=variants)


def _source_instruction_schema(*, expected=False):
    text = _engineering_text(maximum=1024)
    form = _engineering_object({'text': text, 'token_codes': {'type': 'array', 'minItems': 1,
        'maxItems': 16, 'items': {'type': 'string', 'pattern': '^[0-9a-fA-F]{2}$'}}}, ['text', 'token_codes'])
    properties = {'op': _engineering_text(maximum=255),
                  'args': {'type': 'array', 'maxItems': 8192, 'items': {'oneOf': [text, form]}}}
    if expected:
        properties['raw_hex'] = {'type': 'string', 'maxLength': 16 * 1024 * 1024,
                                 'pattern': '^(?:[0-9a-fA-F]{2})*$'}
    return _engineering_object(properties, ['op', 'args'])


def _source_instruction_edit_schema():
    return _engineering_object({
        'operation': {'enum': ['replace', 'insert_before', 'delete', 'move_before']},
        'record_index': {'type': 'integer', 'minimum': 0},
        'target_record_index': {'type': 'integer', 'minimum': 0},
        'expected': _source_record_schema(expected=True),
        'instructions': {'type': 'array', 'maxItems': 8192, 'items': _source_record_schema()}},
        ['operation', 'record_index', 'expected'])


def _source_record_schema(*, expected=False):
    text = {'kind': {'enum': ['statement', 'note']},
            'text': _engineering_text(empty=True, maximum=4 * 1024 * 1024)}
    if expected:
        text['raw_hex'] = {'type': 'string', 'maxLength': 16 * 1024 * 1024,
                           'pattern': '^(?:[0-9a-fA-F]{2})*$'}
    return {'oneOf': [_source_instruction_schema(expected=expected),
                     _engineering_object(text, ['kind', 'text']),
                     *([_engineering_object({'kind': {'enum': ['end_boundary']}}, ['kind'])] if expected else [])]}


def _gxw_edit_schema(*, graph_edits):
    identity = _engineering_text(maximum=512)
    op = {'enum': ['edit']}
    st = _engineering_object({'operation': {'enum': ['edit', 'edit_st_source', 'edit_st_program',
        'edit_st_fb', 'edit_st_function']}, 'st_text': _engineering_text(empty=True, maximum=4 * 1024 * 1024),
        'declarations': _engineering_declarations('any', maximum=8192),
        'return_type': _engineering_text(maximum=255)},
        anyOf=[{'required': [key]} for key in ('st_text', 'declarations', 'return_type')])
    source_command = {'oneOf': [
        _engineering_object({'operation': op, 'instruction_edits': {'type': 'array', 'maxItems': 8192,
            'items': _source_instruction_edit_schema()}}, ['instruction_edits']),
        st,
        _engineering_object({'operation': op, 'model': {'type': 'object'}}, ['model']),
        _engineering_object({'operation': op, 'graph_edits': graph_edits,
            'child_edits': {'type': 'array', 'minItems': 1, 'maxItems': 8192, 'items': _sfc_child_edit_schema()}},
            anyOf=[{'required': ['child_edits']}, {'required': ['graph_edits']}])
    ]}
    return _engineering_object({'version_id': _engineering_text(maximum=128), 'program': identity,
        'edits': {'type': 'array', 'maxItems': 8192,
            'items': _engineering_object({'source_name': identity, 'command': source_command}, ['source_name', 'command'])},
        'declaration_edits': _engineering_declaration_edits(),
        'device_comments': {'type': 'object', 'additionalProperties': {'type': ['string', 'null']}},
        'comment_table': identity, 'save_policy': {'enum': ['draft', 'checked'], 'default': 'draft'},
        'summary': _engineering_text(empty=True, maximum=512)}, ['version_id'],
        anyOf=[{'required': [key]} for key in ('edits', 'declaration_edits', 'device_comments')])


def _sfc_child_edit_schema():
    identity = _engineering_text(maximum=255)
    number = {'type': 'integer', 'minimum': 0, 'maximum': 65535}
    instruction = _source_record_schema()
    edits = _source_instruction_edit_schema()
    registration = _engineering_object({'qualifier': {'type': 'integer', 'minimum': 0, 'maximum': 0xffffffff,
        'description': 'Source qualifier word; Core retains diagnostics for unverified qualifier semantics.'},
        'zoom': identity}, ['qualifier', 'zoom'])
    return _engineering_object({'kind': {'enum': ['zoom', 'transition', 'action']}, 'name': identity,
         'operation': {'enum': ['create', 'edit', 'replace', 'delete', 'rename', 'renumber', 'move_before']},
        'records_hex': {'type': 'string', 'maxLength': 16 * 1024 * 1024},
        'instructions': {'type': 'array', 'maxItems': 8192, 'items': instruction},
        'instruction_edits': {'type': 'array', 'maxItems': 8192, 'items': edits},
         'number': number, 'new_number': number, 'new_name': identity, 'target_name': identity,
        'registrations': {'type': 'array', 'maxItems': 8192, 'items': registration}}, ['kind', 'name'])


def _validate_engineering_input(name, schema, arguments):
    try:
        encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError('Engineering commands must be valid JSON text') from error
    if name == 'execute_native_engineering' and len(encoded) > 4 * 1024 * 1024 + 32:
        raise ValueError('Native engineering plan is too large')
    if name == 'execute_native_engineering':
        if arguments['plan'].get('mode') == 'live':
            from application.live_engineering import normalize_live_plan
            normalize_live_plan(arguments['plan'])
        else:
            from application.native_engineering import normalize_native_plan
            normalize_native_plan(arguments['plan'])
    commands = arguments.get('plan', {}).get('commands', []) if name == 'execute_native_engineering' else [arguments]
    for command in commands:
        rows = command.get('members', command.get('declarations', []))
        if rows:
            from gxw.fb_authoring import _name
            from gxw.declarations import _type_parts
            seen = set()
            for row in rows:
                _name(row['name'], 65001)
                if row['name'].casefold() in seen:
                    raise ValueError('Duplicate engineering declaration name')
                seen.add(row['name'].casefold())
                _, bounds = _type_parts(row['data_type'])
                if len(bounds) > 1 or any(not -2**31 <= low <= high < 2**31 for low, high in bounds):
                    raise ValueError('Engineering declarations require scalar or rank-one types')
        if command.get('operation') in ('create_st_fb', 'edit_st_fb', 'create_st_program', 'edit_st_program',
                                         'edit_st_function', 'edit_st_source'):
            from gxw.fb_authoring import _st_implementation
            _st_implementation(command['st_text'])
        if command.get('operation') == 'configure_project':
            from gxw.fb_authoring import validate_configuration_ranges
            ranges = command['options'].get('system_variable_ranges', {})
            validate_configuration_ranges(ranges, cpu='', known_devices=ranges)
    if name in NATIVE_INSPECTION_TOOL_NAMES:
        queries = arguments.get('queries', [])
        for query in queries:
            fields = ('source', 'name') if name == 'read_native_device_assignments' else ('resource',)
            if any(any(ord(char) < 32 or ord(char) == 127 for char in query[field]) for field in fields):
                raise ValueError('Native inspection names cannot contain control characters')
        identities = [(q.get('source'), q.get('name'), q.get('scope', 'local'))
                      if name == 'read_native_device_assignments' else (q.get('resource'), q.get('step')) for q in queries]
        if len(set(identities)) != len(identities):
            raise ValueError('Duplicate native inspection query')

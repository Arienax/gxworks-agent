# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Business objects at the isolated workspace boundary, never vendor call plans.

The Core continues to validate source bytes and PLC semantics. The workspace
runtime owns language/type encodings, concrete version profiles and native ABI.
"""
from __future__ import annotations

from copy import deepcopy


WORKSPACE_PROTOCOL = 3
_ACTIONS = {
    'save_source': 'edit_program', 'create_source': 'add_program_unit',
    'bind_program_task': 'assign_program', 'create_resource_scope': 'add_resource',
    'create_task_scope': 'add_task', 'validate_project': 'check_project',
    'read_sfc_graph': 'read_sequence_chart',
    'export_library': 'export_user_library', 'import_library': 'import_user_library',
    'author_definition': 'author_project_definition',
    'compile_save_project': 'build_and_save_project',
}
_LANGUAGES = {1: 'ladder', 193: 'st', 208: 'fbd', 240: 'sfc'}
_POU_KINDS = {0x01000000: 'program', 0x01000002: 'function_block'}


def _program(value):
    result = deepcopy(value)
    if 'program_kind' in result:
        kind = result.pop('program_kind')
        if type(kind) is not int or kind not in _LANGUAGES:
            raise ValueError('Workspace source language is outside the supported business contract')
        result['language'] = _LANGUAGES[kind]
    if 'pou_type' in result:
        kind = result.pop('pou_type')
        if type(kind) is not int or kind not in _POU_KINDS:
            raise ValueError('Workspace POU kind is outside the supported business contract')
        result['kind'] = _POU_KINDS[kind]
    for key in ('before_children', 'after_children', 'children'):
        if key in result:
            children = []
            for supplied in result[key]:
                child = deepcopy(supplied)
                if 'type' in child:
                    kind = child.pop('type')
                    if type(kind) is not int or kind not in (66, 68):
                        raise ValueError('Workspace sequence child is outside the supported business contract')
                    child['kind'] = {66: 'transition', 68: 'action'}[kind]
                children.append(child)
            result[key] = children
    return result


def workspace_business_request(request):
    if request.keys() != {'operation', 'protocol_version', 'installation', 'directory', 'owner_token', 'source'}:
        raise ValueError('Invalid internal workspace request')
    if type(request['protocol_version']) is not int or request['protocol_version'] != 1:
        raise ValueError('Invalid internal workspace protocol')
    action = _ACTIONS.get(request['operation'])
    if action is None:
        raise ValueError('Unsupported workspace action')
    source = _program(request['source'])
    project = {'cpu': source.pop('cpu'), 'encoding': source.pop('codepage')}
    for key in ('lexical_reader', 'graph_profile', 'compilation_method'):
        source.pop(key, None)  # The collector selects its own observed profile.
    for key in ('programs', 'libraries'):
        if key in source:
            source[key] = [_program(row) for row in source[key]]
    if 'system_variable_initialization' in source:
        value = source.pop('system_variable_initialization')
        if value.keys() != {'offset', 'before', 'after'} or type(value['offset']) is not int or value['offset'] != 68:
            raise ValueError('Unobserved system variable business change')
        source['pointer_range'] = {'before': deepcopy(value['before']), 'after': deepcopy(value['after'])}
    return {'schema_version': WORKSPACE_PROTOCOL, 'action': action,
            'workspace': {key: request[key] for key in ('installation', 'directory', 'owner_token')},
            'project': project, 'change': source}

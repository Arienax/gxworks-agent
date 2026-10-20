"""Versioned ST generation intentions; project identities are bound by Core."""
from __future__ import annotations

from copy import deepcopy
from plc.st_frontend import parse_source, STUncovered

DECLARATION_FIELDS = frozenset({'name', 'data_type', 'class_name', 'kind', 'initial_value', 'comment', 'device', 'iec_address', 'member_devices'})
UPSERT_FIELDS = DECLARATION_FIELDS | {'scope'}


def validate_shape(data):
    if not isinstance(data, dict):
        raise ValueError('ST candidate must be an object')
    if set(data) == {'st_code'}:
        if not isinstance(data['st_code'], str) or not data['st_code'].strip():
            raise ValueError('st_code must be a nonempty string')
        return data
    if set(data) != {'schema_version', 'language', 'program', 'declaration_edits', 'task_binding'}:
        raise ValueError('ST candidate v2 has unknown or missing fields')
    if type(data['schema_version']) is not int or data['schema_version'] != 2 or data['language'] != 'st':
        raise ValueError('Expected ST candidate schema_version 2')
    program = data['program']
    if not isinstance(program, dict) or set(program) != {'name', 'kind', 'body'} or program['kind'] != 'program':
        raise ValueError('ST candidate requires one ordinary Program')
    if any(not isinstance(program[k], str) or not program[k].strip() for k in ('name', 'body')):
        raise ValueError('Program name and body must be nonempty')
    edits = data['declaration_edits']
    if not isinstance(edits, dict) or set(edits) != {'upserts'} or not isinstance(edits['upserts'], list) or len(edits['upserts']) > 4096:
        raise ValueError('declaration_edits requires a bounded upserts list')
    names = set()
    for row in edits['upserts']:
        if not isinstance(row, dict) or set(row) - UPSERT_FIELDS or not {'name', 'data_type'} <= set(row):
            raise ValueError('Invalid ST declaration upsert')
        scope = row.get('scope')
        if scope is not None and (not isinstance(scope, dict) or set(scope) != {'kind', 'source'} or scope['kind'] != 'global'
                or not isinstance(scope['source'], str) or not scope['source']):
            raise ValueError('Global declaration scope requires a current global-table source')
        identity = (scope['source'] if scope else '', row['name'].upper() if isinstance(row['name'], str) else '')
        if any(not isinstance(row[k], str) or not row[k].strip() for k in ('name', 'data_type')) or identity in names:
            raise ValueError('Missing or duplicate declaration identity')
        names.add(identity)
    task = data['task_binding']
    if not isinstance(task, dict) or set(task) != {'resource', 'task'} or any(not isinstance(v, str) or not v.strip() for v in task.values()):
        raise ValueError('Explicit resource and task identities are required')
    return data


def normalize_candidate(data, *, project_context=None):
    validate_shape(data)
    legacy = 'st_code' in data
    context = project_context or {}
    selected = [p for p in context.get('pous', []) if p['program'] == context.get('selected_source')]
    if legacy:
        name = selected[0]['name'] if len(selected) == 1 else None
        bindings = selected[0].get('task_bindings', []) if len(selected) == 1 else []
        task = {k: bindings[0][k] for k in ('resource', 'task')} if len(bindings) == 1 else None
        body, edits = data['st_code'], []
    else:
        name, body, edits, task = data['program']['name'], data['program']['body'], deepcopy(data['declaration_edits']['upserts']), deepcopy(data['task_binding'])
    parsed = parse_source(body)
    programs = [p for p in parsed.units if p.kind == 'program']
    if len(programs) != 1:
        raise STUncovered('Candidate requires one selected ordinary ST Program')
    program = programs[0]
    if program.name != '__BODY__' and name and program.name.casefold() != name.casefold():
        raise ValueError('ST source owner differs from candidate Program name')
    if program.name != '__BODY__':
        name = program.name
    def scoped(row):
        global_ = row.get('class_name', '').startswith('VAR_GLOBAL') or row.get('scope') is not None
        if global_:
            scope = row.get('scope')
            tables = [g for g in context.get('globals', []) if scope is None or g['source'] == scope['source']]
            if len(tables) != 1:
                raise STUncovered('Global declarations require one explicitly bound current global table')
            row['scope'] = dict(kind='global', source=tables[0]['source'])
            row.setdefault('class_name', 'VAR_GLOBAL')
            if not row['class_name'].startswith('VAR_GLOBAL'):
                raise ValueError('Global declaration scope conflicts with its class')
        return (row.get('scope', {}).get('source', ''), row['name'].upper())
    by_name = {scoped(row): row for row in edits}
    for declaration in [*program.declarations, *parsed.declarations]:
        if declaration.class_name in {'VAR_EXTERNAL', 'VAR_TEMP'}:
            raise STUncovered('Native declaration editing is not covered for ' + declaration.class_name)
        row = declaration.native_row()
        key = scoped(row)
        if key in by_name and any(by_name[key].get(k, row[k]) != row[k] for k in ('data_type', 'initial_value', 'class_name') if row[k]):
            raise ValueError('Source declaration conflicts with declaration_edits: ' + row['name'])
        by_name[key] = {**row, **by_name.get(key, {})}
    if context:
        if task is None or not any(t.get('resource') == task['resource'] and t.get('task') == task['task'] for t in context.get('tasks', [])):
            raise ValueError('Task binding is absent from the current GXW context')
        matching = [p for p in context.get('pous', []) if name and p['name'].casefold() == name.casefold()]
        if len(matching) > 1:
            raise ValueError('Ambiguous Program identity')
        if matching and (matching[0]['kind'] != 'program' or matching[0]['language'] != 'st'):
            raise ValueError('Candidate Program identity belongs to another source kind/language')
        if matching:
            bindings = matching[0].get('task_bindings', [])
            if len(bindings) != 1 or any(bindings[0][k] != task[k] for k in ('resource', 'task')):
                raise ValueError('Candidate differs from current Program task membership')
    candidate = dict(schema_version=2, language='st', program=dict(name=name, kind='program', body=program.body_text),
        declaration_edits={'upserts': list(by_name.values())}, task_binding=task)
    return candidate, parsed

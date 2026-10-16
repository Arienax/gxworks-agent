"""Source-bound user-library inventory and native import readback.

Libraries travel through the vendor's library format. Caller programs, tasks
and project globals are never taken from the donor project.
"""
from __future__ import annotations

from copy import deepcopy

from .callable_sources import ProjectCallableSources, _user_library_owners
from .container_writer import validate_cfb_streams
from .declarations import parse_declarations
from .models import GXWFormatError
from .project_metadata import logical_mapping, read_project_text_context


def _project(raw):
    outer = validate_cfb_streams(raw)
    payloads = validate_cfb_streams(outer['_hdb'])
    streams = {name: payloads[key] for name, key in logical_mapping(outer['projectdatalist.xml']).items()
               if key in payloads}
    configs = [read_project_text_context(value) for name, value in streams.items() if name.endswith('.prj')]
    if len(configs) != 1:
        raise GXWFormatError('library transfer requires one project CPU configuration')
    return outer, streams, configs[0]


def _row(row):
    return {key: getattr(row, key) for key in ('name', 'data_type', 'class_code', 'device',
            'iec_address', 'initial_value', 'comment')}


def library_inventory(raw, library_name=None, *, include_source=False):
    """Describe imported definitions, including both IN_OUT sides and types."""
    from .source_header import source_payload_offset
    from .text_pou import parse_st_pou
    outer, streams, config = _project(raw)
    _, issues, descriptors = _user_library_owners(outer['projectdatalist.xml'], streams)
    program = next((name for name in streams if name.endswith('.pou')), '')
    names = sorted(name.removesuffix('.lif') for name in descriptors if name in streams)
    if library_name is not None:
        matches = [name for name in names if isinstance(library_name, str) and name.casefold() == library_name.casefold()]
        if len(matches) != 1:
            raise GXWFormatError('missing or ambiguous current user library: ' + str(library_name))
        names = matches
    if not names:
        return {'cpu': config['cpu'], 'codepage': config['codepage'], 'libraries': [],
                'issues': issues, 'compilation': 'not_run', 'execution': 'not_run'}
    sources = ProjectCallableSources.from_project(raw, program)
    libraries = []
    for name in names:
        definitions = []
        for logical, owner in sources.user_library_owners.items():
            if owner['library'].casefold() != name.casefold():
                continue
            document = sources.declarations[logical]
            kind = {0x1000002: 'FUNCTION_BLOCK', 0x1000003: 'FUNCTION'}.get(document.owner_pou_type)
            if kind is None:
                raise GXWFormatError('unsupported user library POU kind: ' + owner['name'])
            body = streams[owner['source_program']]
            language = source_payload_offset(body)
            entry = {'name': owner['name'], 'kind': kind, 'language': {193: 'st', 208: 'fbd'}.get(body[language], 'unknown'),
                     'declarations': sorted((_row(row) for row in document.rows), key=lambda row: row['name'].casefold()),
                     'interface': {side: [{key: row[key] for key in ('name', 'class_code', 'declared_type')}
                                         for row in sources.fixed_interface(owner['name'])[side]]
                                   for side in ('inputs', 'outputs')}}
            if include_source:
                if entry['language'] != 'st':
                    raise GXWFormatError('library source transfer currently verifies ST definitions only')
                entry['source_text'] = parse_st_pou(body).text
            definitions.append(entry)
        structures = []
        for (namespace, type_name), documents in sources.library_structures.items():
            if namespace != name.casefold():
                continue
            if len(documents) != 1:
                raise GXWFormatError('ambiguous library structure: ' + type_name)
            document, = documents
            structures.append({'name': document.logical_name.split('\\', 1)[0], 'members': sorted([
                {key: getattr(member, key) for key in ('name', 'data_type', 'initial_value', 'comment')}
                for member in document.members], key=lambda row: row['name'].casefold())})
        globals_ = []
        for logical, value in streams.items():
            if logical.endswith('.lng') and '\\' in logical and logical.rsplit('\\', 1)[1][:-4].casefold() == name.casefold():
                document = parse_declarations(value, logical_name=logical)
                globals_.append({'name': logical.split('\\', 1)[0], 'declarations': [_row(row) for row in document.rows]})
        libraries.append({'name': name, 'definitions': sorted(definitions, key=lambda row: row['name'].casefold()),
                          'structures': sorted(structures, key=lambda row: row['name'].casefold()),
                          'global_tables': sorted(globals_, key=lambda row: row['name'].casefold())})
    return {'cpu': config['cpu'], 'codepage': config['codepage'], 'libraries': libraries,
            'issues': issues + [issue for issue in sources.issues if issue['code'].startswith('user_library_')],
            'compilation': 'not_run', 'execution': 'not_run'}


def library_transfer_plan(target, donor, library_name):
    expected = library_inventory(donor, library_name, include_source=True)
    current = library_inventory(target)
    if expected['issues'] or current['issues']:
        raise GXWFormatError('library ownership is unresolved')
    library, = expected['libraries']
    if any(row['name'].casefold() == library['name'].casefold() for row in current['libraries']):
        raise GXWFormatError('target user library already exists: ' + library['name'])
    _, streams, config = _project(target)
    sources = ProjectCallableSources.from_project(target, next((name for name in streams if name.endswith('.pou')), ''))
    collisions = [row['name'] for row in library['definitions'] if row['name'].casefold() in sources.catalog]
    collisions += [row['name'] for row in library['structures'] if row['name'].casefold() in sources.project_structure_names
                   or any(key[1] == row['name'].casefold() for key in sources.library_structures)]
    if collisions:
        raise GXWFormatError('target callable or type already exists: ' + ', '.join(collisions))
    return {'cpu': config['cpu'], 'codepage': config['codepage'], 'library_name': library['name'],
            'source_cpu': expected['cpu'], 'source_codepage': expected['codepage'], 'expected': library}


def _first_difference(expected, actual, path='library'):
    if type(expected) is not type(actual):
        return path + ': value type differs'
    if isinstance(expected, dict):
        if expected.keys() != actual.keys():
            return path + ': fields differ'
        for key in expected:
            difference = _first_difference(expected[key], actual[key], path + '.' + key)
            if difference:
                return difference
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            return path + ': item count differs'
        for index, (before, after) in enumerate(zip(expected, actual)):
            difference = _first_difference(before, after, f'{path}[{index}]')
            if difference:
                return difference
    elif expected != actual:
        return f'{path}: {str(expected)[:120]!r} -> {str(actual)[:120]!r}'
    return None


def _initial_value_changes(expected, actual):
    """Keep native declaration-cache changes visible, separate from ST source."""
    compared = deepcopy(actual)
    changes = []
    def visit(before, after, path):
        if isinstance(before, dict) and isinstance(after, dict):
            if before.keys() != after.keys():
                return
            for key in before:
                if key == 'initial_value' and before[key] != after[key]:
                    changes.append({'path': path + '.' + key, 'name': before.get('name'),
                                    'data_type': before.get('data_type'), 'before': before[key], 'after': after[key]})
                    after[key] = before[key]
                else:
                    visit(before[key], after[key], path + '.' + key)
        elif isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
            for index, (old, new) in enumerate(zip(before, after)):
                visit(old, new, f'{path}[{index}]')
    visit(expected, compared, 'library')
    return compared, changes


def verify_library_import(target, donor, library_name, saved, observation):
    from .project_writer import ProjectWriteResult
    plan = library_transfer_plan(target, donor, library_name)
    actual = library_inventory(saved, plan['library_name'], include_source=True)
    compared, initial_changes = _initial_value_changes([plan['expected']], actual['libraries'])
    if actual['cpu'] != plan['cpu'] or actual['issues'] or compared != [plan['expected']]:
        raise GXWFormatError('imported library definitions differ from selected source: ' +
                             str(_first_difference([plan['expected']], compared) or actual['issues'] or actual['cpu']))
    before = library_inventory(target, include_source=True)['libraries']
    after = library_inventory(saved, include_source=True)['libraries']
    retained = [row for row in after if row['name'].casefold() != plan['library_name'].casefold()]
    if retained != before:
        raise GXWFormatError('existing target library definitions changed')
    # Check semantic caller state. Native storage/cache normalization is recorded
    # by the established save path rather than rejected as a byte difference.
    from .object_model import read_project_context
    program = next(name for name in _project(target)[1] if name.endswith('.pou'))
    old = read_project_context(target, program)
    reopened = read_project_context(saved, program)
    if old.programs != reopened.programs or old.task_scopes() != reopened.task_scopes():
        raise GXWFormatError('library import changed target programs or tasks')
    for program in old.programs:
        previous = read_project_context(target, program).object_model()
        current = read_project_context(saved, program).object_model()
        for model in (previous, current):
            model['labels'] = {name: rows for name, rows in model['labels'].items() if not name.endswith('.lnl')}
            for node in model['nodes']:
                node.pop('source_offset', None)
            for wire in model['wires']:
                wire.pop('source_offset', None)
        if previous != current:
            raise GXWFormatError('library import changed target caller: ' + program)
    return ProjectWriteResult(saved, {'operation': 'native_library_import', 'library': plan['library_name'],
        'source_cpu': plan['source_cpu'], 'target_cpu': plan['cpu'],
        'library_import': actual['libraries'][0], 'native_initial_value_changes': initial_changes,
        'native_workspace': observation,
        'validation': {'native_save': 'passed', 'library_definitions': 'matched_with_initial_value_changes' if initial_changes else 'matched',
                       'source_text': 'matched', 'initial_value_semantics': 'unverified', 'gx_compile': 'not_run'}})

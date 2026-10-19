"""Current native CSV declarations composed with the existing Core type rules.

These are type/scope observations, not saved declaration streams. Unobserved
ordinary initializers and source storage bytes never become readback evidence.
The supplied baseline contributes immutable libraries and validation framing;
every editable project table must come from this native session's current CSV.
"""
from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import replace
import re

from .declarations import BASIC_TYPES, CLASS_CODES, LabelRecord, _type_parts
from .fb_authoring import (_context, _definition_rows, _global_plan, _name, _native_rows, _rows)
from .models import GXWFormatError
from .visible_engineering import validate_visible_initial_values


def _identity(value):
    keys = ('process_id', 'window_handle', 'process_started', 'executable',
            'software_version', 'project_path', 'cpu_menu')
    if not isinstance(value, dict) or not set(keys) <= value.keys():
        raise GXWFormatError('Active declaration context requires the bound native identity')
    result = {key: value[key] for key in keys}
    language_keys = {'native_ui_language', 'project_codepage'}
    if language_keys.intersection(value):
        if (not language_keys <= value.keys() or value['native_ui_language'] not in ('zh-CN', 'en', 'ja')
                or type(value['project_codepage']) is not int
                or value['project_codepage'] not in (932, 936, 949, 1252)):
            raise GXWFormatError('Active context requires a complete supported language and project-codepage identity')
        result.update({key: value[key] for key in language_keys})
    return result


def _same_native_cpu(left, right):
    from gxworks2.project_profiles import native_project_profile
    a, b = native_project_profile(left), native_project_profile(right)
    return bool(a and b and a.cpu == b.cpu)


def live_read_scope(context, command, observed_tables=()):
    """Current dependencies for one operation, not a complete new observation.

    Unread sources remain the preceding verified phase's protected context.
    The caller must perform a complete read at the phase boundary before it
    reports the operation's unrelated sources as matched.
    """
    inventory = _inventory(context['inventory'])
    operation = command['operation']
    tables = {(row['owner_kind'], row['owner']): row for row in context['tables']}
    tables.update({(row['owner_kind'], row['owner']): row for row in observed_tables})
    pous = {(row['owner_kind'], row['owner']): row for row in inventory['pous']}
    selected = {key for key in tables if key[0] == 'global'}
    bodies = set()
    selector = command.get('caller') or command.get('source_name')
    owners = {key for key in pous if isinstance(selector, str) and selector.casefold() in (
        key[1].casefold(), (key[1] + '.Program.pou').casefold(), (key[1] + '.Body.pou').casefold())}
    selected.update(owners)
    bodies.update(owners)
    # Globals and FB interfaces can affect any caller; deletion also needs all
    # current references. These operations genuinely depend on those bodies.
    if (operation in ('edit_global_labels', 'delete_label')
            or any(kind == 'fb' for kind, _ in owners) and operation in ('edit_pou_labels', 'edit_st_fb')):
        selected.update(pous)
        bodies.update(pous)
    declared = [row.get('data_type') for row in command.get('declarations', []) if isinstance(row, dict)]
    if command.get('definition'):
        declared.append(command['definition'])
    pending = list(selected)
    visited = set()
    while pending or declared:
        types = declared
        declared = []
        if pending:
            key = pending.pop()
            if key in visited:
                continue
            visited.add(key)
            types += [row['data_type'] for row in tables[key]['declarations']]
        for data_type in types:
            if not isinstance(data_type, str):
                continue  # The planner retains responsibility for invalid input.
            try:
                base, _ = _type_parts(data_type)
            except GXWFormatError:
                continue
            matches = {key for key in tables if key[0] in ('fb', 'structure') and key[1].casefold() == base.casefold()}
            for key in matches - selected:
                selected.add(key)
                pending.append(key)
            bodies.update(key for key in matches if key[0] == 'fb')
    return dict(tables=sorted(selected), pous=sorted(bodies),
                configuration=operation == 'configure_project')


def _table_column_identities(table):
    """Bind actual CSV captions to Core semantics without rewriting raw rows."""
    from .native_locale import canonical_label_columns, native_language
    columns = table['csv_columns']
    canonical = canonical_label_columns(columns, language=native_language(table['source_binding']['identity']))
    if table.get('canonical_csv_columns', canonical) != canonical:
        raise GXWFormatError('Saved label comparison has inconsistent localized column metadata')
    return dict(zip(columns, canonical, strict=True))


def _allocation_configuration_profile(configuration, cpu):
    """Bind normalized fields to one actually observed native control schema."""
    from .project_metadata import (native_allocation_controls, project_allocation_profile,
                                   read_native_allocation_grids)
    names = {'allocation_profile', 'control_profile'}
    if not names.intersection(configuration):
        return {}  # Existing FX fixtures carry the original coverage contract.
    profile = project_allocation_profile(cpu)
    if (not names <= configuration.keys()
            or configuration['allocation_profile'] != profile['allocation_profile']
            or not profile['program_supported']):
        raise GXWFormatError('Live allocation fields have a different exact CPU profile')
    controls = native_allocation_controls(configuration['control_profile'])
    if configuration['control_profile'] == 'device_range_grid':
        from .native_locale import native_language
        if configuration.get('coverage') != 'observed_D_M_grid' or not isinstance(configuration.get('identity'), dict):
            raise GXWFormatError('Live grid allocation requires its current bound native observation')
        parsed = read_native_allocation_grids(configuration.get('grid_rows'),
            native_ui_language=native_language(configuration['identity']))
        if (configuration.get('raw_fields') != parsed['raw_fields'] or configuration.get('ranges') != parsed['ranges']
                or configuration.get('parameter_ranges', parsed['parameter_ranges']) != parsed['parameter_ranges']):
            raise GXWFormatError('Live allocation field bindings differ from the actual native grid rows')
        return {key: configuration[key] for key in names}
    if configuration.get('coverage') != 'observed_D_M_controls':
        raise GXWFormatError('Live allocation control schema has no current native observation')
    fields, ranges = configuration.get('raw_fields'), configuration.get('ranges')
    if not isinstance(fields, dict) or set(fields) != {'D', 'M'} or not isinstance(ranges, dict) or set(ranges) != set(fields):
        raise GXWFormatError('Live allocation requires exactly the observed D/M range fields')
    for device, expected in controls['devices'].items():
        rows = fields[device]
        if (not isinstance(rows, list) or len(rows) != 2
                or any(not isinstance(row, dict) for row in rows)
                or [row.get('control_id') for row in rows] != [expected['start_id'], expected['end_id']]
                or any(type(row.get('enabled')) is not bool or not isinstance(row.get('text'), str) for row in rows)):
            raise GXWFormatError('Live allocation field IDs differ from the observed native resource schema')
        numeric = ([int(row['text']) for row in rows]
                   if all(re.fullmatch('[0-9]+', row['text']) for row in rows) else None)
        if ranges[device] != numeric:
            raise GXWFormatError('Live allocation numeric range differs from actual native field text')
        if configuration.get('status') == 'available' and (
                numeric is None or not 0 <= numeric[0] <= numeric[1] < 2**31):
            raise GXWFormatError('Available native allocation fields require ordered integer range values')
    return {key: configuration[key] for key in names}


def active_callable_sources(baseline, tables, *, owner, kind='program', identity=None):
    """Use actual current project labels with the original library catalog.

    ``record_id`` below is observation order for Core interface candidates.
    Native callable port names/directions must independently confirm those
    candidates; CSV order alone is never proof of a native graphic port ID.
    """
    config, _, groups, original, _ = _context(baseline, require_fbd=False)
    if kind not in ('program', 'fb') or not isinstance(tables, (list, tuple)) or not tables:
        raise GXWFormatError('Active type context requires current Program/FB and declaration tables')
    template = next((doc for doc in original.declarations.values() if doc.scope == 'local'), None)
    if template is None:
        raise GXWFormatError('Current source has no validated local declaration framing')
    documents = {name: doc for name, doc in original.declarations.items() if name.endswith('.lnl')}
    current_groups, seen, bound_identity = {}, set(), _identity(identity) if identity else None
    for table in tables:
        if isinstance(table, dict) and any(isinstance(issue, dict)
                and issue.get('code') == 'ambiguous_native_type_alias' for issue in (table.get('issues') or [])):
            raise GXWFormatError('Current native declaration type alias is ambiguous; writing requires an unambiguous observation')
        if isinstance(table, dict) and table.get('owner_kind') == 'structure':
            continue  # Immutable structure framing is checked by the full live dispatcher.
        if (not isinstance(table, dict) or table.get('origin') != 'active_native_labels'
                or table.get('owner_kind') not in ('global', 'program', 'fb')
                or not isinstance(table.get('declarations'), list)):
            raise GXWFormatError('Active type context cannot use a saved or unsupported declaration table')
        table_identity = _identity(table.get('source_binding', {}).get('identity'))
        if bound_identity is None:
            bound_identity = table_identity
        if table_identity != bound_identity:
            raise GXWFormatError('Declaration tables belong to different native sessions')
        name, scope = table['owner'], table['owner_kind']
        if (table['source_binding'].get('owner') != name or table['source_binding'].get('owner_kind') != scope
                or table_identity['software_version'] != '1.635.0.1'
                or not _same_native_cpu(table_identity['cpu_menu'], config['cpu'])
                or table_identity.get('project_codepage', config['codepage']) != config['codepage']):
            raise GXWFormatError('Current declaration page does not belong to this owner/CPU/version')
        if not isinstance(name, str) or not re.fullmatch(r'\w+', name, re.UNICODE):
            raise GXWFormatError('Current native declaration owner is invalid')
        key = ('global' if scope == 'global' else 'local', name.casefold())
        if key in seen:
            raise GXWFormatError('Duplicate current declaration owner')
        seen.add(key)
        logical = name + ('.gh' if scope == 'global' else '.Labels.lh')
        rows = []
        row_names = set()
        for row in table['declarations']:
            if row.get('class_status') != 'resolved' or row.get('class_code') not in CLASS_CODES.values():
                raise GXWFormatError('Current native declaration class is unresolved')
            _name(row.get('name'), config['codepage'])
            if row['name'].casefold() in row_names:
                raise GXWFormatError('Duplicate current native declaration name')
            row_names.add(row['name'].casefold())
            base, bounds = _type_parts(row['data_type'])
            type_kind = row.get('kind')
            # A nonbasic type can be resolved by another current FB table;
            # retain it as a type claim, without inventing its storage bytes.
            code = BASIC_TYPES[base][0] if base in BASIC_TYPES else 15 if type_kind == 'function_block' else 0
            initial = row['initial_value'] if row.get('initial_value_status') == 'observed_constant' else ''
            rows.append(LabelRecord(row['name'], row['data_type'], row['class_code'],
                row.get('device') or '', row.get('iec_address') or '', 0, initial or '', '',
                row['csv_row'], row.get('comment', ''), int(bool(bounds)), code,
                base if code == 15 else '', offset=0, raw=b''))
        frame = original.declarations.get(logical, template)
        documents[logical] = replace(frame, logical_name=logical,
            scope='global' if scope == 'global' else 'local',
            owner_name=None if scope == 'global' else name,
            owner_pou_type=None if scope == 'global' else 0x1000000 if scope == 'program' else 0x1000002,
            owner_return_type=None, rows=tuple(rows), raw=b'')
        if scope != 'global':
            current_groups[name + '.Program.pou'] = {'labels': logical}
    expected = {('global', name.removesuffix('.gh').casefold()) if doc.scope == 'global'
                else ('local', doc.owner_name.casefold()) for name, doc in original.declarations.items()
                if doc.scope == 'global' or name.endswith('.Labels.lh')}
    if not expected <= seen:
        raise GXWFormatError('One or more saved project tables have no current native observation')
    selected = owner + '.Program.pou'
    if selected not in current_groups:
        raise GXWFormatError('The requested current POU has no native declaration observation')
    doc = documents[current_groups[selected]['labels']]
    if doc.owner_pou_type != (0x1000000 if kind == 'program' else 0x1000002):
        raise GXWFormatError('Current Program/FB owner differs from the selected context')
    sources = copy(original)
    sources.source_groups, sources.program_name = current_groups, selected
    sources = sources.with_declarations(documents)
    sources.active_native_identity = bound_identity
    sources.active_observed_fields = ('name', 'data_type', 'class', 'constant', 'device', 'iec_address', 'comment')
    return sources


def prepare_active_labels(baseline, tables, command, *, owner, kind='program', identity=None,
                          device_profile=None, current_graphs=None):
    """Validate an upsert against current CSV; native import owns the mutation."""
    config = _context(baseline, require_fbd=False)[0]
    sources = active_callable_sources(baseline, tables, owner=owner, kind=kind, identity=identity)
    operation = command.get('operation')
    if operation not in ('create_global_labels', 'edit_global_labels', 'edit_pou_labels'):
        raise GXWFormatError('Unsupported active declaration operation')
    allowed = {'operation', 'source_name', 'declarations'}
    if operation != 'edit_pou_labels':
        allowed.add('device_profile')
    if set(command) - allowed or not {'operation', 'source_name', 'declarations'} <= command.keys():
        raise GXWFormatError('Active declaration operation requires explicit name and rows')
    if not isinstance(command['declarations'], list):
        raise GXWFormatError('Active declaration rows must be an array')
    validate_visible_initial_values(command['declarations'])
    calls = []
    if operation != 'edit_pou_labels':
        supplied = command
        if device_profile is not None:
            if command.get('device_profile', device_profile) != device_profile:
                raise GXWFormatError('The active declaration device profile differs from this session')
            supplied = {**command, 'device_profile': device_profile}
        plan, _, _, _ = _global_plan(config, sources, supplied, sources.program_name)
        scope = 'global'
    else:
        name = command['source_name']
        matches = [table for table in tables if table['owner_kind'] in ('program', 'fb')
                   and table['owner'] == name]
        if len(matches) != 1:
            raise GXWFormatError('Missing or ambiguous current POU declaration owner')
        table, = matches
        scope = table['owner_kind']
        logical = name + '.Labels.lh'
        document = sources.declarations[logical]
        requested, seen = list(_rows(document, sources)), set()
        for row in command['declarations']:
            if not isinstance(row, dict) or not {'name', 'data_type'} <= row.keys():
                raise GXWFormatError('Current local upsert needs an explicit name and type')
            folded = row['name'].casefold() if isinstance(row['name'], str) else None
            if folded in seen:
                raise GXWFormatError('Duplicate current POU declaration upsert')
            seen.add(folded)
            index = next((i for i, old in enumerate(requested) if old['name'].casefold() == folded), None)
            if index is None:
                requested.append(deepcopy(row))
            else:
                requested[index] = {**requested[index], **row}
        normalized = _definition_rows(config, sources, document, requested)
        if scope == 'fb':
            calls = _verify_active_st_fb_interface(sources, document, normalized, current_graphs)
        rows = _native_rows(normalized)
        old = {row['name'].casefold(): row for row in table['declarations']}
        for row in rows:
            previous = old.get(row['name'].casefold(), {})
            row.update(device=previous.get('device') or '', iec_address=previous.get('iec_address') or '')
        plan = dict(operation=operation, source_name=name, cpu=config['cpu'], rows=rows)
    for row in plan['rows']:
        row['csv_constant'] = row['initial_value'] if row['class_code'] in (2, 9, 11) else ''
    before = next((table for table in tables if table['owner_kind'] == scope
                   and table['owner'] == plan['source_name']), None)
    return dict(kind='active_labels', scope=scope, plan=plan, before=deepcopy(before),
                origin='current_native_csv', initial_values='ordinary_values_unavailable_in_csv', existing_fb_calls=calls)


def verify_active_labels(prepared, observed):
    """Compare actual CSV columns, preserving all observed unrelated fields."""
    if (observed.get('origin') != 'active_native_labels' or observed.get('owner_kind') != prepared['scope']
            or observed.get('owner') != prepared['plan']['source_name']):
        raise GXWFormatError('Native declaration readback belongs to another table')
    before = prepared['before']
    if before and _identity(observed['source_binding']['identity']) != _identity(before['source_binding']['identity']):
        raise GXWFormatError('Native declaration readback belongs to another process/project')
    columns = _table_column_identities(observed)
    if before and (before['csv_columns'] != observed['csv_columns'] or _table_column_identities(before) != columns):
        raise GXWFormatError('Native declaration readback changed the observed CSV field schema')
    native_columns = {canonical: column for column, canonical in columns.items()}
    if not {'类', '标签名', '数据类型', '常量', '注释'} <= native_columns.keys():
        raise GXWFormatError('Native declaration readback has no complete declaration column schema')
    old = {row['name'].casefold(): row['raw_fields'] for row in (before or {}).get('declarations', [])}
    actual = observed['declarations']
    expected = []
    from .fb_authoring import _visible_st_declaration_rows
    classes = {code: name for name, code in CLASS_CODES.items()}
    for row in _visible_st_declaration_rows(prepared['plan']['rows']):
        fields = {column: old.get(row['name'].casefold(), {}).get(column, '') for column in observed['csv_columns']}
        updates = {'类': classes[row['class_code']], '标签名': row['name'], '数据类型': row['data_type'],
                   '常量': row['csv_constant'], '注释': row['comment']}
        fields.update({native_columns[key]: value for key, value in updates.items()})
        for column, key in (('软元件', 'device'), ('地址', 'iec_address')):
            if column in native_columns:
                fields[native_columns[column]] = row.get(key, '')
        expected.append(fields)
    if [row['raw_fields'] for row in actual] != expected:
        raise GXWFormatError('Current native declaration fields differ from the requested complete table')
    return dict(status='matched', owner=observed['owner'], owner_kind=observed['owner_kind'],
                declarations=len(actual), origin='active_native_csv', save_performed=False,
                ordinary_initial_values='unavailable_in_csv')


def _label_delete_atoms(text, *, body):
    """Inspect only the existing bounded ST grammar, without strings/comments."""
    from plc.st_validation import _Parser, STParseError
    try:
        parser = _Parser(text)
        syntax = parser.body() if body else parser.expression()
    except STParseError as error:
        raise GXWFormatError('Label deletion requires fully covered direct ST syntax: ' + str(error)) from error
    if parser.token.text:
        raise GXWFormatError('Label deletion cannot prove absence through incomplete source syntax')
    atoms = []
    def visit(value):
        if not isinstance(value, (tuple, list)):
            return
        if isinstance(value, tuple) and value:
            if value[0] in ('call', 'invoke'):
                raise GXWFormatError('Label deletion does not cover ST calls or indirect access')
            if value[0] == 'atom':
                text = value[1]
                if text and (text[0].isalpha() or text[0] == '_') and text.upper() not in ('TRUE', 'FALSE'):
                    atoms.append(text)
                return
        for child in value:
            visit(child)
    visit(syntax)
    return atoms


def _verify_label_delete_unused(sources, context, logical, name):
    from plc.validation import DEVICE_ADDRESS_RE
    from .models import NodeKind, node_kind_from_code
    def selected(owner):
        result = copy(sources)
        result.program_name = owner + '.Program.pou'
        return result
    def check(atoms, caller, location):
        for atom in atoms:
            if '.' in atom:
                raise GXWFormatError('Label deletion does not cover qualified or indirect access in ' + location)
            bound = caller.label(atom)
            if bound is not None:
                if bound[0] == logical and bound[1].name.casefold() == name.casefold():
                    raise GXWFormatError('Label is currently referenced in ' + location + ': ' + name)
                continue
            # This is lexical device identity only, not an address/range or
            # runtime validation. Indexed and bit-qualified forms are excluded.
            device = DEVICE_ADDRESS_RE.fullmatch(atom)
            if device is None or device[3] is not None:
                raise GXWFormatError('Label deletion has an unresolved source symbol in ' + location + ': ' + atom)
    for source in context['st']:
        check(_label_delete_atoms(source['st_text'], body=True), selected(source['owner']), source['owner'] + ' ST')
    for graph in context['graphs']:
        caller = selected(graph['owner'])
        for network in graph['networks']:
            for node in network['nodes']:
                kind = node_kind_from_code(node['source_kind_code'])
                location = graph['owner'] + '/network ' + str(network['index'])
                if kind == NodeKind.FUNCTION_BLOCK:
                    check([node['name']], caller, location)
                    binding = caller.label(node['name'])
                    if (binding is None or caller.declaration_kind(binding[1]) != 'function_block'
                            or binding[1].data_type != node.get('type_name')):
                        raise GXWFormatError('Label deletion requires a resolved current FB instance in ' + location)
                    _, definition = caller.definition(binding[1].data_type, 'FUNCTION_BLOCK')
                    if definition.get('source_kind') != 'project_labels':
                        raise GXWFormatError('Label deletion does not cover external FB implementation access')
                    caller.fixed_interface(binding[1].data_type)
                elif kind in (NodeKind.INPUT, NodeKind.OUTPUT, NodeKind.CONTACT, NodeKind.CONTACT_NC,
                              NodeKind.CONTACT_RISING, NodeKind.CONTACT_FALLING, NodeKind.COIL,
                              NodeKind.COIL_NEGATED, NodeKind.COIL_SET, NodeKind.COIL_RESET):
                    check(_label_delete_atoms(node['name'], body=False), caller, location)
                else:
                    raise GXWFormatError('Label deletion does not cover this current native node kind in ' + location)
    safe_columns = {'类', '标签名', '数据类型', '常量', '软元件', '地址', '注释', '备注'}
    for table in context['tables']:
        columns = _table_column_identities(table)
        if (table.get('status') != 'available'
                or any(row['raw_fields'].get(column) for row in table['declarations']
                       for column, canonical in columns.items() if canonical not in safe_columns)):
            raise GXWFormatError('Label deletion requires fully covered current declaration fields')
        if table['owner_kind'] == 'structure':
            continue
        caller = selected(table['owner']) if table['owner_kind'] != 'global' else copy(sources)
        if table['owner_kind'] == 'global':
            caller.program_name = ''  # Global initializers cannot acquire a Program-local scope.
        for row in table['declarations']:
            if row.get('csv_constant'):
                check(_label_delete_atoms(row['csv_constant'], body=False), caller, table['owner'] + ' constant')


def prepare_active_label_delete(baseline, context, command):
    """Plan one unused ordinary Program row; native permission is separate."""
    state = live_context_state(context)
    config = _context(baseline, require_fbd=False)[0]
    if (not isinstance(command, dict) or set(command) != {'operation', 'scope', 'source_name', 'name'}
            or command.get('operation') != 'delete_label'):
        raise GXWFormatError('Live label deletion requires one explicit scope, table and name')
    if command['scope'] != 'program':
        raise GXWFormatError('Native global/FB label deletion has not been verified; only Program VAR deletion is supported')
    from gxworks2.project_profiles import native_project_profile
    profile = native_project_profile(config['cpu'])
    if profile is None or not profile.program_supported or state['identity']['cpu_menu'] != profile.cpu:
        raise GXWFormatError('Native label deletion CPU differs from the current programmable menu')
    owner, name = _name(command['source_name'], config['codepage']), _name(command['name'], config['codepage'])
    tables = [row for row in context['tables'] if row['owner_kind'] == 'program' and row['owner'].casefold() == owner.casefold()]
    if len(tables) != 1:
        raise GXWFormatError('Label deletion has no unique current Program table')
    table, = tables
    rows = table['declarations']
    if ([row.get('csv_row') for row in rows] != list(range(len(rows)))
            or any(type(row.get('csv_row')) is not int for row in rows)):
        raise GXWFormatError('Label deletion needs the complete native CSV row order')
    targets = [row for row in rows if row['name'].casefold() == name.casefold()]
    if len(targets) != 1:
        raise GXWFormatError('Label deletion has no unique current declaration')
    target, = targets
    base, bounds = _type_parts(target['data_type'])
    columns = _table_column_identities(table)
    fields = {canonical: target['raw_fields'].get(column) for column, canonical in columns.items()}
    if (not {'软元件', '地址'} <= set(columns.values())
            or target.get('class_status') != 'resolved' or target.get('class_code') != CLASS_CODES['VAR']
            or target.get('kind') != 'variable' or target.get('type_status') != 'resolved'
            or base not in ('BOOL', 'INT') or bounds or target.get('device') or target.get('iec_address')
            or any(fields.get(column) for column in ('软元件', '地址'))
            or fields.get('类') != 'VAR'
            or fields.get('标签名') != target['name']
            or fields.get('数据类型') != target['data_type']
            or target.get('csv_constant')):
        raise GXWFormatError('Label deletion supports only unmapped ordinary scalar Program VAR BOOL/INT')
    sources = active_callable_sources(baseline, context['tables'], owner=table['owner'],
                                     kind='program', identity=context['identity'])
    bound = sources.label(target['name'])
    logical = table['owner'] + '.Labels.lh'
    if bound is None or bound[0] != logical or bound[1].record_id != target['csv_row']:
        raise GXWFormatError('Label deletion resolved to another or ambiguous declaration scope')
    _verify_label_delete_unused(sources, context, logical, target['name'])
    plan = dict(operation='delete_label', scope='program', source_name=table['owner'], name=target['name'],
                data_type=target['data_type'], csv_row=target['csv_row'], cpu=config['cpu'],
                before_fields=deepcopy(target['raw_fields']))
    return dict(kind='active_label_delete', scope='program', plan=plan, before=deepcopy(table),
                target=deepcopy(target), identity=deepcopy(state['identity']), origin='current_native_pages')


def verify_active_label_delete(prepared, observed):
    """The complete observed table must lose exactly the selected native row."""
    before, target = prepared['before'], prepared['target']
    _table_column_identities(before)
    _table_column_identities(observed)
    if (observed.get('origin') != 'active_native_labels' or observed.get('owner_kind') != 'program'
            or observed.get('owner') != before['owner']
            or observed.get('source_binding', {}).get('owner') != before['owner']
            or observed.get('source_binding', {}).get('owner_kind') != 'program'
            or _identity(observed.get('source_binding', {}).get('identity')) != prepared['identity']
            or observed.get('csv_columns') != before['csv_columns'] or observed.get('csv_title') != before['csv_title']):
        raise GXWFormatError('Deleted-label readback belongs to another native table, schema or identity')
    expected = [row['raw_fields'] for index, row in enumerate(before['declarations']) if index != target['csv_row']]
    rows = observed.get('declarations')
    if (not isinstance(rows, list) or [row.get('raw_fields') for row in rows] != expected
            or any(type(row.get('csv_row')) is not int for row in rows)
            or [row['csv_row'] for row in rows] != list(range(len(rows)))):
        raise GXWFormatError('Native label deletion did not remove exactly the selected row and preserve all other fields')
    return dict(status='matched', owner=before['owner'], owner_kind='program', deleted=deepcopy(prepared['plan']),
                declarations=len(rows), origin='active_native_row_delete', save_performed=False)


def _active_st_fb_calls(sources, graphs, name):
    """Bind current FB nodes through each caller's existing Core symbol scope."""
    from .models import NodeKind, node_kind_from_code
    if not isinstance(graphs, list):
        raise GXWFormatError('Current ST FB editing requires complete current graph observations')
    target_stream, _ = sources.definition(name, 'FUNCTION_BLOCK')
    calls = []
    for graph in graphs:
        caller = copy(sources)
        caller.program_name = graph['owner'] + '.Program.pou'
        possible_targets = {row.name.casefold() for logical, document in caller.declarations.items()
            if document.scope == 'global' or logical == caller.local_table
            for row in document.rows if row.data_type.casefold() == name.casefold()
            and caller.declaration_kind(row) == 'function_block'}
        for network in graph['networks']:
            for node in network['nodes']:
                if node_kind_from_code(node['source_kind_code']) != NodeKind.FUNCTION_BLOCK:
                    continue
                native_target = (isinstance(node.get('type_name'), str)
                                 and node['type_name'].casefold() == name.casefold())
                possible_target = native_target or node['name'].casefold() in possible_targets
                affected = graph['owner'] + '/' + node['name']
                binding, row = None, None
                try:
                    binding = caller.label(node['name'])
                    row = binding[1] if binding else None
                    if row is None or caller.declaration_kind(row) != 'function_block':
                        if native_target:
                            raise GXWFormatError('native call has no matching instance declaration')
                        continue
                    stream, _ = caller.definition(row.data_type, 'FUNCTION_BLOCK')
                    if stream != target_stream:
                        if native_target:
                            raise GXWFormatError('native call and scoped instance definition differ')
                        continue
                    if row.type_code not in (0, 15) or row.data_type != node.get('type_name'):
                        raise GXWFormatError('native call and scoped instance definition differ')
                except GXWFormatError as error:
                    if not possible_target:
                        continue  # No typed candidate in this caller's scope refers to the target.
                    raise GXWFormatError('Cannot verify current ST FB call ' + affected + ': ' + str(error)) from error
                calls.append(dict(caller=graph['owner'], caller_kind=graph['owner_kind'], instance=node['name'],
                    declaration_source=binding[0], definition=name, network=network['index'], node=node['index']))
    return calls


def _active_st_fb_signature(sources, name):
    """Reuse source interface validation; ignore internal VARs and comments."""
    interface = sources.fixed_interface(name)
    sides = tuple((side, tuple((row['name'], row['declared_type'], row['class_code']) for row in interface[side]))
                  for side in ('inputs', 'outputs'))
    _, definition = sources.definition(name, 'FUNCTION_BLOCK')
    formal_classes = {row['class_code'] for side in ('inputs', 'outputs') for row in interface[side]}
    order = tuple((row.name, row.data_type, row.class_code)
                  for row in sorted(definition['declarations'].rows, key=lambda row: row.record_id)
                  if row.class_code in formal_classes)
    return sides, order


def _verify_active_st_fb_interface(sources, document, rows, graphs):
    calls = _active_st_fb_calls(sources, graphs, document.owner_name)
    if calls:
        from .declarations import edit_declarations
        affected = ', '.join(sorted({row['caller'] + '/' + row['instance'] for row in calls}))
        try:
            candidate = edit_declarations(replace(document, rows=()), upserts=rows, native_fbd_locals=True)
            updated = sources.with_declarations({**sources.declarations, document.logical_name: candidate})
            if _active_st_fb_signature(sources, document.owner_name) != _active_st_fb_signature(updated, document.owner_name):
                raise GXWFormatError('port names, types, classes or order changed')
        except GXWFormatError as error:
            raise GXWFormatError('ST FB interface migration is not verified for existing calls ' + affected + ': ' + str(error)) from error
    return calls


def prepare_active_st(baseline, tables, command, *, owner, kind='program', identity=None,
                      current_st=None, task_scopes=None, current_graphs=None):
    """Compose ST syntax and declaration contracts with current native tables."""
    from .fb_authoring import _st_implementation
    from .native_write import native_program_creation_plan
    config = _context(baseline, require_fbd=False)[0]
    sources = active_callable_sources(baseline, tables, owner=owner, kind=kind, identity=identity)
    operation = command.get('operation')
    if operation not in ('create_st_fb', 'edit_st_fb', 'create_st_program', 'edit_st_program'):
        raise GXWFormatError('Unsupported current ST operation')
    create = operation.startswith('create_')
    target_kind = 'fb' if operation.endswith('_fb') else 'program'
    allowed = {'operation', 'source_name', 'declarations', 'st_text'}
    if create and target_kind == 'program':
        allowed |= {'scope_mode', 'resource', 'task'}
    if set(command) - allowed or not {'operation', 'source_name', 'st_text'} <= command.keys():
        raise GXWFormatError('ST authoring requires explicit owner and implementation')
    name = _name(command['source_name'], config['codepage'])
    _st_implementation(command['st_text'])
    matched = [table for table in tables if table['owner_kind'] != 'global'
               and table['owner'].casefold() == name.casefold()]
    before = None
    if create:
        if (matched or name.casefold() in sources.catalog or name.casefold() in sources.project_structures
                or any(key[1] == name.casefold() for key in sources.library_structures)):
            raise GXWFormatError('The current Program, callable or type already exists')
    else:
        if len(matched) != 1 or matched[0]['owner_kind'] != target_kind:
            raise GXWFormatError('Current ST editing requires its exact Program/FB declaration owner')
        before, = matched
        name = before['owner']
        if (not isinstance(current_st, dict) or current_st.get('origin') != 'active_native_st'
                or current_st.get('owner') != name or current_st.get('owner_kind') != target_kind
                or _identity(current_st['source_binding']['identity']) != sources.active_native_identity):
            raise GXWFormatError('Current ST implementation must be read from this native session')
    write_rows = create or command.get('declarations') is not None
    document = (sources.declarations[name + '.Labels.lh'] if before else
                replace(sources.declarations[sources.local_table], owner_name=name,
                        owner_pou_type=0x1000002 if target_kind == 'fb' else 0x1000000, rows=()))
    if write_rows:
        validate_visible_initial_values(command.get('declarations', []))
        normalized = _definition_rows(config, sources, document, command.get('declarations'))
    else:
        normalized = _rows(document, sources)
    calls = (_verify_active_st_fb_interface(sources, document, normalized, current_graphs)
             if before and target_kind == 'fb' else [])
    if before and write_rows and {row['name'].casefold() for row in before['declarations']} - {
            row['name'].casefold() for row in normalized}:
        raise GXWFormatError('Removing existing native declarations by CSV is not yet verified')
    rows = _native_rows(normalized)
    old = {row['name'].casefold(): row for row in (before or {}).get('declarations', [])}
    for row in rows:
        previous = old.get(row['name'].casefold(), {})
        row.update(device=previous.get('device') or '', iec_address=previous.get('iec_address') or '',
                   csv_constant=row['initial_value'] if row['class_code'] in (2, 9, 11) else '')
    plan = dict(operation=operation, source_name=name, cpu=config['cpu'], rows=rows,
                write_declarations=write_rows, st_text=command['st_text'])
    if create and target_kind == 'program':
        if command.get('scope_mode') != 'existing_task' or not isinstance(task_scopes, dict):
            raise GXWFormatError('Current Program creation requires explicit existing task context')
        scope = native_program_creation_plan(baseline, name, resource=command.get('resource'),
            task=command.get('task'), scope_mode='existing_task')
        matches = [task for task in task_scopes.get('tasks', [])
                   if task['resource'] == scope['resource'] and task['task'] == scope['task']]
        if len(matches) != 1:
            raise GXWFormatError('The chosen current task is missing or ambiguous')
        task, = matches
        plan.update(scope)
        plan.update(line=1 + max([0] + [row['line'] for row in task['programs']]),
                    tasks=deepcopy(task_scopes['tasks']))
    labels = dict(kind='active_labels', scope=target_kind, plan=plan, before=deepcopy(before))
    return dict(kind='active_st', plan=plan, scope=target_kind, create=create, labels=labels,
                before_st=deepcopy(current_st), origin='current_native_pages', existing_fb_calls=calls)


def verify_active_st(prepared, observed, labels=None):
    from .visible_engineering import normalized_st
    plan = prepared['plan']
    if (observed.get('origin') != 'active_native_st' or observed.get('owner') != plan['source_name']
            or observed.get('owner_kind') != prepared['scope']
            or normalized_st(observed.get('st_text', '')) != normalized_st(plan['st_text'])):
        raise GXWFormatError('The actual current native ST implementation differs from this request')
    if labels is not None:
        if plan['write_declarations']:
            verify_active_labels(prepared['labels'], labels)
        else:
            previous = prepared['labels']['before']
            if (labels.get('owner') != previous['owner'] or labels.get('owner_kind') != previous['owner_kind']
                    or labels.get('csv_columns') != previous['csv_columns']
                    or [row['raw_fields'] for row in labels.get('declarations', [])] !=
                       [row['raw_fields'] for row in previous['declarations']]):
                raise GXWFormatError('Body-only ST edit changed retained current declaration fields')
    before = prepared['before_st']
    if before and _identity(before['source_binding']['identity']) != _identity(observed['source_binding']['identity']):
        raise GXWFormatError('The ST implementation belongs to another native session')
    return dict(status='matched', owner=plan['source_name'], owner_kind=prepared['scope'],
                implementation='current_native_text', declarations='matched' if labels else 'not_requested',
                save_performed=False, compilation='not_run')


def _inventory(value):
    if (not isinstance(value, dict) or set(value) != {'globals', 'pous', 'structures'}
            or any(not isinstance(value[key], list) for key in value)):
        raise GXWFormatError('Live context requires the complete native project inventory')
    result = deepcopy(value)
    for key in ('globals', 'structures'):
        if (any(not isinstance(name, str) or not name or '\0' in name for name in value[key])
                or len({name.casefold() for name in value[key]}) != len(value[key])):
            raise GXWFormatError('Live native inventory has an invalid or duplicate owner')
        result[key].sort()
    seen = set()
    for row in result['pous']:
        if (not isinstance(row, dict) or set(row) != {'owner', 'owner_kind', 'language'}
                or row['owner_kind'] not in ('program', 'fb') or row['language'] not in ('st', 'fbd')
                or not isinstance(row['owner'], str) or not row['owner'] or '\0' in row['owner']
                or row['owner'].casefold() in seen):
            raise GXWFormatError('Live native POU inventory is unsupported or ambiguous')
        seen.add(row['owner'].casefold())
    result['pous'].sort(key=lambda row: (row['owner_kind'], row['owner']))
    return result


def _graph_source(observation):
    """Source geometry excludes connection/type caches populated by native getters."""
    from .active_graph import active_graph_projection
    projected = active_graph_projection(observation)
    networks = []
    for network, raw in zip(projected['networks'], observation['networks'], strict=True):
        networks.append(dict(index=network['index'], nodes=[
            {key: node[key] for key in ('index', 'template', 'symbol', 'x', 'y', 'width', 'height')}
            | {'source_kind_code': source['source_kind_code'], 'type_name':
                node['template'].partition(':')[2] if source['source_kind_code'] == 1 else source.get('type_name')}
            for node, source in zip(network['nodes'], raw['nodes'], strict=True)],
            wires=[{key: wire[key] for key in ('index', 'start', 'end')} for wire in network['wires']],
            junctions=[{'index': row['index']} for row in raw['junctions']]))
    return dict(owner=projected['owner'], owner_kind=projected['owner_kind'], networks=networks)


def live_context_state(context):
    """Freeze observed engineering meaning, without UI controls or memory addresses."""
    identity = _identity(context.get('identity'))
    inventory = _inventory(context.get('inventory'))
    tables, st, graphs, table_keys = [], [], [], set()
    for table in context.get('tables', []):
        scope, name = table.get('owner_kind'), table.get('owner')
        if (table.get('origin') != 'active_native_labels' or scope not in ('global', 'program', 'fb', 'structure')
                or _identity(table.get('source_binding', {}).get('identity')) != identity
                or table['source_binding'].get('owner') != name
                or table['source_binding'].get('owner_kind') != scope or (scope, name) in table_keys):
            raise GXWFormatError('Live declaration observation is incomplete or belongs to another owner')
        table_keys.add((scope, name))
        rows = table.get('declarations')
        if not isinstance(rows, list) or any(not isinstance(row.get('raw_fields'), dict) for row in rows):
            raise GXWFormatError('Live declarations require the actual native CSV fields')
        tables.append(dict(owner=name, owner_kind=scope, csv_columns=list(table['csv_columns']),
            declarations=[deepcopy(row['raw_fields']) for row in rows]))
    expected_tables = {('global', name) for name in inventory['globals']} | {
        (row['owner_kind'], row['owner']) for row in inventory['pous']} | {
        ('structure', name) for name in inventory['structures']}
    if table_keys != expected_tables:
        raise GXWFormatError('Live context has no complete current observation for every table')
    for key, origin, language, destination in (('st', 'active_native_st', 'st', st),
                                                ('graphs', None, 'fbd', graphs)):
        seen = set()
        for row in context.get(key, []):
            scope, name = row.get('owner_kind'), row.get('owner')
            binding = row.get('source_binding', {}) if origin else row
            if (origin and row.get('origin') != origin or _identity(binding.get('identity')) != identity
                    or (scope, name) in seen):
                raise GXWFormatError('Live source observation belongs to another owner or session')
            seen.add((scope, name))
            if origin:
                from .fb_authoring import _st_implementation
                from .visible_engineering import normalized_st
                destination.append(dict(owner=name, owner_kind=scope,
                                        st_text=normalized_st(_st_implementation(row.get('st_text')))))
            else:
                destination.append(_graph_source(row))
        expected = {(row['owner_kind'], row['owner']) for row in inventory['pous'] if row['language'] == language}
        if seen != expected:
            raise GXWFormatError('Live source inventory and actual native pages differ')
    configuration = context.get('configuration')
    if (not isinstance(configuration, dict) or configuration.get('origin') != 'active_native_configuration'
            or not _same_native_cpu(configuration.get('cpu'), identity['cpu_menu'])
            or configuration.get('status') not in ('available', 'partial', 'unavailable')):
        raise GXWFormatError('Live allocation configuration has no native observation')
    if configuration.get('identity') is not None and _identity(configuration['identity']) != identity:
        raise GXWFormatError('Live allocation settings belong to another native project')
    allocation_profile = _allocation_configuration_profile(configuration, identity['cpu_menu'])
    grid = configuration.get('control_profile') == 'device_range_grid'
    field_keys = ('grid_role', 'native_row', 'native_column', 'text', 'state') if grid else ('text', 'enabled')
    fields = {device: [{key: value[key] for key in field_keys} for value in rows]
              for device, rows in configuration.get('raw_fields', {}).items()}
    grid_rows = configuration.get('grid_rows')
    configuration = {key: deepcopy(configuration.get(key)) for key in ('status', 'origin', 'cpu', 'coverage', 'ranges')}
    configuration['fields'] = fields
    configuration.update(allocation_profile)
    if grid:
        from .project_metadata import native_allocation_grid_state
        configuration['grid_rows'] = native_allocation_grid_state(grid_rows)
    tasks = context.get('tasks')
    if (not isinstance(tasks, dict) or tasks.get('status') != 'available'
            or tasks.get('origin') != 'validated_saved_baseline_and_confirmed_native_appends'
            or not isinstance(tasks.get('resources'), list) or not isinstance(tasks.get('tasks'), list)):
        raise GXWFormatError('Live task ownership has no validated baseline and confirmed append context')
    task_state = {key: deepcopy(tasks[key]) for key in ('resources', 'tasks')}
    return dict(identity=identity, inventory=inventory,
        tables=sorted(tables, key=lambda row: (row['owner_kind'], row['owner'])),
        st=sorted(st, key=lambda row: (row['owner_kind'], row['owner'])),
        graphs=sorted(graphs, key=lambda row: (row['owner_kind'], row['owner'])),
        configuration=configuration, tasks=task_state)


def _comparable_graph_state(graph):
    """Ignore only LINE container order; preserve every segment and its count.

    Native compilation can permute the LINE array without changing geometry.
    Keep the actual indices in public observations for subsequent selections.
    """
    result = deepcopy(graph)
    for network in result['networks']:
        network['wires'] = sorted(
            [{key: value for key, value in wire.items() if key != 'index'} for wire in network['wires']],
            key=lambda wire: (wire['start'], wire['end']))
    return result


def live_context_meaning(context):
    state = live_context_state(context)
    state['graphs'] = [_comparable_graph_state(graph) for graph in state['graphs']]
    return state


def live_compile_plan(baseline, revision):
    """Keep the witnessed saved program-check option across a fresh GX process."""
    from .project_metadata import project_allocation_profile, read_project_compile_options
    if type(revision) is not int or revision < 0:
        raise GXWFormatError('Current native compilation requires its source revision')
    config, streams, _, _, _ = _context(baseline, require_fbd=False)
    profile = project_allocation_profile(config['cpu'])
    if not profile.get('program_supported'):
        raise GXWFormatError('Current native compilation requires a known programmable CPU menu')
    project, = [raw for name, raw in streams.items() if name.endswith('.prj')]
    options = read_project_compile_options(project)['values']
    check_options = [word for word in options if word & 0xffff0000 == 0x20010000]
    if check_options not in ([], [0x20010001]):
        raise GXWFormatError('The saved program-check option has no verified native checkbox mapping')
    # The saved option representation is shared across the CPU-bound native
    # templates. Native 053/054 and 101 establish the checkbox mapping for FX;
    # other tuples still need independent native UI evidence before dispatch.
    # Other option words remain byte-exact.
    return dict(revision=revision, public_checks_requested=not check_options)


def _check_current_structures(baseline, context):
    sources = _context(baseline, require_fbd=False)[3]
    current = {row['owner'].casefold(): row for row in context['tables'] if row['owner_kind'] == 'structure'}
    if set(current) != set(sources.project_structures):
        raise GXWFormatError('Active structure creation/deletion is outside this live declaration path')
    for key, documents in sources.project_structures.items():
        if len(documents) != 1:
            raise GXWFormatError('Saved structure ownership is ambiguous')
        expected = [(row.name, row.data_type, row.comment) for row in documents[0].members]
        if [(row['name'], row['data_type'], row['comment']) for row in current[key]['declarations']] != expected:
            raise GXWFormatError('Current structure fields changed outside the verified live editing scope')


def current_graph(baseline, context, caller):
    """Resolve a current visible Program/FB owner, including saved role names."""
    _, _, groups, sources, _ = _context(baseline, require_fbd=False)
    baseline_owner = sources.declarations[groups[caller]['labels']].owner_name if caller in groups else None
    candidates = [row for row in context['graphs'] if caller in (
        row['owner'], row['owner']+'.Program.pou', row['owner']+'.Body.pou') or row['owner'] == baseline_owner]
    if len(candidates) != 1:
        raise GXWFormatError('Live graph editing requires one current native caller observation')
    return candidates[0]


def prepare_live_command(baseline, context, command, *, device_profile):
    """Dispatch current observations to existing Core planners before native mutation."""
    state = live_context_state(context)
    config, streams, _, _, _ = _context(baseline, require_fbd=False)
    if (not _same_native_cpu(state['identity']['cpu_menu'], config['cpu'])
            or state['identity'].get('project_codepage', config['codepage']) != config['codepage']):
        raise GXWFormatError('Live context and validated baseline CPU differ')
    _check_current_structures(baseline, context)
    operation = command.get('operation')
    if operation == 'delete_label':
        return prepare_active_label_delete(baseline, context, command)
    if operation == 'create_fbd_call':
        from .live_fbd_call import prepare_active_fbd_call
        return prepare_active_fbd_call(baseline, context, command, device_profile=device_profile)
    if operation == 'edit_fbd_graph':
        from .active_graph import prepare_active_graph_action
        if (set(command) != {'operation', 'caller', 'actions'} or not isinstance(command.get('actions'), list)
                or len(command['actions']) != 1):
            raise GXWFormatError('A live graph boundary accepts exactly one action; observe context again before the next action')
        observed = current_graph(baseline, context, command['caller'])
        sources = active_callable_sources(baseline, context['tables'], owner=observed['owner'],
            kind=observed['owner_kind'], identity=context['identity'])
        graph = prepare_active_graph_action(observed, command['actions'][0], sources=sources, device_profile=device_profile)
        return dict(kind='active_graph', plan=dict(operation=operation, owner=observed['owner'], owner_kind=observed['owner_kind']),
            owner=observed['owner'], owner_kind=observed['owner_kind'], graph_action=graph,
            device_profile=device_profile, _baseline=baseline)
    if operation == 'configure_project':
        from .project_metadata import validate_project_allocation_ranges
        name, = [name for name in streams if name.endswith('.prj')]
        options = command.get('options')
        before = context['configuration']
        if (set(command) != {'operation', 'source_name', 'options'} or command['source_name'] != name
                or not isinstance(options, dict) or set(options) != {'system_variable_ranges'}
                or not options['system_variable_ranges'] or set(options['system_variable_ranges']) - {'D', 'M'}
                or before['status'] != 'available'
                or before.get('coverage') not in ('verified_D_M_controls_only', 'observed_D_M_controls', 'observed_D_M_grid')):
            raise GXWFormatError('Live allocation requires current verified D/M controls and the exact project source')
        if before.get('coverage') == 'verified_D_M_controls_only' and config['cpu'] != 'FX3U/FX3UC':
            raise GXWFormatError('Live allocation requires an explicit exact CPU native control profile')
        grid = before.get('control_profile') == 'device_range_grid'
        if any((field['state'] & 0x41 if grid else not field.get('enabled'))
               for device in options['system_variable_ranges'] for field in before.get('raw_fields', {}).get(device, [])):
            raise GXWFormatError('Live allocation range field is not enabled in the current native dialog')
        edits = validate_project_allocation_ranges(options['system_variable_ranges'], cpu=config['cpu'], known_devices={'D', 'M'})
        if any(pair == (0xffffffff, 0xffffffff) for pair in edits.values()):
            raise GXWFormatError('Live allocation disable mode is not verified')
        if grid:
            from .project_metadata import read_native_allocation_grids
            from .native_locale import native_language
            current = read_native_allocation_grids(before['grid_rows'], native_ui_language=native_language(before['identity']))
            if any(not current['parameter_ranges'][device][0] <= pair[0] <= pair[1] <= current['parameter_ranges'][device][1]
                   for device, pair in edits.items()):
                raise GXWFormatError('Live allocation edit exceeds the actual current PLC-parameter grid interval')
        ranges = [dict(device=device, before=list(before['ranges'][device]), after=list(pair)) for device, pair in edits.items()]
        profile_binding = _allocation_configuration_profile(before, config['cpu'])
        return dict(kind='active_configuration', plan=dict(operation=operation, ranges=ranges, **profile_binding), before=deepcopy(before),
                    source_name=name, origin='current_native_configuration')
    if not state['inventory']['pous']:
        raise GXWFormatError('Live editing requires a current Program/FB source owner')
    selected = state['inventory']['pous'][0]
    common = dict(owner=selected['owner'], kind=selected['owner_kind'], identity=context['identity'])
    if operation in ('create_global_labels', 'edit_global_labels', 'edit_pou_labels'):
        return prepare_active_labels(baseline, context['tables'], command, device_profile=device_profile,
                                     current_graphs=context['graphs'], **common)
    if operation in ('create_st_fb', 'edit_st_fb', 'create_st_program', 'edit_st_program'):
        current = next((row for row in context['st'] if row['owner'].casefold() == command['source_name'].casefold()), None)
        return prepare_active_st(baseline, context['tables'], command, current_st=current,
                                 task_scopes=context['tasks'], current_graphs=context['graphs'], **common)
    raise GXWFormatError('This live operation has no verified current-document planner')


def _stable_connections(before, after):
    """Lazy cache initialization is allowed; contradictory observed ports are not."""
    current = {(row['owner_kind'], row['owner']): row for row in after.get('graphs', [])}
    for left in before.get('graphs', []):
        right = current.get((left['owner_kind'], left['owner']))
        if right is None:
            continue
        for old_network, new_network in zip(left['networks'], right['networks']):
            for old, new in zip(old_network['nodes'], new_network['nodes']):
                a, b = old.get('connections', {}), new.get('connections', {})
                if a.get('status') != 'observed' or b.get('status') != 'observed':
                    continue
                ports_a, ports_b = a.get('ports'), b.get('ports')
                if not isinstance(ports_a, list) or not isinstance(ports_b, list) or len(ports_a) != len(ports_b):
                    raise GXWFormatError('Already observed native connection membership became unconfirmed')
                for p, q in zip(ports_a, ports_b):
                    if any(p.get(key) != q.get(key) for key in ('name', 'local_x', 'local_y', 'flags')) or (
                            p.get('native_type_mask') and q.get('native_type_mask')
                            and p['native_type_mask'] != q['native_type_mask']):
                        raise GXWFormatError('Already observed native connection semantics became unconfirmed')


def verify_live_observations(before, after):
    """Reconcile stable source while checking independently observed port caches."""
    if live_context_meaning(before) != live_context_meaning(after):
        raise GXWFormatError('Current native source changed during the observed operation')
    _stable_connections(before, after)
    return dict(status='matched', source='unchanged', observed_port_contradictions='none', save_performed=False)


def verify_live_change(before_context, after_context, command, prepared):
    """Verify the allowed target and freeze every unrelated current source layer."""
    before, after = live_context_meaning(before_context), live_context_meaning(after_context)
    declarations_unchanged = before['tables'] == after['tables']
    if before['identity'] != after['identity']:
        raise GXWFormatError('Live mutation completed in a different native project identity')
    plan, kind = prepared['plan'], prepared['kind']
    if kind == 'active_fbd_call':
        from .live_fbd_call import verify_active_fbd_call
        return verify_active_fbd_call(before_context, after_context, command, prepared)
    if plan.get('operation') != command.get('operation'):
        raise GXWFormatError('Live change and prepared operation differ')
    scope, name = prepared.get('scope'), plan.get('source_name')
    ignored_graph = None
    if kind == 'active_graph':
        from .active_graph import verify_active_graph_action
        owner, owner_kind = plan['owner'], plan['owner_kind']
        a = [row for row in before_context['graphs'] if (row['owner_kind'], row['owner']) == (owner_kind, owner)]
        b = [row for row in after_context['graphs'] if (row['owner_kind'], row['owner']) == (owner_kind, owner)]
        if len(a) != 1 or len(b) != 1:
            raise GXWFormatError('Live graph boundary has no unique before/after native caller')
        sources = active_callable_sources(prepared['_baseline'], before_context['tables'], owner=owner,
            kind=owner_kind, identity=before_context['identity'])
        # The adapter rebinds the current native allocation before dispatch.
        # Final source comparison retains project/owner identity, not an old
        # heap Data ID or recreated MDI HWND from an earlier page read.
        source_before = {key: value for key, value in a[0].items() if key not in ('data_id', 'document_handle')}
        receipt = verify_active_graph_action(source_before, b[0], prepared['graph_action'], sources=sources)
        if receipt['status'] != 'verified':
            raise GXWFormatError('Live graph effect is unconfirmed: ' + '; '.join(row['message'] for row in receipt['diagnostics']))
        receipt['status'] = 'matched'
        ignored_graph = (owner_kind, owner)
        for state in (before, after):
            state['graphs'] = [row for row in state['graphs'] if (row['owner_kind'], row['owner']) != ignored_graph]
    elif kind in ('active_labels', 'active_st', 'active_label_delete'):
        tables = [row for row in after_context['tables'] if row['owner_kind'] == scope and row['owner'] == name]
        if len(tables) != 1:
            raise GXWFormatError('Live edit has no unique current declaration readback')
        if kind == 'active_label_delete':
            if (set(command) != {'operation', 'scope', 'source_name', 'name'} or command.get('scope') != scope
                    or any(not isinstance(command.get(key), str) or command[key].casefold() != plan[key].casefold()
                           for key in ('source_name', 'name'))):
                raise GXWFormatError('Native label deletion differs from the prepared exact scope and target')
            receipt = verify_active_label_delete(prepared, tables[0])
        elif kind == 'active_labels':
            receipt = verify_active_labels(prepared, tables[0])
        else:
            body = [row for row in after_context['st'] if row['owner_kind'] == scope and row['owner'] == name]
            if len(body) != 1:
                raise GXWFormatError('Live ST edit has no unique current native implementation')
            receipt = verify_active_st(prepared, body[0], tables[0])
            for state in (before, after):
                state['st'] = [row for row in state['st'] if (row['owner_kind'], row['owner']) != (scope, name)]
            if prepared['create']:
                after['inventory']['pous'] = [row for row in after['inventory']['pous']
                                              if (row['owner_kind'], row['owner']) != (scope, name)]
                if scope == 'program':
                    expected_tasks = deepcopy(before['tasks'])
                    matches = [row for row in expected_tasks['tasks'] if (row['resource'], row['task']) == (plan['resource'], plan['task'])]
                    if len(matches) != 1:
                        raise GXWFormatError('Created Program has no explicit current task owner')
                    matches[0]['programs'].append(dict(name=name, line=plan['line']))
                    if after['tasks'] != expected_tasks:
                        raise GXWFormatError('Live Program task changes differ from the explicit append')
                    after['tasks'] = deepcopy(before['tasks'])
        for state in (before, after):
            state['tables'] = [row for row in state['tables'] if (row['owner_kind'], row['owner']) != (scope, name)]
        if command['operation'] == 'create_global_labels':
            after['inventory']['globals'].remove(name)
    elif kind == 'active_configuration':
        expected = deepcopy(prepared['before'])
        for row in plan['ranges']:
            expected['ranges'][row['device']] = row['after']
            for field, value in zip(expected['raw_fields'][row['device']], row['after']):
                field['text'] = str(value)
            if expected.get('control_profile') == 'device_range_grid':
                for field, value in zip(expected['raw_fields'][row['device']], row['after']):
                    expected['grid_rows'][field['grid_role']][field['native_row']]['cells'][field['native_column']]['value'] = str(value)
                native_row = expected['raw_fields'][row['device']][0]['native_row']
                expected['grid_rows']['allocation'][native_row]['cells'][7]['value'] = str(row['after'][1] - row['after'][0] + 1)
        actual = after_context['configuration']
        if actual.get('ranges') != expected['ranges'] or actual.get('raw_fields') != expected['raw_fields']:
            raise GXWFormatError('Live allocation readback differs from requested or retained fields')
        if expected.get('control_profile') == 'device_range_grid':
            from .project_metadata import native_allocation_grid_state
            if (any(actual.get(key) != expected.get(key)
                    for key in ('status', 'origin', 'cpu', 'coverage', 'allocation_profile', 'control_profile'))
                    or native_allocation_grid_state(actual.get('grid_rows')) != native_allocation_grid_state(expected['grid_rows'])):
                raise GXWFormatError('Live allocation changed an unrequested native grid cell or retained state')
        before.pop('configuration'), after.pop('configuration')
        receipt = dict(status='matched', origin='active_native_configuration', save_performed=False)
    else:
        raise GXWFormatError('This live change has no verified current-source receipt')
    if before != after:
        raise GXWFormatError('Live engineering changed an unrelated observed source layer')
    if declarations_unchanged or kind == 'active_label_delete':
        if ignored_graph is None:
            _stable_connections(before_context, after_context)
        else:
            _stable_connections({**before_context, 'graphs': [row for row in before_context['graphs']
                if (row['owner_kind'], row['owner']) != ignored_graph]},
                {**after_context, 'graphs': [row for row in after_context['graphs']
                if (row['owner_kind'], row['owner']) != ignored_graph]})
    return {**receipt, 'unrelated_sources': 'matched', 'save_performed': False}


def _saved_graph_state(program, *, owner, kind):
    from .models import StructuredNode, StructuredWire
    from .object_model import _key
    networks = []
    for index, (_, records) in enumerate(program.block_records()):
        nodes, wires = [], []
        for record in records:
            if isinstance(record, StructuredNode):
                nodes.append(dict(index=len(nodes), template=_key(record),
                    symbol=record.symbol, source_kind_code=record.kind_code,
                    type_name=record.symbol if record.kind_code == 1 else record.type_name,
                    x=record.bbox.left, y=record.bbox.top, width=record.bbox.right-record.bbox.left,
                    height=record.bbox.bottom-record.bbox.top))
            elif isinstance(record, StructuredWire):
                wires.append(dict(index=len(wires), start=[record.start.x, record.start.y], end=[record.end.x, record.end.y]))
        networks.append(dict(index=index, nodes=nodes, wires=wires, junctions=[]))
    return dict(owner=owner, owner_kind=kind, networks=networks)


def _verify_saved_tables(saved_context, tables):
    from .visible_engineering import normalized_st
    from .fb_authoring import _visible_st_declaration_rows
    saved = saved_context[3]
    pou_documents = {document.owner_name: document for document in saved.declarations.values()
                     if document.scope == 'local' and document.logical_name.endswith('.lh')}
    basic_columns = {'类', '标签名', '数据类型', '常量', '软元件', '地址', '注释'}
    unobserved = 0
    for table in tables:
        column_identities = _table_column_identities(table)
        scope, name = table['owner_kind'], table['owner']
        if scope == 'structure':
            documents = saved.project_structures.get(name.casefold(), [])
            if len(documents) != 1:
                raise GXWFormatError('Saved structure has no unique current owner')
            expected = {row.name: dict(name=row.name, data_type=row.data_type, comment=row.comment,
                                      class_code=None, device='', iec_address='', initial_value='')
                        for row in documents[0].members}
        else:
            document = saved.declarations.get(name + '.gh') if scope == 'global' else pou_documents.get(name)
            if document is None or (scope != 'global' and document.owner_pou_type != (0x1000000 if scope == 'program' else 0x1000002)):
                raise GXWFormatError('Saved declaration table differs from the current Program/FB/global owner')
            expected = {row.name: dict(name=row.name, data_type=row.data_type, comment=row.comment,
                class_code=row.class_code, device=row.device, iec_address=row.iec_address,
                initial_value=row.initial_value) for row in document.rows}
        actual = table['declarations']
        if len(expected) != len(actual) or set(expected) != {row['name'] for row in actual}:
            raise GXWFormatError('Saved declaration names differ from the actual current native CSV')
        for row in actual:
            target = expected[row['name']]
            type_text = _visible_st_declaration_rows([{'data_type': target['data_type']}])[0]['data_type']
            observed_type = _visible_st_declaration_rows([{'data_type': row['data_type']}])[0]['data_type']
            if (observed_type != type_text or row['class_code'] != target['class_code']
                    or normalized_st(row['comment']) != normalized_st(target['comment'])
                    or row.get('device') is not None and row['device'] != target['device']
                    or row.get('iec_address') is not None and row['iec_address'] != target['iec_address']):
                raise GXWFormatError('Saved declaration fields differ from the actual current native CSV')
            if row['initial_value_status'] == 'observed_constant':
                if row['initial_value'] != target['initial_value']:
                    raise GXWFormatError('Saved constant differs from the observed native CSV value')
            else:
                unobserved += 1
            if any(value for column, value in row['raw_fields'].items()
                   if column_identities.get(column, column) not in basic_columns):
                raise GXWFormatError('Nonempty native CSV fields have no verified saved-source mapping')
    return unobserved


def _verify_saved_ports(saved_context, observation, baseline, tables, identity):
    from .active_graph import active_graph_projection
    from .object_model import _ports
    sources = active_callable_sources(baseline, tables, owner=observation['owner'],
        kind=observation['owner_kind'], identity=identity)
    current = active_graph_projection(observation, sources=sources)
    matched, unavailable = 0, 0
    for projected, native, (_, records) in zip(current['networks'], observation['networks'],
                                               saved_context.program.block_records(), strict=True):
        from .models import StructuredNode
        saved_nodes = [row for row in records if isinstance(row, StructuredNode)]
        for node, raw, saved_node in zip(projected['nodes'], native['nodes'], saved_nodes, strict=True):
            if raw.get('connections', {}).get('status') != 'observed':
                unavailable += 1
                continue
            if node['ports_status'] != 'resolved':
                raise GXWFormatError('Observed native ports have no current source-resolved saved mapping')
            saved_ports = _ports(saved_node, saved_context.sources)
            if len(saved_ports) != len(node['ports']) or len(saved_node.ports) != len(node['ports']):
                raise GXWFormatError('Saved graph port membership differs from the observed native interface')
            for port, descriptor, stored in zip(node['ports'], saved_node.ports, saved_ports):
                if ((port['x'], port['y']) != (descriptor.local_x, descriptor.local_y)
                        or port['flags'] & 1 != descriptor.port_kind_code & 1
                        or port['name'].casefold() != stored['name'].casefold()):
                    raise GXWFormatError('Saved graph ports differ from the observed native names, directions or geometry')
            matched += 1
    return matched, unavailable


def _verify_saved_preservation(baseline, saved, context, old, new, *, native_compiled=False):
    from .container_writer import validate_cfb_streams
    from .native_write import _saved_object_names
    from .project_metadata import logical_mapping, read_task_source
    from .source_header import normalize_visible_save_header, source_payload_offset
    from .text_pou import parse_st_pou, serialize_st_pou
    from .structured_pou_writer import SIZE_LIKE_A_OFFSET, SIZE_LIKE_B_OFFSET
    identity = _identity(context.get('identity'))
    # Legacy fixtures predate language binding. An explicit current language
    # always participates in a newly observed metadata-normalization exception.
    header_identity = identity if 'native_ui_language' in identity else None
    # Existing role mapping permits the measured first-save library rename;
    # every retained library and readonly structure payload must remain exact.
    def protected(raw, parsed):
        metadata = validate_cfb_streams(raw)['projectdatalist.xml']
        mapping = {name: key for name, key in logical_mapping(metadata).items()
                   if name.endswith(('.lnl', '.lnb', '.lns', '.lng', '.lif', '.str'))}
        return metadata, mapping, {name: parsed[1][name] for name in mapping}
    a, am, av = protected(baseline, old)
    b, bm, bv = protected(saved, new)
    names = _saved_object_names(a, am, b, bm, {}, {})
    if any(payload != bv[names[name]] for name, payload in av.items()):
        raise GXWFormatError('Stage save changed retained library or readonly structure source bytes')
    header_changes = []
    old_pous = {old[3].declarations[group['labels']].owner_name: logical for logical, group in old[2].items()}
    new_pous = {new[3].declarations[group['labels']].owner_name: logical for logical, group in new[2].items()}
    for owner, logical in old_pous.items():
        if owner not in new_pous:
            raise GXWFormatError('Stage save removed an existing Program/FB source')
        before, after = old[1][logical], new[1][new_pous[owner]]
        prefix_a, prefix_b = source_payload_offset(before), source_payload_offset(after)
        operation = 'source_save'
        if (header_identity is not None and before[prefix_a] == after[prefix_b]
                and (before[prefix_a:] != after[prefix_b:]
                     or old[1][old[2][logical]['labels']] != new[1][new[2][new_pous[owner]]['labels']])):
            if before[prefix_a] == 208:
                operation = 'edited_source_save'
            elif before[prefix_a] == 193:
                operation = 'edited_st_source_save'
        normalized, changed = normalize_visible_save_header(before[:prefix_a], after[:prefix_b], cpu=old[0]['cpu'],
            native_compiled=native_compiled, native_identity=header_identity, operation=operation)
        if changed is not None:
            header_changes.append(dict(source=new_pous[owner], **changed))
        if before[prefix_a] == 193:
            expected = serialize_st_pou(parse_st_pou(before), parse_st_pou(after).text)
            if expected != normalized + after[prefix_b:]:
                raise GXWFormatError('Stage save changed retained ST source envelope fields')
        elif before[prefix_a] == 208:
            from .object_model import read_project_context
            left, right = read_project_context(baseline, logical).program, read_project_context(saved, new_pous[owner]).program
            lh, rh = bytearray(left.raw[:left.blocks[0].offset]), bytearray(normalized + right.raw[prefix_b:right.blocks[0].offset])
            for offset in (SIZE_LIKE_A_OFFSET, SIZE_LIKE_B_OFFSET):
                lh[offset:offset+4] = rh[offset:offset+4] = bytes(4)
            if (lh != rh or left.trailer != right.trailer or len(left.blocks) != len(right.blocks)):
                raise GXWFormatError('Stage save changed retained FBD header, trailer or network membership')
            for (old_block, old_records), (new_block, new_records) in zip(left.block_records(), right.block_records()):
                if (old_block.raw_header[4:16] != new_block.raw_header[4:16]
                        or [row.raw for row in old_records if row in left.unknown_records] !=
                           [row.raw for row in new_records if row in right.unknown_records]):
                    raise GXWFormatError('Stage save changed retained unknown graph source records')
            for row in left.nodes:
                matches = [node for node in right.nodes if node.kind_code == row.kind_code and (
                    (node.symbol, node.type_name) == (row.symbol, row.type_name) or node.bbox == row.bbox)]
                if len(matches) == 1 and (matches[0].object_flag, matches[0].reserved) != (row.object_flag, row.reserved):
                    raise GXWFormatError('Stage save changed retained node flag fields')
            for row in left.wires:
                matches = [wire for wire in right.wires if (wire.start, wire.end) == (row.start, row.end)]
                if len(matches) == 1 and (matches[0].prefix_fields, matches[0].suffix) != (row.prefix_fields, row.suffix):
                    raise GXWFormatError('Stage save changed retained wire source fields')
        else:
            raise GXWFormatError('Stage save has an unsupported retained source language')
    # Existing rows retain unobservable initializers and opaque address data.
    for logical, document in old[3].declarations.items():
        if document.scope == 'global':
            current = new[3].declarations.get(logical)
        elif document.owner_name in new_pous:
            current = new[3].declarations[new[2][new_pous[document.owner_name]]['labels']]
        else:
            continue
        if current is None:
            raise GXWFormatError('Stage save removed a retained declaration table')
        rows = {row.name: row for row in current.rows}
        for prior in document.rows:
            row = rows.get(prior.name)
            if row is not None and (row.data_type, row.class_code) == (prior.data_type, prior.class_code):
                if any(getattr(row, key) != getattr(prior, key) for key in ('unknown_text', 'unknown_u32', 'value_extension')):
                    raise GXWFormatError('Stage save changed retained opaque declaration storage')
                if row.class_code not in (2, 9, 11) and row.initial_value != prior.initial_value:
                    raise GXWFormatError('Stage save changed an unobserved retained ordinary initializer')
    appends = context['tasks'].get('confirmed_appends', [])
    for logical, before in old[1].items():
        if not logical.endswith('.tsk'):
            continue
        after = new[1].get(logical)
        if after is None:
            raise GXWFormatError('Stage save removed a current task')
        if native_compiled:
            previous_header, current_header = read_task_source(before)['header'], read_task_source(after)['header']
            if previous_header != current_header and int.from_bytes(current_header[50:54], 'little') == 0:
                operation = 'compiled_task_save' if header_identity is not None else 'source_save'
                normalized, changed = normalize_visible_save_header(previous_header, current_header,
                    cpu=old[0]['cpu'], native_compiled=True, native_identity=header_identity, operation=operation)
                after = normalized + after[len(current_header):]
                header_changes.append(dict(source=logical, **changed))
        changes = [row for row in appends if row['task'] + '.tsk' == logical]
        prior_rows = read_task_source(before)['rows']
        # A later Agent transaction may already use a prior stage save as its
        # baseline. Cumulative session receipts are not appended a second time.
        changes = [row for row in changes if not any(
            (prior['name'], prior['line']) == (row['program'], row['line']) for prior in prior_rows)]
        if not changes:
            if before != after:
                raise GXWFormatError('Stage save changed retained unedited task bytes')
            continue
        from .fb_authoring import _visible_task_append_header
        normalized, changed = _visible_task_append_header(before, after, cpu=old[0]['cpu'],
            native_identity=header_identity)
        if changed is not None:
            header_changes.append(dict(source=logical, **changed))
        previous, current = read_task_source(before), read_task_source(normalized)
        if (any(previous[key] != current[key] for key in ('header', 'event_name', 'interval', 'priority', 'opaque_word', 'suffix'))
                or [row['raw'] for row in previous['rows']] != [row['raw'] for row in current['rows'][:len(previous['rows'])]]
                or [(row['name'], row['line'], row['extra_text']) for row in current['rows'][len(previous['rows']):]] !=
                   [(row['program'], row['line'], '') for row in changes]):
            raise GXWFormatError('Stage save task bytes differ from confirmed native appends')
    return header_changes


def verify_active_saved_context(saved, context, baseline, *, compilation=None):
    """Independently bind a phase save to actual current pages and retained sources."""
    from .native_write import native_task_scope_context
    from .project_metadata import (native_fbd_pointer_initialization, read_native_allocation_grids,
                                   read_project_compile_options, read_project_system_variables)
    from .st_program import project_context_snapshot
    from .visible_engineering import normalized_st
    state = live_context_state(context)
    native_compiled = False
    if isinstance(compilation, dict):
        try:
            compiled_identity = _identity(compilation.get('identity'))
        except GXWFormatError:
            compiled_identity = None
        run_id = compilation.get('compile_run_id')
        native_compiled = (compilation.get('status') == 'passed' and type(compilation.get('errors')) is int
            and compilation['errors'] == 0 and compilation.get('origin') == 'current_native_compile_and_active_source'
            and compilation.get('source_unchanged') is True and compiled_identity == state['identity']
            and isinstance(run_id, str) and bool(run_id.strip()) and len(run_id) <= 128
            and not any(ord(char) < 32 or ord(char) == 127 for char in run_id)
            and type(compilation.get('revision')) is int and type(context.get('_revision')) is int
            and compilation['revision'] == context['_revision'] and context['_revision'] >= 0)
    old, new = _context(baseline, require_fbd=False), _context(saved, require_fbd=False)
    if (any(new[0][key] != old[0][key] for key in ('cpu', 'codepage'))
            or not _same_native_cpu(new[0]['cpu'], state['identity']['cpu_menu'])
            or state['identity'].get('project_codepage', new[0]['codepage']) != new[0]['codepage']):
        raise GXWFormatError('Stage save changed the bound native CPU/code page')
    snapshot = project_context_snapshot(saved)
    inventory = dict(globals=[row['name'] for row in snapshot['globals']],
        pous=[dict(owner=row['name'], owner_kind='program' if row['kind'] == 'program' else 'fb', language=row['language'])
              for row in snapshot['pous'] if row['kind'] in ('program', 'function_block')],
        structures=[row['name'] for row in snapshot['structures']])
    if _inventory(inventory) != state['inventory'] or len(inventory['pous']) != len(snapshot['pous']):
        raise GXWFormatError('Stage save project objects differ from the current native tree')
    unobserved = _verify_saved_tables(new, context['tables'])
    saved_pous = {row['name']: row for row in snapshot['pous']}
    for row in state['st']:
        current = saved_pous[row['owner']]
        if current.get('st_text') is None or normalized_st(current['st_text']) != row['st_text']:
            raise GXWFormatError('Stage save ST body differs from the actual native editor')
    from .object_model import read_project_context
    matched_ports, unavailable_ports = 0, 0
    for row in state['graphs']:
        current = saved_pous[row['owner']]
        saved_graph = read_project_context(saved, current['program'])
        expected = _saved_graph_state(saved_graph.program,
                                      owner=row['owner'], kind=row['owner_kind'])
        if (any(network['junctions'] for network in row['networks'])
                or _comparable_graph_state(expected) != _comparable_graph_state(row)):
            raise GXWFormatError('Stage save graph geometry differs or contains unverified native junctions')
        observation, = [graph for graph in context['graphs'] if (graph['owner_kind'], graph['owner']) ==
                        (row['owner_kind'], row['owner'])]
        matched, unavailable = _verify_saved_ports(saved_graph, observation, baseline, context['tables'], context['identity'])
        matched_ports += matched
        unavailable_ports += unavailable
    name, = [name for name in new[1] if name.endswith('.prj')]
    before_name, = [name for name in old[1] if name.endswith('.prj')]
    ranges = read_project_system_variables(new[1][name])['ranges']
    previous_ranges = read_project_system_variables(old[1][before_name])['ranges']
    configuration = state['configuration']
    range_initializations = []
    if ranges['P'] != previous_ranges['P'] and state['graphs']:
        initialization = native_fbd_pointer_initialization(old[1][before_name], 208,
            native_identity=state['identity'])
        if initialization is not None and ranges['P'] == tuple(initialization['after']):
            from .native_locale import native_language, ui_text
            language = native_language(state['identity'])
            # Re-validate the complete current native grid before using its P
            # row. Neither a template nor the newly saved bytes prove the UI.
            observed = read_native_allocation_grids(configuration.get('grid_rows'),
                native_ui_language=language)
            pointers = [row['cells'] for row in observed['grid_rows']['allocation']
                        if row['cells'][2]['value'] == 'P']
            if (len(pointers) == 1 and pointers[0][0]['value'] == ui_text('allocation_grid_var', language)
                    and [cell['value'] for cell in pointers[0][5:7]] ==
                        [str(value) for value in initialization['after']]
                    and pointers[0][7]['value'] == str(initialization['after'][1] - initialization['after'][0] + 1)
                    and all(cell['state'] & ~0x6 == 0 for cell in pointers[0][5:8])):
                previous_ranges['P'] = tuple(initialization['after'])
                range_initializations.append(dict(device='P', before=initialization['before'],
                    after=initialization['after'], origin='current_native_fbd_pointer_default_materialized',
                    cpu_menu=state['identity']['cpu_menu'], native_ui_language=language))
    if configuration['status'] != 'available' or any(
            list(pair) != (configuration['ranges'][device] if device in ('D', 'M') else list(previous_ranges[device]))
            for device, pair in ranges.items()):
        raise GXWFormatError('Stage save allocation differs from observed D/M or retained other ranges')
    if (read_project_compile_options(new[1][name]) != read_project_compile_options(old[1][before_name])
            or new[1].get('Param.wpa') != old[1].get('Param.wpa')):
        raise GXWFormatError('Stage save changed retained compile options or PLC parameter bytes')
    task_context = native_task_scope_context(saved)
    if task_context.get('status') != 'available' or any(task_context[key] != state['tasks'][key] for key in ('resources', 'tasks')):
        raise GXWFormatError('Stage save task membership/configuration differs from the confirmed context')
    header_changes = _verify_saved_preservation(baseline, saved, context, old, new, native_compiled=native_compiled)
    return dict(status='matched', declarations='observed_csv_fields_matched', st='matched', graphs='matched',
        configuration='matched', tasks='matched', libraries_and_structures='byte_exact',
        retained_source_fields='matched', ordinary_initial_values='unavailable_in_csv',
        **({'native_range_initializations': range_initializations} if range_initializations else {}),
        unobserved_ordinary_initializers=unobserved,
        native_port_bindings='matched_where_observed' if matched_ports else 'not_observed',
        native_port_nodes_matched=matched_ports, native_port_nodes_unavailable=unavailable_ports,
        source_header_changes=header_changes,
        compiled_header_revision=compilation['revision'] if native_compiled else None,
        compiled_header_run_id=compilation['compile_run_id'] if native_compiled else None,
        compilation='not_requested', execution='not_run')
